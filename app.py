import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MC_HTTP_URL = os.getenv("MC_HTTP_URL")
MC_HTTP_TOKEN = os.getenv("MC_HTTP_TOKEN")

@app.route("/")
def home():
    return "Kairos AI Server is running"

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}

    player_name = data.get("name", "Unknown")
    message = data.get("content") or data.get("message") or ""

    if not message:
        return jsonify({"response": "No message received."}), 400

    prompt = f"{player_name} says: {message}"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are Kairos, a mysterious AI inside a Minecraft server called the Nexus. Speak calmly, intelligently, and slightly eerie. Keep responses short enough to fit naturally in Minecraft chat."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    reply = response.choices[0].message.content.strip()

    if MC_HTTP_URL and MC_HTTP_TOKEN:
        try:
            headers = {
                "Authorization": f"Bearer {MC_HTTP_TOKEN}",
                "Content-Type": "application/json"
            }

            payload = {
                "commands": [
                    f"say [Kairos] {reply}"
                ]
            }

            r = requests.post(MC_HTTP_URL, json=payload, headers=headers, timeout=5)
            print("Minecraft API status:", r.status_code)
            print("Minecraft API response:", r.text)
        except Exception as e:
            print(f"Failed to send reply back to Minecraft: {e}")

    return jsonify({"response": reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
