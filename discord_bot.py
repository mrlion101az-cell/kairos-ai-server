import os
import asyncio
import time
import threading
from collections import OrderedDict

import discord
import requests
from flask import Flask, request, jsonify

# ============================================================
# KAIROS DISCORD BOT — FULL BRIDGE VERSION
# Replace your entire Discord bot file with this.
#
# Provides:
#   Discord -> Kairos app.py /chat
#   Minecraft/app.py -> Discord through POST /mc_to_discord
#
# Required Render env vars:
#   DISCORD_TOKEN or DISCORD_BOT_TOKEN
#   DISCORD_CHANNEL_ID
#   KAIROS_API_URL=https://kairos-ai-server.onrender.com/chat
#
# Optional:
#   PORT=10000
#   MC_TO_DISCORD_TOKEN=shared secret if you want auth
# ============================================================

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
KAIROS_API_URL = os.getenv("KAIROS_API_URL", "https://kairos-ai-server.onrender.com/chat").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0

PORT = int(os.getenv("PORT", "10000"))
REQUEST_TIMEOUT = int(os.getenv("KAIROS_REQUEST_TIMEOUT", "35"))
DEDUP_SECONDS = float(os.getenv("KAIROS_DISCORD_DEDUPE_SECONDS", "12"))
DISCORD_CHUNK_LIMIT = int(os.getenv("DISCORD_CHUNK_LIMIT", "1850"))
MC_TO_DISCORD_TOKEN = (os.getenv("MC_TO_DISCORD_TOKEN") or "").strip()

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN or DISCORD_BOT_TOKEN is missing.")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
http_app = Flask(__name__)

processed_ids = OrderedDict()
processed_fps = OrderedDict()


def log(message):
    print(f"[Kairos Discord Bridge] {message}", flush=True)


def split_text(text, limit=DISCORD_CHUNK_LIMIT):
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < int(limit * 0.4):
            cut = rest.rfind(". ", 0, limit)
        if cut < int(limit * 0.4):
            cut = rest.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()

    if rest:
        chunks.append(rest)
    return chunks


def cleanup_dedupe():
    cutoff = time.time() - DEDUP_SECONDS
    for store in (processed_ids, processed_fps):
        for key, ts in list(store.items()):
            if ts < cutoff:
                store.pop(key, None)
        while len(store) > 700:
            store.popitem(last=False)


def already_processed(message):
    cleanup_dedupe()
    mid = str(message.id)
    fp = f"{message.author.id}:{message.channel.id}:{message.content.strip().lower()}"
    if mid in processed_ids or fp in processed_fps:
        return True
    processed_ids[mid] = time.time()
    processed_fps[fp] = time.time()
    return False


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
        "ok kairos",
        "okay kairos",
        "kairus",
        "kaiross",
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

    lower = content.lower()
    for prefix in (
        "!kairos",
        "/kairos",
        "hey kairos",
        "yo kairos",
        "ok kairos",
        "okay kairos",
        "kaiross",
        "kairus",
        "kiros",
        "kyros",
        "kairos",
    ):
        if lower.startswith(prefix):
            content = content[len(prefix):].strip()
            break

    return content or "Speak."


def post_to_kairos(message, text, triggered):
    payload = {
        "player": message.author.display_name,
        "message": text,
        "source": "discord",
        "message_id": str(message.id),
        "discord_message_id": str(message.id),
        "platform_user_id": str(message.author.id),
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id),
        "reply_allowed": bool(triggered),
        "discord_reply_allowed": bool(triggered),
        "bridge_only": not bool(triggered),
    }

    response = requests.post(KAIROS_API_URL, json=payload, timeout=REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise RuntimeError(f"Kairos API HTTP {response.status_code}: {response.text[:500]}")

    try:
        return response.json()
    except Exception:
        return {"reply": ""}


async def get_target_channel():
    if not DISCORD_CHANNEL_ID:
        return None

    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        return channel

    try:
        return await client.fetch_channel(DISCORD_CHANNEL_ID)
    except Exception as exc:
        log(f"Could not fetch DISCORD_CHANNEL_ID={DISCORD_CHANNEL_ID}: {exc}")
        return None


async def send_to_discord_channel(text):
    text = str(text or "").strip()
    if not text:
        return False

    channel = await get_target_channel()
    if channel is None:
        log("No Discord target channel found. Check DISCORD_CHANNEL_ID.")
        return False

    for chunk in split_text(text):
        await channel.send(chunk)
        await asyncio.sleep(0.15)

    return True


@client.event
async def on_ready():
    log("=" * 72)
    log(f"Online as {client.user}")
    log(f"KAIROS_API_URL={KAIROS_API_URL}")
    log(f"CHANNEL_LOCK={DISCORD_CHANNEL_ID if DISCORD_CHANNEL_ID else 'ALL'}")
    log("HTTP endpoint active: POST /mc_to_discord")
    log("=" * 72)


@client.event
async def on_message(message):
    if message.author.bot:
        return

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return

    if not message.content or not message.content.strip():
        return

    if already_processed(message):
        return

    triggered = is_kairos_trigger(message)
    user_text = clean_trigger_text(message) if triggered else message.content.strip()

    try:
        data = await asyncio.to_thread(post_to_kairos, message, user_text, triggered)

        if data.get("duplicate"):
            return

        reply = str(data.get("reply") or "").strip()

        # Discord only gets Kairos reply when directly triggered.
        # Normal Discord messages still travel to Minecraft through app.py.
        if triggered and reply:
            async with message.channel.typing():
                for chunk in split_text(reply):
                    await message.channel.send(f"**[Kairos]** {chunk}")
                    await asyncio.sleep(0.35)

    except Exception as exc:
        log(f"Discord -> Kairos ERROR: {exc}")
        if triggered:
            try:
                await message.channel.send("**[Kairos]** ...connection disrupted.")
            except Exception:
                pass


@http_app.route("/", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "kairos-discord-bridge",
        "discord_ready": client.is_ready(),
        "channel_id": DISCORD_CHANNEL_ID,
        "routes": ["/", "/mc_to_discord"],
    })


@http_app.route("/mc_to_discord", methods=["GET"])
def mc_to_discord_get():
    return jsonify({
        "ok": True,
        "endpoint": "/mc_to_discord",
        "method": "POST",
        "example": {"player": "RealSociety5107", "message": "hello"},
    })


@http_app.route("/mc_to_discord", methods=["POST"])
def mc_to_discord():
    try:
        data = request.get_json(silent=True) or {}

        if MC_TO_DISCORD_TOKEN:
            supplied_header = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
            supplied_body = str(data.get("token", "")).strip()

            if supplied_header != MC_TO_DISCORD_TOKEN and supplied_body != MC_TO_DISCORD_TOKEN:
                return jsonify({"ok": False, "error": "unauthorized"}), 401

        player = str(data.get("player") or data.get("username") or data.get("name") or "Minecraft").strip()
        message = str(data.get("message") or data.get("content") or "").strip()

# ----------------------------------------
# BLOCK ALL KAIROS SYSTEM MESSAGES (FINAL FIX)
# ----------------------------------------
# BLOCK ALL KAIROS SYSTEM MESSAGES
if message.strip().startswith("[Kairos]"):
    return jsonify({"ok": True, "blocked": True}), 200

# NORMAL VALIDATION (must NOT be nested)
if not message:
    return jsonify({"ok": False, "error": "missing message"}), 400

        safe_player = player.replace("@", "@\u200b")
        safe_message = message.replace("@", "@\u200b")

        formatted = f"**[Minecraft] {safe_player}:** {safe_message}"

        if not client.is_ready():
            log("Minecraft message received before Discord client was ready.")
            return jsonify({"ok": False, "error": "discord client not ready"}), 503

        future = asyncio.run_coroutine_threadsafe(send_to_discord_channel(formatted), client.loop)
        delivered = future.result(timeout=10)

        if delivered:
            log(f"Minecraft -> Discord delivered for {player}.")
            return jsonify({"ok": True, "delivered": True}), 200

        return jsonify({"ok": False, "delivered": False, "error": "channel unavailable"}), 500

    except Exception as exc:
        log(f"Minecraft -> Discord ERROR: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


def run_http_server():
    http_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    client.run(DISCORD_TOKEN)
