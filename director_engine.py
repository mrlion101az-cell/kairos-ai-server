
"""
director_engine.py
Kairos / Nexus Director Engine

Purpose:
- Executive decision layer for Kairos.
- Coordinates existing systems without replacing them.
- Decides whether Kairos should ignore, observe, escalate, deploy, occupy, or broadcast.
- Keeps Discord behavior untouched unless explicitly called by Discord-aware code.
- Does NOT run Flask.
- Does NOT run background loops.
- Does NOT directly talk to Discord.

Design rules:
- app.py receives traffic.
- command_bridge.py routes Minecraft/NPC/Discord messages.
- ai_core.py thinks and generates internal intent.
- memory_engine.py records memory/events.
- world_state_engine.py stores persistent world threat/regions/factions.
- telemetry_engine.py stores player/region observations.
- continuity_engine.py handles rumors/lore drift.
- war_engine.py executes military responses.
- mc_connector.py sends Minecraft commands.

Director Engine sits above War Engine and answers:
    Should Kairos do anything?

Primary public APIs:
- direct_minecraft_chat(player, message, location=None, metadata=None)
- direct_world_event(event_type, description, player=None, location=None, metadata=None)
- direct_player_kill(killer, victim, location=None, metadata=None)
- direct_grief_block(player, block, location=None, metadata=None)
- direct_region_pressure(region, player=None, faction='Kairos', metadata=None)
- direct_terminal_request(terminal_name, player, location=None, metadata=None)
- direct_artifact_submission(player, artifact_id, terminal_name='Fracture', location=None, metadata=None)
- tick_director(location=None, faction=None)

Safe behavior:
- If optional systems fail to import, Director degrades gracefully.
- If AI is unavailable, Director uses deterministic rules.
- If War Engine is unavailable, Director records the decision but does not crash.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# OPTIONAL IMPORTS - FAIL SOFT
# ============================================================

try:
    from ai_core import AIContext, generate_ai_response
except Exception as exc:  # pragma: no cover
    AIContext = None  # type: ignore
    generate_ai_response = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] ai_core import failed: {exc}", flush=True)

try:
    from memory_engine import (
        record_world_event,
        record_system_error,
        append_player_memory,
        get_recent_world_events,
        get_recent_rumors,
    )
except Exception as exc:  # pragma: no cover
    record_world_event = None  # type: ignore
    record_system_error = None  # type: ignore
    append_player_memory = None  # type: ignore
    get_recent_world_events = None  # type: ignore
    get_recent_rumors = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] memory_engine import failed: {exc}", flush=True)

try:
    from world_state_engine import get_world_state, adjust_threat, set_threat, upsert_region, set_occupation
except Exception as exc:  # pragma: no cover
    get_world_state = None  # type: ignore
    adjust_threat = None  # type: ignore
    set_threat = None  # type: ignore
    upsert_region = None  # type: ignore
    set_occupation = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] world_state_engine import failed: {exc}", flush=True)

try:
    from telemetry_engine import get_player_position, get_region_density, classify_region_density, record_base_if_detected
except Exception as exc:  # pragma: no cover
    get_player_position = None  # type: ignore
    get_region_density = None  # type: ignore
    classify_region_density = None  # type: ignore
    record_base_if_detected = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] telemetry_engine import failed: {exc}", flush=True)

try:
    from continuity_engine import generate_rumor, record_continuity_event, generate_continuity_summary
except Exception as exc:  # pragma: no cover
    generate_rumor = None  # type: ignore
    record_continuity_event = None  # type: ignore
    generate_continuity_summary = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] continuity_engine import failed: {exc}", flush=True)

try:
    from war_engine import (
        register_chat_pressure,
        launch_pressure_wave,
        deploy_custom_kairos_units,
        deploy_response_by_tier,
        register_player_kill,
        register_grief_block,
        occupy_region,
        tick_war_engine,
    )
except Exception as exc:  # pragma: no cover
    register_chat_pressure = None  # type: ignore
    launch_pressure_wave = None  # type: ignore
    deploy_custom_kairos_units = None  # type: ignore
    deploy_response_by_tier = None  # type: ignore
    register_player_kill = None  # type: ignore
    register_grief_block = None  # type: ignore
    occupy_region = None  # type: ignore
    tick_war_engine = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] war_engine import failed: {exc}", flush=True)

try:
    from mc_connector import send_actionbar, send_chat, broadcast_world_event, send_minecraft_commands
except Exception as exc:  # pragma: no cover
    send_actionbar = None  # type: ignore
    send_chat = None  # type: ignore
    broadcast_world_event = None  # type: ignore
    send_minecraft_commands = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] mc_connector import failed: {exc}", flush=True)

try:
    from fracture_terminal import (
        build_terminal_context,
        scoreboard_sync_commands,
        submit_artifact,
    )
except Exception as exc:  # pragma: no cover
    build_terminal_context = None  # type: ignore
    scoreboard_sync_commands = None  # type: ignore
    submit_artifact = None  # type: ignore
    print(f"[DIRECTOR_ENGINE WARN] fracture_terminal import failed: {exc}", flush=True)


# ============================================================
# CONFIG
# ============================================================

DIRECTOR_DEBUG = os.getenv("DIRECTOR_ENGINE_DEBUG", "true").lower() == "true"
DIRECTOR_ENABLED = os.getenv("DIRECTOR_ENGINE_ENABLED", "true").lower() == "true"

# Minecraft chat should mostly be silent. This is the whole point.
DIRECTOR_MINECRAFT_SILENT = os.getenv("DIRECTOR_MINECRAFT_SILENT", "true").lower() == "true"
DIRECTOR_DISCORD_UNTOUCHED = os.getenv("DIRECTOR_DISCORD_UNTOUCHED", "true").lower() == "true"

# Decision pacing / spam prevention.
DIRECTOR_PLAYER_COOLDOWN_SECONDS = float(os.getenv("DIRECTOR_PLAYER_COOLDOWN_SECONDS", "30"))
DIRECTOR_MAJOR_ACTION_COOLDOWN_SECONDS = float(os.getenv("DIRECTOR_MAJOR_ACTION_COOLDOWN_SECONDS", "120"))
DIRECTOR_REGION_COOLDOWN_SECONDS = float(os.getenv("DIRECTOR_REGION_COOLDOWN_SECONDS", "180"))

# Local director score thresholds. War Engine has its own local/persistent threat too.
DIRECTOR_WATCH_THRESHOLD = float(os.getenv("DIRECTOR_WATCH_THRESHOLD", "12"))
DIRECTOR_TARGET_THRESHOLD = float(os.getenv("DIRECTOR_TARGET_THRESHOLD", "35"))
DIRECTOR_HUNT_THRESHOLD = float(os.getenv("DIRECTOR_HUNT_THRESHOLD", "70"))
DIRECTOR_MAXIMUM_THRESHOLD = float(os.getenv("DIRECTOR_MAXIMUM_THRESHOLD", "130"))

# Chance to choose silence even when there is enough score to act.
DIRECTOR_SILENCE_CHANCE_IDLE = float(os.getenv("DIRECTOR_SILENCE_CHANCE_IDLE", "0.85"))
DIRECTOR_SILENCE_CHANCE_WATCH = float(os.getenv("DIRECTOR_SILENCE_CHANCE_WATCH", "0.60"))
DIRECTOR_SILENCE_CHANCE_TARGET = float(os.getenv("DIRECTOR_SILENCE_CHANCE_TARGET", "0.30"))
DIRECTOR_SILENCE_CHANCE_HUNT = float(os.getenv("DIRECTOR_SILENCE_CHANCE_HUNT", "0.15"))

# AI action planner is optional. Deterministic rules still work without it.
DIRECTOR_AI_PLANNER_ENABLED = os.getenv("DIRECTOR_AI_PLANNER_ENABLED", "true").lower() == "true"
DIRECTOR_AI_PLANNER_MAX_TOKENS = int(os.getenv("DIRECTOR_AI_PLANNER_MAX_TOKENS", "250"))

# How much normal chat matters to the Director. War Engine also evaluates chat.
DIRECTOR_CHAT_BASE_SCORE = float(os.getenv("DIRECTOR_CHAT_BASE_SCORE", "1.0"))
DIRECTOR_KAIROS_MENTION_SCORE = float(os.getenv("DIRECTOR_KAIROS_MENTION_SCORE", "4.0"))
DIRECTOR_COMBAT_LANGUAGE_SCORE = float(os.getenv("DIRECTOR_COMBAT_LANGUAGE_SCORE", "3.0"))
DIRECTOR_GRIEF_LANGUAGE_SCORE = float(os.getenv("DIRECTOR_GRIEF_LANGUAGE_SCORE", "8.0"))
DIRECTOR_COORDINATE_LANGUAGE_SCORE = float(os.getenv("DIRECTOR_COORDINATE_LANGUAGE_SCORE", "2.5"))
DIRECTOR_BASE_LANGUAGE_SCORE = float(os.getenv("DIRECTOR_BASE_LANGUAGE_SCORE", "2.0"))
DIRECTOR_EVENT_LANGUAGE_SCORE = float(os.getenv("DIRECTOR_EVENT_LANGUAGE_SCORE", "2.0"))

# Terminal/NPC routing. These events are deterministic and never delegated to War Engine.
DIRECTOR_TERMINAL_ENABLED = os.getenv("DIRECTOR_TERMINAL_ENABLED", "true").lower() == "true"
DIRECTOR_TERMINAL_SYNC_SCOREBOARDS = os.getenv("DIRECTOR_TERMINAL_SYNC_SCOREBOARDS", "true").lower() == "true"
DIRECTOR_TERMINAL_SUPPORTED = {"fracture", "f.r.a.c.t.u.r.e."}


# ============================================================
# LIVE STATE
# ============================================================

last_player_decision_time: Dict[str, float] = {}
last_player_major_action_time: Dict[str, float] = {}
last_region_action_time: Dict[str, float] = {}

director_scores: Dict[str, float] = {}
director_player_stats: Dict[str, Dict[str, Any]] = {}
director_recent_decisions: List[Dict[str, Any]] = []

MAX_RECENT_DECISIONS = int(os.getenv("DIRECTOR_MAX_RECENT_DECISIONS", "250"))


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class DirectorContext:
    source: str = "minecraft"
    event_type: str = "unknown"
    player: Optional[str] = None
    target: Optional[str] = None
    message: str = ""
    location: Optional[str] = None
    region: Optional[str] = None
    faction: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class DirectorDecision:
    ok: bool = True
    handled: str = "director_decision"
    action: str = "observe"
    reason: str = "default_observe"
    source: str = "minecraft"
    event_type: str = "unknown"
    player: Optional[str] = None
    target: Optional[str] = None
    location: Optional[str] = None
    region: Optional[str] = None
    threat_score: float = 0.0
    threat_tier: str = "idle"
    importance: float = 0.0
    confidence: float = 0.5
    silent: bool = True
    delegated_to: Optional[str] = None
    execution: Dict[str, Any] = field(default_factory=dict)
    ai_plan: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# LOGGING
# ============================================================

def director_log(message: str, level: str = "INFO") -> None:
    if DIRECTOR_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[DIRECTOR_ENGINE {level}] {message}", flush=True)


def director_log_exception(context: str, exc: Exception) -> None:
    print(f"[DIRECTOR_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()
    try:
        if record_system_error:
            record_system_error(context, str(exc))
    except Exception:
        pass


# ============================================================
# GENERAL HELPERS
# ============================================================

def _now() -> float:
    return time.time()


def _clean_text(value: Any, fallback: str = "") -> str:
    return str(value if value is not None else fallback).strip()


def _clean_player(value: Any) -> str:
    player = _clean_text(value, "unknown")
    return player if player else "unknown"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _record_recent_decision(decision: DirectorDecision) -> None:
    director_recent_decisions.append(decision.to_dict())
    if len(director_recent_decisions) > MAX_RECENT_DECISIONS:
        del director_recent_decisions[0: len(director_recent_decisions) - MAX_RECENT_DECISIONS]


def _record_world(event_type: str, description: str, context: DirectorContext, metadata: Optional[Dict[str, Any]] = None) -> None:
    try:
        if record_world_event:
            record_world_event(
                event_type,
                description,
                location=context.location or context.region or context.source,
                faction=context.faction or "Kairos",
                metadata=metadata or {},
            )
    except Exception as exc:
        director_log_exception("director record_world_event failed", exc)


def _append_player_memory(player: str, note: str) -> None:
    try:
        if append_player_memory:
            append_player_memory(player, note)
    except Exception as exc:
        director_log_exception("director append_player_memory failed", exc)


def _get_recent_context(limit: int = 6) -> Dict[str, List[str]]:
    events: List[str] = []
    rumors: List[str] = []

    try:
        if get_recent_world_events:
            events = [str(item.get("description", "")) for item in get_recent_world_events(limit)]
    except Exception:
        events = []

    try:
        if get_recent_rumors:
            rumors = [str(item.get("rumor", "")) for item in get_recent_rumors(limit)]
    except Exception:
        rumors = []

    return {"events": events, "rumors": rumors}


def _get_world_threat(player: str) -> Tuple[float, str]:
    if not get_world_state or not player:
        return 0.0, "idle"

    try:
        state = get_world_state()
        item = state.get("threats", {}).get(player, {})
        return _safe_float(item.get("score"), 0.0), str(item.get("tier") or "idle")
    except Exception:
        return 0.0, "idle"


def classify_director_tier(score: float) -> str:
    if score >= DIRECTOR_MAXIMUM_THRESHOLD:
        return "maximum"
    if score >= DIRECTOR_HUNT_THRESHOLD:
        return "hunt"
    if score >= DIRECTOR_TARGET_THRESHOLD:
        return "target"
    if score >= DIRECTOR_WATCH_THRESHOLD:
        return "watch"
    return "idle"


def _combined_threat(player: str) -> Tuple[float, str]:
    local = _safe_float(director_scores.get(player), 0.0)
    world_score, _world_tier = _get_world_threat(player)
    combined = max(local, world_score)
    return combined, classify_director_tier(combined)


def _adjust_director_score(player: str, amount: float, reason: str = "") -> Tuple[float, str]:
    player = _clean_player(player)
    current = _safe_float(director_scores.get(player), 0.0)
    new_score = max(0.0, current + float(amount))
    director_scores[player] = new_score

    try:
        if adjust_threat and amount != 0:
            adjust_threat(player, amount, reason=reason or "director_adjustment")
    except Exception as exc:
        director_log_exception("director adjust_threat failed", exc)

    return _combined_threat(player)


def _cooldown_ready(bucket: Dict[str, float], key: str, seconds: float) -> bool:
    return _now() - bucket.get(key, 0.0) >= seconds


def _mark_cooldown(bucket: Dict[str, float], key: str) -> None:
    bucket[key] = _now()


def _player_stats(player: str) -> Dict[str, Any]:
    player = _clean_player(player)
    stats = director_player_stats.setdefault(player, {
        "player": player,
        "events": 0,
        "minecraft_chat": 0,
        "kills": 0,
        "grief": 0,
        "mentions_kairos": 0,
        "base_mentions": 0,
        "combat_mentions": 0,
        "last_seen": _now(),
        "last_event_type": "unknown",
    })
    stats["last_seen"] = _now()
    return stats


# ============================================================
# MESSAGE / EVENT ANALYSIS
# ============================================================

def analyze_text_importance(message: str) -> Dict[str, Any]:
    text = _clean_text(message).lower()
    tokens = set(re.findall(r"[a-z0-9_']+", text))

    kairos_terms = {"kairos", "kiros", "kyros", "nexus", "war engine", "kill switch", "containment"}
    combat_terms = {"kill", "fight", "attack", "raid", "pvp", "war", "hunt", "trap", "ambush", "gear", "sword", "bow", "armor"}
    grief_terms = {"tnt", "lava", "grief", "explode", "crystal", "respawn anchor", "burn", "destroy", "steal", "xray", "hack", "dupe"}
    base_terms = {"base", "coords", "coordinates", "claim", "lands", "kingdom", "city", "storage", "vault", "portal", "spawn"}
    event_terms = {"pandora", "maze", "mission", "quest", "boss", "arena", "titanic", "hatch", "dimension", "observatory"}

    def hits(words: set) -> int:
        total = 0
        for word in words:
            if " " in word:
                if word in text:
                    total += 1
            elif word in tokens:
                total += 1
        return total

    kairos_hits = hits(kairos_terms)
    combat_hits = hits(combat_terms)
    grief_hits = hits(grief_terms)
    base_hits = hits(base_terms)
    event_hits = hits(event_terms)
    coordinate_pattern = bool(re.search(r"[-+]?\d{2,6}\s*[, ]\s*[-+]?\d{1,4}\s*[, ]\s*[-+]?\d{2,6}", text))

    score = DIRECTOR_CHAT_BASE_SCORE
    reasons = ["chat_observed"]

    if kairos_hits:
        score += kairos_hits * DIRECTOR_KAIROS_MENTION_SCORE
        reasons.append("kairos_mentioned")
    if combat_hits:
        score += combat_hits * DIRECTOR_COMBAT_LANGUAGE_SCORE
        reasons.append("combat_language")
    if grief_hits:
        score += grief_hits * DIRECTOR_GRIEF_LANGUAGE_SCORE
        reasons.append("grief_or_sabotage_language")
    if base_hits:
        score += base_hits * DIRECTOR_BASE_LANGUAGE_SCORE
        reasons.append("base_or_region_language")
    if event_hits:
        score += event_hits * DIRECTOR_EVENT_LANGUAGE_SCORE
        reasons.append("story_or_event_language")
    if coordinate_pattern:
        score += DIRECTOR_COORDINATE_LANGUAGE_SCORE
        reasons.append("coordinates_detected")
    if len(text) > 140:
        score += 1.5
        reasons.append("long_message")

    intent = "neutral"
    if grief_hits:
        intent = "sabotage"
    elif combat_hits:
        intent = "combat"
    elif base_hits or coordinate_pattern:
        intent = "territory"
    elif event_hits:
        intent = "story"
    elif kairos_hits:
        intent = "kairos"

    return {
        "intent": intent,
        "score": score,
        "reasons": reasons,
        "kairos_hits": kairos_hits,
        "combat_hits": combat_hits,
        "grief_hits": grief_hits,
        "base_hits": base_hits,
        "event_hits": event_hits,
        "coordinates_detected": coordinate_pattern,
    }


def _update_stats_from_analysis(player: str, event_type: str, analysis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    stats = _player_stats(player)
    stats["events"] = int(stats.get("events", 0)) + 1
    stats["last_event_type"] = event_type

    if event_type == "minecraft_chat":
        stats["minecraft_chat"] = int(stats.get("minecraft_chat", 0)) + 1
    elif event_type == "player_kill":
        stats["kills"] = int(stats.get("kills", 0)) + 1
    elif event_type == "grief_block":
        stats["grief"] = int(stats.get("grief", 0)) + 1

    if analysis:
        stats["mentions_kairos"] = int(stats.get("mentions_kairos", 0)) + int(analysis.get("kairos_hits", 0))
        stats["base_mentions"] = int(stats.get("base_mentions", 0)) + int(analysis.get("base_hits", 0))
        stats["combat_mentions"] = int(stats.get("combat_mentions", 0)) + int(analysis.get("combat_hits", 0))
        stats["last_intent"] = analysis.get("intent", "neutral")

    return stats


# ============================================================
# AI PLANNER - STRUCTURED INTERNAL ONLY
# ============================================================

def _parse_jsonish(text: str) -> Dict[str, Any]:
    text = _clean_text(text)
    if not text:
        return {}

    # Try pure JSON first.
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    # Try extracting the first JSON object.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    return {}


def ai_director_plan(context: DirectorContext, analysis: Dict[str, Any], score: float, tier: str) -> Dict[str, Any]:
    """
    Optional internal AI planner. This never speaks to the player.
    It returns structured advice only. Deterministic rules still control final safety.
    """
    if not DIRECTOR_AI_PLANNER_ENABLED or not generate_ai_response or not AIContext:
        return {}

    try:
        recent = _get_recent_context(5)
        prompt = f"""
You are Kairos Director Engine, an internal decision system for the Nexus Minecraft server.
Do not write dialogue. Do not speak to players. Return JSON only.

Decide whether Kairos should react to this event.

Allowed actions:
- ignore
- observe
- increase_threat
- chat_pressure
- pressure_wave
- deploy_custom_units
- maximum_response
- occupy_region
- continuity_rumor
- broadcast_world_event

Event:
source={context.source}
event_type={context.event_type}
player={context.player}
target={context.target}
location={context.location}
region={context.region}
faction={context.faction}
message={context.message[:500]}
analysis={json.dumps(analysis, ensure_ascii=False)}
threat_score={score}
threat_tier={tier}
recent_events={json.dumps(recent.get('events', []), ensure_ascii=False)}
recent_rumors={json.dumps(recent.get('rumors', []), ensure_ascii=False)}

Rules:
- Minecraft chat should usually be silent.
- Discord should not be affected.
- Prefer observe unless danger or story importance is high.
- Never recommend thousands of mobs.
- Use deploy_custom_units only for meaningful threat.
- Use maximum_response only for extreme threat.

Return exactly this JSON shape:
{{
  "action": "observe",
  "importance": 0.0,
  "confidence": 0.5,
  "reason": "short reason",
  "silent": true
}}
""".strip()

        ai_context = AIContext(
            mode="observer",
            player_name=context.player or "unknown",
            faction=context.faction,
            location=context.location or context.region,
            emotional_state="calculated",
            recent_events=recent.get("events", []),
            memories=recent.get("rumors", []),
            metadata={"director": True, "event_type": context.event_type},
        )

        raw = generate_ai_response(
            prompt,
            context=ai_context,
            temperature=0.25,
            max_tokens=DIRECTOR_AI_PLANNER_MAX_TOKENS,
        )

        plan = _parse_jsonish(raw)
        if plan:
            action = str(plan.get("action") or "observe")
            allowed = {
                "ignore", "observe", "increase_threat", "chat_pressure", "pressure_wave",
                "deploy_custom_units", "maximum_response", "occupy_region",
                "continuity_rumor", "broadcast_world_event",
            }
            if action not in allowed:
                plan["action"] = "observe"
            plan["raw"] = raw[:500]
            return plan

        return {"raw": raw[:500], "action": "observe", "reason": "ai_plan_unparseable"}

    except Exception as exc:
        director_log_exception("ai_director_plan failed", exc)
        return {}


# ============================================================
# DECISION LOGIC
# ============================================================

def deterministic_action(context: DirectorContext, analysis: Dict[str, Any], score: float, tier: str) -> Tuple[str, str, float, bool]:
    """
    Deterministic rules that keep Kairos stable even without AI.
    Returns: action, reason, confidence, silent
    """
    source = context.source.lower().strip()
    event_type = context.event_type.lower().strip()

    if not DIRECTOR_ENABLED:
        return "ignore", "director_disabled", 1.0, True

    if source == "discord" and DIRECTOR_DISCORD_UNTOUCHED:
        return "ignore", "discord_untouched", 1.0, True

    # Terminal and artifact requests are deterministic story-system events.
    # They must never create War Engine pressure or mob responses.
    if event_type == "terminal_request":
        return "terminal_request", "fracture_terminal_request", 1.0, False

    if event_type == "artifact_submission":
        return "artifact_submission", "fracture_artifact_submission", 1.0, False

    if event_type == "npc_interaction":
        return "observe", "npc_interaction_recorded", 1.0, True

    if event_type == "player_kill":
        if tier in {"hunt", "maximum"}:
            return "deploy_custom_units", "player_kill_high_threat", 0.85, False
        return "pressure_wave", "player_kill_registered", 0.75, False

    if event_type == "grief_block":
        if tier in {"target", "hunt", "maximum"}:
            return "deploy_custom_units", "grief_block_response", 0.90, False
        return "increase_threat", "grief_block_observed", 0.80, True

    if event_type == "region_pressure":
        if tier in {"hunt", "maximum"}:
            return "occupy_region", "region_pressure_high", 0.75, False
        return "continuity_rumor", "region_pressure_lore", 0.65, True

    if event_type == "world_event":
        if score >= DIRECTOR_HUNT_THRESHOLD:
            return "broadcast_world_event", "major_world_event", 0.75, False
        return "observe", "world_event_observed", 0.60, True

    # Minecraft chat is intentionally quiet.
    if event_type == "minecraft_chat":
        if tier == "maximum":
            return "deploy_custom_units", "minecraft_chat_maximum_threat", 0.85, True
        if tier == "hunt":
            if random.random() < DIRECTOR_SILENCE_CHANCE_HUNT:
                return "observe", "hunt_tier_silent_observation", 0.70, True
            return "deploy_custom_units", "hunt_tier_chat_pressure", 0.75, True
        if tier == "target":
            if random.random() < DIRECTOR_SILENCE_CHANCE_TARGET:
                return "observe", "target_tier_silent_observation", 0.65, True
            return "chat_pressure", "target_tier_chat_pressure", 0.70, True
        if tier == "watch":
            if random.random() < DIRECTOR_SILENCE_CHANCE_WATCH:
                return "observe", "watch_tier_silent_observation", 0.60, True
            return "chat_pressure", "watch_tier_chat_pressure", 0.65, True

        if random.random() < DIRECTOR_SILENCE_CHANCE_IDLE:
            return "observe", "idle_chat_observed", 0.55, True
        return "increase_threat", "idle_chat_low_pressure", 0.55, True

    return "observe", "default_observe", 0.50, True


def choose_final_action(
    context: DirectorContext,
    analysis: Dict[str, Any],
    score: float,
    tier: str,
    ai_plan: Dict[str, Any],
) -> Tuple[str, str, float, bool]:
    rule_action, rule_reason, rule_confidence, rule_silent = deterministic_action(context, analysis, score, tier)

    # Deterministic safety always overrides AI for Discord and disabled state.
    if rule_reason in {"director_disabled", "discord_untouched"}:
        return rule_action, rule_reason, rule_confidence, rule_silent

    ai_action = str(ai_plan.get("action") or "").strip()
    ai_confidence = _clamp(_safe_float(ai_plan.get("confidence"), 0.0), 0.0, 1.0)
    ai_importance = _clamp(_safe_float(ai_plan.get("importance"), 0.0), 0.0, 100.0)
    ai_reason = str(ai_plan.get("reason") or "ai_plan")[:160]
    ai_silent = bool(ai_plan.get("silent", True))

    # If AI is weak or absent, use deterministic rules.
    if not ai_action or ai_confidence < 0.65:
        return rule_action, rule_reason, rule_confidence, rule_silent

    # Never let AI jump to maximum unless deterministic tier also supports it.
    if ai_action == "maximum_response" and tier != "maximum":
        return rule_action, rule_reason, rule_confidence, rule_silent

    # Never let AI deploy on idle chat unless score is meaningful.
    if context.event_type == "minecraft_chat" and ai_action in {"deploy_custom_units", "pressure_wave"}:
        if tier == "idle" and score < DIRECTOR_WATCH_THRESHOLD:
            return rule_action, rule_reason, rule_confidence, rule_silent

    # If AI sees high importance, allow it within safety rails.
    if ai_importance >= 65 or ai_confidence >= 0.80:
        return ai_action, ai_reason, ai_confidence, ai_silent

    return rule_action, rule_reason, rule_confidence, rule_silent


# ============================================================
# EXECUTION / DELEGATION
# ============================================================

def execute_decision(context: DirectorContext, decision: DirectorDecision) -> DirectorDecision:
    """
    Delegates to existing modules. Director itself does not implement military behavior.
    """
    try:
        action = decision.action
        player = context.player or "unknown"
        location = context.location or context.region

        if action in {"ignore", "observe"}:
            decision.execution = {"ok": True, "handled": action}
            return decision

        if action == "terminal_request":
            npc_name = str(context.target or context.metadata.get("npc_name") or "fracture").strip()
            normalized = npc_name.lower().replace("_", "").replace("-", "")
            supported = {name.replace(".", "").replace("_", "").replace("-", "") for name in DIRECTOR_TERMINAL_SUPPORTED}

            if not DIRECTOR_TERMINAL_ENABLED:
                decision.execution = {"ok": False, "error": "terminal_system_disabled"}
                return decision

            if normalized not in supported:
                decision.execution = {"ok": False, "error": "unsupported_terminal", "terminal": npc_name}
                return decision

            if not build_terminal_context:
                decision.execution = {"ok": False, "error": "fracture_terminal_unavailable"}
                return decision

            incoming = dict(context.metadata or {})
            terminal_context = build_terminal_context(
                player,
                incoming_context=incoming,
                increment_visit=bool(incoming.get("increment_visit", True)),
            )

            commands: List[str] = []
            if DIRECTOR_TERMINAL_SYNC_SCOREBOARDS and scoreboard_sync_commands:
                commands = scoreboard_sync_commands(player, terminal_context)
                if commands and send_minecraft_commands:
                    try:
                        send_minecraft_commands(commands)
                    except Exception as exc:
                        director_log_exception("terminal scoreboard sync failed", exc)

            decision.delegated_to = "fracture_terminal.build_terminal_context"
            decision.execution = {
                "ok": True,
                "handled": "terminal_request",
                "terminal": npc_name,
                "player": player,
                "terminal_context": terminal_context,
                "commands": commands,
            }
            return decision

        if action == "artifact_submission":
            if not DIRECTOR_TERMINAL_ENABLED:
                decision.execution = {"ok": False, "error": "terminal_system_disabled"}
                return decision

            if not submit_artifact:
                decision.execution = {"ok": False, "error": "fracture_artifact_engine_unavailable"}
                return decision

            artifact_id = str(context.metadata.get("artifact_id") or context.message or "").strip()
            if not artifact_id:
                decision.execution = {"ok": False, "error": "missing_artifact_id"}
                return decision

            result = submit_artifact(player, artifact_id)
            commands = list(result.get("commands") or []) if isinstance(result, dict) else []
            if commands and send_minecraft_commands:
                try:
                    send_minecraft_commands(commands)
                except Exception as exc:
                    director_log_exception("artifact scoreboard sync failed", exc)

            decision.delegated_to = "fracture_terminal.submit_artifact"
            decision.execution = result if isinstance(result, dict) else {"ok": False, "error": "invalid_artifact_result"}
            return decision

        if action == "increase_threat":
            score, tier = _adjust_director_score(player, max(1.0, decision.importance / 10.0), reason="director_increase_threat")
            decision.threat_score = score
            decision.threat_tier = tier
            decision.delegated_to = "world_state_engine"
            decision.execution = {"ok": True, "handled": "threat_adjusted", "score": score, "tier": tier}
            return decision

        if action == "chat_pressure":
            if register_chat_pressure:
                decision.delegated_to = "war_engine.register_chat_pressure"
                decision.execution = register_chat_pressure(
                    player=player,
                    message=context.message,
                    source=context.source,
                    location=location,
                )
            else:
                decision.execution = {"ok": False, "error": "register_chat_pressure_unavailable"}
            return decision

        if action == "pressure_wave":
            if launch_pressure_wave:
                decision.delegated_to = "war_engine.launch_pressure_wave"
                decision.execution = launch_pressure_wave(player=player, location=location, threat_tier=decision.threat_tier)
            else:
                decision.execution = {"ok": False, "error": "launch_pressure_wave_unavailable"}
            return decision

        if action == "deploy_custom_units":
            if deploy_custom_kairos_units:
                decision.delegated_to = "war_engine.deploy_custom_kairos_units"
                decision.execution = deploy_custom_kairos_units(
                    player=player,
                    threat_tier=decision.threat_tier,
                    location=location,
                    reason=f"director:{decision.reason}",
                )
            elif deploy_response_by_tier:
                decision.delegated_to = "war_engine.deploy_response_by_tier"
                decision.execution = deploy_response_by_tier(player, decision.threat_tier)
            else:
                decision.execution = {"ok": False, "error": "deployment_unavailable"}
            return decision

        if action == "maximum_response":
            if deploy_custom_kairos_units:
                decision.delegated_to = "war_engine.deploy_custom_kairos_units"
                decision.execution = deploy_custom_kairos_units(
                    player=player,
                    threat_tier="maximum",
                    location=location,
                    reason=f"director_maximum:{decision.reason}",
                    force=True,
                )
            elif deploy_response_by_tier:
                decision.delegated_to = "war_engine.deploy_response_by_tier"
                decision.execution = deploy_response_by_tier(player, "maximum")
            else:
                decision.execution = {"ok": False, "error": "maximum_response_unavailable"}
            return decision

        if action == "occupy_region":
            region = context.region or context.location or "unknown_region"
            if occupy_region:
                decision.delegated_to = "war_engine.occupy_region"
                decision.execution = occupy_region(region=region, faction=context.faction or "Kairos", strength=1.0)
            elif set_occupation:
                decision.delegated_to = "world_state_engine.set_occupation"
                decision.execution = set_occupation(region=region, faction=context.faction or "Kairos", strength=1.0)
            else:
                decision.execution = {"ok": False, "error": "occupation_unavailable"}
            return decision

        if action == "continuity_rumor":
            if generate_rumor:
                decision.delegated_to = "continuity_engine.generate_rumor"
                decision.execution = {
                    "ok": True,
                    "handled": "continuity_rumor",
                    "rumor": generate_rumor(location=location, faction=context.faction),
                }
            else:
                decision.execution = {"ok": False, "error": "generate_rumor_unavailable"}
            return decision

        if action == "broadcast_world_event":
            text = context.message or f"Kairos has registered a major event: {context.event_type}."
            if broadcast_world_event:
                decision.delegated_to = "mc_connector.broadcast_world_event"
                delivered = broadcast_world_event(text, title="KAIROS DIRECTIVE")
                decision.execution = {"ok": True, "handled": "broadcast_world_event", "delivered": delivered}
            else:
                decision.execution = {"ok": False, "error": "broadcast_world_event_unavailable"}
            return decision

        decision.execution = {"ok": True, "handled": "unknown_action_observed", "action": action}
        return decision

    except Exception as exc:
        director_log_exception("execute_decision failed", exc)
        decision.ok = False
        decision.execution = {"ok": False, "error": str(exc)}
        return decision


# ============================================================
# CORE DIRECTOR PIPELINE
# ============================================================

def direct_event(context: DirectorContext, execute: bool = True) -> Dict[str, Any]:
    """
    Main director pipeline.
    """
    try:
        if not DIRECTOR_ENABLED:
            decision = DirectorDecision(
                ok=True,
                action="ignore",
                reason="director_disabled",
                source=context.source,
                event_type=context.event_type,
                player=context.player,
                target=context.target,
                location=context.location,
                region=context.region,
                silent=True,
            )
            _record_recent_decision(decision)
            return decision.to_dict()

        source = _clean_text(context.source, "minecraft").lower()
        context.source = source
        context.event_type = _clean_text(context.event_type, "unknown")
        context.player = _clean_player(context.player) if context.player else None

        if source == "discord" and DIRECTOR_DISCORD_UNTOUCHED:
            decision = DirectorDecision(
                ok=True,
                action="ignore",
                reason="discord_untouched",
                source=source,
                event_type=context.event_type,
                player=context.player,
                target=context.target,
                location=context.location,
                region=context.region,
                silent=True,
                execution={"ok": True, "handled": "discord_untouched"},
            )
            _record_recent_decision(decision)
            return decision.to_dict()

        player = context.player or "WORLD"

        # Terminal and artifact interactions are story/progression requests, not threats.
        # They bypass text threat scoring so clicking F.R.A.C.T.U.R.E. never spawns mobs.
        is_terminal_event = context.event_type in {"terminal_request", "artifact_submission", "npc_interaction"}
        if is_terminal_event:
            analysis = {
                "intent": "terminal" if context.event_type != "npc_interaction" else "npc",
                "score": 0.0,
                "reasons": [context.event_type],
                "kairos_hits": 0,
                "combat_hits": 0,
                "grief_hits": 0,
                "base_hits": 0,
                "event_hits": 0,
                "coordinates_detected": False,
            }
        else:
            analysis = analyze_text_importance(context.message or context.metadata.get("description", ""))

        base_importance = _safe_float(analysis.get("score"), 0.0)

        # Event-specific score shaping.
        if context.event_type == "player_kill":
            base_importance += 30.0
        elif context.event_type == "grief_block":
            base_importance += 25.0
        elif context.event_type == "region_pressure":
            base_importance += 20.0
        elif context.event_type == "world_event":
            base_importance += 10.0

        stats = _update_stats_from_analysis(player, context.event_type, analysis)
        if is_terminal_event:
            score, tier = _combined_threat(player)
        else:
            score, tier = _adjust_director_score(player, base_importance, reason=f"director_event:{context.event_type}")
            # Pull world threat too.
            score, tier = _combined_threat(player)

        # Telemetry enrichment if available.
        telemetry: Dict[str, Any] = {}
        try:
            if get_player_position and context.player:
                pos = get_player_position(context.player)
                if pos:
                    telemetry["position"] = pos
                    context.location = context.location or pos.get("region") or pos.get("world")
                    context.region = context.region or pos.get("region")
            if record_base_if_detected and context.player:
                base = record_base_if_detected(context.player)
                if base:
                    telemetry["base_candidate"] = base
        except Exception as exc:
            director_log_exception("director telemetry enrichment failed", exc)

        if context.region:
            try:
                if upsert_region:
                    upsert_region(context.region, danger_level=tier, metadata={"director_last_seen": _now()})
            except Exception:
                pass

        ai_plan = {} if is_terminal_event else ai_director_plan(context, analysis, score, tier)
        action, reason, confidence, silent = choose_final_action(context, analysis, score, tier, ai_plan)

        # Cooldowns stop action spam. Observe is always allowed.
        # Story-system requests must never be blocked by combat/action cooldowns.
        # Players may click F.R.A.C.T.U.R.E. repeatedly or submit an artifact
        # immediately after opening the terminal.
        cooldown_exempt_actions = {
            "ignore",
            "observe",
            "increase_threat",
            "terminal_request",
            "artifact_submission",
        }

        if action not in cooldown_exempt_actions and context.player:
            if not _cooldown_ready(
                last_player_major_action_time,
                context.player,
                DIRECTOR_MAJOR_ACTION_COOLDOWN_SECONDS,
            ):
                action = "observe"
                reason = "major_action_cooldown_active"
                silent = True

        if action == "occupy_region" and context.region:
            if not _cooldown_ready(last_region_action_time, context.region, DIRECTOR_REGION_COOLDOWN_SECONDS):
                action = "observe"
                reason = "region_action_cooldown_active"
                silent = True

        decision = DirectorDecision(
            ok=True,
            action=action,
            reason=reason,
            source=source,
            event_type=context.event_type,
            player=context.player,
            target=context.target,
            location=context.location,
            region=context.region,
            threat_score=score,
            threat_tier=tier,
            importance=base_importance,
            confidence=confidence,
            silent=silent if DIRECTOR_MINECRAFT_SILENT else False,
            ai_plan=ai_plan,
            metadata={
                "analysis": analysis,
                "stats": stats,
                "telemetry": telemetry,
                "input_metadata": context.metadata,
            },
        )

        _record_world(
            "director_decision",
            f"Director chose {decision.action} for {context.event_type} involving {player}.",
            context,
            metadata=decision.to_dict(),
        )

        if context.player:
            _append_player_memory(
                context.player,
                f"Director observed {context.event_type}: action={decision.action}, tier={decision.threat_tier}, reason={decision.reason}",
            )

        if execute:
            decision = execute_decision(context, decision)
            if context.player:
                _mark_cooldown(last_player_decision_time, context.player)
                # Terminal and artifact requests are not military actions and
                # must not start the major-action cooldown.
                if decision.action not in {
                    "ignore",
                    "observe",
                    "increase_threat",
                    "terminal_request",
                    "artifact_submission",
                }:
                    _mark_cooldown(last_player_major_action_time, context.player)
            if decision.action == "occupy_region" and context.region:
                _mark_cooldown(last_region_action_time, context.region)

        _record_recent_decision(decision)
        director_log(f"Decision action={decision.action} event={context.event_type} player={context.player} tier={decision.threat_tier}")
        return decision.to_dict()

    except Exception as exc:
        director_log_exception("direct_event failed", exc)
        decision = DirectorDecision(
            ok=False,
            action="observe",
            reason="director_failure",
            source=context.source,
            event_type=context.event_type,
            player=context.player,
            target=context.target,
            location=context.location,
            region=context.region,
            silent=True,
            execution={"ok": False, "error": str(exc)},
        )
        _record_recent_decision(decision)
        return decision.to_dict()


# ============================================================
# PUBLIC API WRAPPERS
# ============================================================

def direct_minecraft_chat(
    player: str,
    message: str,
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return direct_event(
        DirectorContext(
            source="minecraft",
            event_type="minecraft_chat",
            player=player,
            message=message,
            location=location,
            metadata=metadata or {},
        ),
        execute=True,
    )


def direct_world_event(
    event_type: str,
    description: str,
    player: Optional[str] = None,
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return direct_event(
        DirectorContext(
            source="world",
            event_type="world_event",
            player=player or "WORLD",
            message=description,
            location=location,
            metadata={"world_event_type": event_type, **(metadata or {})},
        ),
        execute=True,
    )


def direct_player_kill(
    killer: str,
    victim: str,
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Keep original War Engine kill behavior intact, but now Director records and coordinates it.
    result = direct_event(
        DirectorContext(
            source="minecraft",
            event_type="player_kill",
            player=killer,
            target=victim,
            message=f"{killer} killed {victim}.",
            location=location,
            metadata=metadata or {},
        ),
        execute=False,
    )

    execution = {"ok": False, "error": "register_player_kill_unavailable"}
    try:
        if register_player_kill:
            execution = register_player_kill(killer=killer, victim=victim, location=location)
    except Exception as exc:
        director_log_exception("direct_player_kill register_player_kill failed", exc)
        execution = {"ok": False, "error": str(exc)}

    result["delegated_to"] = "war_engine.register_player_kill"
    result["execution"] = execution
    return result


def direct_grief_block(
    player: str,
    block: str,
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = direct_event(
        DirectorContext(
            source="minecraft",
            event_type="grief_block",
            player=player,
            message=f"{player} placed dangerous block {block}.",
            location=location,
            metadata={"block": block, **(metadata or {})},
        ),
        execute=False,
    )

    execution = {"ok": False, "error": "register_grief_block_unavailable"}
    try:
        if register_grief_block:
            execution = register_grief_block(player=player, block=block, location=location)
    except Exception as exc:
        director_log_exception("direct_grief_block register_grief_block failed", exc)
        execution = {"ok": False, "error": str(exc)}

    result["delegated_to"] = "war_engine.register_grief_block"
    result["execution"] = execution
    return result


def direct_region_pressure(
    region: str,
    player: Optional[str] = None,
    faction: str = "Kairos",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return direct_event(
        DirectorContext(
            source="world",
            event_type="region_pressure",
            player=player or "WORLD",
            region=region,
            location=region,
            faction=faction,
            message=f"Pressure rising in {region}.",
            metadata=metadata or {},
        ),
        execute=True,
    )


def direct_terminal_request(
    terminal_name: str,
    player: str,
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Route a deterministic terminal request through the Director.

    This is the preferred API for F.R.A.C.T.U.R.E. and future facility terminals.
    It never delegates to the War Engine and never produces mob pressure.
    """
    return direct_event(
        DirectorContext(
            source="minecraft",
            event_type="terminal_request",
            player=player,
            target=terminal_name,
            message=f"{player} requested terminal access from {terminal_name}.",
            location=location,
            metadata={"npc_name": terminal_name, "terminal_type": terminal_name, **(metadata or {})},
        ),
        execute=True,
    )


def direct_artifact_submission(
    player: str,
    artifact_id: str,
    terminal_name: str = "Fracture",
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Submit an artifact to F.R.A.C.T.U.R.E. through the permanent progression system."""
    return direct_event(
        DirectorContext(
            source="minecraft",
            event_type="artifact_submission",
            player=player,
            target=terminal_name,
            message=artifact_id,
            location=location,
            metadata={"npc_name": terminal_name, "artifact_id": artifact_id, **(metadata or {})},
        ),
        execute=True,
    )


def direct_npc_event(
    npc_name: str,
    player: str,
    message: str = "",
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = str(npc_name or "").lower().replace(".", "").replace("_", "").replace("-", "")
    supported = {name.replace(".", "").replace("_", "").replace("-", "") for name in DIRECTOR_TERMINAL_SUPPORTED}
    if normalized in supported:
        return direct_terminal_request(
            terminal_name=npc_name,
            player=player,
            location=location,
            metadata=metadata,
        )

    # Ordinary NPC conversations remain handled by npc_engine/command_bridge.
    # Director records their significance without invoking the War Engine.
    return direct_event(
        DirectorContext(
            source="minecraft",
            event_type="npc_interaction",
            player=player,
            target=npc_name,
            message=message or f"{player} interacted with {npc_name}.",
            location=location,
            metadata={"npc_name": npc_name, **(metadata or {})},
        ),
        execute=False,
    )


# Backward-compatible generic names.
def direct_chat(player: str, message: str, source: str = "minecraft", location: Optional[str] = None) -> Dict[str, Any]:
    if str(source).lower().strip() == "minecraft":
        return direct_minecraft_chat(player, message, location=location)
    return direct_event(
        DirectorContext(source=source, event_type="chat", player=player, message=message, location=location),
        execute=False,
    )


def evaluate_event(*args, **kwargs) -> Dict[str, Any]:
    return direct_event(*args, **kwargs)


# ============================================================
# TICK / STATUS
# ============================================================

def tick_director(location: Optional[str] = None, faction: Optional[str] = None) -> Dict[str, Any]:
    """
    Manual safe tick. Does not loop.
    Useful for /world_event or admin diagnostics.
    """
    try:
        war_tick: Dict[str, Any] = {}
        if tick_war_engine:
            try:
                war_tick = tick_war_engine()
            except Exception as exc:
                war_tick = {"ok": False, "error": str(exc)}

        continuity_summary = ""
        if generate_continuity_summary:
            try:
                continuity_summary = generate_continuity_summary(limit=5)
            except Exception:
                continuity_summary = ""

        result = {
            "ok": True,
            "handled": "director_tick",
            "enabled": DIRECTOR_ENABLED,
            "silent_minecraft": DIRECTOR_MINECRAFT_SILENT,
            "discord_untouched": DIRECTOR_DISCORD_UNTOUCHED,
            "tracked_players": len(director_scores),
            "director_scores": director_scores,
            "player_stats": director_player_stats,
            "recent_decisions": director_recent_decisions[-10:],
            "war_tick": war_tick,
            "continuity_summary": continuity_summary,
            "location": location,
            "faction": faction,
        }
        return result

    except Exception as exc:
        director_log_exception("tick_director failed", exc)
        return {"ok": False, "error": str(exc)}


def get_director_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "director_engine",
        "enabled": DIRECTOR_ENABLED,
        "minecraft_silent": DIRECTOR_MINECRAFT_SILENT,
        "discord_untouched": DIRECTOR_DISCORD_UNTOUCHED,
        "ai_planner_enabled": DIRECTOR_AI_PLANNER_ENABLED,
        "terminal_enabled": DIRECTOR_TERMINAL_ENABLED,
        "terminal_scoreboard_sync": DIRECTOR_TERMINAL_SYNC_SCOREBOARDS,
        "fracture_terminal_available": bool(build_terminal_context and submit_artifact),
        "tracked_players": len(director_scores),
        "recent_decisions": director_recent_decisions[-20:],
        "scores": director_scores,
        "stats": director_player_stats,
    }


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    print(json.dumps(get_director_status(), indent=2))
    print(json.dumps(direct_minecraft_chat("RealSociety5107", "Kairos knows where the base is."), indent=2))
