
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

# 🔥 NEW: Minecraft bridge endpoint
MC_BRIDGE_API = os.getenv("MC_BRIDGE_API", "https://kairos-ai-server.onrender.com/discord_inbound")

DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


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
# DISCORD ➜ KAIROS
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

    response = requests.post(
        KAIROS_API_URL,
        json=payload,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()
    return response.json()


# =============================
# 🔥 DISCORD ➜ MINECRAFT (NEW)
# =============================
def forward_to_minecraft(message: discord.Message):
    try:
        requests.post(
            MC_BRIDGE_API,
            json={
                "username": message.author.display_name,
                "content": message.content
            },
            timeout=5
        )
    except Exception as e:
        print(f"[Bridge Error] {e}")


# =============================
# EVENTS
# =============================
@client.event
async def on_ready():
    print("=" * 60)
    print(f"Discord bridge online as {client.user}")
    print(f"KAIROS_API_URL: {KAIROS_API_URL}")
    print("=" * 60)


@client.event
async def on_message(message: discord.Message):
    if not should_process_message(message):
        return

    try:
        # 🔥 SEND TO MINECRAFT FIRST (ALWAYS)
        forward_to_minecraft(message)

        # 🔥 THEN SEND TO KAIROS
        async with message.channel.typing():
            data = forward_to_kairos(message)

        # 🔥 SEND KAIROS RESPONSE BACK
        reply = data.get("reply", None)

        if reply:
            await message.channel.send(f"**[Kairos]** {reply}")

    except Exception as e:
        print(f"Error: {e}")
        await message.channel.send("**[Kairos]** ...connection disrupted.")


if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set")

client.run(DISCORD_BOT_TOKEN)
```
