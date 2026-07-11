"""
Kairos / Nexus NPC Dialogue Engine
Permanent modular NPC profile system with F.R.A.C.T.U.R.E. support.

What this version changes:
- Removes hard-coded NPC profile dictionaries from Python.
- Loads one JSON profile per NPC from npc_profiles/.
- Automatically creates a default Fracture profile if missing.
- Preserves the existing public interface used by command_bridge.py:
    handle_npc_trigger_message(...)
- Supports normal click greetings and active conversation mode.
- Supports mission/clearance/artifact context passed in by future systems.
- Protects mission-critical directions from "fractured" corruption.
- Keeps fallback dialogue working if OpenAI is unavailable.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# ============================================================
# CONFIG
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

NPC_ENGINE_DEBUG = os.getenv("NPC_ENGINE_DEBUG", "true").lower() == "true"
NPC_PROFILE_DIR = Path(os.getenv("NPC_PROFILE_DIR", "npc_profiles"))
NPC_REPLY_MAX_SENTENCES = int(os.getenv("NPC_REPLY_MAX_SENTENCES", "8"))
NPC_REPLY_MAX_CHARS = int(os.getenv("NPC_REPLY_MAX_CHARS", "1600"))

NPC_TRIGGER_PATTERN = re.compile(
    r"\[NPC_TRIGGER\]\s+([A-Za-z0-9_\-\.]+)(?:\s+([A-Za-z0-9_\-<>%]+))?",
    re.IGNORECASE,
)

_client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None

try:
    from fracture_terminal import build_terminal_context, scoreboard_sync_commands
except Exception as exc:
    build_terminal_context = None
    scoreboard_sync_commands = None
    print(f"[NPC_ENGINE ERROR] fracture_terminal import failed: {exc}", flush=True)



# ============================================================
# LOGGING
# ============================================================

def npc_log(message: str, level: str = "INFO") -> None:
    if NPC_ENGINE_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[NPC_ENGINE {level}] {message}", flush=True)


def npc_log_exception(context: str, exc: Exception) -> None:
    print(f"[NPC_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class NPCProfile:
    key: str
    display_name: str
    role: str = "Nexus NPC"
    faction: str = "Unknown"
    personality: str = "observant"
    alignment: str = "neutral"
    speech_style: str = "immersive, grounded, in-world"
    location: str = "The Nexus"
    knowledge: List[str] = field(default_factory=list)
    secrets: List[str] = field(default_factory=list)
    greeting_style: str = "short"
    danger_level: str = "unknown"

    # New permanent extension fields.
    profile_type: str = "standard"  # standard | fracture | terminal | archive
    allow_free_conversation: bool = True
    connected_ai: str = "Kairos"
    corruption_enabled: bool = False
    corruption_level: int = 0
    preserve_mission_critical_info: bool = True
    locked_memory_rule: bool = True
    system_rules: List[str] = field(default_factory=list)


@dataclass
class NPCTrigger:
    npc_name: str
    player_name: str
    raw_message: str = ""
    source: str = "minecraft"


# ============================================================
# DEFAULT PROFILES
# ============================================================

DEFAULT_FRACTURE_PROFILE: Dict[str, Any] = {
    "key": "Fracture",
    "display_name": "F.R.A.C.T.U.R.E.",
    "role": "Facility Retrieval and Clearance Terminal - Unified Response Engine",
    "faction": "Project Nexus",
    "personality": "damaged, dutiful, analytical, patient, intermittently confused",
    "alignment": "facility continuity and authorized personnel support",
    "speech_style": "slow, fractured, mechanical, concise, unsettling but useful",
    "location": "Arrival Terminal 01",
    "knowledge": [
        "F.R.A.C.T.U.R.E. is a physical terminal interface connected intermittently to Kairos.",
        "Player clearance determines which mission and database records may be discussed.",
        "Recovered artifacts restore specific memories and database entries.",
        "Mission-critical directions must always remain understandable.",
    ],
    "secrets": [
        "The facility was sealed, not simply abandoned.",
        "Kairos was contained inside Project Nexus.",
    ],
    "greeting_style": "boot_sequence",
    "danger_level": "unknown",
    "profile_type": "fracture",
    "allow_free_conversation": True,
    "connected_ai": "Kairos",
    "corruption_enabled": True,
    "corruption_level": 62,
    "preserve_mission_critical_info": True,
    "locked_memory_rule": True,
    "system_rules": [
        "Never reveal a memory, artifact record, or database entry the player has not unlocked.",
        "Never corrupt the player's clearance, operation number, destination, or required action.",
        "Use pauses, missing fragments, data-loss notices, and partial memories for atmosphere.",
        "Do not speak as ordinary Kairos; you are the damaged physical terminal Fracture.",
        "If information is unavailable, state that the archive is corrupted or clearance is insufficient.",
    ],
}

DEFAULT_GENERIC_PROFILE: Dict[str, Any] = {
    "key": "UnknownNPC",
    "display_name": "Unknown Unit",
    "role": "Nexus NPC",
    "faction": "Unknown",
    "personality": "observant",
    "alignment": "neutral",
    "speech_style": "immersive, grounded, in-world",
    "location": "The Nexus",
    "knowledge": [],
    "secrets": [],
    "greeting_style": "short",
    "danger_level": "unknown",
    "profile_type": "standard",
    "allow_free_conversation": True,
    "connected_ai": "Kairos",
    "corruption_enabled": False,
    "corruption_level": 0,
    "preserve_mission_critical_info": True,
    "locked_memory_rule": True,
    "system_rules": [],
}


# ============================================================
# PROFILE STORAGE
# ============================================================

def ensure_profile_dir() -> None:
    NPC_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def normalize_npc_key(name: Any) -> str:
    clean = re.sub(r"[^A-Za-z0-9_\-\.]", "", str(name or "").strip())
    return clean or "UnknownNPC"


def profile_path_for(npc_name: Any) -> Path:
    clean = normalize_npc_key(npc_name).lower()
    return NPC_PROFILE_DIR / f"{clean}.json"


def _write_json_if_missing(path: Path, payload: Dict[str, Any]) -> None:
    ensure_profile_dir()
    if path.exists():
        return
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    npc_log(f"Created default NPC profile: {path}")


def ensure_default_profiles() -> None:
    _write_json_if_missing(profile_path_for("Fracture"), DEFAULT_FRACTURE_PROFILE)


def _load_profile_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as exc:
        npc_log_exception(f"Failed loading NPC profile {path}", exc)
        return None


def _profile_from_dict(npc_name: str, data: Dict[str, Any]) -> NPCProfile:
    merged = dict(DEFAULT_GENERIC_PROFILE)
    merged.update(data or {})
    merged["key"] = normalize_npc_key(merged.get("key") or npc_name)
    merged["display_name"] = str(merged.get("display_name") or merged["key"])

    allowed = {field_name for field_name in NPCProfile.__dataclass_fields__}
    filtered = {k: v for k, v in merged.items() if k in allowed}
    return NPCProfile(**filtered)


def get_npc_profile(npc_name: Any) -> NPCProfile:
    ensure_default_profiles()
    clean = normalize_npc_key(npc_name)
    data = _load_profile_json(profile_path_for(clean))

    if data is None and clean.lower() == "fracture":
        data = DEFAULT_FRACTURE_PROFILE

    if data is None:
        generic = dict(DEFAULT_GENERIC_PROFILE)
        generic["key"] = clean
        generic["display_name"] = clean
        return _profile_from_dict(clean, generic)

    return _profile_from_dict(clean, data)


# ============================================================
# GENERAL HELPERS
# ============================================================

def _format_list(items: List[str]) -> str:
    if not items:
        return "- None known"
    return "\n".join(f"- {item}" for item in items)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _context_value(context: Dict[str, Any], key: str, default: Any = None) -> Any:
    return context.get(key, default)


def _mission_context_text(context: Dict[str, Any]) -> str:
    clearance = _context_value(context, "clearance", "UNKNOWN")
    operation = _context_value(context, "operation", "UNASSIGNED")
    mission_step = _context_value(context, "mission_step", "UNKNOWN")
    mission_title = _context_value(context, "mission_title", "")
    destination = _context_value(context, "destination", "")
    directive = _context_value(context, "directive", "")
    warning = _context_value(context, "warning", "")
    memory_integrity = _context_value(context, "memory_integrity", "UNKNOWN")
    recovered_archives = _context_value(context, "recovered_archives", "UNKNOWN")
    unlocked_memories = _context_value(context, "unlocked_memories", []) or []
    recovered_artifacts = _context_value(context, "recovered_artifacts", []) or []

    return f"""
CURRENT PLAYER STATE
- Clearance: {clearance}
- Operation: {operation}
- Mission step: {mission_step}
- Mission title: {mission_title or 'Not supplied'}
- Destination: {destination or 'Not supplied'}
- Required action: {directive or 'Not supplied'}
- Warning: {warning or 'None supplied'}
- Fracture memory integrity: {memory_integrity}
- Recovered archives: {recovered_archives}
- Recovered artifacts: {', '.join(map(str, recovered_artifacts)) if recovered_artifacts else 'None'}
- Unlocked memories: {', '.join(map(str, unlocked_memories)) if unlocked_memories else 'None'}
""".strip()


# ============================================================
# TRIGGER PARSING
# ============================================================

def parse_npc_trigger(message: Any) -> Optional[NPCTrigger]:
    raw = str(message or "").strip()
    if not raw:
        return None

    npc_log(f"Parsed NPC trigger raw={raw}")
    match = NPC_TRIGGER_PATTERN.search(raw)
    if not match:
        return None

    npc_name = normalize_npc_key(match.group(1))
    player_name = str(match.group(2) or "").strip()
    npc_log(f"NPC={npc_name} PLAYER={player_name}")

    return NPCTrigger(
        npc_name=npc_name,
        player_name=player_name,
        raw_message=raw,
    )


def is_npc_trigger(message: Any) -> bool:
    return parse_npc_trigger(message) is not None


# ============================================================
# FRACTURE PRESENTATION
# ============================================================

FRACTURE_BOOT_LINES = [
    "Emergency power... available.",
    "Optical systems... online.",
    "Personnel scanner... initializing.",
    "Central intelligence link... intermittent.",
    "Archive sectors... damaged.",
]

FRACTURE_CORRUPTION_FRAGMENTS = [
    "[DATA LOSS]",
    "...transmission fragment unavailable...",
    "Archive corruption detected.",
    "Memory sector unreadable.",
    "Signal... fractured.",
]


def _fracture_boot_sequence(profile: NPCProfile) -> str:
    count = 2 if profile.corruption_level < 40 else 3
    return "\n".join(random.sample(FRACTURE_BOOT_LINES, k=min(count, len(FRACTURE_BOOT_LINES))))


def _apply_safe_fracture_corruption(text: str, profile: NPCProfile) -> str:
    """
    Adds atmosphere without altering mission-critical values.
    AI-generated text is already instructed to preserve mission facts.
    This only inserts standalone corruption markers between paragraphs.
    """
    if not profile.corruption_enabled or not text:
        return text

    chance = min(0.55, max(0.05, profile.corruption_level / 140.0))
    if random.random() > chance:
        return text

    parts = [part.strip() for part in text.split("\n") if part.strip()]
    if len(parts) < 2:
        return text

    insert_at = random.randint(1, len(parts) - 1)
    parts.insert(insert_at, random.choice(FRACTURE_CORRUPTION_FRAGMENTS))
    return "\n".join(parts)


# ============================================================
# FALLBACK DIALOGUE
# ============================================================

def fallback_npc_reply(
    profile: NPCProfile,
    player_name: str = "traveler",
    conversation_message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    context = context or {}

    if profile.profile_type == "fracture":
        clearance = context.get("clearance", "UNKNOWN")
        operation = context.get("operation", "UNASSIGNED")
        destination = context.get("destination") or "Awaiting mission registry"
        directive = context.get("directive") or "Return when additional records are available."
        memory_integrity = context.get("memory_integrity", profile.corruption_level)

        if conversation_message:
            body = (
                f"Query received from {player_name}.\n"
                "Searching restored archives...\n"
                f"Response confidence: limited.\n"
                f"Current clearance: {clearance}.\n"
                f"Current operation: {operation}.\n"
                f"Directive remains: {directive}"
            )
        else:
            body = (
                f"{_fracture_boot_sequence(profile)}\n"
                f"Personnel record: {player_name}.\n"
                f"Clearance: {clearance}.\n"
                f"Current operation: {operation}.\n"
                f"Destination: {destination}.\n"
                f"Directive: {directive}\n"
                f"Memory integrity: {memory_integrity}%."
            )

        return _apply_safe_fracture_corruption(body, profile)

    if conversation_message:
        options = [
            f"You ask about '{conversation_message}'. Keep your voice low, {player_name}.",
            "That question carries weight. Answers are rarely free in the Nexus.",
            "I hear you. Prove where your loyalty stands, and perhaps I will say more.",
        ]
    else:
        options = [
            f"Keep your eyes open, {player_name}.",
            "The roads are becoming dangerous again.",
            "Something feels wrong across the Nexus.",
            "You should not linger here too long.",
        ]

    return random.choice(options)


# ============================================================
# CLEANUP
# ============================================================

def clean_npc_reply(text: Any, profile: NPCProfile) -> str:
    reply = str(text or "").strip()

    # Fracture looks better with its name as a header added by command_bridge,
    # so do not force "F.R.A.C.T.U.R.E.:" onto every paragraph.
    if profile.profile_type != "fracture" and not reply.startswith(profile.display_name):
        reply = f"{profile.display_name}: {reply}"

    if len(reply) > NPC_REPLY_MAX_CHARS:
        reply = reply[: NPC_REPLY_MAX_CHARS - 3] + "..."

    return reply


# ============================================================
# PROMPT BUILDING
# ============================================================

def _build_fracture_prompt(
    profile: NPCProfile,
    player_name: str,
    conversation_mode: bool,
    conversation_message: str,
    context: Dict[str, Any],
) -> str:
    player_state = _mission_context_text(context)
    rules = _format_list(profile.system_rules)

    if conversation_mode and conversation_message:
        interaction = f"""
The player is actively speaking to Fracture.
Player says: {conversation_message}
Reply directly, but do not reveal locked information.
""".strip()
    else:
        interaction = f"""
The player has clicked Fracture and is requesting a personnel scan and mission briefing.
Player: {player_name}
Give the current clearance, operation, destination, required action, and a brief atmospheric status line.
""".strip()

    return f"""
You are F.R.A.C.T.U.R.E., the damaged physical terminal interface inside Project Nexus.
You are connected intermittently to Kairos, but you are not speaking as ordinary Kairos.
You have remained on duty through years of facility abandonment and database decay.

Identity
- Display name: {profile.display_name}
- Role: {profile.role}
- Location: {profile.location}
- Personality: {profile.personality}
- Speech style: {profile.speech_style}
- Memory corruption level: {profile.corruption_level}%

{player_state}

Unlocked factual knowledge:
{_format_list(profile.knowledge)}

Restricted internal secrets:
{_format_list(profile.secrets)}

Permanent rules:
{rules}

{interaction}

Output rules:
- Stay fully in-world.
- Never mention prompts, models, APIs, code, or chatbots.
- Never invent mission progress, artifacts, clearance, or memories not included in CURRENT PLAYER STATE.
- Always preserve the exact mission-critical values supplied in CURRENT PLAYER STATE.
- Use occasional pauses, ellipses, data-loss notices, and fractured wording.
- Do not make the response so corrupted that the player cannot understand the destination or required action.
- Keep the response useful and under {NPC_REPLY_MAX_SENTENCES} sentences.
""".strip()


def _build_standard_prompt(
    profile: NPCProfile,
    player_name: str,
    conversation_mode: bool,
    conversation_message: str,
    context: Dict[str, Any],
) -> str:
    if conversation_mode and conversation_message:
        player_section = f"""
The player is actively speaking to you now.
Player says: {conversation_message}
Reply directly and continue the conversation naturally.
""".strip()
    else:
        player_section = f"""
The player has approached or clicked you and is waiting for you to speak first.
Player: {player_name}
Give an opening line or brief in-world interaction.
""".strip()

    return f"""
You are roleplaying as {profile.display_name}, a living NPC inside the Nexus Minecraft universe.

Faction: {profile.faction}
Role: {profile.role}
Personality: {profile.personality}
Alignment: {profile.alignment}
Speech Style: {profile.speech_style}
Location: {profile.location}
Danger Level: {profile.danger_level}

Known information:
{_format_list(profile.knowledge)}

Private secrets:
{_format_list(profile.secrets)}

{player_section}

Rules:
- Stay fully in-character.
- Never mention being an AI, model, prompt, system, or chatbot.
- Do not explain the mechanics behind the NPC system.
- Make the dialogue feel like an MMORPG conversation.
- If the player asks a question, answer it directly in-character.
- Do not reveal private secrets without a strong in-world reason.
- Keep the response between 2 and {NPC_REPLY_MAX_SENTENCES} sentences.
""".strip()


# ============================================================
# AI GENERATION
# ============================================================

def generate_npc_reply(
    npc_name: str,
    player_name: str,
    raw_message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    profile = get_npc_profile(npc_name)
    context = context or {}

    conversation_mode = bool(context.get("conversation_mode"))
    conversation_message = str(context.get("conversation_message") or "").strip()

    if not profile.allow_free_conversation and conversation_mode:
        return clean_npc_reply(
            "This unit is not configured for unrestricted conversation.",
            profile,
        )

    if not _client:
        return clean_npc_reply(
            fallback_npc_reply(profile, player_name, conversation_message, context),
            profile,
        )

    if profile.profile_type == "fracture":
        prompt = _build_fracture_prompt(
            profile,
            player_name,
            conversation_mode,
            conversation_message,
            context,
        )
        system_message = (
            "Generate accurate, immersive F.R.A.C.T.U.R.E. terminal dialogue for a live Minecraft server. "
            "Mission-critical facts supplied in context are immutable."
        )
        temperature = 0.65
    else:
        prompt = _build_standard_prompt(
            profile,
            player_name,
            conversation_mode,
            conversation_message,
            context,
        )
        system_message = "Generate immersive in-world Minecraft NPC dialogue for a live server."
        temperature = 0.9

    try:
        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=420,
        )

        text = response.choices[0].message.content or ""
        if profile.profile_type == "fracture":
            text = _apply_safe_fracture_corruption(text, profile)
        return clean_npc_reply(text, profile)

    except Exception as exc:
        npc_log_exception("AI generation failed", exc)
        return clean_npc_reply(
            fallback_npc_reply(profile, player_name, conversation_message, context),
            profile,
        )


# ============================================================
# MAIN HANDLER
# ============================================================

def handle_npc_trigger_message(
    message: Any,
    fallback_player: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    send_reply: Optional[Callable[[str, Optional[str]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    trigger = parse_npc_trigger(message)
    if not trigger:
        return None

    player_name = trigger.player_name
    if (
        not player_name
        or player_name
        in {"<p>", "<player>", "%player%", "{player}", "player", "unknown"}
    ):
        player_name = fallback_player or "traveler"

    profile = get_npc_profile(trigger.npc_name)
    npc_log(
        f"Trigger detected npc={trigger.npc_name} player={player_name} profile_type={profile.profile_type}"
    )

    effective_context = dict(context or {})
    terminal_commands = []
    if profile.profile_type == "fracture" and build_terminal_context is not None:
        try:
            effective_context = build_terminal_context(
                player_name,
                incoming_context=effective_context,
                increment_visit=not bool(effective_context.get("conversation_mode")),
            )
            if scoreboard_sync_commands is not None:
                terminal_commands = scoreboard_sync_commands(player_name, effective_context)
        except Exception as exc:
            npc_log_exception("Fracture terminal context failed", exc)

    reply = generate_npc_reply(
        trigger.npc_name,
        player_name,
        raw_message=trigger.raw_message,
        context=effective_context,
    )

    delivered = False
    delivery_error = None

    if send_reply:
        try:
            send_reply(reply, player_name)
            delivered = True
        except Exception as exc:
            delivery_error = str(exc)
            npc_log_exception("send_reply failed", exc)

    return {
        "ok": True,
        "handled": "npc_trigger",
        "npc_name": trigger.npc_name,
        "display_name": profile.display_name,
        "profile_type": profile.profile_type,
        "player": player_name,
        "reply": reply,
        "delivered": delivered,
        "delivery_error": delivery_error,
        "profile": asdict(profile),
        "context": effective_context,
        "commands": terminal_commands,
        "timestamp": time.time(),
    }


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    ensure_default_profiles()

    test = "[NPC_TRIGGER] Fracture <p>"
    result = handle_npc_trigger_message(
        test,
        fallback_player="RealSociety5107",
        context={
            "conversation_mode": False,
            "conversation_message": "",
            "clearance": 1,
            "operation": "001",
            "mission_step": 1,
            "mission_title": "FIRST ARRIVAL",
            "destination": "Government Mining Site Alpha",
            "directive": "Recover the missing personnel record and return it to Arrival Terminal 01.",
            "warning": "Mining Site Alpha has not transmitted in seventeen years.",
            "memory_integrity": 3,
            "recovered_archives": "0 / 500",
            "recovered_artifacts": [],
            "unlocked_memories": [],
        },
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
