
# ============================================================
# KAIROS MODULAR ORCHESTRATOR
# app.py
# ============================================================

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, request

# ============================================================
# MODULAR IMPORTS
# ============================================================

try:
    from command_bridge import process_incoming_message
except Exception as e:
    process_incoming_message = None
    print(f"[APP ERROR] command_bridge import failed: {e}", flush=True)

try:
    from memory_engine import (
        record_world_event,
        append_player_memory,
        ensure_memory_dirs
    )

    ensure_memory_dirs()

    MEMORY_ENGINE_ONLINE = True

except Exception as e:

    MEMORY_ENGINE_ONLINE = False

    record_world_event = None
    append_player_memory = None

    print(f"[APP ERROR] memory_engine import failed: {e}", flush=True)

# ============================================================
# APP SETUP
# ============================================================

app = Flask(__name__)

PORT = int(os.getenv("PORT", "10000"))

KAIROS_VERSION = "kairos_modular_v1"

# ============================================================
# LOGGING
# ============================================================

def log(message: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[KAIROS APP {timestamp}] {message}", flush=True)

# ============================================================
# HEALTH ROUTE
# ============================================================

@app.route("/", methods=["GET"])
def health():

    return jsonify({
        "ok": True,
        "service": "kairos_modular_orchestrator",
        "version": KAIROS_VERSION,

        "systems": {
            "command_bridge": process_incoming_message is not None,
            "memory_engine": MEMORY_ENGINE_ONLINE,
        }
    })

# ============================================================
# MAIN CHAT ROUTE
# ============================================================

@app.route("/chat", methods=["POST"])
def chat():

    try:

        data = request.get_json(silent=True) or {}

        player = str(data.get("player", "unknown"))
        message = str(data.get("message", "")).strip()
        source = str(data.get("source", "unknown"))

        if not message:
            return jsonify({
                "ok": False,
                "error": "missing_message"
            }), 400

        log(f"Incoming message from {source}::{player}")

        # ====================================================
        # MEMORY RECORDING
        # ====================================================

        try:

            if append_player_memory:

                append_player_memory(
                    player,
                    f"{source}: {message}"
                )

            if record_world_event:

                record_world_event(
                    "player_message",
                    message,
                    location=source,
                    faction=None,
                    metadata={
                        "player": player
                    }
                )

        except Exception as memory_error:

            log(f"Memory Engine Error: {memory_error}")

        # ====================================================
        # COMMAND BRIDGE ROUTING
        # ====================================================

        if process_incoming_message is None:

            return jsonify({
                "ok": False,
                "system": "command_bridge",
                "error": "offline"
            }), 503

        response = process_incoming_message(data)

        if not isinstance(response, dict):
            response = {
                "ok": True,
                "reply": str(response)
            }

        return jsonify(response)

    except Exception as e:

        traceback.print_exc()

        log(f"APP ROUTE FAILURE: {e}")

        return jsonify({
            "ok": False,
            "system": "app_orchestrator",
            "error": str(e)
        }), 500

# ============================================================
# NPC DIRECT ROUTE
# ============================================================

@app.route("/npc", methods=["POST"])
def npc_route():

    try:

        data = request.get_json(silent=True) or {}

        data["route"] = "npc"

        if process_incoming_message is None:

            return jsonify({
                "ok": False,
                "system": "command_bridge",
                "error": "offline"
            }), 503

        response = process_incoming_message(data)

        return jsonify(response)

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "ok": False,
            "system": "npc_route",
            "error": str(e)
        }), 500

# ============================================================
# WORLD EVENT ROUTE
# ============================================================

@app.route("/world_event", methods=["POST"])
def world_event():

    try:

        data = request.get_json(silent=True) or {}

        event_type = str(data.get("event_type", "unknown"))

        log(f"World Event Triggered: {event_type}")

        if process_incoming_message is None:

            return jsonify({
                "ok": False,
                "system": "command_bridge",
                "error": "offline"
            }), 503

        response = process_incoming_message({
            "route": "world_event",
            "event": data
        })

        return jsonify(response)

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "ok": False,
            "system": "world_event_route",
            "error": str(e)
        }), 500

# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":

    log("=" * 72)
    log("KAIROS MODULAR ORCHESTRATOR BOOTING")
    log(f"Version: {KAIROS_VERSION}")

    log("Subsystem Status:")

    log(f" - Command Bridge: {'ONLINE' if process_incoming_message else 'OFFLINE'}")
    log(f" - Memory Engine: {'ONLINE' if MEMORY_ENGINE_ONLINE else 'OFFLINE'}")

    log("=" * 72)

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )
```
