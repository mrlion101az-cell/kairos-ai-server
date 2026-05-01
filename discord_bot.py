import os
import asyncio
import time
import threading
from collections import OrderedDict

import discord
import requests
from flask import Flask, request, jsonify

# ============================================================
# KAIROS FULL DISCORD BRIDGE BOT
# Replace your old discord_bot.py with this entire file.
# ============================================================

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
KAIROS_API_URL = os.getenv("KAIROS_API_URL", "https://kairos-ai-server.onrender.com/chat").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0
PORT = int(os.getenv("PORT", "10000"))
DEDUP_SECONDS = float(os.getenv("KAIROS_DISCORD_DEDUPE_SECONDS", "12"))
REQUEST_TIMEOUT = int(os.getenv("KAIROS_REQUEST_TIMEOUT", "35"))
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


def log(msg):
    print(f"[Kairos Discord Bridge] {msg}", flush=True)


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
    triggers = (
        "kairos", "!kairos", "/kairos", "hey kairos", "yo kairos",
        "ok kairos", "okay kairos", "kairus", "kaiross", "kiros", "kyros"
    )
    return content.startswith(triggers)


def clean_trigger_text(message):
    content = (message.content or "").strip()
    if client.user:
        content = content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    lower = content.lower()
    prefixes = (
        "!kairos", "/kairos", "hey kairos", "yo kairos", "ok kairos",
        "okay kairos", "kaiross", "kairus", "kiros", "kyros", "kairos"
    )
    for prefix in prefixes:
        if lower.startswith(prefix):
            content = content[len(prefix):].strip()
            break
    return content or "Speak."


def split_text(text):
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= DISCORD_CHUNK_LIMIT:
        return [text]
    chunks = []
    rest = text
    while len(rest) > DISCORD_CHUNK_LIMIT:
        cut = rest.rfind("\n", 0, DISCORD_CHUNK_LIMIT)
        if cut < int(DISCORD_CHUNK_LIMIT * 0.4):
            cut = rest.rfind(". ", 0, DISCORD_CHUNK_LIMIT)
        if cut < int(DISCORD_CHUNK_LIMIT * 0.4):
            cut = rest.rfind(" ", 0, DISCORD_CHUNK_LIMIT)
        if cut < 1:
            cut = DISCORD_CHUNK_LIMIT
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def post_to_kairos_from_discord(message, text, should_reply_in_discord):
    payload = {
        "player": message.author.display_name,
        "message": text,
        "source": "discord",
        "message_id": str(message.id),
        "discord_message_id": str(message.id),
        "platform_user_id": str(message.author.id),
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id),
        "reply_allowed": bool(should_reply_in_discord),
        "discord_reply_allowed": bool(should_reply_in_discord),
        "bridge_only": not bool(should_reply_in_discord),
    }
    res = requests.post(KAIROS_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
    if res.status_code != 200:
        raise RuntimeError(f"Kairos API HTTP {res.status_code}: {res.text[:500]}")
    try:
        return res.json()
    except Exception:
        return {"reply": ""}


async def get_target_channel():
    if not DISCORD_CHANNEL_ID:
        return None
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(DISCORD_CHANNEL_ID)
        except Exception as e:
            log(f"Could not fetch Discord channel {DISCORD_CHANNEL_ID}: {e}")
            return None
    return channel


async def send_to_discord_channel(text):
    text = str(text or "").strip()
    if not text:
        return False
    channel = await get_target_channel()
    if channel is None:
        log("No target Discord channel available. Check DISCORD_CHANNEL_ID.")
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
    log("Discord normal chat bridges to Minecraft; Discord Kairos replies only when triggered.")
    log("Minecraft bridge endpoint: POST /mc_to_discord")
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
        data = await asyncio.to_thread(post_to_kairos_from_discord, message, user_text, triggered)
        if data.get("duplicate"):
            return
        reply = str(data.get("reply") or "").strip()
        if triggered and reply:
            async with message.channel.typing():
                for chunk in split_text(reply):
                    await message.channel.send(f"**[Kairos]** {chunk}")
                    await asyncio.sleep(0.35)
    except Exception as e:
        log(f"Discord -> Kairos ERROR: {e}")
        if triggered:
            try:
                await message.channel.send("**[Kairos]** ...connection disrupted.")
            except Exception:
                pass


@http_app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "service": "kairos-discord-full-bridge",
        "discord_ready": client.is_ready(),
        "channel_id": DISCORD_CHANNEL_ID,
        "endpoint": "/mc_to_discord",
    })


@http_app.route("/mc_to_discord", methods=["POST"])
def mc_to_discord():
    try:
        data = request.get_json(silent=True) or {}
        if MC_TO_DISCORD_TOKEN:
            supplied = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
            supplied_alt = str(data.get("token", "")).strip()
            if supplied != MC_TO_DISCORD_TOKEN and supplied_alt != MC_TO_DISCORD_TOKEN:
                return jsonify({"ok": False, "error": "unauthorized"}), 401

        player = str(data.get("player") or data.get("username") or data.get("name") or "Minecraft").strip()
        message = str(data.get("message") or data.get("content") or "").strip()

        if not message:
            return jsonify({"ok": False, "error": "missing message"}), 400

        safe_player = player.replace("@", "@\u200b")
        safe_message = message.replace("@", "@\u200b")
        formatted = f"**[Minecraft] {safe_player}:** {safe_message}"

        if not client.is_ready():
            log("Received Minecraft message before Discord client was ready.")
            return jsonify({"ok": False, "error": "discord client not ready"}), 503

        future = asyncio.run_coroutine_threadsafe(send_to_discord_channel(formatted), client.loop)
        ok = future.result(timeout=10)
        if ok:
            log(f"Minecraft -> Discord delivered for {player}.")
            return jsonify({"ok": True, "delivered": True}), 200
        return jsonify({"ok": False, "delivered": False, "error": "no channel"}), 500
    except Exception as e:
        log(f"Minecraft -> Discord ERROR: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def run_http_server():
    http_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    client.run(DISCORD_TOKEN)
