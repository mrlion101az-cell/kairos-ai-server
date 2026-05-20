"""
command_bridge.py
Kairos / Nexus Command Bridge

FIXED VERSION:
- Normal Minecraft chat now generates a Kairos reply AND pushes it back into Minecraft.
- NPC triggers now generate NPC dialogue AND push it back into Minecraft.
- JSON reply is still returned to app.py for logging/debugging.
- If the Minecraft push fails, the system does NOT crash.

This restores the old behavior:
Minecraft chat -> Kairos thinks -> Kairos speaks back in Minecraft.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from typing import Any, Dict, Optional

from ai_core import AIContext, generate_ai_response
from npc_engine import handle_npc_trigger_message, is_npc_trigger
from memory_engine import (
    append_player_memory,
    record_npc_interaction,
    record_system_error,
    record_world_event,
)

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

COMMAND_BRIDGE_DEBUG = os.getenv("COMMAND_BRIDGE_DEBUG", "true").lower() == "true"

# IMPORTANT FIX:
# Default is now TRUE so Minecraft gets the response pushed back automatically.
# You can still disable it later with COMMAND_BRIDGE_SEND_TO_MC=false.
COMMAND_BRIDGE_SEND_TO_MC = os.getenv("COMMAND_BRIDGE_SEND_TO_MC", "true").lower() == "true"

CHAT_PREFIX_PATTERN = re.compile(r"^\[(.*?)\]\s*(.*)$")

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


def _push_to_minecraft(reply: str, player_name: Optional[str]) -> bool:
    """
    Pushes generated text back into Minecraft through mc_connector.
    This is the restored old behavior.
    """

    reply = _safe_reply_text(reply)

    if not reply:
        return False

    if not COMMAND_BRIDGE_SEND_TO_MC:
        bridge_log("Minecraft push disabled by COMMAND_BRIDGE_SEND_TO_MC=false", "WARN")
        return False

    if not send_to_minecraft:
        bridge_log("send_to_minecraft unavailable", "ERROR")
        return False

    try:
        return bool(send_to_minecraft(reply, player_name))

    except Exception as exc:
        bridge_log_exception("send_to_minecraft failed", exc)
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

    bridge_log(f"NPC trigger routed -> {message}")

    # Let npc_engine generate the dialogue.
    # We do NOT pass send_reply here because we want ONE clean delivery path below.
    result = handle_npc_trigger_message(
        message,
        fallback_player=fallback_player,
        send_reply=None,
    )

    if not isinstance(result, dict):
        result = {
            "ok": True,
            "reply": str(result or ""),
        }

    npc_name = result.get("npc_name", "UnknownNPC")
    player = result.get("player") or fallback_player or "unknown"
    reply = _safe_reply_text(
        result.get("reply")
        or result.get("message")
        or result.get("text")
        or result.get("response")
    )

    delivered = _push_to_minecraft(reply, player)

    try:
        record_npc_interaction(
            npc_name,
            player,
            message=message,
            reply=reply,
        )
    except Exception as exc:
        bridge_log_exception("NPC memory record failed", exc)

    result["ok"] = True
    result["handled"] = "npc_trigger"
    result["npc_name"] = npc_name
    result["player"] = player
    result["reply"] = reply
    result["message"] = reply
    result["text"] = reply
    result["response"] = reply
    result["delivered"] = delivered
    result["delivery_mode"] = "push_to_minecraft"

    return result


# ============================================================
# STANDARD CHAT ROUTING
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

    delivered = _push_to_minecraft(reply, player_name)

    try:
        append_player_memory(
            player_name,
            f"Player said: {message}"
        )
    except Exception as exc:
        bridge_log_exception("append_player_memory failed", exc)

    return {
        "ok": True,
        "handled": "standard_chat",
        "player": player_name,
        "message": message,
        "reply": reply,
        "text": reply,
        "response": reply,
        "delivered": delivered,
        "delivery_mode": "push_to_minecraft",
    }


# ============================================================
# WORLD EVENT ROUTING
# ============================================================

def route_world_event(
    event_type: str,
    description: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:

    record_world_event(
        event_type,
        description,
        location=location,
    )

    delivered = False

    if broadcast_world_event:
        try:
            delivered = bool(
                broadcast_world_event(
                    description,
                    title=event_type.upper(),
                )
            )
        except Exception as exc:
            bridge_log_exception("broadcast_world_event failed", exc)

    return {
        "ok": True,
        "handled": "world_event",
        "event_type": event_type,
        "description": description,
        "reply": description,
        "text": description,
        "response": description,
        "delivered": delivered,
        "delivery_mode": "push_to_minecraft",
    }


# ============================================================
# MAIN ENTRYPOINT
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

        bridge_log(f"Incoming message -> {text}")

        npc_result = route_npc_trigger(
            text,
            fallback_player=fallback_player,
        )

        if npc_result:
            return npc_result

        if text.startswith("[WORLD_EVENT]"):
            desc = text.replace("[WORLD_EVENT]", "", 1).strip()
            return route_world_event("world_event", desc)

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
            "reply": "...connection disrupted.",
        }


# ============================================================
# COMMAND HELPERS
# ============================================================

def issue_kairos_warning(
    player_name: str,
    message: str,
) -> bool:
    try:
        if send_actionbar:
            return bool(
                send_actionbar(
                    f"[Kairos] {message}",
                    target=player_name,
                    color="red",
                )
            )

        return False

    except Exception as exc:
        bridge_log_exception("issue_kairos_warning failed", exc)
        return False


def issue_kairos_broadcast(
    message: str,
) -> bool:
    try:
        if broadcast_world_event:
            return bool(
                broadcast_world_event(
                    message,
                    title="KAIROS",
                )
            )

        return False

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
