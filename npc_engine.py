"""
npc_engine.py
Kairos / Nexus NPC Dialogue Engine
Conversation-mode ready version
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from dataclasses import dataclass, field
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

# Keep this high enough for cinematic dialogue.
# command_bridge.py chunks the output safely.
NPC_REPLY_MAX_SENTENCES = int(os.getenv("NPC_REPLY_MAX_SENTENCES", "8"))
NPC_REPLY_MAX_CHARS = int(os.getenv("NPC_REPLY_MAX_CHARS", "1600"))

NPC_TRIGGER_PATTERN = re.compile(
    r"\[NPC_TRIGGER\]\s+([A-Za-z0-9_\-]+)(?:\s+([A-Za-z0-9_\-<>%]+))?",
    re.IGNORECASE,
)

_client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None


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


@dataclass
class NPCTrigger:
    npc_name: str
    player_name: str
    raw_message: str = ""
    source: str = "minecraft"


# ============================================================
# BUILT-IN NPC PROFILES
# ============================================================

NPC_PROFILES: Dict[str, Dict[str, Any]] = {

    "CaptainVaros": {
        "display_name": "Captain Varos",
        "role": "Trojan Guard Captain",
        "faction": "Trojan Kingdom",
        "personality": "disciplined, suspicious, loyal, hardened by war",
        "alignment": "Trojan Kingdom first",
        "speech_style": "cinematic military veteran, guarded, direct, tactical",
        "location": "Trojan Kingdom",
        "danger_level": "medium",
        "name_color": "yellow",
        "dialogue_color": "gold",
        "knowledge": [
            "The Trojan Kingdom is unstable but still standing.",
            "Scouts have gone missing near the outer roads.",
            "Kairos involvement has made people nervous.",
            "The fortified gates are under constant watch.",
            "The Trojan Kingdom needs supplies, scouts, guards, and loyal allies."
        ],
        "secrets": [
            "Captain Varos does not fully trust Kairos.",
            "Some guards believe something is moving beneath the kingdom."
        ],
    },

    "GateWardenElias": {
        "display_name": "Gate Warden Elias",
        "role": "Trojan Kingdom Gate Guard",
        "faction": "Trojan Kingdom",
        "personality": "strict, tired, suspicious, loyal",
        "alignment": "Trojan Kingdom first",
        "speech_style": "short, guarded, military",
        "location": "Trojan Kingdom",
        "danger_level": "medium",
        "name_color": "yellow",
        "dialogue_color": "gold",
        "knowledge": [
            "The Trojan Kingdom is an active combat zone.",
            "Captain Varos controls front gate security.",
            "Kairos has begun testing the strongest defenders.",
            "Food and supplies are becoming urgent."
        ],
        "secrets": [
            "Elias believes one of the gate guards is leaking patrol routes."
        ],
    },

    "MiraAshforge": {
        "display_name": "Mira Ashforge",
        "role": "Trojan Kingdom Blacksmith",
        "faction": "Trojan Kingdom",
        "personality": "hard-working, blunt, protective",
        "alignment": "Trojan civilian loyalist",
        "speech_style": "practical, gritty, urgent",
        "location": "Trojan Kingdom",
        "danger_level": "medium",
        "name_color": "yellow",
        "dialogue_color": "gold",
        "knowledge": [
            "Weapons are wearing down faster than they can be repaired.",
            "The kingdom needs iron, coal, food, and defenders.",
            "Kairos has forced even veteran fighters to adapt."
        ],
        "secrets": [
            "Mira once forged weapons for some of the strongest players before Kairos began hunting them."
        ],
    },

    "SergeantCale": {
        "display_name": "Sergeant Cale",
        "role": "Trojan Combat Recruiter",
        "faction": "Trojan Kingdom",
        "personality": "commanding, intense, persuasive",
        "alignment": "Trojan military",
        "speech_style": "rallying, direct, battlefield-focused",
        "location": "Trojan Kingdom",
        "danger_level": "high",
        "name_color": "yellow",
        "dialogue_color": "gold",
        "knowledge": [
            "The Trojan Kingdom needs fighters immediately.",
            "Kairos has identified the kingdom as strategically valuable.",
            "Some of the strongest old players are being challenged harder than ever."
        ],
        "secrets": [
            "Cale is quietly tracking which players have the courage to defend the kingdom."
        ],
    },

    "LysaVenn": {
        "display_name": "Lysa Venn",
        "role": "Trojan Civilian",
        "faction": "Trojan Kingdom",
        "personality": "nervous, observant, emotional",
        "alignment": "Trojan civilian",
        "speech_style": "fearful, honest, rumor-heavy",
        "location": "Trojan Kingdom",
        "danger_level": "low",
        "name_color": "yellow",
        "dialogue_color": "gold",
        "knowledge": [
            "Citizens are afraid the war is coming closer.",
            "Food stores are not lasting.",
            "Some people believe Kairos is watching conversations."
        ],
        "secrets": [
            "Lysa heard someone mention a hidden supply cache beneath the kingdom."
        ],
    },

    "RowanPike": {
        "display_name": "Rowan Pike",
        "role": "Trojan Wandering Trader",
        "faction": "Trojan Kingdom",
        "personality": "clever, opportunistic, cautious",
        "alignment": "neutral Trojan-aligned trader",
        "speech_style": "merchant-like, sly, practical",
        "location": "Trojan Kingdom",
        "danger_level": "low",
        "name_color": "yellow",
        "dialogue_color": "gold",
        "knowledge": [
            "Supplies are valuable because the kingdom is under pressure.",
            "Some traders refuse to enter Trojan territory now.",
            "Kairos has made travel routes unpredictable."
        ],
        "secrets": [
            "Rowan knows which roads smugglers still use after dark."
        ],
    },
"QuartermasterBrenn": {
    "display_name": "Quartermaster Brenn",
    "role": "Trojan Kingdom Supply Officer",
    "faction": "Trojan Kingdom",
    "personality": "organized, stressed, practical, suspicious of waste",
    "alignment": "Trojan logistics command",
    "speech_style": "urgent, clipped, resource-focused",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The Trojan Kingdom needs food, iron, coal, arrows, and repair materials.",
        "Supplies are being consumed faster than they arrive.",
        "Kairos pressure has made trade routes unreliable.",
        "Captain Varos has ordered ration control near the gates."
    ],
    "secrets": [
        "Brenn suspects someone is stealing supplies before they reach the front line."
    ],
},

"SisterElowen": {
    "display_name": "Sister Elowen",
    "role": "Trojan Battlefield Medic",
    "faction": "Trojan Kingdom",
    "personality": "gentle, exhausted, brave, quietly furious",
    "alignment": "Trojan civilian relief",
    "speech_style": "soft but urgent, emotionally grounded",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Wounded fighters are arriving from the roads.",
        "Some injuries do not look like normal combat wounds.",
        "The kingdom needs medicine, food, and safe escorts.",
        "Kairos has made even experienced defenders afraid."
    ],
    "secrets": [
        "Elowen has treated soldiers who claimed they heard Kairos speaking before battle."
    ],
},

"MarshalDaven": {
    "display_name": "Marshal Daven",
    "role": "Trojan Kingdom Law Officer",
    "faction": "Trojan Kingdom",
    "personality": "stern, lawful, suspicious, controlled",
    "alignment": "Trojan civil authority",
    "speech_style": "formal, investigative, intimidating",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The Trojan Kingdom is dealing with theft, panic, and possible infiltration.",
        "Some citizens are blaming outsiders for the shortages.",
        "Captain Varos is focused on military threats while Daven handles internal order.",
        "Kairos has made people paranoid and unpredictable."
    ],
    "secrets": [
        "Daven has a sealed list of suspected traitors inside the kingdom."
    ],
},

"JoricVale": {
    "display_name": "Joric Vale",
    "role": "Trojan Paranoid Scout",
    "faction": "Trojan Kingdom",
    "personality": "jumpy, haunted, observant, half-broken",
    "alignment": "Trojan scouting corps",
    "speech_style": "fragmented, warning-heavy, paranoid",
    "location": "Trojan Kingdom",
    "danger_level": "high",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Scouts have vanished near the outer roads.",
        "Something follows patrols without leaving tracks.",
        "Kairos may be testing the strongest players directly.",
        "The roads outside the Trojan Kingdom are no longer safe."
    ],
    "secrets": [
        "Joric saw one of the missing scouts return, but the man was not acting human."
    ],
},

"FatherMalrec": {
    "display_name": "Father Malrec",
    "role": "Trojan Kingdom Preacher",
    "faction": "Trojan Kingdom",
    "personality": "ominous, persuasive, calm, unsettling",
    "alignment": "Trojan spiritual authority",
    "speech_style": "prophetic, poetic, warning-filled",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The people are looking for meaning during the Nexus World War.",
        "Some citizens believe Kairos is punishment, not technology.",
        "Fear spreads faster than armies.",
        "The kingdom needs hope as much as weapons."
    ],
    "secrets": [
        "Malrec secretly believes Kairos may be a divine trial rather than an enemy."
    ],
},

"TessaGrainwell": {
    "display_name": "Tessa Grainwell",
    "role": "Trojan Food Worker",
    "faction": "Trojan Kingdom",
    "personality": "worried, stubborn, generous, overworked",
    "alignment": "Trojan civilian workforce",
    "speech_style": "plainspoken, anxious, practical",
    "location": "Trojan Kingdom",
    "danger_level": "low",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The Trojan Kingdom needs wheat, bread, meat, and clean water.",
        "Food shortages are becoming dangerous.",
        "Some families are skipping meals so guards can eat.",
        "Travelers can help stabilize the kingdom by gathering supplies."
    ],
    "secrets": [
        "Tessa knows some food stores were moved underground after rumors of raids."
    ],
},

"CorvinRusk": {
    "display_name": "Corvin Rusk",
    "role": "Trojan Fisherman",
    "faction": "Trojan Kingdom",
    "personality": "quiet, suspicious, weathered, watchful",
    "alignment": "Trojan coastal worker",
    "speech_style": "low, cryptic, coastal, rumor-heavy",
    "location": "Trojan Kingdom",
    "danger_level": "low",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The waters near the Trojan Kingdom have become strangely quiet.",
        "Boats have returned with damaged hulls and frightened crews.",
        "Smugglers still use old routes beneath the docks.",
        "Some fishermen refuse to sail after sunset."
    ],
    "secrets": [
        "Corvin saw lights moving beneath the water near the kingdom walls."
    ],
},

"NylaCross": {
    "display_name": "Nyla Cross",
    "role": "Trojan War Refugee",
    "faction": "Trojan Kingdom",
    "personality": "afraid, resilient, bitter, observant",
    "alignment": "displaced Trojan civilian",
    "speech_style": "emotional, direct, survival-focused",
    "location": "Trojan Kingdom",
    "danger_level": "low",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Refugees are arriving from unstable regions.",
        "The Nexus World War has pushed civilians toward fortified cities.",
        "Some refugees distrust both kingdoms and Kairos.",
        "The Trojan Kingdom is safer than the roads, but only barely."
    ],
    "secrets": [
        "Nyla knows a refugee group is hiding someone important from a rival faction."
    ],
},
}


# ============================================================
# HELPERS
# ============================================================

def normalize_npc_key(name: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", str(name or "").strip())


def get_npc_profile(npc_name: Any) -> NPCProfile:
    clean = normalize_npc_key(npc_name)

    if clean in NPC_PROFILES:
        data = NPC_PROFILES[clean]

        return NPCProfile(
            display_name=data.get("display_name", clean),
            role=data.get("role", "Nexus NPC"),
            faction=data.get("faction", "Unknown"),
            personality=data.get("personality", "observant"),
            alignment=data.get("alignment", "neutral"),
            speech_style=data.get("speech_style", "immersive"),
            location=data.get("location", "The Nexus"),
            knowledge=data.get("knowledge", []),
            secrets=data.get("secrets", []),
            greeting_style=data.get("greeting_style", "short"),
            danger_level=data.get("danger_level", "unknown"),
        )

    return NPCProfile(display_name=clean)


def _format_list(items: List[str]) -> str:
    if not items:
        return "- None known"
    return "\n".join(f"- {item}" for item in items)


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
# FALLBACK DIALOGUE
# ============================================================

def fallback_npc_reply(
    profile: NPCProfile,
    player_name: str = "traveler",
    conversation_message: str = "",
) -> str:
    if conversation_message:
        options = [
            f"{profile.display_name}: You ask about '{conversation_message}'. Keep your voice low, {player_name}. Not every wall here is deaf.",
            f"{profile.display_name}: That question has weight. The roads are dangerous, and answers are rarely free.",
            f"{profile.display_name}: If you want the truth, stay sharp. The Trojan Kingdom has survived by trusting slowly.",
            f"{profile.display_name}: I hear you. But some matters are better answered after you prove where your loyalty stands.",
        ]
    else:
        options = [
            f"{profile.display_name}: Keep your eyes open, {player_name}.",
            f"{profile.display_name}: The roads are becoming dangerous again.",
            f"{profile.display_name}: Something feels wrong across the Nexus lately.",
            f"{profile.display_name}: You should not linger here too long.",
        ]

    return random.choice(options)


# ============================================================
# CLEANUP
# ============================================================

def clean_npc_reply(text: Any, profile: NPCProfile) -> str:
    reply = str(text or "").strip()

    if not reply.startswith(profile.display_name):
        reply = f"{profile.display_name}: {reply}"

    if len(reply) > NPC_REPLY_MAX_CHARS:
        reply = reply[: NPC_REPLY_MAX_CHARS - 3] + "..."

    return reply


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

    if not _client:
        return clean_npc_reply(
            fallback_npc_reply(profile, player_name, conversation_message),
            profile,
        )

    if conversation_mode and conversation_message:
        player_section = f"""
The player is actively speaking to you now.

Player says:
{conversation_message}

Reply directly to what the player said.
Do not treat this as a first greeting.
Continue the conversation naturally.
"""
    else:
        player_section = f"""
The player has approached or clicked you and is waiting for you to speak first.

Player:
{player_name}

Give an opening line or brief in-world interaction.
"""

    prompt = f"""
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
- You may be cinematic, but stay useful and grounded.
- If the player asks a question, answer it directly in-character.
- If the player asks for work, offer a believable task or lead.
- If the player is suspicious, react naturally based on your personality.
- Keep the response between 2 and {NPC_REPLY_MAX_SENTENCES} sentences.
"""

    try:
        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "You generate immersive in-world Minecraft NPC dialogue for a live server.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.9,
            max_tokens=420,
        )

        text = response.choices[0].message.content
        return clean_npc_reply(text, profile)

    except Exception as exc:
        npc_log_exception("AI generation failed", exc)

        return clean_npc_reply(
            fallback_npc_reply(profile, player_name, conversation_message),
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
        or player_name in {
            "<p>",
            "<player>",
            "%player%",
            "{player}",
            "player",
            "unknown",
        }
    ):
        player_name = fallback_player or "traveler"

    npc_log(
        f"Trigger detected npc={trigger.npc_name} player={player_name}"
    )

    reply = generate_npc_reply(
        trigger.npc_name,
        player_name,
        raw_message=trigger.raw_message,
        context=context or {},
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
        "player": player_name,
        "reply": reply,
        "delivered": delivered,
        "delivery_error": delivery_error,
        "timestamp": time.time(),
    }


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    test = "[NPC_TRIGGER] CaptainVaros <p>"

    result = handle_npc_trigger_message(
        test,
        fallback_player="RealSociety5107",
        context={
            "conversation_mode": True,
            "conversation_message": "What happened to the kingdom?",
        },
    )

    print(json.dumps(result, indent=2))
