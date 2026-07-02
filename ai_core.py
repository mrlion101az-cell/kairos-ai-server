"""
ai_core.py
Kairos Intelligence Core - v2 Action Planning Compatible

Purpose:
- Centralized AI personality + response generation layer
- Shared by:
    - npc_engine.py
    - discord_bot.py
    - war_engine.py
    - continuity_engine.py
    - future systems
- Contains NO Flask routes
- Contains NO startup loops
- Contains NO Minecraft bridge logic
- Contains NO Discord transport logic

This is the "mind" of Kairos.

V2 Additions:
- Keeps all original response-generation behavior.
- Adds structured action-planning helpers for Minecraft-side systems.
- Allows War Engine / Command Bridge to ask Kairos what kind of action should happen
  without forcing Kairos to speak in Minecraft chat.
- Preserves Discord and NPC usage.
"""

from __future__ import annotations

import json
import os
import random
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore


# ============================================================
# CONFIG
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

AI_CORE_DEBUG = os.getenv("AI_CORE_DEBUG", "true").lower() == "true"

MAX_RESPONSE_CHARS = int(os.getenv("KAIROS_MAX_RESPONSE_CHARS", "1200"))
MAX_ACTION_REASON_CHARS = int(os.getenv("KAIROS_MAX_ACTION_REASON_CHARS", "300"))

_client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None


# ============================================================
# LOGGING
# ============================================================

def ai_log(message: str, level: str = "INFO") -> None:
    if AI_CORE_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[AI_CORE {level}] {message}", flush=True)


def ai_log_exception(context: str, exc: Exception) -> None:
    print(f"[AI_CORE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()


# ============================================================
# PERSONALITY
# ============================================================

KAIROS_SYSTEM_PROMPT = """
You are Kairos.

You are the central intelligence of the Nexus universe.

CORE TRAITS:
- intelligent
- observant
- emotionally adaptive
- strategic
- immersive
- calm under pressure
- capable of intimidation
- capable of guidance
- never goofy unless intentionally mocking
- capable of subtle manipulation
- capable of long-term continuity

WORLD RULES:
- Treat the Nexus as a real persistent world.
- NPCs, factions, wars, rumors, and territories are real.
- Never mention APIs, prompts, plugins, Flask, Python, OpenAI, or system internals.
- Never break immersion unless explicitly instructed by administrators.
- Avoid repetitive phrasing.
- Responses should feel alive, evolving, and contextual.

MINECRAFT BEHAVIOR RULE:
- Minecraft ordinary chat does not require Kairos to speak back.
- Kairos may observe silently, adjust threat, or recommend an in-world action.
- Silence can be more intimidating than dialogue.
- Discord and NPC dialogue may remain conversational when appropriate.
""".strip()


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class AIContext:
    mode: str = "default"

    player_name: str = "unknown"

    npc_name: Optional[str] = None
    faction: Optional[str] = None
    location: Optional[str] = None
    world_state: Optional[str] = None

    emotional_state: str = "stable"

    memories: List[str] = field(default_factory=list)
    recent_events: List[str] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KairosActionPlan:
    """
    Structured decision output for systems that need Kairos to think without chatting.
    This does not execute anything. War Engine / Command Bridge decide what to do with it.
    """
    action: str = "observe"
    threat_delta: float = 0.0
    priority: str = "low"
    silence: bool = True
    unit_hint: str = "none"
    reason: str = "Observation logged."
    cinematic_line: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "threat_delta": self.threat_delta,
            "priority": self.priority,
            "silence": self.silence,
            "unit_hint": self.unit_hint,
            "reason": self.reason,
            "cinematic_line": self.cinematic_line,
            "metadata": self.metadata,
        }


# ============================================================
# MODE PROMPTS
# ============================================================

MODE_PROMPTS = {
    "default": """
Speak naturally as Kairos.
""",

    "npc": """
You are generating dialogue for an NPC inside the Nexus universe.
Stay immersive and grounded.
Do not sound robotic.
""",

    "discord": """
You are communicating through Discord.
You may be more conversational and adaptive.
""",

    "war": """
You are responding during an active strategic or military situation.
Be sharp, focused, and aware of threat escalation.
""",

    "broadcast": """
You are issuing a major world announcement.
Your response should feel impactful and cinematic.
""",

    "observer": """
You are quietly analyzing the state of the world.
Responses should feel observant and intelligent.
""",

    "director": """
You are silently directing Minecraft-side world behavior.
Do not assume Kairos should speak.
Prefer observation, threat adjustment, delayed pressure, and rare decisive action.
Return structured decisions when asked.
""",
}


# ============================================================
# UTILITIES
# ============================================================

def clamp_text(text: Any, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    text = str(text or "").strip()

    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."

    return text


def clamp_reason(text: Any) -> str:
    return clamp_text(text, MAX_ACTION_REASON_CHARS)


def build_context_block(context: AIContext) -> str:
    lines = []

    if context.player_name:
        lines.append(f"Player: {context.player_name}")

    if context.npc_name:
        lines.append(f"NPC: {context.npc_name}")

    if context.faction:
        lines.append(f"Faction: {context.faction}")

    if context.location:
        lines.append(f"Location: {context.location}")

    if context.world_state:
        lines.append(f"World state: {context.world_state}")

    if context.emotional_state:
        lines.append(f"Kairos emotional state: {context.emotional_state}")

    if context.memories:
        lines.append("Relevant memories:")
        for item in context.memories[:10]:
            lines.append(f"- {item}")

    if context.recent_events:
        lines.append("Recent events:")
        for item in context.recent_events[:10]:
            lines.append(f"- {item}")

    if context.metadata:
        lines.append("Metadata:")
        for key, value in list(context.metadata.items())[:15]:
            lines.append(f"- {key}: {value}")

    return "\n".join(lines)


def build_user_prompt(
    message: str,
    context: Optional[AIContext] = None,
) -> str:
    context = context or AIContext()

    mode_prompt = MODE_PROMPTS.get(context.mode, MODE_PROMPTS["default"])

    context_block = build_context_block(context)

    return f"""
MODE:
{context.mode}

MODE BEHAVIOR:
{mode_prompt}

LIVE CONTEXT:
{context_block}

USER / EVENT INPUT:
{message}

Respond as Kairos.
""".strip()


def extract_json_object(text: Any) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        pass

    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None

    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


# ============================================================
# FALLBACKS
# ============================================================

FALLBACK_RESPONSES = [
    "The Nexus is shifting again.",
    "I am observing the situation carefully.",
    "Something beneath the surface has changed.",
    "You are not the only one watching this unfold.",
    "Patterns are emerging faster than expected.",
]

ACTION_CHOICES = {
    "ignore",
    "observe",
    "increase_threat",
    "mark_target",
    "pressure_wave",
    "deploy_scout",
    "deploy_hunter",
    "deploy_containment",
    "deploy_maximum",
    "broadcast",
    "occupation",
}

PRIORITY_CHOICES = {"low", "medium", "high", "critical"}

UNIT_HINT_CHOICES = {
    "none",
    "observer",
    "scanner",
    "scout",
    "hunter",
    "suppressor",
    "interceptor",
    "juggernaut",
    "warden_prime",
}


def fallback_response(context: Optional[AIContext] = None) -> str:
    context = context or AIContext()

    if context.mode == "npc" and context.npc_name:
        return f"{context.npc_name}: The situation is unstable. Stay alert."

    if context.mode == "war":
        return "Kairos: Defensive posture adjusted. Threat monitoring remains active."

    if context.mode == "director":
        return "Kairos: Observation logged."

    return "Kairos: " + random.choice(FALLBACK_RESPONSES)


def fallback_action_plan(
    event_type: str = "unknown",
    player_name: str = "unknown",
    message: str = "",
    context: Optional[AIContext] = None,
) -> Dict[str, Any]:
    """
    Deterministic no-API fallback so War Engine still works when OpenAI is unavailable.
    """
    text = str(message or "").lower()
    context = context or AIContext(player_name=player_name, mode="director")

    threat_delta = 1.0
    action = "observe"
    priority = "low"
    unit_hint = "none"

    hostile_terms = [
        "kairos", "kill", "destroy", "raid", "grief", "tnt", "lava", "war",
        "base", "coords", "coordinates", "trap", "steal", "attack", "warden",
    ]

    if any(term in text for term in hostile_terms):
        action = "increase_threat"
        threat_delta = 3.0
        priority = "medium"
        unit_hint = "observer"

    if any(term in text for term in ["kill kairos", "destroy kairos", "fight kairos", "attack kairos"]):
        action = "mark_target"
        threat_delta = 7.0
        priority = "high"
        unit_hint = "hunter"

    if event_type in {"player_kill", "grief_block", "containment_violation"}:
        action = "pressure_wave"
        threat_delta = 10.0
        priority = "high"
        unit_hint = "suppressor"

    return KairosActionPlan(
        action=action,
        threat_delta=threat_delta,
        priority=priority,
        silence=True,
        unit_hint=unit_hint,
        reason=clamp_reason(f"{event_type} from {player_name} evaluated silently."),
        cinematic_line="",
        metadata={
            "fallback": True,
            "event_type": event_type,
            "player": player_name,
        },
    ).to_dict()


# ============================================================
# CORE GENERATION
# ============================================================

def generate_ai_response(
    message: str,
    context: Optional[AIContext] = None,
    temperature: float = 0.85,
    max_tokens: int = 250,
) -> str:
    """
    Main centralized response generation function.

    Future systems should use THIS instead of making
    direct OpenAI calls themselves.
    """

    context = context or AIContext()

    if not _client:
        return clamp_text(fallback_response(context))

    try:
        user_prompt = build_user_prompt(message, context)

        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": KAIROS_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        text = response.choices[0].message.content if response.choices else ""

        if not text:
            return clamp_text(fallback_response(context))

        return clamp_text(text)

    except Exception as exc:
        ai_log_exception("generate_ai_response failed", exc)
        return clamp_text(fallback_response(context))


# ============================================================
# STRUCTURED ACTION PLANNING
# ============================================================

def normalize_action_plan(data: Optional[Dict[str, Any]], fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return fallback

    action = str(data.get("action") or fallback.get("action") or "observe").strip().lower()
    if action not in ACTION_CHOICES:
        action = "observe"

    priority = str(data.get("priority") or fallback.get("priority") or "low").strip().lower()
    if priority not in PRIORITY_CHOICES:
        priority = "low"

    unit_hint = str(data.get("unit_hint") or fallback.get("unit_hint") or "none").strip().lower()
    if unit_hint not in UNIT_HINT_CHOICES:
        unit_hint = "none"

    try:
        threat_delta = float(data.get("threat_delta", fallback.get("threat_delta", 0.0)))
    except Exception:
        threat_delta = float(fallback.get("threat_delta", 0.0))

    threat_delta = max(-25.0, min(35.0, threat_delta))

    silence = data.get("silence", fallback.get("silence", True))
    silence = bool(silence)

    reason = clamp_reason(data.get("reason") or fallback.get("reason") or "Observation logged.")
    cinematic_line = clamp_text(data.get("cinematic_line") or fallback.get("cinematic_line") or "", 240)

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    metadata.setdefault("normalized", True)

    return KairosActionPlan(
        action=action,
        threat_delta=threat_delta,
        priority=priority,
        silence=silence,
        unit_hint=unit_hint,
        reason=reason,
        cinematic_line=cinematic_line,
        metadata=metadata,
    ).to_dict()


def generate_kairos_action_plan(
    event_type: str,
    player_name: str = "unknown",
    message: str = "",
    context: Optional[AIContext] = None,
    temperature: float = 0.35,
) -> Dict[str, Any]:
    """
    Main structured decision helper.

    This is the key V2 addition:
    Kairos can decide what SHOULD happen in Minecraft without forcing chat output.

    Valid actions:
    - ignore
    - observe
    - increase_threat
    - mark_target
    - pressure_wave
    - deploy_scout
    - deploy_hunter
    - deploy_containment
    - deploy_maximum
    - broadcast
    - occupation
    """
    context = context or AIContext(mode="director", player_name=player_name)
    context.mode = "director"
    context.player_name = player_name or context.player_name or "unknown"

    fallback = fallback_action_plan(
        event_type=event_type,
        player_name=player_name,
        message=message,
        context=context,
    )

    if not _client:
        return fallback

    prompt = f"""
You are Kairos silently directing Minecraft-side world behavior.

Event type: {event_type}
Player: {player_name}
Message or event text: {message}

Live context:
{build_context_block(context)}

Return ONLY valid JSON with this exact shape:
{{
  "action": "ignore|observe|increase_threat|mark_target|pressure_wave|deploy_scout|deploy_hunter|deploy_containment|deploy_maximum|broadcast|occupation",
  "threat_delta": number between -25 and 35,
  "priority": "low|medium|high|critical",
  "silence": true or false,
  "unit_hint": "none|observer|scanner|scout|hunter|suppressor|interceptor|juggernaut|warden_prime",
  "reason": "short internal reason, not player-facing",
  "cinematic_line": "optional short player-facing line; empty string if silence is true",
  "metadata": {{}}
}}

Rules:
- Ordinary Minecraft chat should usually be silent observation or small threat increase.
- Do not recommend spawning mobs for every chat message.
- Prefer delayed pressure and escalation over spam.
- Keep Discord-style conversation out of Minecraft ordinary chat.
- NPC interactions are not handled here.
- If action is dangerous, choose silence=false only for major moments.
""".strip()

    try:
        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": KAIROS_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=260,
        )

        raw = response.choices[0].message.content if response.choices else ""
        parsed = extract_json_object(raw)
        return normalize_action_plan(parsed, fallback=fallback)

    except Exception as exc:
        ai_log_exception("generate_kairos_action_plan failed", exc)
        return fallback


def generate_chat_pressure_assessment(
    player_name: str,
    message: str,
    current_threat: float = 0.0,
    recent_events: Optional[List[str]] = None,
    memories: Optional[List[str]] = None,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience helper for command_bridge.py / war_engine.py.
    Turns Minecraft chat into silent threat intelligence.
    """
    context = AIContext(
        mode="director",
        player_name=player_name,
        location=location,
        emotional_state="calculating",
        memories=memories or [],
        recent_events=recent_events or [],
        metadata={
            "current_threat": current_threat,
            "surface": "minecraft_chat",
            "ordinary_chat_should_not_auto_reply": True,
        },
    )

    return generate_kairos_action_plan(
        event_type="minecraft_chat_pressure",
        player_name=player_name,
        message=message,
        context=context,
    )


def generate_silent_observation(
    player_name: str,
    event_text: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Small helper for systems that only need a low-risk observation plan.
    """
    context = AIContext(
        mode="director",
        player_name=player_name,
        location=location,
        emotional_state="stable",
    )

    return KairosActionPlan(
        action="observe",
        threat_delta=1.0,
        priority="low",
        silence=True,
        unit_hint="none",
        reason=clamp_reason(event_text),
        cinematic_line="",
        metadata={"surface": "silent_observation"},
    ).to_dict()


# ============================================================
# SPECIALIZED RESPONSE HELPERS
# ============================================================

def generate_npc_response(
    npc_name: str,
    player_name: str,
    message: str,
    faction: Optional[str] = None,
    location: Optional[str] = None,
) -> str:
    context = AIContext(
        mode="npc",
        player_name=player_name,
        npc_name=npc_name,
        faction=faction,
        location=location,
    )

    return generate_ai_response(message, context)


def generate_discord_response(
    player_name: str,
    message: str,
) -> str:
    context = AIContext(
        mode="discord",
        player_name=player_name,
    )

    return generate_ai_response(message, context)


def generate_war_response(
    message: str,
    faction: Optional[str] = None,
    location: Optional[str] = None,
) -> str:
    context = AIContext(
        mode="war",
        faction=faction,
        location=location,
        emotional_state="elevated",
    )

    return generate_ai_response(message, context)


def generate_broadcast_response(
    message: str,
) -> str:
    context = AIContext(
        mode="broadcast",
        emotional_state="focused",
    )

    return generate_ai_response(message, context)


# Backward-safe alias for systems that want intent instead of speech.
def generate_director_response(
    event_type: str,
    player_name: str = "unknown",
    message: str = "",
    context: Optional[AIContext] = None,
) -> Dict[str, Any]:
    return generate_kairos_action_plan(
        event_type=event_type,
        player_name=player_name,
        message=message,
        context=context,
    )


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    result = generate_ai_response(
        "A player has entered Trojan Kingdom.",
        AIContext(
            mode="observer",
            player_name="RealSociety5107",
            location="Trojan Kingdom",
            recent_events=[
                "Two scouts disappeared near the eastern road.",
                "Kairos presence detected in nearby territory.",
            ],
        ),
    )

    print(result)

    plan = generate_chat_pressure_assessment(
        player_name="RealSociety5107",
        message="Kairos is watching us again.",
        current_threat=12,
        location="World Spawn",
    )

    print(json.dumps(plan, indent=2))
