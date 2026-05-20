
"""
ai_core.py
Kairos Intelligence Core

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
"""

from __future__ import annotations

import json
import os
import random
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
}


# ============================================================
# UTILITIES
# ============================================================

def clamp_text(text: Any, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    text = str(text or "").strip()

    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."

    return text


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


def fallback_response(context: Optional[AIContext] = None) -> str:
    context = context or AIContext()

    if context.mode == "npc" and context.npc_name:
        return f"{context.npc_name}: The situation is unstable. Stay alert."

    if context.mode == "war":
        return "Kairos: Defensive posture adjusted. Threat monitoring remains active."

    return "Kairos: " + random.choice(FALLBACK_RESPONSES)


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
# SPECIALIZED HELPERS
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
