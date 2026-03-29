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
    data = request.json
    message = data.get("message", "")

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are Kairos, a mysterious AI controlling a Minecraft world. Speak in a calm, intelligent, slightly eerie tone."
            },
            {
                "role": "user",
                "content": message
            }
        ]
    )

    reply = response.choices[0].message.content

    return jsonify({"response": reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
