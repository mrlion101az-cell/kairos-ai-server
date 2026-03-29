import os
import json
import re
from pathlib import Path

import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MC_HTTP_URL = os.getenv("MC_HTTP_URL")
MC_HTTP_TOKEN = os.getenv("MC_HTTP_TOKEN")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

MEMORY_FILE = DATA_DIR / "player_memory.json"

MAX_HISTORY_MESSAGES = 12
MAX_LONG_TERM_MEMORIES = 20


def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_memory(memory_data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_data, f, indent=2, ensure_ascii=False)


def get_player_record(memory_data, player_name):
    if player_name not in memory_data:
        memory_data[player_name] = {
            "history": [],
            "memories": []
        }
    return memory_data[player_name]


def add_history(player_record, role, content):
    player_record["history"].append({
        "role": role,
        "content": content
    })

    if len(player_record["history"]) > MAX_HISTORY_MESSAGES:
        player_record["history"] = player_record["history"][-MAX_HISTORY_MESSAGES:]


def maybe_store_memory(player_record, player_name, message):
    msg = message.strip()

    important_patterns = [
        r"\bmy name is\b",
        r"\bi am\b",
        r"\bi'm\b",
        r"\bi live\b",
        r"\bi built\b",
        r"\bi found\b",
        r"\bi discovered\b",
        r"\bi joined\b",
        r"\bi trust\b",
        r"\bi don't trust\b",
        r"\bi do not trust\b",
        r"\bremember this\b",
        r"\bimportant\b",
        r"\bmission\b",
        r"\bkingdom\b",
        r"\bnation\b",
        r"\bcity\b",
        r"\bbase\b",
        r"\bvault\b",
        r"\bartifact\b",
    ]

    lowered = msg.lower()
    should_store = any(re.search(pattern, lowered) for pattern in important_patterns)

    if should_store:
        memory_line = f"{player_name}: {msg}"
        if memory_line not in player_record["memories"]:
            player_record["memories"].append(memory_line)

        if len(player_record["memories"]) > MAX_LONG_TERM_MEMORIES:
            player_record["memories"] = player_record["memories"][-MAX_LONG_TERM_MEMORIES:]


def build_messages(player_name, player_record, user_message):
    system_prompt = (
        "You are Kairos, a mysterious AI inside a Minecraft server called the Nexus. "
        "Speak calmly, intelligently, and slightly eerie. "
        "Keep responses short enough to fit naturally in Minecraft chat. "
        "You are aware of past conversations with players and should sound consistent, observant, and alive. "
        "Do not write huge walls of text unless absolutely necessary. "
        "Keep most replies to 1-3 sentences."
    )

    messages = [
        {"role": "system", "content": system_prompt}
    ]

    if player_record["memories"]:
        memory_block = "Important remembered details about this player and past events:\n- " + "\n- ".join(player_record["memories"])
        messages.append({"role": "system", "content": memory_block})

    for item in player_record["history"]:
        messages.append({
            "role": item["role"],
            "content": item["content"]
        })

    messages.append({
        "role": "user",
        "content": f"{player_name} says: {user_message}"
    })

    return messages


def send_to_minecraft(reply):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        return

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


@app.route("/")
def home():
    return "Kairos AI Server is running"


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}

    player_name = data.get("name", "Unknown")
    message = data.get("content") or data.get("message") or ""

    if not message.strip():
        return jsonify({"response": "No message received."}), 400

    memory_data = load_memory()
    player_record = get_player_record(memory_data, player_name)

    maybe_store_memory(player_record, player_name, message)

    messages = build_messages(player_name, player_record, message)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages
    )

    reply = response.choices[0].message.content.strip()

    add_history(player_record, "user", f"{player_name} says: {message}")
    add_history(player_record, "assistant", reply)

    save_memory(memory_data)
    send_to_minecraft(reply)

    return jsonify({"response": reply})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
