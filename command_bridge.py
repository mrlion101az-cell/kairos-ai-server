```python
"""
command_bridge.py
Kairos / Nexus Command Bridge
FULL NPC ENGINE INTEGRATION VERSION
"""

from __future__ import annotations

import os
import re
import traceback
from typing import Any, Dict, Optional

from ai_core import AIContext, generate_ai_response

from memory_engine import (
    append_player_memory,
    record_npc_interaction,
    record_system_error,
)

# ============================================================
# REAL NPC ENGINE IMPORT
# ============================================================

from npc_engine import (
    handle_npc_trigger_message,
    is_npc_trigger,
)

# ============================================================
# MC CONNECTOR IMPORTS
# ============================================================

try:
    from mc_connector import (
        send_to_minecraft,
        send_actionbar,
        broadcast_world_event,
    )
except Exception:
    send_to_minecraft = None
    send_actionbar = None
    broadcast_world_event = None


# ============================================================
# CONFIG
# ============================================================

COMMAND_BRIDGE_DEBUG = (
    os.getenv(
        "COMMAND_BRIDGE_DEBUG",
        "true"
    ).lower() == "true"
)

COMMAND_BRIDGE_SEND_TO_MC = (
    os.getenv(
        "COMMAND_BRIDGE_SEND_TO_MC",
        "true"
    ).lower() == "true"
)

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

def bridge_log(
    message: str,
    level: str = "INFO"
) -> None:

    if (
        COMMAND_BRIDGE_DEBUG
        or level in {
            "WARN",
            "ERROR",
            "FATAL"
        }
    ):
        print(
            f"[COMMAND_BRIDGE {level}] {message}",
            flush=True
        )


def bridge_log_exception(
    context: str,
    exc: Exception
) -> None:

    print(
        f"[COMMAND_BRIDGE ERROR] {context}: {exc}",
        flush=True
    )

    traceback.print_exc()

    try:
        record_system_error(
            context,
            str(exc)
        )
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
    return str(value or "").strip()


# ============================================================
# MINECRAFT DELIVERY
# ============================================================

def _push_to_minecraft(
    reply: str,
    player_name: Optional[str]
) -> bool:

    reply = _safe_reply_text(reply)

    if not reply:
        return False

    if not COMMAND_BRIDGE_SEND_TO_MC:
        bridge_log(
            "Minecraft push disabled",
            "WARN"
        )
        return False

    if not send_to_minecraft:
        bridge_log(
            "send_to_minecraft unavailable",
            "ERROR"
        )
        return False

    try:
        return bool(
            send_to_minecraft(
                reply,
                player_name
            )
        )

    except Exception as exc:
        bridge_log_exception(
            "send_to_minecraft failed",
            exc
        )

        return False


# ============================================================
# NPC ROUTING
# ============================================================

def route_npc_trigger(
    message: str,
    fallback_player: Optional[str] = None,
) -> Optional[Dict[str, Any]]:

    if not is_npc_trigger(message):
        return None

    bridge_log(
        f"NPC trigger routed -> {message}"
    )

    result = handle_npc_trigger_message(
        message,
        fallback_player=fallback_player,
        send_reply=send_to_minecraft,
    )

    if not result:
        return None

    try:
        record_npc_interaction(
            result.get("npc_name", "UnknownNPC"),
            result.get("player", fallback_player or "unknown"),
            message=message,
            reply=result.get("reply", ""),
        )
    except Exception as exc:
        bridge_log_exception(
            "NPC memory record failed",
            exc
        )

    return result


# ============================================================
# STANDARD CHAT
# ============================================================

def route_standard_chat(
    player_name: str,
    message: str,
) -> Dict[str, Any]:

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
        player_name
    )

    try:
        append_player_memory(
            player_name,
            f"Player said: {message}"
        )
    except Exception as exc:
        bridge_log_exception(
            "append_player_memory failed",
            exc
        )

    return {
        "ok": True,
        "handled": "standard_chat",
        "player": player_name,
        "message": message,
        "reply": reply,
        "delivered": delivered,
    }


# ============================================================
# MAIN ROUTER
# ============================================================

def process_incoming_message(
    message: Any,
    fallback_player: Optional[str] = None,
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

        bridge_log(
            f"Incoming message -> {text}"
        )

        # ====================================================
        # NPC ROUTING FIRST
        # ====================================================

        npc_result = route_npc_trigger(
            text,
            fallback_player=fallback_player,
        )

        if npc_result:
            return npc_result

        # ====================================================
        # NORMAL CHAT
        # ====================================================

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

        return route_standard_chat(
            player_name,
            content,
        )

    except Exception as exc:

        bridge_log_exception(
            "process_incoming_message failed",
            exc
        )

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
            "[NPC_TRIGGER] CaptainVaros RealSociety5107"
        )
    )
```
