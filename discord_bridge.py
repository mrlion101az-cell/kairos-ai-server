import os
import requests
import discord

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
KAIROS_API_URL = os.getenv("KAIROS_API_URL", "").strip()
KAIROS_SHARED_SECRET = os.getenv("KAIROS_SHARED_SECRET", "").strip()

DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def should_process_message(message: discord.Message) -> bool:
    if message.author.bot:
        return False

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return False

    if not message.content.strip():
        return False

    return True


def forward_to_kairos(message: discord.Message):
    headers = {"Content-Type": "application/json"}

    if KAIROS_SHARED_SECRET:
        headers["X-Kairos-Secret"] = KAIROS_SHARED_SECRET

    payload = {
        "message": message.content,                  # ✅ FIXED
        "player_name": message.author.display_name,  # ✅ FIXED
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
        async with message.channel.typing():
            data = forward_to_kairos(message)

        # 🔥 THIS WAS MISSING — SEND REPLY BACK
        reply = data.get("reply", None)

        if reply:
            await message.channel.send(f"**[Kairos]** {reply}")

    except Exception as e:
        print(f"Error: {e}")
        await message.channel.send("**[Kairos]** ...connection disrupted.")


if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set")

client.run(DISCORD_BOT_TOKEN)
