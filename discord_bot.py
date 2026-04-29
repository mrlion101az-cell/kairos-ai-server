import os
import discord
import requests

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
KAIROS_API = "https://kairos-ai-server.onrender.com/chat"

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

    try:
        res = requests.post(
            KAIROS_API,
            json={
                "player": message.author.name,  # ✅ FIXED
                "message": message.content,
                "source": "discord"
            },
            timeout=20
        )

        data = res.json()

        reply = data.get("reply")

        if reply:
            await message.channel.send(f"**[Kairos]** {reply}")
        else:
            await message.channel.send("**[Kairos]** ...no response.")

    except Exception as e:
        print(f"[ERROR] {e}")
        await message.channel.send("**[Kairos]** connection error.")

client.run(DISCORD_TOKEN)
