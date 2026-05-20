
"""
command_bridge.py
Kairos / Nexus Command Bridge

Purpose:
- Central routing layer between systems
- Receives incoming events/messages
- Determines what subsystem should handle them
- Safely routes:
    - NPC triggers
    - Minecraft chat
    - world events
    - Kairos broadcasts
    - future faction/war systems

This file is:
- NOT Flask
- NOT Discord
- NOT the AI brain
- NOT Minecraft transport

This is the TRAFFIC CONTROLLER.
"""

from __future__ import annotations

import json
import re
import time
import traceback
from typing import Any, Dict, Optional

from ai_core import (
    AIContext,
    generate_ai_response,
)

from npc_engine import (
    handle_npc_trigger_message,
    is_npc_trigger,
)

from memory_engine import (
    append_player_memory,
    record_npc_interaction,
    record_system_error,
    record_system_note,
    record_world_event,
)

from mc_connector import (
    send_to_minecraft,
    send_chat,
    send_actionbar,
    broadcast_world_event,
)


# ============================================================
# CONFIG
# ============================================================

COMMAND_BRIDGE_DEBUG = True

CHAT_PREFIX_PATTERN = re.compile(
    r"^\[(.*?)\]\s*(.*)$"
)

SYSTEM_IGNORE_PATTERNS = [
    "[Server thread/",
    "issued server command",
]


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
# HELPERS
# ============================================================

def normalize_message(message: Any) -> str:
    return str(message or "").strip()


def should_ignore_message(message: str) -> bool:
    text = message.lower()

    for item in SYSTEM_IGNORE_PATTERNS:
        if item.lower() in text:
            return True

    return False


def parse_basic_chat(message: str) -> Dict[str, Any]:
    """
    Attempts to parse:
      [Player] hello
      [Server] message
    """

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


# ============================================================
# NPC ROUTING
# ============================================================

def route_npc_trigger(
    message: str,
    fallback_player: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Routes NPC trigger events into npc_engine.
    """

    if not is_npc_trigger(message):
        return None

    bridge_log(f"NPC trigger routed -> {message}")

    result = handle_npc_trigger_message(
        message,
        fallback_player=fallback_player,
        send_reply=send_to_minecraft,
    )

    if result:
        try:
            record_npc_interaction(
                result.get("npc_name", "UnknownNPC"),
                result.get("player", "unknown"),
                message=message,
                reply=result.get("reply", ""),
            )
        except Exception as exc:
            bridge_log_exception("NPC memory record failed", exc)

    return result


# ============================================================
# STANDARD CHAT ROUTING
# ============================================================

def route_standard_chat(
    player_name: str,
    message: str,
) -> Dict[str, Any]:
    """
    Standard Kairos interaction.
    """

    context = AIContext(
        mode="observer",
        player_name=player_name,
    )

    reply = generate_ai_response(
        message,
        context=context,
    )

    send_to_minecraft(reply, player_name)

    append_player_memory(
        player_name,
        f"Player said: {message}"
    )

    return {
        "ok": True,
        "handled": "standard_chat",
        "player": player_name,
        "message": message,
        "reply": reply,
    }


# ============================================================
# WORLD EVENT ROUTING
# ============================================================

def route_world_event(
    event_type: str,
    description: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Routes autonomous world events.
    """

    record_world_event(
        event_type,
        description,
        location=location,
    )

    broadcast_world_event(
        description,
        title=event_type.upper(),
    )

    return {
        "ok": True,
        "handled": "world_event",
        "event_type": event_type,
        "description": description,
    }


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def process_incoming_message(
    message: Any,
    fallback_player: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    MAIN CENTRAL ROUTER.

    Future app.py will mostly call THIS.
    """

    try:
        text = normalize_message(message)

        if not text:
            return None

        if should_ignore_message(text):
            return None

        bridge_log(f"Incoming message -> {text}")

        # ====================================================
        # NPC TRIGGERS
        # ====================================================

        npc_result = route_npc_trigger(
            text,
            fallback_player=fallback_player,
        )

        if npc_result:
            return npc_result

        # ====================================================
        # STANDARD CHAT
        # ====================================================

        parsed = parse_basic_chat(text)

        player_name = (
            fallback_player
            or parsed.get("player")
            or "unknown"
        )

        content = parsed.get("content") or text

        return route_standard_chat(
            player_name,
            content,
        )

    except Exception as exc:
        bridge_log_exception("process_incoming_message failed", exc)

        return {
            "ok": False,
            "error": str(exc),
        }


# ============================================================
# COMMAND HELPERS
# ============================================================

def issue_kairos_warning(
    player_name: str,
    message: str,
) -> bool:
    try:
        send_actionbar(
            f"[Kairos] {message}",
            target=player_name,
            color="red",
        )

        return True

    except Exception as exc:
        bridge_log_exception("issue_kairos_warning failed", exc)
        return False


def issue_kairos_broadcast(
    message: str,
) -> bool:
    try:
        broadcast_world_event(
            message,
            title="KAIROS",
        )

        return True

    except Exception as exc:
        bridge_log_exception("issue_kairos_broadcast failed", exc)
        return False


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":

    test = "[NPC_TRIGGER] CaptainVaros RealSociety5107"

    result = process_incoming_message(test)

    print(json.dumps(result, indent=2))
