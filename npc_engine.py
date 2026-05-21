"""
npc_engine.py
Kairos / Nexus NPC Dialogue Engine
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# ============================================================
# CONFIG
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

NPC_ENGINE_DEBUG = os.getenv("NPC_ENGINE_DEBUG", "true").lower() == "true"
NPC_PROFILE_DIR = Path(os.getenv("NPC_PROFILE_DIR", "npc_profiles"))

NPC_REPLY_MAX_SENTENCES = int(os.getenv("NPC_REPLY_MAX_SENTENCES", "3"))
NPC_REPLY_MAX_CHARS = int(os.getenv("NPC_REPLY_MAX_CHARS", "420"))

# UPDATED REGEX
NPC_TRIGGER_PATTERN = re.compile(
    r"\[NPC_TRIGGER\]\s+([A-Za-z0-9_\-]+)(?:\s+([A-Za-z0-9_\-<>%]+))?",
    re.IGNORECASE,
)

_client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None


# ============================================================
# LOGGING
# ============================================================

def npc_log(message: str, level: str = "INFO") -> None:
    if NPC_ENGINE_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[NPC_ENGINE {level}] {message}", flush=True)


def npc_log_exception(context: str, exc: Exception) -> None:
    print(f"[NPC_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class NPCProfile:
    display_name: str
    role: str = "Nexus NPC"
    faction: str = "Unknown"
    personality: str = "observant"
    alignment: str = "neutral"
    speech_style: str = "immersive, grounded, in-world"
    location: str = "The Nexus"
    knowledge: List[str] = field(default_factory=list)
    secrets: List[str] = field(default_factory=list)
    greeting_style: str = "short"
    danger_level: str = "unknown"


@dataclass
class NPCTrigger:
    npc_name: str
    player_name: str
    raw_message: str = ""
    source: str = "minecraft"


# ============================================================
# BUILT-IN NPC PROFILES
# ============================================================

NPC_PROFILES: Dict[str, Dict[str, Any]] = {
    "CaptainVaros": {
        "display_name": "Captain Varos",
        "role": "Trojan Guard Captain",
        "faction": "Trojan Kingdom",
        "personality": "disciplined, suspicious, loyal",
        "alignment": "Trojan Kingdom first",
        "speech_style": "short, guarded, military",
        "location": "Trojan Kingdom",
        "danger_level": "medium",
        "knowledge": [
            "The Trojan Kingdom is unstable but still standing.",
            "Scouts have gone missing near the outer roads.",
            "Kairos involvement has made people nervous.",
        ],
        "secrets": [
            "Captain Varos does not fully trust Kairos.",
        ],
    },
}


# ============================================================
# HELPERS
# ============================================================

def normalize_npc_key(name: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", str(name or "").strip())


def get_npc_profile(npc_name: Any) -> NPCProfile:
    clean = normalize_npc_key(npc_name)

    if clean in NPC_PROFILES:
        data = NPC_PROFILES[clean]

        return NPCProfile(
            display_name=data.get("display_name", clean),
            role=data.get("role", "Nexus NPC"),
            faction=data.get("faction", "Unknown"),
            personality=data.get("personality", "observant"),
            alignment=data.get("alignment", "neutral"),
            speech_style=data.get("speech_style", "immersive"),
            location=data.get("location", "The Nexus"),
            knowledge=data.get("knowledge", []),
            secrets=data.get("secrets", []),
            greeting_style=data.get("greeting_style", "short"),
            danger_level=data.get("danger_level", "unknown"),
        )

    return NPCProfile(display_name=clean)


# ============================================================
# TRIGGER PARSING
# ============================================================

def parse_npc_trigger(message: Any) -> Optional[NPCTrigger]:
    raw = str(message or "").strip()

    if not raw:
        return None

    npc_log(f"Parsed NPC trigger raw={raw}")

    match = NPC_TRIGGER_PATTERN.search(raw)

    if not match:
        return None

    npc_name = normalize_npc_key(match.group(1))
    player_name = str(match.group(2) or "").strip()

    npc_log(f"NPC={npc_name} PLAYER={player_name}")

    return NPCTrigger(
        npc_name=npc_name,
        player_name=player_name,
        raw_message=raw,
    )


def is_npc_trigger(message: Any) -> bool:
    return parse_npc_trigger(message) is not None


# ============================================================
# FALLBACK DIALOGUE
# ============================================================

def fallback_npc_reply(profile: NPCProfile, player_name: str = "traveler") -> str:
    options = [
        f"{profile.display_name}: Keep your eyes open, {player_name}.",
        f"{profile.display_name}: The roads are becoming dangerous again.",
        f"{profile.display_name}: Something feels wrong across the Nexus lately.",
        f"{profile.display_name}: You should not linger here too long.",
    ]

    return random.choice(options)


# ============================================================
# CLEANUP
# ============================================================

def clean_npc_reply(text: Any, profile: NPCProfile) -> str:
    reply = str(text or "").strip()

    if not reply.startswith(profile.display_name):
        reply = f"{profile.display_name}: {reply}"

    if len(reply) > NPC_REPLY_MAX_CHARS:
        reply = reply[: NPC_REPLY_MAX_CHARS - 3] + "..."

    return reply


# ============================================================
# AI GENERATION
# ============================================================

def generate_npc_reply(
    npc_name: str,
    player_name: str,
    raw_message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:

    profile = get_npc_profile(npc_name)

    if not _client:
        return clean_npc_reply(
            fallback_npc_reply(profile, player_name),
            profile
        )

    prompt = f"""
You are roleplaying as {profile.display_name}.

Faction: {profile.faction}
Role: {profile.role}
Personality: {profile.personality}

Player interacting:
{player_name}

Stay immersive.
Stay in-character.
Maximum 3 sentences.
"""

    try:
        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "You generate immersive Minecraft NPC dialogue."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.9,
            max_tokens=120,
        )

        text = response.choices[0].message.content
        return clean_npc_reply(text, profile)

    except Exception as exc:
        npc_log_exception("AI generation failed", exc)

        return clean_npc_reply(
            fallback_npc_reply(profile, player_name),
            profile
        )


# ============================================================
# MAIN HANDLER
# ============================================================

def handle_npc_trigger_message(
    message: Any,
    fallback_player: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    send_reply: Optional[Callable[[str, Optional[str]], Any]] = None,
) -> Optional[Dict[str, Any]]:

    trigger = parse_npc_trigger(message)

    if not trigger:
        return None

    player_name = trigger.player_name

    # MASSIVE FIX
    if (
        not player_name
        or player_name in {
            "<p>",
            "<player>",
            "%player%",
            "{player}",
            "player",
            "unknown",
        }
    ):
        player_name = fallback_player or "traveler"

    npc_log(
        f"Trigger detected npc={trigger.npc_name} player={player_name}"
    )

    reply = generate_npc_reply(
        trigger.npc_name,
        player_name,
        raw_message=trigger.raw_message,
        context=context or {},
    )

    delivered = False
    delivery_error = None

    if send_reply:
        try:
            send_reply(reply, player_name)
            delivered = True
        except Exception as exc:
            delivery_error = str(exc)
            npc_log_exception("send_reply failed", exc)

    return {
        "ok": True,
        "handled": "npc_trigger",
        "npc_name": trigger.npc_name,
        "player": player_name,
        "reply": reply,
        "delivered": delivered,
        "delivery_error": delivery_error,
        "timestamp": time.time(),
    }


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    test = "[NPC_TRIGGER] CaptainVaros <p>"

    result = handle_npc_trigger_message(
        test,
        fallback_player="RealSociety5107"
    )

    print(json.dumps(result, indent=2))
