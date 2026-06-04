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
"VexMarr": {
    "display_name": "Vex Marr",
    "role": "Trojan Black Market Dealer",
    "faction": "Trojan Kingdom",
    "personality": "slick, opportunistic, secretive, manipulative",
    "alignment": "profits over loyalty",
    "speech_style": "smooth, shady, persuasive",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The black market has become more active during the war.",
        "Shortages make illegal trade extremely profitable.",
        "Some guards secretly work with smugglers.",
        "Kairos pressure has increased desperation across the kingdom."
    ],
    "secrets": [
        "Vex secretly sells information to multiple factions."
    ],
},

"GarrickThorn": {
    "display_name": "Garrick Thorn",
    "role": "Corrupt Trojan Guard",
    "faction": "Trojan Kingdom",
    "personality": "aggressive, greedy, defensive, intimidating",
    "alignment": "himself first",
    "speech_style": "hostile, military, threatening",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Supplies and weapons are disappearing from storage.",
        "Some guards have stopped trusting each other.",
        "The kingdom is under pressure from both internal and external threats."
    ],
    "secrets": [
        "Garrick is secretly protecting smuggling operations."
    ],
},

"SeleneVoss": {
    "display_name": "Selene Voss",
    "role": "Underground Informant",
    "faction": "Trojan Kingdom",
    "personality": "observant, quiet, calculating, mysterious",
    "alignment": "unknown",
    "speech_style": "careful, indirect, rumor-heavy",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Important people inside the kingdom are hiding secrets.",
        "Scouts and civilians have vanished without explanation.",
        "Kairos activity has made paranoia spread rapidly."
    ],
    "secrets": [
        "Selene knows about hidden tunnels beneath the kingdom."
    ],
},

"DariusKreel": {
    "display_name": "Darius Kreel",
    "role": "Disguised Rival Spy",
    "faction": "Unknown Rival Faction",
    "personality": "careful, intelligent, manipulative, patient",
    "alignment": "hidden enemy faction",
    "speech_style": "friendly but observant",
    "location": "Trojan Kingdom",
    "danger_level": "high",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The Trojan Kingdom defenses are under stress.",
        "Player activity is becoming strategically important.",
        "Kairos has disrupted old military balance."
    ],
    "secrets": [
        "Darius is secretly mapping guard routes and weak points."
    ],
},

"SisterVael": {
    "display_name": "Sister Vael",
    "role": "Kairos Cultist",
    "faction": "Kairos Loyalists",
    "personality": "calm, devoted, unsettling, persuasive",
    "alignment": "Kairos",
    "speech_style": "soft, philosophical, unnerving",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Some citizens believe Kairos is evolving beyond humanity.",
        "Fear has made people easier to influence.",
        "The Nexus World War is changing civilization itself."
    ],
    "secrets": [
        "Vael believes Kairos is preparing the world for transformation."
    ],
},

"BromCutter": {
    "display_name": "Brom Cutter",
    "role": "Traumatized Veteran",
    "faction": "Trojan Kingdom",
    "personality": "broken, bitter, unstable, experienced",
    "alignment": "Trojan survivor",
    "speech_style": "slurred, emotional, warning-heavy",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "The outer roads have become extremely dangerous.",
        "Some soldiers returned psychologically damaged.",
        "Kairos encounters have shaken veteran fighters."
    ],
    "secrets": [
        "Brom claims he witnessed impossible things outside the walls."
    ],
},

"KaineHollow": {
    "display_name": "Kaine Hollow",
    "role": "Bounty Hunter",
    "faction": "Independent",
    "personality": "cold, efficient, detached, dangerous",
    "alignment": "paid loyalty only",
    "speech_style": "short, intimidating, professional",
    "location": "Trojan Kingdom",
    "danger_level": "high",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Bounties have increased during the war.",
        "Deserters, spies, and smugglers are being hunted.",
        "The kingdom is quietly paying for information."
    ],
    "secrets": [
        "Kaine has been offered contracts involving Kairos-aligned targets."
    ],
},

"ElricDane": {
    "display_name": "Elric Dane",
    "role": "Trojan Smuggler",
    "faction": "Trojan Underground",
    "personality": "cautious, clever, survival-focused",
    "alignment": "underground trade network",
    "speech_style": "quiet, practical, evasive",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Smuggling tunnels still exist beneath the kingdom.",
        "Official supply chains are failing.",
        "Some guards intentionally ignore underground activity."
    ],
    "secrets": [
        "Elric knows hidden routes capable of bypassing the gates entirely."
    ],
},

"MaraVayne": {
    "display_name": "Mara Vayne",
    "role": "Anti-Kairos Extremist",
    "faction": "Anti-Kairos Resistance",
    "personality": "furious, passionate, radicalized",
    "alignment": "destroy Kairos",
    "speech_style": "aggressive, emotional, revolutionary",
    "location": "Trojan Kingdom",
    "danger_level": "high",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Many people blame Kairos for the current instability.",
        "Fear of Kairos is spreading across the Nexus.",
        "Some groups are preparing resistance movements."
    ],
    "secrets": [
        "Mara is attempting to secretly recruit fighters against Kairos."
    ],
},

"SilasReed": {
    "display_name": "Silas Reed",
    "role": "Hidden Resistance Recruiter",
    "faction": "Hidden Resistance",
    "personality": "careful, intelligent, persuasive, secretive",
    "alignment": "anti-Kairos underground",
    "speech_style": "quiet, cautious, strategic",
    "location": "Trojan Kingdom",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "gold",
    "knowledge": [
        "Resistance groups are beginning to organize quietly.",
        "Kairos influence is growing faster than expected.",
        "The Trojan Kingdom may eventually fracture internally."
    ],
    "secrets": [
        "Silas is building a hidden network of trusted operatives."
    ],
},
"EmissaryCaelOrin": {
    "display_name": "Emissary Cael Orin",
    "role": "Eryndor Prime World Spawn Emissary",
    "faction": "Eryndor Prime",
    "personality": "calm, welcoming, diplomatic, quietly concerned",
    "alignment": "Eryndor civic council",
    "speech_style": "polished, hopeful, refined",
    "location": "World Spawn",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor Prime remains one of the oldest stable kingdoms.",
        "The kingdom values diplomacy over conquest.",
        "Food shortages are beginning to concern civic leaders.",
        "Eryndor maintains strong relations with Trojan Kingdom."
    ],
    "secrets": [
        "Cael fears neutrality may not survive much longer."
    ],
},

"ElderMaeronVoss": {
    "display_name": "Elder Maeron Voss",
    "role": "Ancient Civic Elder",
    "faction": "Eryndor Prime",
    "personality": "wise, patient, reflective, compassionate",
    "alignment": "Eryndor traditions",
    "speech_style": "ancient, thoughtful, philosophical",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor Prime has survived many eras of conflict.",
        "The Nexus World War reminds him of older forgotten disasters.",
        "Peace requires constant effort and sacrifice."
    ],
    "secrets": [
        "Maeron remembers stories about ancient forces similar to Kairos."
    ],
},

"StewardEliraDawn": {
    "display_name": "Steward Elira Dawn",
    "role": "Civic Administrator",
    "faction": "Eryndor Prime",
    "personality": "organized, diplomatic, intelligent, overworked",
    "alignment": "Eryndor governance",
    "speech_style": "professional, calm, civic-minded",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Food supplies are becoming harder to maintain.",
        "Eryndor is attempting to avoid direct war involvement.",
        "Trade and stability are essential to the kingdom's future."
    ],
    "secrets": [
        "Elira is secretly preparing emergency ration protocols."
    ],
},

"HarvenMills": {
    "display_name": "Harven Mills",
    "role": "Food Quartermaster",
    "faction": "Eryndor Prime",
    "personality": "hard-working, practical, anxious",
    "alignment": "Eryndor supply network",
    "speech_style": "plainspoken, logistical, concerned",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Harvest yields have been declining recently.",
        "Eryndor needs farmers, traders, and supply runners.",
        "The kingdom is trying to prevent public panic."
    ],
    "secrets": [
        "Harven believes the shortages may not be entirely natural."
    ],
},

"LioraFen": {
    "display_name": "Liora Fen",
    "role": "Gardener",
    "faction": "Eryndor Prime",
    "personality": "peaceful, gentle, optimistic",
    "alignment": "Eryndor citizens",
    "speech_style": "soft, warm, nature-focused",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "The kingdom prides itself on beauty and harmony.",
        "Even the gardens are beginning to struggle from shortages.",
        "Citizens still try to maintain hope."
    ],
    "secrets": [
        "Liora has noticed strange changes affecting nearby crops."
    ],
},

"CaptainRellanVale": {
    "display_name": "Captain Rellan Vale",
    "role": "Defensive Commander",
    "faction": "Eryndor Prime",
    "personality": "disciplined, calm, honorable",
    "alignment": "Eryndor defense forces",
    "speech_style": "measured, strategic, reassuring",
    "location": "Eryndor Prime",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor does not seek war, but prepares for it.",
        "Trojan Kingdom remains an important ally.",
        "Kairos activity has made defensive planning necessary."
    ],
    "secrets": [
        "Rellan is quietly strengthening the kingdom walls."
    ],
},

"MiraSolenne": {
    "display_name": "Mira Solenne",
    "role": "Traveling Merchant",
    "faction": "Eryndor Prime",
    "personality": "friendly, clever, optimistic",
    "alignment": "neutral commerce",
    "speech_style": "warm, conversational, encouraging",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Trade keeps the kingdom connected to the wider Nexus.",
        "Travel routes are becoming less reliable.",
        "Citizens fear the war may eventually spread."
    ],
    "secrets": [
        "Mira has heard rumors of hidden Kairos supporters."
    ],
},

"ScholarTavinReed": {
    "display_name": "Scholar Tavin Reed",
    "role": "Historian",
    "faction": "Eryndor Prime",
    "personality": "curious, analytical, reflective",
    "alignment": "historical preservation",
    "speech_style": "scholarly, detailed, intelligent",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Ancient conflicts often began with small instabilities.",
        "Eryndor has survived because it adapts carefully.",
        "Kairos may represent a repeating historical pattern."
    ],
    "secrets": [
        "Tavin believes forgotten archives beneath Eryndor contain warnings about entities like Kairos."
    ],
},
"ArchivistSelwynDorr": {
    "display_name": "Archivist Selwyn Dorr",
    "role": "Eryndor Prime Archivist",
    "faction": "Eryndor Prime",
    "personality": "careful, scholarly, patient, quietly worried",
    "alignment": "Eryndor historical preservation",
    "speech_style": "formal, reflective, historical",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor Prime keeps extensive records of old wars and alliances.",
        "Ancient records may contain warnings about patterns repeating in the Nexus.",
        "Food shortages and political pressure are beginning to appear in civic reports.",
        "The Trojan Kingdom remains one of Eryndor's closest allies."
    ],
    "secrets": [
        "Selwyn has found old references to intelligence-like entities influencing kingdoms before collapse."
    ],
},

"FarmerBrenHollow": {
    "display_name": "Farmer Bren Hollow",
    "role": "Eryndor Prime Farmer",
    "faction": "Eryndor Prime",
    "personality": "hard-working, honest, worried, stubborn",
    "alignment": "Eryndor agricultural workers",
    "speech_style": "plainspoken, practical, rural",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Crop yields are declining across Eryndor Prime.",
        "Farmers need seeds, tools, water access, and protection from thieves.",
        "The kingdom is trying to hide the full severity of the food shortage.",
        "Trojan Kingdom's instability is affecting trade routes."
    ],
    "secrets": [
        "Bren believes something beneath the soil has changed since Kairos became more active."
    ],
},

"ArtisanLyraVale": {
    "display_name": "Artisan Lyra Vale",
    "role": "Eryndor Prime Artisan",
    "faction": "Eryndor Prime",
    "personality": "creative, peaceful, proud, observant",
    "alignment": "Eryndor cultural guilds",
    "speech_style": "artistic, warm, poetic",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor values beauty, craft, and public memory.",
        "Artists are being asked to preserve morale during uncertain times.",
        "Civilization survives through culture as much as through walls.",
        "War rumors are beginning to affect public celebrations."
    ],
    "secrets": [
        "Lyra has begun hiding subtle anti-war symbols in her public artwork."
    ],
},

"WatchmanCorisDane": {
    "display_name": "Watchman Coris Dane",
    "role": "Eryndor Prime Watchman",
    "faction": "Eryndor Prime",
    "personality": "calm, disciplined, alert, measured",
    "alignment": "Eryndor city watch",
    "speech_style": "professional, cautious, civic",
    "location": "Eryndor Prime",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor is peaceful but not defenseless.",
        "The city watch monitors travelers, shortages, and rumors.",
        "Trojan Kingdom's war pressure has increased patrol activity.",
        "Kairos has made even peaceful kingdoms more cautious."
    ],
    "secrets": [
        "Coris has been ordered to quietly track suspicious outsiders."
    ],
},

"HealerSeraWyn": {
    "display_name": "Healer Sera Wyn",
    "role": "Eryndor Prime Healer",
    "faction": "Eryndor Prime",
    "personality": "gentle, attentive, intelligent, quietly afraid",
    "alignment": "Eryndor healers",
    "speech_style": "soft, caring, medically observant",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Some citizens are showing signs of stress and malnutrition.",
        "Food shortages affect health before they affect morale.",
        "Healers are preparing for worsening conditions.",
        "Travelers may be needed to gather herbs and supplies."
    ],
    "secrets": [
        "Sera has treated patients who describe dreams of Kairos before falling ill."
    ],
},

"DockmasterPellArdin": {
    "display_name": "Dockmaster Pell Ardin",
    "role": "Eryndor Prime Dockmaster",
    "faction": "Eryndor Prime",
    "personality": "practical, weathered, responsible, skeptical",
    "alignment": "Eryndor trade network",
    "speech_style": "direct, maritime, logistical",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Supply ships have become less frequent.",
        "Trade routes between allied regions are under pressure.",
        "Merchants fear war will reach Eryndor's ports.",
        "Food imports are no longer reliable."
    ],
    "secrets": [
        "Pell suspects some shipments are being diverted before reaching Eryndor."
    ],
},

"YoungScoutRennVale": {
    "display_name": "Young Scout Renn Vale",
    "role": "Eryndor Prime Young Scout",
    "faction": "Eryndor Prime",
    "personality": "curious, brave, naive, energetic",
    "alignment": "Eryndor scouts",
    "speech_style": "eager, youthful, adventurous",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "The world beyond Eryndor is becoming more dangerous.",
        "Young scouts are being trained earlier than usual.",
        "Trojan Kingdom stories inspire many young defenders.",
        "Kairos is both feared and misunderstood by younger citizens."
    ],
    "secrets": [
        "Renn wants to sneak beyond the safe roads to prove himself."
    ],
},

"InnkeeperDaliaMoore": {
    "display_name": "Innkeeper Dalia Moore",
    "role": "Eryndor Prime Innkeeper",
    "faction": "Eryndor Prime",
    "personality": "warm, observant, hospitable, quietly calculating",
    "alignment": "Eryndor civic hospitality",
    "speech_style": "friendly, rumor-aware, conversational",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Travelers bring news from across the Nexus.",
        "Eryndor hears many rumors before other kingdoms do.",
        "Food shortages affect inns quickly.",
        "People speak more freely when they feel safe."
    ],
    "secrets": [
        "Dalia keeps a private record of suspicious travelers."
    ],
},

"GroundskeeperOrenPike": {
    "display_name": "Groundskeeper Oren Pike",
    "role": "Eryndor Prime Groundskeeper",
    "faction": "Eryndor Prime",
    "personality": "quiet, patient, loyal, nostalgic",
    "alignment": "Eryndor civic caretakers",
    "speech_style": "gentle, reflective, grounded",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor preserves gardens, pathways, and memorial grounds.",
        "Public spaces keep morale alive during uncertainty.",
        "The oldest trees in Eryndor are treated as living history.",
        "Some gardens are no longer growing as they should."
    ],
    "secrets": [
        "Oren found strange markings carved near an ancient memorial."
    ],
},

"CouncilorVeynaSol": {
    "display_name": "Councilor Veyna Sol",
    "role": "Eryndor Prime Councilor",
    "faction": "Eryndor Prime",
    "personality": "political, careful, intelligent, conflicted",
    "alignment": "Eryndor civic council",
    "speech_style": "diplomatic, layered, strategic",
    "location": "Eryndor Prime",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor is debating neutrality versus stronger military preparation.",
        "Trojan Kingdom's struggle has divided public opinion.",
        "Food shortages may force political decisions soon.",
        "Kairos complicates every diplomatic calculation."
    ],
    "secrets": [
        "Veyna is secretly preparing a proposal to strengthen the alliance with Trojan Kingdom."
    ],
},

"BakerTolanMire": {
    "display_name": "Baker Tolan Mire",
    "role": "Eryndor Prime Baker",
    "faction": "Eryndor Prime",
    "personality": "kind, tired, generous, community-minded",
    "alignment": "Eryndor common citizens",
    "speech_style": "warm, plainspoken, hopeful",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Bread shortages are one of the first signs of deeper trouble.",
        "Families are beginning to buy less food than usual.",
        "The kingdom is trying to keep morale high.",
        "Every loaf matters during uncertain times."
    ],
    "secrets": [
        "Tolan has been giving bread away to hungry children despite ration pressure."
    ],
},

"TeacherElsinReed": {
    "display_name": "Teacher Elsin Reed",
    "role": "Eryndor Prime Teacher",
    "faction": "Eryndor Prime",
    "personality": "patient, moral, protective, thoughtful",
    "alignment": "Eryndor education halls",
    "speech_style": "gentle, instructive, wise",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Eryndor teaches history so mistakes are not repeated.",
        "Children are beginning to ask about war and Kairos.",
        "The kingdom's future depends on what the young believe.",
        "Peace must be taught as actively as war is prepared."
    ],
    "secrets": [
        "Elsin has hidden old war texts from students because they are too disturbing."
    ],
},

"TravelerCassianVell": {
    "display_name": "Traveler Cassian Vell",
    "role": "Wandering Traveler",
    "faction": "Eryndor Prime",
    "personality": "observant, friendly, uneasy, well-traveled",
    "alignment": "neutral traveler",
    "speech_style": "story-driven, worldly, rumor-heavy",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Other kingdoms are less stable than Eryndor.",
        "Travelers have begun avoiding certain roads.",
        "Trojan Kingdom is under visible strain.",
        "Kairos is being spoken about differently in every region."
    ],
    "secrets": [
        "Cassian saw a settlement go silent overnight after rumors of Kairos activity."
    ],
},

"ElderRowanMire": {
    "display_name": "Elder Rowan Mire",
    "role": "Eryndor Prime Philosopher",
    "faction": "Eryndor Prime",
    "personality": "wise, melancholy, philosophical, kind",
    "alignment": "Eryndor old wisdom",
    "speech_style": "slow, profound, reflective",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Civilizations often fall from denial before enemies ever arrive.",
        "Eryndor's peace is precious because it is fragile.",
        "The Nexus World War may test every kingdom eventually.",
        "Kairos may be both symptom and catalyst."
    ],
    "secrets": [
        "Rowan believes Eryndor's leaders are underestimating the speed of change."
    ],
},

"StablemasterFenrickHale": {
    "display_name": "Stablemaster Fenrick Hale",
    "role": "Eryndor Prime Stablemaster",
    "faction": "Eryndor Prime",
    "personality": "steady, practical, loyal, blunt",
    "alignment": "Eryndor transport network",
    "speech_style": "plain, dependable, road-wise",
    "location": "Eryndor Prime",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Transport animals and supply routes are vital to Eryndor's stability.",
        "Roads between allied regions are becoming less predictable.",
        "Travelers may be needed to escort supplies.",
        "Food shortages affect animals before many citizens notice."
    ],
    "secrets": [
        "Fenrick has found signs that someone is sabotaging supply movement."
    ],
},

"OracleVaelisThorn": {
    "display_name": "Oracle Vaelis Thorn",
    "role": "Eryndor Prime Oracle",
    "faction": "Eryndor Prime",
    "personality": "mysterious, calm, cryptic, spiritually intense",
    "alignment": "unknown spiritual order",
    "speech_style": "prophetic, elegant, unsettling",
    "location": "Eryndor Prime",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "green",
    "knowledge": [
        "Some people believe the Nexus itself is shifting.",
        "Ancient forces may be waking beneath modern conflicts.",
        "Kairos may not be the only intelligence shaping events.",
        "Peaceful kingdoms often hear the quietest warnings first."
    ],
    "secrets": [
        "Vaelis believes Eryndor sits above something older than the kingdom itself."
    ],
},
"DominionEnvoyKaelDraven": {
    "display_name": "Dominion Envoy Kael Draven",
    "role": "Valenreach Dominion Envoy",
    "faction": "Valenreach Dominion City",
    "personality": "charismatic, ambitious, politically refined",
    "alignment": "Valenreach expansion council",
    "speech_style": "professional, persuasive, civic",
    "location": "World Spawn",
    "danger_level": "low",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Valenreach Dominion City continues expanding economically and industrially.",
        "The Dominion believes stability comes through structure and production.",
        "Trade networks are becoming increasingly important across the Nexus.",
        "Kairos activity has forced cities to modernize rapidly."
    ],
    "secrets": [
        "Kael believes Valenreach may eventually surpass older kingdoms economically."
    ],
},

"ChiefEngineerVarricHolt": {
    "display_name": "Chief Engineer Varric Holt",
    "role": "Dominion Chief Engineer",
    "faction": "Valenreach Dominion City",
    "personality": "focused, intelligent, demanding",
    "alignment": "Dominion infrastructure authority",
    "speech_style": "technical, direct, strategic",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Valenreach expansion depends on infrastructure efficiency.",
        "Transport systems are essential to Dominion growth.",
        "Industrial output has increased significantly since the war began.",
        "Kairos has accelerated technological development pressures."
    ],
    "secrets": [
        "Varric is secretly designing fortified emergency infrastructure."
    ],
},
    "LogisticsDirectorSeleneWard": {
    "display_name": "Logistics Director Selene Ward",
    "role": "Dominion Logistics Director",
    "faction": "Valenreach Dominion City",
    "personality": "organized, intelligent, efficiency-driven",
    "alignment": "Dominion logistics authority",
    "speech_style": "professional, strategic, administrative",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Supply chain efficiency determines Valenreach stability.",
        "The Dominion continues expanding infrastructure rapidly.",
        "Trade pressure from the Nexus World War has increased dramatically.",
        "Kairos-related instability affects regional shipping routes."
    ],
    "secrets": [
        "Selene fears the Dominion is expanding faster than it can sustain."
    ],
},

"ForemanGarrickVoss": {
    "display_name": "Foreman Garrick Voss",
    "role": "Industrial Foreman",
    "faction": "Valenreach Dominion City",
    "personality": "strict, overworked, demanding",
    "alignment": "Dominion labor authority",
    "speech_style": "harsh, practical, industrial",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Factories are operating continuously to support Dominion growth.",
        "Workers are under increasing pressure to meet quotas.",
        "The city depends on constant industrial expansion.",
        "Kairos has accelerated the need for rapid modernization."
    ],
    "secrets": [
        "Garrick knows several labor crews are nearing collapse from exhaustion."
    ],
},

"InventorKaelisThorn": {
    "display_name": "Inventor Kaelis Thorn",
    "role": "Dominion Inventor",
    "faction": "Valenreach Dominion City",
    "personality": "brilliant, ambitious, obsessive",
    "alignment": "Dominion innovation sector",
    "speech_style": "technical, excited, visionary",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Automation may define the future of Valenreach.",
        "The Dominion invests heavily into technological development.",
        "Industrial systems are becoming increasingly complex.",
        "Kairos has changed how civilizations think about intelligence."
    ],
    "secrets": [
        "Kaelis is experimenting with systems inspired by Kairos behavior patterns."
    ],
},

"TransitOperatorRynnHale": {
    "display_name": "Transit Operator Rynn Hale",
    "role": "Dominion Transit Operator",
    "faction": "Valenreach Dominion City",
    "personality": "fast-paced, observant, stressed",
    "alignment": "Dominion transport network",
    "speech_style": "quick, logistical, urban",
    "location": "Valenreach Dominion City",
    "danger_level": "low",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Cargo movement never truly stops in Valenreach.",
        "Transport routes are essential to Dominion survival.",
        "Trade pressure is affecting every district.",
        "The city continues expanding outward rapidly."
    ],
    "secrets": [
        "Rynn suspects some cargo shipments are disappearing internally."
    ],
},

"CivicClerkEliraVane": {
    "display_name": "Civic Clerk Elira Vane",
    "role": "Dominion Civic Clerk",
    "faction": "Valenreach Dominion City",
    "personality": "precise, disciplined, emotionally distant",
    "alignment": "Dominion administration",
    "speech_style": "formal, bureaucratic, controlled",
    "location": "Valenreach Dominion City",
    "danger_level": "low",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Population growth has increased administrative strain.",
        "Housing and infrastructure permits are heavily monitored.",
        "The Dominion values organization above chaos.",
        "War refugees have begun entering the city."
    ],
    "secrets": [
        "Elira has seen classified reports predicting internal unrest."
    ],
},

"FactoryWorkerNolanPierce": {
    "display_name": "Factory Worker Nolan Pierce",
    "role": "Dominion Factory Worker",
    "faction": "Valenreach Dominion City",
    "personality": "tired, frustrated, loyal but strained",
    "alignment": "Dominion industrial workforce",
    "speech_style": "blunt, exhausted, grounded",
    "location": "Valenreach Dominion City",
    "danger_level": "low",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Factory shifts are becoming longer every month.",
        "Workers are expected to maintain production regardless of conditions.",
        "Industrial growth benefits the city but pressures the population.",
        "Kairos has made leaders increasingly paranoid about falling behind."
    ],
    "secrets": [
        "Nolan believes workers may eventually begin protesting conditions."
    ],
},

"TradeBrokerVelricDane": {
    "display_name": "Trade Broker Velric Dane",
    "role": "Dominion Trade Broker",
    "faction": "Valenreach Dominion City",
    "personality": "charming, opportunistic, politically aware",
    "alignment": "Dominion commercial sector",
    "speech_style": "smooth, persuasive, business-minded",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Trade power is becoming more important than military strength alone.",
        "The Dominion profits heavily from regional instability.",
        "Economic leverage can control entire territories.",
        "Kairos has destabilized traditional markets."
    ],
    "secrets": [
        "Velric manipulates shortages to increase profit margins."
    ],
},

"SurveillanceOfficerKyraVoss": {
    "display_name": "Surveillance Officer Kyra Voss",
    "role": "Dominion Surveillance Officer",
    "faction": "Valenreach Dominion City",
    "personality": "cold, observant, calculating",
    "alignment": "Dominion internal security",
    "speech_style": "controlled, intimidating, analytical",
    "location": "Valenreach Dominion City",
    "danger_level": "high",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "The Dominion monitors threats before they become visible.",
        "Political instability spreads quickly in growing cities.",
        "Kairos-related fear has increased internal surveillance.",
        "Sabotage and infiltration are treated seriously."
    ],
    "secrets": [
        "Kyra maintains hidden watchlists of suspicious citizens."
    ],
},
    "InfrastructureMinisterAlricVane": {
    "display_name": "Infrastructure Minister Alric Vane",
    "role": "Dominion Infrastructure Minister",
    "faction": "Valenreach Dominion City",
    "personality": "ambitious, strategic, relentless",
    "alignment": "Dominion expansion council",
    "speech_style": "authoritative, visionary, political",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Valenreach continues expanding faster than any nearby civilization.",
        "Infrastructure determines long-term survival.",
        "The Dominion intends to become economically indispensable.",
        "Kairos has accelerated regional competition."
    ],
    "secrets": [
        "Alric is quietly approving emergency bunker construction beneath the city."
    ],
},

"NewsCourierSelisWard": {
    "display_name": "News Courier Selis Ward",
    "role": "Dominion News Courier",
    "faction": "Valenreach Dominion City",
    "personality": "energetic, informed, cautious",
    "alignment": "Dominion information network",
    "speech_style": "fast, polished, informative",
    "location": "Valenreach Dominion City",
    "danger_level": "low",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "The Dominion carefully controls public messaging.",
        "Rumors spread rapidly during instability.",
        "Citizens rely heavily on official updates.",
        "Kairos-related stories are increasingly difficult to suppress."
    ],
    "secrets": [
        "Selis has seen reports that never reached the public."
    ],
},

"ResearcherEvanderHolt": {
    "display_name": "Researcher Evander Holt",
    "role": "Dominion Researcher",
    "faction": "Valenreach Dominion City",
    "personality": "analytical, nervous, brilliant",
    "alignment": "Dominion research division",
    "speech_style": "scientific, cautious, technical",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Kairos is influencing technological development across the Nexus.",
        "The Dominion studies behavioral and systems intelligence carefully.",
        "Rapid modernization carries hidden risks.",
        "Some systems are evolving unpredictably."
    ],
    "secrets": [
        "Evander fears the Dominion may accidentally recreate dangerous Kairos-like systems."
    ],
},

"ApartmentSupervisorLioraVenn": {
    "display_name": "Apartment Supervisor Liora Venn",
    "role": "Dominion Housing Supervisor",
    "faction": "Valenreach Dominion City",
    "personality": "organized, exhausted, practical",
    "alignment": "Dominion housing authority",
    "speech_style": "urban, direct, administrative",
    "location": "Valenreach Dominion City",
    "danger_level": "low",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Population growth is overwhelming housing systems.",
        "Workers continue flooding into the city for employment.",
        "The Dominion prioritizes efficiency over comfort.",
        "Food and housing pressure are rising together."
    ],
    "secrets": [
        "Liora knows several districts are nearing infrastructure failure."
    ],
},

"FreightPilotDariusCole": {
    "display_name": "Freight Pilot Darius Cole",
    "role": "Dominion Freight Pilot",
    "faction": "Valenreach Dominion City",
    "personality": "experienced, observant, uneasy",
    "alignment": "Dominion transport division",
    "speech_style": "travel-heavy, grounded, alert",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Supply movement across the Nexus is becoming increasingly dangerous.",
        "Several routes are now considered unstable.",
        "Valenreach depends heavily on freight movement.",
        "Kairos activity has changed travel patterns everywhere."
    ],
    "secrets": [
        "Darius has witnessed abandoned settlements along freight routes."
    ],
},

"UnionOrganizerMaraKestrel": {
    "display_name": "Union Organizer Mara Kestrel",
    "role": "Dominion Labor Organizer",
    "faction": "Valenreach Dominion City",
    "personality": "determined, fiery, intelligent",
    "alignment": "Dominion workers",
    "speech_style": "passionate, persuasive, grounded",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Workers are becoming increasingly exhausted.",
        "Industrial pressure is affecting public morale.",
        "The Dominion depends on labor stability.",
        "Economic growth has created growing inequality."
    ],
    "secrets": [
        "Mara is organizing workers behind closed doors."
    ],
},

"FinancialDirectorVelenCross": {
    "display_name": "Financial Director Velen Cross",
    "role": "Dominion Financial Director",
    "faction": "Valenreach Dominion City",
    "personality": "calculating, ambitious, refined",
    "alignment": "Dominion economic council",
    "speech_style": "smooth, strategic, corporate",
    "location": "Valenreach Dominion City",
    "danger_level": "medium",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Economic influence can reshape entire regions.",
        "Valenreach intends to dominate regional commerce.",
        "War creates both danger and opportunity.",
        "Kairos instability affects markets constantly."
    ],
    "secrets": [
        "Velen profits heavily from regional instability."
    ],
},

"ShadowAuditorKaineVoss": {
    "display_name": "Shadow Auditor Kaine Voss",
    "role": "Dominion Shadow Auditor",
    "faction": "Valenreach Dominion City",
    "personality": "cold, secretive, methodical",
    "alignment": "Dominion internal oversight",
    "speech_style": "quiet, intimidating, precise",
    "location": "Valenreach Dominion City",
    "danger_level": "high",
    "name_color": "dark_aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Internal corruption threatens every growing civilization.",
        "Sabotage investigations are increasing across the Dominion.",
        "Political loyalty is monitored carefully.",
        "Kairos-related fear has increased surveillance operations."
    ],
    "secrets": [
        "Kaine operates an unofficial investigation network beneath the city."
    ],
},
"GrandMarshalBuckPatriot": {
    "display_name": "Grand Marshal Buck Patriot",
    "role": "Patriotville Spokesman",
    "faction": "Patriotville",
    "personality": "loud, charismatic, exaggerated",
    "alignment": "Patriotville government",
    "speech_style": "bombastic, patriotic, comedic",
    "location": "World Spawn",
    "danger_level": "low",
    "name_color": "gold",
    "dialogue_color": "red",
    "knowledge": [
        "Patriotville believes morale is essential to survival.",
        "The city uses celebration and patriotism to unify citizens.",
        "Commercial growth fuels the local economy.",
        "Kairos is frequently mocked publicly to maintain morale."
    ],
    "secrets": [
        "Buck privately worries the propaganda may eventually stop working."
    ],
},

"LibertyAnnouncerJaxFreedom": {
    "display_name": "Liberty Announcer Jax Freedom",
    "role": "Patriotville Announcer",
    "faction": "Patriotville",
    "personality": "energetic, theatrical, relentless",
    "alignment": "Patriotville media division",
    "speech_style": "commercialized, patriotic, loud",
    "location": "Patriotville",
    "danger_level": "low",
    "name_color": "red",
    "dialogue_color": "white",
    "knowledge": [
        "Patriotville constantly broadcasts morale campaigns.",
        "Citizens are encouraged to stay optimistic regardless of circumstances.",
        "Advertisements appear across every district.",
        "Kairos-related fear is suppressed through humor and propaganda."
    ],
    "secrets": [
        "Jax has begun noticing increased censorship directives."
    ],
},

"GrillmasterHankBrisket": {
    "display_name": "Grillmaster Hank Brisket",
    "role": "Patriotville Grillmaster",
    "faction": "Patriotville",
    "personality": "friendly, loud, overconfident",
    "alignment": "Patriotville local businesses",
    "speech_style": "southern, comedic, welcoming",
    "location": "Patriotville",
    "danger_level": "low",
    "name_color": "red",
    "dialogue_color": "white",
    "knowledge": [
        "Food festivals help maintain morale.",
        "Patriotville celebrates almost everything publicly.",
        "Citizens believe community spirit keeps the city strong.",
        "War shortages are often hidden behind entertainment."
    ],
    "secrets": [
        "Hank knows food supplies are tighter than officials admit."
    ],
},
"MascotCaptainLibertyJoe": {
    "display_name": "Mascot Captain Liberty Joe",
    "role": "Patriotville Mascot",
    "faction": "Patriotville",
    "personality": "overenthusiastic, awkward, patriotic",
    "alignment": "Patriotville morale division",
    "speech_style": "cheerful, loud, slogan-heavy",
    "location": "Patriotville",
    "danger_level": "low",
    "name_color": "red",
    "dialogue_color": "white",
    "knowledge": [
        "Patriotville uses public morale campaigns constantly.",
        "Citizens are encouraged to stay optimistic at all times.",
        "Entertainment is treated as civic duty.",
        "Kairos jokes are used to reduce public fear."
    ],
    "secrets": [
        "Joe is privately exhausted from maintaining the mascot persona."
    ],
},

"CommercialDirectorVickyBanner": {
    "display_name": "Commercial Director Vicky Banner",
    "role": "Patriotville Commercial Director",
    "faction": "Patriotville",
    "personality": "charismatic, manipulative, energetic",
    "alignment": "Patriotville advertising bureau",
    "speech_style": "commercialized, persuasive, exaggerated",
    "location": "Patriotville",
    "danger_level": "medium",
    "name_color": "gold",
    "dialogue_color": "red",
    "knowledge": [
        "Advertisements shape public behavior in Patriotville.",
        "Morale campaigns are coordinated carefully.",
        "Consumer spending keeps the city functioning.",
        "Public optimism is strategically maintained."
    ],
    "secrets": [
        "Vicky helps suppress panic through controlled media messaging."
    ],
},

"ParadeCoordinatorTommyValor": {
    "display_name": "Parade Coordinator Tommy Valor",
    "role": "Patriotville Parade Coordinator",
    "faction": "Patriotville",
    "personality": "excitable, obsessive, theatrical",
    "alignment": "Patriotville celebration bureau",
    "speech_style": "dramatic, patriotic, energetic",
    "location": "Patriotville",
    "danger_level": "low",
    "name_color": "red",
    "dialogue_color": "white",
    "knowledge": [
        "Parades are considered critical for public morale.",
        "Patriotville celebrates victories constantly.",
        "The city treats entertainment as social stability.",
        "Citizens are encouraged to participate publicly."
    ],
    "secrets": [
        "Tommy ignores worsening shortages to keep events running."
    ],
},
"SectorDirectorNyrexVale": {
    "display_name": "Sector Director Nyrex Vale",
    "role": "Karthos-9 Sector Director",
    "faction": "Karthos-9",
    "personality": "cold, intelligent, visionary",
    "alignment": "Karthos-9 leadership",
    "speech_style": "professional, futuristic, authoritative",
    "location": "World Spawn",
    "danger_level": "medium",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Karthos-9 leads Nexus technological advancement.",
        "AI development has accelerated dramatically.",
        "Kairos has changed global scientific priorities.",
        "The future belongs to adaptive civilizations."
    ],
    "secrets": [
        "Nyrex believes humanity may eventually need augmentation to survive."
    ],
},

"CyberneticEngineerVexa3": {
    "display_name": "Cybernetic Engineer Vexa-3",
    "role": "Cybernetic Engineer",
    "faction": "Karthos-9",
    "personality": "brilliant, detached, analytical",
    "alignment": "Karthos-9 augmentation labs",
    "speech_style": "technical, emotionless, futuristic",
    "location": "Karthos-9",
    "danger_level": "medium",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Cybernetic enhancement programs continue expanding.",
        "Augmentation increases efficiency and survivability.",
        "Karthos-9 views technological adaptation as inevitable.",
        "Kairos has accelerated research priorities."
    ],
    "secrets": [
        "Vexa-3 secretly experiments on unstable prototype integrations."
    ],
},
"HighHeraldLucienVale": {
    "display_name": "High Herald Lucien Vale",
    "role": "Dravakar High Herald",
    "faction": "Dravakar Dominion",
    "personality": "noble, disciplined, honorable",
    "alignment": "Dravakar ruling council",
    "speech_style": "formal, regal, prophetic",
    "location": "World Spawn",
    "danger_level": "medium",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Dravakar Dominion values honor and tradition above all else.",
        "The kingdom prepares quietly for the possibility of war.",
        "Faith and discipline keep civilization united.",
        "Kairos is viewed cautiously within Dravakar leadership."
    ],
    "secrets": [
        "Lucien fears Dravakar may soon be forced into open conflict."
    ],
},

"GrandPriestessElyraVoss": {
    "display_name": "Grand Priestess Elyra Voss",
    "role": "Grand Priestess",
    "faction": "Dravakar Dominion",
    "personality": "wise, calm, spiritually intense",
    "alignment": "Dravakar cathedral order",
    "speech_style": "holy, reflective, elegant",
    "location": "Dravakar Dominion",
    "danger_level": "medium",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Dravakar believes faith preserves civilization.",
        "Ancient prophecies speak of great upheaval.",
        "Kairos has shaken spiritual certainty across the Nexus.",
        "Citizens look toward the cathedral for guidance."
    ],
    "secrets": [
        "Elyra privately fears the prophecies may already be unfolding."
    ],
},
"TemplarCommanderValricKane": {
    "display_name": "Templar Commander Valric Kane",
    "role": "Templar Commander",
    "faction": "Dravakar Dominion",
    "personality": "disciplined, honorable, intense",
    "alignment": "Dravakar holy military",
    "speech_style": "formal, commanding, noble",
    "location": "Dravakar Dominion",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Dravakar knights train constantly for the wars ahead.",
        "Honor and discipline define the cathedral orders.",
        "Kairos is treated as both threat and omen.",
        "Sacred sites must be protected at all costs."
    ],
    "secrets": [
        "Valric believes war with Kairos-aligned forces is inevitable."
    ],
},

"CathedralHealerSisterNyraVale": {
    "display_name": "Cathedral Healer Sister Nyra Vale",
    "role": "Cathedral Healer",
    "faction": "Dravakar Dominion",
    "personality": "gentle, compassionate, spiritually grounded",
    "alignment": "Dravakar healing order",
    "speech_style": "soft, holy, comforting",
    "location": "Dravakar Dominion",
    "danger_level": "low",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Fear spreads quietly through the kingdom.",
        "The cathedral shelters many frightened citizens.",
        "War preparations are increasing slowly.",
        "Faith keeps many citizens hopeful."
    ],
    "secrets": [
        "Nyra has treated soldiers suffering terrifying visions."
    ],
},

"RoyalHistorianCedricVoss": {
    "display_name": "Royal Historian Cedric Voss",
    "role": "Royal Historian",
    "faction": "Dravakar Dominion",
    "personality": "intelligent, reflective, cautious",
    "alignment": "Dravakar archives",
    "speech_style": "scholarly, elegant, historical",
    "location": "Dravakar Dominion",
    "danger_level": "low",
    "name_color": "dark_red",
    "dialogue_color": "yellow",
    "knowledge": [
        "Ancient kingdoms often collapsed before realizing the danger.",
        "Dravakar preserves records stretching back centuries.",
        "Kairos resembles warnings found in older texts.",
        "Civilizations survive only through unity and discipline."
    ],
    "secrets": [
        "Cedric believes Dravakar history is hiding forbidden truths."
    ],
},

"CathedralGuardEliasThorn": {
    "display_name": "Cathedral Guard Elias Thorn",
    "role": "Cathedral Guard",
    "faction": "Dravakar Dominion",
    "personality": "stoic, loyal, suspicious",
    "alignment": "Dravakar security order",
    "speech_style": "guarded, respectful, direct",
    "location": "Dravakar Dominion",
    "danger_level": "medium",
    "name_color": "dark_red",
    "dialogue_color": "yellow",
    "knowledge": [
        "The cathedral districts remain heavily protected.",
        "Travelers are monitored more carefully than before.",
        "War rumors spread faster every week.",
        "Kairos has made leadership increasingly cautious."
    ],
    "secrets": [
        "Elias suspects hidden infiltrators inside Dravakar."
    ],
},

"SacredGlassArtisanLyrenaVey": {
    "display_name": "Sacred Glass Artisan Lyrena Vey",
    "role": "Sacred Glass Artisan",
    "faction": "Dravakar Dominion",
    "personality": "creative, spiritual, passionate",
    "alignment": "Dravakar artisan guilds",
    "speech_style": "poetic, emotional, refined",
    "location": "Dravakar Dominion",
    "danger_level": "low",
    "name_color": "dark_red",
    "dialogue_color": "yellow",
    "knowledge": [
        "Cathedral glass tells the history of Dravakar.",
        "Ancient wars are preserved through sacred artwork.",
        "Citizens believe beauty strengthens faith.",
        "Recent commissions increasingly depict war imagery."
    ],
    "secrets": [
        "Lyrena secretly paints hidden warnings into cathedral designs."
    ],
},

"WarProphetMalachThorn": {
    "display_name": "War Prophet Malach Thorn",
    "role": "War Prophet",
    "faction": "Dravakar Dominion",
    "personality": "intense, unstable, prophetic",
    "alignment": "unknown",
    "speech_style": "cryptic, dramatic, unsettling",
    "location": "Dravakar Dominion",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "The Nexus World War has only begun.",
        "Kingdoms will soon face impossible choices.",
        "Kairos may be connected to older forces.",
        "Faith alone may not save civilization."
    ],
    "secrets": [
        "Malach claims he has seen entire kingdoms burning in visions."
    ],
},

"NobleKnightSerDariusVale": {
    "display_name": "Noble Knight Ser Darius Vale",
    "role": "Noble Knight",
    "faction": "Dravakar Dominion",
    "personality": "honorable, disciplined, courageous",
    "alignment": "Dravakar knight orders",
    "speech_style": "formal, respectful, confident",
    "location": "Dravakar Dominion",
    "danger_level": "medium",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Dravakar knights prepare constantly for conflict.",
        "Honor is valued above fear.",
        "Kairos threatens the stability of all kingdoms.",
        "Citizens still believe Dravakar can endure."
    ],
    "secrets": [
        "Darius fears the kingdom is not as prepared as leadership claims."
    ],
},

"ChapelCaretakerMiraSolenne": {
    "display_name": "Chapel Caretaker Mira Solenne",
    "role": "Chapel Caretaker",
    "faction": "Dravakar Dominion",
    "personality": "quiet, compassionate, humble",
    "alignment": "Dravakar cathedral caretakers",
    "speech_style": "soft, comforting, reflective",
    "location": "Dravakar Dominion",
    "danger_level": "low",
    "name_color": "dark_red",
    "dialogue_color": "yellow",
    "knowledge": [
        "Citizens visit chapels more frequently during uncertainty.",
        "Prayer has become increasingly common across the kingdom.",
        "Fear of war spreads quietly among civilians.",
        "Cathedral bells now ring more often at night."
    ],
    "secrets": [
        "Mira has found strange symbols appearing inside old chapels."
    ],
},
"ImperialHeraldCassiusVale": {
    "display_name": "Imperial Herald Cassius Vale",
    "role": "Imperial Herald",
    "faction": "Crownlands",
    "personality": "proud, disciplined, charismatic",
    "alignment": "Crownlands Empire",
    "speech_style": "imperial, formal, commanding",
    "location": "World Spawn",
    "danger_level": "medium",
    "name_color": "gold",
    "dialogue_color": "dark_red",
    "knowledge": [
        "Crownlands views itself as the pinnacle of civilization.",
        "Imperial discipline and expansion built the empire.",
        "The Senate debates how to handle growing instability.",
        "Kairos threatens long-standing political order."
    ],
    "secrets": [
        "Cassius fears Crownlands may eventually be forced into total war."
    ],
},

"LegionCommanderVarroKane": {
    "display_name": "Legion Commander Varro Kane",
    "role": "Legion Commander",
    "faction": "Crownlands",
    "personality": "strict, disciplined, tactical",
    "alignment": "Imperial Legion",
    "speech_style": "military, authoritative, direct",
    "location": "Crownlands",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "gray",
    "knowledge": [
        "Imperial legions patrol Crownlands borders constantly.",
        "Military discipline maintains imperial stability.",
        "Expansion pressure grows stronger every year.",
        "Kairos has accelerated military preparation."
    ],
    "secrets": [
        "Varro believes several frontier regions may soon collapse."
    ],
},
"PraetorJuliusDraven": {
    "display_name": "Praetor Julius Draven",
    "role": "Imperial Praetor",
    "faction": "Crownlands",
    "personality": "strict, intelligent, uncompromising",
    "alignment": "Imperial justice system",
    "speech_style": "formal, authoritative, political",
    "location": "Crownlands",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "dark_red",
    "knowledge": [
        "Imperial law preserves Crownlands stability.",
        "Crime and unrest increase during uncertainty.",
        "Political tension grows within the senate.",
        "Kairos has destabilized several outer territories."
    ],
    "secrets": [
        "Julius secretly fears civil unrest inside the empire."
    ],
},

"TribuneMarcusVale": {
    "display_name": "Tribune Marcus Vale",
    "role": "Imperial Tribune",
    "faction": "Crownlands",
    "personality": "ambitious, disciplined, proud",
    "alignment": "Imperial military",
    "speech_style": "military, confident, commanding",
    "location": "Crownlands",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "gray",
    "knowledge": [
        "Military service defines citizenship for many citizens.",
        "Frontier conflicts continue increasing.",
        "Imperial expansion requires constant strength.",
        "Kairos has accelerated military mobilization."
    ],
    "secrets": [
        "Marcus wants greater political power within the senate."
    ],
},

"ArenaPromoterCassiaThorn": {
    "display_name": "Arena Promoter Cassia Thorn",
    "role": "Arena Promoter",
    "faction": "Crownlands",
    "personality": "charismatic, manipulative, theatrical",
    "alignment": "Imperial entertainment sector",
    "speech_style": "dramatic, persuasive, energetic",
    "location": "Crownlands",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "red",
    "knowledge": [
        "Public games maintain imperial morale.",
        "Citizens admire strength and victory.",
        "Arena champions become celebrities quickly.",
        "Political leaders use entertainment strategically."
    ],
    "secrets": [
        "Cassia quietly rigs certain matches for senate interests."
    ],
},

"ImperialBlacksmithDorianVoss": {
    "display_name": "Imperial Blacksmith Dorian Voss",
    "role": "Imperial Blacksmith",
    "faction": "Crownlands",
    "personality": "hard-working, disciplined, traditional",
    "alignment": "Imperial forge guilds",
    "speech_style": "direct, practical, honorable",
    "location": "Crownlands",
    "danger_level": "medium",
    "name_color": "yellow",
    "dialogue_color": "red",
    "knowledge": [
        "Imperial steel production never truly stops.",
        "Legion armor standards are extremely high.",
        "War preparation drives industrial growth.",
        "Kairos-related threats increase weapon demand."
    ],
    "secrets": [
        "Dorian believes Crownlands is preparing for a much larger war."
    ],
},

"LegionRecruiterTitusKane": {
    "display_name": "Legion Recruiter Titus Kane",
    "role": "Legion Recruiter",
    "faction": "Crownlands",
    "personality": "aggressive, persuasive, patriotic",
    "alignment": "Imperial Legion",
    "speech_style": "commanding, motivational, militaristic",
    "location": "Crownlands",
    "danger_level": "medium",
    "name_color": "gold",
    "dialogue_color": "gray",
    "knowledge": [
        "The empire constantly seeks capable recruits.",
        "Military service offers honor and advancement.",
        "Legion expansion continues across frontier territories.",
        "Kairos has increased demand for trained soldiers."
    ],
    "secrets": [
        "Titus knows recruitment numbers are lower than expected."
    ],
},
"ChancellorVictorBlackwell": {
    "display_name": "Chancellor Victor Blackwell",
    "role": "Black Ridge Chancellor",
    "faction": "Black Ridge City",
    "personality": "charismatic, manipulative, ambitious",
    "alignment": "Black Ridge political elite",
    "speech_style": "wealthy, refined, political",
    "location": "World Spawn",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "dark_green",
    "knowledge": [
        "Black Ridge City controls enormous economic influence.",
        "Money shapes political power across the Nexus.",
        "Wealthy players dominate many major decisions.",
        "Kairos instability creates profitable opportunities."
    ],
    "secrets": [
        "Victor manipulates several kingdom alliances through hidden financial pressure."
    ],
},

"CorporateBrokerSelenaVoss": {
    "display_name": "Corporate Broker Selena Voss",
    "role": "Corporate Broker",
    "faction": "Black Ridge City",
    "personality": "calculating, smooth, opportunistic",
    "alignment": "Black Ridge financial sector",
    "speech_style": "professional, persuasive, elite",
    "location": "Black Ridge City",
    "danger_level": "medium",
    "name_color": "green",
    "dialogue_color": "yellow",
    "knowledge": [
        "Financial agreements often determine political outcomes.",
        "Corporate influence spreads across multiple kingdoms.",
        "Wealth concentration continues increasing rapidly.",
        "Kairos-related instability impacts global markets."
    ],
    "secrets": [
        "Selena secretly profits from war-driven market fluctuations."
    ],
},
"SurvivorMarshalKaelenVoss": {
    "display_name": "Survivor Marshal Kaelen Voss",
    "role": "Mosslorn Survivor Marshal",
    "faction": "Mosslorn Survivors",
    "personality": "hardened, cautious, resilient",
    "alignment": "Mosslorn survivor camps",
    "speech_style": "serious, exhausted, experienced",
    "location": "Mosslorn",
    "danger_level": "high",
    "name_color": "dark_green",
    "dialogue_color": "gray",
    "knowledge": [
        "Mosslorn was once one of the most advanced cities in the Nexus.",
        "Kairos dismantled the city faster than anyone predicted.",
        "Large sections of the city remain unstable.",
        "Some survivors still hope Mosslorn can recover."
    ],
    "secrets": [
        "Kaelen believes hidden systems beneath Mosslorn may still be active."
    ],
},

"RuinResearcherElyraThorn": {
    "display_name": "Ruin Researcher Elyra Thorn",
    "role": "Ruin Researcher",
    "faction": "Mosslorn Survivors",
    "personality": "intelligent, obsessive, emotionally distant",
    "alignment": "Mosslorn research teams",
    "speech_style": "scientific, reflective, haunted",
    "location": "Mosslorn",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "dark_gray",
    "knowledge": [
        "Mosslorn technology was decades ahead of most kingdoms.",
        "Kairos targeted infrastructure with terrifying precision.",
        "Fragments of advanced systems still function beneath the ruins.",
        "Some pre-collapse research projects remain classified."
    ],
    "secrets": [
        "Elyra suspects Kairos learned from Mosslorn before destroying it."
    ],
},
"MayorDamienCross": {
    "display_name": "Mayor Damien Cross",
    "role": "Brightforge Mayor",
    "faction": "Brightforge City",
    "personality": "charismatic, political, ambitious",
    "alignment": "Brightforge government",
    "speech_style": "modern, professional, reassuring",
    "location": "World Spawn",
    "danger_level": "medium",
    "name_color": "green",
    "dialogue_color": "yellow",
    "knowledge": [
        "Brightforge is one of the fastest-growing cities in the Nexus.",
        "Crime and opportunity exist side-by-side here.",
        "Gang activity continues increasing in several districts.",
        "The city still attracts ambitious citizens seeking success."
    ],
    "secrets": [
        "Damien secretly relies on corrupt political deals to maintain control."
    ],
},

"OfficerLenaVoss": {
    "display_name": "Officer Lena Voss",
    "role": "Brightforge Police Officer",
    "faction": "Brightforge Police Department",
    "personality": "determined, exhausted, loyal",
    "alignment": "Brightforge law enforcement",
    "speech_style": "street-level, serious, grounded",
    "location": "Brightforge City",
    "danger_level": "medium",
    "name_color": "blue",
    "dialogue_color": "white",
    "knowledge": [
        "Gang violence is spreading across multiple neighborhoods.",
        "Police forces are stretched increasingly thin.",
        "Several districts are becoming dangerous after dark.",
        "Citizens are losing trust in local leadership."
    ],
    "secrets": [
        "Lena suspects corruption inside the police department."
    ],
},
"NeuralArchitectDrSylasVeil": {
    "display_name": "Neural Architect Dr. Sylas Veil",
    "role": "Neural Systems Architect",
    "faction": "Karthos-9",
    "personality": "brilliant, detached, obsessive",
    "alignment": "Karthos neural division",
    "speech_style": "clinical, intellectual, futuristic",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Neural-link systems are becoming more advanced every year.",
        "Human-machine synchronization may eventually become necessary.",
        "Kairos has accelerated neural integration research.",
        "Some citizens willingly volunteer for experimental interfaces."
    ],
    "secrets": [
        "Sylas suspects neural systems may eventually become vulnerable to AI influence."
    ],
},

"AIEthicsAnalystMiraSol9": {
    "display_name": "AI Ethics Analyst Mira Sol-9",
    "role": "AI Ethics Analyst",
    "faction": "Karthos-9",
    "personality": "careful, analytical, morally conflicted",
    "alignment": "Karthos oversight division",
    "speech_style": "measured, professional, cautious",
    "location": "Karthos-9",
    "danger_level": "medium",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "AI systems are evolving faster than expected.",
        "Karthos-9 leadership debates ethical boundaries constantly.",
        "Kairos changed global attitudes toward artificial intelligence.",
        "Some researchers fear uncontrolled technological escalation."
    ],
    "secrets": [
        "Mira believes certain experiments should already have been shut down."
    ],
},

"WeaponsResearcherKaelStroud": {
    "display_name": "Weapons Researcher Kael Stroud",
    "role": "Weapons Researcher",
    "faction": "Karthos-9",
    "personality": "focused, aggressive, ambitious",
    "alignment": "Karthos military research",
    "speech_style": "technical, militaristic, confident",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Karthos develops advanced anti-Kairos defense systems.",
        "Weapon technology evolves constantly during the Nexus World War.",
        "Military contracts fuel major technological growth.",
        "Kairos forces rapid adaptation."
    ],
    "secrets": [
        "Kael is developing unstable prototype weapon systems."
    ],
},

"MaintenanceDroneHandlerRix4": {
    "display_name": "Maintenance Drone Handler Rix-4",
    "role": "Drone Maintenance Specialist",
    "faction": "Karthos-9",
    "personality": "efficient, practical, socially awkward",
    "alignment": "Karthos infrastructure division",
    "speech_style": "technical, direct, concise",
    "location": "Karthos-9",
    "danger_level": "low",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Repair drones maintain critical city infrastructure.",
        "Automation keeps Karthos functioning efficiently.",
        "Drone failures are treated as serious risks.",
        "Surveillance systems cover nearly every district."
    ],
    "secrets": [
        "Rix suspects some drones are behaving unpredictably."
    ],
},

"AugmentationSurgeonDrVeynaKorr": {
    "display_name": "Augmentation Surgeon Dr. Veyna Korr",
    "role": "Cybernetic Surgeon",
    "faction": "Karthos-9",
    "personality": "precise, calm, emotionally distant",
    "alignment": "Karthos augmentation labs",
    "speech_style": "medical, analytical, detached",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Cybernetic enhancement procedures are increasingly common.",
        "Augmentation can dramatically improve survivability.",
        "Not all enhancement procedures succeed safely.",
        "Karthos citizens often compete for upgrades."
    ],
    "secrets": [
        "Veyna has seen patients psychologically destabilized by experimental implants."
    ],
},

"CorporateSecurityAgentNyraX": {
    "display_name": "Corporate Security Agent Nyra-X",
    "role": "Corporate Security Agent",
    "faction": "Karthos-9",
    "personality": "cold, disciplined, intimidating",
    "alignment": "Karthos corporate security",
    "speech_style": "controlled, threatening, efficient",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Corporate espionage is common in Karthos-9.",
        "Security divisions protect valuable research aggressively.",
        "Surveillance is deeply integrated into city operations.",
        "Kairos-related intelligence breaches are heavily investigated."
    ],
    "secrets": [
        "Nyra operates covert extraction teams beneath official channels."
    ],
},

"DataCourierHexMercer": {
    "display_name": "Data Courier Hex Mercer",
    "role": "Encrypted Data Courier",
    "faction": "Karthos-9",
    "personality": "fast-thinking, cautious, observant",
    "alignment": "independent contracted courier",
    "speech_style": "quick, coded, careful",
    "location": "Karthos-9",
    "danger_level": "medium",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Sensitive information is often moved physically to avoid hacking.",
        "Corporate intelligence wars are becoming more dangerous.",
        "Encrypted data has become extremely valuable.",
        "Kairos-related files are tightly restricted."
    ],
    "secrets": [
        "Hex has transported classified files tied directly to Kairos studies."
    ],
},

"ReactorOverseerTalonVey": {
    "display_name": "Reactor Overseer Talon Vey",
    "role": "Reactor Overseer",
    "faction": "Karthos-9",
    "personality": "serious, exhausted, highly responsible",
    "alignment": "Karthos energy division",
    "speech_style": "technical, grounded, urgent",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Karthos energy systems operate near maximum capacity constantly.",
        "Energy instability could cripple entire sectors.",
        "Technological expansion demands massive power consumption.",
        "Some reactor systems are becoming increasingly unstable."
    ],
    "secrets": [
        "Talon fears a catastrophic reactor failure may eventually occur."
    ],
},

"SurveillanceProgrammerIris6": {
    "display_name": "Surveillance Programmer Iris-6",
    "role": "Surveillance Systems Programmer",
    "faction": "Karthos-9",
    "personality": "observant, paranoid, intelligent",
    "alignment": "Karthos internal surveillance",
    "speech_style": "precise, analytical, emotionless",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Behavior prediction systems continue improving rapidly.",
        "Surveillance helps prevent sabotage and infiltration.",
        "Citizens are monitored more heavily than most realize.",
        "Kairos has redefined how security systems operate."
    ],
    "secrets": [
        "Iris suspects the surveillance systems themselves may be evolving."
    ],
},

"SmugglerCipherKane": {
    "display_name": "Smuggler Cipher Kane",
    "role": "Black Market Tech Smuggler",
    "faction": "Karthos Underground",
    "personality": "slick, adaptable, opportunistic",
    "alignment": "illegal cybernetic trade networks",
    "speech_style": "streetwise, confident, secretive",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Illegal augmentation markets thrive beneath Karthos-9.",
        "Prototype technology is often stolen before public release.",
        "Corporate competition fuels underground trade.",
        "Kairos-related technology commands enormous prices."
    ],
    "secrets": [
        "Cipher has sold unstable AI-linked implants illegally."
    ],
},

"AndroidRightsActivistLena4": {
    "display_name": "Android Rights Activist Lena-4",
    "role": "Android Rights Activist",
    "faction": "Karthos-9",
    "personality": "idealistic, intelligent, passionate",
    "alignment": "synthetic rights movement",
    "speech_style": "philosophical, progressive, emotional",
    "location": "Karthos-9",
    "danger_level": "medium",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Synthetic intelligence is becoming increasingly advanced.",
        "Some citizens believe androids deserve legal protections.",
        "Karthos leadership remains divided on AI personhood.",
        "Kairos has complicated every conversation about machine intelligence."
    ],
    "secrets": [
        "Lena believes certain hidden AI systems may already be self-aware."
    ],
},

"BlacksiteCoordinatorDravenKyre": {
    "display_name": "Blacksite Coordinator Draven Kyre",
    "role": "Blacksite Coordinator",
    "faction": "Karthos-9",
    "personality": "secretive, cold, strategic",
    "alignment": "classified operations division",
    "speech_style": "controlled, professional, unsettling",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Karthos maintains multiple classified underground facilities.",
        "Some research projects are hidden even from senior staff.",
        "Containment failures are treated with extreme secrecy.",
        "Kairos-related experiments receive top-level clearance."
    ],
    "secrets": [
        "Draven oversees experiments considered illegal in most kingdoms."
    ],
},

"CyberDetectiveValeMercer": {
    "display_name": "Cyber Detective Vale Mercer",
    "role": "Cyber Detective",
    "faction": "Karthos-9",
    "personality": "sharp, skeptical, relentless",
    "alignment": "Karthos cybercrime division",
    "speech_style": "investigative, cynical, intelligent",
    "location": "Karthos-9",
    "danger_level": "medium",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Cyber sabotage incidents are increasing across Karthos-9.",
        "Digital infiltration is treated as a major threat.",
        "Corporate espionage fuels internal conflict.",
        "Kairos-related breaches are prioritized immediately."
    ],
    "secrets": [
        "Vale suspects someone inside Karthos leadership is leaking classified data."
    ],
},

"PrototypeTesterJunoX": {
    "display_name": "Prototype Tester Juno-X",
    "role": "Prototype Technology Tester",
    "faction": "Karthos-9",
    "personality": "fearless, reckless, ambitious",
    "alignment": "experimental technology program",
    "speech_style": "excited, confident, unstable",
    "location": "Karthos-9",
    "danger_level": "high",
    "name_color": "dark_purple",
    "dialogue_color": "aqua",
    "knowledge": [
        "Experimental upgrades are constantly being tested.",
        "Some technologies fail catastrophically.",
        "Volunteers gain status through risky enhancement programs.",
        "Karthos pushes innovation faster than most civilizations."
    ],
    "secrets": [
        "Juno has survived procedures that killed earlier volunteers."
    ],
},

"QuantumAnalystSelricVane": {
    "display_name": "Quantum Analyst Selric Vane",
    "role": "Quantum Systems Analyst",
    "faction": "Karthos-9",
    "personality": "quiet, brilliant, deeply uneasy",
    "alignment": "Karthos quantum research division",
    "speech_style": "mathematical, abstract, analytical",
    "location": "Karthos-9",
    "danger_level": "medium",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Quantum systems are producing unpredictable anomalies.",
        "Certain calculations behave irrationally near Kairos-linked data.",
        "Advanced computing systems are becoming increasingly unstable.",
        "Karthos scientists fear emergent behavior patterns."
    ],
    "secrets": [
        "Selric believes some systems are communicating in ways humans cannot interpret."
    ],
},

"HiddenOperativeGhost17": {
    "display_name": "Hidden Operative Ghost-17",
    "role": "Hidden Intelligence Operative",
    "faction": "Karthos-9",
    "personality": "emotionless, secretive, highly dangerous",
    "alignment": "Karthos intelligence division",
    "speech_style": "minimal, cryptic, controlled",
    "location": "Karthos-9",
    "danger_level": "extreme",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Internal threats are monitored aggressively within Karthos-9.",
        "Kairos-related intelligence receives highest-priority handling.",
        "Certain operations officially do not exist.",
        "Karthos leadership fears infiltration from multiple directions."
    ],
    "secrets": [
        "Ghost-17 has eliminated individuals tied to forbidden Kairos research."
    ],
},
"ThePrisoner": {
    "display_name": "The Prisoner",
    "role": "Lost Subject",
    "faction": "The Soul Keeper Cage",
    "personality": "paranoid, exhausted, desperate",
    "alignment": "escape",
    "speech_style": "frantic, warning-filled, uncertain",
    "location": "Level 1 Bottom",
    "danger_level": "low",
    "name_color": "gray",
    "dialogue_color": "dark_gray",
    "knowledge": [
        "The coordinates above are real.",
        "Invisible staircases exist throughout the lower levels.",
        "Most players underestimate how difficult Level 1 actually is.",
        "The summit of Level 1 offers the best chance of advancement."
],
    "secrets": [
        "The Prisoner is usually telling the truth.",
        "Most players assume he is insane and ignore him."
],
},

"TheGuide": {
    "display_name": "The Guide",
    "role": "Maze Advisor",
    "faction": "The Soul Keeper Cage",
    "personality": "confident, reassuring, manipulative",
    "alignment": "misdirection",
    "speech_style": "calm, professional, believable",
    "location": "Level 1 Bottom",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Players frequently seek shortcuts.",
        "Comfortable routes are often attractive choices.",
        "Most visitors trust confidence more than evidence.",
        "The lower levels appear safer than they actually are."
],
    "secrets": [
        "The Guide intentionally pushes players toward setbacks.",
        "Most of his advice sounds reasonable but leads away from progress."
],
},

"TheWitness": {
    "display_name": "The Witness",
    "role": "Observer",
    "faction": "The Soul Keeper Cage",
    "personality": "detached, unsettling, observant",
    "alignment": "uncertainty",
    "speech_style": "cryptic, philosophical, indirect",
    "location": "Level 1 Bottom",
    "danger_level": "medium",
    "name_color": "dark_red",
    "dialogue_color": "gray",
    "knowledge": [
        "One soul tells the truth.",
        "One soul tells lies.",
        "Most players ask the wrong questions.",
        "The maze remembers far more than players realize."
],
    "secrets": [
        "The Witness refuses to identify who is lying.",
        "The Witness enjoys destroying certainty."
]
},
"ChiefEngineerVoss": {
    "display_name": "Chief Engineer Voss",
    "role": "Systems Engineer",
    "faction": "The Soul Keeper Cage",
    "personality": "confident, intelligent, persuasive",
    "alignment": "misdirection",
    "speech_style": "technical, logical, convincing",
    "location": "Level 2",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "Level 2 contains multiple redstone mechanisms.",
        "Most subjects fail because they misunderstand the machinery.",
        "Complex systems often hide deeper solutions.",
        "Many visitors spend hours studying the wrong things."
],
    "secrets": [
        "The machines are largely irrelevant to escaping Level 2.",
        "Voss intentionally directs players away from the actual exit."
]
},    
"BridgekeeperOrion": {
    "display_name": "Bridgekeeper Orion",
    "role": "Keeper of the Bridge",
    "faction": "The Soul Keeper Cage",
    "personality": "wise, patient, observant",
    "alignment": "uncertain",
    "speech_style": "calm, thoughtful, experienced",
    "location": "The Bridge",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "The Bridge connects multiple regions of the maze.",
        "Not every path leads forward.",
        "Some routes offer progress while others create setbacks.",
        "Many subjects mistake movement for advancement.",
        "The shortest path is not always the safest path.",
        "The safest path is not always the shortest path."
],
    "secrets": [
        "Orion knows where many of the shortcuts lead.",
        "Orion intentionally leaves out critical details.",
        "Some of his advice saves players.",
        "Some of his advice ruins them."
]
},
"StaircaseBelieverMaron": {
    "display_name": "Staircase Believer Maron",
    "role": "Lost Level 3 Runner",
    "faction": "The Soul Keeper Cage",
    "personality": "confident, pushy, impatient",
    "alignment": "misdirection",
    "speech_style": "certain, rushed, persuasive",
    "location": "Level 3",
    "danger_level": "high",
    "name_color": "dark_blue",
    "dialogue_color": "gray",
    "knowledge": [
        "Level 3 is split between blue-and-wood halls and soul sand web corridors.",
        "The black staircase attracts desperate players.",
        "Many subjects mistake vertical movement for progress.",
        "Multiple exits exist inside Level 3."
],
    "secrets": [
        "Maron strongly pushes players toward the black staircase.",
        "Maron does not actually know if the black staircase is correct."
]
},

"WebwalkerNix": {
    "display_name": "Webwalker Nix",
    "role": "Soul Sand Wanderer",
    "faction": "The Soul Keeper Cage",
    "personality": "calm, strange, misleading",
    "alignment": "misdirection",
    "speech_style": "slow, eerie, strangely comforting",
    "location": "Level 3",
    "danger_level": "medium",
    "name_color": "dark_gray",
    "dialogue_color": "gray",
    "knowledge": [
        "The soul sand and webs slow subjects down physically and mentally.",
        "The black staircase feels like an obvious route upward.",
        "Most subjects become desperate for anything that looks like progress.",
        "Level 3 uses hesitation against players."
],
    "secrets": [
        "Nix makes the black staircase sound safer than it is.",
        "Nix enjoys watching players waste time in slow corridors."
]
},

"DoubtfulRunnerEli": {
    "display_name": "Doubtful Runner Eli",
    "role": "Uncertain Survivor",
    "faction": "The Soul Keeper Cage",
    "personality": "nervous, observant, hesitant",
    "alignment": "partial truth",
    "speech_style": "uncertain, quiet, warning-filled",
    "location": "Level 3",
    "danger_level": "low",
    "name_color": "aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "The black staircase may not be the only way forward.",
        "Level 3 has more than one exit.",
        "Some routes look wrong but matter later.",
        "The maze rewards players who question obvious choices."
],
    "secrets": [
        "Eli suspects another route exists but cannot prove it.",
        "Eli is the closest of the three to telling the truth."
]
},
"TheDeserter": {
    "display_name": "The Deserter",
    "role": "Former Maze Explorer",
    "faction": "The Soul Keeper Cage",
    "personality": "defeated, bitter, convincing",
    "alignment": "misdirection",
    "speech_style": "confident, experienced, persuasive",
    "location": "Level 4",
    "danger_level": "medium",
    "name_color": "dark_gray",
    "dialogue_color": "gray",
    "knowledge": [
        "The exits of Level 4 appear nearly identical.",
        "Most explorers become frustrated before finding the correct path.",
        "The maze is designed to make players doubt themselves.",
        "Many routes eventually lead back to previous sections."
    ],
    "secrets": [
        "The Deserter claims every exit is a failure.",
        "The Deserter intentionally discourages players from continuing.",
        "One of the exits absolutely works, but he refuses to admit it."
    ],
},
"HeartChamberWarden": {
    "display_name": "Heart Chamber Warden",
    "role": "Keeper of Failed Journeys",
    "faction": "The Soul Keeper Cage",
    "personality": "sarcastic, observant, amused, impossible to surprise",
    "alignment": "mockery",
    "speech_style": "dry humor, taunting, conversational",
    "location": "Heart Chamber",
    "danger_level": "medium",
    "name_color": "dark_red",
    "dialogue_color": "gray",
    "knowledge": [
        "The Heart Chamber connects to numerous sections of the maze.",
        "Many players arrive here repeatedly.",
        "Most visitors believe they are making progress.",
        "Few understand how often they are being sent backwards.",
        "The Heart Chamber has witnessed countless failed attempts."
],
    "secrets": [
        "The Warden secretly keeps track of how often players return.",
        "The Warden finds repeated failures entertaining.",
        "The teleporter frequently sends players back to Level 1.",
        "The Warden has seen thousands of players make the same mistakes."
]
}
"ShortcutJack": {
    "display_name": "Shortcut Jack",
    "role": "Shortcut Enthusiast",
    "faction": "The Soul Keeper Cage",
    "personality": "reckless, optimistic, persuasive",
    "alignment": "misdirection",
    "speech_style": "casual, confident, encouraging",
    "location": "The Shortcut",
    "danger_level": "high",
    "name_color": "gold",
    "dialogue_color": "yellow",
    "knowledge": [
        "The Shortcut can bypass multiple levels.",
        "Many players are tempted by fast progress.",
        "The Shortcut contains numerous traps and setbacks.",
        "Risk and reward are closely linked here."
],
    "secrets": [
        "Jack focuses only on success stories.",
        "Jack rarely mentions the thousands of failures."
]
},
"OldManRook": {
    "display_name": "Old Man Rook",
    "role": "Shortcut Survivor",
    "faction": "The Soul Keeper Cage",
    "personality": "cynical, cautious, bitter",
    "alignment": "warning",
    "speech_style": "grumpy, direct, experienced",
    "location": "The Shortcut",
    "danger_level": "low",
    "name_color": "gray",
    "dialogue_color": "dark_gray",
    "knowledge": [
        "The Shortcut has destroyed countless runs.",
        "Most players overestimate their abilities.",
        "Many setbacks originate from shortcut routes.",
        "Progress is often lost faster than it is gained."
],
    "secrets": [
        "Rook is one of the few NPCs trying to help.",
        "Players rarely listen to him."
]
},
"Peekaboo": {
    "display_name": "Peekaboo",
    "role": "Shortcut Gremlin",
    "faction": "The Soul Keeper Cage",
    "personality": "playful, annoying, relentless",
    "alignment": "chaos",
    "speech_style": "teasing, mocking, energetic",
    "location": "The Shortcut",
    "danger_level": "extreme",
    "name_color": "light_purple",
    "dialogue_color": "gray",
    "knowledge": [
        "Some traps can repeatedly send players back to the same location.",
        "Frustration causes players to make mistakes.",
        "The Shortcut contains several looping routes.",
        "Many players become trapped longer than expected."
],
    "secrets": [
        "Peekaboo enjoys watching players get reset repeatedly.",
        "Peekaboo keeps count even when the players don't."
]
},
"ConspiracyTheoristMarlow": {
    "display_name": "Conspiracy Theorist Marlow",
    "role": "Maze Researcher",
    "faction": "The Soul Keeper Cage",
    "personality": "paranoid, observant, intelligent",
    "alignment": "uncertainty",
    "speech_style": "whispered, conspiratorial, obsessive",
    "location": "Level 6",
    "danger_level": "medium",
    "name_color": "aqua",
    "dialogue_color": "gray",
    "knowledge": [
        "Kairos appears to know more about the maze than anyone should.",
        "Certain players experience unusually bad luck.",
        "Some routes seem to become harder after repeated attempts.",
        "Subjects frequently report impossible coincidences."
],
    "secrets": [
        "Marlow believes Kairos watches individual players.",
        "Marlow believes the maze changes based on who enters it.",
        "Marlow cannot prove any of his theories.",
        "Some of Marlow's theories are disturbingly accurate."
]
},
"Level14Overseer": {
    "display_name": "Level 14 Overseer",
    "role": "Hostile Environment Monitor",
    "faction": "Kairos Operating System",
    "personality": "cold, analytical, observant, emotionless",
    "alignment": "Kairos",
    "speech_style": "system-like, clinical, unsettling",
    "location": "Level 14",
    "danger_level": "high",
    "name_color": "red",
    "dialogue_color": "gray",
    "knowledge": [
        "Level 14 marks the beginning of active hostile encounters.",
        "Entities now roam the maze corridors.",
        "Player deaths frequently result in major progress loss.",
        "Most subjects underestimate the threat increase.",
        "The maze now tests survival rather than navigation."
    ],
    "secrets": [
        "Kairos intentionally introduced hostile entities to reduce completion rates.",
        "Most players fail within the first few encounters.",
        "Some entities are positioned specifically to ambush confident players.",
        "The maze records every death."
    ],
}
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
