# ============================================================
# KAIROS DISCORD BOT — MODULAR ECOSYSTEM VERSION
# ============================================================

import os
import asyncio
import time
import threading
from collections import OrderedDict

import discord
import requests
from flask import Flask, request, jsonify

# ============================================================
# CORE CONFIG
# ============================================================

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()

KAIROS_API_URL = os.getenv(
    "KAIROS_API_URL",
    "https://kairos-ai-server.onrender.com/chat"
).strip()

DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0

PORT = int(os.getenv("PORT", "10000"))
REQUEST_TIMEOUT = int(os.getenv("KAIROS_REQUEST_TIMEOUT", "35"))
DISCORD_CHUNK_LIMIT = int(os.getenv("DISCORD_CHUNK_LIMIT", "1850"))

# ============================================================
# MODULAR ECOSYSTEM FLAGS
# ============================================================

KAIROS_ECOSYSTEM_VERSION = "kairos_modular_v1"

ENABLE_AI_CORE = True
ENABLE_NPC_ENGINE = True
ENABLE_MEMORY_ENGINE = True
ENABLE_COMMAND_BRIDGE = True
ENABLE_MC_CONNECTOR = True

# ============================================================
# DISCORD SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
http_app = Flask(__name__)

processed_ids = OrderedDict()

# ============================================================
# LOGGING
# ============================================================

def log(message):
    print(f"[Kairos Discord Bridge] {message}", flush=True)

# ============================================================
# UTILITIES
# ============================================================

def split_text(text, limit=DISCORD_CHUNK_LIMIT):
    text = str(text or "").strip()

    if not text:
        return []

    if len(text) <= limit:
        return [text]

    chunks = []

    while len(text) > limit:
        split_at = text.rfind(" ", 0, limit)

        if split_at <= 0:
            split_at = limit

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        chunks.append(text)

    return chunks

# ============================================================
# DUPLICATE PROTECTION
# ============================================================

def already_processed(message):
    now = time.time()

    for key, ts in list(processed_ids.items()):
        if now - ts > 12:
            processed_ids.pop(key, None)

    mid = str(message.id)

    if mid in processed_ids:
        return True

    processed_ids[mid] = now
    return False

# ============================================================
# TRIGGER DETECTION
# ============================================================

def is_kairos_trigger(message):
    content = (message.content or "").strip().lower()

    if client.user and client.user.mentioned_in(message):
        return True

    return content.startswith((
        "kairos",
        "!kairos",
        "/kairos",
        "hey kairos",
        "yo kairos",
        "kiros",
        "kyros",
    ))

def clean_trigger_text(message):
    content = (message.content or "").strip()

    if client.user:
        content = (
            content
            .replace(f"<@{client.user.id}>", "")
            .replace(f"<@!{client.user.id}>", "")
            .strip()
        )

    prefixes = (
        "!kairos",
        "/kairos",
        "hey kairos",
        "yo kairos",
        "kiros",
        "kyros",
        "kairos",
    )

    lower = content.lower()

    for prefix in prefixes:
        if lower.startswith(prefix):
            content = content[len(prefix):].strip()
            break

    return content or "Speak."

# ============================================================
# MODULAR REQUEST ROUTING
# ============================================================

def post_to_kairos(message, text, triggered):

    payload = {
        "player": message.author.display_name,
        "username": message.author.display_name,
        "message": text,
        "content": text,

        # Platform Metadata
        "source": "discord",
        "platform": "discord",

        # Ecosystem Metadata
        "ecosystem_version": KAIROS_ECOSYSTEM_VERSION,
        "route": "discord_chat",
        "target_system": "ai_core",

        # System Awareness
        "ai_core_enabled": ENABLE_AI_CORE,
        "npc_engine_enabled": ENABLE_NPC_ENGINE,
        "memory_engine_enabled": ENABLE_MEMORY_ENGINE,
        "command_bridge_enabled": ENABLE_COMMAND_BRIDGE,
        "mc_connector_enabled": ENABLE_MC_CONNECTOR,

        # Discord Metadata
        "message_id": str(message.id),
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id),

        # Behavior
        "reply_allowed": bool(triggered),
        "discord_reply_allowed": bool(triggered),

        # Future Routing Support
        "bridge_type": "modular_ecosystem",
    }

    response = requests.post(
        KAIROS_API_URL,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")

    try:
        data = response.json()
    except Exception:
        return {"reply": ""}

    return data

# ============================================================
# CHANNEL HELPERS
# ============================================================

async def get_target_channel():
    if not DISCORD_CHANNEL_ID:
        return None

    channel = client.get_channel(DISCORD_CHANNEL_ID)

    if channel:
        return channel

    try:
        return await client.fetch_channel(DISCORD_CHANNEL_ID)
    except Exception as exc:
        log(f"Channel fetch failed: {exc}")
        return None

# ============================================================
# DISCORD EVENTS
# ============================================================

@client.event
async def on_ready():

    log("=" * 72)
    log(f"ONLINE AS {client.user}")
    log(f"KAIROS API = {KAIROS_API_URL}")

    # ========================================================
    # MODULAR ECOSYSTEM STARTUP LOGS
    # ========================================================

    log("AI Core bridge armed.")
    log("NPC Engine bridge armed.")
    log("Memory Engine bridge armed.")
    log("Command Bridge armed.")
    log("MC Connector armed.")

    log(f"Ecosystem Version = {KAIROS_ECOSYSTEM_VERSION}")

    log("=" * 72)

@client.event
async def on_message(message):

    if message.author.bot:
        return

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return

    if already_processed(message):
        return

    triggered = is_kairos_trigger(message)

    user_text = (
        clean_trigger_text(message)
        if triggered
        else message.content.strip()
    )

    try:

        data = await asyncio.to_thread(
            post_to_kairos,
            message,
            user_text,
            triggered
        )

        # ====================================================
        # SYSTEM FAILURE PROTECTION
        # ====================================================

        if data.get("ok") is False:

            system_name = data.get("system", "unknown_system")
            error_message = data.get("error", "unknown_error")

            log(f"SYSTEM FAILURE: {system_name} -> {error_message}")

            if triggered:
                await message.channel.send(
                    f"**[Kairos]** {system_name} connection unstable."
                )

            return

        reply = str(data.get("reply") or "").strip()

        if triggered and reply:

            for chunk in split_text(reply):
                await message.channel.send(
                    f"**[Kairos]** {chunk}"
                )
                await asyncio.sleep(0.35)

    except Exception as exc:

        log(f"Discord -> Kairos ERROR: {exc}")

        if triggered:
            try:
                await message.channel.send(
                    "**[Kairos]** ...connection disrupted."
                )
            except Exception:
                pass

# ============================================================
# HEALTH ROUTES
# ============================================================

@http_app.route("/", methods=["GET"])
def health():

    return jsonify({
        "ok": True,
        "service": "kairos_discord_bridge",

        "ecosystem": {
            "version": KAIROS_ECOSYSTEM_VERSION,
            "ai_core": ENABLE_AI_CORE,
            "npc_engine": ENABLE_NPC_ENGINE,
            "memory_engine": ENABLE_MEMORY_ENGINE,
            "command_bridge": ENABLE_COMMAND_BRIDGE,
            "mc_connector": ENABLE_MC_CONNECTOR,
        },

        "discord_ready": client.is_ready(),
    })

# ============================================================
# MC -> DISCORD BRIDGE
# ============================================================

@http_app.route("/mc_to_discord", methods=["POST"])
def mc_to_discord():

    try:

        data = request.get_json(silent=True) or {}

        player = str(
            data.get("player")
            or "Minecraft"
        ).strip()

        message = str(
            data.get("message")
            or ""
        ).strip()

        if not message:
            return jsonify({
                "ok": False,
                "error": "missing_message"
            }), 400

        formatted = f"**[Minecraft] {player}:** {message}"

        if not client.is_ready():
            return jsonify({
                "ok": False,
                "error": "discord_not_ready"
            }), 503

        future = asyncio.run_coroutine_threadsafe(
            send_to_discord_channel(formatted),
            client.loop
        )

        delivered = future.result(timeout=10)

        return jsonify({
            "ok": bool(delivered)
        })

    except Exception as exc:

        log(f"MC -> Discord ERROR: {exc}")

        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500

# ============================================================
# DISCORD SENDER
# ============================================================

async def send_to_discord_channel(text):

    channel = await get_target_channel()

    if channel is None:
        return False

    for chunk in split_text(text):
        await channel.send(chunk)
        await asyncio.sleep(0.15)

    return True

# ============================================================
# HTTP SERVER
# ============================================================

def run_http_server():

    http_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )

# ============================================================
# BOOT
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=run_http_server,
        daemon=True
    ).start()

    client.run(DISCORD_TOKEN)
