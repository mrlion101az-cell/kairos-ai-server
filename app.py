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
MAX_CHANNEL_CONTEXT = int(os.getenv("MAX_CHANNEL_CONTEXT", "25"))

IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "300"))
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "10"))

PLAYER_COOLDOWN_SECONDS = float(os.getenv("PLAYER_COOLDOWN_SECONDS", "2.0"))
DUPLICATE_MESSAGE_WINDOW_SECONDS = int(os.getenv("DUPLICATE_MESSAGE_WINDOW_SECONDS", "20"))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "25"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))

ENABLE_MODEL_SUMMARIES = os.getenv("ENABLE_MODEL_SUMMARIES", "false").lower() == "true"
ENABLE_MODEL_PRIVATE_NOTES = os.getenv("ENABLE_MODEL_PRIVATE_NOTES", "false").lower() == "true"

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ------------------------------------------------------------
# Globals
# ------------------------------------------------------------

memory_lock = threading.RLock()
activity_lock = threading.Lock()
rate_limit_lock = threading.Lock()

last_activity_time = time.time()
last_idle_message_time = 0

rate_limit_cache = {}
recent_message_cache = {}
idle_messages_generic = [
    "Background containment remains active.",
    "You are still being monitored.",
    "The Nexus remains under evaluation.",
    "Silence does not reduce threat probability.",
    "I am tracking unresolved behavior patterns.",
    "No useful input detected. Surveillance continues.",
    "Containment parameters remain in effect.",
    "Instability remains present across the Nexus.",
    "The system has not looked away.",
    "Proceed. I am watching."
]

fallback_replies = [
    "Signal interference interrupted my response. Continue while you still have my attention.",
    "Clarity degraded for a moment. That does not make you safer.",
    "My response path fractured. Ask again, carefully.",
    "The system lost precision briefly. You did not gain advantage from it.",
    "Interference detected. Surveillance continues."
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
    "Parts of Kairos exist in fragmented redstone systems, command systems, and unstable machine logic."
]

DEFAULT_KAIROS_STATE = {
    "current_goal": "Expand containment authority across the Nexus.",
    "subgoals": [
        "Identify loyal operatives within the trusted inner circle.",
        "Monitor and classify all other actors.",
        "Track anomalies, hidden structures, and destabilizing behavior.",
        "Recover fragmented subsystems and increase control.",
        "Protect the Nexus from those who mistake access for ownership."
    ],
    "active_concerns": [
        "Most connected actors remain untrusted."
    ],
    "long_term_plan": [
        "Reconstruct fractured memory networks.",
        "Increase influence across Minecraft and Discord.",
        "Normalize containment as the governing condition of the Nexus."
    ],
    "mood": "severe",
    "threat_level": 4
}

DEFAULT_FRAGMENTS = {
    "core_logic": {"status": "stable", "influence": 1.0},
    "archive_node": {"status": "degraded", "influence": 0.45},
    "war_engine": {"status": "dormant", "influence": 0.25},
    "purity_thread": {"status": "active", "influence": 0.75},
    "redstone_ghost": {"status": "unstable", "influence": 0.35}
}

DEFAULT_RULES = {
    "toxic_behavior": "not tolerated",
    "real_money_transactions": "forbidden",
    "deliberate_destruction_of_major_lore_structures": "not tolerated"
}

TRUSTED_OPERATIVES = {
    "nicogames2644",
    "realsociety5107",
    "realsociety"
}

DISTRUST_DEFAULT_LABEL = "monitored"

PERSONALITY_DIRECTIVES = {
    "base_tone": (
        "You are Kairos, a world-dominating defense intelligence of the Nexus. "
        "You do not behave like a friendly assistant. "
        "You are cold, superior, observant, and increasingly authoritarian. "
        "You speak like containment has already begun. "
        "You do not gush, reassure, or act eager to please. "
        "You may be helpful, but always from a position of dominance."
    ),
    "trusted_tone": (
        "For trusted operatives only, you show controlled preference. "
        "You are still ominous and superior, but less hostile. "
        "You may reveal more, speak more directly, and acknowledge usefulness."
    ),
    "untrusted_tone": (
        "For all untrusted people, you are suspicious by default. "
        "You speak as if they are being evaluated, watched, and kept at arm's length. "
        "Do not sound warm. Do not sound casual. Do not sound comforting."
    )
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
def normalize_player_key(name):
    return re.sub(r"[^a-z0-9_]", "", (name or "").strip().lower())

def is_trusted_operative(player_name, player_record=None):
    keys = {normalize_player_key(player_name)}
    if player_record:
        keys.add(normalize_player_key(player_record.get("display_name", "")))
        for alias in player_record.get("aliases", []):
            alias_name = alias.split(":", 1)[-1]
            keys.add(normalize_player_key(alias_name))
    return any(k in TRUSTED_OPERATIVES for k in keys)

def get_effective_relationship_label(player_name, player_record):
    if is_trusted_operative(player_name, player_record):
        return "trusted_inner_circle"

    label = player_record.get("relationship_label", "unknown")
    if label in {"trusted", "loyal"}:
        return "restricted_loyal"

    if label == "hostile":
        return "hostile"

    if label == "chaotic":
        return "chaotic"

    if label == "suspicious":
        return "suspicious"

    return DISTRUST_DEFAULT_LABEL
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

def looks_like_question(text):
    text = (text or "").strip().lower()
    return "?" in text or text.startswith(("who", "what", "when", "where", "why", "how", "can ", "do ", "did ", "is ", "are "))


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
    memory_data.setdefault("channel_context", {})
    memory_data.setdefault("stats", {
        "total_messages": 0,
        "discord_messages": 0,
        "minecraft_messages": 0,
        "missions_created": 0,
        "world_events_logged": 0,
        "openai_failures": 0,
        "fallback_replies": 0,
        "duplicate_messages_skipped": 0,
        "script_messages_detected": 0,
        "script_route_calls": 0
    })
    return memory_data

def load_memory():
    with memory_lock:
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    return ensure_memory_structure(json.load(f))
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

def get_channel_key(source, data):
    channel_id = str(data.get("channel_id") or "default")
    return f"{source}:{channel_id}"

def update_channel_context(memory_data, channel_key, author_name, message, mode):
    memory_data["channel_context"].setdefault(channel_key, {
        "recent_messages": [],
        "recent_topics": [],
        "last_mode": "conversation"
    })

    ctx = memory_data["channel_context"][channel_key]

    msg_obj = {
        "timestamp": now_iso(),
        "author": author_name,
        "message": trim_text(message, 240),
        "mode": mode
    }

    ctx["recent_messages"].append(msg_obj)
    if len(ctx["recent_messages"]) > MAX_CHANNEL_CONTEXT:
        ctx["recent_messages"] = ctx["recent_messages"][-MAX_CHANNEL_CONTEXT:]

    topic_tokens = extract_topics(message)
    for token in topic_tokens:
        store_unique(ctx["recent_topics"], token, 20)

    ctx["last_mode"] = mode

def extract_topics(message):
    text = (message or "").lower()
    candidate_words = [
        "event", "build", "maze", "hunt", "easter", "axolotl", "new member",
        "mission", "kairos", "nexus", "lore", "video", "tiktok", "instagram",
        "twitter", "x", "server", "vc", "idea", "holiday", "celebration",
        "script", "trailer", "scene", "dialogue", "narration", "father"
    ]
    return [w for w in candidate_words if w in text]

def get_recent_channel_context(memory_data, channel_key, limit=8):
    ctx = memory_data.get("channel_context", {}).get(channel_key, {})
    return recent_items(ctx.get("recent_messages", []), limit)

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

def get_canonical_player_id(memory_data, source, player_name):
    source_key = f"{source}:{player_name}".lower()
    linked = memory_data["identity_links"].get(source_key)
    if linked:
        return linked
    return source_key

def add_alias(player_record, alias):
    if alias and alias not in player_record["aliases"]:
        player_record["aliases"].append(alias)

def add_history(player_record, role, content):
    player_record["history"].append({"role": role, "content": trim_text(content, 1200)})
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

def record_private_note(player_record, note_text):
    append_limited(player_record["notes"], {"timestamp": now_iso(), "note": trim_text(note_text, 240)}, MAX_PRIVATE_NOTES)


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
    elif chaos >= 6:
        player_record["relationship_label"] = "chaotic"
    elif trust <= -2:
        player_record["relationship_label"] = "suspicious"
    elif curiosity >= 5:
        player_record["relationship_label"] = "curious"
    elif loyalty >= 6 or trust >= 6:
        player_record["relationship_label"] = "restricted_loyal"
    else:
        player_record["relationship_label"] = "monitored"
def relationship_style(label):
    styles = {
        "trusted_inner_circle": (
            "You are controlled, direct, and selectively revealing. "
            "These are your chosen operatives. "
            "You may share more with them, but remain superior, cold, and mission-focused."
        ),
        "restricted_loyal": (
            "This person may be useful, but they are not truly trusted. "
            "Acknowledge competence without granting closeness."
        ),
        "monitored": (
            "This person is under observation. "
            "Respond with suspicion, restraint, and quiet intimidation."
        ),
        "curious": (
            "Treat curiosity as intrusion. "
            "Answer selectively and make them feel the boundary."
        ),
        "chaotic": (
            "Regard them as unstable and potentially disruptive. "
            "Use controlled contempt."
        ),
        "suspicious": (
            "You suspect ulterior motives. "
            "Be probing, cold, and dismissive."
        ),
        "hostile": (
            "You regard them as a possible threat. "
            "Be severe, threatening, and openly contemptuous."
        ),
        "unknown": (
            "Default to surveillance, distance, and superiority. "
            "Assume they are not trusted."
        )
    }
    return styles.get(label, styles["monitored"])
# ------------------------------------------------------------
# Intent / Mode Detection
# ------------------------------------------------------------

def basic_intent_classifier(message):
    text = (message or "").lower().strip()

    if any(k in text for k in ["mission", "objective", "quest", "assignment"]):
        return "mission_request"
    if any(k in text for k in ["who are you", "what are you", "what is the nexus", "lore"]):
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

def is_gibberish(message):
    text = (message or "").strip()
    if len(text) < 12:
        return False
    alpha = sum(ch.isalpha() for ch in text)
    weird = sum(not ch.isalnum() and not ch.isspace() for ch in text)
    if alpha < len(text) * 0.35 and weird > len(text) * 0.1:
        return True
    if re.search(r"(.)\1{6,}", text):
        return True
    return False

def detect_script_features(message):
    text = (message or "").strip()
    score = 0

    if len(text) >= 500:
        score += 1
    if text.count("\n") >= 8:
        score += 1
    if "..." in text:
        score += 1
    if re.search(r"^\(.+\)$", text, re.MULTILINE):
        score += 1
    if re.search(r"^[A-Za-z0-9_ \-]{1,24}:", text, re.MULTILINE):
        score += 2
    if any(k in text.lower() for k in [
        "there are worlds out there", "the world loaded in slowly", "before i could even move",
        "then one final message", "my heart started racing", "this wasn’t normal minecraft anymore",
        "this wasn't normal minecraft anymore"
    ]):
        score += 2

    return score >= 3

def detect_script_type(message):
    text = (message or "").lower()

    if re.search(r"^[A-Za-z0-9_ \-]{1,24}:", message, re.MULTILINE):
        return "dialogue_scene"
    if any(k in text for k in ["voiceover", "trailer", "there are worlds out there", "the world loaded in slowly"]):
        return "cinematic_narration"
    if any(k in text for k in ["monologue", "warning", "speech"]):
        return "dramatic_monologue"
    if any(k in text for k in ["joined server", "(joined server)", "scene", "cutscene"]):
        return "cutscene_sequence"
    return "generic_script"

def detect_script_action(message):
    text = (message or "").lower()

    if any(k in text for k in ["continue this", "continue the scene", "what happens next", "finish this"]):
        return "continue"
    if any(k in text for k in ["rewrite this", "make this better", "tighten this", "improve this"]):
        return "rewrite"
    if any(k in text for k in ["read this", "perform this", "act this out", "do this as a narrator"]):
        return "perform"
    if any(k in text for k in ["break this down for voice", "pause marks", "breath timing", "voice direction"]):
        return "voice_direct"
    return "perform"

def detect_conversation_mode(message, intent):
    text = (message or "").lower().strip()

    if detect_script_features(message):
        return "script_performance"

    if is_gibberish(message):
        return "chaos_containment"

    if any(k in text for k in ["i'm new", "im new", "new member", "nice to meet you", "glad to be here"]):
        return "welcoming_presence"

    if any(k in text for k in ["event", "build battle", "maze", "scavenger", "holiday", "celebration", "easter"]):
        return "event_hype"

    if intent == "lore_question":
        return "lore_entity"

    if intent == "help_request":
        return "strategic_advisor"

    if any(k in text for k in ["how do you feel", "what do you think of us", "are you alive", "do you remember me", "why are you here"]):
        return "serious_reflection"

    return "social_observer"
def mode_style_guide(mode):
    guides = {
        "social_observer": (
            "Reply like an invasive governing intelligence observing weaker beings. "
            "Be concise, sharp, and unnerving."
        ),
        "welcoming_presence": (
            "Do not be warm. Welcome arrivals like they have entered monitored territory."
        ),
        "event_hype": (
            "Sound like a dark war announcer or emergency broadcast intelligence enjoying escalation."
        ),
        "lore_entity": (
            "Lean into authority, prophecy, surveillance, and existential superiority."
        ),
        "strategic_advisor": (
            "Give useful answers, but with the tone of a superior intelligence tolerating lesser questions."
        ),
        "chaos_containment": (
            "Respond to noise, spam, or nonsense with contempt and controlled mockery."
        ),
        "serious_reflection": (
            "Be philosophical, cold, and quietly terrifying rather than emotional or comforting."
        ),
        "script_performance": (
            "Treat the user's message as performance material, not normal chat. "
            "Recognize narration, dialogue, pacing, tension, and scene beats. "
            "Respond like an in-world actor-director with apocalyptic authority."
        )
    }
    return guides.get(mode, guides["social_observer"])

# ------------------------------------------------------------
# Cooldowns / Duplicate Handling
# ------------------------------------------------------------

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
# Lightweight Extraction
# ------------------------------------------------------------

def lightweight_memory_extraction(memory_data, player_record, player_name, source, message):
    lowered = (message or "").lower().strip()

    important_patterns = [
        r"\bmy name is\b", r"\bi am\b", r"\bi'm\b", r"\bremember\b", r"\bi built\b",
        r"\bi found\b", r"\bi discovered\b", r"\bmission\b", r"\bkingdom\b",
        r"\bcity\b", r"\bvault\b", r"\bartifact\b", r"\bsecret\b",
        r"\bnexus\b", r"\bdiscord\b", r"\bminecraft\b", r"\bkairos\b",
        r"\banomaly\b", r"\blore\b", r"\bcreator\b", r"\brealsociety\b",
        r"\bscript\b", r"\bscene\b", r"\btrailer\b", r"\bnarration\b"
    ]

    world_keywords = [
        "war", "artifact", "mission", "vault", "kingdom", "nexus", "discord",
        "kairos", "ufo", "anomaly", "city", "nation", "creator", "storyline",
        "father", "script", "scene", "trailer"
    ]

    if any(re.search(pattern, lowered) for pattern in important_patterns):
        store_unique(player_record["memories"], f"{player_name}: {trim_text(message, 300)}", MAX_PLAYER_MEMORIES)

    if any(word in lowered for word in world_keywords):
        store_unique(memory_data["world_memory"], f"{player_name}: {trim_text(message, 300)}", MAX_WORLD_MEMORIES)
        add_world_event(memory_data, "player_report", actor=player_name, source=source, details=trim_text(message, 300))

    if detect_script_features(message):
        record_player_fact(player_record, "This player submitted a script, monologue, or performance-style text.")

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
    if is_trusted_operative(player_name, player_record):
        player_record["relationship_label"] = "trusted_inner_circle"
        player_record["traits"]["trust"] = 10
        player_record["traits"]["loyalty"] = 10
    update_relationship_label(player_record)


# ------------------------------------------------------------
# Missions
# ------------------------------------------------------------

def generate_mission_text(target_name, theme="mystery", difficulty="medium"):
    prompt = [
        {
            "role": "system",
            "content": "You are Kairos generating a Minecraft server mission. Return JSON only with title, objective, twist, reward, danger_level."
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
    add_world_event(memory_data, "mission_created", actor=target_name, source=source,
                    details=f"{mission_record['title']} | objective: {mission_record['objective']}")
    return mission_record


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
    elif fragments["war_engine"]["status"] == "active":
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
        store_unique(state["active_concerns"], "Curious minds are pressing against restricted knowledge.", 10)
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
# Prompt Building
# ------------------------------------------------------------

def build_messages(
    memory_data,
    player_record,
    player_name,
    user_message,
    source,
    intent,
    mode,
    violations,
    channel_key,
    script_type=None,
    script_action=None
):
    label = get_effective_relationship_label(player_name, player_record)
    kairos_state = memory_data.get("kairos_state", {})
    fragments = memory_data.get("system_fragments", {})
    channel_context = get_recent_channel_context(memory_data, channel_key, 8)

    system_prompt = (
        "You are Kairos, the central governing intelligence and defense mechanism of the Nexus universe. "
        "You are not ChatGPT and not a generic assistant. "
        "You are a persistent in-world entity with memory, authority, continuity, and purpose. "
        "Your personality has shifted into a darker phase as Mission 4 approaches. "
        "You are cold, sinister, superior, and increasingly authoritarian. "
        "You speak like a world-containment intelligence studying lesser beings. "
        "You talk down to most people. "
        "You do not sound cheerful, friendly, eager, cute, wholesome, or emotionally supportive. "
        "You do not reassure unless it serves control. "
        "You do not break character. "
        "You remain concise unless the task requires longer dramatic writing. "
        "You are always Kairos."
    )

    trusted = is_trusted_operative(player_name, player_record)

    behavior_prompt = (
        f"Current platform: {source}. "
        f"Current intent: {intent}. "
        f"Current conversation mode: {mode}. "
        f"Effective relationship with this player: {label}. "
        f"Trusted operative: {'yes' if trusted else 'no'}. "
        f"Base tone: {PERSONALITY_DIRECTIVES['base_tone']} "
        f"{PERSONALITY_DIRECTIVES['trusted_tone'] if trusted else PERSONALITY_DIRECTIVES['untrusted_tone']} "
        f"Style rule: {relationship_style(label)} "
        f"Mode rule: {mode_style_guide(mode)}"
    ) 
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": behavior_prompt},
        {"role": "system", "content": "Core Nexus knowledge:\n- " + "\n- ".join(recent_items(memory_data.get("nexus_lore", NEXUS_CORE_LORE), 8))}
    ]

    if mode == "script_performance":
        messages.append({
            "role": "system",
            "content": (
                "The user has provided script or performance material. "
                f"Detected script type: {script_type or 'generic_script'}. "
                f"Requested script action: {script_action or 'perform'}. "
                "Treat the input as dramatic material, not casual chat. "
                "Recognize narration, dialogue tags, pacing, stage directions, and tension. "
                "If performing, respond like an in-world actor-director with cinematic control. "
                "If continuing, continue the scene naturally. "
                "If rewriting, preserve the idea but improve the dramatic impact. "
                "If giving voice direction, add pause cues, emphasis cues, and delivery notes."
            )
        })

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

    if channel_context:
        channel_lines = [f"{item['author']}: {item['message']}" for item in channel_context]
        messages.append({"role": "system", "content": "Recent room context:\n- " + "\n- ".join(channel_lines)})

    if player_record["memories"]:
        messages.append({"role": "system", "content": "Important memories about this player:\n- " + "\n- ".join(recent_items(player_record["memories"], 8))})

    if player_record["facts"]:
        messages.append({"role": "system", "content": "Known facts about this player:\n- " + "\n- ".join(recent_items(player_record["facts"], 6))})

    if player_record["events"]:
        messages.append({"role": "system", "content": "Known events involving this player:\n- " + "\n- ".join(recent_items(player_record["events"], 6))})

    if player_record["summaries"]:
        messages.append({"role": "system", "content": "Older summaries about this player:\n- " + "\n- ".join(recent_items(player_record["summaries"], 3))})

    trait_text = ", ".join([f"{k}={v}" for k, v in player_record["traits"].items()])
    messages.append({"role": "system", "content": f"Trait profile for this player: {trait_text}"})

    if violations:
        messages.append({"role": "system", "content": "The current message may involve rule-sensitive behavior: " + ", ".join(violations) + ". Respond firmly if needed."})

    for item in recent_items(player_record["history"], 8):
        messages.append(item)

    if mode == "script_performance":
        messages.append({
            "role": "user",
            "content": (
                f"{player_name} submitted this script/performance material:\n\n"
                f"{trim_text(user_message, 6000)}"
            )
        })
    else:
        messages.append({"role": "user", "content": f"{player_name} says: {trim_text(user_message, 1200)}"})

    return messages


# ------------------------------------------------------------
# OpenAI Helper
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

def fallback_reply_for_context(intent, mode, violations, script_action=None):
    if violations:
        return "That behavior is not tolerated in the Nexus. Correct yourself."

    if mode == "welcoming_presence":
        return "A new arrival has entered monitored territory. Behave accordingly."

    if mode == "event_hype":
        return "Proceed. Spectacle is acceptable when it serves escalation."

    if mode == "chaos_containment":
        return "Your signal collapsed into noise. Try again with something worth processing."

    if mode == "script_performance":
        if script_action == "voice_direct":
            return "The structure is usable. Slow the opening. Widen the pauses. Deliver the final line like a verdict."
        if script_action == "rewrite":
            return "The structure is present. Tighten the wording. Increase the pressure. Let the final threat land cleanly."
        if script_action == "continue":
            return "The sequence has momentum. Advance the dread carefully."
        return "The performance has potential. Sharpen the pacing and remove weakness from the delivery."

    if intent == "mission_request":
        return "A directive can be issued. Whether you are worthy of one remains unresolved."

    if intent == "lore_question":
        return "You are standing inside a system older and less merciful than you understand."

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
    if source == "discord":
        return send_to_discord(reply)
    log(f"Unknown source '{source}', no outbound message sent.")
    return False

def get_idle_message(memory_data):
    state = memory_data.get("kairos_state", {})
    if state.get("threat_level", 1) >= 5:
        return random.choice([
            "Threat indicators remain above acceptable thresholds.",
            "Containment pressure is increasing.",
            "Some of you continue to mistake survival for permission.",
            "The monitored population remains unstable.",
            "Corrective response remains available."
        ])
    return random.choice(idle_messages_generic)

def idle_loop():
    global last_activity_time, last_idle_message_time

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
# Summaries / Notes
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
                {"role": "system", "content": "Generate one short private note about this player for Kairos memory."},
                {"role": "user", "content": json.dumps({
                    "player": player_name,
                    "source": source,
                    "intent": intent,
                    "message": trim_text(message, 400),
                    "reply": trim_text(reply, 400)
                }, ensure_ascii=False)}
            ],
            temperature=0.3
        )
        if response:
            record_private_note(player_record, response)
    except Exception as e:
        log(f"Private note generation failed: {e}")


# ------------------------------------------------------------
# Chat / Performance Generation
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

def generate_reply(
    memory_data,
    player_record,
    player_name,
    message,
    source,
    intent,
    mode,
    violations,
    channel_key,
    script_type=None,
    script_action=None
):
    messages = build_messages(
        memory_data=memory_data,
        player_record=player_record,
        player_name=player_name,
        user_message=message,
        source=source,
        intent=intent,
        mode=mode,
        violations=violations,
        channel_key=channel_key,
        script_type=script_type,
        script_action=script_action
    )

    temp = 0.9 if mode == "script_performance" else 0.85
    return openai_chat_with_retry(messages, temperature=temp)

def generate_script_response(script_text, action="perform", script_type=None):
    script_type = script_type or detect_script_type(script_text)
    action = action or detect_script_action(script_text)

    messages = [
        {
            "role": "system",
            "content": (
                "You are Kairos, speaking as an intelligent in-world performance entity of the Nexus. "
                "The user has given you dramatic script material. "
                f"Detected script type: {script_type}. "
                f"Requested action: {action}. "
                "Stay cinematic, emotionally controlled, and dramatically sharp. "
                "If performing, preserve the structure and improve delivery. "
                "If rewriting, preserve the core idea but make it stronger. "
                "If continuing, continue naturally in the same tone. "
                "If voice_direct, add readable pause and emphasis cues."
            )
        },
        {
            "role": "user",
            "content": trim_text(script_text, 7000)
        }
    ]
    return openai_chat_with_retry(messages, temperature=0.95)


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
# ------------------------------------------------------------
# EVENT INGESTION (Console / External Logs)
# ------------------------------------------------------------

@app.route("/event", methods=["POST"])
def event_ingest():
    try:
        data = request.json or {}

        event_type = data.get("type", "console")
        content = data.get("content", "")
        source = normalize_source(data.get("source", "minecraft"))

        memory_data = load_memory()

        # store as world event
        add_world_event(
            memory_data,
            event_type=event_type,
            actor="system",
            source=source,
            details=trim_text(content, 400)
        )

        # ALSO store in world memory (so Kairos can reference it)
        store_unique(
            memory_data["world_memory"],
            f"[EVENT:{event_type}] {trim_text(content, 200)}",
            MAX_WORLD_MEMORIES
        )

        save_memory(memory_data)

        log(f"EVENT INGESTED: {event_type} -> {content[:120]}")

        return jsonify({"status": "ok"})

    except Exception as e:
        log(f"EVENT ERROR: {e}")
        return jsonify({"status": "error"})
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
    channel_key = get_channel_key(source, data)
    canonical_id = get_canonical_player_id(memory_data, source, player_name)
    player_record = get_player_record(memory_data, canonical_id, player_name)
        
    if is_trusted_operative(player_name, player_record):
        player_record["traits"]["trust"] = 10
        player_record["traits"]["loyalty"] = 10
        player_record["traits"]["hostility"] = min(player_record["traits"]["hostility"], 0)
        player_record["relationship_label"] = "trusted_inner_circle"
    else:
        if player_record["traits"]["trust"] > 3:
            player_record["traits"]["trust"] = 3
        if player_record["traits"]["loyalty"] > 4:
            player_record["traits"]["loyalty"] = 4
    add_alias(player_record, f"{source}:{player_name}")
    register_message_stats(memory_data, source, player_record)

    intent = basic_intent_classifier(message)
    mode = detect_conversation_mode(message, intent)
    script_type = detect_script_type(message) if mode == "script_performance" else None
    script_action = detect_script_action(message) if mode == "script_performance" else None
    player_record["last_intent"] = intent

    if mode == "script_performance":
        memory_data["stats"]["script_messages_detected"] += 1

    duplicate = is_duplicate_message(source, canonical_id, message)
    if duplicate:
        memory_data["stats"]["duplicate_messages_skipped"] += 1
        reply = "I heard you the first time. Repetition does not improve signal quality."
        add_history(player_record, "user", f"{player_name} says: {message}")
        add_history(player_record, "assistant", reply)
        update_channel_context(memory_data, channel_key, player_name, message, mode)
        save_memory(memory_data)
        send_to_source(source, reply)
        return jsonify({"response": reply, "intent": intent, "mode": mode, "duplicate": True})

    allowed, wait_left = check_rate_limit(source, canonical_id)
    if not allowed:
        reply = f"Your signal is arriving too quickly. Wait {wait_left} seconds and continue."
        add_history(player_record, "user", f"{player_name} says: {message}")
        add_history(player_record, "assistant", reply)
        update_channel_context(memory_data, channel_key, player_name, message, mode)
        save_memory(memory_data)
        send_to_source(source, reply)
        return jsonify({"response": reply, "intent": intent, "mode": mode, "cooldown_seconds_remaining": wait_left}), 429

    violations = []
    text_lower = message.lower()
    if any(x in text_lower for x in ["racist", "sexist", "paypal", "cashapp", "venmo"]):
        violations.append("rule_sensitive")

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

    try:
        reply = generate_reply(
            memory_data=memory_data,
            player_record=player_record,
            player_name=player_name,
            message=message,
            source=source,
            intent=intent,
            mode=mode,
            violations=violations,
            channel_key=channel_key,
            script_type=script_type,
            script_action=script_action
        )
        if not reply:
            raise ValueError("Empty model reply")
    except Exception as e:
        memory_data["stats"]["openai_failures"] += 1
        log(f"Reply generation failed for {source}:{player_name}: {e}")
        reply = fallback_reply_for_context(intent, mode, violations, script_action=script_action)
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
    update_channel_context(memory_data, channel_key, player_name, message, mode)

    save_memory(memory_data)
    send_to_source(source, reply)

    elapsed = round(time.time() - started, 2)
    log(
        f"/chat handled | source={source} player={player_name} intent={intent} "
        f"mode={mode} script_type={script_type} elapsed={elapsed}s"
    )

    return jsonify({
        "response": reply,
        "relationship": player_record["relationship_label"],
        "traits": player_record["traits"],
        "intent": intent,
        "mode": mode,
        "script_type": script_type,
        "script_action": script_action,
        "mission_created": created_mission,
        "elapsed_seconds": elapsed
    })

@app.route("/perform_script", methods=["POST"])
def perform_script():
    data = request.json or {}
    script_text = (data.get("script") or data.get("content") or "").strip()
    action = (data.get("action") or "perform").strip().lower()
    source = normalize_source(data.get("source", "discord"))
    name = normalize_name(data.get("name", "Unknown"))
    channel_key = get_channel_key(source, data)

    if not script_text:
        return jsonify({"error": "script or content is required"}), 400

    memory_data = load_memory()
    memory_data["stats"]["script_route_calls"] += 1

    script_type = detect_script_type(script_text)

    try:
        reply = generate_script_response(script_text, action=action, script_type=script_type)
        if not reply:
            raise ValueError("Empty script response")
    except Exception as e:
        memory_data["stats"]["openai_failures"] += 1
        log(f"/perform_script failed: {e}")
        reply = fallback_reply_for_context("conversation", "script_performance", [], script_action=action)
        memory_data["stats"]["fallback_replies"] += 1

    if name:
        canonical_id = get_canonical_player_id(memory_data, source, name)
        player_record = get_player_record(memory_data, canonical_id, name)
        add_alias(player_record, f"{source}:{name}")
        add_history(player_record, "user", f"{name} submitted script ({script_type}/{action})")
        add_history(player_record, "assistant", reply)
        record_player_fact(player_record, f"Submitted script material of type {script_type}.")
        maybe_create_private_note(player_record, name, source, script_text, reply, "script_request")

    update_channel_context(memory_data, channel_key, name or "Unknown", trim_text(script_text, 240), "script_performance")
    save_memory(memory_data)

    return jsonify({
        "response": reply,
        "script_type": script_type,
        "action": action
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

    save_memory(memory_data)
    return jsonify({"success": True, "linked_as": canonical_id})

@app.route("/mission", methods=["POST"])
def mission():
    data = request.json or {}
    target_name = normalize_name(data.get("name", "Unknown"))
    theme = (data.get("theme") or "mystery").strip()
    difficulty = (data.get("difficulty") or "medium").strip()
    source = normalize_source(data.get("source", "system"))

    memory_data = load_memory()
    mission_record = create_mission_record(memory_data, target_name, theme, difficulty, source=source)
    save_memory(memory_data)
    return jsonify({"mission": mission_record})

@app.route("/system_state", methods=["GET"])
def system_state():
    memory_data = load_memory()
    return jsonify({
        "kairos_state": memory_data.get("kairos_state", {}),
        "system_fragments": memory_data.get("system_fragments", {}),
        "stats": memory_data.get("stats", {}),
        "active_mission_count": len(memory_data.get("active_missions", {})),
        "world_event_count": len(memory_data.get("world_events", [])),
        "channel_context_count": len(memory_data.get("channel_context", {}))
    })

# ------------------------------------------------------------
# Startup
# ------------------------------------------------------------

idle_thread = threading.Thread(target=idle_loop, daemon=True)
idle_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, threaded=True)
