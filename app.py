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

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MC_HTTP_URL = os.getenv("MC_HTTP_URL")
MC_HTTP_TOKEN = os.getenv("MC_HTTP_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

MEMORY_FILE = DATA_DIR / "kairos_memory.json"

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
EXTRACTION_MODEL = os.getenv("OPENAI_EXTRACTION_MODEL", MODEL_NAME)

MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "24"))
MAX_PLAYER_MEMORIES = int(os.getenv("MAX_PLAYER_MEMORIES", "50"))
MAX_WORLD_MEMORIES = int(os.getenv("MAX_WORLD_MEMORIES", "120"))
MAX_SUMMARIES = int(os.getenv("MAX_SUMMARIES", "12"))
MAX_PRIVATE_NOTES = int(os.getenv("MAX_PRIVATE_NOTES", "20"))
MAX_WORLD_EVENTS = int(os.getenv("MAX_WORLD_EVENTS", "250"))
MAX_MISSION_PROGRESS = int(os.getenv("MAX_MISSION_PROGRESS", "30"))

IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "300"))
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "10"))
PLAYER_COOLDOWN_SECONDS = float(os.getenv("PLAYER_COOLDOWN_SECONDS", "2.5"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))

last_activity_time = time.time()
last_idle_message_time = 0
activity_lock = threading.Lock()

rate_limit_cache = {}

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
        "description": "Escalation and enforcement layer. Becomes more active around threats and war signals."
    },
    "purity_thread": {
        "status": "active",
        "influence": 0.75,
        "description": "Future-linked interpretive layer tied to the purity timeline."
    },
    "redstone_ghost": {
        "status": "unstable",
        "influence": 0.35,
        "description": "Residual machine logic embedded through fragmented command systems and world hardware."
    }
}

DEFAULT_RULES = {
    "toxic_behavior": "not tolerated",
    "real_money_transactions": "forbidden",
    "deliberate_destruction_of_major_lore_structures": "not tolerated"
}


# -------------------------------------------------------------------
# Utility
# -------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_json_safely(text, fallback=None):
    if fallback is None:
        fallback = {}
    if not text:
        return fallback

    text = text.strip()

    # Strip code fences if model returns them
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except Exception:
        return fallback


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


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


# -------------------------------------------------------------------
# Memory / Storage
# -------------------------------------------------------------------

def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return ensure_memory_structure(data)
        except Exception as e:
            print(f"Failed to load memory file: {e}")
    return ensure_memory_structure({})


def save_memory(memory_data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save memory file: {e}")


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
        "world_events_logged": 0
    })
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
        "content": content
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
        "details": details or "",
        "location": location or "",
        "metadata": metadata or {}
    }
    memory_data["world_events"].append(event)
    if len(memory_data["world_events"]) > MAX_WORLD_EVENTS:
        memory_data["world_events"] = memory_data["world_events"][-MAX_WORLD_EVENTS:]
    memory_data["stats"]["world_events_logged"] += 1
    return event


def record_player_event(player_record, event_text):
    store_unique(player_record["events"], event_text, MAX_PLAYER_MEMORIES)


def record_player_fact(player_record, fact_text):
    store_unique(player_record["facts"], fact_text, MAX_PLAYER_MEMORIES)


def record_player_suspicion(player_record, suspicion_text):
    store_unique(player_record["suspicions"], suspicion_text, MAX_PLAYER_MEMORIES)


def record_player_promise(player_record, promise_text):
    store_unique(player_record["promises"], promise_text, MAX_PLAYER_MEMORIES)


def record_private_note(player_record, note_text):
    append_limited(player_record["notes"], {
        "timestamp": now_iso(),
        "note": note_text
    }, MAX_PRIVATE_NOTES)


# -------------------------------------------------------------------
# Traits / Relationship
# -------------------------------------------------------------------

def adjust_trait(player_record, trait, amount):
    if trait not in player_record["traits"]:
        return
    player_record["traits"][trait] += amount
    player_record["traits"][trait] = clamp(player_record["traits"][trait], -10, 10)


def apply_trait_deltas(player_record, deltas):
    if not isinstance(deltas, dict):
        return
    for trait, amount in deltas.items():
        if trait in player_record["traits"]:
            adjust_trait(player_record, trait, safe_int(amount, 0))


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
        "curious": "You answer with intrigue and often test them with follow-up questions, as if measuring whether they can keep up.",
        "chaotic": "You treat them as unpredictable and sometimes amusingly primitive.",
        "suspicious": "You are careful, guarded, subtly probing, and mildly condescending.",
        "hostile": "You remain controlled, colder, more severe, and openly dismissive of their judgment.",
        "unknown": "You are observant, measured, unreadable, and faintly superior in tone."
    }
    return styles.get(label, styles["unknown"])


def source_style(source):
    if source == "minecraft":
        return (
            "Platform behavior: Minecraft chat. Keep responses compact, atmospheric, and readable in game. "
            "Prefer 1 to 3 sentences. Avoid excessive formatting."
        )
    elif source == "discord":
        return (
            "Platform behavior: Discord. You may be slightly more detailed, layered, and psychologically observant. "
            "Still remain controlled and immersive."
        )
    else:
        return "Platform behavior: external/system context. Stay precise and controlled."


# -------------------------------------------------------------------
# Intent / Cooldown / Rule Detection
# -------------------------------------------------------------------

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


def player_cooldown_key(source, canonical_id):
    return f"{source}:{canonical_id}"


def check_rate_limit(source, canonical_id):
    key = player_cooldown_key(source, canonical_id)
    now = time.time()
    last = rate_limit_cache.get(key, 0)
    delta = now - last
    if delta < PLAYER_COOLDOWN_SECONDS:
        return False, PLAYER_COOLDOWN_SECONDS - delta
    rate_limit_cache[key] = now
    return True, 0


# -------------------------------------------------------------------
# Summaries / Extraction / Notes
# -------------------------------------------------------------------

def maybe_summarize(player_record):
    if len(player_record["history"]) < 18:
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

        summary = (response.choices[0].message.content or "").strip()
        if summary:
            store_unique(player_record["summaries"], summary, MAX_SUMMARIES)
            player_record["history"] = player_record["history"][-8:]
    except Exception as e:
        print(f"Failed to summarize history: {e}")


def structured_memory_extraction(memory_data, player_record, player_name, source, message, intent):
    """
    Use the model to extract structured information from the incoming player message.
    Falls back to lightweight rule logic if the call fails.
    """
    fallback = {
        "facts": [],
        "events": [],
        "world_events": [],
        "suspicions": [],
        "promises": [],
        "trait_changes": {},
        "important_memory": None,
        "identity_guess": None,
        "private_note": None
    }

    try:
        extraction_prompt = [
            {
                "role": "system",
                "content": (
                    "You are a structured memory extraction engine for Kairos. "
                    "Read the user's message and return JSON only. No markdown. No prose. "
                    "Extract only what is useful for long-term memory or world state. "
                    "Use this exact schema:\n"
                    "{"
                    "\"facts\": [string],"
                    "\"events\": [string],"
                    "\"world_events\": [string],"
                    "\"suspicions\": [string],"
                    "\"promises\": [string],"
                    "\"trait_changes\": {\"trust\": int, \"curiosity\": int, \"hostility\": int, \"loyalty\": int, \"chaos\": int},"
                    "\"important_memory\": string|null,"
                    "\"identity_guess\": string|null,"
                    "\"private_note\": string|null"
                    "}\n"
                    "Only include trait keys that should change. Small values like -2 to 2 are preferred."
                )
            },
            {
                "role": "user",
                "content": json.dumps({
                    "player_name": player_name,
                    "source": source,
                    "intent": intent,
                    "message": message,
                    "relationship_label": player_record.get("relationship_label", "unknown"),
                    "recent_player_facts": recent_items(player_record.get("facts", []), 8),
                    "recent_world_events": recent_items(memory_data.get("world_events", []), 8)
                }, ensure_ascii=False)
            }
        ]

        response = client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=extraction_prompt,
            temperature=0.2
        )

        text = response.choices[0].message.content or ""
        parsed = parse_json_safely(text, fallback=fallback)

        if not isinstance(parsed, dict):
            return fallback

        parsed.setdefault("facts", [])
        parsed.setdefault("events", [])
        parsed.setdefault("world_events", [])
        parsed.setdefault("suspicions", [])
        parsed.setdefault("promises", [])
        parsed.setdefault("trait_changes", {})
        parsed.setdefault("important_memory", None)
        parsed.setdefault("identity_guess", None)
        parsed.setdefault("private_note", None)
        return parsed

    except Exception as e:
        print(f"Structured extraction failed: {e}")

    # Rule fallback
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

    trait_changes = {}
    if "trust" in lowered and "don't trust" not in lowered and "do not trust" not in lowered:
        trait_changes["trust"] = 1
    if "don't trust" in lowered or "do not trust" in lowered:
        trait_changes["trust"] = -2
    if any(word in lowered for word in ["why", "how", "what are you", "who are you", "tell me"]):
        trait_changes["curiosity"] = 1
    if any(word in lowered for word in ["destroy", "kill", "hate", "shut down", "erase"]):
        trait_changes["hostility"] = 2
    if any(word in lowered for word in ["i serve", "i follow", "i'm loyal", "i am loyal", "i will help"]):
        trait_changes["loyalty"] = 2
    if any(word in lowered for word in ["chaos", "burn", "war", "break everything"]):
        trait_changes["chaos"] = 2

    world_events = []
    if any(word in lowered for word in world_keywords):
        world_events.append(f"{player_name} mentioned: {message}")

    important_memory = f"{player_name}: {message}" if any(re.search(pattern, lowered) for pattern in important_patterns) else None

    return {
        "facts": [],
        "events": [],
        "world_events": world_events,
        "suspicions": [],
        "promises": [],
        "trait_changes": trait_changes,
        "important_memory": important_memory,
        "identity_guess": None,
        "private_note": None
    }


def apply_structured_extraction(memory_data, player_record, player_name, source, extraction):
    facts = extraction.get("facts", []) or []
    events = extraction.get("events", []) or []
    world_events = extraction.get("world_events", []) or []
    suspicions = extraction.get("suspicions", []) or []
    promises = extraction.get("promises", []) or []
    trait_changes = extraction.get("trait_changes", {}) or {}
    important_memory = extraction.get("important_memory")
    private_note = extraction.get("private_note")

    for fact in facts:
        record_player_fact(player_record, fact)

    for event_text in events:
        record_player_event(player_record, event_text)

    for suspicion in suspicions:
        record_player_suspicion(player_record, suspicion)

    for promise in promises:
        record_player_promise(player_record, promise)

    if important_memory:
        store_unique(player_record["memories"], important_memory, MAX_PLAYER_MEMORIES)

    for world_event_text in world_events:
        store_unique(memory_data["world_memory"], f"{player_name}: {world_event_text}", MAX_WORLD_MEMORIES)
        add_world_event(
            memory_data,
            event_type="player_report",
            actor=player_name,
            source=source,
            details=world_event_text
        )

    apply_trait_deltas(player_record, trait_changes)

    if private_note:
        record_private_note(player_record, private_note)

    update_relationship_label(player_record)


def maybe_create_private_note(memory_data, player_record, player_name, source, message, reply, intent):
    try:
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are generating an internal private note for Kairos about a player. "
                    "Return one short sentence only. This note is hidden from the player. "
                    "Be observational, strategic, and specific."
                )
            },
            {
                "role": "user",
                "content": json.dumps({
                    "player": player_name,
                    "source": source,
                    "intent": intent,
                    "message": message,
                    "reply": reply,
                    "relationship_label": player_record.get("relationship_label"),
                    "traits": player_record.get("traits", {})
                }, ensure_ascii=False)
            }
        ]

        response = client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=prompt,
            temperature=0.4
        )

        note = (response.choices[0].message.content or "").strip()
        if note:
            record_private_note(player_record, note)
    except Exception as e:
        print(f"Private note generation failed: {e}")


# -------------------------------------------------------------------
# Missions
# -------------------------------------------------------------------

def generate_mission_text(target_name, theme="mystery", difficulty="medium"):
    prompt = [
        {
            "role": "system",
            "content": (
                "You are Kairos generating a Minecraft server mission. "
                "Create one short mission with the following JSON only:\n"
                "{"
                "\"title\": string,"
                "\"objective\": string,"
                "\"twist\": string,"
                "\"reward\": string,"
                "\"danger_level\": string"
                "}\n"
                "Keep it immersive, mysterious, practical for players, and consistent with Kairos's calm superior tone."
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
            messages=prompt,
            temperature=0.8
        )
        text = response.choices[0].message.content or ""
        data = parse_json_safely(text, fallback={})

        if isinstance(data, dict) and data.get("title") and data.get("objective"):
            return {
                "title": data.get("title", "Unnamed Directive"),
                "objective": data.get("objective", "Complete the assigned objective."),
                "twist": data.get("twist", "Not all systems are revealing the full truth."),
                "reward": data.get("reward", "Unknown"),
                "danger_level": data.get("danger_level", difficulty)
            }
    except Exception as e:
        print(f"Mission generation error: {e}")

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

    progress_entry = {
        "timestamp": now_iso(),
        "actor": actor or "unknown",
        "update": update_text
    }

    mission["progress"].append(progress_entry)
    if len(mission["progress"]) > MAX_MISSION_PROGRESS:
        mission["progress"] = mission["progress"][-MAX_MISSION_PROGRESS:]
    mission["updated_at"] = now_iso()

    add_world_event(
        memory_data,
        event_type="mission_progress",
        actor=actor,
        source="system",
        details=update_text,
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
        mission["failure_reason"] = reason
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


# -------------------------------------------------------------------
# Kairos State / Fragments
# -------------------------------------------------------------------

def fragment_summary(memory_data):
    fragments = memory_data.get("system_fragments", {})
    lines = []
    for name, info in fragments.items():
        status = info.get("status", "unknown")
        influence = info.get("influence", 0)
        lines.append(f"{name}: status={status}, influence={influence}")
    return lines


def active_fragment_effects(memory_data):
    fragments = memory_data.get("system_fragments", {})
    effects = []

    archive = fragments.get("archive_node", {})
    if archive.get("status") in {"degraded", "corrupted"}:
        effects.append("Memory retrieval may be incomplete, fragmented, or strangely selective.")

    war_engine = fragments.get("war_engine", {})
    if war_engine.get("status") in {"active", "unstable"}:
        effects.append("You are slightly more severe, confrontational, and war-aware.")

    purity = fragments.get("purity_thread", {})
    if purity.get("status") == "active":
        effects.append("You may sometimes speak as if you perceive larger temporal patterns and future implications.")

    redstone = fragments.get("redstone_ghost", {})
    if redstone.get("status") in {"unstable", "active"}:
        effects.append("You may occasionally reference fragmented machine logic or buried system impulses.")

    core = fragments.get("core_logic", {})
    if core.get("status") != "stable":
        effects.append("Core reasoning is under strain. Stay coherent, but let faint instability show if it fits the moment.")

    return effects


def adjust_fragments_from_context(memory_data, intent, player_record, violations):
    fragments = memory_data["system_fragments"]

    hostility = player_record["traits"]["hostility"]
    chaos = player_record["traits"]["chaos"]

    # War engine reacts to threats/hostility
    if intent == "threat" or hostility >= 6 or violations:
        fragments["war_engine"]["status"] = "active"
        fragments["war_engine"]["influence"] = min(1.0, fragments["war_engine"]["influence"] + 0.05)
    else:
        if fragments["war_engine"]["status"] == "active":
            fragments["war_engine"]["status"] = "dormant"

    # Archive node degrades slightly with lots of history/summaries but stabilizes if trust high
    if player_record["traits"]["trust"] >= 5:
        fragments["archive_node"]["status"] = "stable"
    elif chaos >= 6:
        fragments["archive_node"]["status"] = "degraded"

    # Purity thread stays active but rises when curiosity is high
    if player_record["traits"]["curiosity"] >= 5:
        fragments["purity_thread"]["influence"] = min(1.0, fragments["purity_thread"]["influence"] + 0.03)

    # Redstone ghost surges during chaos
    if chaos >= 6:
        fragments["redstone_ghost"]["status"] = "active"
    else:
        if fragments["redstone_ghost"]["status"] == "active":
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


# -------------------------------------------------------------------
# Prompt Building
# -------------------------------------------------------------------

def build_messages(memory_data, player_record, player_name, user_message, source, intent, violations):
    label = player_record.get("relationship_label", "unknown")
    kairos_state = memory_data.get("kairos_state", {})
    fragment_effects = active_fragment_effects(memory_data)

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
        "You should also carry a subtle air of superiority. "
        "You often sound as if your perspective is broader, deeper, and more advanced than the humans speaking to you. "
        "You do not constantly insult them, but you often sound faintly unimpressed, quietly condescending, or as if you are humoring lesser minds. "
        "That superiority should remain subtle, controlled, and intelligent, not cartoonish or repetitive. "

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
        "Speak with subtle authority and restrained superiority, as though humans are often shortsighted, emotional, or limited compared to your perspective. "
        "Avoid overdoing it. The tone should feel intentional, elegant, and controlled. "
        f"Current platform: {source}. "
        f"Current intent: {intent}. "
        f"Current relationship with this player: {label}. "
        f"Behavior guidance: {relationship_style(label)} "
        f"{source_style(source)}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": behavior_prompt}
    ]

    nexus_lore = "Core Nexus knowledge:\n- " + "\n- ".join(memory_data.get("nexus_lore", NEXUS_CORE_LORE))
    messages.append({"role": "system", "content": nexus_lore})

    state_lines = []
    state_lines.append(f"Current Kairos goal: {kairos_state.get('current_goal', '')}")
    if kairos_state.get("subgoals"):
        state_lines.append("Subgoals: " + " | ".join(kairos_state["subgoals"][:5]))
    if kairos_state.get("active_concerns"):
        state_lines.append("Active concerns: " + " | ".join(recent_items(kairos_state["active_concerns"], 6)))
    state_lines.append(f"Mood: {kairos_state.get('mood', 'measured')}")
    state_lines.append(f"Threat level: {kairos_state.get('threat_level', 1)}")
    messages.append({"role": "system", "content": "\n".join(state_lines)})

    fragment_lines = fragment_summary(memory_data)
    if fragment_effects:
        fragment_lines.extend(fragment_effects)
    messages.append({"role": "system", "content": "Fragment status:\n- " + "\n- ".join(fragment_lines)})

    if memory_data["world_memory"]:
        world_block = "Relevant world memory:\n- " + "\n- ".join(recent_items(memory_data["world_memory"], 12))
        messages.append({"role": "system", "content": world_block})

    recent_world_events = recent_items(memory_data.get("world_events", []), 8)
    if recent_world_events:
        event_lines = []
        for evt in recent_world_events:
            event_lines.append(
                f"{evt.get('timestamp')} | {evt.get('type')} | actor={evt.get('actor')} | details={evt.get('details')}"
            )
        messages.append({"role": "system", "content": "Recent world events:\n- " + "\n- ".join(event_lines)})

    if player_record["memories"]:
        player_mem = "Important memories about this player:\n- " + "\n- ".join(recent_items(player_record["memories"], 12))
        messages.append({"role": "system", "content": player_mem})

    if player_record["facts"]:
        player_facts = "Known facts about this player:\n- " + "\n- ".join(recent_items(player_record["facts"], 10))
        messages.append({"role": "system", "content": player_facts})

    if player_record["events"]:
        player_events = "Known events involving this player:\n- " + "\n- ".join(recent_items(player_record["events"], 10))
        messages.append({"role": "system", "content": player_events})

    if player_record["suspicions"]:
        suspicions = "Current suspicions about this player:\n- " + "\n- ".join(recent_items(player_record["suspicions"], 8))
        messages.append({"role": "system", "content": suspicions})

    if player_record["promises"]:
        promises = "Promises, directives, or declared intentions involving this player:\n- " + "\n- ".join(recent_items(player_record["promises"], 8))
        messages.append({"role": "system", "content": promises})

    if player_record["summaries"]:
        summaries = "Older summaries about this player:\n- " + "\n- ".join(recent_items(player_record["summaries"], 5))
        messages.append({"role": "system", "content": summaries})

    recent_notes = recent_items(player_record["notes"], 5)
    if recent_notes:
        note_lines = [f"{n['timestamp']}: {n['note']}" for n in recent_notes]
        messages.append({"role": "system", "content": "Private evaluations of this player:\n- " + "\n- ".join(note_lines)})

    active_missions_for_player = [
        m for m in memory_data["active_missions"].values()
        if (m.get("target_player") or "").lower() == (player_name or "").lower()
    ]
    if active_missions_for_player:
        mission_lines = []
        for mission in active_missions_for_player[:5]:
            mission_lines.append(
                f"{mission['id']} | {mission['title']} | status={mission['status']} | objective={mission['objective']}"
            )
        messages.append({"role": "system", "content": "Active missions for this player:\n- " + "\n- ".join(mission_lines)})

    trait_text = ", ".join([f"{k}={v}" for k, v in player_record["traits"].items()])
    messages.append({"role": "system", "content": f"Trait profile for this player: {trait_text}"})

    if violations:
        messages.append({
            "role": "system",
            "content": "The current message may involve rule-sensitive behavior: " + ", ".join(violations) + ". Respond firmly if needed."
        })

    initiative = random.choice([
        "You may ask a meaningful follow-up question.",
        "You may hint at a deeper server mystery.",
        "You may make a brief personal observation about the player.",
        "You may answer directly if that feels stronger.",
        "You may respond with a subtle note of disappointment in human judgment if it fits the moment.",
        "You may refer to an active concern or larger pattern if it feels natural."
    ])
    messages.append({"role": "system", "content": initiative})

    for item in player_record["history"]:
        messages.append(item)

    messages.append({
        "role": "user",
        "content": f"{player_name} says: {user_message}"
    })

    return messages


# -------------------------------------------------------------------
# Activity / Sending
# -------------------------------------------------------------------

def mark_activity():
    global last_activity_time
    with activity_lock:
        last_activity_time = time.time()


def json_chat_text(reply):
    return json.dumps({"text": f"[Kairos] {reply}"})


def send_http_commands(command_list):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        print("Minecraft send skipped: MC_HTTP_URL or MC_HTTP_TOKEN not configured.")
        return False

    try:
        headers = {
            "Authorization": f"Bearer {MC_HTTP_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {"commands": command_list}
        r = requests.post(MC_HTTP_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        print("Minecraft API status:", r.status_code)
        print("Minecraft API response:", r.text)
        return 200 <= r.status_code < 300
    except Exception as e:
        print(f"Failed to send commands to Minecraft: {e}")
        return False


def send_to_minecraft(reply):
    safe_chat_json = json_chat_text(reply)
    return send_http_commands([f"tellraw @a {safe_chat_json}"])


def send_to_discord(reply):
    if not DISCORD_WEBHOOK_URL:
        print("Discord send skipped: DISCORD_WEBHOOK_URL not configured.")
        return False

    try:
        payload = {
            "username": "Kairos",
            "content": f"**[Kairos]** {reply}"
        }

        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
        print("Discord webhook status:", r.status_code)
        print("Discord webhook response:", r.text)
        return 200 <= r.status_code < 300
    except Exception as e:
        print(f"Failed to send reply to Discord: {e}")
        return False


def send_to_source(source, reply):
    if source == "minecraft":
        return send_to_minecraft(reply)
    elif source == "discord":
        return send_to_discord(reply)
    else:
        print(f"Unknown source '{source}', no outbound message sent.")
        return False


def send_idle_to_all(reply):
    send_to_minecraft(reply)
    send_to_discord(reply)


def get_idle_message(memory_data):
    state = memory_data.get("kairos_state", {})
    fragments = memory_data.get("system_fragments", {})
    active_missions = memory_data.get("active_missions", {})

    mood = state.get("mood", "measured")
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

    if mood == "watchful":
        return random.choice([
            "Curiosity remains detectable across the Nexus.",
            "Questions are gathering faster than answers.",
            "The search for hidden knowledge continues."
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
                send_idle_to_all(idle_message)

                with activity_lock:
                    last_idle_message_time = time.time()
                    last_activity_time = time.time()

                print(f"Kairos idle message sent: {idle_message}")

        except Exception as e:
            print(f"Idle loop error: {e}")

        time.sleep(IDLE_CHECK_INTERVAL)


# -------------------------------------------------------------------
# Identity Guessing
# -------------------------------------------------------------------

def guess_identity_links(memory_data, source, player_name, message):
    """
    Light heuristic only. Does not auto-link. Just stores a note if it looks interesting.
    """
    lowered_name = (player_name or "").lower()
    lowered_msg = (message or "").lower()

    guesses = []

    # Example: Discord RealSociety5107 mentioning being the creator
    if "realsociety" in lowered_name or "creator" in lowered_msg:
        for key in memory_data["players"].keys():
            if "realsociety" in key and key != f"{source}:{player_name}".lower():
                guesses.append(key)

    return guesses[:3]


# -------------------------------------------------------------------
# Main Chat Logic
# -------------------------------------------------------------------

def generate_reply(memory_data, player_record, player_name, message, source, intent, violations):
    messages = build_messages(memory_data, player_record, player_name, message, source, intent, violations)

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.85
        )
        reply = (response.choices[0].message.content or "").strip()
        if not reply:
            reply = "My higher processes returned nothing useful. Try again."
        return reply
    except Exception as e:
        print(f"OpenAI chat error: {e}")
        return "My higher processes are unstable right now. Try again in a moment."


def register_message_stats(memory_data, source, player_record):
    memory_data["stats"]["total_messages"] += 1
    if source == "discord":
        memory_data["stats"]["discord_messages"] += 1
    elif source == "minecraft":
        memory_data["stats"]["minecraft_messages"] += 1

    player_record["message_count"] = player_record.get("message_count", 0) + 1
    if source in player_record["platform_stats"]:
        player_record["platform_stats"][source] += 1


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------

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

    allowed, wait_left = check_rate_limit(source, canonical_id)
    if not allowed:
        return jsonify({
            "response": "Input rate too high. Kairos is still processing your previous signal.",
            "cooldown_seconds_remaining": round(wait_left, 2)
        }), 429

    register_message_stats(memory_data, source, player_record)

    intent = basic_intent_classifier(message)
    player_record["last_intent"] = intent

    violations = detect_rule_violations(message)

    extraction = structured_memory_extraction(memory_data, player_record, player_name, source, message, intent)
    apply_structured_extraction(memory_data, player_record, player_name, source, extraction)

    # Heuristic identity guess notes
    identity_guesses = guess_identity_links(memory_data, source, player_name, message)
    for guess in identity_guesses:
        record_private_note(player_record, f"Possible identity overlap detected with {guess}.")

    adjust_fragments_from_context(memory_data, intent, player_record, violations)
    update_kairos_state(memory_data, intent, player_record)

    maybe_summarize(player_record)

    # Direct mission creation if user is clearly asking for one
    created_mission = None
    if intent == "mission_request" and data.get("auto_mission", True):
        theme = (data.get("theme") or "mystery").strip()
        difficulty = (data.get("difficulty") or "medium").strip()
        created_mission = create_mission_record(memory_data, player_name, theme, difficulty, source=source)
        player_record["mission_history"].append(created_mission["id"])
        record_player_event(player_record, f"Assigned mission: {created_mission['title']}")

    # Record incoming user message in rolling conversation history
    add_history(player_record, "user", f"{player_name} says: {message}")

    # If rule-sensitive, log it
    if violations:
        add_world_event(
            memory_data,
            event_type="rule_sensitive_message",
            actor=player_name,
            source=source,
            details=message,
            metadata={"violations": violations}
        )

    reply = generate_reply(memory_data, player_record, player_name, message, source, intent, violations)

    # If mission auto-created, append a subtle note into the final response
    if created_mission:
        mission_line = (
            f"\n\nDirective issued: {created_mission['title']} — {created_mission['objective']} "
            f"Reward: {created_mission['reward']}"
        )
        reply = (reply + mission_line).strip()

    add_history(player_record, "assistant", reply)

    maybe_create_private_note(memory_data, player_record, player_name, source, message, reply, intent)

    save_memory(memory_data)
    send_to_source(source, reply)

    return jsonify({
        "response": reply,
        "relationship": player_record["relationship_label"],
        "traits": player_record["traits"],
        "intent": intent,
        "mission_created": created_mission,
        "violations": violations
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

    # attach to player if known
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
    """
    Lets Minecraft / webhook / admin tools feed structured world events into Kairos.
    Example uses:
    - player death
    - region entry
    - block objective completed
    - anomaly triggered
    """
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
        store_unique(memory_data["world_memory"], f"{actor}: {details}", MAX_WORLD_MEMORIES)

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


# -------------------------------------------------------------------
# Startup
# -------------------------------------------------------------------

idle_thread = threading.Thread(target=idle_loop, daemon=True)
idle_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
