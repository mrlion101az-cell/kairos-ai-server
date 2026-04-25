import os
import requests
import discord

# =============================
# ENV CONFIG
# =============================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()

KAIROS_API_URL = os.getenv("KAIROS_API_URL", "").strip()
KAIROS_SHARED_SECRET = os.getenv("KAIROS_SHARED_SECRET", "").strip()

MC_BRIDGE_API = os.getenv(
    "MC_BRIDGE_API",
    "https://kairos-ai-server.onrender.com/discord_inbound"
)

# =============================
# VALIDATION
# =============================
if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set")

if not KAIROS_API_URL:
    raise RuntimeError("KAIROS_API_URL is not set")

DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0

# =============================
# DISCORD SETUP
# =============================
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


# =============================
# SAFE REQUEST FUNCTION
# =============================
def safe_post(url, payload, headers=None, timeout=10):
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)

        if res.status_code != 200:
            print(f"[HTTP ERROR] {url} -> {res.status_code}")
            return None

        try:
            return res.json()
        except Exception:
            print(f"[JSON ERROR] Invalid response from {url}")
            return None

    except Exception as e:
        print(f"[REQUEST ERROR] {url} -> {e}")
        return None


# =============================
# FILTER
# =============================
def should_process_message(message: discord.Message) -> bool:
    if message.author.bot:
        return False

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return False

    if not message.content.strip():
        return False

    return True


# =============================
# DISCORD ➜ KAIROS (SAFE)
# =============================
def forward_to_kairos(message: discord.Message):
    headers = {"Content-Type": "application/json"}

    if KAIROS_SHARED_SECRET:
        headers["X-Kairos-Secret"] = KAIROS_SHARED_SECRET

    payload = {
        "message": message.content,
        "player_name": message.author.display_name,
        "source": "discord",
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id)
    }

    return safe_post(
        KAIROS_API_URL,
        payload,
        headers=headers,
        timeout=25
    )


# =============================
# DISCORD ➜ MINECRAFT (SAFE)
# =============================
def forward_to_minecraft(message: discord.Message):
    safe_post(
        MC_BRIDGE_API,
        {
            "username": message.author.display_name,
            "content": message.content
        },
        timeout=5
    )


# =============================
# EVENTS
# =============================
@client.event
async def on_ready():
    print("=" * 60)
    print(f"[Kairos Bridge] Online as {client.user}")
    print(f"KAIROS_API_URL: {KAIROS_API_URL}")
    print(f"CHANNEL LOCK: {DISCORD_CHANNEL_ID if DISCORD_CHANNEL_ID else 'ALL'}")
    print("=" * 60)


@client.event
async def on_message(message: discord.Message):
    if not should_process_message(message):
        return

    try:
        # 🔥 ALWAYS SEND TO MINECRAFT
        forward_to_minecraft(message)

        # 🔥 THEN PROCESS KAIROS
        async with message.channel.typing():
            data = forward_to_kairos(message)

        # 🔥 HANDLE RESPONSE SAFELY
        if data:
            reply = data.get("reply", None)

            if reply:
                await message.channel.send(f"**[Kairos]** {reply}")
        else:
            await message.channel.send("**[Kairos]** ...no response from core.")

    except Exception as e:
        print(f"[FATAL MESSAGE ERROR] {e}")
        await message.channel.send("**[Kairos]** ...connection disrupted.")


# =============================
# START BOT
# =============================
try:
    client.run(DISCORD_BOT_TOKEN)
except Exception as e:
    print(f"[FATAL START ERROR] {e}")
