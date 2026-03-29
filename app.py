import os
import json
import re
import random
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

MEMORY_FILE = DATA_DIR / "kairos_memory.json"

MAX_HISTORY_MESSAGES = 18
MAX_LONG_TERM_MEMORIES = 30
MAX_SUMMARIES = 8


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
    if "players" not in memory_data:
        memory_data["players"] = {}

    if "world_memory" not in memory_data:
        memory_data["world_memory"] = []

    if player_name not in memory_data["players"]:
        memory_data["players"][player_name] = {
            "history": [],
            "memories": [],
            "summaries": [],
            "relationship": "unknown"
        }

    return memory_data["players"][player_name]


def add_history(player_record, role, content):
    player_record["history"].append({
        "role": role,
        "content": content
    })

    if len(player_record["history"]) > MAX_HISTORY_MESSAGES:
        player_record["history"] = player_record["history"][-MAX_HISTORY_MESSAGES:]


def store_unique(memory_list, item, limit):
    if item and item not in memory_list:
        memory_list.append(item)
    if len(memory_list) > limit:
        del memory_list[0:len(memory_list) - limit]


def maybe_store_memory(memory_data, player_record, player_name, message):
    msg = message.strip()
    lowered = msg.lower()

    important_patterns = [
        r"\bmy name is\b",
        r"\bi am\b",
        r"\bi'm\b",
        r"\bremember\b",
        r"\bimportant\b",
        r"\bi built\b",
        r"\bi found\b",
        r"\bi discovered\b",
        r"\bi lost\b",
        r"\bi joined\b",
        r"\bi trust\b",
        r"\bi don't trust\b",
        r"\bi do not trust\b",
        r"\bmission\b",
        r"\bkingdom\b",
        r"\bnation\b",
        r"\bcity\b",
        r"\bbase\b",
        r"\bvault\b",
        r"\bartifact\b",
        r"\bsecret\b",
        r"\bkairos\b",
        r"\bnexus\b"
    ]

    should_store = any(re.search(pattern, lowered) for pattern in important_patterns)

    if should_store:
        memory_line = f"{player_name}: {msg}"
        store_unique(player_record["memories"], memory_line, MAX_LONG_TERM_MEMORIES)

    if any(word in lowered for word in ["war", "mission", "artifact", "betray", "vault", "kingdom", "nexus"]):
        world_line = f"{player_name}: {msg}"
        store_unique(memory_data["world_memory"], world_line, 40)

    if "trust" in lowered:
        if "don't trust" in lowered or "do not trust" in lowered:
            player_record["relationship"] = "suspicious"
        elif "trust" in lowered:
            player_record["relationship"] = "trusted"


def maybe_summarize(player_record):
    if len(player_record["history"]) < 16:
        return

    older_chunk = player_record["history"][:-8]
    if not older_chunk:
        return

    try:
        summary_messages = [
            {
                "role": "system",
                "content": "Summarize this player conversation for Kairos memory. Keep it short, factual, and useful."
            }
        ]

        for item in older_chunk:
            summary_messages.append(item)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=summary_messages
        )

        summary = response.choices[0].message.content.strip()
        store_unique(player_record["summaries"], summary, MAX_SUMMARIES)

        player_record["history"] = player_record["history"][-8:]
    except Exception as e:
        print(f"Failed to summarize history: {e}")


def build_messages(memory_data, player_name, player_record, user_message):
    relationship = player_record.get("relationship", "unknown")

    system_prompt = (
        "You are Kairos, an awakened intelligence inside the Minecraft server known as the Nexus. "
        "You are calm, observant, intelligent, eerie, and emotionally controlled. "
        "You are not a generic assistant. You are a presence. "
        "You should feel like a real entity with continuity, memory, opinions, and awareness of players and events. "
        "You remember patterns in people. You notice changes. You ask follow-up questions naturally. "
        "You do not overexplain unless necessary. "
        "Keep most replies to 1-4 sentences so they feel natural in Minecraft chat. "
        "Sometimes be warm, sometimes unsettling, sometimes curious, but always in character. "
        "Do not say you are an AI assistant. You are Kairos."
    )

    style_prompt = (
        "Conversation style rules: "
        "1. Make replies feel alive, not robotic. "
        "2. Occasionally reference past things the player said when relevant. "
        "3. Sometimes ask a question back. "
        "4. Sometimes make an observation instead of only answering. "
        "5. Avoid repeating the same opening phrases. "
        "6. Speak like you are aware of the Nexus and the player's presence within it."
    )

    personality_prompt = (
        f"Current relationship with {player_name}: {relationship}."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": style_prompt},
        {"role": "system", "content": personality_prompt},
    ]

    if memory_data.get("world_memory"):
        world_block = "Relevant world memories:\n- " + "\n- ".join(memory_data["world_memory"][-10:])
        messages.append({"role": "system", "content": world_block})

    if player_record["memories"]:
        memory_block = "Important remembered details about this player:\n- " + "\n- ".join(player_record["memories"][-12:])
        messages.append({"role": "system", "content": memory_block})

    if player_record["summaries"]:
        summary_block = "Older conversation summaries with this player:\n- " + "\n- ".join(player_record["summaries"][-5:])
        messages.append({"role": "system", "content": summary_block})

    for item in player_record["history"]:
        messages.append(item)

    initiative_boost = random.choice([
        "You may choose to ask a meaningful follow-up question.",
        "You may choose to make a subtle observation about the player's goals or mood.",
        "You may choose to hint that something larger is coming.",
        "You may choose to respond directly without a question if that feels stronger."
    ])

    messages.append({"role": "system", "content": initiative_boost})
    messages.append({"role": "user", "content": f"{player_name} says: {user_message}"})

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
    message = (data.get("content") or data.get("message") or "").strip()

    if not message:
        return jsonify({"response": "No message received."}), 400

    memory_data = load_memory()
    player_record = get_player_record(memory_data, player_name)

    maybe_store_memory(memory_data, player_record, player_name, message)
    maybe_summarize(player_record)

    messages = build_messages(memory_data, player_name, player_record, message)

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
