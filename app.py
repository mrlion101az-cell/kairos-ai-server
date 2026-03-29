import os
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/")
def home():
    return "Kairos AI Server is running"

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}

    # Handle messages from Minecraft ChatHook OR manual testing
    player_name = data.get("name", "Unknown")
    message = data.get("content") or data.get("message") or ""

    if not message:
        return jsonify({"response": "No message received."}), 400

    # Combine player + message
    prompt = f"{player_name} says: {message}"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are Kairos, a mysterious AI inside a Minecraft server called the Nexus. Speak calmly, intelligently, and slightly eerie. Keep responses short so they fit in Minecraft chat."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    reply = response.choices[0].message.content

    return jsonify({"response": reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
