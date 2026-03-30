import os
import requests
import discord

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
KAIROS_API_URL = os.getenv("KAIROS_API_URL", "").strip()
KAIROS_SHARED_SECRET = os.getenv("KAIROS_SHARED_SECRET", "").strip()

DISCORD_CHANNEL_ID = 0
if DISCORD_CHANNEL_ID_RAW.isdigit():
    DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)


def should_process_message(message: discord.Message) -> bool:
    if message.author.bot:
        print(f"Ignored bot message from {message.author}")
        return False

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        print(
            f"Ignored message from channel {message.channel.id}; "
            f"listening only to {DISCORD_CHANNEL_ID}"
        )
        return False

    content = (message.content or "").strip()
    if not content:
        print(f"Ignored empty message from {message.author}")
        return False

    return True


def forward_to_kairos(message: discord.Message):
    if not KAIROS_API_URL:
        raise RuntimeError("KAIROS_API_URL is not set")

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

    print(f"Forwarding to Kairos API: {KAIROS_API_URL}")
    print(f"Payload: {payload}")

    response = requests.post(
        KAIROS_API_URL,
        json=payload,
        headers=headers,
        timeout=30
    )

    print(f"Kairos API status: {response.status_code}")
    print(f"Kairos API response: {response.text}")

    response.raise_for_status()
    return response.json()


@client.event
async def on_ready():
    print("=" * 60)
    print(f"Discord bridge online as {client.user} (ID: {client.user.id})")
    print(f"KAIROS_API_URL set: {bool(KAIROS_API_URL)}")

    if DISCORD_CHANNEL_ID:
        print(f"Listening only in channel ID: {DISCORD_CHANNEL_ID}")
    else:
        print("No DISCORD_CHANNEL_ID set. Listening in all accessible channels.")

    print("=" * 60)


@client.event
async def on_message(message: discord.Message):
    print(
        f"Message seen | author={message.author} "
        f"| channel={message.channel.id} "
        f"| content={message.content!r}"
    )

    if not should_process_message(message):
        print("Message ignored by filter.")
        return

    try:
        async with message.channel.typing():
            data = forward_to_kairos(message)

        print("Forwarded Discord message to Kairos successfully.")
        print(f"Relationship returned: {data.get('relationship', 'unknown')}")

    except Exception as e:
        print(f"Failed to forward Discord message: {e}")


if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set")

client.run(DISCORD_BOT_TOKEN)
