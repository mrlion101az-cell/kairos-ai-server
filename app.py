# ============================================================
# KAIROS MODULAR ORCHESTRATOR
# app.py
# ============================================================
"""
Kairos / Nexus Modular Orchestrator

Purpose:
- Flask HTTP gateway for Kairos.
- Keeps app.py thin and safe.
- Imports Command Bridge for NPC / Discord / fallback routing.
- Imports Director Engine for Minecraft chat and world-event decisions.
- Preserves memory logging and old behavior as fallback.
- Does NOT directly run background loops.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request


# ============================================================
# COMMAND BRIDGE IMPORT
# ============================================================

try:
    from command_bridge import process_incoming_message
except Exception as e:
    process_incoming_message = None
    print(f"[APP ERROR] command_bridge import failed: {e}", flush=True)


# ============================================================
# DIRECTOR ENGINE IMPORT
# ============================================================

try:
    from director_engine import (
        direct_minecraft_chat,
        direct_world_event,
        direct_player_kill,
        direct_grief_block,
        tick_director,
    )
    DIRECTOR_ENGINE_ONLINE = True
except Exception as e:
    direct_minecraft_chat = None
    direct_world_event = None
    direct_player_kill = None
    direct_grief_block = None
    tick_director = None
    DIRECTOR_ENGINE_ONLINE = False
    print(f"[APP ERROR] director_engine import failed: {e}", flush=True)


# ============================================================
# MEMORY ENGINE IMPORT
# ============================================================

try:
    from memory_engine import record_world_event, append_player_memory, ensure_memory_dirs
    ensure_memory_dirs()
    MEMORY_ENGINE_ONLINE = True
except Exception as e:
    MEMORY_ENGINE_ONLINE = False
    record_world_event = None
    append_player_memory = None
    print(f"[APP ERROR] memory_engine import failed: {e}", flush=True)


# ============================================================
# APP CONFIG
# ============================================================

app = Flask(__name__)

PORT = int(os.getenv("PORT", "10000"))
KAIROS_VERSION = os.getenv("KAIROS_VERSION", "kairos_modular_v2_director")

# Controls whether Minecraft chat enters Director first.
APP_USE_DIRECTOR_FOR_MINECRAFT_CHAT = os.getenv(
    "APP_USE_DIRECTOR_FOR_MINECRAFT_CHAT",
    "true",
).lower() == "true"

# Controls whether world_event enters Director first.
APP_USE_DIRECTOR_FOR_WORLD_EVENTS = os.getenv(
    "APP_USE_DIRECTOR_FOR_WORLD_EVENTS",
    "true",
).lower() == "true"

# Discord must remain unchanged unless explicitly changed later.
APP_DISCORD_USES_COMMAND_BRIDGE = os.getenv(
    "APP_DISCORD_USES_COMMAND_BRIDGE",
    "true",
).lower() == "true"


# ============================================================
# LOGGING
# ============================================================

def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[KAIROS APP {timestamp}] {message}", flush=True)


# ============================================================
# PAYLOAD HELPERS
# ============================================================

def extract_payload(data: Optional[Dict[str, Any]]):
    data = data or {}

    player = str(
        data.get("player")
        or data.get("username")
        or data.get("name")
        or data.get("sender")
        or data.get("user")
        or "unknown"
    ).strip()

    message = str(
        data.get("message")
        or data.get("content")
        or data.get("text")
        or data.get("chat")
        or data.get("msg")
        or ""
    ).strip()

    source = str(
        data.get("source")
        or data.get("platform")
        or "minecraft"
    ).strip().lower()

    return player, message, source


def normalize_response(response: Any, player: str, source: str) -> Dict[str, Any]:
    if not isinstance(response, dict):
        response = {
            "ok": True,
            "reply": str(response or ""),
        }

    reply = str(
        response.get("reply")
        or response.get("message")
        or response.get("text")
        or response.get("response")
        or ""
    ).strip()

    if reply:
        response["reply"] = reply
        response["message"] = reply
        response["text"] = reply
        response["response"] = reply
    else:
        response.setdefault("reply", "")

    response.setdefault("ok", True)
    response.setdefault("player", player)
    response.setdefault("source", source)

    return response


def call_command_bridge(
    message: str,
    player: str,
    source: str = "minecraft",
) -> Dict[str, Any]:
    """
    Calls command_bridge safely.

    Newer command_bridge.py versions may accept source=source.
    Older versions may not. This helper supports both.
    """
    if process_incoming_message is None:
        return {
            "ok": False,
            "system": "command_bridge",
            "error": "offline",
            "reply": "...connection disrupted.",
        }

    try:
        response = process_incoming_message(
            message,
            fallback_player=player,
            source=source,
        )
    except TypeError:
        response = process_incoming_message(
            message,
            fallback_player=player,
        )

    return normalize_response(response, player, source)


def record_incoming_message(player: str, message: str, source: str) -> None:
    try:
        if append_player_memory:
            append_player_memory(player, f"{source}: {message}")

        if record_world_event:
            record_world_event(
                "player_message",
                message,
                location=source,
                faction=None,
                metadata={
                    "player": player,
                    "source": source,
                },
            )

    except Exception as memory_error:
        log(f"Memory Engine Error: {memory_error}")


# ============================================================
# HEALTH
# ============================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "kairos_modular_orchestrator",
        "version": KAIROS_VERSION,
        "systems": {
            "director_engine": DIRECTOR_ENGINE_ONLINE,
            "command_bridge": process_incoming_message is not None,
            "memory_engine": MEMORY_ENGINE_ONLINE,
        },
        "routing": {
            "minecraft_chat_director": APP_USE_DIRECTOR_FOR_MINECRAFT_CHAT,
            "world_event_director": APP_USE_DIRECTOR_FOR_WORLD_EVENTS,
            "discord_command_bridge": APP_DISCORD_USES_COMMAND_BRIDGE,
        },
    })


@app.route("/systems", methods=["GET"])
def systems():
    return jsonify({
        "ok": True,
        "version": KAIROS_VERSION,
        "director_engine": {
            "online": DIRECTOR_ENGINE_ONLINE,
            "direct_minecraft_chat": direct_minecraft_chat is not None,
            "direct_world_event": direct_world_event is not None,
            "direct_player_kill": direct_player_kill is not None,
            "direct_grief_block": direct_grief_block is not None,
            "tick_director": tick_director is not None,
        },
        "command_bridge": {
            "online": process_incoming_message is not None,
        },
        "memory_engine": {
            "online": MEMORY_ENGINE_ONLINE,
        },
    })


# ============================================================
# CHAT ROUTE
# ============================================================

@app.route("/chat", methods=["GET"])
def chat_get():
    return jsonify({
        "ok": True,
        "endpoint": "/chat",
        "method": "POST",
        "accepted_fields": [
            "player",
            "username",
            "name",
            "sender",
            "user",
            "message",
            "content",
            "text",
            "chat",
            "msg",
            "source",
            "platform",
        ],
    })


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = request.form.to_dict() if request.form else {}

        player, message, source = extract_payload(data)

        if not message:
            log(f"Rejected /chat missing message. Payload keys={list(data.keys())}")
            return jsonify({
                "ok": False,
                "error": "missing_message",
                "accepted_fields": [
                    "message",
                    "content",
                    "text",
                    "chat",
                    "msg",
                ],
                "received_keys": list(data.keys()),
                "reply": "",
            }), 200

        log(f"Incoming message from {source}::{player} -> {message}")

        record_incoming_message(player, message, source)

        # ----------------------------------------------------
        # Discord stays on Command Bridge.
        # This preserves the existing Discord personality and behavior.
        # ----------------------------------------------------
        if source == "discord" and APP_DISCORD_USES_COMMAND_BRIDGE:
            response = call_command_bridge(
                message=message,
                player=player,
                source=source,
            )
            return jsonify(normalize_response(response, player, source)), 200

        # ----------------------------------------------------
        # NPC traffic should also stay on Command Bridge.
        # NPC-specific endpoint exists below, but this protects route metadata too.
        # ----------------------------------------------------
        if source in {"npc", "citizens", "citizenscmd"}:
            response = call_command_bridge(
                message=message,
                player=player,
                source=source,
            )
            return jsonify(normalize_response(response, player, source)), 200

        # ----------------------------------------------------
        # Minecraft chat goes to Director first when available.
        # Director can observe, raise threat, call War Engine, or stay silent.
        # If Director is offline, fall back to Command Bridge.
        # ----------------------------------------------------
        if (
            source == "minecraft"
            and APP_USE_DIRECTOR_FOR_MINECRAFT_CHAT
            and direct_minecraft_chat is not None
        ):
            response = direct_minecraft_chat(
                player=player,
                message=message,
                location=source,
                metadata={
                    "route": "/chat",
                    "source": source,
                    "payload_keys": list(data.keys()),
                },
            )
            return jsonify(normalize_response(response, player, source)), 200

        # ----------------------------------------------------
        # Fallback: Command Bridge.
        # ----------------------------------------------------
        response = call_command_bridge(
            message=message,
            player=player,
            source=source,
        )

        return jsonify(normalize_response(response, player, source)), 200

    except Exception as e:
        traceback.print_exc()
        log(f"APP ROUTE FAILURE: {e}")
        return jsonify({
            "ok": False,
            "system": "app_orchestrator",
            "error": str(e),
            "reply": "...connection disrupted.",
        }), 200


# ============================================================
# NPC ROUTE
# ============================================================

@app.route("/npc", methods=["POST"])
def npc_route():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = request.form.to_dict() if request.form else {}

        player, message, source = extract_payload(data)

        npc_name = str(
            data.get("npc_name")
            or data.get("npc")
            or ""
        ).strip()

        if not message and npc_name:
            message = f"[NPC_TRIGGER] {npc_name} {player}"

        if not message:
            return jsonify({
                "ok": False,
                "system": "npc_route",
                "error": "missing_message_or_npc_name",
                "reply": "...NPC route disrupted.",
            }), 200

        # NPC stays with Command Bridge/NPC Engine by design.
        response = call_command_bridge(
            message=message,
            player=player,
            source="npc",
        )

        return jsonify(normalize_response(response, player, "npc")), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "system": "npc_route",
            "error": str(e),
            "reply": "...NPC route disrupted.",
        }), 200


# ============================================================
# WORLD EVENT ROUTE
# ============================================================

@app.route("/world_event", methods=["POST"])
def world_event():
    try:
        data = request.get_json(silent=True) or {}

        event_type = str(
            data.get("event_type")
            or data.get("type")
            or "unknown"
        ).strip()

        description = str(
            data.get("description")
            or data.get("message")
            or data.get("text")
            or ""
        ).strip()

        player = str(
            data.get("player")
            or data.get("username")
            or data.get("name")
            or "WORLD"
        ).strip()

        location = str(
            data.get("location")
            or data.get("region")
            or "world_event"
        ).strip()

        log(f"World Event Triggered: {event_type}")

        world_message = f"[WORLD_EVENT] {event_type}: {description}"

        if (
            APP_USE_DIRECTOR_FOR_WORLD_EVENTS
            and direct_world_event is not None
        ):
            response = direct_world_event(
                event_type=event_type,
                description=description,
                player=player,
                location=location,
                metadata={
                    "route": "/world_event",
                    "payload_keys": list(data.keys()),
                    "raw": data,
                },
            )
            return jsonify(normalize_response(response, player, "world_event")), 200

        response = call_command_bridge(
            message=world_message,
            player="WORLD",
            source="world_event",
        )

        return jsonify(normalize_response(response, "WORLD", "world_event")), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "system": "world_event_route",
            "error": str(e),
            "reply": "",
        }), 200


# ============================================================
# DIRECT EVENT ROUTES
# Optional future hooks for plugins / scripts.
# ============================================================

@app.route("/player_kill", methods=["POST"])
def player_kill():
    try:
        data = request.get_json(silent=True) or {}

        killer = str(data.get("killer") or data.get("player") or "unknown").strip()
        victim = str(data.get("victim") or data.get("target") or "unknown").strip()
        location = str(data.get("location") or data.get("region") or "").strip() or None

        if direct_player_kill:
            response = direct_player_kill(
                killer=killer,
                victim=victim,
                location=location,
                metadata={
                    "route": "/player_kill",
                    "raw": data,
                },
            )
        else:
            response = call_command_bridge(
                message=f"[WORLD_EVENT] player_kill: {killer} killed {victim}",
                player=killer,
                source="world_event",
            )

        return jsonify(normalize_response(response, killer, "minecraft")), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "system": "player_kill_route",
            "error": str(e),
            "reply": "",
        }), 200


@app.route("/grief_block", methods=["POST"])
def grief_block():
    try:
        data = request.get_json(silent=True) or {}

        player = str(data.get("player") or data.get("username") or "unknown").strip()
        block = str(data.get("block") or data.get("material") or "unknown").strip()
        location = str(data.get("location") or data.get("region") or "").strip() or None

        if direct_grief_block:
            response = direct_grief_block(
                player=player,
                block=block,
                location=location,
                metadata={
                    "route": "/grief_block",
                    "raw": data,
                },
            )
        else:
            response = call_command_bridge(
                message=f"[WORLD_EVENT] grief_block: {player} placed {block}",
                player=player,
                source="world_event",
            )

        return jsonify(normalize_response(response, player, "minecraft")), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "system": "grief_block_route",
            "error": str(e),
            "reply": "",
        }), 200


@app.route("/director_tick", methods=["POST", "GET"])
def director_tick():
    try:
        if tick_director is None:
            return jsonify({
                "ok": False,
                "system": "director_engine",
                "error": "offline",
                "reply": "",
            }), 200

        data = request.get_json(silent=True) if request.method == "POST" else {}
        if not isinstance(data, dict):
            data = {}

        location = data.get("location")
        faction = data.get("faction")

        response = tick_director(
            location=location,
            faction=faction,
        )

        return jsonify(normalize_response(response, "WORLD", "director")), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "system": "director_tick_route",
            "error": str(e),
            "reply": "",
        }), 200


# ============================================================
# BOOT
# ============================================================

if __name__ == "__main__":
    log("=" * 72)
    log("KAIROS MODULAR ORCHESTRATOR BOOTING")
    log(f"Version: {KAIROS_VERSION}")
    log("Subsystem Status:")
    log(f" - Director Engine : {'ONLINE' if DIRECTOR_ENGINE_ONLINE else 'OFFLINE'}")
    log(f" - Command Bridge  : {'ONLINE' if process_incoming_message else 'OFFLINE'}")
    log(f" - Memory Engine   : {'ONLINE' if MEMORY_ENGINE_ONLINE else 'OFFLINE'}")
    log("Routing:")
    log(f" - Minecraft Chat -> Director : {APP_USE_DIRECTOR_FOR_MINECRAFT_CHAT}")
    log(f" - World Events   -> Director : {APP_USE_DIRECTOR_FOR_WORLD_EVENTS}")
    log(f" - Discord        -> Command Bridge : {APP_DISCORD_USES_COMMAND_BRIDGE}")
    log("=" * 72)

    app.run(host="0.0.0.0", port=PORT, debug=False)
