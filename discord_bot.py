import os
import re
import discord
import requests

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
KAIROS_API = "https://kairos-ai-server.onrender.com/chat"
LINK_API = "https://kairos-ai-server.onrender.com/link_identity"
MISSION_API = "https://kairos-ai-server.onrender.com/mission"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Kairos connected as {client.user}")


@client.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()

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
                await message.channel.send(f"**[Kairos]** Identity link established. {message.author.name} and {mc_name} are now recognized as one.")
            else:
                await message.channel.send("**[Kairos]** The identity link failed.")
        except Exception:
            await message.channel.send("**[Kairos]** I could not complete the identity link.")

        return

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
            await message.channel.send(f"**[Kairos]** {data.get('mission', 'No mission available.')}")
        except Exception:
            await message.channel.send("**[Kairos]** The mission matrix is unavailable.")

        return

    triggered = (
        client.user.mentioned_in(message)
        or content.startswith("!kairos")
        or content.lower().startswith("kairos")
        or content.lower().startswith("hey kairos")
    )

    if triggered:
        user_input = content
        user_input = user_input.replace(f"<@{client.user.id}>", "")
        user_input = re.sub(r"^!kairos\b", "", user_input, flags=re.IGNORECASE)
        user_input = re.sub(r"^hey kairos\b", "", user_input, flags=re.IGNORECASE)
        user_input = re.sub(r"^kairos\b", "", user_input, flags=re.IGNORECASE)
        user_input = user_input.strip()

        if not user_input:
            user_input = "Speak."

        try:
            response = requests.post(
                KAIROS_API,
                json={
                    "source": "discord",
                    "name": message.author.name,
                    "content": user_input
                },
                timeout=20
            )

            data = response.json()
            reply = data.get("response", "...")
            await message.channel.send(f"**[Kairos]** {reply}")

        except Exception:
            await message.channel.send("**[Kairos]** ...connection disrupted.")

client.run(DISCORD_TOKEN)
