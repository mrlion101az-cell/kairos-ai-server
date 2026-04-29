import os
import asyncio
import time
from collections import OrderedDict
import discord
import requests

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
KAIROS_API_URL = os.getenv("KAIROS_API_URL", "https://kairos-ai-server.onrender.com/chat").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0
REQUIRE_TRIGGER = os.getenv("KAIROS_REQUIRE_TRIGGER", "false").lower() == "true"
DEDUP_SECONDS = float(os.getenv("KAIROS_DISCORD_DEDUPE_SECONDS", "12"))
REQUEST_TIMEOUT = int(os.getenv("KAIROS_REQUEST_TIMEOUT", "35"))
DISCORD_CHUNK_LIMIT = int(os.getenv("DISCORD_CHUNK_LIMIT", "1850"))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN or DISCORD_BOT_TOKEN is missing")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

processed_ids = OrderedDict()
processed_fps = OrderedDict()

def cleanup():
    cutoff = time.time() - DEDUP_SECONDS
    for store in (processed_ids, processed_fps):
        for k, ts in list(store.items()):
            if ts < cutoff:
                store.pop(k, None)
        while len(store) > 500:
            store.popitem(last=False)

def already_processed(message):
    cleanup()
    mid = str(message.id)
    fp = f"{message.author.id}:{message.channel.id}:{message.content.strip().lower()}"
    if mid in processed_ids or fp in processed_fps:
        return True
    processed_ids[mid] = time.time()
    processed_fps[fp] = time.time()
    return False

def triggered(message):
    content = message.content.strip().lower()
    if client.user and client.user.mentioned_in(message):
        return True
    return content.startswith(("kairos", "!kairos", "hey kairos", "kairus", "kaiross"))

def clean_text(message):
    content = message.content.strip()
    if client.user:
        content = content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "")
    lower = content.lower()
    for prefix in ("!kairos", "hey kairos", "kaiross", "kairus", "kairos"):
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

def post_to_kairos(message, text):
    payload = {
        "player": message.author.display_name,
        "message": text,
        "source": "discord",
        "message_id": str(message.id),
        "discord_message_id": str(message.id),
        "platform_user_id": str(message.author.id),
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id),
    }
    res = requests.post(KAIROS_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
    if res.status_code != 200:
        raise RuntimeError(f"Kairos API HTTP {res.status_code}: {res.text[:300]}")
    return res.json()

@client.event
async def on_ready():
    print("=" * 72, flush=True)
    print(f"[Kairos Discord Bridge] Online as {client.user}", flush=True)
    print(f"KAIROS_API_URL={KAIROS_API_URL}", flush=True)
    print(f"CHANNEL_LOCK={DISCORD_CHANNEL_ID if DISCORD_CHANNEL_ID else 'ALL'}", flush=True)
    print(f"REQUIRE_TRIGGER={REQUIRE_TRIGGER}", flush=True)
    print("=" * 72, flush=True)

@client.event
async def on_message(message):
    if message.author.bot:
        return
    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return
    if not message.content or not message.content.strip():
        return
    if REQUIRE_TRIGGER and not triggered(message):
        return
    if already_processed(message):
        return

    user_text = clean_text(message) if triggered(message) else message.content.strip()
    try:
        async with message.channel.typing():
            data = await asyncio.to_thread(post_to_kairos, message, user_text)
        if data.get("duplicate"):
            return
        reply = data.get("reply") or ""
        for chunk in split_text(reply):
            await message.channel.send(f"**[Kairos]** {chunk}")
            await asyncio.sleep(0.35)
    except Exception as e:
        print(f"[Kairos Discord Bridge ERROR] {e}", flush=True)
        await message.channel.send("**[Kairos]** ...connection disrupted.")

client.run(DISCORD_TOKEN)
