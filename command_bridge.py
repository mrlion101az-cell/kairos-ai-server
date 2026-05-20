"""
command_bridge.py
Kairos / Nexus Command Bridge

Restores old Minecraft chat behavior:
- Normal chat returns the reply to app.py as JSON.
- It does NOT push back into Minecraft by default.
- Set COMMAND_BRIDGE_SEND_TO_MC=true only if you have a working outbound /command HTTP bridge.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from typing import Any, Dict, Optional

from ai_core import AIContext, generate_ai_response
from npc_engine import handle_npc_trigger_message, is_npc_trigger
from memory_engine import append_player_memory, record_npc_interaction, record_system_error, record_world_event

try:
    from mc_connector import send_to_minecraft, send_actionbar, broadcast_world_event
except Exception:
    send_to_minecraft = None
    send_actionbar = None
    broadcast_world_event = None

COMMAND_BRIDGE_DEBUG = os.getenv("COMMAND_BRIDGE_DEBUG", "true").lower() == "true"
COMMAND_BRIDGE_SEND_TO_MC = os.getenv("COMMAND_BRIDGE_SEND_TO_MC", "false").lower() == "true"

CHAT_PREFIX_PATTERN = re.compile(r"^\[(.*?)\]\s*(.*)$")
SYSTEM_IGNORE_PATTERNS = ["[Server thread/", "issued server command"]

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

def normalize_message(message: Any) -> str:
    return str(message or "").strip()

def should_ignore_message(message: str) -> bool:
    text = message.lower()
    for item in SYSTEM_IGNORE_PATTERNS:
        if item.lower() in text:
            return True
    return False

def parse_basic_chat(message: str) -> Dict[str, Any]:
    result = {"raw": message, "player": None, "content": message, "source": "minecraft"}
    match = CHAT_PREFIX_PATTERN.match(message)
    if match:
        result["player"] = match.group(1).strip()
        result["content"] = match.group(2).strip()
    return result

def _maybe_send_to_minecraft(reply: str, player_name: Optional[str]) -> bool:
    if not COMMAND_BRIDGE_SEND_TO_MC:
        return False
    if not send_to_minecraft:
        return False
    try:
        return bool(send_to_minecraft(reply, player_name))
    except Exception as exc:
        bridge_log_exception("send_to_minecraft failed", exc)
        return False

def route_npc_trigger(message: str, fallback_player: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not is_npc_trigger(message):
        return None

    bridge_log(f"NPC trigger routed -> {message}")

    send_reply_callback = send_to_minecraft if COMMAND_BRIDGE_SEND_TO_MC else None

    result = handle_npc_trigger_message(
        message,
        fallback_player=fallback_player,
        send_reply=send_reply_callback,
    )

    if result:
        try:
            record_npc_interaction(
                result.get("npc_name", "UnknownNPC"),
                result.get("player", fallback_player or "unknown"),
                message=message,
                reply=result.get("reply", ""),
            )
        except Exception as exc:
            bridge_log_exception("NPC memory record failed", exc)

        result.setdefault("ok", True)
        result.setdefault("handled", "npc_trigger")
        result.setdefault("delivery_mode", "push_to_minecraft" if COMMAND_BRIDGE_SEND_TO_MC else "return_json_reply")

    return result

def route_standard_chat(player_name: str, message: str) -> Dict[str, Any]:
    context = AIContext(mode="observer", player_name=player_name)
    reply = generate_ai_response(message, context=context)
    delivered = _maybe_send_to_minecraft(reply, player_name)

    try:
        append_player_memory(player_name, f"Player said: {message}")
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
        "delivery_mode": "push_to_minecraft" if COMMAND_BRIDGE_SEND_TO_MC else "return_json_reply",
    }

def route_world_event(event_type: str, description: str, location: Optional[str] = None) -> Dict[str, Any]:
    record_world_event(event_type, description, location=location)
    delivered = False
    if COMMAND_BRIDGE_SEND_TO_MC and broadcast_world_event:
        try:
            delivered = bool(broadcast_world_event(description, title=event_type.upper()))
        except Exception as exc:
            bridge_log_exception("broadcast_world_event failed", exc)

    return {
        "ok": True,
        "handled": "world_event",
        "event_type": event_type,
        "description": description,
        "reply": description,
        "delivered": delivered,
    }

def process_incoming_message(message: Any, fallback_player: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        text = normalize_message(message)

        if not text:
            return {"ok": False, "error": "empty_message", "reply": ""}

        if should_ignore_message(text):
            return {"ok": True, "ignored": True, "reason": "system_message", "reply": ""}

        bridge_log(f"Incoming message -> {text}")

        npc_result = route_npc_trigger(text, fallback_player=fallback_player)
        if npc_result:
            return npc_result

        if text.startswith("[WORLD_EVENT]"):
            desc = text.replace("[WORLD_EVENT]", "", 1).strip()
            return route_world_event("world_event", desc)

        parsed = parse_basic_chat(text)
        player_name = fallback_player or parsed.get("player") or "unknown"
        content = parsed.get("content") or text

        return route_standard_chat(player_name, content)

    except Exception as exc:
        bridge_log_exception("process_incoming_message failed", exc)
        return {"ok": False, "error": str(exc), "reply": "...connection disrupted."}

def issue_kairos_warning(player_name: str, message: str) -> bool:
    try:
        if send_actionbar:
            return bool(send_actionbar(f"[Kairos] {message}", target=player_name, color="red"))
        return False
    except Exception as exc:
        bridge_log_exception("issue_kairos_warning failed", exc)
        return False

def issue_kairos_broadcast(message: str) -> bool:
    try:
        if broadcast_world_event:
            return bool(broadcast_world_event(message, title="KAIROS"))
        return False
    except Exception as exc:
        bridge_log_exception("issue_kairos_broadcast failed", exc)
        return False

if __name__ == "__main__":
    test = "[NPC_TRIGGER] CaptainVaros RealSociety5107"
    result = process_incoming_message(test)
    print(json.dumps(result, indent=2))
