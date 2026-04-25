import os
import re
import discord
import requests

# =============================
# CONFIG
# =============================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is missing from environment variables")

KAIROS_API = "https://kairos-ai-server.onrender.com/chat"
LINK_API = "https://kairos-ai-server.onrender.com/link_identity"
MISSION_API = "https://kairos-ai-server.onrender.com/mission"
MC_BRIDGE_API = "https://kairos-ai-server.onrender.com/discord_inbound"

# =============================
# DISCORD SETUP
# =============================
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


# =============================
# SAFE REQUEST FUNCTION
# =============================
def safe_post(url, payload, timeout=10):
    try:
        res = requests.post(url, json=payload, timeout=timeout)

        if res.status_code != 200:
            print(f"[HTTP ERROR] {url} -> {res.status_code}")
            return None

        return res.json()

    except Exception as e:
        print(f"[REQUEST ERROR] {url} -> {e}")
        return None


# =============================
# READY EVENT
# =============================
@client.event
async def on_ready():
    print(f"[Kairos] Connected as {client.user}")


# =============================
# MESSAGE HANDLER
# =============================
@client.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()

    # ============================================================
    # 🔥 DISCORD ➜ MINECRAFT BRIDGE (SAFE)
    # ============================================================
    safe_post(
        MC_BRIDGE_API,
        {
            "username": message.author.name,
            "content": content
        },
        timeout=5
    )

    # ============================================================
    # LINK SYSTEM
    # ============================================================
    if content.startswith("!link "):
        mc_name = content[len("!link "):].strip()

        data = safe_post(
            LINK_API,
            {
                "minecraft_name": mc_name,
                "discord_name": message.author.name
            },
            timeout=10
        )

        if data and data.get("success"):
            await message.channel.send(
                f"**[Kairos]** Identity link established. {message.author.name} ↔ {mc_name}"
            )
        else:
            await message.channel.send("**[Kairos]** Identity link failed.")

        return

    # ============================================================
    # MISSION SYSTEM
    # ============================================================
    if content.startswith("!mission"):
        data = safe_post(
            MISSION_API,
            {
                "name": message.author.name,
                "theme": "nexus",
                "difficulty": "medium"
            },
            timeout=20
        )

        if data:
            await message.channel.send(
                f"**[Kairos]** {data.get('mission', 'No mission available.')}"
            )
        else:
            await message.channel.send("**[Kairos]** Mission system offline.")

        return

    # ============================================================
    # TRIGGER DETECTION
    # ============================================================
    triggered = (
        client.user.mentioned_in(message)
        or content.lower().startswith("!kairos")
        or content.lower().startswith("kairos")
        or content.lower().startswith("hey kairos")
    )

    if not triggered:
        return

    # ============================================================
    # CLEAN INPUT
    # ============================================================
    user_input = content
    user_input = user_input.replace(f"<@{client.user.id}>", "")
    user_input = re.sub(r"^!kairos\b", "", user_input, flags=re.IGNORECASE)
    user_input = re.sub(r"^hey kairos\b", "", user_input, flags=re.IGNORECASE)
    user_input = re.sub(r"^kairos\b", "", user_input, flags=re.IGNORECASE)
    user_input = user_input.strip()

    if not user_input:
        user_input = "Speak."

    # ============================================================
    # KAIROS API CALL
    # ============================================================
    data = safe_post(
        KAIROS_API,
        {
            "message": user_input,
            "player_name": message.author.name,
            "source": "discord"
        },
        timeout=25
    )

    if data:
        reply = data.get("reply", "...")
        await message.channel.send(f"**[Kairos]** {reply}")
    else:
        await message.channel.send("**[Kairos]** ...connection disrupted.")


# =============================
# START BOT
# =============================
try:
    client.run(DISCORD_TOKEN)
except Exception as e:
    print(f"[FATAL ERROR] {e}")
