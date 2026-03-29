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
    print(f"Kairos connected as {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if client.user.mentioned_in(message) or message.content.startswith("!kairos"):

        user_input = message.content.replace(f"<@{client.user.id}>", "").replace("!kairos", "").strip()

        try:
            response = requests.post(
                KAIROS_API,
                json={
                    "name": message.author.name,
                    "content": user_input
                },
                timeout=10
            )

            reply = response.json().get("response", "...")

            await message.channel.send(f"**[Kairos]** {reply}")

        except Exception:
            await message.channel.send("**[Kairos]** ...connection disrupted.")

client.run(DISCORD_TOKEN)
