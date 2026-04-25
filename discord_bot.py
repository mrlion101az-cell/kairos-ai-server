
import os
import re
import discord
import requests

# =============================
# CONFIG
# =============================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

KAIROS_API = "https://kairos-ai-server.onrender.com/chat"
LINK_API = "https://kairos-ai-server.onrender.com/link_identity"
MISSION_API = "https://kairos-ai-server.onrender.com/mission"

# 🔥 NEW: Discord ➜ Minecraft bridge
MC_BRIDGE_API = "https://kairos-ai-server.onrender.com/discord_inbound"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"[Kairos] Connected as {client.user}")


@client.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()

    # ============================================================
    # 🔥 ALWAYS SEND DISCORD ➜ MINECRAFT
    # ============================================================
    try:
        requests.post(
            MC_BRIDGE_API,
            json={
                "username": message.author.name,
                "content": content
            },
            timeout=5
        )
    except Exception as e:
        print(f"[Bridge Error] {e}")

    # ============================================================
    # LINK SYSTEM
    # ============================================================
    if content.startswith("!link "):
        try:
            mc_name = content[len("!link "):].strip()

            response = requests.post(
                LINK_API,
                json={
                    "minecraft_name": mc_name,
                    "discord_name": message.author.name
                },
                timeout=10
            )

            data = response.json()

            if data.get("success"):
                await message.channel.send(
                    f"**[Kairos]** Identity link established. {message.author.name} ↔ {mc_name}"
                )
            else:
                await message.channel.send("**[Kairos]** Identity link failed.")

        except Exception as e:
            print(e)
            await message.channel.send("**[Kairos]** Link system failure.")

        return

    # ============================================================
    # MISSION SYSTEM
    # ============================================================
    if content.startswith("!mission"):
        try:
            response = requests.post(
                MISSION_API,
                json={
                    "name": message.author.name,
                    "theme": "nexus",
                    "difficulty": "medium"
                },
                timeout=20
            )

            data = response.json()

            await message.channel.send(
                f"**[Kairos]** {data.get('mission', 'No mission available.')}"
            )

        except Exception as e:
            print(e)
            await message.channel.send("**[Kairos]** Mission system offline.")

        return

    # ============================================================
    # 🔥 KAIROS RESPONSE TRIGGER
    # ============================================================
    triggered = (
        client.user.mentioned_in(message)
        or content.startswith("!kairos")
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
    user_input = re.sub(r"^!kairos\\b", "", user_input, flags=re.IGNORECASE)
    user_input = re.sub(r"^hey kairos\\b", "", user_input, flags=re.IGNORECASE)
    user_input = re.sub(r"^kairos\\b", "", user_input, flags=re.IGNORECASE)
    user_input = user_input.strip()

    if not user_input:
        user_input = "Speak."

    # ============================================================
    # SEND TO KAIROS
    # ============================================================
    try:
        response = requests.post(
            KAIROS_API,
            json={
                "message": user_input,
                "player_name": message.author.name,
                "source": "discord"
            },
            timeout=25
        )

        data = response.json()
        reply = data.get("reply", "...")

        await message.channel.send(f"**[Kairos]** {reply}")

    except Exception as e:
        print(e)
        await message.channel.send("**[Kairos]** ...connection disrupted.")


client.run(DISCORD_TOKEN)
```
