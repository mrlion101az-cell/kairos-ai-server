
"""
npc_engine.py
Kairos / Nexus NPC Dialogue Engine

Purpose:
- Keep NPC dialogue logic OUT of app.py.
- Parse CitizensCMD trigger text such as:
    [NPC_TRIGGER] CaptainVaros RealSociety5107
- Generate immersive NPC dialogue using OpenAI when available.
- Fall back safely if OpenAI is unavailable or fails.
- Expose clean functions app.py can call without importing the war engine.

This file is intentionally self-contained and safe:
- No Flask app.run()
- No background loops
- No war engine startup
- No Discord logic
- No Minecraft HTTP dependency
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore


# ============================================================
# CONFIG
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

NPC_ENGINE_DEBUG = os.getenv("NPC_ENGINE_DEBUG", "true").lower() == "true"
NPC_PROFILE_DIR = Path(os.getenv("NPC_PROFILE_DIR", "npc_profiles"))

NPC_REPLY_MAX_SENTENCES = int(os.getenv("NPC_REPLY_MAX_SENTENCES", "3"))
NPC_REPLY_MAX_CHARS = int(os.getenv("NPC_REPLY_MAX_CHARS", "420"))

NPC_TRIGGER_PATTERN = re.compile(
    r"\[NPC_TRIGGER\]\s+([A-Za-z0-9_\-]+)\s+([A-Za-z0-9_\-<>%]+)",
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any], fallback_name: str = "Unknown NPC") -> "NPCProfile":
        return cls(
            display_name=str(data.get("display_name") or fallback_name),
            role=str(data.get("role") or "Nexus NPC"),
            faction=str(data.get("faction") or "Unknown"),
            personality=str(data.get("personality") or "observant"),
            alignment=str(data.get("alignment") or "neutral"),
            speech_style=str(data.get("speech_style") or "immersive, grounded, in-world"),
            location=str(data.get("location") or "The Nexus"),
            knowledge=list(data.get("knowledge") or []),
            secrets=list(data.get("secrets") or []),
            greeting_style=str(data.get("greeting_style") or "short"),
            danger_level=str(data.get("danger_level") or "unknown"),
        )


@dataclass
class NPCTrigger:
    npc_name: str
    player_name: str
    raw_message: str = ""
    source: str = "minecraft"


# ============================================================
# BUILT-IN NPC PROFILES
# Add future NPCs here OR put JSON files into npc_profiles/
# ============================================================

NPC_PROFILES: Dict[str, Dict[str, Any]] = {
    "CaptainVaros": {
        "display_name": "Captain Varos",
        "role": "Trojan Guard Captain",
        "faction": "Trojan Kingdom",
        "personality": "disciplined, suspicious, loyal, observant, protective",
        "alignment": "Trojan Kingdom first, cautiously cooperative with Kairos",
        "speech_style": "short, grounded, in-world, guarded, military, never goofy",
        "location": "Trojan Kingdom",
        "danger_level": "medium",
        "knowledge": [
            "The Trojan Kingdom is unstable but still standing.",
            "Trojan Kingdom scouts have gone missing near the outer roads.",
            "Kairos involvement has made people nervous.",
            "Some citizens support Kairos, while others fear him.",
            "Travelers disappearing near the roads is becoming a rumor.",
        ],
        "secrets": [
            "Captain Varos does not fully trust Kairos.",
            "Varos suspects the kingdom has informants.",
        ],
    },
}


# ============================================================
# PROFILE LOADING
# ============================================================

def normalize_npc_key(name: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", str(name or "").strip())


def load_profiles_from_json_dir(profile_dir: Path = NPC_PROFILE_DIR) -> Dict[str, Dict[str, Any]]:
    """
    Loads every *.json file inside npc_profiles/.

    Supported formats:
    1. Single object:
       {
         "CaptainVaros": {...},
         "ObserverNyra": {...}
       }

    2. List:
       [
         {"key": "CaptainVaros", "display_name": "Captain Varos", ...}
       ]
    """
    loaded: Dict[str, Dict[str, Any]] = {}

    try:
        if not profile_dir.exists():
            return loaded

        for path in profile_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))

                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, dict):
                            loaded[normalize_npc_key(key)] = value

                elif isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        key = normalize_npc_key(item.get("key") or item.get("npc_name") or item.get("display_name"))
                        if key:
                            loaded[key] = item

            except Exception as exc:
                npc_log_exception(f"Failed loading NPC profile file {path}", exc)

    except Exception as exc:
        npc_log_exception("load_profiles_from_json_dir failed", exc)

    return loaded


def get_all_profiles() -> Dict[str, Dict[str, Any]]:
    profiles = dict(NPC_PROFILES)
    profiles.update(load_profiles_from_json_dir())
    return profiles


def get_npc_profile(npc_name: Any) -> NPCProfile:
    clean = normalize_npc_key(npc_name)
    profiles = get_all_profiles()

    if clean in profiles:
        return NPCProfile.from_dict(profiles[clean], fallback_name=clean)

    # Case-insensitive fallback
    for key, value in profiles.items():
        if key.lower() == clean.lower():
            return NPCProfile.from_dict(value, fallback_name=clean)

    return NPCProfile(
        display_name=str(npc_name or "Unknown NPC"),
        role="Nexus NPC",
        faction="Unknown",
        personality="observant, cautious",
        speech_style="short, immersive, in-world",
        knowledge=[
            "The Nexus is unstable.",
            "Kairos is watching.",
        ],
    )


# ============================================================
# TRIGGER PARSING
# ============================================================

def parse_npc_trigger(message: Any) -> Optional[NPCTrigger]:
    """
    Parses:
      [NPC_TRIGGER] CaptainVaros RealSociety5107

    Also tolerates server prefixes such as:
      [Server] [NPC_TRIGGER] CaptainVaros RealSociety5107
    """
    raw = str(message or "").strip()
    if not raw:
        return None

    match = NPC_TRIGGER_PATTERN.search(raw)
    if not match:
        return None

    npc_name = normalize_npc_key(match.group(1))
    player_name = str(match.group(2)).strip()

    # If the placeholder failed to resolve, we still return it,
    # but app.py can provide fallback player from request data.
    return NPCTrigger(
        npc_name=npc_name,
        player_name=player_name,
        raw_message=raw,
    )


def is_npc_trigger(message: Any) -> bool:
    return parse_npc_trigger(message) is not None


# ============================================================
# PROMPT BUILDING
# ============================================================

def build_npc_prompt(
    profile: NPCProfile,
    player_name: str,
    raw_message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    context = context or {}

    known_local_context = []
    if context.get("location"):
        known_local_context.append(f"Current location: {context.get('location')}")
    if context.get("world_time"):
        known_local_context.append(f"World time: {context.get('world_time')}")
    if context.get("quest"):
        known_local_context.append(f"Active quest context: {context.get('quest')}")

    knowledge_lines = "\n".join(f"- {line}" for line in profile.knowledge[:10]) or "- No confirmed local knowledge."
    context_lines = "\n".join(f"- {line}" for line in known_local_context) or "- No extra live context provided."

    return f"""
You are speaking AS the Minecraft NPC "{profile.display_name}", not as Kairos directly.

NPC PROFILE:
- Name: {profile.display_name}
- Role: {profile.role}
- Faction: {profile.faction}
- Alignment: {profile.alignment}
- Location: {profile.location}
- Personality: {profile.personality}
- Speech style: {profile.speech_style}
- Danger level: {profile.danger_level}

KNOWN LOCAL INFORMATION:
{knowledge_lines}

LIVE CONTEXT:
{context_lines}

PLAYER:
- The player interacting with you is {player_name}.

RAW TRIGGER:
{raw_message}

RESPONSE RULES:
- Stay fully in character as {profile.display_name}.
- Do not say you are an AI.
- Do not mention prompts, APIs, scripts, routes, Flask, Discord, or plugins.
- Do not explain the system.
- Give one immersive in-world reply.
- Maximum {NPC_REPLY_MAX_SENTENCES} sentences.
- Make it useful, atmospheric, and grounded.
- If appropriate, hint at danger, rumors, quests, faction tension, or local instability.
""".strip()


# ============================================================
# FALLBACK DIALOGUE
# ============================================================

def fallback_npc_reply(profile: NPCProfile, player_name: str = "traveler") -> str:
    lines = [
        f"{profile.display_name}: Keep your eyes open. {profile.location} is quiet, and quiet is rarely safe.",
        f"{profile.display_name}: You picked a dangerous time to walk through {profile.location}.",
        f"{profile.display_name}: I know your face now, {player_name}. That may matter sooner than you think.",
        f"{profile.display_name}: The roads have changed. People vanish, and the survivors invent safer stories.",
        f"{profile.display_name}: If you came looking for certainty, you came to the wrong gate.",
    ]

    if profile.faction.lower() != "unknown":
        lines.append(f"{profile.display_name}: {profile.faction} still stands. For now.")

    return random.choice(lines)


def clean_npc_reply(text: Any, profile: NPCProfile) -> str:
    reply = str(text or "").strip()

    # Remove accidental AI/system phrasing.
    bad_prefixes = [
        "As an AI",
        "As a language model",
        "I am an AI",
        "System:",
        "Assistant:",
    ]
    for prefix in bad_prefixes:
        if reply.lower().startswith(prefix.lower()):
            reply = fallback_npc_reply(profile)

    # Remove wrapping quotes.
    reply = reply.strip().strip('"').strip("'").strip()

    # Force display name prefix if missing.
    if not reply.startswith("[") and not reply.startswith(profile.display_name + ":"):
        reply = f"{profile.display_name}: {reply}"

    # Hard length cap for Minecraft chat safety.
    if len(reply) > NPC_REPLY_MAX_CHARS:
        reply = reply[: NPC_REPLY_MAX_CHARS - 3].rstrip() + "..."

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
    prompt = build_npc_prompt(profile, player_name, raw_message, context)

    if not _client:
        return clean_npc_reply(fallback_npc_reply(profile, player_name), profile)

    try:
        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate short immersive Minecraft NPC dialogue. "
                        "You stay in character and never reveal system mechanics."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=160,
        )

        text = response.choices[0].message.content if response.choices else ""
        return clean_npc_reply(text, profile)

    except Exception as exc:
        npc_log_exception("OpenAI NPC reply failed", exc)
        return clean_npc_reply(fallback_npc_reply(profile, player_name), profile)


# Backward-compatible alias matching the old monolith naming.
def generate_npc_kairos_reply(npc_name: str, player_name: str, raw_message: str = "") -> str:
    return generate_npc_reply(npc_name, player_name, raw_message)


# ============================================================
# MAIN HANDLER FOR APP.PY
# ============================================================

def handle_npc_trigger_message(
    message: Any,
    fallback_player: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    send_reply: Optional[Callable[[str, Optional[str]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Main function app.py should call.

    Example from app.py:

        from npc_engine import handle_npc_trigger_message
        from mc_connector import send_to_minecraft

        result = handle_npc_trigger_message(
            message,
            fallback_player=player,
            send_reply=send_to_minecraft
        )
        if result:
            return jsonify(result)

    If send_reply is provided, npc_engine sends the reply.
    If send_reply is not provided, npc_engine only returns the reply.
    """
    trigger = parse_npc_trigger(message)
    if not trigger:
        return None

    player_name = trigger.player_name

    # Repair unresolved CitizensCMD placeholders.
    if player_name in {"<p>", "<player>", "%player%", "{player}", "player"}:
        player_name = fallback_player or "unknown"

    npc_log(f"Trigger detected npc={trigger.npc_name} player={player_name}")

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
            npc_log_exception("send_reply callback failed", exc)

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
# OPTIONAL ROUTE REGISTRATION
# ============================================================

def register_npc_routes(app: Any, send_reply: Optional[Callable[[str, Optional[str]], Any]] = None) -> None:
    """
    Optional helper if you want npc_engine.py to register Flask routes.

    In app.py:
        from npc_engine import register_npc_routes
        from mc_connector import send_to_minecraft

        register_npc_routes(app, send_reply=send_to_minecraft)

    This adds:
        POST /npc_chat
        POST /kairos/npc_chat
        POST /kairos/npc/dialogue
    """

    @app.route("/npc_chat", methods=["POST"])
    @app.route("/kairos/npc_chat", methods=["POST"])
    @app.route("/kairos/npc/dialogue", methods=["POST"])
    def npc_chat_route():  # type: ignore
        try:
            from flask import jsonify, request

            data = request.get_json(silent=True) or {}

            player = str(
                data.get("player")
                or data.get("player_name")
                or data.get("username")
                or "unknown"
            ).strip()

            npc_name = str(
                data.get("npc_name")
                or data.get("npc")
                or data.get("name")
                or "UnknownNPC"
            ).strip()

            raw_message = str(
                data.get("message")
                or data.get("content")
                or f"[NPC_TRIGGER] {npc_name} {player}"
            )

            context = {
                "location": data.get("location"),
                "world_time": data.get("world_time"),
                "quest": data.get("quest"),
                "raw": data,
            }

            reply = generate_npc_reply(npc_name, player, raw_message, context)

            delivered = False
            delivery_error = None

            if send_reply:
                try:
                    send_reply(reply, player)
                    delivered = True
                except Exception as exc:
                    delivery_error = str(exc)
                    npc_log_exception("npc_chat route delivery failed", exc)

            return jsonify({
                "ok": True,
                "handled": "npc_chat",
                "npc_name": npc_name,
                "player": player,
                "reply": reply,
                "delivered": delivered,
                "delivery_error": delivery_error,
            })

        except Exception as exc:
            npc_log_exception("npc_chat_route failed", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    test = "[NPC_TRIGGER] CaptainVaros RealSociety5107"
    result = handle_npc_trigger_message(test)
    print(json.dumps(result, indent=2))
