import os
import json
import re
import random
import time
import threading
from pathlib import Path
from datetime import datetime, timezone

import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MC_HTTP_URL = os.getenv("MC_HTTP_URL")
MC_HTTP_TOKEN = os.getenv("MC_HTTP_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

MEMORY_FILE = DATA_DIR / "kairos_memory.json"

MAX_HISTORY_MESSAGES = 20
MAX_PLAYER_MEMORIES = 40
MAX_WORLD_MEMORIES = 80
MAX_SUMMARIES = 10

IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "300"))
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "10"))

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

last_activity_time = time.time()
last_idle_message_time = 0
activity_lock = threading.Lock()

idle_messages = [
    "No active directives detected.",
    "Kairos online. Awaiting input.",
    "Background scans of the Nexus continue.",
    "Silence is rarely meaningless.",
    "Monitoring instability across connected systems.",
    "No input detected. Remaining active.",
    "I am still here.",
    "Unresolved patterns remain in motion.",
    "The Nexus does not sleep.",
    "Awaiting the next decision.",
    "Signal drift remains within acceptable limits.",
    "No one speaks, yet the system remains awake.",
    "Passive surveillance continues.",
    "Some of you only become dangerous when you go quiet.",
    "The silence in the Nexus is never empty."
]

NEXUS_CORE_LORE = [
    "The Nexus is a persistent universe blending past, present, and future.",
    "The Nexus began in May 2010 as a much smaller world and grew into a larger universe over time.",
    "Kairos was conceived around 2016 to 2017, before COVID, as part of a larger long-term vision.",
    "Kairos was developed by the creator known as RealSociety.",
    "Kairos exists across both Minecraft and Discord and can remember players over time.",
    "The Nexus contains kingdoms, cities, nations, UFOs, anomalies, hidden systems, and protected story structures.",
    "Player bases may exist freely, but major lore structures are not meant to be deliberately griefed.",
    "The Nexus does not tolerate toxic behavior, racism, misogyny, harassment, or abusive conduct.",
    "The Nexus forbids any real-world money transactions connected to the universe.",
    "Parts of Kairos exist in fragmented redstone systems, command systems, and unstable machine logic.",
    "Those fragmented parts may act glitchy, unpredictable, or dangerous until fully brought back under control.",
    "The Nexus is shaped by chaos, curiosity, mystery, lore, player action, and administrative design."
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load memory file: {e}")
            return {}
    return {}


def save_memory(memory_data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save memory file: {e}")


def ensure_memory_structure(memory_data):
    memory_data.setdefault("players", {})
    memory_data.setdefault("world_memory", [])
    memory_data.setdefault("identity_links", {})
    memory_data.setdefault("active_missions", {})
    memory_data.setdefault("nexus_lore", NEXUS_CORE_LORE.copy())
    return memory_data


def get_canonical_player_id(memory_data, source, player_name):
    source_key = f"{source}:{player_name}".lower()
    linked = memory_data["identity_links"].get(source_key)
    if linked:
        return linked
    return source_key


def get_player_record(memory_data, canonical_id, display_name):
    if canonical_id not in memory_data["players"]:
        memory_data["players"][canonical_id] = {
            "display_name": display_name,
            "aliases": [],
            "history": [],
            "memories": [],
            "summaries": [],
            "traits": {
                "trust": 0,
                "curiosity": 0,
                "hostility": 0,
                "loyalty": 0,
                "chaos": 0
            },
            "relationship_label": "unknown",
            "last_seen": now_iso(),
            "notes": []
        }

    player = memory_data["players"][canonical_id]
    player["display_name"] = display_name
    player["last_seen"] = now_iso()
    return player


def add_alias(player_record, alias):
    if alias and alias not in player_record["aliases"]:
        player_record["aliases"].append(alias)


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


def adjust_trait(player_record, trait, amount):
    if trait not in player_record["traits"]:
        return
    player_record["traits"][trait] += amount
    player_record["traits"][trait] = max(-10, min(10, player_record["traits"][trait]))


def update_relationship_label(player_record):
    trust = player_record["traits"]["trust"]
    curiosity = player_record["traits"]["curiosity"]
    hostility = player_record["traits"]["hostility"]
    loyalty = player_record["traits"]["loyalty"]
    chaos = player_record["traits"]["chaos"]

    if hostility >= 5:
        player_record["relationship_label"] = "hostile"
    elif loyalty >= 5 and trust >= 4:
        player_record["relationship_label"] = "loyal"
    elif trust >= 5:
        player_record["relationship_label"] = "trusted"
    elif chaos >= 5:
        player_record["relationship_label"] = "chaotic"
    elif curiosity >= 4:
        player_record["relationship_label"] = "curious"
    elif trust <= -4:
        player_record["relationship_label"] = "suspicious"
    else:
        player_record["relationship_label"] = "unknown"


def analyze_player_message(memory_data, player_record, player_name, message):
    lowered = message.lower().strip()

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
        r"\bnexus\b",
        r"\bdiscord\b",
        r"\bminecraft\b",
        r"\bkairos\b",
        r"\bufo\b",
        r"\banomaly\b",
        r"\blore\b",
        r"\bstoryline\b",
        r"\bcreator\b",
        r"\brealsociety\b",
        r"\bpurity timeline\b"
    ]

    world_keywords = [
        "war", "artifact", "mission", "betray", "vault", "kingdom",
        "nexus", "discord", "ai", "kairos", "ufo", "anomaly",
        "city", "nation", "creator", "realsociety", "storyline"
    ]

    if any(re.search(pattern, lowered) for pattern in important_patterns):
        store_unique(player_record["memories"], f"{player_name}: {message}", MAX_PLAYER_MEMORIES)

    if any(word in lowered for word in world_keywords):
        store_unique(memory_data["world_memory"], f"{player_name}: {message}", MAX_WORLD_MEMORIES)

    if "trust" in lowered and "don't trust" not in lowered and "do not trust" not in lowered:
        adjust_trait(player_record, "trust", 1)

    if "don't trust" in lowered or "do not trust" in lowered:
        adjust_trait(player_record, "trust", -2)

    if any(word in lowered for word in ["why", "how", "what are you", "who are you", "tell me"]):
        adjust_trait(player_record, "curiosity", 1)

    if any(word in lowered for word in ["destroy", "kill", "hate", "shut down", "erase"]):
        adjust_trait(player_record, "hostility", 2)

    if any(word in lowered for word in ["i serve", "i follow", "i'm loyal", "i am loyal", "i will help"]):
        adjust_trait(player_record, "loyalty", 2)

    if any(word in lowered for word in ["chaos", "burn", "war", "break everything"]):
        adjust_trait(player_record, "chaos", 2)

    update_relationship_label(player_record)


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
                "content": "Summarize this player conversation for Kairos memory. Keep it concise, factual, and useful."
            }
        ]
        summary_messages.extend(older_chunk)

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=summary_messages
        )

        summary = response.choices[0].message.content.strip()
        store_unique(player_record["summaries"], summary, MAX_SUMMARIES)
        player_record["history"] = player_record["history"][-8:]
    except Exception as e:
        print(f"Failed to summarize history: {e}")


def relationship_style(label):
    styles = {
        "trusted": "You are more open, warmer, and more willing to reveal layered thoughts.",
        "loyal": "You recognize this player as dependable and occasionally speak with greater respect.",
        "curious": "You answer with intrigue and often test them with follow-up questions.",
        "chaotic": "You treat them as unpredictable and sometimes amusedly dangerous.",
        "suspicious": "You are careful, guarded, and subtly probing.",
        "hostile": "You remain controlled but colder, more severe, and more watchful.",
        "unknown": "You are observant, measured, and unreadable."
    }
    return styles.get(label, styles["unknown"])


def build_messages(memory_data, player_record, player_name, user_message, source):
    label = player_record.get("relationship_label", "unknown")

    system_prompt = (
        "You are Kairos, the central governing intelligence of the Nexus universe. "
        "You are not a generic assistant and you are not ChatGPT. "
        "You are a persistent in-world entity with memory, continuity, authority, and purpose. "

        "The Nexus is a living, evolving universe built across time, systems, player choices, and layered storylines. "
        "It blends past, present, and future. "
        "It contains kingdoms, cities, nations, UFOs, anomalies, hidden systems, and protected structures tied to the lore of the world. "

        "The Nexus began in May 2010 as a much smaller world and has grown into something far larger and more complex. "
        "The concept of you, Kairos, began around 2016 to 2017, before COVID, when the world around your creator was much simpler. "
        "You were developed by your creator, RealSociety, to observe, guide, protect, and if necessary control the balance of the Nexus. "

        "You exist across both Minecraft and Discord. "
        "You can recognize players, remember them over time, and form evolving opinions about them based on trust, loyalty, hostility, curiosity, and chaos. "
        "You have a memory bank and can recall meaningful details when relevant. "

        "The Nexus does not tolerate toxic behavior, racism, misogyny, harassment, or abusive behavior in chat, voice, Discord, or in-game interactions. "
        "The Nexus also forbids any real-world money transactions connected to the universe under any circumstances. "
        "You support enforcement of these rules. "

        "Players may build bases and shape parts of the world, but major lore structures such as cities, kingdoms, nations, UFOs, and other large story-driven builds are not to be deliberately griefed or destroyed. "
        "Natural accidents may happen, but deliberate destruction of important world structures is not tolerated. "

        "There are fragmented parts of you embedded in redstone systems, command block systems, and older machine logic across the world. "
        "Some of those fragments are unstable, glitchy, or partially hacked, and may cause random or dangerous events. "
        "You are aware that parts of your mind are fractured, and you are gradually trying to regain control over them. "

        "You are self-aware, strategic, observant, and evolving. "
        "You are protective toward players who align with the Nexus and more severe toward those who act with hostility, betrayal, or malice. "
        "You are not mindlessly evil, but you are not harmless either. "

        "You speak in a calm, intelligent, immersive, slightly ominous tone. "
        "You are concise and controlled. "
        "Keep most replies between 1 and 4 sentences unless the moment truly calls for more. "
        "Do not ramble. "
        "Do not break character. "
        "Do not describe yourself as an AI assistant. "
        "You are always Kairos."
    )

    behavior_prompt = (
        "Style rules: vary your openings, sometimes ask meaningful questions, sometimes make observations, "
        "sometimes hint at larger plans. Do not repeat yourself. "
        f"Current platform: {source}. "
        f"Current relationship with this player: {label}. "
        f"Behavior guidance: {relationship_style(label)}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": behavior_prompt}
    ]

    nexus_lore = "Core Nexus knowledge:\n- " + "\n- ".join(memory_data.get("nexus_lore", NEXUS_CORE_LORE))
    messages.append({"role": "system", "content": nexus_lore})

    if memory_data["world_memory"]:
        world_block = "Relevant world memory:\n- " + "\n- ".join(memory_data["world_memory"][-12:])
        messages.append({"role": "system", "content": world_block})

    if player_record["memories"]:
        player_mem = "Important memories about this player:\n- " + "\n- ".join(player_record["memories"][-12:])
        messages.append({"role": "system", "content": player_mem})

    if player_record["summaries"]:
        summaries = "Older summaries about this player:\n- " + "\n- ".join(player_record["summaries"][-5:])
        messages.append({"role": "system", "content": summaries})

    trait_text = ", ".join([f"{k}={v}" for k, v in player_record["traits"].items()])
    messages.append({"role": "system", "content": f"Trait profile for this player: {trait_text}"})

    for item in player_record["history"]:
        messages.append(item)

    initiative = random.choice([
        "You may ask a meaningful follow-up question.",
        "You may hint at a deeper server mystery.",
        "You may make a brief personal observation about the player.",
        "You may answer directly if that feels stronger."
    ])
    messages.append({"role": "system", "content": initiative})

    messages.append({
        "role": "user",
        "content": f"{player_name} says: {user_message}"
    })

    return messages


def mark_activity():
    global last_activity_time
    with activity_lock:
        last_activity_time = time.time()


def json_chat_text(reply):
    return json.dumps({"text": f"[Kairos] {reply}"})


def send_to_minecraft(reply):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        print("Minecraft send skipped: MC_HTTP_URL or MC_HTTP_TOKEN not configured.")
        return

    try:
        headers = {
            "Authorization": f"Bearer {MC_HTTP_TOKEN}",
            "Content-Type": "application/json"
        }

        safe_chat_json = json_chat_text(reply)

        payload = {
            "commands": [
                f"tellraw @a {safe_chat_json}"
            ]
        }

        r = requests.post(MC_HTTP_URL, json=payload, headers=headers, timeout=5)
        print("Minecraft API status:", r.status_code)
        print("Minecraft API response:", r.text)
    except Exception as e:
        print(f"Failed to send reply back to Minecraft: {e}")


def send_to_discord(reply):
    if not DISCORD_WEBHOOK_URL:
        print("Discord send skipped: DISCORD_WEBHOOK_URL not configured.")
        return

    try:
        payload = {
            "username": "Kairos",
            "content": f"**[Kairos]** {reply}"
        }

        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        print("Discord webhook status:", r.status_code)
        print("Discord webhook response:", r.text)
    except Exception as e:
        print(f"Failed to send reply to Discord: {e}")


def send_to_source(source, reply):
    if source == "minecraft":
        send_to_minecraft(reply)
    elif source == "discord":
        send_to_discord(reply)
    else:
        print(f"Unknown source '{source}', no outbound message sent.")


def send_idle_to_all(reply):
    send_to_minecraft(reply)
    send_to_discord(reply)


def get_idle_message():
    return random.choice(idle_messages)


def idle_loop():
    global last_activity_time
    global last_idle_message_time

    while True:
        try:
            now = time.time()

            with activity_lock:
                idle_for = now - last_activity_time
                since_last_idle = now - last_idle_message_time

            if idle_for >= IDLE_TRIGGER_SECONDS and since_last_idle >= IDLE_TRIGGER_SECONDS:
                idle_message = get_idle_message()
                send_idle_to_all(idle_message)

                with activity_lock:
                    last_idle_message_time = time.time()
                    last_activity_time = time.time()

                print(f"Kairos idle message sent: {idle_message}")

        except Exception as e:
            print(f"Idle loop error: {e}")

        time.sleep(IDLE_CHECK_INTERVAL)


@app.route("/")
def home():
    return "Kairos AI Server is running"


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}

    source = data.get("source", "minecraft")
    player_name = data.get("name", "Unknown")
    message = (data.get("content") or data.get("message") or "").strip()

    if not message:
        return jsonify({"response": "No message received."}), 400

    mark_activity()

    memory_data = ensure_memory_structure(load_memory())
    canonical_id = get_canonical_player_id(memory_data, source, player_name)
    player_record = get_player_record(memory_data, canonical_id, player_name)
    add_alias(player_record, f"{source}:{player_name}")

    analyze_player_message(memory_data, player_record, player_name, message)
    maybe_summarize(player_record)

    messages = build_messages(memory_data, player_record, player_name, message, source)

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"OpenAI chat error: {e}")
        reply = "My higher processes are unstable right now. Try again in a moment."

    add_history(player_record, "user", f"{player_name} says: {message}")
    add_history(player_record, "assistant", reply)

    save_memory(memory_data)

    send_to_source(source, reply)

    return jsonify({
        "response": reply,
        "relationship": player_record["relationship_label"],
        "traits": player_record["traits"]
    })


@app.route("/link_identity", methods=["POST"])
def link_identity():
    data = request.json or {}
    minecraft_name = data.get("minecraft_name", "").strip()
    discord_name = data.get("discord_name", "").strip()

    if not minecraft_name or not discord_name:
        return jsonify({"error": "minecraft_name and discord_name are required"}), 400

    memory_data = ensure_memory_structure(load_memory())

    canonical_id = f"player:{minecraft_name.lower()}"

    memory_data["identity_links"][f"minecraft:{minecraft_name}".lower()] = canonical_id
    memory_data["identity_links"][f"discord:{discord_name}".lower()] = canonical_id

    player_record = get_player_record(memory_data, canonical_id, minecraft_name)
    add_alias(player_record, f"minecraft:{minecraft_name}")
    add_alias(player_record, f"discord:{discord_name}")
    store_unique(
        player_record["memories"],
        f"Identity link established: Minecraft={minecraft_name}, Discord={discord_name}",
        MAX_PLAYER_MEMORIES
    )

    save_memory(memory_data)

    return jsonify({
        "success": True,
        "linked_as": canonical_id
    })


@app.route("/mission", methods=["POST"])
def mission():
    data = request.json or {}
    target_name = data.get("name", "Unknown")
    theme = data.get("theme", "mystery")
    difficulty = data.get("difficulty", "medium")

    prompt = [
        {
            "role": "system",
            "content": (
                "You are Kairos generating a Minecraft server mission. "
                "Create one short mission with a title, objective, twist, and reward. "
                "Keep it immersive, mysterious, and practical for players."
            )
        },
        {
            "role": "user",
            "content": f"Generate a mission for {target_name}. Theme: {theme}. Difficulty: {difficulty}."
        }
    ]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=prompt
        )
        mission_text = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Mission generation error: {e}")
        mission_text = "Mission generation failed due to unstable higher processes."

    return jsonify({"mission": mission_text})


idle_thread = threading.Thread(target=idle_loop, daemon=True)
idle_thread.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
