# ============================================================
# KAIROS MODULAR ORCHESTRATOR
# app.py
# ============================================================

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, request

try:
    from command_bridge import process_incoming_message
except Exception as e:
    process_incoming_message = None
    print(f"[APP ERROR] command_bridge import failed: {e}", flush=True)

try:
    from memory_engine import record_world_event, append_player_memory, ensure_memory_dirs
    ensure_memory_dirs()
    MEMORY_ENGINE_ONLINE = True
except Exception as e:
    MEMORY_ENGINE_ONLINE = False
    record_world_event = None
    append_player_memory = None
    print(f"[APP ERROR] memory_engine import failed: {e}", flush=True)

app = Flask(__name__)
PORT = int(os.getenv("PORT", "10000"))
KAIROS_VERSION = "kairos_modular_v1"

def log(message: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[KAIROS APP {timestamp}] {message}", flush=True)

def extract_payload(data):
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
    source = str(data.get("source") or data.get("platform") or "minecraft").strip()
    return player, message, source

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "kairos_modular_orchestrator",
        "version": KAIROS_VERSION,
        "systems": {
            "command_bridge": process_incoming_message is not None,
            "memory_engine": MEMORY_ENGINE_ONLINE,
        },
    })

@app.route("/chat", methods=["GET"])
def chat_get():
    return jsonify({
        "ok": True,
        "endpoint": "/chat",
        "method": "POST",
        "accepted_fields": ["player", "username", "name", "sender", "message", "content", "text", "chat", "msg"],
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
                "accepted_fields": ["message", "content", "text", "chat", "msg"],
                "received_keys": list(data.keys()),
                "reply": "",
            }), 200

        log(f"Incoming message from {source}::{player} -> {message}")

        try:
            if append_player_memory:
                append_player_memory(player, f"{source}: {message}")
            if record_world_event:
                record_world_event(
                    "player_message",
                    message,
                    location=source,
                    faction=None,
                    metadata={"player": player, "source": source},
                )
        except Exception as memory_error:
            log(f"Memory Engine Error: {memory_error}")

        if process_incoming_message is None:
            return jsonify({
                "ok": False,
                "system": "command_bridge",
                "error": "offline",
                "reply": "...connection disrupted.",
            }), 200

        response = process_incoming_message(message, fallback_player=player)

        if not isinstance(response, dict):
            response = {"ok": True, "reply": str(response)}

        reply = str(response.get("reply") or response.get("message") or response.get("text") or response.get("response") or "").strip()
        if reply:
            response["reply"] = reply
            response["message"] = reply
            response["text"] = reply
            response["response"] = reply

        response.setdefault("ok", True)
        response.setdefault("player", player)
        response.setdefault("source", source)
        return jsonify(response), 200

    except Exception as e:
        traceback.print_exc()
        log(f"APP ROUTE FAILURE: {e}")
        return jsonify({
            "ok": False,
            "system": "app_orchestrator",
            "error": str(e),
            "reply": "...connection disrupted.",
        }), 200

@app.route("/npc", methods=["POST"])
def npc_route():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = request.form.to_dict() if request.form else {}

        player, message, source = extract_payload(data)
        npc_name = str(data.get("npc_name") or data.get("npc") or "").strip()
        if not message and npc_name:
            message = f"[NPC_TRIGGER] {npc_name} {player}"

        if process_incoming_message is None:
            return jsonify({
                "ok": False,
                "system": "command_bridge",
                "error": "offline",
                "reply": "...NPC routing disrupted.",
            }), 200

        response = process_incoming_message(message, fallback_player=player)
        return jsonify(response), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "system": "npc_route",
            "error": str(e),
            "reply": "...NPC route disrupted.",
        }), 200

@app.route("/world_event", methods=["POST"])
def world_event():
    try:
        data = request.get_json(silent=True) or {}
        event_type = str(data.get("event_type", "unknown"))
        description = str(data.get("description", "")).strip()
        log(f"World Event Triggered: {event_type}")

        if process_incoming_message is None:
            return jsonify({"ok": False, "system": "command_bridge", "error": "offline"}), 200

        response = process_incoming_message(f"[WORLD_EVENT] {event_type}: {description}", fallback_player="WORLD")
        return jsonify(response), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "system": "world_event_route", "error": str(e)}), 200

if __name__ == "__main__":
    log("=" * 72)
    log("KAIROS MODULAR ORCHESTRATOR BOOTING")
    log(f"Version: {KAIROS_VERSION}")
    log("Subsystem Status:")
    log(f" - Command Bridge: {'ONLINE' if process_incoming_message else 'OFFLINE'}")
    log(f" - Memory Engine: {'ONLINE' if MEMORY_ENGINE_ONLINE else 'OFFLINE'}")
    log("=" * 72)
    app.run(host="0.0.0.0", port=PORT, debug=False)
