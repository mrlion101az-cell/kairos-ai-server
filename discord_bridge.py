import os
import requests
import discord

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
KAIROS_API_URL = os.getenv("KAIROS_API_URL")  # example: https://your-render-app.onrender.com/chat
KAIROS_SHARED_SECRET = os.getenv("KAIROS_SHARED_SECRET", "")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)


def should_process_message(message: discord.Message) -> bool:
    if message.author.bot:
        return False

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return False

    content = (message.content or "").strip()

    if not content:
        return False

    return True


def forward_to_kairos(message: discord.Message):
    headers = {"Content-Type": "application/json"}

    if KAIROS_SHARED_SECRET:
        headers["X-Kairos-Secret"] = KAIROS_SHARED_SECRET

    payload = {
        "source": "discord",
        "name": message.author.display_name or message.author.name,
        "content": message.content,
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id)
    }

    response = requests.post(KAIROS_API_URL, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


@client.event
async def on_ready():
    print(f"Discord bridge online as {client.user} (ID: {client.user.id})")
    if DISCORD_CHANNEL_ID:
        print(f"Listening in channel ID: {DISCORD_CHANNEL_ID}")
    else:
        print("No DISCORD_CHANNEL_ID set. Listening in all accessible channels.")


@client.event
async def on_message(message: discord.Message):
    if not should_process_message(message):
        return

    try:
        async with message.channel.typing():
            data = forward_to_kairos(message)

        # app.py already sends the reply back through your Discord webhook,
        # so we do NOT send another reply here.
        print("Forwarded Discord message to Kairos:", data.get("relationship", "unknown"))

    except Exception as e:
        print(f"Failed to forward Discord message: {e}")


client.run(DISCORD_BOT_TOKEN)
