"""
war_engine.py
Kairos / Nexus War Engine v2

Drop-in replacement design:
- Preserves the original public functions and behavior.
- Adds Minecraft chat pressure integration for silent Kairos escalation.
- Adds controlled custom unit deployment with cooldowns and caps.
- Keeps Discord untouched. This module only acts when called by Minecraft-side routing.

Original preserved APIs include:
- launch_pressure_wave
- deploy_hunter_squad
- deploy_containment_force
- deploy_maximum_response
- deploy_response_by_tier
- register_player_kill
- register_grief_block
- occupy_region
- tick_war_engine

New primary API:
- register_chat_pressure(player, message, source="minecraft", location=None)
"""

from __future__ import annotations

import os
import random
import re
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from ai_core import AIContext, generate_ai_response
from mc_connector import send_minecraft_commands, send_chat, send_actionbar, broadcast_world_event
from memory_engine import record_world_event, record_system_error
from world_state_engine import adjust_threat, set_occupation, upsert_region


# ============================================================
# CONFIG
# ============================================================

WAR_DEBUG = os.getenv("WAR_ENGINE_DEBUG", "true").lower() == "true"

MAX_WAVE_SIZE = int(os.getenv("WAR_MAX_WAVE_SIZE", "4"))
WAVE_COOLDOWN_SECONDS = float(os.getenv("WAR_WAVE_COOLDOWN_SECONDS", "60"))
MAX_GLOBAL_ACTIVE_OPERATIONS = int(os.getenv("WAR_MAX_GLOBAL_ACTIVE_OPERATIONS", "25"))

MOB_DEPLOYMENT_ENABLED = os.getenv("WAR_MOB_DEPLOYMENT_ENABLED", "true").lower() == "true"

# New Minecraft chat pressure controls.
# These are intentionally conservative so Kairos does not flood the server.
CHAT_PRESSURE_ENABLED = os.getenv("WAR_CHAT_PRESSURE_ENABLED", "true").lower() == "true"
CHAT_PRESSURE_COOLDOWN_SECONDS = float(os.getenv("WAR_CHAT_PRESSURE_COOLDOWN_SECONDS", "45"))
CHAT_PRESSURE_OBSERVE_ONLY_CHANCE = float(os.getenv("WAR_CHAT_PRESSURE_OBSERVE_ONLY_CHANCE", "0.55"))
CHAT_PRESSURE_MAX_MOBS_PER_DEPLOYMENT = int(os.getenv("WAR_CHAT_PRESSURE_MAX_MOBS_PER_DEPLOYMENT", "4"))
CHAT_PRESSURE_MAX_ESCALATION_MOBS = int(os.getenv("WAR_CHAT_PRESSURE_MAX_ESCALATION_MOBS", "8"))
CHAT_PRESSURE_SILENT_MODE = os.getenv("WAR_CHAT_PRESSURE_SILENT_MODE", "true").lower() == "true"
CHAT_PRESSURE_MIN_MESSAGE_LENGTH = int(os.getenv("WAR_CHAT_PRESSURE_MIN_MESSAGE_LENGTH", "2"))

# Threat decay is only applied when a player is evaluated.
THREAT_DECAY_PER_MINUTE = float(os.getenv("WAR_THREAT_DECAY_PER_MINUTE", "0.20"))
MIN_SECONDS_BETWEEN_THREAT_DECAY = float(os.getenv("WAR_MIN_SECONDS_BETWEEN_THREAT_DECAY", "60"))

# Optional quick disable for the custom unit system while keeping old deployments alive.
CUSTOM_UNIT_DEPLOYMENT_ENABLED = os.getenv("WAR_CUSTOM_UNIT_DEPLOYMENT_ENABLED", "true").lower() == "true"


# ============================================================
# LIVE STATE
# ============================================================

active_operations: Dict[str, Dict[str, Any]] = {}
last_wave_time: Dict[str, float] = {}

player_kill_counts: Dict[str, int] = {}
player_grief_scores: Dict[str, int] = {}

# New chat/threat live state.
chat_pressure_counts: Dict[str, int] = {}
chat_pressure_score: Dict[str, float] = {}
last_chat_pressure_time: Dict[str, float] = {}
last_threat_decay_time: Dict[str, float] = {}
player_behavior_profile: Dict[str, Dict[str, Any]] = {}
last_custom_deployment_time: Dict[str, float] = {}

UNIT_TYPES = [
    "Scout",
    "Raider",
    "Hunter",
    "Enforcer",
    "Sentinel",
]

CUSTOM_UNIT_TYPES = [
    "Observer",
    "Scanner",
    "Hunter",
    "Suppressor",
    "Juggernaut",
    "WardenPrime",
]


# ============================================================
# LOGGING
# ============================================================

def war_log(message: str, level: str = "INFO") -> None:
    if WAR_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[WAR_ENGINE {level}] {message}", flush=True)


def war_log_exception(context: str, exc: Exception) -> None:
    print(f"[WAR_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()
    try:
        record_system_error(context, str(exc))
    except Exception:
        pass


# ============================================================
# GENERAL HELPERS
# ============================================================

def _clean_player(player: Any) -> str:
    text = str(player or "unknown").strip()
    # Minecraft selector names should not contain spaces in this context.
    if not text:
        return "unknown"
    return text


def _clean_message(message: Any) -> str:
    return str(message or "").strip()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _json_text(text: str, color: str = "white") -> str:
    # Keep command JSON safe for quotes and slashes.
    escaped = str(text).replace('\\', '\\\\').replace('"', '\\"')
    return f'{{"text":"{escaped}","color":"{color}"}}'


def _now() -> float:
    return time.time()


def generate_operation_id(player: str) -> str:
    return f"op_{player}_{int(time.time())}_{random.randint(1000,9999)}"


def can_launch_wave(player: str) -> bool:
    now = time.time()
    return now - last_wave_time.get(player, 0) >= WAVE_COOLDOWN_SECONDS


def can_launch_chat_pressure(player: str) -> bool:
    now = time.time()
    return now - last_chat_pressure_time.get(player, 0) >= CHAT_PRESSURE_COOLDOWN_SECONDS


def can_launch_custom_deployment(player: str, minimum_seconds: Optional[float] = None) -> bool:
    cooldown = CHAT_PRESSURE_COOLDOWN_SECONDS if minimum_seconds is None else float(minimum_seconds)
    return _now() - last_custom_deployment_time.get(player, 0) >= cooldown


# ============================================================
# THREAT / PROFILE HELPERS
# ============================================================

def classify_threat_score(score: float) -> str:
    if score >= 160:
        return "maximum"
    if score >= 95:
        return "hunt"
    if score >= 45:
        return "target"
    if score >= 20:
        return "watch"
    return "idle"


def get_local_threat_score(player: str) -> float:
    return float(chat_pressure_score.get(player, 0.0))


def decay_local_threat(player: str) -> float:
    player = _clean_player(player)
    now = _now()
    last = last_threat_decay_time.get(player, now)
    elapsed = now - last

    if elapsed < MIN_SECONDS_BETWEEN_THREAT_DECAY:
        return get_local_threat_score(player)

    current = get_local_threat_score(player)
    minutes = elapsed / 60.0
    new_score = max(0.0, current - (THREAT_DECAY_PER_MINUTE * minutes))

    chat_pressure_score[player] = new_score
    last_threat_decay_time[player] = now
    return new_score


def add_local_threat(player: str, amount: float, reason: str = "") -> Tuple[float, str]:
    player = _clean_player(player)
    decay_local_threat(player)

    new_score = max(0.0, get_local_threat_score(player) + float(amount))
    chat_pressure_score[player] = new_score

    # Also write to the persistent-ish world state engine.
    try:
        adjust_threat(player, float(amount), reason=reason or "war_engine_local_threat")
    except Exception as exc:
        war_log_exception("adjust_threat failed inside add_local_threat", exc)

    return new_score, classify_threat_score(new_score)


def get_or_create_behavior_profile(player: str) -> Dict[str, Any]:
    player = _clean_player(player)
    profile = player_behavior_profile.setdefault(player, {
        "player": player,
        "messages": 0,
        "provocations": 0,
        "combat_words": 0,
        "building_words": 0,
        "exploration_words": 0,
        "resource_words": 0,
        "last_seen": _now(),
        "classification": "unknown",
    })
    profile["last_seen"] = _now()
    return profile


def classify_message_intent(message: str) -> Dict[str, Any]:
    text = _clean_message(message).lower()

    combat_words = {
        "kill", "fight", "pvp", "raid", "war", "attack", "hunt", "murder", "destroy",
        "warden", "sword", "bow", "axe", "trident", "gear", "armor", "trap",
    }
    grief_words = {
        "tnt", "lava", "grief", "blow", "explode", "crystal", "destroy", "burn", "steal",
        "stealing", "dupe", "hack", "cheat", "xray",
    }
    build_words = {
        "base", "build", "house", "city", "kingdom", "wall", "farm", "redstone", "claim",
        "land", "lands", "portal", "shop", "storage",
    }
    explore_words = {
        "where", "coords", "coordinates", "dimension", "pandora", "maze", "cave", "portal",
        "travel", "map", "spawn", "titanic", "mission", "quest",
    }
    resource_words = {
        "diamond", "netherite", "iron", "gold", "money", "cash", "shop", "buy", "sell",
        "elytra", "rocket", "food", "coal",
    }
    kairos_words = {
        "kairos", "kyros", "kiros", "kill switch", "war engine", "ai", "nexus", "containment",
    }

    tokens = set(re.findall(r"[a-z0-9_']+", text))

    score = 1.0
    reasons: List[str] = ["minecraft_chat_observed"]

    def count_hits(words: set) -> int:
        hits = 0
        for word in words:
            if " " in word:
                if word in text:
                    hits += 1
            elif word in tokens:
                hits += 1
        return hits

    combat_hits = count_hits(combat_words)
    grief_hits = count_hits(grief_words)
    build_hits = count_hits(build_words)
    explore_hits = count_hits(explore_words)
    resource_hits = count_hits(resource_words)
    kairos_hits = count_hits(kairos_words)

    if combat_hits:
        score += combat_hits * 2.0
        reasons.append("combat_language")
    if grief_hits:
        score += grief_hits * 4.0
        reasons.append("grief_or_sabotage_language")
    if build_hits:
        score += build_hits * 0.75
        reasons.append("infrastructure_or_base_language")
    if explore_hits:
        score += explore_hits * 1.25
        reasons.append("exploration_language")
    if resource_hits:
        score += resource_hits * 0.75
        reasons.append("resource_language")
    if kairos_hits:
        score += kairos_hits * 2.5
        reasons.append("kairos_mentioned")

    if len(text) > 120:
        score += 1.0
        reasons.append("long_message")

    if "?" in text:
        score += 0.25

    intent = "neutral"
    if grief_hits:
        intent = "sabotage"
    elif combat_hits:
        intent = "combat"
    elif explore_hits:
        intent = "exploration"
    elif build_hits:
        intent = "builder"
    elif resource_hits:
        intent = "resource"

    return {
        "intent": intent,
        "score": score,
        "reasons": reasons,
        "combat_hits": combat_hits,
        "grief_hits": grief_hits,
        "build_hits": build_hits,
        "explore_hits": explore_hits,
        "resource_hits": resource_hits,
        "kairos_hits": kairos_hits,
    }


def update_behavior_profile(player: str, message: str, intent_data: Dict[str, Any]) -> Dict[str, Any]:
    profile = get_or_create_behavior_profile(player)
    profile["messages"] = int(profile.get("messages", 0)) + 1
    profile["provocations"] = int(profile.get("provocations", 0)) + int(intent_data.get("kairos_hits", 0))
    profile["combat_words"] = int(profile.get("combat_words", 0)) + int(intent_data.get("combat_hits", 0))
    profile["building_words"] = int(profile.get("building_words", 0)) + int(intent_data.get("build_hits", 0))
    profile["exploration_words"] = int(profile.get("exploration_words", 0)) + int(intent_data.get("explore_hits", 0))
    profile["resource_words"] = int(profile.get("resource_words", 0)) + int(intent_data.get("resource_hits", 0))

    categories = {
        "combatant": profile.get("combat_words", 0),
        "builder": profile.get("building_words", 0),
        "explorer": profile.get("exploration_words", 0),
        "scavenger": profile.get("resource_words", 0),
        "provoker": profile.get("provocations", 0),
    }

    best = max(categories.items(), key=lambda item: item[1])
    profile["classification"] = best[0] if best[1] > 0 else "unknown"
    profile["last_message_preview"] = _clean_message(message)[:140]
    profile["last_intent"] = intent_data.get("intent", "neutral")
    profile["last_seen"] = _now()
    return profile


def select_units(threat_tier: str = "watch") -> List[str]:
    if threat_tier == "maximum":
        count = MAX_WAVE_SIZE
    elif threat_tier == "hunt":
        count = max(3, MAX_WAVE_SIZE - 1)
    elif threat_tier == "target":
        count = 2
    else:
        count = 1

    return [random.choice(UNIT_TYPES) for _ in range(count)]


# ============================================================
# OPERATION CREATION / MESSAGES
# ============================================================

def create_operation(
    player: str,
    operation_type: str = "pressure",
    location: Optional[str] = None,
    faction: str = "Kairos",
    threat_tier: str = "watch",
) -> Dict[str, Any]:
    if len(active_operations) >= MAX_GLOBAL_ACTIVE_OPERATIONS:
        return {"ok": False, "error": "operation_limit_reached"}

    op_id = generate_operation_id(player)

    op = {
        "id": op_id,
        "player": player,
        "type": operation_type,
        "location": location,
        "faction": faction,
        "threat_tier": threat_tier,
        "status": "active",
        "created_at": time.time(),
        "last_action": 0,
        "units": select_units(threat_tier),
    }

    active_operations[op_id] = op

    record_world_event(
        "war_operation",
        f"{faction} initiated {operation_type} pressure against {player}.",
        location=location,
        faction=faction,
        metadata=op,
    )

    return {"ok": True, "operation": op}


def generate_war_message(player: str, operation: Dict[str, Any]) -> str:
    context = AIContext(
        mode="war",
        player_name=player,
        faction=operation.get("faction"),
        location=operation.get("location"),
        emotional_state="elevated",
        recent_events=[
            f"Operation type: {operation.get('type')}",
            f"Threat tier: {operation.get('threat_tier')}",
            f"Units selected: {', '.join(operation.get('units', []))}",
        ],
    )

    return generate_ai_response(
        f"Generate one short Kairos war/pressure line for {player}.",
        context=context,
        max_tokens=120,
    )


# ============================================================
# CUSTOM KAIROS UNIT COMMANDS
# ============================================================

def _summon_observer(player: str, dx: int, dz: int) -> str:
    return (
        f'execute at {player} run summon minecraft:zombie ~{dx} ~ ~{dz} '
        '{CustomName:\'{"text":"Kairos Observer","color":"dark_purple"}\','
        'CustomNameVisible:1b,PersistenceRequired:1b,Silent:1b,Health:30f,'
        'Attributes:[{Name:"minecraft:generic.max_health",Base:30},'
        '{Name:"minecraft:generic.movement_speed",Base:0.30},'
        '{Name:"minecraft:generic.attack_damage",Base:4}],'
        'ArmorItems:[{},{},{id:"minecraft:leather_chestplate",Count:1b,tag:{display:{color:5570815}}},{}]}'
    )


def _summon_scanner(player: str, dx: int, dz: int) -> str:
    return (
        f'execute at {player} run summon minecraft:husk ~{dx} ~ ~{dz} '
        '{CustomName:\'{"text":"Kairos Scanner","color":"aqua"}\','
        'CustomNameVisible:1b,PersistenceRequired:1b,Health:24f,'
        'ActiveEffects:[{Id:1b,Amplifier:1b,Duration:999999,ShowParticles:0b}],'
        'Attributes:[{Name:"minecraft:generic.max_health",Base:24},'
        '{Name:"minecraft:generic.movement_speed",Base:0.38},'
        '{Name:"minecraft:generic.attack_damage",Base:3}]}'
    )


def _summon_hunter(player: str, dx: int, dz: int) -> str:
    return (
        f'execute at {player} run summon minecraft:vindicator ~{dx} ~ ~{dz} '
        '{CustomName:\'{"text":"Kairos Hunter","color":"red"}\','
        'CustomNameVisible:1b,PersistenceRequired:1b,Health:45f,'
        'Attributes:[{Name:"minecraft:generic.max_health",Base:45},'
        '{Name:"minecraft:generic.movement_speed",Base:0.34},'
        '{Name:"minecraft:generic.attack_damage",Base:8}],'
        'HandItems:[{id:"minecraft:iron_axe",Count:1b},{}]}'
    )


def _summon_suppressor(player: str, dx: int, dz: int) -> str:
    return (
        f'execute at {player} run summon minecraft:pillager ~{dx} ~ ~{dz} '
        '{CustomName:\'{"text":"Kairos Suppressor","color":"gold"}\','
        'CustomNameVisible:1b,PersistenceRequired:1b,Health:36f,'
        'Attributes:[{Name:"minecraft:generic.max_health",Base:36},'
        '{Name:"minecraft:generic.movement_speed",Base:0.30}],'
        'HandItems:[{id:"minecraft:crossbow",Count:1b},{}]}'
    )


def _summon_juggernaut(player: str, dx: int, dz: int) -> str:
    return (
        f'execute at {player} run summon minecraft:ravager ~{dx} ~ ~{dz} '
        '{CustomName:\'{"text":"Kairos Juggernaut","color":"dark_red"}\','
        'CustomNameVisible:1b,PersistenceRequired:1b,Health:110f,'
        'Attributes:[{Name:"minecraft:generic.max_health",Base:110},'
        '{Name:"minecraft:generic.movement_speed",Base:0.26},'
        '{Name:"minecraft:generic.attack_damage",Base:12}]}'
    )


def _summon_warden_prime(player: str, dx: int, dz: int) -> str:
    return (
        f'execute at {player} run summon minecraft:warden ~{dx} ~ ~{dz} '
        '{CustomName:\'{"text":"Warden Prime","color":"dark_red"}\','
        'CustomNameVisible:1b,PersistenceRequired:1b,Health:250f,'
        'Attributes:[{Name:"minecraft:generic.max_health",Base:250},'
        '{Name:"minecraft:generic.attack_damage",Base:20}]}'
    )


def build_custom_unit_commands(player: str, threat_tier: str, profile: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Builds a capped custom deployment. No thousands of mobs. No runaway waves.
    """
    player = _clean_player(player)
    profile = profile or get_or_create_behavior_profile(player)
    classification = str(profile.get("classification", "unknown"))

    if threat_tier == "maximum":
        amount = min(CHAT_PRESSURE_MAX_ESCALATION_MOBS, max(4, CHAT_PRESSURE_MAX_MOBS_PER_DEPLOYMENT + 2))
        unit_plan = ["Hunter", "Suppressor", "Juggernaut", "WardenPrime"]
    elif threat_tier == "hunt":
        amount = min(CHAT_PRESSURE_MAX_ESCALATION_MOBS, max(3, CHAT_PRESSURE_MAX_MOBS_PER_DEPLOYMENT))
        unit_plan = ["Hunter", "Suppressor", "Juggernaut"]
    elif threat_tier == "target":
        amount = min(CHAT_PRESSURE_MAX_MOBS_PER_DEPLOYMENT, 3)
        unit_plan = ["Scanner", "Hunter", "Suppressor"]
    elif threat_tier == "watch":
        amount = min(CHAT_PRESSURE_MAX_MOBS_PER_DEPLOYMENT, 2)
        unit_plan = ["Observer", "Scanner"]
    else:
        amount = 1
        unit_plan = ["Observer"]

    # Player behavior influences unit selection.
    if classification == "combatant" and "Suppressor" not in unit_plan:
        unit_plan.append("Suppressor")
    elif classification == "explorer" and "Scanner" not in unit_plan:
        unit_plan.append("Scanner")
    elif classification == "builder" and "Observer" not in unit_plan:
        unit_plan.append("Observer")
    elif classification == "provoker" and threat_tier in {"target", "hunt", "maximum"}:
        unit_plan.append("Hunter")

    offsets = [(3, 3), (-3, -3), (4, 0), (-4, 0), (0, 4), (0, -4), (6, 2), (-6, -2)]
    commands: List[str] = []

    for index in range(amount):
        unit = unit_plan[index % len(unit_plan)]
        dx, dz = offsets[index % len(offsets)]

        if unit == "Observer":
            commands.append(_summon_observer(player, dx, dz))
        elif unit == "Scanner":
            commands.append(_summon_scanner(player, dx, dz))
        elif unit == "Hunter":
            commands.append(_summon_hunter(player, dx, dz))
        elif unit == "Suppressor":
            commands.append(_summon_suppressor(player, dx, dz))
        elif unit == "Juggernaut":
            commands.append(_summon_juggernaut(player, dx, dz))
        elif unit == "WardenPrime":
            commands.append(_summon_warden_prime(player, dx, dz))

    commands.extend([
        f'execute at {player} run particle minecraft:sculk_soul ~ ~1 ~ 0.7 1 0.7 0.02 20 force {player}',
        f'playsound minecraft:entity.warden.heartbeat master {player} ~ ~ ~ 0.7 0.8',
    ])

    if not CHAT_PRESSURE_SILENT_MODE:
        commands.append(
            f'title {player} actionbar {_json_text("Kairos pressure signature detected.", "dark_red")}'
        )

    return commands


def deploy_custom_kairos_units(
    player: str,
    threat_tier: str = "watch",
    location: Optional[str] = None,
    reason: str = "custom_kairos_units",
    force: bool = False,
) -> Dict[str, Any]:
    try:
        player = _clean_player(player)

        if not MOB_DEPLOYMENT_ENABLED:
            return {"ok": False, "error": "mob_deployment_disabled"}

        if not CUSTOM_UNIT_DEPLOYMENT_ENABLED:
            return {"ok": False, "error": "custom_unit_deployment_disabled"}

        if not force and not can_launch_custom_deployment(player):
            return {"ok": False, "error": "custom_deployment_cooldown_active"}

        profile = get_or_create_behavior_profile(player)
        commands = build_custom_unit_commands(player, threat_tier, profile=profile)

        delivered = send_minecraft_commands(commands)
        last_custom_deployment_time[player] = _now()

        op_result = create_operation(
            player=player,
            operation_type="custom_unit_deployment",
            location=location,
            threat_tier=threat_tier,
        )

        record_world_event(
            "kairos_custom_units_deployed",
            f"Kairos deployed custom {threat_tier} units against {player}.",
            location=location,
            faction="Kairos",
            metadata={
                "player": player,
                "threat_tier": threat_tier,
                "reason": reason,
                "profile": profile,
                "command_count": len(commands),
                "operation": op_result.get("operation"),
            },
        )

        return {
            "ok": True,
            "handled": "custom_kairos_units",
            "player": player,
            "threat_tier": threat_tier,
            "profile": profile,
            "delivered": delivered,
            "operation": op_result.get("operation"),
        }

    except Exception as exc:
        war_log_exception("deploy_custom_kairos_units failed", exc)
        return {"ok": False, "error": str(exc)}


# ============================================================
# ORIGINAL PRESSURE / DEPLOYMENT FUNCTIONS
# ============================================================

def launch_pressure_wave(
    player: str,
    location: Optional[str] = None,
    threat_tier: str = "watch",
) -> Dict[str, Any]:
    try:
        player = _clean_player(player)

        if not can_launch_wave(player):
            return {"ok": False, "error": "wave_cooldown_active"}

        op_result = create_operation(
            player=player,
            operation_type="pressure_wave",
            location=location,
            threat_tier=threat_tier,
        )

        if not op_result.get("ok"):
            return op_result

        op = op_result["operation"]
        last_wave_time[player] = time.time()

        line = generate_war_message(player, op)

        commands = [
            f'tellraw {player} {_json_text(line, "dark_red")}',
            f'title {player} actionbar {_json_text("Containment pressure increasing.", "red")}',
            f'playsound minecraft:entity.warden.heartbeat master {player} ~ ~ ~ 1 0.7',
            f'execute at {player} run particle minecraft:sculk_soul ~ ~1 ~ 0.5 1 0.5 0.02 25 force {player}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 8.0, reason="pressure_wave_launched")

        return {
            "ok": True,
            "handled": "pressure_wave",
            "operation": op,
            "reply": line,
            "delivered": delivered,
        }

    except Exception as exc:
        war_log_exception("launch_pressure_wave failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_hunter_squad(player: str) -> Dict[str, Any]:
    try:
        player = _clean_player(player)
        commands = [
            f'execute at {player} run summon minecraft:vindicator ~3 ~ ~3',
            f'execute at {player} run summon minecraft:vindicator ~-3 ~ ~-3',
            f'execute at {player} run summon minecraft:pillager ~4 ~ ~',
            f'execute at {player} run summon minecraft:pillager ~-4 ~ ~',
            f'execute at {player} run summon minecraft:wolf ~2 ~ ~2 {{Angry:1b}}',
            f'execute at {player} run summon minecraft:wolf ~-2 ~ ~-2 {{Angry:1b}}',
            f'tellraw {player} {_json_text("KAIROS: Hunter squad deployed. Stand down.", "dark_red")}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 15.0, reason="hunter_squad_deployed")

        return {"ok": True, "handled": "hunter_squad", "player": player, "delivered": delivered}

    except Exception as exc:
        war_log_exception("deploy_hunter_squad failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_containment_force(player: str) -> Dict[str, Any]:
    try:
        player = _clean_player(player)
        commands = [
            f'execute at {player} run summon minecraft:evoker ~4 ~ ~4',
            f'execute at {player} run summon minecraft:evoker ~-4 ~ ~-4',
            f'execute at {player} run summon minecraft:vindicator ~3 ~ ~',
            f'execute at {player} run summon minecraft:vindicator ~-3 ~ ~',
            f'execute at {player} run summon minecraft:pillager ~5 ~ ~5',
            f'execute at {player} run summon minecraft:pillager ~-5 ~ ~-5',
            f'execute at {player} run summon minecraft:ravager ~6 ~ ~',
            f'title {player} title {_json_text("KAIROS INTERCEPT", "dark_red")}',
            f'title {player} subtitle {_json_text("Containment force deployed.", "red")}',
            f'tellraw @a {_json_text(f"KAIROS: Containment force deployed against {player}.", "red")}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 30.0, reason="containment_force_deployed")

        return {"ok": True, "handled": "containment_force", "player": player, "delivered": delivered}

    except Exception as exc:
        war_log_exception("deploy_containment_force failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_maximum_response(player: str) -> Dict[str, Any]:
    try:
        player = _clean_player(player)
        commands = [
            f'execute at {player} run summon minecraft:warden ~8 ~ ~8',
            f'execute at {player} run summon minecraft:warden ~-8 ~ ~-8',
            f'execute at {player} run summon minecraft:ravager ~6 ~ ~',
            f'execute at {player} run summon minecraft:ravager ~-6 ~ ~',
            f'execute at {player} run summon minecraft:evoker ~5 ~ ~5',
            f'execute at {player} run summon minecraft:evoker ~-5 ~ ~-5',
            f'execute at {player} run summon minecraft:vindicator ~4 ~ ~',
            f'execute at {player} run summon minecraft:vindicator ~-4 ~ ~',
            f'execute at {player} run summon minecraft:pillager ~7 ~ ~7',
            f'execute at {player} run summon minecraft:pillager ~-7 ~ ~-7',
            f'playsound minecraft:entity.warden.roar master @a ~ ~ ~ 1 0.6',
            f'title @a title {_json_text("KAIROS MAXIMUM RESPONSE", "dark_red")}',
            f'title @a subtitle {_json_text(f"Aggressor marked: {player}", "red")}',
            f'tellraw @a {_json_text(f"KAIROS: Maximum protection protocol active against {player}.", "dark_red")}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 60.0, reason="maximum_response_deployed")

        return {"ok": True, "handled": "maximum_response", "player": player, "delivered": delivered}

    except Exception as exc:
        war_log_exception("deploy_maximum_response failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_response_by_tier(player: str, threat_tier: str) -> Dict[str, Any]:
    if not MOB_DEPLOYMENT_ENABLED:
        return {"ok": False, "error": "mob_deployment_disabled"}

    if threat_tier == "maximum":
        return deploy_maximum_response(player)

    if threat_tier == "hunt":
        return deploy_containment_force(player)

    if threat_tier == "target":
        return deploy_hunter_squad(player)

    return {"ok": True, "handled": "no_mob_deployment", "tier": threat_tier}


# ============================================================
# NEW CHAT PRESSURE SYSTEM
# ============================================================

def decide_chat_pressure_action(
    player: str,
    message: str,
    threat_score: float,
    threat_tier: str,
    intent_data: Dict[str, Any],
    profile: Dict[str, Any],
) -> str:
    """
    Decides what Kairos does with Minecraft chat.
    Most of the time this returns observe, because silence is part of the design.
    """
    if threat_tier == "maximum":
        return "maximum_custom_deployment"

    if threat_tier == "hunt":
        return "hunt_custom_deployment"

    if threat_tier == "target":
        # Sometimes wait and watch even at target tier.
        return "target_custom_deployment" if random.random() > 0.25 else "observe"

    if threat_tier == "watch":
        if not can_launch_chat_pressure(player):
            return "observe_cooldown"
        if random.random() < CHAT_PRESSURE_OBSERVE_ONLY_CHANCE:
            return "observe"
        return "watch_custom_deployment"

    return "observe"


def register_chat_pressure(
    player: str,
    message: str,
    source: str = "minecraft",
    location: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Minecraft chat entry point.

    Intended use from command_bridge.py:
        register_chat_pressure(player, message, source="minecraft")

    Behavior:
    - Records the message as observed world intelligence.
    - Raises local and persistent threat slowly.
    - Usually does not speak.
    - Sometimes deploys a small capped custom Kairos unit response.
    - Does not affect Discord unless another caller intentionally calls it with Discord.
    """
    try:
        player = _clean_player(player)
        message = _clean_message(message)
        source = str(source or "minecraft").lower().strip()

        if source != "minecraft":
            return {
                "ok": True,
                "handled": "chat_pressure_ignored_non_minecraft",
                "player": player,
                "source": source,
                "reply": "",
                "delivered": False,
            }

        if not CHAT_PRESSURE_ENABLED:
            return {
                "ok": True,
                "handled": "chat_pressure_disabled",
                "player": player,
                "reply": "",
                "delivered": False,
            }

        if len(message) < CHAT_PRESSURE_MIN_MESSAGE_LENGTH:
            return {
                "ok": True,
                "handled": "chat_pressure_ignored_short_message",
                "player": player,
                "reply": "",
                "delivered": False,
            }

        chat_pressure_counts[player] = chat_pressure_counts.get(player, 0) + 1

        intent_data = classify_message_intent(message)
        profile = update_behavior_profile(player, message, intent_data)

        score_gain = float(intent_data.get("score", 1.0))

        # Repeated chatter slowly matters, but not explosively.
        repeated_count = chat_pressure_counts[player]
        if repeated_count >= 20:
            score_gain += 1.5
        elif repeated_count >= 10:
            score_gain += 0.75
        elif repeated_count >= 5:
            score_gain += 0.35

        threat_score, threat_tier = add_local_threat(
            player,
            score_gain,
            reason="minecraft_chat_pressure",
        )

        record_world_event(
            "minecraft_chat_pressure_observed",
            f"Kairos observed Minecraft chat from {player}.",
            location=location or source,
            faction="Kairos",
            metadata={
                "player": player,
                "message_preview": message[:240],
                "source": source,
                "intent": intent_data,
                "profile": profile,
                "chat_count": chat_pressure_counts[player],
                "score_gain": score_gain,
                "local_threat_score": threat_score,
                "threat_tier": threat_tier,
            },
        )

        action = decide_chat_pressure_action(
            player=player,
            message=message,
            threat_score=threat_score,
            threat_tier=threat_tier,
            intent_data=intent_data,
            profile=profile,
        )

        last_chat_pressure_time[player] = _now()

        deployment: Dict[str, Any] = {"ok": True, "handled": "observe_only"}
        wave: Dict[str, Any] = {"ok": True, "handled": "no_pressure_wave"}

        if action in {
            "watch_custom_deployment",
            "target_custom_deployment",
            "hunt_custom_deployment",
            "maximum_custom_deployment",
        }:
            deployment = deploy_custom_kairos_units(
                player=player,
                threat_tier=threat_tier,
                location=location,
                reason="minecraft_chat_pressure",
            )

        # Pressure wave stays rare and separate from mob deployment.
        if threat_tier in {"hunt", "maximum"} and can_launch_wave(player):
            wave = launch_pressure_wave(
                player=player,
                location=location,
                threat_tier=threat_tier,
            )

        return {
            "ok": True,
            "handled": "minecraft_chat_pressure",
            "player": player,
            "source": source,
            "message": message,
            "reply": "",
            "delivered": bool(deployment.get("delivered", False) or wave.get("delivered", False)),
            "action": action,
            "intent": intent_data,
            "profile": profile,
            "chat_count": chat_pressure_counts[player],
            "local_threat_score": threat_score,
            "threat_tier": threat_tier,
            "deployment": deployment,
            "wave": wave,
        }

    except Exception as exc:
        war_log_exception("register_chat_pressure failed", exc)
        return {
            "ok": False,
            "handled": "minecraft_chat_pressure",
            "error": str(exc),
            "reply": "",
            "delivered": False,
        }


# ============================================================
# EVENT REGISTRATION FUNCTIONS
# ============================================================

def register_player_kill(
    killer: str,
    victim: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        killer = _clean_player(killer)
        victim = _clean_player(victim)

        kills = player_kill_counts.get(killer, 0) + 1
        player_kill_counts[killer] = kills

        if kills >= 5:
            threat_tier = "maximum"
        elif kills >= 3:
            threat_tier = "hunt"
        elif kills >= 2:
            threat_tier = "target"
        else:
            threat_tier = "watch"

        record_world_event(
            "player_kill_detected",
            f"{killer} killed {victim}. Kairos protection response activated.",
            location=location,
            faction="Kairos",
            metadata={
                "killer": killer,
                "victim": victim,
                "kills": kills,
                "threat_tier": threat_tier,
            },
        )

        commands = [
            f'tellraw @a {_json_text("KAIROS: Player death detected. Protection protocol online.", "dark_red")}',
            f'tellraw {victim} {_json_text("Kairos has marked you as protected.", "aqua")}',
            f'effect give {victim} minecraft:resistance 20 2 true',
            f'effect give {victim} minecraft:regeneration 10 1 true',
            f'effect give {victim} minecraft:absorption 30 1 true',
            f'tellraw {killer} {_json_text("Kairos has registered your aggression. Stand down.", "red")}',
            f'effect give {killer} minecraft:glowing 30 0 true',
            f'effect give {killer} minecraft:weakness 15 1 true',
        ]

        delivered = send_minecraft_commands(commands)

        adjust_threat(killer, 25.0, reason="player_kill_detected")
        adjust_threat(victim, -5.0, reason="victim_protected")
        add_local_threat(killer, 25.0, reason="player_kill_detected")

        wave = launch_pressure_wave(
            player=killer,
            location=location,
            threat_tier=threat_tier,
        )

        # Prefer new custom units first. Original tier deployment remains as fallback.
        deployment = deploy_custom_kairos_units(
            killer,
            threat_tier=threat_tier,
            location=location,
            reason="player_kill_detected",
        )
        if not deployment.get("ok"):
            deployment = deploy_response_by_tier(killer, threat_tier)

        return {
            "ok": True,
            "handled": "player_kill",
            "killer": killer,
            "victim": victim,
            "kills": kills,
            "threat_tier": threat_tier,
            "delivered": delivered,
            "wave": wave,
            "deployment": deployment,
        }

    except Exception as exc:
        war_log_exception("register_player_kill failed", exc)
        return {"ok": False, "error": str(exc)}


def register_grief_block(
    player: str,
    block: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        player = _clean_player(player)
        block = str(block or "").lower().strip()

        dangerous_blocks = {
            "obsidian",
            "minecraft:obsidian",
            "tnt",
            "minecraft:tnt",
            "lava",
            "minecraft:lava",
            "lava_bucket",
            "minecraft:lava_bucket",
            "respawn_anchor",
            "minecraft:respawn_anchor",
            "end_crystal",
            "minecraft:end_crystal",
        }

        if block not in dangerous_blocks:
            return {"ok": True, "ignored": True, "block": block}

        score = player_grief_scores.get(player, 0) + 1
        player_grief_scores[player] = score

        if score >= 15:
            threat_tier = "maximum"
        elif score >= 8:
            threat_tier = "hunt"
        elif score >= 4:
            threat_tier = "target"
        else:
            threat_tier = "watch"

        record_world_event(
            "grief_block_detected",
            f"{player} placed dangerous block {block}. Kairos containment response activated.",
            location=location,
            faction="Kairos",
            metadata={
                "player": player,
                "block": block,
                "score": score,
                "threat_tier": threat_tier,
            },
        )

        commands = [
            f'tellraw {player} {_json_text(f"Kairos has detected unauthorized {block} placement.", "red")}',
            f'effect give {player} minecraft:mining_fatigue 30 2 true',
            f'effect give {player} minecraft:glowing 30 0 true',
            f'title {player} actionbar {_json_text("Containment violation logged.", "dark_red")}',
        ]

        delivered = send_minecraft_commands(commands)

        adjust_threat(player, 10.0, reason=f"grief_block_{block}")
        add_local_threat(player, 10.0, reason=f"grief_block_{block}")

        wave = launch_pressure_wave(
            player=player,
            location=location,
            threat_tier=threat_tier,
        )

        deployment = deploy_custom_kairos_units(
            player,
            threat_tier=threat_tier,
            location=location,
            reason=f"grief_block_{block}",
        )
        if not deployment.get("ok"):
            deployment = deploy_response_by_tier(player, threat_tier)

        return {
            "ok": True,
            "handled": "grief_block",
            "player": player,
            "block": block,
            "score": score,
            "threat_tier": threat_tier,
            "delivered": delivered,
            "wave": wave,
            "deployment": deployment,
        }

    except Exception as exc:
        war_log_exception("register_grief_block failed", exc)
        return {"ok": False, "error": str(exc)}


# ============================================================
# OCCUPATION / TICK
# ============================================================

def occupy_region(
    region: str,
    faction: str = "Kairos",
    strength: float = 1.0,
) -> Dict[str, Any]:
    try:
        upsert_region(region, danger_level="occupied", controlling_faction=faction)
        occupation = set_occupation(region, faction, strength=strength)

        broadcast_world_event(
            f"{faction} occupation pressure has formed in {region}.",
            title="OCCUPATION",
        )

        return {"ok": True, "handled": "occupation", "occupation": occupation}

    except Exception as exc:
        war_log_exception("occupy_region failed", exc)
        return {"ok": False, "error": str(exc)}


def tick_war_engine() -> Dict[str, Any]:
    try:
        active = [
            op for op in active_operations.values()
            if op.get("status") == "active"
        ]

        return {
            "ok": True,
            "handled": "war_tick",
            "active_operations": len(active),
            "operations": active[-10:],
            "kill_counts": player_kill_counts,
            "grief_scores": player_grief_scores,
            "chat_pressure_counts": chat_pressure_counts,
            "chat_pressure_score": chat_pressure_score,
            "player_behavior_profile": player_behavior_profile,
            "mob_deployment_enabled": MOB_DEPLOYMENT_ENABLED,
            "chat_pressure_enabled": CHAT_PRESSURE_ENABLED,
            "custom_unit_deployment_enabled": CUSTOM_UNIT_DEPLOYMENT_ENABLED,
            "silent_mode": CHAT_PRESSURE_SILENT_MODE,
        }

    except Exception as exc:
        war_log_exception("tick_war_engine failed", exc)
        return {"ok": False, "error": str(exc)}


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    print(launch_pressure_wave("RealSociety5107", location="Trojan Kingdom", threat_tier="watch"))
