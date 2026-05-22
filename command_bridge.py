"""
command_bridge.py
Kairos / Nexus Command Bridge
FULL NPC ENGINE INTEGRATION + CHUNKED NPC DIALOGUE VERSION
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import traceback
from typing import Any, Dict, List, Optional

from ai_core import AIContext, generate_ai_response

from memory_engine import (
    append_player_memory,
    record_npc_interaction,
    record_system_error,
)

try:
    from npc_engine import handle_npc_trigger_message
except Exception as exc:
    handle_npc_trigger_message = None
    print(f"[COMMAND_BRIDGE ERROR] npc_engine import failed: {exc}", flush=True)

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

COMMAND_BRIDGE_DEBUG = os.getenv("COMMAND_BRIDGE_DEBUG", "true").lower() == "true"
COMMAND_BRIDGE_SEND_TO_MC = os.getenv("COMMAND_BRIDGE_SEND_TO_MC", "true").lower() == "true"

NPC_DIALOGUE_CHUNK_SIZE = int(os.getenv("NPC_DIALOGUE_CHUNK_SIZE", "230"))
NPC_DIALOGUE_MAX_CHUNKS = int(os.getenv("NPC_DIALOGUE_MAX_CHUNKS", "8"))
NPC_DIALOGUE_COLOR = os.getenv("NPC_DIALOGUE_COLOR", "gold")
NPC_DIALOGUE_HEADER_COLOR = os.getenv("NPC_DIALOGUE_HEADER_COLOR", "yellow")

CHAT_PREFIX_PATTERN = re.compile(r"^\[(.*?)\]\s*(.*)$")
NPC_TRIGGER_PATTERN = re.compile(
    r"^\[?NPC_TRIGGER\]?\s+([A-Za-z0-9_\-]+)(?:\s+(.+))?$",
    re.IGNORECASE,
)

SYSTEM_IGNORE_PATTERNS = [
    "[Server thread/",
    "issued server command",
]


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
    text = str(message or "").lower()
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


def _safe_reply_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _minecraft_json_text(text: str, color: str = "white") -> str:
    return json.dumps({"text": str(text), "color": color}, ensure_ascii=False)


def _tellraw_command(target: str, text: str, color: str = "white") -> str:
    target = str(target or "@a").strip()
    return f"tellraw {target} {_minecraft_json_text(text, color=color)}"


def split_dialogue_into_chunks(text: str, chunk_size: int = NPC_DIALOGUE_CHUNK_SIZE) -> List[str]:
    text = _safe_reply_text(text)
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            chunks.append(paragraph)
            continue
        chunks.extend(textwrap.wrap(paragraph, width=chunk_size, break_long_words=False, break_on_hyphens=False))
    if not chunks:
        chunks = textwrap.wrap(text, width=chunk_size, break_long_words=False, break_on_hyphens=False)
    if len(chunks) > NPC_DIALOGUE_MAX_CHUNKS:
        chunks = chunks[:NPC_DIALOGUE_MAX_CHUNKS]
        chunks[-1] = chunks[-1].rstrip() + " ..."
    return chunks


def _push_to_minecraft(reply: str, player_name: Optional[str]) -> bool:
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


def push_npc_dialogue_to_minecraft(npc_name: str, reply: str, player_name: Optional[str]) -> bool:
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

    display_reply = re.sub(r"^\[?Kairos\]?\s*", "", reply, flags=re.IGNORECASE).strip()
    chunks = split_dialogue_into_chunks(display_reply)
    if not chunks:
        return False

    commands: List[str] = [
        _tellraw_command(player_name, f"--- {npc_name} ---", color=NPC_DIALOGUE_HEADER_COLOR)
    ]
    for chunk in chunks:
        commands.append(_tellraw_command(player_name, chunk, color=NPC_DIALOGUE_COLOR))

    try:
        success = bool(send_minecraft_commands(commands))
        bridge_log(f"NPC dialogue delivered={success} npc={npc_name} player={player_name} chunks={len(chunks)}")
        return success
    except Exception as exc:
        bridge_log_exception("push_npc_dialogue_to_minecraft failed", exc)
        return False


def parse_npc_trigger(message: Any, fallback_player: Optional[str] = None) -> Optional[Dict[str, str]]:
    text = normalize_message(message)
    match = NPC_TRIGGER_PATTERN.match(text)
    if not match:
        return None
    npc_name = str(match.group(1) or "").strip()
    player_name = str(match.group(2) or "").strip()
    if player_name in {"", "<p>", "<player>", "%player%", "{player}", "player", "unknown"}:
        player_name = fallback_player or "traveler"
    return {"npc_name": npc_name, "player": player_name, "raw": text}


def is_npc_trigger(message: Any) -> bool:
    return parse_npc_trigger(message) is not None


def normalize_trigger_for_npc_engine(message: str, fallback_player: Optional[str] = None) -> str:
    parsed = parse_npc_trigger(message, fallback_player=fallback_player)
    if not parsed:
        return str(message or "")
    return f"[NPC_TRIGGER] {parsed['npc_name']} {parsed['player']}"


def route_npc_trigger(message: str, fallback_player: Optional[str] = None) -> Optional[Dict[str, Any]]:
    parsed = parse_npc_trigger(message, fallback_player=fallback_player)
    if not parsed:
        return None
    if handle_npc_trigger_message is None:
        return {"ok": False, "handled": "npc_trigger", "error": "npc_engine_offline", "reply": "...NPC engine offline."}

    npc_name = parsed["npc_name"]
    player_name = parsed["player"]
    bridge_log(f"NPC trigger routed -> npc={npc_name} player={player_name}")

    normalized_message = normalize_trigger_for_npc_engine(message, fallback_player=player_name)

    # Do NOT let npc_engine send directly. We deliver chunked dialogue here.
    result = handle_npc_trigger_message(normalized_message, fallback_player=player_name, send_reply=None)
    if not result:
        return None
    if not isinstance(result, dict):
        result = {"ok": True, "reply": str(result or "")}

    reply = _safe_reply_text(result.get("reply") or result.get("message") or result.get("text") or result.get("response"))
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
    return result


def route_standard_chat(player_name: str, message: str) -> Dict[str, Any]:
    context = AIContext(mode="observer", player_name=player_name)
    reply = generate_ai_response(message, context=context)
    reply = _safe_reply_text(reply)
    delivered = _push_to_minecraft(reply, player_name)
    try:
        append_player_memory(player_name, f"Player said: {message}")
    except Exception as exc:
        bridge_log_exception("append_player_memory failed", exc)
    return {"ok": True, "handled": "standard_chat", "player": player_name, "message": message, "reply": reply, "delivered": delivered}


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

        parsed = parse_basic_chat(text)
        player_name = fallback_player or parsed.get("player") or "unknown"
        content = parsed.get("content") or text
        return route_standard_chat(player_name, content)

    except Exception as exc:
        bridge_log_exception("process_incoming_message failed", exc)
        return {"ok": False, "error": str(exc), "reply": "...connection disrupted."}


if __name__ == "__main__":
    print(process_incoming_message("NPC_TRIGGER CaptainVaros RealSociety5107", fallback_player="RealSociety5107"))
