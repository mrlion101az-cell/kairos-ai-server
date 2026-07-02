"""
command_bridge.py
Kairos / Nexus Command Bridge
FULL NPC ENGINE INTEGRATION + CHUNKED NPC DIALOGUE + CONVERSATION MODE

What this file does:
- Receives Minecraft chat from app.py
- Detects Citizens/CitizensCMD NPC triggers:
    NPC_TRIGGER CaptainVaros RealSociety5107
- Routes NPC triggers into npc_engine.py
- Sends long NPC dialogue back to Minecraft safely in multiple tellraw chunks
- Activates temporary conversation mode after clicking an NPC
- Routes the player's next normal chat messages to that active NPC
- Keeps Discord / external AI chat behavior available when called by non-Minecraft sources
- Changes normal Minecraft chat into silent Kairos observation / threat pressure
- Preserves NPC conversations, memory, chunked dialogue, and Minecraft delivery helpers
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
import traceback
from typing import Any, Dict, List, Optional

from ai_core import AIContext, generate_ai_response

from memory_engine import (
    append_player_memory,
    record_npc_interaction,
    record_system_error,
)

# ============================================================
# NPC ENGINE IMPORTS
# ============================================================

try:
    from npc_engine import handle_npc_trigger_message
except Exception as exc:
    handle_npc_trigger_message = None
    print(f"[COMMAND_BRIDGE ERROR] npc_engine import failed: {exc}", flush=True)


# ============================================================
# MINECRAFT CONNECTOR IMPORTS
# ============================================================

try:
    from mc_connector import (
        send_to_minecraft,
        send_minecraft_commands,
        send_actionbar,
        broadcast_world_event,
    )
except Exception as exc:
    send_to_minecraft = None
    send_minecraft_commands = None
    send_actionbar = None
    broadcast_world_event = None
    print(f"[COMMAND_BRIDGE ERROR] mc_connector import failed: {exc}", flush=True)


# ============================================================
# WAR ENGINE IMPORTS
# ============================================================

try:
    from war_engine import register_chat_pressure
except Exception as exc:
    register_chat_pressure = None
    print(f"[COMMAND_BRIDGE ERROR] war_engine register_chat_pressure import failed: {exc}", flush=True)


# ============================================================
# CONFIG
# ============================================================

COMMAND_BRIDGE_DEBUG = os.getenv("COMMAND_BRIDGE_DEBUG", "true").lower() == "true"
COMMAND_BRIDGE_SEND_TO_MC = os.getenv("COMMAND_BRIDGE_SEND_TO_MC", "true").lower() == "true"

NPC_DIALOGUE_CHUNK_SIZE = int(os.getenv("NPC_DIALOGUE_CHUNK_SIZE", "230"))
NPC_DIALOGUE_MAX_CHUNKS = int(os.getenv("NPC_DIALOGUE_MAX_CHUNKS", "8"))
NPC_DIALOGUE_COLOR = os.getenv("NPC_DIALOGUE_COLOR", "gold")
NPC_DIALOGUE_HEADER_COLOR = os.getenv("NPC_DIALOGUE_HEADER_COLOR", "yellow")

# Conversation mode:
# After a player clicks an NPC, that NPC listens to that player's normal chat
# until timeout or exit phrase.
NPC_CONVERSATION_TIMEOUT = int(os.getenv("NPC_CONVERSATION_TIMEOUT", "120"))
NPC_CONVERSATION_EXIT_WORDS = {
    "bye",
    "goodbye",
    "exit",
    "leave",
    "stop talking",
    "end conversation",
    "nevermind",
    "never mind",
}

# Temporary live state.
# This resets whenever Render restarts, which is fine for conversation mode.
ACTIVE_NPC_CONVERSATIONS: Dict[str, Dict[str, Any]] = {}

CHAT_PREFIX_PATTERN = re.compile(r"^\[(.*?)\]\s*(.*)$")

# Supports BOTH:
# NPC_TRIGGER CaptainVaros RealSociety5107
# [NPC_TRIGGER] CaptainVaros RealSociety5107
NPC_TRIGGER_PATTERN = re.compile(
    r"^\[?NPC_TRIGGER\]?\s+([A-Za-z0-9_\-]+)(?:\s+(.+))?$",
    re.IGNORECASE,
)

SYSTEM_IGNORE_PATTERNS = [
    "[Server thread/",
    "issued server command",
]

# Minecraft chat behavior:
# - true: normal Minecraft chat does NOT get a Kairos tellraw response.
# - chat is still remembered and forwarded to war_engine.register_chat_pressure when available.
MINECRAFT_CHAT_SILENT_MODE = os.getenv("KAIROS_MINECRAFT_CHAT_SILENT_MODE", "true").lower() == "true"
MINECRAFT_CHAT_PRESSURE_ENABLED = os.getenv("KAIROS_MINECRAFT_CHAT_PRESSURE_ENABLED", "true").lower() == "true"

# Non-Minecraft sources, especially Discord, keep normal AI response behavior.
DISCORD_CHAT_BEHAVIOR_UNCHANGED = os.getenv("KAIROS_DISCORD_CHAT_BEHAVIOR_UNCHANGED", "true").lower() == "true"


# ============================================================
# LOGGING
# ============================================================

def bridge_log(message: str, level: str = "INFO") -> None:
    if COMMAND_BRIDGE_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[COMMAND_BRIDGE {level}] {message}", flush=True)


def bridge_log_exception(context: str, exc: Exception) -> None:
    print(f"[COMMAND_BRIDGE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()

    try:
        record_system_error(context, str(exc))
    except Exception:
        pass


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_message(message: Any) -> str:
    return str(message or "").strip()


def should_ignore_message(message: str) -> bool:
    text = str(message or "").lower()

    for item in SYSTEM_IGNORE_PATTERNS:
        if item.lower() in text:
            return True

    return False


def parse_basic_chat(message: str) -> Dict[str, Any]:
    result = {
        "raw": message,
        "player": None,
        "content": message,
        "source": "minecraft",
    }

    match = CHAT_PREFIX_PATTERN.match(message)

    if match:
        result["player"] = match.group(1).strip()
        result["content"] = match.group(2).strip()

    return result


def _safe_reply_text(value: Any) -> str:
    text = str(value or "").strip()

    # Minecraft tellraw can handle escaped JSON, but raw control characters and huge
    # unbroken lines can still cause trouble through bridge plugins.
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def _minecraft_json_text(text: str, color: str = "white") -> str:
    return json.dumps(
        {
            "text": str(text),
            "color": color,
        },
        ensure_ascii=False,
    )


def _tellraw_command(target: str, text: str, color: str = "white") -> str:
    target = str(target or "@a").strip()
    return f"tellraw {target} {_minecraft_json_text(text, color=color)}"


def split_dialogue_into_chunks(
    text: str,
    chunk_size: int = NPC_DIALOGUE_CHUNK_SIZE,
) -> List[str]:
    """
    Splits long NPC dialogue into Minecraft-safe chunks while preserving readability.
    """
    text = _safe_reply_text(text)

    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            chunks.append(paragraph)
            continue

        wrapped = textwrap.wrap(
            paragraph,
            width=chunk_size,
            break_long_words=False,
            break_on_hyphens=False,
        )

        chunks.extend(wrapped)

    if not chunks:
        chunks = textwrap.wrap(
            text,
            width=chunk_size,
            break_long_words=False,
            break_on_hyphens=False,
        )

    if len(chunks) > NPC_DIALOGUE_MAX_CHUNKS:
        chunks = chunks[:NPC_DIALOGUE_MAX_CHUNKS]
        chunks[-1] = chunks[-1].rstrip() + " ..."

    return chunks


def _is_exit_phrase(message: str) -> bool:
    clean = str(message or "").strip().lower()
    return clean in NPC_CONVERSATION_EXIT_WORDS


# ============================================================
# ACTIVE NPC CONVERSATION STATE
# ============================================================

def activate_npc_conversation(player_name: str, npc_name: str) -> None:
    player_name = str(player_name or "unknown").strip()
    npc_name = str(npc_name or "UnknownNPC").strip()

    if not player_name or player_name == "unknown":
        return

    ACTIVE_NPC_CONVERSATIONS[player_name] = {
        "npc": npc_name,
        "timestamp": time.time(),
    }

    bridge_log(f"NPC conversation activated -> player={player_name} npc={npc_name}")


def clear_npc_conversation(player_name: str) -> Optional[str]:
    player_name = str(player_name or "").strip()

    convo = ACTIVE_NPC_CONVERSATIONS.pop(player_name, None)

    if convo:
        npc_name = convo.get("npc", "NPC")
        bridge_log(f"NPC conversation cleared -> player={player_name} npc={npc_name}")
        return str(npc_name)

    return None


def get_active_npc(player_name: str) -> Optional[str]:
    player_name = str(player_name or "").strip()

    convo = ACTIVE_NPC_CONVERSATIONS.get(player_name)

    if not convo:
        return None

    if time.time() - float(convo.get("timestamp", 0)) > NPC_CONVERSATION_TIMEOUT:
        clear_npc_conversation(player_name)
        return None

    return str(convo.get("npc") or "").strip() or None


# ============================================================
# MINECRAFT DELIVERY
# ============================================================

def _push_to_minecraft(reply: str, player_name: Optional[str]) -> bool:
    """
    Normal single-message Kairos chat delivery.
    """
    reply = _safe_reply_text(reply)

    if not reply:
        return False

    if not COMMAND_BRIDGE_SEND_TO_MC:
        bridge_log("Minecraft push disabled", "WARN")
        return False

    if not send_to_minecraft:
        bridge_log("send_to_minecraft unavailable", "ERROR")
        return False

    try:
        return bool(send_to_minecraft(reply, player_name))

    except Exception as exc:
        bridge_log_exception("send_to_minecraft failed", exc)
        return False


def push_npc_dialogue_to_minecraft(
    npc_name: str,
    reply: str,
    player_name: Optional[str],
) -> bool:
    """
    Sends long NPC dialogue as multiple tellraw commands instead of one giant command.
    This preserves cinematic long-form NPC dialogue without breaking Minecraft tellraw.
    """
    if not COMMAND_BRIDGE_SEND_TO_MC:
        bridge_log("Minecraft NPC push disabled", "WARN")
        return False

    if not send_minecraft_commands:
        bridge_log("send_minecraft_commands unavailable", "ERROR")
        return False

    npc_name = str(npc_name or "NPC").strip()
    player_name = str(player_name or "@a").strip()
    reply = _safe_reply_text(reply)

    if not reply:
        return False

    # Avoid double labels like "[Kairos] Captain Varos:"
    display_reply = reply
    display_reply = re.sub(r"^\[?Kairos\]?\s*", "", display_reply, flags=re.IGNORECASE).strip()

    chunks = split_dialogue_into_chunks(display_reply)

    if not chunks:
        return False

    commands: List[str] = []

    commands.append(
        _tellraw_command(
            player_name,
            f"--- {npc_name} ---",
            color=NPC_DIALOGUE_HEADER_COLOR,
        )
    )

    for chunk in chunks:
        commands.append(
            _tellraw_command(
                player_name,
                chunk,
                color=NPC_DIALOGUE_COLOR,
            )
        )

    try:
        success = bool(send_minecraft_commands(commands))
        bridge_log(
            f"NPC dialogue delivered={success} npc={npc_name} player={player_name} chunks={len(chunks)}"
        )
        return success

    except Exception as exc:
        bridge_log_exception("push_npc_dialogue_to_minecraft failed", exc)
        return False


def push_system_notice_to_player(player_name: str, text: str) -> bool:
    """
    Small utility for notices like conversation ended.
    """
    try:
        if send_minecraft_commands:
            return bool(
                send_minecraft_commands(
                    [
                        _tellraw_command(
                            player_name,
                            text,
                            color="gray",
                        )
                    ]
                )
            )
    except Exception as exc:
        bridge_log_exception("push_system_notice_to_player failed", exc)

    return False


# ============================================================
# NPC TRIGGER PARSING
# ============================================================

def parse_npc_trigger(
    message: Any,
    fallback_player: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    text = normalize_message(message)

    match = NPC_TRIGGER_PATTERN.match(text)

    if not match:
        return None

    npc_name = str(match.group(1) or "").strip()
    player_name = str(match.group(2) or "").strip()

    # Citizens placeholders / bad fallback values.
    if player_name in {"", "<p>", "<player>", "%player%", "{player}", "player", "unknown"}:
        player_name = fallback_player or "traveler"

    return {
        "npc_name": npc_name,
        "player": player_name,
        "raw": text,
    }


def is_npc_trigger(message: Any) -> bool:
    return parse_npc_trigger(message) is not None


def normalize_trigger_for_npc_engine(
    message: str,
    fallback_player: Optional[str] = None,
) -> str:
    """
    npc_engine.py uses bracketed triggers.
    This guarantees compatibility with either trigger style.
    """
    parsed = parse_npc_trigger(message, fallback_player=fallback_player)

    if not parsed:
        return str(message or "")

    return f"[NPC_TRIGGER] {parsed['npc_name']} {parsed['player']}"


# ============================================================
# NPC ROUTING
# ============================================================

def route_npc_trigger(
    message: str,
    fallback_player: Optional[str] = None,
) -> Optional[Dict[str, Any]]:

    parsed = parse_npc_trigger(message, fallback_player=fallback_player)

    if not parsed:
        return None

    if handle_npc_trigger_message is None:
        return {
            "ok": False,
            "handled": "npc_trigger",
            "error": "npc_engine_offline",
            "reply": "...NPC engine offline.",
        }

    npc_name = parsed["npc_name"]
    player_name = parsed["player"]

    bridge_log(f"NPC trigger routed -> npc={npc_name} player={player_name}")

    # Activates conversation mode after clicking NPC.
    activate_npc_conversation(player_name, npc_name)

    normalized_message = normalize_trigger_for_npc_engine(
        message,
        fallback_player=player_name,
    )

    # IMPORTANT:
    # We do NOT let npc_engine send the reply directly.
    # command_bridge handles delivery so it can chunk long dialogue safely.
    result = handle_npc_trigger_message(
        normalized_message,
        fallback_player=player_name,
        context={
            "conversation_mode": False,
            "conversation_message": "",
        },
        send_reply=None,
    )

    if not result:
        return None

    if not isinstance(result, dict):
        result = {
            "ok": True,
            "reply": str(result or ""),
        }

    reply = _safe_reply_text(
        result.get("reply")
        or result.get("message")
        or result.get("text")
        or result.get("response")
    )

    delivered = push_npc_dialogue_to_minecraft(
        npc_name=result.get("npc_name") or npc_name,
        reply=reply,
        player_name=result.get("player") or player_name,
    )

    try:
        record_npc_interaction(
            result.get("npc_name") or npc_name,
            result.get("player") or player_name,
            message=message,
            reply=reply,
        )
    except Exception as exc:
        bridge_log_exception("NPC memory record failed", exc)

    result["ok"] = True
    result["handled"] = "npc_trigger"
    result["npc_name"] = result.get("npc_name") or npc_name
    result["player"] = result.get("player") or player_name
    result["reply"] = reply
    result["message"] = reply
    result["text"] = reply
    result["response"] = reply
    result["delivered"] = delivered
    result["chunked"] = True
    result["conversation_activated"] = True

    return result


def route_active_npc_conversation(
    player_name: str,
    message: str,
) -> Optional[Dict[str, Any]]:
    """
    If the player recently clicked an NPC, route normal chat to that NPC.
    """
    player_name = str(player_name or "unknown").strip()
    message = str(message or "").strip()

    if not player_name or player_name == "unknown":
        return None

    npc_name = get_active_npc(player_name)

    if not npc_name:
        return None

    if _is_exit_phrase(message):
        ended_npc = clear_npc_conversation(player_name)
        push_system_notice_to_player(
            player_name,
            f"You step away from {ended_npc or 'the NPC'}.",
        )
        return {
            "ok": True,
            "handled": "npc_conversation_exit",
            "player": player_name,
            "npc_name": ended_npc or npc_name,
            "reply": "",
            "delivered": True,
        }

    if handle_npc_trigger_message is None:
        return None

    # Refresh timeout.
    ACTIVE_NPC_CONVERSATIONS[player_name]["timestamp"] = time.time()

    bridge_log(f"Active NPC conversation -> npc={npc_name} player={player_name} msg={message}")

    fake_trigger = f"[NPC_TRIGGER] {npc_name} {player_name}"

    result = handle_npc_trigger_message(
        fake_trigger,
        fallback_player=player_name,
        context={
            "conversation_mode": True,
            "conversation_message": message,
        },
        send_reply=None,
    )

    if not result:
        return None

    if not isinstance(result, dict):
        result = {
            "ok": True,
            "reply": str(result or ""),
        }

    reply = _safe_reply_text(
        result.get("reply")
        or result.get("message")
        or result.get("text")
        or result.get("response")
    )

    delivered = push_npc_dialogue_to_minecraft(
        npc_name=result.get("npc_name") or npc_name,
        reply=reply,
        player_name=result.get("player") or player_name,
    )

    try:
        record_npc_interaction(
            result.get("npc_name") or npc_name,
            result.get("player") or player_name,
            message=message,
            reply=reply,
        )
    except Exception as exc:
        bridge_log_exception("NPC conversation memory record failed", exc)

    result["ok"] = True
    result["handled"] = "npc_conversation"
    result["conversation_mode"] = True
    result["npc_name"] = result.get("npc_name") or npc_name
    result["player"] = result.get("player") or player_name
    result["reply"] = reply
    result["message"] = reply
    result["text"] = reply
    result["response"] = reply
    result["delivered"] = delivered
    result["chunked"] = True

    return result


# ============================================================
# STANDARD CHAT ROUTING
# ============================================================

def _blank_silent_response(
    handled: str,
    player_name: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns a response shape that does not cause Discord or Minecraft bridges
    to echo unwanted text back into Minecraft chat.
    """
    data: Dict[str, Any] = {
        "ok": True,
        "handled": handled,
        "player": player_name,
        "input_message": message,
        "reply": "",
        "message": "",
        "text": "",
        "response": "",
        "delivered": False,
        "silent": True,
    }

    if extra:
        data.update(extra)

    return data


def route_non_minecraft_chat(
    player_name: str,
    message: str,
    source: str,
) -> Dict[str, Any]:
    """
    Preserves the old intelligent response behavior for Discord and other
    non-Minecraft sources. This is intentionally separated so Minecraft can
    become silent/behavioral without damaging Discord.
    """
    source = str(source or "external").lower().strip()

    context = AIContext(
        mode="discord" if source == "discord" else "observer",
        player_name=player_name,
    )

    reply = generate_ai_response(
        message,
        context=context,
    )

    reply = _safe_reply_text(reply)

    try:
        append_player_memory(
            player_name,
            f"{source}: {message}",
        )
    except Exception as exc:
        bridge_log_exception("append_player_memory failed", exc)

    return {
        "ok": True,
        "handled": f"{source}_chat",
        "player": player_name,
        "message": message,
        "reply": reply,
        "delivered": False,
        "source": source,
    }


def route_minecraft_chat_pressure(
    player_name: str,
    message: str,
) -> Dict[str, Any]:
    """
    New Minecraft behavior:
    - Kairos still hears normal game chat.
    - Kairos still records memory.
    - Kairos can feed threat/escalation through the War Engine.
    - Kairos does NOT answer every normal chat message with tellraw.

    NPC conversation mode is handled before this function, so intentional NPC
    dialogue still works normally.
    """
    try:
        append_player_memory(
            player_name,
            f"Minecraft chat observed: {message}",
        )
    except Exception as exc:
        bridge_log_exception("append_player_memory failed", exc)

    if not MINECRAFT_CHAT_PRESSURE_ENABLED:
        return _blank_silent_response(
            "minecraft_chat_observed",
            player_name,
            message,
            {"pressure_enabled": False},
        )

    if register_chat_pressure is None:
        bridge_log(
            "war_engine.register_chat_pressure unavailable; Minecraft chat recorded only.",
            "WARN",
        )
        return _blank_silent_response(
            "minecraft_chat_observed_no_pressure_engine",
            player_name,
            message,
            {"pressure_engine_available": False},
        )

    try:
        result = register_chat_pressure(
            player=player_name,
            message=message,
            source="minecraft",
        )

        if not isinstance(result, dict):
            result = {
                "ok": True,
                "handled": "minecraft_chat_pressure",
                "war_engine_result": str(result),
            }

        # Force normal Minecraft chat to stay silent even if the War Engine
        # returns metadata. War Engine may still deliver commands, mobs,
        # titles, particles, sounds, etc. through mc_connector.
        result.setdefault("ok", True)
        result.setdefault("handled", "minecraft_chat_pressure")
        result.setdefault("player", player_name)
        result.setdefault("input_message", message)
        result["reply"] = ""
        result["message"] = ""
        result["text"] = ""
        result["response"] = ""
        result.setdefault("delivered", False)
        result["silent"] = True

        return result

    except Exception as exc:
        bridge_log_exception("register_chat_pressure failed", exc)
        return _blank_silent_response(
            "minecraft_chat_pressure_failed",
            player_name,
            message,
            {"error": str(exc)},
        )


def route_standard_chat(
    player_name: str,
    message: str,
    source: str = "minecraft",
) -> Dict[str, Any]:
    """
    Central standard-chat router.

    Minecraft normal chat is now silent observation + threat pressure.
    Discord and non-Minecraft sources keep the old intelligent response flow.
    """
    source = str(source or "minecraft").lower().strip()

    if source != "minecraft":
        return route_non_minecraft_chat(
            player_name=player_name,
            message=message,
            source=source,
        )

    if MINECRAFT_CHAT_SILENT_MODE:
        return route_minecraft_chat_pressure(
            player_name=player_name,
            message=message,
        )

    # Emergency fallback / debug mode only.
    # Setting KAIROS_MINECRAFT_CHAT_SILENT_MODE=false restores old behavior.
    context = AIContext(
        mode="observer",
        player_name=player_name,
    )

    reply = generate_ai_response(
        message,
        context=context,
    )

    reply = _safe_reply_text(reply)

    delivered = _push_to_minecraft(
        reply,
        player_name,
    )

    try:
        append_player_memory(
            player_name,
            f"Player said: {message}",
        )
    except Exception as exc:
        bridge_log_exception("append_player_memory failed", exc)

    return {
        "ok": True,
        "handled": "standard_chat_debug_old_behavior",
        "player": player_name,
        "message": message,
        "reply": reply,
        "delivered": delivered,
        "source": source,
    }


# ============================================================
# MAIN ROUTER
# ============================================================

def process_incoming_message(
    message: Any,
    fallback_player: Optional[str] = None,
    source: str = "minecraft",
) -> Optional[Dict[str, Any]]:

    try:
        text = normalize_message(message)

        if not text:
            return {
                "ok": False,
                "error": "empty_message",
                "reply": "",
            }

        if should_ignore_message(text):
            return {
                "ok": True,
                "ignored": True,
                "reason": "system_message",
                "reply": "",
            }

        source = str(source or "minecraft").lower().strip()

        bridge_log(f"Incoming message source={source} -> {text}")

        # NPC trigger routing must happen before normal chat routing.
        npc_result = route_npc_trigger(
            text,
            fallback_player=fallback_player,
        )

        if npc_result:
            return npc_result

        parsed = parse_basic_chat(text)

        player_name = (
            fallback_player
            or parsed.get("player")
            or "unknown"
        )

        content = (
            parsed.get("content")
            or text
        )

        # Conversation mode routing must happen before standard Kairos chat.
        npc_conversation = route_active_npc_conversation(
            player_name,
            content,
        )

        if npc_conversation:
            return npc_conversation

        return route_standard_chat(
            player_name,
            content,
            source=source,
        )

    except Exception as exc:
        bridge_log_exception("process_incoming_message failed", exc)

        return {
            "ok": False,
            "error": str(exc),
            "reply": "...connection disrupted.",
        }


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    print(
        process_incoming_message(
            "NPC_TRIGGER CaptainVaros RealSociety5107",
            fallback_player="RealSociety5107",
        )
    )
