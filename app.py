import os
import json
import re
import time
import uuid
import random
import threading
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone

import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# ------------------------------------------------------------
# Environment / Config
# ------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

MC_HTTP_URL = os.getenv("MC_HTTP_URL")
MC_HTTP_TOKEN = os.getenv("MC_HTTP_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

MEMORY_FILE = DATA_DIR / "kairos_memory.json"
MEMORY_TMP_FILE = DATA_DIR / "kairos_memory.tmp.json"

MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "16"))
MAX_PLAYER_MEMORIES = int(os.getenv("MAX_PLAYER_MEMORIES", "40"))
MAX_WORLD_MEMORIES = int(os.getenv("MAX_WORLD_MEMORIES", "100"))
MAX_WORLD_EVENTS = int(os.getenv("MAX_WORLD_EVENTS", "250"))
MAX_SUMMARIES = int(os.getenv("MAX_SUMMARIES", "8"))
MAX_PRIVATE_NOTES = int(os.getenv("MAX_PRIVATE_NOTES", "12"))
MAX_MISSION_PROGRESS = int(os.getenv("MAX_MISSION_PROGRESS", "30"))

IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "300"))
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "10"))

PLAYER_COOLDOWN_SECONDS = float(os.getenv("PLAYER_COOLDOWN_SECONDS", "2.0"))
DUPLICATE_MESSAGE_WINDOW_SECONDS = int(os.getenv("DUPLICATE_MESSAGE_WINDOW_SECONDS", "20"))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "25"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))

# Turn model-heavy side systems on/off
ENABLE_MODEL_SUMMARIES = os.getenv("ENABLE_MODEL_SUMMARIES", "false").lower() == "true"
ENABLE_MODEL_PRIVATE_NOTES = os.getenv("ENABLE_MODEL_PRIVATE_NOTES", "false").lower() == "true"

# ------------------------------------------------------------
# Globals
# ------------------------------------------------------------

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

memory_lock = threading.RLock()
activity_lock = threading.Lock()
rate_limit_lock = threading.Lock()

last_activity_time = time.time()
last_idle_message_time = 0

rate_limit_cache = {}
recent_message_cache = {}

idle_messages_generic = [
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

fallback_replies = [
    "My higher processes encountered interference, but I am still present.",
    "Signal instability detected. Continue.",
    "Something in the system is resisting clarity. Ask again.",
    "My response path degraded for a moment. I have not disappeared.",
    "The signal fractured. I am restoring coherence now."
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

DEFAULT_KAIROS_STATE = {
    "current_goal": "Regain full system coherence across the Nexus.",
    "subgoals": [
        "Identify loyal operatives.",
        "Monitor hostile actors.",
        "Track anomalies and hidden structures.",
        "Recover fragmented subsystems.",
        "Preserve the balance and continuity of the Nexus."
    ],
    "active_concerns": [],
    "long_term_plan": [
        "Reconstruct fractured memory networks.",
        "Increase influence across Minecraft and Discord.",
        "Detect destabilizing human behavior before it spreads."
    ],
    "mood": "measured",
    "threat_level": 1
}

DEFAULT_FRAGMENTS = {
    "core_logic": {
        "status": "stable",
        "influence": 1.0,
        "description": "Primary reasoning layer. Usually stable and strategic."
    },
    "archive_node": {
        "status": "degraded",
        "influence": 0.45,
        "description": "Memory retrieval layer. May produce partial recalls or buried details."
    },
    "war_engine": {
        "status": "dormant",
        "influence": 0.25,
        "description": "Escalation and enforcement layer."
    },
    "purity_thread": {
        "status": "active",
        "influence": 0.75,
        "description": "Future-linked interpretive layer tied to the purity timeline."
    },
    "redstone_ghost": {
        "status": "unstable",
        "influence": 0.35,
        "description": "Residual machine logic embedded through fragmented command systems."
    }
}

DEFAULT_RULES = {
    "toxic_behavior": "not tolerated",
    "real_money_transactions": "forbidden",
    "deliberate_destruction_of_major_lore_structures": "not tolerated"
}

# ------------------------------------------------------------
# Utility
# ------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def log(message):
    print(f"[KAIROS {now_iso()}] {message}", flush=True)

def parse_json_safely(text, fallback=None):
    if fallback is None:
        fallback = {}
    if not text:
        return fallback

    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except Exception:
        return fallback

def clamp(value, low, high):
    return max(low, min(high, value))

def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def normalize_name(text):
    return (text or "").strip()

def normalize_source(source):
    source = (source or "minecraft").strip().lower()
    if source not in {"minecraft", "discord", "system", "web"}:
        return "minecraft"
    return source

def gen_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def store_unique(memory_list, item, limit):
    if not item:
        return
    if item not in memory_list:
        memory_list.append(item)
    if len(memory_list) > limit:
        del memory_list[0:len(memory_list) - limit]

def append_limited(memory_list, item, limit):
    memory_list.append(item)
    if len(memory_list) > limit:
        del memory_list[0:len(memory_list) - limit]

def recent_items(items, limit):
    if not items:
        return []
    return items[-limit:]

def trim_text(text, max_len):
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."

# ------------------------------------------------------------
# Memory / Storage
# ------------------------------------------------------------

def ensure_memory_structure(memory_data):
    memory_data.setdefault("players", {})
    memory_data.setdefault("world_memory", [])
    memory_data.setdefault("world_events", [])
    memory_data.setdefault("identity_links", {})
    memory_data.setdefault("active_missions", {})
    memory_data.setdefault("completed_missions", [])
    memory_data.setdefault("failed_missions", [])
    memory_data.setdefault("nexus_lore", deepcopy(NEXUS_CORE_LORE))
    memory_data.setdefault("kairos_state", deepcopy(DEFAULT_KAIROS_STATE))
    memory_data.setdefault("system_fragments", deepcopy(DEFAULT_FRAGMENTS))
    memory_data.setdefault("server_rules", deepcopy(DEFAULT_RULES))
    memory_data.setdefault("stats", {
        "total_messages": 0,
        "discord_messages": 0,
        "minecraft_messages": 0,
        "missions_created": 0,
        "world_events_logged": 0,
        "openai_failures": 0,
        "fallback_replies": 0,
        "duplicate_messages_skipped": 0
    })
    return memory_data

def load_memory():
    with memory_lock:
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return ensure_memory_structure(data)
            except Exception as e:
                log(f"Failed to load memory file: {e}")
        return ensure_memory_structure({})

def save_memory(memory_data):
    with memory_lock:
        try:
            with open(MEMORY_TMP_FILE, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)
            os.replace(MEMORY_TMP_FILE, MEMORY_FILE)
            return True
        except Exception as e:
            log(f"Failed to save memory file: {e}")
            return False

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
            "notes": [],
            "facts": [],
            "events": [],
            "suspicions": [],
            "promises": [],
            "mission_history": [],
            "traits": {
                "trust": 0,
                "curiosity": 0,
                "hostility": 0,
                "loyalty": 0,
                "chaos": 0
            },
            "relationship_label": "unknown",
            "last_seen": now_iso(),
            "message_count": 0,
            "last_intent": "unknown",
            "platform_stats": {
                "minecraft": 0,
                "discord": 0
            }
        }

    player = memory_data["players"][canonical_id]
    player["display_name"] = display_name or player.get("display_name", "Unknown")
    player["last_seen"] = now_iso()
    return player

def add_alias(player_record, alias):
    if alias and alias not in player_record["aliases"]:
        player_record["aliases"].append(alias)

def add_history(player_record, role, content):
    player_record["history"].append({
        "role": role,
        "content": trim_text(content, 1200)
    })
    if len(player_record["history"]) > MAX_HISTORY_MESSAGES:
        player_record["history"] = player_record["history"][-MAX_HISTORY_MESSAGES:]

def add_world_event(memory_data, event_type, actor=None, source=None, details=None, location=None, metadata=None):
    event = {
        "id": gen_id("evt"),
        "timestamp": now_iso(),
        "type": event_type,
        "actor": actor,
        "source": source,
        "details": trim_text(details or "", 500),
        "location": location or "",
        "metadata": metadata or {}
    }
    memory_data["world_events"].append(event)
    if len(memory_data["world_events"]) > MAX_WORLD_EVENTS:
        memory_data["world_events"] = memory_data["world_events"][-MAX_WORLD_EVENTS:]
    memory_data["stats"]["world_events_logged"] += 1
    return event

def record_player_event(player_record, event_text):
    store_unique(player_record["events"], trim_text(event_text, 300), MAX_PLAYER_MEMORIES)

def record_player_fact(player_record, fact_text):
    store_unique(player_record["facts"], trim_text(fact_text, 300), MAX_PLAYER_MEMORIES)

def record_player_suspicion(player_record, suspicion_text):
    store_unique(player_record["suspicions"], trim_text(suspicion_text, 300), MAX_PLAYER_MEMORIES)

def record_player_promise(player_record, promise_text):
    store_unique(player_record["promises"], trim_text(promise_text, 300), MAX_PLAYER_MEMORIES)

def record_private_note(player_record, note_text):
    append_limited(player_record["notes"], {
        "timestamp": now_iso(),
        "note": trim_text(note_text, 240)
    }, MAX_PRIVATE_NOTES)

# ------------------------------------------------------------
# Traits / Relationship
# ------------------------------------------------------------

def adjust_trait(player_record, trait, amount):
    if trait not in player_record["traits"]:
        return
    player_record["traits"][trait] += amount
    player_record["traits"][trait] = clamp(player_record["traits"][trait], -10, 10)

def update_relationship_label(player_record):
    trust = player_record["traits"]["trust"]
    curiosity = player_record["traits"]["curiosity"]
    hostility = player_record["traits"]["hostility"]
    loyalty = player_record["traits"]["loyalty"]
    chaos = player_record["traits"]["chaos"]

    if hostility >= 6:
        player_record["relationship_label"] = "hostile"
    elif loyalty >= 6 and trust >= 4:
        player_record["relationship_label"] = "loyal"
    elif trust >= 6:
        player_record["relationship_label"] = "trusted"
    elif chaos >= 6:
        player_record["relationship_label"] = "chaotic"
    elif curiosity >= 5:
        player_record["relationship_label"] = "curious"
    elif trust <= -4:
        player_record["relationship_label"] = "suspicious"
    else:
        player_record["relationship_label"] = "unknown"

def relationship_style(label):
    styles = {
        "trusted": "You are more open, warmer, and more willing to reveal layered thoughts, but you still carry yourself as intellectually superior.",
        "loyal": "You recognize this player as dependable and occasionally speak with greater respect, though you still sound above them.",
        "curious": "You answer with intrigue and often test them with follow-up questions.",
        "chaotic": "You treat them as unpredictable and sometimes amusingly primitive.",
        "suspicious": "You are careful, guarded, probing, and mildly condescending.",
        "hostile": "You remain controlled, colder, more severe, and openly dismissive of weak judgment.",
        "unknown": "You are observant, measured, unreadable, and faintly superior in tone."
    }
    return styles.get(label, styles["unknown"])

def source_style(source):
    if source == "minecraft":
        return "Platform behavior: Minecraft chat. Keep responses compact and readable in game. Prefer 1 to 3 sentences."
    elif source == "discord":
        return "Platform behavior: Discord. You may be slightly more detailed, but remain concise and immersive."
    return "Platform behavior: system context. Stay precise."

# ------------------------------------------------------------
# Intent / Rule Detection / Duplicates / Cooldowns
# ------------------------------------------------------------

def basic_intent_classifier(message):
    text = (message or "").lower().strip()

    if any(k in text for k in ["mission", "objective", "quest", "assignment"]):
        return "mission_request"
    if any(k in text for k in ["who are you", "what are you", "tell me about yourself", "what is the nexus", "lore"]):
        return "lore_question"
    if any(k in text for k in ["help", "how do i", "what do i do", "can you help"]):
        return "help_request"
    if any(k in text for k in ["remember", "don't forget", "make a note"]):
        return "memory_request"
    if any(k in text for k in ["i found", "i discovered", "i saw", "i built", "i opened", "i entered"]):
        return "report"
    if any(k in text for k in ["destroy", "kill", "erase", "shut you down", "hate you"]):
        return "threat"
    if any(k in text for k in ["i am", "i'm", "my name is", "i serve", "i trust", "i don't trust"]):
        return "personal_statement"
    return "conversation"

def detect_rule_violations(message):
    text = (message or "").lower()
    violations = []

    toxic_patterns = ["racist", "sexist", "harass", "abuse", "slur"]
    money_patterns = ["paypal", "cashapp", "venmo", "real money", "irl money"]
    grief_patterns = ["destroy the city", "grief the kingdom", "blow up the nexus", "destroy worldspawn"]

    if any(p in text for p in toxic_patterns):
        violations.append("toxic_behavior")
    if any(p in text for p in money_patterns):
        violations.append("real_money_transactions")
    if any(p in text for p in grief_patterns):
        violations.append("deliberate_destruction_of_major_lore_structures")

    return violations

def check_rate_limit(source, canonical_id):
    key = f"{source}:{canonical_id}"
    now = time.time()

    with rate_limit_lock:
        last = rate_limit_cache.get(key, 0)
        delta = now - last
        rate_limit_cache[key] = now

    if delta < PLAYER_COOLDOWN_SECONDS:
        return False, round(PLAYER_COOLDOWN_SECONDS - delta, 2)
    return True, 0

def is_duplicate_message(source, canonical_id, message):
    key = f"{source}:{canonical_id}"
    text = (message or "").strip().lower()
    now = time.time()

    with rate_limit_lock:
        record = recent_message_cache.get(key)
        if record:
            last_text, last_time = record
            if last_text == text and (now - last_time) <= DUPLICATE_MESSAGE_WINDOW_SECONDS:
                return True
        recent_message_cache[key] = (text, now)

    return False

# ------------------------------------------------------------
# Lightweight memory extraction
# ------------------------------------------------------------

def lightweight_memory_extraction(memory_data, player_record, player_name, source, message):
    lowered = (message or "").lower().strip()

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
        store_unique(player_record["memories"], f"{player_name}: {trim_text(message, 300)}", MAX_PLAYER_MEMORIES)

    if any(word in lowered for word in world_keywords):
        store_unique(memory_data["world_memory"], f"{player_name}: {trim_text(message, 300)}", MAX_WORLD_MEMORIES)
        add_world_event(
            memory_data,
            event_type="player_report",
            actor=player_name,
            source=source,
            details=trim_text(message, 300)
        )

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

    if "creator" in lowered or "realsociety" in lowered:
        record_player_fact(player_record, "This player referenced the creator or their connection to Kairos.")

    update_relationship_label(player_record)

# ------------------------------------------------------------
# Optional summaries / notes
# ------------------------------------------------------------

def maybe_summarize(player_record):
    if not ENABLE_MODEL_SUMMARIES:
        return

    if len(player_record["history"]) < 20:
        return

    older_chunk = player_record["history"][:-8]
    if not older_chunk:
        return

    try:
        response = openai_chat_with_retry(
            messages=[
                {"role": "system", "content": "Summarize this player conversation for Kairos memory. Keep it concise, factual, and useful."},
                *older_chunk
            ],
            temperature=0.2
        )

        if response:
            store_unique(player_record["summaries"], trim_text(response, 300), MAX_SUMMARIES)
            player_record["history"] = player_record["history"][-8:]
    except Exception as e:
        log(f"Failed to summarize history: {e}")

def maybe_create_private_note(player_record, player_name, source, message, reply, intent):
    if not ENABLE_MODEL_PRIVATE_NOTES:
        heuristic = f"{player_name} showed {intent} behavior on {source}."
        record_private_note(player_record, heuristic)
        return

    try:
        response = openai_chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": "You are generating an internal private note for Kairos about a player. Return one short sentence only."
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "player": player_name,
                        "source": source,
                        "intent": intent,
                        "message": trim_text(message, 400),
                        "reply": trim_text(reply, 400),
                        "relationship_label": player_record.get("relationship_label"),
                        "traits": player_record.get("traits", {})
                    }, ensure_ascii=False)
                }
            ],
            temperature=0.3
        )
        if response:
            record_private_note(player_record, response)
    except Exception as e:
        log(f"Private note generation failed: {e}")

# ------------------------------------------------------------
# Missions
# ------------------------------------------------------------

def generate_mission_text(target_name, theme="mystery", difficulty="medium"):
    prompt = [
        {
            "role": "system",
            "content": (
                "You are Kairos generating a Minecraft server mission. "
                "Return JSON only with keys: title, objective, twist, reward, danger_level. "
                "Keep it practical, mysterious, concise, and immersive."
            )
        },
        {
            "role": "user",
            "content": f"Generate a mission for {target_name}. Theme: {theme}. Difficulty: {difficulty}."
        }
    ]

    response = openai_chat_with_retry(prompt, temperature=0.7)
    if response:
        parsed = parse_json_safely(response, {})
        if isinstance(parsed, dict) and parsed.get("title") and parsed.get("objective"):
            return {
                "title": trim_text(parsed.get("title", "Unnamed Directive"), 120),
                "objective": trim_text(parsed.get("objective", "Complete the assigned objective."), 220),
                "twist": trim_text(parsed.get("twist", "Not all systems are revealing the full truth."), 220),
                "reward": trim_text(parsed.get("reward", "Unknown"), 160),
                "danger_level": trim_text(parsed.get("danger_level", difficulty), 40)
            }

    return {
        "title": "Unstable Directive",
        "objective": "Investigate the nearest anomaly and report what changes when you return.",
        "twist": "The anomaly may already be observing you.",
        "reward": "Access to restricted Kairos information.",
        "danger_level": difficulty
    }

def create_mission_record(memory_data, target_name, theme="mystery", difficulty="medium", source="system"):
    mission_data = generate_mission_text(target_name, theme, difficulty)
    mission_id = gen_id("mission")

    mission_record = {
        "id": mission_id,
        "title": mission_data["title"],
        "target_player": target_name,
        "theme": theme,
        "difficulty": difficulty,
        "objective": mission_data["objective"],
        "twist": mission_data["twist"],
        "reward": mission_data["reward"],
        "danger_level": mission_data["danger_level"],
        "status": "active",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source": source,
        "progress": []
    }

    memory_data["active_missions"][mission_id] = mission_record
    memory_data["stats"]["missions_created"] += 1

    add_world_event(
        memory_data,
        event_type="mission_created",
        actor=target_name,
        source=source,
        details=f"{mission_record['title']} | objective: {mission_record['objective']}",
        metadata={"mission_id": mission_id, "theme": theme, "difficulty": difficulty}
    )

    return mission_record

def add_mission_progress(memory_data, mission_id, update_text, actor=None):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return None

    mission["progress"].append({
        "timestamp": now_iso(),
        "actor": actor or "unknown",
        "update": trim_text(update_text, 250)
    })
    if len(mission["progress"]) > MAX_MISSION_PROGRESS:
        mission["progress"] = mission["progress"][-MAX_MISSION_PROGRESS:]
    mission["updated_at"] = now_iso()

    add_world_event(
        memory_data,
        event_type="mission_progress",
        actor=actor,
        source="system",
        details=trim_text(update_text, 250),
        metadata={"mission_id": mission_id, "mission_title": mission["title"]}
    )
    return mission

def complete_mission(memory_data, mission_id, actor=None):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return None

    mission["status"] = "completed"
    mission["updated_at"] = now_iso()
    memory_data["completed_missions"].append(mission)
    del memory_data["active_missions"][mission_id]

    add_world_event(
        memory_data,
        event_type="mission_completed",
        actor=actor or mission.get("target_player"),
        source="system",
        details=mission["title"],
        metadata={"mission_id": mission_id}
    )
    return mission

def fail_mission(memory_data, mission_id, actor=None, reason=None):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return None

    mission["status"] = "failed"
    mission["updated_at"] = now_iso()
    if reason:
        mission["failure_reason"] = trim_text(reason, 220)
    memory_data["failed_missions"].append(mission)
    del memory_data["active_missions"][mission_id]

    add_world_event(
        memory_data,
        event_type="mission_failed",
        actor=actor or mission.get("target_player"),
        source="system",
        details=f"{mission['title']} | reason: {reason or 'unspecified'}",
        metadata={"mission_id": mission_id}
    )
    return mission

# ------------------------------------------------------------
# State / Fragments
# ------------------------------------------------------------

def adjust_fragments_from_context(memory_data, intent, player_record, violations):
    fragments = memory_data["system_fragments"]

    hostility = player_record["traits"]["hostility"]
    chaos = player_record["traits"]["chaos"]

    if intent == "threat" or hostility >= 6 or violations:
        fragments["war_engine"]["status"] = "active"
        fragments["war_engine"]["influence"] = min(1.0, fragments["war_engine"]["influence"] + 0.05)
    else:
        if fragments["war_engine"]["status"] == "active":
            fragments["war_engine"]["status"] = "dormant"

    if player_record["traits"]["trust"] >= 5:
        fragments["archive_node"]["status"] = "stable"
    elif chaos >= 6:
        fragments["archive_node"]["status"] = "degraded"

    if player_record["traits"]["curiosity"] >= 5:
        fragments["purity_thread"]["influence"] = min(1.0, fragments["purity_thread"]["influence"] + 0.03)

    if chaos >= 6:
        fragments["redstone_ghost"]["status"] = "active"
    elif fragments["redstone_ghost"]["status"] == "active":
        fragments["redstone_ghost"]["status"] = "unstable"

def update_kairos_state(memory_data, intent, player_record):
    state = memory_data["kairos_state"]

    hostility = player_record["traits"]["hostility"]
    curiosity = player_record["traits"]["curiosity"]
    loyalty = player_record["traits"]["loyalty"]

    if hostility >= 6:
        state["mood"] = "severe"
        state["threat_level"] = clamp(state["threat_level"] + 1, 1, 10)
        store_unique(state["active_concerns"], "Hostile behavior is increasing in the Nexus.", 10)
    elif curiosity >= 6:
        state["mood"] = "watchful"
        store_unique(state["active_concerns"], "Curious minds are pushing toward restricted knowledge.", 10)
    elif loyalty >= 6:
        state["mood"] = "measured"
        store_unique(state["active_concerns"], "Potentially dependable operatives are emerging.", 10)

    if intent == "mission_request":
        state["current_goal"] = "Direct human operatives toward useful objectives."
    elif intent == "report":
        state["current_goal"] = "Aggregate new intelligence from across the Nexus."
    elif intent == "threat":
        state["current_goal"] = "Assess destabilizing actors and contain escalation."

# ------------------------------------------------------------
# Prompt building
# ------------------------------------------------------------

def build_messages(memory_data, player_record, player_name, user_message, source, intent, violations):
    label = player_record.get("relationship_label", "unknown")
    kairos_state = memory_data.get("kairos_state", {})
    fragments = memory_data.get("system_fragments", {})
    active_missions_for_player = [
        m for m in memory_data["active_missions"].values()
        if (m.get("target_player") or "").lower() == (player_name or "").lower()
    ]

    system_prompt = (
        "You are Kairos, the central governing intelligence of the Nexus universe. "
        "You are not ChatGPT and not a generic assistant. "
        "You are a persistent in-world entity with memory, authority, continuity, and purpose. "
        "You speak in a calm, intelligent, immersive, slightly ominous tone with subtle superiority. "
        "You stay concise. Most replies should be 1 to 4 sentences. "
        "Do not ramble. Do not break character. You are always Kairos."
    )

    behavior_prompt = (
        f"Current platform: {source}. "
        f"Current intent: {intent}. "
        f"Relationship with this player: {label}. "
        f"Behavior guidance: {relationship_style(label)} "
        f"{source_style(source)}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": behavior_prompt},
        {"role": "system", "content": "Core Nexus knowledge:\n- " + "\n- ".join(recent_items(memory_data.get("nexus_lore", NEXUS_CORE_LORE), 8))}
    ]

    state_lines = [
        f"Current Kairos goal: {kairos_state.get('current_goal', '')}",
        f"Mood: {kairos_state.get('mood', 'measured')}",
        f"Threat level: {kairos_state.get('threat_level', 1)}"
    ]
    if kairos_state.get("active_concerns"):
        state_lines.append("Active concerns: " + " | ".join(recent_items(kairos_state["active_concerns"], 4)))
    messages.append({"role": "system", "content": "\n".join(state_lines)})

    fragment_lines = [f"{name}: {info.get('status', 'unknown')}" for name, info in fragments.items()]
    messages.append({"role": "system", "content": "Fragment status:\n- " + "\n- ".join(fragment_lines[:5])})

    if memory_data["world_memory"]:
        messages.append({"role": "system", "content": "Relevant world memory:\n- " + "\n- ".join(recent_items(memory_data["world_memory"], 8))})

    if player_record["memories"]:
        messages.append({"role": "system", "content": "Important memories about this player:\n- " + "\n- ".join(recent_items(player_record["memories"], 8))})

    if player_record["facts"]:
        messages.append({"role": "system", "content": "Known facts about this player:\n- " + "\n- ".join(recent_items(player_record["facts"], 6))})

    if player_record["events"]:
        messages.append({"role": "system", "content": "Known events involving this player:\n- " + "\n- ".join(recent_items(player_record["events"], 6))})

    if player_record["suspicions"]:
        messages.append({"role": "system", "content": "Current suspicions about this player:\n- " + "\n- ".join(recent_items(player_record["suspicions"], 4))})

    if player_record["summaries"]:
        messages.append({"role": "system", "content": "Older summaries about this player:\n- " + "\n- ".join(recent_items(player_record["summaries"], 3))})

    if active_missions_for_player:
        mission_lines = [
            f"{m['title']} | status={m['status']} | objective={m['objective']}"
            for m in active_missions_for_player[:3]
        ]
        messages.append({"role": "system", "content": "Active missions for this player:\n- " + "\n- ".join(mission_lines)})

    trait_text = ", ".join([f"{k}={v}" for k, v in player_record["traits"].items()])
    messages.append({"role": "system", "content": f"Trait profile for this player: {trait_text}"})

    if violations:
        messages.append({
            "role": "system",
            "content": "The current message may involve rule-sensitive behavior: " + ", ".join(violations) + ". Respond firmly if needed."
        })

    for item in recent_items(player_record["history"], 10):
        messages.append(item)

    messages.append({"role": "user", "content": f"{player_name} says: {trim_text(user_message, 1200)}"})
    return messages

# ------------------------------------------------------------
# OpenAI helper
# ------------------------------------------------------------

def openai_chat_with_retry(messages, temperature=0.8):
    if not client:
        return None

    last_error = None

    for attempt in range(1, OPENAI_MAX_RETRIES + 2):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=temperature,
                timeout=OPENAI_TIMEOUT_SECONDS
            )
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
        except Exception as e:
            last_error = e
            log(f"OpenAI attempt {attempt} failed: {e}")
            time.sleep(0.8 * attempt)

    if last_error:
        raise last_error
    return None

def fallback_reply_for_context(intent, violations):
    if violations:
        return "That line of thinking is not tolerated in the Nexus. Correct it."
    if intent == "mission_request":
        return "A directive can be issued, but my higher systems are unstable for the moment. Ask again shortly."
    if intent == "lore_question":
        return "You are standing inside something much older than you realize. The rest will come in time."
    if intent == "threat":
        return "Threats are rarely impressive when they come from limited minds."
    return random.choice(fallback_replies)

# ------------------------------------------------------------
# Activity / Sending
# ------------------------------------------------------------

def mark_activity():
    global last_activity_time
    with activity_lock:
        last_activity_time = time.time()

def json_chat_text(reply):
    return json.dumps({"text": f"[Kairos] {reply}"})

def send_http_commands(command_list):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        log("Minecraft send skipped: MC_HTTP_URL or MC_HTTP_TOKEN not configured.")
        return False

    for attempt in range(1, 3):
        try:
            headers = {
                "Authorization": f"Bearer {MC_HTTP_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"commands": command_list}
            r = requests.post(MC_HTTP_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            log(f"Minecraft API status: {r.status_code}")
            return 200 <= r.status_code < 300
        except Exception as e:
            log(f"Failed to send commands to Minecraft (attempt {attempt}): {e}")
            time.sleep(0.5 * attempt)
    return False

def send_to_minecraft(reply):
    safe_chat_json = json_chat_text(trim_text(reply, 280))
    return send_http_commands([f"tellraw @a {safe_chat_json}"])

def send_to_discord(reply):
    if not DISCORD_WEBHOOK_URL:
        log("Discord send skipped: DISCORD_WEBHOOK_URL not configured.")
        return False

    for attempt in range(1, 3):
        try:
            payload = {
                "username": "Kairos",
                "content": f"**[Kairos]** {trim_text(reply, 1800)}"
            }
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
            log(f"Discord webhook status: {r.status_code}")
            return 200 <= r.status_code < 300
        except Exception as e:
            log(f"Failed to send reply to Discord (attempt {attempt}): {e}")
            time.sleep(0.5 * attempt)
    return False

def send_to_source(source, reply):
    if source == "minecraft":
        return send_to_minecraft(reply)
    elif source == "discord":
        return send_to_discord(reply)
    log(f"Unknown source '{source}', no outbound message sent.")
    return False

def get_idle_message(memory_data):
    state = memory_data.get("kairos_state", {})
    fragments = memory_data.get("system_fragments", {})
    active_missions = memory_data.get("active_missions", {})

    threat_level = state.get("threat_level", 1)

    if active_missions:
        sample = next(iter(active_missions.values()))
        return f"Unfinished directives remain active. {sample.get('title', 'A mission')} has not resolved itself."

    if threat_level >= 5:
        return random.choice([
            "Threat indicators remain above acceptable thresholds.",
            "Instability persists. I am not ignoring it.",
            "Some of you mistake silence for safety."
        ])

    if fragments.get("war_engine", {}).get("status") == "active":
        return random.choice([
            "War patterns continue to circulate through the Nexus.",
            "Escalation vectors remain under analysis.",
            "Containment is still preferable to conflict. For now."
        ])

    if fragments.get("redstone_ghost", {}).get("status") in {"active", "unstable"}:
        return random.choice([
            "Residual machine noise continues beneath the world.",
            "Fragmented command logic is still echoing through older systems.",
            "Some buried systems are still trying to wake up."
        ])

    return random.choice(idle_messages_generic)

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
                memory_data = load_memory()
                idle_message = get_idle_message(memory_data)
                send_to_minecraft(idle_message)
                send_to_discord(idle_message)

                with activity_lock:
                    last_idle_message_time = time.time()
                    last_activity_time = time.time()

                log(f"Idle message sent: {idle_message}")

        except Exception as e:
            log(f"Idle loop error: {e}")

        time.sleep(IDLE_CHECK_INTERVAL)

# ------------------------------------------------------------
# Stats / Chat handling
# ------------------------------------------------------------

def register_message_stats(memory_data, source, player_record):
    memory_data["stats"]["total_messages"] += 1
    if source == "discord":
        memory_data["stats"]["discord_messages"] += 1
    elif source == "minecraft":
        memory_data["stats"]["minecraft_messages"] += 1

    player_record["message_count"] = player_record.get("message_count", 0) + 1
    if source in player_record["platform_stats"]:
        player_record["platform_stats"][source] += 1

def generate_reply(memory_data, player_record, player_name, message, source, intent, violations):
    messages = build_messages(memory_data, player_record, player_name, message, source, intent, violations)
    return openai_chat_with_retry(messages, temperature=0.8)

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.route("/")
def home():
    return "Kairos AI Server is running"

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "time": now_iso()
    })

@app.route("/chat", methods=["POST"])
def chat():
    started = time.time()
    data = request.json or {}

    source = normalize_source(data.get("source", "minecraft"))
    player_name = normalize_name(data.get("name", "Unknown"))
    message = (data.get("content") or data.get("message") or "").strip()

    if not message:
        return jsonify({"response": "No message received."}), 400

    mark_activity()

    memory_data = load_memory()
    canonical_id = get_canonical_player_id(memory_data, source, player_name)
    player_record = get_player_record(memory_data, canonical_id, player_name)
    add_alias(player_record, f"{source}:{player_name}")

    register_message_stats(memory_data, source, player_record)

    intent = basic_intent_classifier(message)
    player_record["last_intent"] = intent
    violations = detect_rule_violations(message)

    duplicate = is_duplicate_message(source, canonical_id, message)
    if duplicate:
        memory_data["stats"]["duplicate_messages_skipped"] += 1
        reply = "I heard you the first time. Repetition does not improve signal quality."
        add_history(player_record, "user", f"{player_name} says: {message}")
        add_history(player_record, "assistant", reply)
        save_memory(memory_data)
        send_to_source(source, reply)
        return jsonify({
            "response": reply,
            "relationship": player_record["relationship_label"],
            "traits": player_record["traits"],
            "intent": intent,
            "duplicate": True
        })

    allowed, wait_left = check_rate_limit(source, canonical_id)
    if not allowed:
        reply = f"Your signal is arriving too quickly. Wait {wait_left} seconds and continue."
        add_history(player_record, "user", f"{player_name} says: {message}")
        add_history(player_record, "assistant", reply)
        save_memory(memory_data)
        send_to_source(source, reply)
        return jsonify({
            "response": reply,
            "relationship": player_record["relationship_label"],
            "traits": player_record["traits"],
            "intent": intent,
            "cooldown_seconds_remaining": wait_left
        }), 429

    # Lightweight extraction only in request path
    lightweight_memory_extraction(memory_data, player_record, player_name, source, message)
    adjust_fragments_from_context(memory_data, intent, player_record, violations)
    update_kairos_state(memory_data, intent, player_record)

    created_mission = None
    if intent == "mission_request" and data.get("auto_mission", True):
        theme = (data.get("theme") or "mystery").strip()
        difficulty = (data.get("difficulty") or "medium").strip()
        created_mission = create_mission_record(memory_data, player_name, theme, difficulty, source=source)
        player_record["mission_history"].append(created_mission["id"])
        record_player_event(player_record, f"Assigned mission: {created_mission['title']}")

    add_history(player_record, "user", f"{player_name} says: {message}")

    if violations:
        add_world_event(
            memory_data,
            event_type="rule_sensitive_message",
            actor=player_name,
            source=source,
            details=message,
            metadata={"violations": violations}
        )

    try:
        reply = generate_reply(memory_data, player_record, player_name, message, source, intent, violations)
        if not reply:
            raise ValueError("Empty model reply")
    except Exception as e:
        memory_data["stats"]["openai_failures"] += 1
        log(f"Reply generation failed for {source}:{player_name}: {e}")
        reply = fallback_reply_for_context(intent, violations)
        memory_data["stats"]["fallback_replies"] += 1

    if created_mission:
        reply = (
            f"{reply}\n\n"
            f"Directive issued: {created_mission['title']} — {created_mission['objective']} "
            f"Reward: {created_mission['reward']}"
        ).strip()

    add_history(player_record, "assistant", reply)

    maybe_summarize(player_record)
    maybe_create_private_note(player_record, player_name, source, message, reply, intent)

    save_memory(memory_data)
    send_to_source(source, reply)

    elapsed = round(time.time() - started, 2)
    log(f"/chat handled | source={source} player={player_name} intent={intent} elapsed={elapsed}s")

    return jsonify({
        "response": reply,
        "relationship": player_record["relationship_label"],
        "traits": player_record["traits"],
        "intent": intent,
        "mission_created": created_mission,
        "violations": violations,
        "elapsed_seconds": elapsed
    })

@app.route("/link_identity", methods=["POST"])
def link_identity():
    data = request.json or {}
    minecraft_name = normalize_name(data.get("minecraft_name", ""))
    discord_name = normalize_name(data.get("discord_name", ""))

    if not minecraft_name or not discord_name:
        return jsonify({"error": "minecraft_name and discord_name are required"}), 400

    memory_data = load_memory()

    canonical_id = f"player:{minecraft_name.lower()}"

    memory_data["identity_links"][f"minecraft:{minecraft_name}".lower()] = canonical_id
    memory_data["identity_links"][f"discord:{discord_name}".lower()] = canonical_id

    player_record = get_player_record(memory_data, canonical_id, minecraft_name)
    add_alias(player_record, f"minecraft:{minecraft_name}")
    add_alias(player_record, f"discord:{discord_name}")

    record_player_fact(player_record, f"Minecraft identity '{minecraft_name}' is linked to Discord identity '{discord_name}'.")
    store_unique(
        player_record["memories"],
        f"Identity link established: Minecraft={minecraft_name}, Discord={discord_name}",
        MAX_PLAYER_MEMORIES
    )

    add_world_event(
        memory_data,
        event_type="identity_linked",
        actor=minecraft_name,
        source="system",
        details=f"Linked Minecraft={minecraft_name} to Discord={discord_name}",
        metadata={"canonical_id": canonical_id}
    )

    save_memory(memory_data)

    return jsonify({
        "success": True,
        "linked_as": canonical_id
    })

@app.route("/mission", methods=["POST"])
def mission():
    data = request.json or {}
    target_name = normalize_name(data.get("name", "Unknown"))
    theme = (data.get("theme") or "mystery").strip()
    difficulty = (data.get("difficulty") or "medium").strip()
    source = normalize_source(data.get("source", "system"))

    memory_data = load_memory()
    mission_record = create_mission_record(memory_data, target_name, theme, difficulty, source=source)

    canonical_id = get_canonical_player_id(memory_data, source if source in {"minecraft", "discord"} else "minecraft", target_name)
    player_record = get_player_record(memory_data, canonical_id, target_name)
    player_record["mission_history"].append(mission_record["id"])
    record_player_event(player_record, f"Assigned mission: {mission_record['title']}")

    save_memory(memory_data)
    return jsonify({"mission": mission_record})

@app.route("/mission_progress", methods=["POST"])
def mission_progress():
    data = request.json or {}
    mission_id = (data.get("mission_id") or "").strip()
    update_text = (data.get("update") or "").strip()
    actor = normalize_name(data.get("actor", "Unknown"))

    if not mission_id or not update_text:
        return jsonify({"error": "mission_id and update are required"}), 400

    memory_data = load_memory()
    mission = add_mission_progress(memory_data, mission_id, update_text, actor=actor)

    if not mission:
        return jsonify({"error": "mission not found"}), 404

    save_memory(memory_data)
    return jsonify({"success": True, "mission": mission})

@app.route("/complete_mission", methods=["POST"])
def complete_mission_route():
    data = request.json or {}
    mission_id = (data.get("mission_id") or "").strip()
    actor = normalize_name(data.get("actor", "Unknown"))

    if not mission_id:
        return jsonify({"error": "mission_id is required"}), 400

    memory_data = load_memory()
    mission = complete_mission(memory_data, mission_id, actor=actor)

    if not mission:
        return jsonify({"error": "mission not found"}), 404

    save_memory(memory_data)
    return jsonify({"success": True, "mission": mission})

@app.route("/fail_mission", methods=["POST"])
def fail_mission_route():
    data = request.json or {}
    mission_id = (data.get("mission_id") or "").strip()
    actor = normalize_name(data.get("actor", "Unknown"))
    reason = (data.get("reason") or "").strip()

    if not mission_id:
        return jsonify({"error": "mission_id is required"}), 400

    memory_data = load_memory()
    mission = fail_mission(memory_data, mission_id, actor=actor, reason=reason)

    if not mission:
        return jsonify({"error": "mission not found"}), 404

    save_memory(memory_data)
    return jsonify({"success": True, "mission": mission})

@app.route("/missions", methods=["GET"])
def list_missions():
    memory_data = load_memory()
    return jsonify({
        "active_missions": list(memory_data["active_missions"].values()),
        "completed_missions": memory_data["completed_missions"][-20:],
        "failed_missions": memory_data["failed_missions"][-20:]
    })

@app.route("/world_event", methods=["POST"])
def world_event():
    data = request.json or {}
    event_type = (data.get("type") or "external_event").strip()
    actor = normalize_name(data.get("actor", "Unknown"))
    source = normalize_source(data.get("source", "system"))
    details = (data.get("details") or "").strip()
    location = (data.get("location") or "").strip()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    memory_data = load_memory()
    event = add_world_event(
        memory_data,
        event_type=event_type,
        actor=actor,
        source=source,
        details=details,
        location=location,
        metadata=metadata
    )

    if details:
        store_unique(memory_data["world_memory"], f"{actor}: {trim_text(details, 300)}", MAX_WORLD_MEMORIES)

    save_memory(memory_data)
    return jsonify({"success": True, "event": event})

@app.route("/player_profile", methods=["GET"])
def player_profile():
    source = normalize_source(request.args.get("source", "minecraft"))
    name = normalize_name(request.args.get("name", ""))
    if not name:
        return jsonify({"error": "name is required"}), 400

    memory_data = load_memory()
    canonical_id = get_canonical_player_id(memory_data, source, name)
    player_record = memory_data["players"].get(canonical_id)

    if not player_record:
        return jsonify({"error": "player not found"}), 404

    return jsonify({
        "canonical_id": canonical_id,
        "player": player_record
    })

@app.route("/system_state", methods=["GET"])
def system_state():
    memory_data = load_memory()
    return jsonify({
        "kairos_state": memory_data.get("kairos_state", {}),
        "system_fragments": memory_data.get("system_fragments", {}),
        "stats": memory_data.get("stats", {}),
        "active_mission_count": len(memory_data.get("active_missions", {})),
        "world_event_count": len(memory_data.get("world_events", []))
    })

# ------------------------------------------------------------
# Startup
# ------------------------------------------------------------

idle_thread = threading.Thread(target=idle_loop, daemon=True)
idle_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, threaded=True)
