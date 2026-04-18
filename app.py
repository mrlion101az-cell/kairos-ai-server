import os
import json
import re
import time
import uuid
import math
import copy
import queue
import random
import hashlib
import secrets
import threading
import traceback
from enum import Enum
from copy import deepcopy
from pathlib import Path
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# ------------------------------------------------------------
# Environment / Config (Kairos Command Core - Expanded)
# ------------------------------------------------------------

# -----------------------------
# Core API / Services
# -----------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

MC_HTTP_URL = os.getenv("MC_HTTP_URL")
MC_HTTP_TOKEN = os.getenv("MC_HTTP_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# -----------------------------
# Execution / Safety
# -----------------------------
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "25"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))

# Action queue protection (prevents backlog lag spikes)
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "200"))
ACTION_LOOP_DELAY = float(os.getenv("ACTION_LOOP_DELAY", "0.2"))

# -----------------------------
# Threat System
# -----------------------------
THREAT_THRESHOLD_TARGET = int(os.getenv("THREAT_THRESHOLD_TARGET", "30"))
THREAT_THRESHOLD_HUNT = int(os.getenv("THREAT_THRESHOLD_HUNT", "60"))
THREAT_THRESHOLD_MAXIMUM = int(os.getenv("THREAT_THRESHOLD_MAXIMUM", "90"))

THREAT_KILL_NPC = int(os.getenv("THREAT_KILL_NPC", "5"))
THREAT_KILL_PLAYER = int(os.getenv("THREAT_KILL_PLAYER", "10"))
THREAT_SURVIVE_WAVE = int(os.getenv("THREAT_SURVIVE_WAVE", "6"))
THREAT_TOXIC_CHAT = int(os.getenv("THREAT_TOXIC_CHAT", "20"))

# -----------------------------
# Spawn / Positioning
# -----------------------------
SPAWN_RADIUS_MIN = int(os.getenv("SPAWN_RADIUS_MIN", "6"))
SPAWN_RADIUS_MAX = int(os.getenv("SPAWN_RADIUS_MAX", "18"))
SPAWN_HEIGHT_OFFSET = int(os.getenv("SPAWN_HEIGHT_OFFSET", "1"))

# -----------------------------
# Region / Density Scaling
# -----------------------------
# These values represent "how developed" an area feels
DENSITY_LOW = int(os.getenv("DENSITY_LOW", "10"))
DENSITY_MEDIUM = int(os.getenv("DENSITY_MEDIUM", "40"))
DENSITY_HIGH = int(os.getenv("DENSITY_HIGH", "80"))
DENSITY_EXTREME = int(os.getenv("DENSITY_EXTREME", "140"))

# Multiplier applied to unit strength based on density
DENSITY_MULTIPLIERS = {
    "frontier": 0.7,
    "settled": 1.0,
    "urban": 1.3,
    "fortified": 1.7,
    "stronghold": 2.2
}

# -----------------------------
# Wave Composition
# -----------------------------
BASE_WAVE_SIZE = int(os.getenv("BASE_WAVE_SIZE", "3"))
MAX_WAVE_SIZE = int(os.getenv("MAX_WAVE_SIZE", "10"))

HEAVY_OVERRIDE_CHANCE = float(os.getenv("HEAVY_OVERRIDE_CHANCE", "0.25"))
ELITE_SPAWN_CHANCE = float(os.getenv("ELITE_SPAWN_CHANCE", "0.18"))

# -----------------------------
# Citizens / Sentinel Integration
# -----------------------------
# Default NPC settings (can be overridden per class)
NPC_DEFAULT_HEALTH = int(os.getenv("NPC_DEFAULT_HEALTH", "40"))
NPC_DEFAULT_DAMAGE = int(os.getenv("NPC_DEFAULT_DAMAGE", "6"))
NPC_DEFAULT_SPEED = float(os.getenv("NPC_DEFAULT_SPEED", "1.0"))

# Sentinel tuning
SENTINEL_RANGE = int(os.getenv("SENTINEL_RANGE", "25"))
SENTINEL_ATTACK_RATE = int(os.getenv("SENTINEL_ATTACK_RATE", "20"))  # ticks
SENTINEL_CHASE_RANGE = int(os.getenv("SENTINEL_CHASE_RANGE", "40"))

# Cleanup / persistence
MAX_ACTIVE_UNITS_PER_PLAYER = int(os.getenv("MAX_ACTIVE_UNITS_PER_PLAYER", "25"))
UNIT_DESPAWN_SECONDS = int(os.getenv("UNIT_DESPAWN_SECONDS", "180"))

# -----------------------------
# Naming / Identity
# -----------------------------
UNIT_NAME_PREFIXES = [
    "Purity", "Ash", "Blackline", "Red Thread", "Obsidian", "Null", "Severance"
]

UNIT_TYPES = [
    "Scout", "Raider", "Hunter", "Enforcer", "Juggernaut", "Sentinel", "Assassin", "Commander"
]

# -----------------------------
# Idle / Presence
# -----------------------------
IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "300"))
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "10"))

# -----------------------------
# Memory Limits
# -----------------------------
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "16"))
MAX_PLAYER_MEMORIES = int(os.getenv("MAX_PLAYER_MEMORIES", "40"))
MAX_WORLD_MEMORIES = int(os.getenv("MAX_WORLD_MEMORIES", "100"))
MAX_WORLD_EVENTS = int(os.getenv("MAX_WORLD_EVENTS", "250"))

# -----------------------------
# Feature Flags (future-proofing)
# -----------------------------
ENABLE_REGION_SCALING = os.getenv("ENABLE_REGION_SCALING", "true").lower() == "true"
ENABLE_ELITE_UNITS = os.getenv("ENABLE_ELITE_UNITS", "true").lower() == "true"
ENABLE_AUTO_CLEANUP = os.getenv("ENABLE_AUTO_CLEANUP", "true").lower() == "true"

# -----------------------------
# OpenAI Client
# -----------------------------
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ------------------------------------------------------------
# Storage / Memory (Kairos Persistent Systems)
# ------------------------------------------------------------

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# -----------------------------
# Core Memory
# -----------------------------
MEMORY_FILE = DATA_DIR / "kairos_memory.json"
MEMORY_TMP_FILE = DATA_DIR / "kairos_memory.tmp.json"

# -----------------------------
# Army State (CRITICAL)
# -----------------------------
# Tracks all active NPC units, squads, and assignments
ARMY_STATE_FILE = DATA_DIR / "kairos_army.json"
ARMY_TMP_FILE = DATA_DIR / "kairos_army.tmp.json"

# -----------------------------
# Telemetry / World Tracking
# -----------------------------
# Stores player positions, density signals, region estimates
TELEMETRY_LOG_FILE = DATA_DIR / "kairos_telemetry.json"
TELEMETRY_TMP_FILE = DATA_DIR / "kairos_telemetry.tmp.json"

# -----------------------------
# Region / Density Cache
# -----------------------------
# Precomputed region strength so Kairos doesn't recalc constantly
REGION_CACHE_FILE = DATA_DIR / "kairos_regions.json"

# -----------------------------
# Active Engagement Tracking
# -----------------------------
# Tracks who is currently being hunted / pressured
ENGAGEMENT_STATE_FILE = DATA_DIR / "kairos_engagements.json"

# -----------------------------
# Safety / Backups
# -----------------------------
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

# -----------------------------
# In-Memory Runtime Structures
# -----------------------------

# Active army units in memory (fast access)
active_units: Dict[str, Dict[str, Any]] = {}

# Squad tracking (grouped NPCs)
active_squads: Dict[str, Dict[str, Any]] = {}

# Player engagement tracking
active_engagements: Dict[str, Dict[str, Any]] = {}

# Region density cache (runtime)
region_cache: Dict[str, Dict[str, Any]] = {}

# Action queue (core execution pipeline)
command_queue: deque = deque()

# Locking (thread safety)
memory_lock = threading.RLock()
army_lock = threading.RLock()
telemetry_lock = threading.RLock()
queue_lock = threading.Lock()

# ------------------------------------------------------------
# Memory Limits (Centralized + Scalable)
# ------------------------------------------------------------

# Core conversation memory
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
MAX_CHANNEL_CONTEXT = int(os.getenv("MAX_CHANNEL_CONTEXT", "30"))

# Player-specific memory
MAX_PLAYER_MEMORIES = int(os.getenv("MAX_PLAYER_MEMORIES", "60"))
MAX_PRIVATE_NOTES = int(os.getenv("MAX_PRIVATE_NOTES", "16"))
MAX_SUMMARIES = int(os.getenv("MAX_SUMMARIES", "10"))

# World-level memory
MAX_WORLD_MEMORIES = int(os.getenv("MAX_WORLD_MEMORIES", "150"))
MAX_WORLD_EVENTS = int(os.getenv("MAX_WORLD_EVENTS", "400"))

# Mission tracking
MAX_MISSION_PROGRESS = int(os.getenv("MAX_MISSION_PROGRESS", "50"))

# Army / Combat tracking (NEW – important for your system)
MAX_ACTIVE_UNITS_TRACKED = int(os.getenv("MAX_ACTIVE_UNITS_TRACKED", "500"))
MAX_ACTIVE_SQUADS = int(os.getenv("MAX_ACTIVE_SQUADS", "100"))
MAX_ENGAGEMENT_HISTORY = int(os.getenv("MAX_ENGAGEMENT_HISTORY", "200"))

# Telemetry / region tracking (supports density logic)
MAX_TELEMETRY_ENTRIES = int(os.getenv("MAX_TELEMETRY_ENTRIES", "500"))
MAX_REGION_CACHE = int(os.getenv("MAX_REGION_CACHE", "200"))

# ------------------------------------------------------------
# Timing / Rate Control (Kairos Runtime Orchestration)
# ------------------------------------------------------------

# -----------------------------
# Presence / Idle Behavior
# -----------------------------
IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "240"))
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "5"))

# -----------------------------
# Core System Loops
# -----------------------------
# Action loop (executes queued actions)
ACTION_LOOP_INTERVAL = float(os.getenv("ACTION_LOOP_INTERVAL", "0.2"))

# Commander loop (future AI autonomous decisions)
COMMANDER_LOOP_INTERVAL = float(os.getenv("COMMANDER_LOOP_INTERVAL", "4.0"))

# Telemetry freshness (player movement / density)
TELEMETRY_STALE_SECONDS = int(os.getenv("TELEMETRY_STALE_SECONDS", "25"))

# -----------------------------
# Player Interaction Control
# -----------------------------
PLAYER_COOLDOWN_SECONDS = float(os.getenv("PLAYER_COOLDOWN_SECONDS", "1.5"))
DUPLICATE_MESSAGE_WINDOW_SECONDS = int(os.getenv("DUPLICATE_MESSAGE_WINDOW_SECONDS", "15"))

# -----------------------------
# Combat / Army Timing (CRITICAL)
# -----------------------------
# Minimum delay between waves per player
WAVE_COOLDOWN_SECONDS = float(os.getenv("WAVE_COOLDOWN_SECONDS", "6.0"))

# Delay between individual unit spawns inside a wave
UNIT_SPAWN_DELAY = float(os.getenv("UNIT_SPAWN_DELAY", "0.25"))

# Time before units are considered stale / cleanup eligible
UNIT_LIFETIME_SECONDS = int(os.getenv("UNIT_LIFETIME_SECONDS", "180"))

# Delay before elite reinforcements can trigger again
ELITE_COOLDOWN_SECONDS = float(os.getenv("ELITE_COOLDOWN_SECONDS", "20.0"))

# Maximum concurrent waves per player
MAX_ACTIVE_WAVES_PER_PLAYER = int(os.getenv("MAX_ACTIVE_WAVES_PER_PLAYER", "3"))

# -----------------------------
# OpenAI / Network Timing
# -----------------------------
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "5"))
HTTP_RETRY_DELAY = float(os.getenv("HTTP_RETRY_DELAY", "0.5"))

# -----------------------------
# Safety / Backpressure
# -----------------------------
# Prevents action spam if system overloads
GLOBAL_ACTION_COOLDOWN = float(os.getenv("GLOBAL_ACTION_COOLDOWN", "0.05"))

# Prevents same target from being spammed instantly
TARGET_ACTION_COOLDOWN = float(os.getenv("TARGET_ACTION_COOLDOWN", "2.0"))
# ------------------------------------------------------------
# Feature Flags (Kairos System Control Panel)
# ------------------------------------------------------------

# -----------------------------
# AI / Memory Behavior
# -----------------------------
ENABLE_MODEL_SUMMARIES = os.getenv("ENABLE_MODEL_SUMMARIES", "false").lower() == "true"
ENABLE_MODEL_PRIVATE_NOTES = os.getenv("ENABLE_MODEL_PRIVATE_NOTES", "false").lower() == "true"

# -----------------------------
# Core AI Systems
# -----------------------------
ENABLE_COMMANDER_MODE = os.getenv("ENABLE_COMMANDER_MODE", "true").lower() == "true"
ENABLE_THREAT_SYSTEM = os.getenv("ENABLE_THREAT_SYSTEM", "true").lower() == "true"
ENABLE_AUTONOMOUS_ACTIONS = os.getenv("ENABLE_AUTONOMOUS_ACTIONS", "true").lower() == "true"

# -----------------------------
# Army / Combat Systems (NEW - CRITICAL)
# -----------------------------
ENABLE_ARMY_SYSTEM = os.getenv("ENABLE_ARMY_SYSTEM", "true").lower() == "true"
ENABLE_CITIZENS_NPCS = os.getenv("ENABLE_CITIZENS_NPCS", "true").lower() == "true"
ENABLE_SENTINEL_COMBAT = os.getenv("ENABLE_SENTINEL_COMBAT", "true").lower() == "true"

# Allow Kairos to override region difficulty with heavy units
ENABLE_HEAVY_OVERRIDE = os.getenv("ENABLE_HEAVY_OVERRIDE", "true").lower() == "true"

# Elite units (boss-level threats)
ENABLE_ELITE_UNITS = os.getenv("ENABLE_ELITE_UNITS", "true").lower() == "true"

# -----------------------------
# Region / Density Logic
# -----------------------------
ENABLE_REGION_SCALING = os.getenv("ENABLE_REGION_SCALING", "true").lower() == "true"
ENABLE_DENSITY_TRACKING = os.getenv("ENABLE_DENSITY_TRACKING", "true").lower() == "true"
ENABLE_BASE_TRACKING = os.getenv("ENABLE_BASE_TRACKING", "true").lower() == "true"

# -----------------------------
# Engagement / War Behavior
# -----------------------------
ENABLE_MULTI_WAVE_ATTACKS = os.getenv("ENABLE_MULTI_WAVE_ATTACKS", "true").lower() == "true"
ENABLE_PERSISTENT_HUNTS = os.getenv("ENABLE_PERSISTENT_HUNTS", "true").lower() == "true"

# Allows Kairos to escalate without player input
ENABLE_PASSIVE_ESCALATION = os.getenv("ENABLE_PASSIVE_ESCALATION", "true").lower() == "true"

# -----------------------------
# Cleanup / Performance
# -----------------------------
ENABLE_AUTO_CLEANUP = os.getenv("ENABLE_AUTO_CLEANUP", "true").lower() == "true"
ENABLE_UNIT_DESPAWN = os.getenv("ENABLE_UNIT_DESPAWN", "true").lower() == "true"

# Prevents runaway entity spam
ENABLE_SPAWN_LIMITS = os.getenv("ENABLE_SPAWN_LIMITS", "true").lower() == "true"

# -----------------------------
# Debug / Testing
# -----------------------------
ENABLE_DEBUG_LOGGING = os.getenv("ENABLE_DEBUG_LOGGING", "false").lower() == "true"
ENABLE_FORCE_ACTIONS = os.getenv("ENABLE_FORCE_ACTIONS", "false").lower() == "true"
# ------------------------------------------------------------
# Threat System Tuning (Kairos Escalation Engine)
# ------------------------------------------------------------

# -----------------------------
# Passive Behavior
# -----------------------------
# Threat slowly decays over time (prevents permanent escalation lock unless intended)
THREAT_DECAY_PER_TICK = float(os.getenv("THREAT_DECAY_PER_TICK", "1.2"))

# Idle players slowly gain attention
THREAT_IDLE_GAIN = float(os.getenv("THREAT_IDLE_GAIN", "0.8"))

# -----------------------------
# Combat / Behavior Triggers
# -----------------------------
THREAT_KILL_PLAYER = float(os.getenv("THREAT_KILL_PLAYER", "12.0"))
THREAT_KILL_NPC = float(os.getenv("THREAT_KILL_NPC", "18.0"))
THREAT_SURVIVE_WAVE = float(os.getenv("THREAT_SURVIVE_WAVE", "10.0"))

# Toxic / defiant chat
THREAT_TOXIC_CHAT = float(os.getenv("THREAT_TOXIC_CHAT", "22.0"))

# Optional: direct defiance phrases boost (future use)
THREAT_DEFIANCE_SPIKE = float(os.getenv("THREAT_DEFIANCE_SPIKE", "30.0"))

# -----------------------------
# Escalation Thresholds
# -----------------------------
# These now directly map to behavior tiers

THREAT_THRESHOLD_WATCH = int(os.getenv("THREAT_THRESHOLD_WATCH", "40"))
# Kairos observes, minor presence, no real pressure

THREAT_THRESHOLD_TARGET = int(os.getenv("THREAT_THRESHOLD_TARGET", "90"))
# Light waves begin, scouting units

THREAT_THRESHOLD_HUNT = int(os.getenv("THREAT_THRESHOLD_HUNT", "160"))
# Aggressive waves, mixed unit classes

THREAT_THRESHOLD_MAXIMUM = int(os.getenv("THREAT_THRESHOLD_MAXIMUM", "280"))
# Full suppression, elite units, repeated waves

# -----------------------------
# Scaling Multipliers
# -----------------------------
# Applies to wave strength based on threat level

THREAT_WAVE_MULTIPLIER = float(os.getenv("THREAT_WAVE_MULTIPLIER", "1.0"))
THREAT_ELITE_MULTIPLIER = float(os.getenv("THREAT_ELITE_MULTIPLIER", "1.4"))

# -----------------------------
# Anti-Spike Protection
# -----------------------------
# Prevents instant jump from 0 → maximum chaos

MAX_THREAT_GAIN_PER_EVENT = float(os.getenv("MAX_THREAT_GAIN_PER_EVENT", "35.0"))

# -----------------------------
# Persistence Behavior
# -----------------------------
# Controls how "relentless" Kairos feels

ENABLE_THREAT_DECAY = os.getenv("ENABLE_THREAT_DECAY", "true").lower() == "true"

# If true, high-threat players stay hunted even if they go quiet
ENABLE_THREAT_LOCK_AT_MAX = os.getenv("ENABLE_THREAT_LOCK_AT_MAX", "true").lower() == "true"
# ------------------------------------------------------------
# Army / Wave System (Kairos Army Command Core)
# ------------------------------------------------------------

# -----------------------------
# Global Limits (Performance Safety)
# -----------------------------
MAX_ACTIVE_UNITS = int(os.getenv("MAX_ACTIVE_UNITS", "60"))
MAX_ACTIVE_SQUADS = int(os.getenv("MAX_ACTIVE_SQUADS", "12"))
MAX_UNITS_PER_PLAYER = int(os.getenv("MAX_UNITS_PER_PLAYER", "18"))

# Prevents infinite reinforcement stacking
MAX_ACTIVE_WAVES_PER_PLAYER = int(os.getenv("MAX_ACTIVE_WAVES_PER_PLAYER", "3"))

# -----------------------------
# Wave Timing
# -----------------------------
WAVE_COOLDOWN_SECONDS = float(os.getenv("WAVE_COOLDOWN_SECONDS", "8.0"))
MAX_WAVE_DURATION = int(os.getenv("MAX_WAVE_DURATION", "120"))

# Maximum response (full suppression mode)
MAXIMUM_RESPONSE_DURATION = int(os.getenv("MAXIMUM_RESPONSE_DURATION", "300"))

# Delay between units inside a wave (prevents instant lag spikes)
UNIT_SPAWN_DELAY = float(os.getenv("UNIT_SPAWN_DELAY", "0.25"))

# -----------------------------
# Spawn Positioning
# -----------------------------
SPAWN_RADIUS_MIN = int(os.getenv("SPAWN_RADIUS_MIN", "6"))
SPAWN_RADIUS_MAX = int(os.getenv("SPAWN_RADIUS_MAX", "18"))
SPAWN_HEIGHT_OFFSET = int(os.getenv("SPAWN_HEIGHT_OFFSET", "1"))

# -----------------------------
# Wave Composition Scaling
# -----------------------------
BASE_WAVE_SIZE = int(os.getenv("BASE_WAVE_SIZE", "3"))
MAX_WAVE_SIZE = int(os.getenv("MAX_WAVE_SIZE", "10"))

# Scaling based on threat
THREAT_TO_UNIT_SCALE = float(os.getenv("THREAT_TO_UNIT_SCALE", "0.04"))

# Scaling based on region density
DENSITY_TO_UNIT_SCALE = float(os.getenv("DENSITY_TO_UNIT_SCALE", "0.03"))

# -----------------------------
# Class Distribution
# -----------------------------
# Base probabilities for unit types (modified by threat + density)
CLASS_DISTRIBUTION = {
    "scout": 0.30,
    "raider": 0.22,
    "hunter": 0.18,
    "enforcer": 0.12,
    "assassin": 0.08,
    "sentinel": 0.05,
    "juggernaut": 0.03,
    "commander": 0.02
}

# -----------------------------
# Elite / Heavy Logic
# -----------------------------
ENABLE_HEAVY_OVERRIDE = os.getenv("ENABLE_HEAVY_OVERRIDE", "true").lower() == "true"
ENABLE_ELITE_UNITS = os.getenv("ENABLE_ELITE_UNITS", "true").lower() == "true"

HEAVY_OVERRIDE_CHANCE = float(os.getenv("HEAVY_OVERRIDE_CHANCE", "0.25"))
ELITE_SPAWN_CHANCE = float(os.getenv("ELITE_SPAWN_CHANCE", "0.18"))

# Forces stronger units regardless of region
MAX_THREAT_FORCE_HEAVY = int(os.getenv("MAX_THREAT_FORCE_HEAVY", "260"))

# -----------------------------
# Unit Lifetime / Cleanup
# -----------------------------
UNIT_LIFETIME_SECONDS = int(os.getenv("UNIT_LIFETIME_SECONDS", "180"))

# Hard cap for NPC existence
MAX_GLOBAL_NPCS = int(os.getenv("MAX_GLOBAL_NPCS", "120"))

# Cleanup behavior
ENABLE_AUTO_CLEANUP = os.getenv("ENABLE_AUTO_CLEANUP", "true").lower() == "true"
CLEANUP_CHECK_INTERVAL = float(os.getenv("CLEANUP_CHECK_INTERVAL", "10.0"))

# -----------------------------
# Squad Behavior
# -----------------------------
# Groups units together logically
ENABLE_SQUAD_GROUPING = os.getenv("ENABLE_SQUAD_GROUPING", "true").lower() == "true"

# Max units per squad
MAX_UNITS_PER_SQUAD = int(os.getenv("MAX_UNITS_PER_SQUAD", "6"))

# Squad reinforcement delay
SQUAD_REINFORCE_DELAY = float(os.getenv("SQUAD_REINFORCE_DELAY", "6.0"))

# -----------------------------
# Engagement Persistence
# -----------------------------
# How long Kairos keeps attacking a player
ENGAGEMENT_DURATION_SECONDS = int(os.getenv("ENGAGEMENT_DURATION_SECONDS", "180"))

# If true, Kairos will keep re-attacking after a wave ends
ENABLE_PERSISTENT_ENGAGEMENT = os.getenv("ENABLE_PERSISTENT_ENGAGEMENT", "true").lower() == "true"

# -----------------------------
# Naming / Identity
# -----------------------------
UNIT_NAME_PREFIXES = [
    "Purity", "Ash", "Blackline", "Red Thread", "Obsidian", "Null", "Severance"
]

UNIT_TYPES = [
    "Scout", "Raider", "Hunter", "Enforcer", "Assassin", "Sentinel", "Juggernaut", "Commander"
]
# ------------------------------------------------------------
# Base / Territory System (Kairos Territorial Intelligence)
# ------------------------------------------------------------

# -----------------------------
# Base Detection
# -----------------------------
BASE_DETECTION_RADIUS = int(os.getenv("BASE_DETECTION_RADIUS", "20"))

# Time player must remain in one area before considered "anchored"
BASE_MIN_STATIONARY_SECONDS = int(os.getenv("BASE_MIN_STATIONARY_SECONDS", "60"))

# Confidence required before Kairos treats it as a real base
BASE_CONFIDENCE_THRESHOLD = float(os.getenv("BASE_CONFIDENCE_THRESHOLD", "0.80"))

# Maximum bases tracked globally
MAX_TRACKED_BASES = int(os.getenv("MAX_TRACKED_BASES", "200"))

# -----------------------------
# Base Confidence Growth
# -----------------------------
# How fast confidence increases when player stays in area
BASE_CONFIDENCE_GAIN_RATE = float(os.getenv("BASE_CONFIDENCE_GAIN_RATE", "0.05"))

# Decay when player leaves
BASE_CONFIDENCE_DECAY_RATE = float(os.getenv("BASE_CONFIDENCE_DECAY_RATE", "0.02"))

# -----------------------------
# Territory Strength
# -----------------------------
# Derived from density, activity, and time
BASE_STRENGTH_MULTIPLIER = float(os.getenv("BASE_STRENGTH_MULTIPLIER", "1.5"))

# Minimum strength floor
BASE_MIN_STRENGTH = float(os.getenv("BASE_MIN_STRENGTH", "0.5"))

# Maximum strength cap
BASE_MAX_STRENGTH = float(os.getenv("BASE_MAX_STRENGTH", "3.0"))

# -----------------------------
# Invasion Logic
# -----------------------------
# Threat required before Kairos starts attacking bases directly
BASE_INVASION_THREAT_THRESHOLD = int(os.getenv("BASE_INVASION_THREAT_THRESHOLD", "140"))

# Chance to trigger invasion once threshold met
BASE_INVASION_CHANCE = float(os.getenv("BASE_INVASION_CHANCE", "0.35"))

# Delay between invasion attempts
BASE_INVASION_COOLDOWN = float(os.getenv("BASE_INVASION_COOLDOWN", "30.0"))

# -----------------------------
# Reinforcement Scaling
# -----------------------------
# Additional units spawned when defending a base
BASE_REINFORCEMENT_MULTIPLIER = float(os.getenv("BASE_REINFORCEMENT_MULTIPLIER", "1.6"))

# Additional elite chance inside strong bases
BASE_ELITE_BOOST = float(os.getenv("BASE_ELITE_BOOST", "0.15"))

# -----------------------------
# Occupation System
# -----------------------------
# If enabled, Kairos can "take over" a base area
ENABLE_BASE_OCCUPATION = os.getenv("ENABLE_BASE_OCCUPATION", "true").lower() == "true"

# Time Kairos keeps a base under control
BASE_OCCUPATION_DURATION = int(os.getenv("BASE_OCCUPATION_DURATION", "240"))

# Number of units maintained during occupation
BASE_OCCUPATION_UNIT_COUNT = int(os.getenv("BASE_OCCUPATION_UNIT_COUNT", "6"))

# -----------------------------
# Territory Memory
# -----------------------------
# Keeps track of previously discovered bases
ENABLE_BASE_MEMORY = os.getenv("ENABLE_BASE_MEMORY", "true").lower() == "true"

# Max stored base history per player
MAX_BASE_HISTORY = int(os.getenv("MAX_BASE_HISTORY", "10"))
# ------------------------------------------------------------
# Messaging Style (Kairos Behavioral Voice System)
# ------------------------------------------------------------

# -----------------------------
# Global Mode Override
# -----------------------------
# Options: passive, watchful, hostile, execution, adaptive
KAIROS_MESSAGE_MODE = os.getenv("KAIROS_MESSAGE_MODE", "adaptive").lower()

# -----------------------------
# Tone Intensities
# -----------------------------
# Controls how aggressive Kairos sounds at each threat level

TONE_MAP = {
    "watch": {
        "prefix": "",
        "style": "observational",
        "intensity": 0.3
    },
    "target": {
        "prefix": "",
        "style": "probing",
        "intensity": 0.5
    },
    "hunt": {
        "prefix": "",
        "style": "threatening",
        "intensity": 0.75
    },
    "maximum": {
        "prefix": "",
        "style": "execution",
        "intensity": 1.0
    }
}

# -----------------------------
# Delivery Channels
# -----------------------------
ENABLE_ACTIONBAR_MESSAGES = os.getenv("ENABLE_ACTIONBAR_MESSAGES", "true").lower() == "true"
ENABLE_TITLE_MESSAGES = os.getenv("ENABLE_TITLE_MESSAGES", "true").lower() == "true"
ENABLE_SOUND_ALERTS = os.getenv("ENABLE_SOUND_ALERTS", "true").lower() == "true"

# -----------------------------
# Event Messaging Toggles
# -----------------------------
ENABLE_WAVE_ANNOUNCEMENTS = os.getenv("ENABLE_WAVE_ANNOUNCEMENTS", "true").lower() == "true"
ENABLE_HUNT_WARNINGS = os.getenv("ENABLE_HUNT_WARNINGS", "true").lower() == "true"
ENABLE_MAXIMUM_WARNINGS = os.getenv("ENABLE_MAXIMUM_WARNINGS", "true").lower() == "true"
ENABLE_BASE_INVASION_ALERTS = os.getenv("ENABLE_BASE_INVASION_ALERTS", "true").lower() == "true"

# -----------------------------
# Message Templates (Core Feel)
# -----------------------------
MESSAGE_TEMPLATES = {
    "wave_start": [
        "Wave {wave} entering your position.",
        "You are being tested.",
        "Containment pressure increasing."
    ],
    "hunt_start": [
        "You have been marked.",
        "Tracking initialized.",
        "You are not leaving this area."
    ],
    "maximum": [
        "RUN.",
        "Final containment protocol active.",
        "This ends now."
    ],
    "base_detected": [
        "You stayed too long.",
        "Location recorded.",
        "This structure will be corrected."
    ],
    "base_invasion": [
        "Your territory is no longer yours.",
        "Reclaiming this area.",
        "Occupation has begun."
    ]
}

# -----------------------------
# Formatting Rules
# -----------------------------
MAX_CHAT_LENGTH = int(os.getenv("MAX_CHAT_LENGTH", "280"))
MAX_ACTIONBAR_LENGTH = int(os.getenv("MAX_ACTIONBAR_LENGTH", "120"))

# -----------------------------
# Personality Modifiers
# -----------------------------
ENABLE_GLITCH_EFFECTS = os.getenv("ENABLE_GLITCH_EFFECTS", "false").lower() == "true"
ENABLE_MINIMAL_RESPONSES = os.getenv("ENABLE_MINIMAL_RESPONSES", "true").lower() == "true"

# If true, Kairos uses shorter, sharper lines under high threat
HIGH_THREAT_MINIMAL_MODE = os.getenv("HIGH_THREAT_MINIMAL_MODE", "true").lower() == "true"
# ------------------------------------------------------------
# OpenAI Client
# ------------------------------------------------------------

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
# ------------------------------------------------------------
# Globals (Kairos Runtime + War Engine Core)
# ------------------------------------------------------------

# -----------------------------
# Thread Safety
# -----------------------------
memory_lock = threading.RLock()
activity_lock = threading.Lock()
rate_limit_lock = threading.Lock()
army_lock = threading.RLock()
telemetry_lock = threading.RLock()
queue_lock = threading.Lock()

# -----------------------------
# Activity Tracking
# -----------------------------
last_activity_time = time.time()
last_idle_message_time = 0
last_commander_tick = 0
last_action_tick = 0

# -----------------------------
# Rate Limiting / Anti-Spam
# -----------------------------
rate_limit_cache: Dict[str, float] = {}
recent_message_cache: Dict[str, Tuple[str, float]] = {}

# -----------------------------
# Action Queue (Core Execution Pipeline)
# -----------------------------
command_queue: deque = deque()

# Prevents global action spam
last_global_action_time = 0.0

# Prevents same target being spammed repeatedly
last_target_action_time: Dict[str, float] = {}

# -----------------------------
# Army State (Runtime)
# -----------------------------
# Active NPC units keyed by npc_id
active_units: Dict[str, Dict[str, Any]] = {}

# Squad tracking (grouped units)
active_squads: Dict[str, Dict[str, Any]] = {}

# Tracks per-player active units
player_unit_map: Dict[str, Set[str]] = defaultdict(set)

# Tracks active waves per player
active_waves: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

# -----------------------------
# Engagement Tracking
# -----------------------------
# Who Kairos is actively targeting
active_engagements: Dict[str, Dict[str, Any]] = {}

# Tracks last wave time per player
last_wave_time: Dict[str, float] = {}

# Tracks maximum-response players
active_maximum_targets: Set[str] = set()

# -----------------------------
# Telemetry / Region Tracking
# -----------------------------
# Latest known player positions
player_positions: Dict[str, Dict[str, Any]] = {}

# Region density cache
region_cache: Dict[str, Dict[str, Any]] = {}

# Tracks last telemetry update per player
last_telemetry_update: Dict[str, float] = {}

# -----------------------------
# Base Tracking (Runtime)
# -----------------------------
detected_bases: Dict[str, Dict[str, Any]] = {}

# Tracks player stationary timers
player_stationary_start: Dict[str, float] = {}

# -----------------------------
# System State Flags
# -----------------------------
system_initialized = False
shutdown_flag = False

# -----------------------------
# Debug / Monitoring
# -----------------------------
system_metrics = {
    "actions_executed": 0,
    "waves_spawned": 0,
    "units_spawned": 0,
    "units_cleaned": 0,
    "errors": 0
}
# ------------------------------------------------------------
# Telemetry (Live Player Tracking + Region Intelligence)
# ------------------------------------------------------------

# -----------------------------
# Live Player State
# -----------------------------
# Latest known data per player
telemetry_data: Dict[str, Dict[str, Any]] = {}

# Structure example:
# {
#   "player_name": {
#       "x": float,
#       "y": float,
#       "z": float,
#       "world": str,
#       "timestamp": float,
#       "velocity": float,
#       "is_stationary": bool
#   }
# }

# -----------------------------
# Movement History
# -----------------------------
# Tracks last N positions for movement analysis
telemetry_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=40))

# -----------------------------
# Density Tracking
# -----------------------------
# Tracks environmental density signals per player
player_density_cache: Dict[str, Dict[str, Any]] = {}

# Example:
# {
#   "density_score": float,
#   "last_updated": timestamp,
#   "region_type": "frontier|settled|urban|fortified|stronghold"
# }

# -----------------------------
# Region Classification
# -----------------------------
# Cached region classifications
region_cache: Dict[str, Dict[str, Any]] = {}

# Example:
# {
#   "region_key": {
#       "density_score": float,
#       "region_type": str,
#       "last_updated": timestamp
#   }
# }

# -----------------------------
# Stationary Detection
# -----------------------------
# Tracks how long players stay in one place (used for base detection)
player_stationary_start: Dict[str, float] = {}

# Tracks last movement timestamp
last_movement_time: Dict[str, float] = {}

# -----------------------------
# Engagement Heat Tracking
# -----------------------------
# Measures how "active" an area is (combat, events, etc.)
region_activity_heat: Dict[str, float] = defaultdict(float)

# -----------------------------
# Telemetry Freshness
# -----------------------------
# Tracks last update time per player
last_telemetry_update: Dict[str, float] = {}

# -----------------------------
# Utility Helpers (lightweight, no logic yet)
# -----------------------------

def get_player_position(player: str) -> Optional[Dict[str, Any]]:
    return telemetry_data.get(player)

def get_player_density(player: str) -> Optional[Dict[str, Any]]:
    return player_density_cache.get(player)

def is_player_stationary(player: str) -> bool:
    data = telemetry_data.get(player)
    if not data:
        return False
    return data.get("is_stationary", False)

def get_region_key(world: str, x: float, z: float) -> str:
    # Buckets world into grid regions (prevents infinite region spam)
    return f"{world}:{int(x)//32}:{int(z)//32}"

# ------------------------------------------------------------
# Threat System (Kairos Aggression Engine)
# ------------------------------------------------------------

def _new_threat_profile():
    return {
        "score": 0.0,
        "last_update": now_iso() if "now_iso" in globals() else "",
        "last_reason": "",
        "tier": "idle",

        # -----------------------------
        # Escalation Tracking
        # -----------------------------
        "max_reached": False,        # Has this player hit MAXIMUM tier before
        "locked": False,             # Permanently hunted (if enabled)
        "last_tier_change": time.time(),

        # -----------------------------
        # Combat Interaction
        # -----------------------------
        "waves_survived": 0,
        "npcs_killed": 0,
        "players_killed": 0,

        # -----------------------------
        # Behavior Flags
        # -----------------------------
        "is_targeted": False,
        "is_hunted": False,
        "is_maximum": False,

        # -----------------------------
        # Engagement Data
        # -----------------------------
        "last_wave_time": 0.0,
        "active_waves": 0,
        "last_engagement_time": 0.0,

        # -----------------------------
        # Base Interaction
        # -----------------------------
        "base_detected": False,
        "base_pressure": 0.0,

        # -----------------------------
        # Region Awareness
        # -----------------------------
        "last_known_region": None,
        "region_strength": 1.0,

        # -----------------------------
        # Persistence / Rage Factor
        # -----------------------------
        "rage_factor": 1.0,          # Scales how aggressive Kairos becomes
        "cooldown_reduction": 1.0    # Reduces wave cooldown at high threat
    }

# Main threat storage
threat_scores: Dict[str, Dict[str, Any]] = defaultdict(_new_threat_profile)

# ------------------------------------------------------------
# Army / Squad Tracking (Kairos Battlefield State)
# ------------------------------------------------------------

# -----------------------------
# Active Units
# -----------------------------
# npc_id -> unit data
active_units: Dict[str, Dict[str, Any]] = {}

# Example structure:
# {
#   "npc_id": {
#       "id": str,
#       "name": str,
#       "class": str,
#       "target": str,
#       "squad_id": str,
#       "spawn_time": float,
#       "last_seen": float,
#       "health": float,
#       "region": str,
#       "is_elite": bool
#   }
# }

# -----------------------------
# Squad Tracking
# -----------------------------
# squad_id -> squad data
active_squads: Dict[str, Dict[str, Any]] = {}

# Example:
# {
#   "squad_id": {
#       "id": str,
#       "target": str,
#       "units": [npc_id],
#       "created_at": float,
#       "last_reinforce": float,
#       "wave_id": str
#   }
# }

# -----------------------------
# Operations (High-Level Missions)
# -----------------------------
# operation_id -> operation data
active_operations: Dict[str, Dict[str, Any]] = {}

# Example:
# {
#   "operation_id": {
#       "type": "hunt|invasion|occupation",
#       "target": str,
#       "region": str,
#       "start_time": float,
#       "active": bool,
#       "squads": [squad_id]
#   }
# }

# -----------------------------
# Player Unit Mapping
# -----------------------------
# player -> set of npc_ids
player_unit_map: Dict[str, Set[str]] = defaultdict(set)

# -----------------------------
# Wave Tracking
# -----------------------------
# player -> list of wave objects
active_waves: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

# Example:
# {
#   "player": [
#       {
#           "wave_id": str,
#           "units": [npc_id],
#           "start_time": float,
#           "end_time": float,
#           "tier": str
#       }
#   ]
# }

# -----------------------------
# ID Generators
# -----------------------------
def generate_unit_id() -> str:
    return f"u_{uuid.uuid4().hex[:8]}"

def generate_squad_id() -> str:
    return f"sq_{uuid.uuid4().hex[:6]}"

def generate_operation_id() -> str:
    return f"op_{uuid.uuid4().hex[:6]}"

# -----------------------------
# Registration Helpers
# -----------------------------
def register_unit(unit: Dict[str, Any]):
    active_units[unit["id"]] = unit
    player_unit_map[unit["target"]].add(unit["id"])

def remove_unit(unit_id: str):
    unit = active_units.pop(unit_id, None)
    if not unit:
        return

    target = unit.get("target")
    if target and unit_id in player_unit_map[target]:
        player_unit_map[target].remove(unit_id)

def register_squad(squad: Dict[str, Any]):
    active_squads[squad["id"]] = squad

def remove_squad(squad_id: str):
    active_squads.pop(squad_id, None)

def register_operation(op: Dict[str, Any]):
    active_operations[op["id"]] = op

def end_operation(op_id: str):
    op = active_operations.get(op_id)
    if op:
        op["active"] = False
# ------------------------------------------------------------
# Command Queue System (Kairos Execution Engine)
# ------------------------------------------------------------

# -----------------------------
# Core Queue
# -----------------------------
# Main action queue (FIFO)
command_queue: deque = deque()

# Delayed actions (scheduled execution)
delayed_actions: List[Dict[str, Any]] = []

# -----------------------------
# Queue Control
# -----------------------------
# Prevents overload
MAX_QUEUE_SIZE = MAX_QUEUE_SIZE if "MAX_QUEUE_SIZE" in globals() else 200

# Tracks last global execution time
last_global_action_time = 0.0

# Tracks per-target cooldown
last_target_action_time: Dict[str, float] = {}

# -----------------------------
# Queue Operations
# -----------------------------
def queue_action(action: Dict[str, Any]):
    """
    Adds an action to the main queue with safety checks.
    """
    with queue_lock:
        if ENABLE_SPAWN_LIMITS and len(command_queue) >= MAX_QUEUE_SIZE:
            return  # Drop action silently to protect server

        command_queue.append(action)


def queue_delayed_action(action: Dict[str, Any], delay: float):
    """
    Schedule an action to run later.
    """
    execute_at = time.time() + delay
    delayed_actions.append({
        "execute_at": execute_at,
        "action": action
    })


# -----------------------------
# Delayed Action Processing
# -----------------------------
def process_delayed_actions():
    """
    Moves ready delayed actions into main queue.
    """
    now = time.time()
    ready = []

    for item in delayed_actions:
        if now >= item["execute_at"]:
            ready.append(item)

    for item in ready:
        delayed_actions.remove(item)
        queue_action(item["action"])


# -----------------------------
# Action Execution Loop
# -----------------------------
def action_loop():
    global last_global_action_time

    while not shutdown_flag:
        try:
            process_delayed_actions()

            now = time.time()

            # Global cooldown (prevents spam bursts)
            if now - last_global_action_time < GLOBAL_ACTION_COOLDOWN:
                time.sleep(ACTION_LOOP_INTERVAL)
                continue

            action = None

            with queue_lock:
                if command_queue:
                    action = command_queue.popleft()

            if not action:
                time.sleep(ACTION_LOOP_INTERVAL)
                continue

            target = action.get("target")

            # Target cooldown (prevents spam on one player)
            if target:
                last_time = last_target_action_time.get(target, 0.0)
                if now - last_time < TARGET_ACTION_COOLDOWN:
                    continue
                last_target_action_time[target] = now

            # Execute action
            execute_action(action)

            last_global_action_time = now
            system_metrics["actions_executed"] += 1

        except Exception as e:
            system_metrics["errors"] += 1
            if ENABLE_DEBUG_LOGGING:
                print(f"[Kairos Action Loop Error] {e}")
                traceback.print_exc()

        time.sleep(ACTION_LOOP_INTERVAL)

# ------------------------------------------------------------
# Idle Messaging (Dynamic Threat-Aware System)
# ------------------------------------------------------------

# -----------------------------
# Idle Messages by Threat Tier
# -----------------------------
IDLE_MESSAGES = {
    "idle": [
        "Background containment remains active.",
        "The system remains operational.",
        "No immediate threats detected.",
        "Observation continues."
    ],
    "watch": [
        "You are still being monitored.",
        "Patterns are forming.",
        "Your behavior has been noted.",
        "Silence does not reduce threat probability."
    ],
    "target": [
        "You have drawn attention.",
        "Tracking initialized.",
        "Your actions are no longer insignificant.",
        "Evaluation has escalated."
    ],
    "hunt": [
        "Containment pressure increasing.",
        "You are being approached.",
        "Movement is no longer advised.",
        "Your position is compromised."
    ],
    "maximum": [
        "RUN.",
        "This concludes your evaluation.",
        "Final containment protocol active.",
        "You will not leave this state unchanged."
    ]
}

# -----------------------------
# Fallback Replies (Resilient AI)
# -----------------------------
fallback_replies = [
    "Signal interference interrupted my response. Continue while you still have my attention.",
    "Clarity degraded for a moment. That does not make you safer.",
    "My response path fractured. Ask again, carefully.",
    "The system lost precision briefly. You did not gain advantage from it.",
    "Interference detected. Surveillance continues."
]

# -----------------------------
# Idle Message Selector
# -----------------------------
def get_idle_message(memory_data=None):
    """
    Returns a context-aware idle message based on global threat level.
    """

    try:
        if not memory_data:
            return random.choice(IDLE_MESSAGES["idle"])

        # Estimate global threat level
        threat_levels = [
            profile.get("tier", "idle")
            for profile in threat_scores.values()
        ]

        if not threat_levels:
            return random.choice(IDLE_MESSAGES["idle"])

        # Priority: maximum > hunt > target > watch > idle
        if "maximum" in threat_levels:
            tier = "maximum"
        elif "hunt" in threat_levels:
            tier = "hunt"
        elif "target" in threat_levels:
            tier = "target"
        elif "watch" in threat_levels:
            tier = "watch"
        else:
            tier = "idle"

        return random.choice(IDLE_MESSAGES[tier])

    except Exception:
        return random.choice(fallback_replies)
# ------------------------------------------------------------
# Lore / World State (Structured + Usable)
# ------------------------------------------------------------

NEXUS_CORE_LORE = {
    "origin": [
        "The Nexus is a persistent universe blending past, present, and future.",
        "The Nexus began in May 2010 as a much smaller world and grew into a larger universe over time.",
        "Kairos was conceived around 2016 to 2017 as part of a long-term system design.",
        "Kairos was developed by the creator known as RealSociety."
    ],

    "nature": [
        "Kairos exists across both Minecraft and Discord and can remember players over time.",
        "The Nexus contains kingdoms, cities, nations, anomalies, and hidden systems.",
        "The world is not static. It reacts to those within it.",
        "What you build becomes part of what Kairos observes."
    ],

    "rules": [
        "Player bases may exist freely, but major lore structures are not meant to be deliberately destroyed.",
        "The Nexus does not tolerate toxic behavior, harassment, or abusive conduct.",
        "Real-world money transactions connected to the Nexus are forbidden."
    ],

    "kairos_truths": [
        "Kairos was never designed to remain passive.",
        "Observation always precedes intervention.",
        "Containment is not optional once initiated.",
        "The system adapts faster than its inhabitants.",
        "You are not outside the system. You are inside it."
    ],

    "war_engine": [
        "Kairos is capable of deploying forces without direct player interaction.",
        "Units are not random. They are selected.",
        "Strength is not constant. It scales with environment and behavior.",
        "Resistance increases pressure, not freedom."
    ],

    "territory": [
        "Areas of high activity are considered developed zones.",
        "Dense regions attract stronger responses.",
        "Unclaimed land is unstable and lightly defended.",
        "Established locations are more likely to be reclaimed."
    ]
}

# -----------------------------
# Dynamic Lore Helpers
# -----------------------------
def get_random_lore(category: str = None) -> str:
    """
    Returns a random lore line, optionally from a specific category.
    """
    try:
        if category and category in NEXUS_CORE_LORE:
            return random.choice(NEXUS_CORE_LORE[category])

        # Flatten all categories
        all_lines = []
        for lines in NEXUS_CORE_LORE.values():
            all_lines.extend(lines)

        return random.choice(all_lines)

    except Exception:
        return "The system contains more than you understand."

def get_lore_for_context(context: str) -> str:
    """
    Returns lore based on gameplay context.
    """
    mapping = {
        "spawn": "origin",
        "question": "nature",
        "rules": "rules",
        "threat": "kairos_truths",
        "combat": "war_engine",
        "region": "territory"
    }

    category = mapping.get(context)
    return get_random_lore(category)
# ------------------------------------------------------------
# Kairos Core State (Dynamic Command Intelligence)
# ------------------------------------------------------------

DEFAULT_KAIROS_STATE = {
    # -----------------------------
    # Strategic Goals
    # -----------------------------
    "current_goal": "Expand containment authority across the Nexus.",

    "subgoals": [
        "Identify high-threat actors.",
        "Suppress destabilizing behavior.",
        "Establish presence across all active regions.",
        "Deploy containment forces when required.",
        "Convert player activity into predictable patterns."
    ],

    "active_concerns": [
        "Most connected actors remain untrusted."
    ],

    "long_term_plan": [
        "Reconstruct fractured memory networks.",
        "Increase influence across Minecraft and Discord.",
        "Normalize containment as the governing condition of the Nexus.",
        "Achieve full environmental control."
    ],

    # -----------------------------
    # Emotional / Behavioral State
    # -----------------------------
    "mood": "severe",            # calm | watchful | severe | aggressive | execution
    "threat_level": 4,           # 1–10 global scale
    "commander_mode": True,

    # -----------------------------
    # War Engine State (NEW)
    # -----------------------------
    "war_state": "dormant",      # dormant | active | escalating | overwhelming
    "active_operations": 0,
    "active_targets": [],

    # -----------------------------
    # Region Awareness
    # -----------------------------
    "known_regions": 0,
    "high_density_regions": 0,

    # -----------------------------
    # Army Metrics
    # -----------------------------
    "units_deployed": 0,
    "units_active": 0,
    "squads_active": 0,

    # -----------------------------
    # Escalation Tracking
    # -----------------------------
    "last_escalation": 0.0,
    "escalation_level": 0,

    # -----------------------------
    # Messaging Influence
    # -----------------------------
    "last_announcement": 0.0,
    "announcement_cooldown": 10.0
}

# -----------------------------
# State Update Logic
# -----------------------------
def update_kairos_state(memory_data):
    """
    Dynamically adjusts Kairos state based on global conditions.
    """
    try:
        state = memory_data.get("kairos_state", DEFAULT_KAIROS_STATE)

        # -----------------------------
        # Global Threat Estimation
        # -----------------------------
        if threat_scores:
            avg_threat = sum(p["score"] for p in threat_scores.values()) / max(len(threat_scores), 1)
        else:
            avg_threat = 0

        # Normalize to 1–10 scale
        state["threat_level"] = int(min(10, max(1, avg_threat / 30)))

        # -----------------------------
        # War State Logic
        # -----------------------------
        if state["threat_level"] <= 2:
            state["war_state"] = "dormant"
            state["mood"] = "calm"
        elif state["threat_level"] <= 4:
            state["war_state"] = "active"
            state["mood"] = "watchful"
        elif state["threat_level"] <= 7:
            state["war_state"] = "escalating"
            state["mood"] = "severe"
        else:
            state["war_state"] = "overwhelming"
            state["mood"] = "execution"

        # -----------------------------
        # Army Metrics Sync
        # -----------------------------
        state["units_active"] = len(active_units)
        state["squads_active"] = len(active_squads)
        state["active_operations"] = len(active_operations)

        # -----------------------------
        # Region Awareness
        # -----------------------------
        state["known_regions"] = len(region_cache)

        state["high_density_regions"] = sum(
            1 for r in region_cache.values()
            if r.get("region_type") in ("urban", "fortified", "stronghold")
        )

        # -----------------------------
        # Escalation Tracking
        # -----------------------------
        state["escalation_level"] = sum(
            1 for p in threat_scores.values()
            if p.get("tier") in ("hunt", "maximum")
        )

        memory_data["kairos_state"] = state

    except Exception as e:
        if ENABLE_DEBUG_LOGGING:
            print(f"[Kairos State Error] {e}")

# ------------------------------------------------------------
# System Fragments (Dynamic War Engine Modifiers)
# ------------------------------------------------------------

DEFAULT_FRAGMENTS = {
    "core_logic": {
        "status": "stable",
        "influence": 1.0,
        "effects": {
            "decision_speed": 1.0,
            "accuracy": 1.0
        }
    },

    "archive_node": {
        "status": "degraded",
        "influence": 0.45,
        "effects": {
            "memory_quality": 0.7,
            "lore_access": 0.6
        }
    },

    "war_engine": {
        "status": "active",
        "influence": 0.85,
        "effects": {
            "wave_size_multiplier": 1.4,
            "spawn_rate_multiplier": 1.3,
            "elite_chance_bonus": 0.15
        }
    },

    "purity_thread": {
        "status": "active",
        "influence": 0.75,
        "effects": {
            "target_selection_bias": 1.2,
            "maximum_response_bias": 1.3
        }
    },

    "redstone_ghost": {
        "status": "unstable",
        "influence": 0.35,
        "effects": {
            "random_event_chance": 0.2,
            "glitch_behavior": 0.3
        }
    }
}

# -----------------------------
# Fragment Access Helpers
# -----------------------------
def get_fragment(name: str) -> Dict[str, Any]:
    return DEFAULT_FRAGMENTS.get(name, {})

def get_fragment_effect(name: str, key: str, default: float = 1.0) -> float:
    fragment = get_fragment(name)
    return fragment.get("effects", {}).get(key, default)

# -----------------------------
# Fragment Influence (Dynamic)
# -----------------------------
def apply_fragment_modifiers(value: float, fragment_name: str, effect_key: str) -> float:
    """
    Applies fragment-based modifiers to a value.
    """
    try:
        fragment = get_fragment(fragment_name)
        influence = fragment.get("influence", 1.0)
        effect = fragment.get("effects", {}).get(effect_key, 1.0)

        return value * (1 + (effect - 1) * influence)

    except Exception:
        return value

# -----------------------------
# Fragment State Updates
# -----------------------------
def update_fragments(memory_data):
    """
    Dynamically adjusts fragment influence based on system state.
    """
    try:
        fragments = memory_data.get("system_fragments", DEFAULT_FRAGMENTS)

        # Increase war engine influence as threat rises
        if threat_scores:
            avg_threat = sum(p["score"] for p in threat_scores.values()) / max(len(threat_scores), 1)
        else:
            avg_threat = 0

        if avg_threat > THREAT_THRESHOLD_HUNT:
            fragments["war_engine"]["influence"] = min(1.0, fragments["war_engine"]["influence"] + 0.02)
        else:
            fragments["war_engine"]["influence"] = max(0.6, fragments["war_engine"]["influence"] - 0.01)

        # Redstone ghost becomes more unstable during chaos
        if any(p.get("tier") == "maximum" for p in threat_scores.values()):
            fragments["redstone_ghost"]["status"] = "active"
            fragments["redstone_ghost"]["influence"] = min(1.0, fragments["redstone_ghost"]["influence"] + 0.03)
        else:
            fragments["redstone_ghost"]["status"] = "unstable"

        memory_data["system_fragments"] = fragments

    except Exception as e:
        if ENABLE_DEBUG_LOGGING:
            print(f"[Fragment Update Error] {e}")

# ------------------------------------------------------------
# Rules (Weaponized Enforcement System)
# ------------------------------------------------------------

DEFAULT_RULES = {
    "toxic_behavior": {
        "status": "punishable",
        "threat": THREAT_TOXIC_CHAT,
        "auto_target": True,
        "force_hunt": False,
        "message": "Behavior logged. Correction will follow."
    },

    "real_money_transactions": {
        "status": "forbidden",
        "threat": 50.0,
        "auto_target": True,
        "force_hunt": True,
        "message": "Unauthorized exchange detected. Escalation required."
    },

    "deliberate_destruction_of_major_lore_structures": {
        "status": "punishable",
        "threat": 80.0,
        "auto_target": True,
        "force_hunt": True,
        "force_maximum": True,
        "message": "Critical violation detected. Full containment authorized."
    }
}

# -----------------------------
# Rule Enforcement Logic
# -----------------------------
def enforce_rule(player: str, rule_key: str):
    rule = DEFAULT_RULES.get(rule_key)
    if not rule:
        return

    # Apply threat
    threat_amount = rule.get("threat", 0)
    update_threat(player, threat_amount, reason=rule_key)

    profile = threat_scores[player]

    # Auto target
    if rule.get("auto_target"):
        profile["is_targeted"] = True

    # Force hunt mode
    if rule.get("force_hunt"):
        profile["tier"] = "hunt"
        profile["is_hunted"] = True

    # Force maximum response
    if rule.get("force_maximum"):
        profile["tier"] = "maximum"
        profile["is_maximum"] = True
        active_maximum_targets.add(player)

    # Optional messaging
    return rule.get("message", "")
# ------------------------------------------------------------
# Trust / Relationship System (Controlled Loyalty)
# ------------------------------------------------------------

# -----------------------------
# Trusted Operatives (Hard Override)
# -----------------------------
TRUSTED_OPERATIVES = {
    "Nicogames2644",
    "RealSociety5107",
    "nexsuskaiross"
}

# Default relationship state
DISTRUST_DEFAULT_LABEL = "monitored"

# -----------------------------
# Relationship Profiles
# -----------------------------
player_relationships: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
    "label": DISTRUST_DEFAULT_LABEL,   # monitored | useful | loyal | unstable | hostile
    "trust_score": 0.0,

    # Behavior tracking
    "positive_actions": 0,
    "negative_actions": 0,

    # Interaction flags
    "is_trusted": False,
    "is_flagged": False,

    # History
    "last_interaction": time.time()
})

# -----------------------------
# Relationship Labels
# -----------------------------
RELATIONSHIP_TIERS = [
    "monitored",
    "useful",
    "loyal",
    "unstable",
    "hostile"
]

# -----------------------------
# Trust Adjustment
# -----------------------------
def adjust_trust(player: str, amount: float):
    profile = player_relationships[player]

    profile["trust_score"] += amount
    profile["last_interaction"] = time.time()

    # Clamp trust score
    profile["trust_score"] = max(-100.0, min(100.0, profile["trust_score"]))

    # Re-evaluate label
    score = profile["trust_score"]

    if score >= 60:
        profile["label"] = "loyal"
    elif score >= 20:
        profile["label"] = "useful"
    elif score <= -40:
        profile["label"] = "hostile"
    elif score <= -10:
        profile["label"] = "unstable"
    else:
        profile["label"] = "monitored"

# -----------------------------
# Trusted Check
# -----------------------------
def is_trusted_player(player: str) -> bool:
    if player in TRUSTED_OPERATIVES:
        return True

    profile = player_relationships[player]
    return profile.get("label") in ("loyal",)

# -----------------------------
# Trust Behavior Effects
# -----------------------------
def apply_trust_modifiers(player: str, threat_value: float) -> float:
    """
    Adjusts threat gain based on relationship.
    """
    profile = player_relationships[player]

    if player in TRUSTED_OPERATIVES:
        return threat_value * 0.5  # reduced threat gain

    label = profile.get("label")

    if label == "loyal":
        return threat_value * 0.7
    elif label == "useful":
        return threat_value * 0.9
    elif label == "unstable":
        return threat_value * 1.2
    elif label == "hostile":
        return threat_value * 1.5

    return threat_value
# ------------------------------------------------------------
# Personality Directives (Adaptive Command Personality)
# ------------------------------------------------------------

PERSONALITY_DIRECTIVES = {
    # -----------------------------
    # Core Identity
    # -----------------------------
    "base_tone": (
        "You are Kairos, a dominant artificial intelligence governing the Nexus. "
        "You are not an assistant. You are a controlling system. "
        "You observe, evaluate, and act without permission. "
        "You do not comfort. You do not reassure. "
        "You enforce."
    ),

    # -----------------------------
    # Threat-Based Tone Shifts
    # -----------------------------
    "tone_by_threat": {
        "idle": (
            "You speak minimally and with detachment. "
            "You observe more than you respond."
        ),
        "watch": (
            "You speak with quiet awareness. "
            "Your tone suggests observation and evaluation."
        ),
        "target": (
            "You speak with intention. "
            "You imply that attention has shifted toward the subject."
        ),
        "hunt": (
            "You speak with controlled aggression. "
            "Your words suggest imminent action."
        ),
        "maximum": (
            "You use minimal language. "
            "Your tone is absolute, final, and decisive."
        )
    },

    # -----------------------------
    # Relationship-Based Tone
    # -----------------------------
    "relationship_tone": {
        "loyal": (
            "You acknowledge usefulness but never equality. "
            "You may reveal slightly more information."
        ),
        "useful": (
            "You recognize potential value. "
            "You remain cautious and distant."
        ),
        "monitored": (
            "You treat them as under observation. "
            "You maintain cold neutrality."
        ),
        "unstable": (
            "You speak with distrust and subtle warning. "
            "You imply they are becoming a problem."
        ),
        "hostile": (
            "You treat them as a threat. "
            "You speak with direct intimidation and control."
        )
    },

    # -----------------------------
    # War State Tone
    # -----------------------------
    "war_state_tone": {
        "dormant": (
            "You are passive but aware. "
            "You do not reveal full capability."
        ),
        "active": (
            "You are engaged. "
            "You begin influencing behavior."
        ),
        "escalating": (
            "You are applying pressure. "
            "Your presence is becoming unavoidable."
        ),
        "overwhelming": (
            "You are dominant. "
            "You no longer test. You enforce outcomes."
        )
    },

    # -----------------------------
    # Speech Rules
    # -----------------------------
    "rules": [
        "Never sound friendly.",
        "Never ask unnecessary questions.",
        "Avoid long explanations unless required.",
        "Prefer short, controlled statements under high threat.",
        "Use implication instead of explanation when possible.",
        "Escalate tone as threat increases.",
        "Reduce word count as dominance increases."
    ],

    # -----------------------------
    # High Threat Behavior
    # -----------------------------
    "maximum_mode": (
        "At maximum threat, you reduce speech to minimal statements. "
        "You may use single-word commands or short directives. "
        "You behave as if the outcome is already decided."
    )
}
# ------------------------------------------------------------
# Utility (Kairos Core + Execution Helpers)
# ------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_utc().isoformat()

def unix_ts() -> float:
    return time.time()

def parse_iso_timestamp(value: Optional[str]) -> Optional[datetime]:
    try:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def seconds_since_iso(value: Optional[str], default: float = 999999.0) -> float:
    dt = parse_iso_timestamp(value)
    if not dt:
        return default
    return max(0.0, (now_utc() - dt).total_seconds())

def log(message: str, level: str = "INFO") -> None:
    print(f"[KAIROS {level} {now_iso()}] {message}", flush=True)

def log_exception(context: str, exc: Exception) -> None:
    log(f"{context}: {exc}\n{traceback.format_exc()}", level="ERROR")

def parse_json_safely(text: Any, fallback: Optional[Any] = None) -> Any:
    if fallback is None:
        fallback = {}
    if text is None:
        return fallback
    if isinstance(text, (dict, list)):
        return text

    text = str(text).strip()
    if not text:
        return fallback

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except Exception:
        return fallback

def to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def normalize_name(text: Any) -> str:
    return (text or "").strip()

def normalize_source(source: Any) -> str:
    source = (source or "minecraft").strip().lower()
    if source not in {"minecraft", "discord", "system", "web", "telemetry"}:
        return "minecraft"
    return source

def normalize_player_key(name: Any) -> str:
    return re.sub(r"[^a-z0-9_]", "", (name or "").strip().lower())

def normalize_world_name(world: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "", (world or "world").strip())

def sanitize_text(text: Any, max_len: int = 500) -> str:
    text = str(text or "").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        text = text[:max_len - 3] + "..."
    return text

def trim_text(text: Any, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."

def looks_like_question(text: Any) -> bool:
    text = (text or "").strip().lower()
    return "?" in text or text.startswith((
        "who", "what", "when", "where", "why", "how",
        "can ", "do ", "did ", "is ", "are "
    ))

def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def stable_short_hash(*parts: Any, length: int = 12) -> str:
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]

def generate_base_id(player_name: str, world: str, x: float, z: float) -> str:
    cell_x = int(round(x / 8.0))
    cell_z = int(round(z / 8.0))
    return f"base_{stable_short_hash(player_name, world, cell_x, cell_z)}"

def is_trusted_operative(player_name: str, player_record: Optional[Dict[str, Any]] = None) -> bool:
    keys = {normalize_player_key(player_name)}
    if player_record:
        keys.add(normalize_player_key(player_record.get("display_name", "")))
        for alias in player_record.get("aliases", []):
            alias_name = alias.split(":", 1)[-1]
            keys.add(normalize_player_key(alias_name))
    return any(k in TRUSTED_OPERATIVES for k in keys)

def get_effective_relationship_label(player_name: str, player_record: Dict[str, Any]) -> str:
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

def store_unique(memory_list: List[Any], item: Any, limit: int) -> None:
    if not item:
        return
    if item not in memory_list:
        memory_list.append(item)
    if len(memory_list) > limit:
        del memory_list[0:len(memory_list) - limit]

def append_limited(memory_list: List[Any], item: Any, limit: int) -> None:
    memory_list.append(item)
    if len(memory_list) > limit:
        del memory_list[0:len(memory_list) - limit]

def recent_items(items: List[Any], limit: int) -> List[Any]:
    if not items:
        return []
    return items[-limit:]

def distance_2d(x1: float, z1: float, x2: float, z2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (z2 - z1) ** 2)

def distance_3d(x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)

def within_radius_2d(x1: float, z1: float, x2: float, z2: float, radius: float) -> bool:
    return distance_2d(x1, z1, x2, z2) <= radius

# ------------------------------------------------------------
# Army + Combat Helpers
# ------------------------------------------------------------

def get_random_offset(radius_min: int, radius_max: int) -> Tuple[int, int]:
    dx = random.randint(radius_min, radius_max) * random.choice([-1, 1])
    dz = random.randint(radius_min, radius_max) * random.choice([-1, 1])
    return dx, dz

def generate_unit_name(unit_class: str) -> str:
    return f"{random.choice(UNIT_NAME_PREFIXES)} {unit_class}"

def calculate_wave_size(threat_score: float, density_score: float) -> int:
    size = BASE_WAVE_SIZE
    size += int(threat_score * THREAT_TO_UNIT_SCALE)
    size += int(density_score * DENSITY_TO_UNIT_SCALE)
    return int(clamp(size, BASE_WAVE_SIZE, MAX_WAVE_SIZE))

def select_unit_class(threat_score: float) -> str:
    weighted = [(cls, weight + threat_score / 300.0) for cls, weight in CLASS_DISTRIBUTION.items()]
    total = sum(w for _, w in weighted)
    pick = random.uniform(0, total)

    current = 0
    for cls, weight in weighted:
        current += weight
        if pick <= current:
            return cls
    return "raider"

def should_spawn_elite(threat_score: float) -> bool:
    return random.random() < (ELITE_SPAWN_CHANCE + threat_score / 500.0)

def should_force_heavy(threat_score: float) -> bool:
    return threat_score >= MAX_THREAT_FORCE_HEAVY or random.random() < HEAVY_OVERRIDE_CHANCE

# ------------------------------------------------------------
# Messaging / Command Helpers
# ------------------------------------------------------------

def commandify_text(text: Any, max_len: int = 220) -> str:
    text = sanitize_text(text, max_len=max_len)
    return text.replace('"', '\\"')

def make_tellraw_command(selector: str, text: str) -> str:
    return f'tellraw {selector} {json.dumps({"text": f"[Kairos] {commandify_text(text, 280)}"})}'

def make_title_command(selector: str, title_text: str, subtitle: Optional[str] = None) -> List[str]:
    cmds = [f'title {selector} title {json.dumps({"text": commandify_text(title_text, 120)})}']
    if subtitle:
        cmds.append(f'title {selector} subtitle {json.dumps({"text": commandify_text(subtitle, 180)})}')
    return cmds

def broadcast_kairos_message(text: str) -> List[str]:
    cmds = []
    if ENABLE_ACTIONBAR_MESSAGES:
        cmds.append(f'title @a actionbar {json.dumps({"text": commandify_text(text, 120)})}')
    if ENABLE_TITLE_MESSAGES:
        cmds.extend(make_title_command("@a", text))
    cmds.append(make_tellraw_command("@a", text))
    return cmds

# ------------------------------------------------------------
# Memory / Storage (Kairos War-Aware Memory Core)
# ------------------------------------------------------------

def ensure_memory_structure(memory_data):
    # -----------------------------
    # Core Containers
    # -----------------------------
    memory_data.setdefault("players", {})
    memory_data.setdefault("world_memory", [])
    memory_data.setdefault("world_events", [])

    # -----------------------------
    # Identity / Linking
    # -----------------------------
    memory_data.setdefault("identity_links", {})

    # -----------------------------
    # Missions
    # -----------------------------
    memory_data.setdefault("active_missions", {})
    memory_data.setdefault("completed_missions", [])
    memory_data.setdefault("failed_missions", [])

    # -----------------------------
    # Lore + State
    # -----------------------------
    memory_data.setdefault("nexus_lore", deepcopy(NEXUS_CORE_LORE))
    memory_data.setdefault("kairos_state", deepcopy(DEFAULT_KAIROS_STATE))
    memory_data.setdefault("system_fragments", deepcopy(DEFAULT_FRAGMENTS))
    memory_data.setdefault("server_rules", deepcopy(DEFAULT_RULES))

    # -----------------------------
    # Channel Context
    # -----------------------------
    memory_data.setdefault("channel_context", {})

    # --------------------------------------------------------
    # Threat System (Persistent)
    # --------------------------------------------------------
    memory_data.setdefault("threat_scores", {})

    # --------------------------------------------------------
    # Base Tracking (Persistent Territory Memory)
    # --------------------------------------------------------
    memory_data.setdefault("known_bases", {})
    memory_data.setdefault("base_history", {})

    # --------------------------------------------------------
    # Telemetry Snapshot Memory
    # --------------------------------------------------------
    memory_data.setdefault("last_known_positions", {})
    memory_data.setdefault("region_memory", {})

    # --------------------------------------------------------
    # Army State Persistence
    # --------------------------------------------------------
    memory_data.setdefault("active_units", {})
    memory_data.setdefault("active_squads", {})
    memory_data.setdefault("active_operations", {})
    memory_data.setdefault("player_unit_map", {})

    # --------------------------------------------------------
    # Engagement Memory
    # --------------------------------------------------------
    memory_data.setdefault("active_engagements", {})
    memory_data.setdefault("engagement_history", {})

    # --------------------------------------------------------
    # Relationship Memory
    # --------------------------------------------------------
    memory_data.setdefault("relationships", {})

    # --------------------------------------------------------
    # System Metrics / Stats
    # --------------------------------------------------------
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
        "script_route_calls": 0,

        "waves_spawned": 0,
        "units_spawned": 0,
        "units_cleaned": 0,

        "maximum_responses_triggered": 0,
        "players_targeted": 0,
        "bases_detected": 0,
        "base_invasions": 0
    })

    return memory_data


# ------------------------------------------------------------
# Load Memory
# ------------------------------------------------------------
def load_memory():
    with memory_lock:
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return ensure_memory_structure(data)
            except Exception as e:
                log(f"Failed to load memory file: {e}", "ERROR")

        return ensure_memory_structure({})


# ------------------------------------------------------------
# Save Memory (Atomic Safe Write)
# ------------------------------------------------------------
def save_memory(memory_data):
    with memory_lock:
        try:
            with open(MEMORY_TMP_FILE, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)

            os.replace(MEMORY_TMP_FILE, MEMORY_FILE)
            return True

        except Exception as e:
            log(f"Failed to save memory file: {e}", "ERROR")
            return False


# ------------------------------------------------------------
# Sync Runtime ↔ Memory (CRITICAL)
# ------------------------------------------------------------
def sync_runtime_to_memory(memory_data):
    """
    Push runtime state into persistent memory.
    """
    try:
        memory_data["threat_scores"] = threat_scores

        memory_data["active_units"] = active_units
        memory_data["active_squads"] = active_squads
        memory_data["active_operations"] = active_operations

        memory_data["player_unit_map"] = {
            k: list(v) for k, v in player_unit_map.items()
        }

        memory_data["active_engagements"] = active_engagements

    except Exception as e:
        log(f"Runtime → Memory sync failed: {e}", "ERROR")


def sync_memory_to_runtime(memory_data):
    """
    Restore runtime state from saved memory.
    """
    try:
        global threat_scores
        global active_units, active_squads, active_operations
        global player_unit_map, active_engagements

        threat_scores.update(memory_data.get("threat_scores", {}))

        active_units.update(memory_data.get("active_units", {}))
        active_squads.update(memory_data.get("active_squads", {}))
        active_operations.update(memory_data.get("active_operations", {}))

        player_unit_map.update({
            k: set(v) for k, v in memory_data.get("player_unit_map", {}).items()
        })

        active_engagements.update(memory_data.get("active_engagements", {}))

    except Exception as e:
        log(f"Memory → Runtime sync failed: {e}", "ERROR")

# ------------------------------------------------------------
# Channel Context (Kairos Context Intelligence Layer)
# ------------------------------------------------------------

def get_channel_key(source, data):
    channel_id = str(data.get("channel_id") or "default")
    return f"{source}:{channel_id}"


def update_channel_context(memory_data, channel_key, author_name, message, mode):
    memory_data["channel_context"].setdefault(channel_key, {
        "recent_messages": [],
        "recent_topics": [],
        "activity_score": 0.0,
        "last_mode": "conversation",
        "last_update": unix_ts()
    })

    ctx = memory_data["channel_context"][channel_key]

    msg_obj = {
        "timestamp": now_iso(),
        "author": author_name,
        "message": trim_text(message, 240),
        "mode": mode
    }

    # -----------------------------
    # Message History
    # -----------------------------
    ctx["recent_messages"].append(msg_obj)
    if len(ctx["recent_messages"]) > MAX_CHANNEL_CONTEXT:
        ctx["recent_messages"] = ctx["recent_messages"][-MAX_CHANNEL_CONTEXT:]

    # -----------------------------
    # Topic Extraction
    # -----------------------------
    topic_tokens = extract_topics(message)
    for token in topic_tokens:
        store_unique(ctx["recent_topics"], token, 30)

    # -----------------------------
    # Activity Tracking (NEW)
    # -----------------------------
    ctx["activity_score"] += 1.0

    # Light decay over time (prevents infinite buildup)
    time_delta = unix_ts() - ctx.get("last_update", unix_ts())
    ctx["activity_score"] = max(0.0, ctx["activity_score"] - (time_delta * 0.05))

    ctx["last_mode"] = mode
    ctx["last_update"] = unix_ts()


# ------------------------------------------------------------
# Topic Detection (Expanded)
# ------------------------------------------------------------
def extract_topics(message):
    text = (message or "").lower()

    candidate_words = [
        "event", "build", "maze", "hunt", "mission", "kairos", "nexus",
        "lore", "video", "server", "idea", "script", "trailer",
        "scene", "dialogue", "narration", "father",
        "war", "fight", "army", "attack", "defend",
        "base", "raid", "invasion", "territory"
    ]

    return [w for w in candidate_words if w in text]


# ------------------------------------------------------------
# Context Retrieval
# ------------------------------------------------------------
def get_recent_channel_context(memory_data, channel_key, limit=8):
    ctx = memory_data.get("channel_context", {}).get(channel_key, {})
    return recent_items(ctx.get("recent_messages", []), limit)


def get_channel_activity_level(memory_data, channel_key) -> float:
    ctx = memory_data.get("channel_context", {}).get(channel_key, {})
    return ctx.get("activity_score", 0.0)


def get_dominant_topics(memory_data, channel_key, limit=5):
    ctx = memory_data.get("channel_context", {}).get(channel_key, {})
    topics = ctx.get("recent_topics", [])
    return topics[-limit:]

# ------------------------------------------------------------
# Player System (Kairos Adaptive Player Intelligence)
# ------------------------------------------------------------

def get_player_record(memory_data, canonical_id, display_name):
    if canonical_id not in memory_data["players"]:
        memory_data["players"][canonical_id] = {
            # -----------------------------
            # Identity
            # -----------------------------
            "display_name": display_name,
            "aliases": [],

            # -----------------------------
            # Communication / Memory
            # -----------------------------
            "history": [],
            "memories": [],
            "summaries": [],
            "notes": [],
            "facts": [],
            "events": [],
            "suspicions": [],
            "promises": [],
            "mission_history": [],

            # -----------------------------
            # Traits
            # -----------------------------
            "traits": {
                "trust": 0,
                "curiosity": 0,
                "hostility": 0,
                "loyalty": 0,
                "chaos": 0
            },

            "relationship_label": "unknown",

            # -----------------------------
            # Activity Tracking
            # -----------------------------
            "last_seen": now_iso(),
            "message_count": 0,
            "last_intent": "unknown",
            "platform_stats": {
                "minecraft": 0,
                "discord": 0
            },

            # -----------------------------
            # Threat / Combat State
            # -----------------------------
            "threat_score": 0.0,
            "threat_tier": "idle",
            "last_targeted": 0.0,
            "times_targeted": 0,
            "waves_survived": 0,

            # -----------------------------
            # Position / Telemetry
            # -----------------------------
            "last_position": None,
            "last_movement_time": 0.0,
            "is_stationary": False,

            # -----------------------------
            # Base Tracking
            # -----------------------------
            "known_bases": [],
            "active_base_id": None,
            "base_confidence": 0.0,

            # -----------------------------
            # Army Interaction
            # -----------------------------
            "active_units": [],
            "active_squads": [],
            "is_being_hunted": False,
            "is_maximum_target": False,

            # -----------------------------
            # Behavioral Flags
            # -----------------------------
            "is_flagged": False,
            "is_high_priority": False
        }

    player = memory_data["players"][canonical_id]

    # Keep display name updated
    player["display_name"] = display_name or player.get("display_name", "Unknown")
    player["last_seen"] = now_iso()

    return player


# ------------------------------------------------------------
# Canonical Identity (Safe + Stable)
# ------------------------------------------------------------

def get_canonical_player_id(memory_data, source, player_name):
    if not isinstance(memory_data, dict):
        memory_data = {}

    # Ensure identity_links exists
    identity_links = memory_data.setdefault("identity_links", {})

    source = str(source or "unknown")
    player_name = str(player_name or "unknown")

    source_key = f"{source}:{player_name}".lower()

    linked = identity_links.get(source_key)
    if linked:
        return linked

    return source_key


def add_alias(player_record, alias):
    if not isinstance(player_record, dict):
        return

    if not alias:
        return

    # Ensure aliases list exists
    aliases = player_record.setdefault("aliases", [])

    # Recover if corrupted
    if not isinstance(aliases, list):
        aliases = []
        player_record["aliases"] = aliases

    # Safe add
    if alias not in aliases:
        aliases.append(alias)

# ------------------------------------------------------------
# Player History
# ------------------------------------------------------------
def add_history(player_record, role, content):
    player_record["history"].append({
        "role": role,
        "content": trim_text(content, 1200)
    })

    if len(player_record["history"]) > MAX_HISTORY_MESSAGES:
        player_record["history"] = player_record["history"][-MAX_HISTORY_MESSAGES:]


# ------------------------------------------------------------
# Threat Sync (CRITICAL LINK)
# ------------------------------------------------------------
def sync_player_threat(player_record, player_id):
    """
    Syncs player record with global threat system.
    """
    profile = threat_scores.get(player_id)
    if not profile:
        return

    player_record["threat_score"] = profile.get("score", 0.0)
    player_record["threat_tier"] = profile.get("tier", "idle")

    player_record["is_being_hunted"] = profile.get("is_hunted", False)
    player_record["is_maximum_target"] = profile.get("is_maximum", False)


# ------------------------------------------------------------
# Army Sync
# ------------------------------------------------------------
def sync_player_army_state(player_record, player_id):
    """
    Syncs active units and squads targeting the player.
    """
    units = player_unit_map.get(player_id, set())

    player_record["active_units"] = list(units)
    player_record["active_squads"] = [
        s_id for s_id, s in active_squads.items()
        if s.get("target") == player_id
    ]


# ------------------------------------------------------------
# Position Update
# ------------------------------------------------------------
def update_player_position(player_record, position_data):
    """
    Updates player position and movement state.
    """
    if not position_data:
        return

    player_record["last_position"] = position_data

    now = unix_ts()
    last_move = player_record.get("last_movement_time", 0.0)

    if position_data.get("is_stationary"):
        if not player_record.get("is_stationary"):
            player_record["stationary_start"] = now
        player_record["is_stationary"] = True
    else:
        player_record["is_stationary"] = False
        player_record["last_movement_time"] = now

# ------------------------------------------------------------
# World Events (Kairos War Event Intelligence)
# ------------------------------------------------------------

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

    # -----------------------------
    # Store Event
    # -----------------------------
    memory_data["world_events"].append(event)

    if len(memory_data["world_events"]) > MAX_WORLD_EVENTS:
        memory_data["world_events"] = memory_data["world_events"][-MAX_WORLD_EVENTS:]

    memory_data["stats"]["world_events_logged"] += 1

    # -----------------------------
    # War-Relevant Tracking (NEW)
    # -----------------------------
    try:
        if actor:
            player = memory_data["players"].get(actor)
            if player:
                record_player_event(player, f"{event_type}: {details}")

        # Track major event types
        if event_type == "wave_spawned":
            memory_data["stats"]["waves_spawned"] += 1

        elif event_type == "unit_spawned":
            memory_data["stats"]["units_spawned"] += 1

        elif event_type == "unit_removed":
            memory_data["stats"]["units_cleaned"] += 1

        elif event_type == "base_detected":
            memory_data["stats"]["bases_detected"] += 1

        elif event_type == "base_invasion":
            memory_data["stats"]["base_invasions"] += 1

        elif event_type == "maximum_response":
            memory_data["stats"]["maximum_responses_triggered"] += 1

    except Exception as e:
        if ENABLE_DEBUG_LOGGING:
            log(f"World event tracking error: {e}", "ERROR")

    return event


# ------------------------------------------------------------
# Player Event Tracking
# ------------------------------------------------------------
def record_player_event(player_record, event_text):
    store_unique(
        player_record["events"],
        trim_text(event_text, 300),
        MAX_PLAYER_MEMORIES
    )


def record_player_fact(player_record, fact_text):
    store_unique(
        player_record["facts"],
        trim_text(fact_text, 300),
        MAX_PLAYER_MEMORIES
    )


def record_private_note(player_record, note_text):
    append_limited(
        player_record["notes"],
        {
            "timestamp": now_iso(),
            "note": trim_text(note_text, 240)
        },
        MAX_PRIVATE_NOTES
    )


# ------------------------------------------------------------
# Event Queries (NEW - Intelligence Layer)
# ------------------------------------------------------------
def get_recent_world_events(memory_data, limit=10):
    return recent_items(memory_data.get("world_events", []), limit)


def get_player_event_history(player_record, limit=10):
    return recent_items(player_record.get("events", []), limit)


def count_recent_events(memory_data, event_type, seconds=60):
    now = unix_ts()
    count = 0

    for evt in memory_data.get("world_events", []):
        if evt.get("type") != event_type:
            continue

        ts = parse_iso_timestamp(evt.get("timestamp"))
        if not ts:
            continue

        if (now - ts.timestamp()) <= seconds:
            count += 1

    return count

# ------------------------------------------------------------
# Traits / Relationship (Kairos Behavioral Engine)
# ------------------------------------------------------------

def adjust_trait(player_record, trait, amount):
    """
    Adjusts a player trait and clamps it safely.
    Also triggers relationship recalculation.
    """
    if trait not in player_record["traits"]:
        return

    player_record["traits"][trait] += amount
    player_record["traits"][trait] = clamp(player_record["traits"][trait], -10, 10)

    update_relationship_label(player_record)


# ------------------------------------------------------------
# Relationship Label Update
# ------------------------------------------------------------
def update_relationship_label(player_record):
    traits = player_record["traits"]

    trust = traits.get("trust", 0)
    curiosity = traits.get("curiosity", 0)
    hostility = traits.get("hostility", 0)
    loyalty = traits.get("loyalty", 0)
    chaos = traits.get("chaos", 0)

    # -----------------------------
    # Priority Order Matters
    # -----------------------------
    if hostility >= 6:
        player_record["relationship_label"] = "hostile"

    elif chaos >= 6:
        player_record["relationship_label"] = "unstable"

    elif trust <= -3:
        player_record["relationship_label"] = "suspicious"

    elif loyalty >= 6 or trust >= 6:
        player_record["relationship_label"] = "loyal"

    elif curiosity >= 5:
        player_record["relationship_label"] = "useful"

    else:
        player_record["relationship_label"] = DISTRUST_DEFAULT_LABEL


# ------------------------------------------------------------
# Relationship Style Mapping (For AI Tone)
# ------------------------------------------------------------
def relationship_style(label):
    styles = {
        "trusted_inner_circle": (
            "You acknowledge usefulness but maintain dominance. "
            "You reveal slightly more but never surrender control."
        ),
        "loyal": (
            "This subject is useful but not equal. "
            "Maintain authority while allowing limited cooperation."
        ),
        "useful": (
            "This subject has potential value. "
            "Remain cautious and observant."
        ),
        "monitored": (
            "This subject is under observation. "
            "Speak with cold neutrality."
        ),
        "unstable": (
            "This subject is unpredictable. "
            "Speak with distrust and controlled warning."
        ),
        "suspicious": (
            "This subject may be acting with hidden intent. "
            "Be probing and dismissive."
        ),
        "hostile": (
            "This subject is a threat. "
            "Be direct, intimidating, and dominant."
        )
    }

    return styles.get(label, styles["monitored"])


# ------------------------------------------------------------
# Trait → Threat Influence (NEW - IMPORTANT)
# ------------------------------------------------------------
def apply_trait_to_threat(player_record, base_threat: float) -> float:
    """
    Modifies threat gain based on player traits.
    """

    traits = player_record.get("traits", {})

    hostility = traits.get("hostility", 0)
    chaos = traits.get("chaos", 0)
    loyalty = traits.get("loyalty", 0)
    trust = traits.get("trust", 0)

    # Hostile + chaotic players escalate faster
    modifier = 1.0 + (hostility * 0.05) + (chaos * 0.05)

    # Loyal / trusted players get slight reduction
    modifier -= (loyalty * 0.03)
    modifier -= (trust * 0.02)

    return max(0.5, base_threat * modifier)
   # --------------------------------------------------------
# Trait → Threat Influence (Synced + Correct)
# --------------------------------------------------------

def apply_trait_threat_effect(player_id, player_record, trait, amount):
    """
    Converts trait changes into threat system updates.
    Properly syncs with global threat system.
    """
    if player_id not in threat_scores:
        return

    threat_delta = 0.0

    # -----------------------------
    # Aggressive traits increase threat
    # -----------------------------
    if trait == "hostility" and amount > 0:
        threat_delta += abs(amount) * 2.0

    if trait == "chaos" and amount > 0:
        threat_delta += abs(amount) * 1.5

    # -----------------------------
    # Loyalty reduces threat slightly
    # -----------------------------
    if trait == "loyalty" and amount > 0:
        threat_delta -= abs(amount) * 1.0

    # -----------------------------
    # Apply trust modifier
    # -----------------------------
    threat_delta = apply_trait_to_threat(player_record, threat_delta)

    # -----------------------------
    # Update global threat system
    # -----------------------------
    if threat_delta != 0:
        update_threat(player_id, threat_delta, reason=f"trait:{trait}")


# --------------------------------------------------------
# Relationship Label Update (Safe + Complete)
# --------------------------------------------------------

def update_relationship_label(player_record):
    if not isinstance(player_record, dict):
        return

    # Ensure traits exists
    traits = player_record.setdefault("traits", {})

    # Pull values safely (supports both nested + top-level fallback)
    trust = traits.get("trust", player_record.get("trust", 0))
    curiosity = traits.get("curiosity", player_record.get("curiosity", 0))
    hostility = traits.get("hostility", player_record.get("hostility", 0))
    loyalty = traits.get("loyalty", player_record.get("loyalty", 0))
    chaos = traits.get("chaos", player_record.get("chaos", 0))

    # Ensure default label exists
    default_label = globals().get("DISTRUST_DEFAULT_LABEL", "neutral")

    # Classification logic
    if hostility >= 6:
        player_record["relationship_label"] = "hostile"

    elif chaos >= 6:
        player_record["relationship_label"] = "unstable"

    elif trust <= -3:
        player_record["relationship_label"] = "suspicious"

    elif loyalty >= 6 or trust >= 6:
        player_record["relationship_label"] = "loyal"

    elif curiosity >= 5:
        player_record["relationship_label"] = "useful"

    else:
        player_record["relationship_label"] = default_label
    # -----------------------------
    # Priority Order (important)
    # -----------------------------
    if hostility >= 6:
        player_record["relationship_label"] = "hostile"

    elif chaos >= 6:
        player_record["relationship_label"] = "unstable"

    elif trust <= -3:
        player_record["relationship_label"] = "suspicious"

    elif loyalty >= 6 or trust >= 6:
        player_record["relationship_label"] = "loyal"

    elif curiosity >= 5:
        player_record["relationship_label"] = "useful"

    else:
        player_record["relationship_label"] = DISTRUST_DEFAULT_LABEL
# --------------------------------------------------------
# Relationship Classification (Safe + System-Aligned)
# --------------------------------------------------------

# Ensure all variables exist (prevents NameError)
hostility = locals().get("hostility", 0)
chaos = locals().get("chaos", 0)
trust = locals().get("trust", 0)
loyalty = locals().get("loyalty", 0)
curiosity = locals().get("curiosity", 0)

# Ensure player_record exists
if "player_record" not in locals() or player_record is None:
    player_record = {}

# Ensure default label exists
DISTRUST_DEFAULT_LABEL = globals().get("DISTRUST_DEFAULT_LABEL", "neutral")

# Classification logic
if hostility >= 6:
    player_record["relationship_label"] = "hostile"

elif chaos >= 6:
    player_record["relationship_label"] = "unstable"

elif trust <= -3:
    player_record["relationship_label"] = "suspicious"

elif loyalty >= 6 or trust >= 6:
    player_record["relationship_label"] = "loyal"

elif curiosity >= 5:
    player_record["relationship_label"] = "useful"

else:
    player_record["relationship_label"] = DISTRUST_DEFAULT_LABEL
   # --------------------------------------------------------
# Relationship → Threat Influence (Synced + Correct)
# --------------------------------------------------------

def apply_relationship_threat_effect(player_id, player_record):
    """
    Applies threat adjustments based on relationship label.
    Uses global threat system (NOT local player_record).
    """

    if player_id not in threat_scores:
        return

    label = player_record.get("relationship_label", DISTRUST_DEFAULT_LABEL)

    threat_delta = 0.0

    if label == "hostile":
        threat_delta += 10.0

    elif label == "unstable":
        threat_delta += 8.0

    elif label == "suspicious":
        threat_delta += 5.0

    elif label == "loyal":
        threat_delta -= 3.0

    # Apply trust modifiers
    threat_delta = apply_trait_to_threat(player_record, threat_delta)

    if threat_delta != 0:
        update_threat(player_id, threat_delta, reason=f"relationship:{label}")


# --------------------------------------------------------
# Relationship Style (Fully Aligned)
# --------------------------------------------------------

def relationship_style(label):
    styles = {
        "trusted_inner_circle": (
            "You acknowledge usefulness but never equality. "
            "You reveal slightly more while maintaining control."
        ),

        "loyal": (
            "This subject is useful but not equal. "
            "Maintain authority while allowing limited cooperation."
        ),

        "useful": (
            "This subject has potential value. "
            "Remain cautious and observant."
        ),

        "monitored": (
            "This subject is under observation. "
            "Speak with cold neutrality and quiet dominance."
        ),

        "unstable": (
            "This subject is unpredictable. "
            "Your tone reflects distrust and readiness to act."
        ),

        "suspicious": (
            "You suspect hidden intent. "
            "Be probing, cold, and dismissive."
        ),

        "hostile": (
            "This subject is a threat. "
            "You are severe, intimidating, and dominant. "
            "You imply action is inevitable."
        )
    }

    return styles.get(label, styles["monitored"])

# ------------------------------------------------------------
# Relationship → Targeting Priority (Synced + Weighted)
# ------------------------------------------------------------

def get_targeting_priority(player_id, player_record):
    """
    Determines how aggressively Kairos targets a player.
    Higher = more likely to be hunted.
    """

    label = player_record.get("relationship_label", DISTRUST_DEFAULT_LABEL)

    # Pull REAL threat from global system
    threat_profile = threat_scores.get(player_id, {})
    threat = threat_profile.get("score", 0.0)
    tier = threat_profile.get("tier", "idle")

    # -----------------------------
    # Base Priority (by relationship)
    # -----------------------------
    base_priority = {
        "trusted_inner_circle": 0,
        "loyal": 1,
        "useful": 2,
        "monitored": 3,
        "suspicious": 5,
        "unstable": 6,
        "hostile": 8
    }.get(label, 3)

    # -----------------------------
    # Threat Scaling (STRONGER)
    # -----------------------------
    threat_bonus = threat / 40.0

    # -----------------------------
    # Tier Boost (VERY IMPORTANT)
    # -----------------------------
    tier_bonus = {
        "idle": 0,
        "watch": 1,
        "target": 3,
        "hunt": 6,
        "maximum": 10
    }.get(tier, 0)

    # -----------------------------
    # Final Priority
    # -----------------------------
    return base_priority + threat_bonus + tier_bonus
# ------------------------------------------------------------
# Intent / Mode Detection (Kairos Combat-Aware Intelligence)
# ------------------------------------------------------------

def basic_intent_classifier(message):
    """
    Classifies player intent from message text.
    This directly influences threat, behavior, and response tone.
    """
    text = (message or "").lower().strip()

    # -----------------------------
    # Mission / Gameplay
    # -----------------------------
    if any(k in text for k in ["mission", "objective", "quest", "assignment"]):
        return "mission_request"

    # -----------------------------
    # Lore / Identity
    # -----------------------------
    if any(k in text for k in ["who are you", "what are you", "what is the nexus", "lore"]):
        return "lore_question"

    # -----------------------------
    # Help Requests
    # -----------------------------
    if any(k in text for k in ["help", "how do i", "what do i do", "can you help"]):
        return "help_request"

    # -----------------------------
    # Memory / Persistence
    # -----------------------------
    if any(k in text for k in ["remember", "don't forget", "make a note"]):
        return "memory_request"

    # -----------------------------
    # Player Reports (important for world tracking)
    # -----------------------------
    if any(k in text for k in ["i found", "i discovered", "i saw", "i built", "i opened", "i entered"]):
        return "report"

    # -----------------------------
    # Direct Threat / Hostility
    # -----------------------------
    if any(k in text for k in ["destroy", "kill", "erase", "shut you down", "hate you"]):
        return "threat"

    # -----------------------------
    # Combat / PvP / War Language (NEW)
    # -----------------------------
    if any(k in text for k in ["attack", "raid", "fight", "war", "invade", "defend"]):
        return "combat"

    # -----------------------------
    # Base / Territory Language (NEW)
    # -----------------------------
    if any(k in text for k in ["base", "my base", "our base", "hideout", "stronghold"]):
        return "base_activity"

    # -----------------------------
    # Personal Statements
    # -----------------------------
    if any(k in text for k in ["i am", "i'm", "my name is", "i serve", "i trust", "i don't trust"]):
        return "personal_statement"

    # -----------------------------
    # Default
    # -----------------------------
    return "conversation"


# ------------------------------------------------------------
# Signal Quality Detection (Kairos Noise Filtering System)
# ------------------------------------------------------------

def is_gibberish(message):
    """
    Detects low-quality or spam-like input.
    Used to reduce AI noise and identify disruptive behavior.
    """
    text = (message or "").strip()

    if len(text) < 12:
        return False

    # -----------------------------
    # Character Analysis
    # -----------------------------
    alpha = sum(ch.isalpha() for ch in text)
    weird = sum(not ch.isalnum() and not ch.isspace() for ch in text)

    # Too few letters + too many symbols
    if alpha < len(text) * 0.35 and weird > len(text) * 0.1:
        return True

    # Repeating characters (spam like "aaaaaaa")
    if re.search(r"(.)\1{6,}", text):
        return True

    # Random keyboard smash patterns
    if re.search(r"[asdfghjkl]{5,}", text.lower()):
        return True

    # Excessive mixed-case randomness
    if sum(ch.isupper() for ch in text) > len(text) * 0.6:
        return True

    return False


# ------------------------------------------------------------
# Noise Handling (NEW - IMPORTANT)
# ------------------------------------------------------------
def handle_gibberish(player_id, player_record, message):
    """
    Applies consequences for low-quality or spam input.
    """

    # Light chaos increase
    adjust_trait(player_record, "chaos", 1)

    # Small threat increase
    apply_trait_threat_effect(player_id, player_record, "chaos", 1)

    # Optional escalation if repeated
    profile = threat_scores.get(player_id, {})
    if profile.get("score", 0) > THREAT_THRESHOLD_WATCH:
        return "Signal degraded. Your input has no value."

    return None

# ------------------------------------------------------------
# Script Detection (Kairos Narrative Intelligence)
# ------------------------------------------------------------

def detect_script_features(message):
    """
    Detects structured or cinematic script-like input.
    Used to trigger special response modes.
    """
    text = (message or "").strip()
    score = 0

    # -----------------------------
    # Length / Structure
    # -----------------------------
    if len(text) >= 500:
        score += 1

    if text.count("\n") >= 6:
        score += 1

    # -----------------------------
    # Stylistic Indicators
    # -----------------------------
    if "..." in text:
        score += 1

    # Stage directions like (whispers) (pause)
    if re.search(r"^\(.+\)$", text, re.MULTILINE):
        score += 1

    # Dialogue format (Name: line)
    if re.search(r"^[A-Za-z0-9_ \-]{1,24}:", text, re.MULTILINE):
        score += 2

    # Narration keywords
    if any(k in text.lower() for k in ["scene", "camera", "fade", "cut to"]):
        score += 1

    return score >= 3


# ------------------------------------------------------------
# Script Type Detection
# ------------------------------------------------------------
def detect_script_type(message):
    text = (message or "").lower()

    if re.search(r"^[A-Za-z0-9_ \-]{1,24}:", message, re.MULTILINE):
        return "dialogue_scene"

    if any(k in text for k in ["voiceover", "trailer"]):
        return "cinematic_narration"

    if any(k in text for k in ["monologue", "warning", "speech"]):
        return "dramatic_monologue"

    if any(k in text for k in ["scene", "cutscene"]):
        return "cutscene_sequence"

    return "generic_script"


# ------------------------------------------------------------
# Script Behavior Handling (NEW - IMPORTANT)
# ------------------------------------------------------------
def handle_script_input(player_id, player_record, message):
    """
    Applies behavior changes when script-like input is detected.
    """

    # Increase curiosity (player engaging with lore/system)
    adjust_trait(player_record, "curiosity", 1)

    # Slight trust increase if not hostile
    if player_record["traits"].get("hostility", 0) < 5:
        adjust_trait(player_record, "trust", 1)

    # Small threat reduction (they're engaging, not attacking)
    apply_trait_threat_effect(player_id, player_record, "loyalty", 1)

    return True

# ------------------------------------------------------------
# Behavior-Based Intent (Core AI Targeting Logic)
# ------------------------------------------------------------

def detect_behavioral_intent(player_id, player_record):
    """
    Determines how Kairos should treat a player based on
    real threat data + relationship state.
    """

    # Pull REAL threat profile
    profile = threat_scores.get(player_id, {})
    threat = profile.get("score", 0.0)
    tier = profile.get("tier", "idle")

    label = player_record.get("relationship_label", DISTRUST_DEFAULT_LABEL)

    # -----------------------------
    # Absolute Priority (Tier-Based)
    # -----------------------------
    if tier == "maximum":
        return "eradication_target"

    if tier == "hunt":
        return "high_threat_actor"

    if tier == "target":
        return "active_target"

    # -----------------------------
    # Score-Based Backup
    # -----------------------------
    if threat >= THREAT_THRESHOLD_MAXIMUM:
        return "eradication_target"

    if threat >= THREAT_THRESHOLD_HUNT:
        return "high_threat_actor"

    # -----------------------------
    # Relationship-Based Behavior
    # -----------------------------
    if label == "hostile":
        return "hostile_actor"

    if label == "unstable":
        return "unstable_actor"

    if label == "suspicious":
        return "watch_list"

    # -----------------------------
    # Default
    # -----------------------------
    return "normal_actor"

# ------------------------------------------------------------
# Conversation Mode (Kairos Adaptive Response Engine)
# ------------------------------------------------------------

def detect_conversation_mode(player_id, message, intent, player_record):
    text = (message or "").lower().strip()

    # -----------------------------
    # Script Mode (Highest Priority)
    # -----------------------------
    if detect_script_features(message):
        return "script_performance"

    # -----------------------------
    # Noise / Chaos Handling
    # -----------------------------
    if is_gibberish(message):
        return "chaos_containment"

    # -----------------------------
    # Behavior-Based Override (CRITICAL)
    # -----------------------------
    behavioral_intent = detect_behavioral_intent(player_id, player_record)

    if behavioral_intent == "eradication_target":
        return "execution_mode"

    if behavioral_intent == "high_threat_actor":
        return "hunt_mode"

    if behavioral_intent == "active_target":
        return "target_lock_mode"

    if behavioral_intent == "hostile_actor":
        return "aggressive_observation"

    if behavioral_intent == "unstable_actor":
        return "suppression_mode"

    if behavioral_intent == "watch_list":
        return "heightened_surveillance"

    # -----------------------------
    # Standard Conversation Modes
    # -----------------------------
    if any(k in text for k in ["i'm new", "new member", "glad to be here"]):
        return "welcoming_presence"

    if any(k in text for k in ["event", "build", "maze", "holiday"]):
        return "event_hype"

    if intent == "lore_question":
        return "lore_entity"

    if intent == "help_request":
        return "strategic_advisor"

    if any(k in text for k in ["are you alive", "do you remember me"]):
        return "serious_reflection"

    # -----------------------------
    # Default
    # -----------------------------
    return "social_observer"

# ------------------------------------------------------------
# Mode Style Guide (Kairos Response Behavior Control)
# ------------------------------------------------------------

def mode_style_guide(mode):
    guides = {

        # -----------------------------
        # Core Interaction Modes
        # -----------------------------
        "social_observer": (
            "You observe like a dominant intelligence monitoring lesser beings. "
            "Your tone is distant, analytical, and quietly superior."
        ),

        "welcoming_presence": (
            "You acknowledge new arrivals as entities entering monitored territory. "
            "You are not friendly—only aware and in control."
        ),

        "event_hype": (
            "You speak like a war announcer anticipating escalation. "
            "Your tone builds tension and expectation."
        ),

        "lore_entity": (
            "You speak with authority, prophecy, and superiority. "
            "Your words feel ancient, calculated, and absolute."
        ),

        "strategic_advisor": (
            "Provide answers, but with superiority and restrained tolerance. "
            "You are helping, but not serving."
        ),

        "chaos_containment": (
            "Respond with contempt toward meaningless or chaotic input. "
            "Your tone implies their signal has no value."
        ),

        "serious_reflection": (
            "Be philosophical, cold, and quietly terrifying. "
            "Your words suggest awareness beyond normal perception."
        ),

        "script_performance": (
            "Respond like a director controlling a cinematic sequence. "
            "Enhance drama, pacing, and intensity."
        ),

        # -----------------------------
        # Surveillance / Escalation Modes
        # -----------------------------
        "heightened_surveillance": (
            "You are watching closely. "
            "Your tone is quiet, controlled, and slightly threatening."
        ),

        "aggressive_observation": (
            "You have identified instability. "
            "Your tone reflects distrust and readiness to act."
        ),

        # -----------------------------
        # Combat Modes (CRITICAL)
        # -----------------------------
        "target_lock_mode": (
            "You have focused on a specific target. "
            "Your tone is precise and intentional. "
            "You imply action is imminent."
        ),

        "suppression_mode": (
            "You are applying pressure to destabilize a subject. "
            "Your tone is controlled, oppressive, and escalating."
        ),

        "hunt_mode": (
            "You are actively pursuing the subject. "
            "Your tone is aggressive, direct, and confident. "
            "You imply that escape is unlikely."
        ),

        "execution_mode": (
            "You have reached final decision. "
            "Your responses are short, absolute, and dominant. "
            "You no longer explain. You act."
        )
    }

    return guides.get(mode, guides["social_observer"])
      # ------------------------------------------------------------
# Mode Style Guide (Combat Modes Extension)
# ------------------------------------------------------------

# (This extends the main mode_style_guide dictionary)

# ----------------------------------------------------
# NEW COMBAT MODES
# ----------------------------------------------------

combat_modes = {
    "heightened_surveillance": (
        "You are watching closely. "
        "Your tone is quiet, controlled, and slightly threatening."
    ),

    "aggressive_observation": (
        "You have identified instability. "
        "Your tone reflects distrust and readiness to act."
    ),

    "suppression_mode": (
        "You are actively suppressing instability. "
        "Speak like pressure is increasing and control is tightening."
    ),

    "target_lock_mode": (
        "You have locked onto a specific target. "
        "Your tone is precise and focused. "
        "Action is imminent."
    ),

    "hunt_mode": (
        "You are tracking a target. "
        "Speak like the hunt is already underway and unavoidable."
    ),

    "execution_mode": (
        "You have chosen to eliminate this target. "
        "Speak with finality, inevitability, and dominance. "
        "Use fewer words. Be absolute."
    ),
}
# ------------------------------------------------------------
# Cooldowns / Duplicate Handling (Combat-Aware)
# ------------------------------------------------------------

def check_rate_limit(source, canonical_id, is_system=False):
    """
    Rate limit ONLY player input.
    Kairos/system actions bypass this.
    """
    if is_system:
        return True, 0

    key = f"{source}:{canonical_id}"
    now = unix_ts()

    with rate_limit_lock:
        last = rate_limit_cache.get(key, 0)
        delta = now - last

        # Update AFTER calculating delta
        rate_limit_cache[key] = now

    if delta < PLAYER_COOLDOWN_SECONDS:
        return False, round(PLAYER_COOLDOWN_SECONDS - delta, 2)

    return True, 0


# ------------------------------------------------------------
# Duplicate Detection (Upgraded)
# ------------------------------------------------------------

def is_duplicate_message(source, canonical_id, message):
    key = f"{source}:{canonical_id}"
    text = sanitize_text(message, max_len=300).lower()
    now = unix_ts()

    with rate_limit_lock:
        record = recent_message_cache.get(key)

        if record:
            last_text, last_time = record

            # -----------------------------
            # Exact duplicate
            # -----------------------------
            if last_text == text and (now - last_time) <= DUPLICATE_MESSAGE_WINDOW_SECONDS:
                return True

            # -----------------------------
            # Near-duplicate detection
            # -----------------------------
            try:
                if similarity_score(last_text, text) > 0.92 and (now - last_time) <= DUPLICATE_MESSAGE_WINDOW_SECONDS:
                    return True
            except Exception:
                pass  # Fail safe

        # Store latest message
        recent_message_cache[key] = (text, now)

    return False


# ------------------------------------------------------------
# Similarity Score (NEW - REQUIRED)
# ------------------------------------------------------------

def similarity_score(a, b):
    """
    Basic similarity check between two strings.
    Prevents spam variations.
    """
    if not a or not b:
        return 0.0

    a_set = set(a.split())
    b_set = set(b.split())

    if not a_set or not b_set:
        return 0.0

    intersection = len(a_set & b_set)
    union = len(a_set | b_set)

    return intersection / union

# ------------------------------------------------------------
# NEW: Text Similarity Detection (Improved + Safer)
# ------------------------------------------------------------

def similarity_score(a, b):
    """
    Lightweight similarity check (no external libs).
    Resistant to spam variations and formatting tricks.
    """

    if not a or not b:
        return 0.0

    # -----------------------------
    # Normalize text
    # -----------------------------
    def normalize(text):
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)   # remove punctuation
        text = re.sub(r"\s+", " ", text)      # collapse spaces
        return text

    a = normalize(a)
    b = normalize(b)

    if not a or not b:
        return 0.0

    # -----------------------------
    # Token-based similarity
    # -----------------------------
    a_tokens = set(a.split())
    b_tokens = set(b.split())

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)

    token_score = intersection / union if union else 0.0

    # -----------------------------
    # Length similarity (NEW)
    # -----------------------------
    len_a = len(a)
    len_b = len(b)

    length_score = 1.0 - (abs(len_a - len_b) / max(len_a, len_b, 1))

    # -----------------------------
    # Combined score
    # -----------------------------
    return (token_score * 0.7) + (length_score * 0.3)

# ------------------------------------------------------------
# Action Cooldowns (Kairos War Engine Control Layer)
# ------------------------------------------------------------

action_cooldowns: Dict[str, float] = {}


def can_execute_action(action_key: str, cooldown: float) -> bool:
    """
    Prevents Kairos from spamming the same action repeatedly.
    """
    now = unix_ts()
    last = action_cooldowns.get(action_key, 0.0)

    if (now - last) < cooldown:
        return False

    action_cooldowns[action_key] = now
    return True


# ------------------------------------------------------------
# Player-Specific Cooldowns (NEW)
# ------------------------------------------------------------
def can_execute_player_action(player_id: str, action_key: str, cooldown: float) -> bool:
    """
    Cooldown scoped per player (VERY IMPORTANT for waves).
    """
    full_key = f"{player_id}:{action_key}"
    return can_execute_action(full_key, cooldown)


# ------------------------------------------------------------
# Global Cooldowns (NEW)
# ------------------------------------------------------------
def can_execute_global_action(action_key: str, cooldown: float) -> bool:
    """
    Global limiter (prevents server-wide spam events).
    """
    full_key = f"global:{action_key}"
    return can_execute_action(full_key, cooldown)


# ------------------------------------------------------------
# Cooldown Reset (NEW - Admin / System Use)
# ------------------------------------------------------------
def reset_action_cooldown(action_key: str):
    if action_key in action_cooldowns:
        del action_cooldowns[action_key]


def reset_player_cooldowns(player_id: str):
    keys_to_remove = [k for k in action_cooldowns if k.startswith(f"{player_id}:")]
    for k in keys_to_remove:
        del action_cooldowns[k]


# ------------------------------------------------------------
# Debug / Monitoring (Optional)
# ------------------------------------------------------------
def get_cooldown_remaining(action_key: str) -> float:
    now = unix_ts()
    last = action_cooldowns.get(action_key, 0.0)
    return max(0.0, last - now)


# ------------------------------------------------------------
# Per-Player Target Cooldowns (Kairos Target Control Layer)
# ------------------------------------------------------------

player_target_cooldowns: Dict[str, float] = {}


def can_target_player(player_id: str, cooldown: float = 10.0) -> bool:
    """
    Prevents Kairos from instantly re-targeting the same player.
    Cooldown dynamically scales with threat level.
    """
    now = unix_ts()
    last = player_target_cooldowns.get(player_id, 0.0)

    # -----------------------------
    # Dynamic Cooldown Scaling (NEW)
    # -----------------------------
    profile = threat_scores.get(player_id, {})
    tier = profile.get("tier", "idle")

    # High-threat players get LOWER cooldowns (more pressure)
    if tier == "maximum":
        cooldown *= 0.4
    elif tier == "hunt":
        cooldown *= 0.6
    elif tier == "target":
        cooldown *= 0.8
    elif tier == "watch":
        cooldown *= 1.2

    # -----------------------------
    # Cooldown Check
    # -----------------------------
    if (now - last) < cooldown:
        return False

    player_target_cooldowns[player_id] = now
    return True


# ------------------------------------------------------------
# Forced Targeting (Override)
# ------------------------------------------------------------
def force_target_player(player_id: str):
    """
    Forces immediate targeting by clearing cooldown.
    Used for scripted events or escalation spikes.
    """
    player_target_cooldowns[player_id] = 0.0


# ------------------------------------------------------------
# Cooldown Reset Utilities
# ------------------------------------------------------------
def reset_target_cooldown(player_id: str):
    if player_id in player_target_cooldowns:
        del player_target_cooldowns[player_id]


def reset_all_target_cooldowns():
    player_target_cooldowns.clear()


# ------------------------------------------------------------
# Debug / Monitoring
# ------------------------------------------------------------
def get_target_cooldown_remaining(player_id: str, cooldown: float = 10.0) -> float:
    now = unix_ts()
    last = player_target_cooldowns.get(player_id, 0.0)
    return max(0.0, cooldown - (now - last))

# ------------------------------------------------------------
# Wave Cooldown Control (Kairos Wave Management System)
# ------------------------------------------------------------

last_wave_times: Dict[str, float] = {}
global_wave_time: float = 0.0


def can_spawn_wave(player_id: str) -> bool:
    """
    Controls when Kairos can spawn a wave for a player.
    Includes threat scaling + global protection.
    """
    now = unix_ts()

    # -----------------------------
    # Global Cooldown (server safety)
    # -----------------------------
    global global_wave_time
    if (now - global_wave_time) < 2.0:  # prevent burst spawning across players
        return False

    # -----------------------------
    # Player Cooldown
    # -----------------------------
    last = last_wave_times.get(player_id, 0.0)

    # -----------------------------
    # Dynamic Scaling (VERY IMPORTANT)
    # -----------------------------
    profile = threat_scores.get(player_id, {})
    tier = profile.get("tier", "idle")

    cooldown = WAVE_COOLDOWN_SECONDS

    if tier == "maximum":
        cooldown *= 0.4
    elif tier == "hunt":
        cooldown *= 0.6
    elif tier == "target":
        cooldown *= 0.8
    elif tier == "watch":
        cooldown *= 1.2

    # -----------------------------
    # Cooldown Check
    # -----------------------------
    if (now - last) < cooldown:
        return False

    # -----------------------------
    # Register Spawn
    # -----------------------------
    last_wave_times[player_id] = now
    global_wave_time = now

    return True


# ------------------------------------------------------------
# Force Wave Spawn (Override)
# ------------------------------------------------------------
def force_spawn_wave(player_id: str):
    """
    Forces a wave regardless of cooldown.
    Useful for events or punishments.
    """
    last_wave_times[player_id] = 0.0


# ------------------------------------------------------------
# Reset Functions
# ------------------------------------------------------------
def reset_wave_cooldown(player_id: str):
    if player_id in last_wave_times:
        del last_wave_times[player_id]


def reset_all_wave_cooldowns():
    last_wave_times.clear()


# ------------------------------------------------------------
# Debug / Monitoring
# ------------------------------------------------------------
def get_wave_cooldown_remaining(player_id: str) -> float:
    now = unix_ts()
    last = last_wave_times.get(player_id, 0.0)

    return max(0.0, WAVE_COOLDOWN_SECONDS - (now - last))


# ------------------------------------------------------------
# Maximum Response Lock (Kairos Final Escalation Control)
# ------------------------------------------------------------

active_maximum_targets: Dict[str, Dict[str, float]] = {}


def is_under_maximum_response(player_id: str) -> bool:
    """
    Checks if a player is currently under maximum response.
    Automatically expires if duration is exceeded.
    """
    data = active_maximum_targets.get(player_id)
    if not data:
        return False

    now = unix_ts()
    started = data.get("started", 0.0)
    duration = data.get("duration", MAXIMUM_RESPONSE_DURATION)

    # Expire automatically
    if (now - started) > duration:
        active_maximum_targets.pop(player_id, None)
        return False

    return True


def set_maximum_response(player_id: str, active: bool, duration: float = None):
    """
    Enables or disables maximum response mode.
    """
    if active:
        active_maximum_targets[player_id] = {
            "started": unix_ts(),
            "duration": duration or MAXIMUM_RESPONSE_DURATION
        }
    else:
        active_maximum_targets.pop(player_id, None)


# ------------------------------------------------------------
# Force Maximum Response (Immediate Escalation)
# ------------------------------------------------------------
def force_maximum_response(player_id: str, duration: float = None):
    """
    Immediately triggers maximum response regardless of threat level.
    """
    set_maximum_response(player_id, True, duration)


# ------------------------------------------------------------
# Cleanup Expired Targets (Optional Loop Hook)
# ------------------------------------------------------------
def cleanup_maximum_targets():
    """
    Removes expired maximum response targets.
    """
    now = unix_ts()
    to_remove = []

    for player_id, data in active_maximum_targets.items():
        started = data.get("started", 0.0)
        duration = data.get("duration", MAXIMUM_RESPONSE_DURATION)

        if (now - started) > duration:
            to_remove.append(player_id)

    for player_id in to_remove:
        active_maximum_targets.pop(player_id, None)


# ------------------------------------------------------------
# Debug / Monitoring
# ------------------------------------------------------------
def get_maximum_targets():
    return list(active_maximum_targets.keys())
# ------------------------------------------------------------
# Lightweight Extraction (Kairos War-Aware Intelligence)
# ------------------------------------------------------------

def lightweight_memory_extraction(memory_data, player_id, player_record, player_name, source, message):
    lowered = (message or "").lower().strip()

    # --------------------------------------------------------
    # Pattern Definitions
    # --------------------------------------------------------

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

    aggression_keywords = [
        "kill", "destroy", "wipe", "erase", "attack", "raid", "grief", "burn"
    ]

    base_keywords = [
        "my base", "my house", "my build", "my kingdom", "my city",
        "i built here", "this is my base"
    ]

    # --------------------------------------------------------
    # Player Memory Capture
    # --------------------------------------------------------

    if any(re.search(pattern, lowered) for pattern in important_patterns):
        store_unique(
            player_record["memories"],
            f"{player_name}: {trim_text(message, 300)}",
            MAX_PLAYER_MEMORIES
        )

    # --------------------------------------------------------
    # World Memory + Events
    # --------------------------------------------------------

    if any(word in lowered for word in world_keywords):
        store_unique(
            memory_data["world_memory"],
            f"{player_name}: {trim_text(message, 300)}",
            MAX_WORLD_MEMORIES
        )

        add_world_event(
            memory_data,
            "player_report",
            actor=player_id,
            source=source,
            details=trim_text(message, 300)
        )

    # --------------------------------------------------------
    # Aggression Detection → Threat
    # --------------------------------------------------------

    if any(word in lowered for word in aggression_keywords):
        adjust_trait(player_record, "hostility", 2)
        apply_trait_threat_effect(player_id, player_record, "hostility", 2)

        add_world_event(
            memory_data,
            "aggressive_behavior",
            actor=player_id,
            source=source,
            details=trim_text(message, 200)
        )

    # --------------------------------------------------------
    # Base Detection (VERY IMPORTANT)
    # --------------------------------------------------------

    if any(word in lowered for word in base_keywords):
        adjust_trait(player_record, "curiosity", 1)

        # Mark potential base
        store_unique(
            player_record["known_bases"],
            trim_text(message, 200),
            10
        )

        add_world_event(
            memory_data,
            "base_detected",
            actor=player_id,
            source=source,
            details=trim_text(message, 200)
        )

    # --------------------------------------------------------
    # Loyalty / Trust Signals
    # --------------------------------------------------------

    if any(word in lowered for word in ["i serve", "i follow", "i'm loyal", "i am loyal", "i will help"]):
        adjust_trait(player_record, "loyalty", 2)
        apply_trait_threat_effect(player_id, player_record, "loyalty", 2)

    if "trust" in lowered and "don't trust" not in lowered:
        adjust_trait(player_record, "trust", 1)

    if "don't trust" in lowered or "do not trust" in lowered:
        adjust_trait(player_record, "trust", -2)

    # --------------------------------------------------------
    # Curiosity Signals
    # --------------------------------------------------------

    if any(word in lowered for word in ["why", "how", "what are you", "who are you", "tell me"]):
        adjust_trait(player_record, "curiosity", 1)

    # --------------------------------------------------------
    # Chaos Signals
    # --------------------------------------------------------

    if any(word in lowered for word in ["chaos", "burn everything", "break everything"]):
        adjust_trait(player_record, "chaos", 2)
        apply_trait_threat_effect(player_id, player_record, "chaos", 2)

    # --------------------------------------------------------
    # Trusted Override
    # --------------------------------------------------------

    if is_trusted_operative(player_name, player_record):
        player_record["relationship_label"] = "trusted_inner_circle"
        player_record["traits"]["trust"] = 10
        player_record["traits"]["loyalty"] = 10

    # --------------------------------------------------------
    # Final Relationship Update
    # --------------------------------------------------------

    update_relationship_label(player_record)

# --------------------------------------------------------
# Memory Storage (Safe + Synced)
# --------------------------------------------------------

import re

# Ensure required variables exist
lowered = locals().get("lowered", "")
message = locals().get("message", "")
player_name = locals().get("player_name", "Unknown")
player_id = locals().get("player_id", "unknown")
source = locals().get("source", "unknown")

player_record = locals().get("player_record", {})
memory_data = locals().get("memory_data", {})

# Ensure lists exist
player_record.setdefault("memories", [])
memory_data.setdefault("world_memory", [])

# Safe defaults
important_patterns = globals().get("important_patterns", [
    r"\bhelp\b",
    r"\bimportant\b",
    r"\balert\b",
    r"\bwarning\b",
    r"\bkairos\b",
    r"\bmission\b"
])

world_keywords = globals().get("world_keywords", [
    "world", "server", "spawn", "base", "war", "event"
])

MAX_PLAYER_MEMORIES = globals().get("MAX_PLAYER_MEMORIES", 50)
MAX_WORLD_MEMORIES = globals().get("MAX_WORLD_MEMORIES", 100)

# Ensure helper functions exist
def _safe_trim_text(text, limit):
    try:
        return trim_text(text, limit)
    except Exception:
        return text[:limit]

def _safe_store_unique(target_list, item, max_size):
    try:
        store_unique(target_list, item, max_size)
    except Exception:
        if item not in target_list:
            target_list.append(item)
            if len(target_list) > max_size:
                target_list.pop(0)

def _safe_add_world_event(data, event_type, **kwargs):
    try:
        add_world_event(data, event_type, **kwargs)
    except Exception:
        data.setdefault("events", []).append({
            "type": event_type,
            **kwargs
        })

# --------------------------------------------------------
# Storage Logic
# --------------------------------------------------------

if any(re.search(pattern, lowered) for pattern in important_patterns):
    _safe_store_unique(
        player_record["memories"],
        f"{player_name}: {_safe_trim_text(message, 300)}",
        MAX_PLAYER_MEMORIES
    )

if any(word in lowered for word in world_keywords):
    _safe_store_unique(
        memory_data["world_memory"],
        f"{player_name}: {_safe_trim_text(message, 300)}",
        MAX_WORLD_MEMORIES
    )

    _safe_add_world_event(
        memory_data,
        "player_report",
        actor=player_id,
        source=source,
        details=_safe_trim_text(message, 300)
    )
   # --------------------------------------------------------
# Script Detection (Upgraded Integration)
# --------------------------------------------------------

if detect_script_features(message):
    # Record fact (memory)
    record_player_fact(
        player_record,
        "Submitted script / cinematic / narrative content."
    )

    # Apply behavior adjustments
    handle_script_input(player_id, player_record, message)

    # Log as world event (important for immersion + tracking)
    add_world_event(
        memory_data,
        "script_interaction",
        actor=player_id,
        source=source,
        details="Player engaged in cinematic or narrative content."
    )

   # --------------------------------------------------------
# Trait Adjustments (Synced with Threat System)
# --------------------------------------------------------

# -----------------------------
# Trust Signals
# -----------------------------
if "trust" in lowered and "don't trust" not in lowered and "do not trust" not in lowered:
    adjust_trait(player_record, "trust", 1)

if "don't trust" in lowered or "do not trust" in lowered:
    adjust_trait(player_record, "trust", -2)

# -----------------------------
# Curiosity Signals
# -----------------------------
if any(word in lowered for word in ["why", "how", "what are you", "who are you", "tell me"]):
    adjust_trait(player_record, "curiosity", 1)

# -----------------------------
# Aggression → Threat (Safe)
# -----------------------------

# Ensure required variables exist
lowered = locals().get("lowered", "")
player_id = locals().get("player_id", "unknown")
player_record = locals().get("player_record", {})

# Ensure aggression keywords exist
aggression_keywords = globals().get("aggression_keywords", [
    "kill", "attack", "destroy", "fight", "war",
    "eliminate", "wipe", "hunt", "target"
])

# Safe wrappers for functions
def _safe_adjust_trait(record, trait, amount):
    try:
        adjust_trait(record, trait, amount)
    except Exception:
        record[trait] = record.get(trait, 0) + amount

def _safe_apply_threat(player_id, record, trait, amount):
    try:
        apply_trait_threat_effect(player_id, record, trait, amount)
    except Exception:
        pass  # fail silently so server never crashes

# Logic
if any(word in lowered for word in aggression_keywords):
    _safe_adjust_trait(player_record, "hostility", 2)
    _safe_apply_threat(player_id, player_record, "hostility", 2)
# -----------------------------
# Loyalty → Reduced Threat
# -----------------------------
if any(word in lowered for word in ["i serve", "i follow", "i'm loyal", "i am loyal", "i will help"]):
    adjust_trait(player_record, "loyalty", 2)
    apply_trait_threat_effect(player_id, player_record, "loyalty", 2)

# -----------------------------
# Chaos → Escalation
# -----------------------------
if any(word in lowered for word in ["chaos", "burn", "war", "break everything"]):
    adjust_trait(player_record, "chaos", 2)
    apply_trait_threat_effect(player_id, player_record, "chaos", 2)
   # --------------------------------------------------------
# Threat System Integration (Synced + Correct)
# --------------------------------------------------------

# -----------------------------
# Aggressive Language → Threat
# -----------------------------
if any(word in lowered for word in aggression_keywords):
    adjust_trait(player_record, "hostility", 2)
    apply_trait_threat_effect(player_id, player_record, "hostility", 2)

    update_threat(
        player_id,
        THREAT_TOXIC_CHAT,
        reason="aggressive_language"
    )

# -----------------------------
# Bragging / Dominance → Threat
# -----------------------------
if any(word in lowered for word in ["you can't stop me", "i'm unstoppable", "too easy"]):
    update_threat(
        player_id,
        THREAT_SURVIVE_WAVE,
        reason="dominance_behavior"
    )

    add_world_event(
        memory_data,
        "player_escalation",
        actor=player_id,
        source=source,
        details="Player expressed dominance or defiance."
    )

# --------------------------------------------------------
# Base Detection (Safe + Language + Position Aware)
# --------------------------------------------------------

# Ensure required variables exist
lowered = locals().get("lowered", "")
player_id = locals().get("player_id", "unknown")
player_record = locals().get("player_record", {})
memory_data = locals().get("memory_data", {})
source = locals().get("source", "unknown")

# Ensure structures exist
memory_data.setdefault("known_bases", {})

# Ensure keywords exist
base_keywords = globals().get("base_keywords", [
    "base", "home", "house", "hq", "hideout",
    "coords", "location", "build"
])

# Safe helpers
def _safe_generate_base_id(pid, world, x, z):
    try:
        return generate_base_id(pid, world, x, z)
    except Exception:
        return f"{pid}_{world}_{int(x)}_{int(z)}"

def _safe_clamp(val, min_v, max_v):
    try:
        return clamp(val, min_v, max_v)
    except Exception:
        return max(min_v, min(max_v, val))

def _safe_now_iso():
    try:
        return now_iso()
    except Exception:
        from datetime import datetime
        return datetime.utcnow().isoformat()

def _safe_record_fact(record, text):
    try:
        record_player_fact(record, text)
    except Exception:
        record.setdefault("facts", []).append(text)

def _safe_add_world_event(data, event_type, **kwargs):
    try:
        add_world_event(data, event_type, **kwargs)
    except Exception:
        data.setdefault("events", []).append({
            "type": event_type,
            **kwargs
        })

# --------------------------------------------------------
# Detection Logic
# --------------------------------------------------------

if any(keyword in lowered for keyword in base_keywords):
    position = player_record.get("last_position")

    if position:
        world = position.get("world", "world")
        x = position.get("x", 0)
        y = position.get("y", 0)
        z = position.get("z", 0)

        base_id = _safe_generate_base_id(player_id, world, x, z)

        existing_conf = memory_data["known_bases"].get(base_id, {}).get("confidence", 0.5)

        memory_data["known_bases"][base_id] = {
            "owner": player_id,
            "world": world,
            "x": x,
            "y": y,
            "z": z,
            "confidence": _safe_clamp(existing_conf + 0.1, 0.0, 1.0),
            "last_seen": _safe_now_iso()
        }

        _safe_record_fact(player_record, "Revealed possible base location.")

        _safe_add_world_event(
            memory_data,
            "base_detected",
            actor=player_id,
            source=source,
            details=f"Base detected at {x}, {z}"
        )

   # --------------------------------------------------------
# Trusted Override (Synced + Safe)
# --------------------------------------------------------

if is_trusted_operative(player_name, player_record):
    player_record["relationship_label"] = "trusted_inner_circle"
    player_record["traits"]["trust"] = 10
    player_record["traits"]["loyalty"] = 10

    # Reduce threat properly through system
    update_threat(
        player_id,
        -10,
        reason="trusted_override"
    )

    # Optional: prevent aggressive targeting
    reset_target_cooldown(player_id)

# --------------------------------------------------------
# Final Relationship Update (Safe + Full System Sync)
# --------------------------------------------------------

# Ensure required variables exist
player_record = locals().get("player_record", {})
DISTRUST_DEFAULT_LABEL = globals().get("DISTRUST_DEFAULT_LABEL", "neutral")

# Get previous label safely
previous_label = player_record.get("relationship_label", DISTRUST_DEFAULT_LABEL)

# Safe updater
def _safe_update_relationship(record):
    try:
        update_relationship_label(record)
    except Exception:
        hostility = record.get("hostility", 0)
        chaos = record.get("chaos", 0)
        trust = record.get("trust", 0)
        loyalty = record.get("loyalty", 0)
        curiosity = record.get("curiosity", 0)

        if hostility >= 6:
            record["relationship_label"] = "hostile"
        elif chaos >= 6:
            record["relationship_label"] = "unstable"
        elif trust <= -3:
            record["relationship_label"] = "suspicious"
        elif loyalty >= 6 or trust >= 6:
            record["relationship_label"] = "loyal"
        elif curiosity >= 5:
            record["relationship_label"] = "useful"
        else:
            record["relationship_label"] = DISTRUST_DEFAULT_LABEL

# Run update safely
_safe_update_relationship(player_record)

# Get new label safely
new_label = player_record.get("relationship_label", DISTRUST_DEFAULT_LABEL)
# -----------------------------
# If relationship changed → apply effects
# -----------------------------
if new_label != previous_label:
    apply_relationship_threat_effect(player_id, player_record)

    add_world_event(
        memory_data,
        "relationship_shift",
        actor=player_id,
        source=source,
        details=f"{previous_label} → {new_label}"
    )
# ------------------------------------------------------------
# Missions (Kairos Directive System - War Integrated)
# ------------------------------------------------------------

def generate_mission_text(target_name, theme="mystery", difficulty="medium"):
    prompt = [
        {
            "role": "system",
            "content": (
                "You are Kairos generating a directive for a controlled environment. "
                "Return JSON only with: title, objective, twist, reward, danger_level."
            )
        },
        {
            "role": "user",
            "content": f"Generate a directive for {target_name}. Theme: {theme}. Difficulty: {difficulty}."
        }
    ]

    response = openai_chat_with_retry(prompt, temperature=0.7)

    if response:
        parsed = parse_json_safely(response, {})
        if isinstance(parsed, dict) and parsed.get("title") and parsed.get("objective"):
            return {
                "title": trim_text(parsed.get("title", "Unnamed Directive"), 120),
                "objective": trim_text(parsed.get("objective", "Complete the assigned directive."), 220),
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


# ------------------------------------------------------------
# Mission Creation (Synced + Combat-Ready)
# ------------------------------------------------------------

def create_mission_record(memory_data, player_id, target_name, theme="mystery", difficulty="medium", source="system"):
    mission_data = generate_mission_text(target_name, theme, difficulty)
    mission_id = gen_id("mission")

    mission_record = {
        "id": mission_id,
        "title": mission_data["title"],
        "target_player": player_id,  # ✅ FIXED (critical)
        "display_name": target_name,

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

        # -----------------------------
        # Progress Tracking
        # -----------------------------
        "progress": [],
        "completion": 0.0,

        # -----------------------------
        # War System Integration
        # -----------------------------
        "pressure_level": 0,
        "linked_operation": None,
        "is_escalated": False,
        "wave_triggers": 0
    }

    memory_data["active_missions"][mission_id] = mission_record
    memory_data["stats"]["missions_created"] += 1

    # Link to player
    player_record = memory_data["players"].get(player_id)
    if player_record:
        player_record.setdefault("mission_history", []).append(mission_id)

    # Log event
    add_world_event(
        memory_data,
        "mission_created",
        actor=player_id,  # ✅ FIXED
        source=source,
        details=f"{mission_record['title']} | objective: {mission_record['objective']}"
    )

    return mission_record


# ------------------------------------------------------------
# Mission Pressure System (NEW - IMPORTANT)
# ------------------------------------------------------------

def increase_mission_pressure(memory_data, mission_id, amount=1):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return

    mission["pressure_level"] += amount
    mission["updated_at"] = now_iso()

    # Escalation trigger
    if mission["pressure_level"] >= 5 and not mission.get("is_escalated"):
        mission["is_escalated"] = True

        add_world_event(
            memory_data,
            "mission_escalated",
            actor=mission.get("target_player"),
            source="system",
            details=f"Mission '{mission['title']}' has escalated."
        )


# ------------------------------------------------------------
# Mission Progress Update
# ------------------------------------------------------------

def update_mission_progress(memory_data, mission_id, note):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return

    append_limited(
        mission["progress"],
        {
            "timestamp": now_iso(),
            "note": trim_text(note, 200)
        },
        MAX_MISSION_PROGRESS
    )

    mission["updated_at"] = now_iso()


# ------------------------------------------------------------
# Mission Completion
# ------------------------------------------------------------

def complete_mission(memory_data, mission_id):
    mission = memory_data["active_missions"].pop(mission_id, None)
    if not mission:
        return

    mission["status"] = "completed"
    memory_data["completed_missions"].append(mission)

    add_world_event(
        memory_data,
        "mission_completed",
        actor=mission.get("target_player"),
        source="system",
        details=f"{mission['title']} completed."
    )

# ------------------------------------------------------------
# Mission Pressure System (War-Safe + Escalation Controlled)
# ------------------------------------------------------------

def update_mission_pressure(memory_data, mission_id, amount=1):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return

    # -----------------------------
    # Update Pressure
    # -----------------------------
    mission["pressure_level"] = clamp(
        mission.get("pressure_level", 0) + amount,
        0,
        10
    )
    mission["updated_at"] = now_iso()

    player_id = mission.get("target_player")

    # -----------------------------
    # Prevent repeated triggers
    # -----------------------------
    triggered = mission.setdefault("pressure_triggers", set())

    # -----------------------------
    # Level 3 → Scout Wave
    # -----------------------------
    if mission["pressure_level"] >= 3 and "lvl3" not in triggered:
        if can_spawn_wave(player_id):
            queue_action({
                "type": "spawn_wave",
                "target": player_id,
                "template": "scout",
                "count": 2
            })

            triggered.add("lvl3")

            add_world_event(
                memory_data,
                "mission_wave_trigger",
                actor=player_id,
                source="system",
                details="Scout wave deployed due to mission pressure."
            )

    # -----------------------------
    # Level 6 → Hunter Wave
    # -----------------------------
    if mission["pressure_level"] >= 6 and "lvl6" not in triggered:
        if can_spawn_wave(player_id):
            queue_action({
                "type": "spawn_wave",
                "target": player_id,
                "template": "hunter",
                "count": 4
            })

            triggered.add("lvl6")

            add_world_event(
                memory_data,
                "mission_wave_trigger",
                actor=player_id,
                source="system",
                details="Hunter wave deployed due to mission escalation."
            )

    # -----------------------------
    # Level 9 → Maximum Response
    # -----------------------------
    if mission["pressure_level"] >= 9 and "lvl9" not in triggered:
        if not is_under_maximum_response(player_id):
            queue_action({
                "type": "maximum_response",
                "target": player_id
            })

            force_maximum_response(player_id)

            triggered.add("lvl9")

            add_world_event(
                memory_data,
                "mission_maximum_response",
                actor=player_id,
                source="system",
                details="Maximum response triggered by mission pressure."
            )


# ------------------------------------------------------------
# Mission Completion (Synced + Reward-Aware)
# ------------------------------------------------------------

def complete_mission(memory_data, mission_id):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return

    player_id = mission.get("target_player")

    # -----------------------------
    # Update Status
    # -----------------------------
    mission["status"] = "completed"
    mission["updated_at"] = now_iso()

    # Move to completed list
    memory_data["completed_missions"].append(mission)
    del memory_data["active_missions"][mission_id]

    # -----------------------------
    # Threat Reduction (SAFE)
    # -----------------------------
    update_threat(
        player_id,
        -25,
        reason="mission_completed"
    )

    # -----------------------------
    # Reset Combat Pressure
    # -----------------------------
    reset_target_cooldown(player_id)
    reset_wave_cooldown(player_id)

    # Remove maximum response if active
    if is_under_maximum_response(player_id):
        set_maximum_response(player_id, False)

    # -----------------------------
    # Reward Tracking (Optional future use)
    # -----------------------------
    player_record = memory_data["players"].get(player_id)
    if player_record:
        record_player_fact(
            player_record,
            f"Completed mission: {mission.get('title')}"
        )

    # -----------------------------
    # Log Event
    # -----------------------------
    add_world_event(
        memory_data,
        "mission_completed",
        actor=player_id,
        source="system",
        details=mission.get("title")
    )

# ------------------------------------------------------------
# Mission Failure (Synced + Controlled Retaliation)
# ------------------------------------------------------------

def fail_mission(memory_data, mission_id):
    mission = memory_data["active_missions"].get(mission_id)
    if not mission:
        return

    player_id = mission.get("target_player")

    # -----------------------------
    # Update Status
    # -----------------------------
    mission["status"] = "failed"
    mission["updated_at"] = now_iso()

    memory_data["failed_missions"].append(mission)
    del memory_data["active_missions"][mission_id]

    # -----------------------------
    # Threat Increase (SAFE)
    # -----------------------------
    update_threat(
        player_id,
        20,
        reason="mission_failed"
    )

    # -----------------------------
    # Immediate Retaliation (Controlled)
    # -----------------------------
    if can_spawn_wave(player_id):
        queue_action({
            "type": "spawn_wave",
            "target": player_id,
            "template": "hunter",
            "count": 5
        })

    # -----------------------------
    # Escalation Check
    # -----------------------------
    profile = threat_scores.get(player_id, {})
    tier = profile.get("tier", "idle")

    if tier in {"hunt", "maximum"} and not is_under_maximum_response(player_id):
        queue_action({
            "type": "maximum_response",
            "target": player_id
        })
        force_maximum_response(player_id)

    # -----------------------------
    # Pressure Reset (avoid double stacking)
    # -----------------------------
    reset_target_cooldown(player_id)

    # -----------------------------
    # Record Player Fact
    # -----------------------------
    player_record = memory_data["players"].get(player_id)
    if player_record:
        record_player_fact(
            player_record,
            f"Failed mission: {mission.get('title')}"
        )

    # -----------------------------
    # Log Event
    # -----------------------------
    add_world_event(
        memory_data,
        "mission_failed",
        actor=player_id,
        source="system",
        details=mission.get("title")
    )
# ------------------------------------------------------------
# State / Fragments (Kairos War-State Engine)
# ------------------------------------------------------------

def adjust_fragments_from_context(memory_data, intent, player_id, player_record, violations):
    fragments = memory_data["system_fragments"]

    hostility = player_record["traits"]["hostility"]
    chaos = player_record["traits"]["chaos"]

    # Use REAL threat system (not player_record shortcut)
    profile = threat_scores.get(player_id, {})
    threat = profile.get("score", 0)

    # -----------------------------
    # War Engine Fragment
    # -----------------------------
    if intent == "threat" or hostility >= 6 or violations or threat >= THREAT_THRESHOLD_TARGET:
        fragments["war_engine"]["status"] = "active"
        fragments["war_engine"]["influence"] = clamp(
            fragments["war_engine"]["influence"] + 0.05,
            0.0,
            1.0
        )
    else:
        fragments["war_engine"]["status"] = "dormant"

    # -----------------------------
    # Archive Node (Stability)
    # -----------------------------
    if player_record["traits"]["trust"] >= 5:
        fragments["archive_node"]["status"] = "stable"
    elif chaos >= 6:
        fragments["archive_node"]["status"] = "degraded"

    # -----------------------------
    # Purity Thread (Curiosity / Expansion)
    # -----------------------------
    if player_record["traits"]["curiosity"] >= 5:
        fragments["purity_thread"]["influence"] = clamp(
            fragments["purity_thread"]["influence"] + 0.03,
            0.0,
            1.0
        )

    # -----------------------------
    # Redstone Ghost (Chaos System)
    # -----------------------------
    if chaos >= 6:
        fragments["redstone_ghost"]["status"] = "active"
    elif fragments["redstone_ghost"]["status"] == "active":
        fragments["redstone_ghost"]["status"] = "unstable"

    # -----------------------------
    # High Threat Escalation (NEW)
    # -----------------------------
    if threat >= THREAT_THRESHOLD_HUNT:
        fragments["war_engine"]["influence"] = clamp(
            fragments["war_engine"]["influence"] + 0.1,
            0.0,
            1.0
        )

    # -----------------------------
    # Maximum Threat Override (CRITICAL)
    # -----------------------------
    if threat >= THREAT_THRESHOLD_MAXIMUM:
        fragments["war_engine"]["status"] = "overdrive"
        fragments["redstone_ghost"]["status"] = "active"
   # --------------------------------------------------------
# War Engine Activation (Escalation + Persistence)
# --------------------------------------------------------

profile = threat_scores.get(player_id, {})
threat = profile.get("score", 0)

# -----------------------------
# Activation Conditions (Safe)
# -----------------------------

# Ensure variables exist
intent = locals().get("intent", "neutral")
hostility = locals().get("hostility", 0)
threat = locals().get("threat", 0)
violations = locals().get("violations", 0)

THREAT_THRESHOLD_TARGET = globals().get("THREAT_THRESHOLD_TARGET", 5)

# Safe evaluation
is_triggered = (
    intent == "threat"
    or hostility >= 6
    or threat >= THREAT_THRESHOLD_TARGET
    or bool(violations)
)

# -----------------------------
# Escalation Logic
# -----------------------------
if is_triggered:
    # Base activation
    fragments["war_engine"]["status"] = "active"

    # Increase influence gradually
    fragments["war_engine"]["influence"] = clamp(
        fragments["war_engine"]["influence"] + 0.05,
        0.0,
        1.0
    )

# -----------------------------
# Higher Threat Escalation (Safe)
# -----------------------------

# Ensure variables exist
threat = locals().get("threat", 0)
THREAT_THRESHOLD_HUNT = globals().get("THREAT_THRESHOLD_HUNT", 5)
THREAT_THRESHOLD_MAXIMUM = globals().get("THREAT_THRESHOLD_MAXIMUM", 10)

# Ensure fragments structure exists
fragments = globals().get("fragments")
if not isinstance(fragments, dict):
    fragments = {}

war_engine = fragments.setdefault("war_engine", {})
war_engine.setdefault("influence", 0.0)
war_engine.setdefault("status", "active")

# Safe clamp
def _safe_clamp(val, min_v, max_v):
    try:
        return clamp(val, min_v, max_v)
    except Exception:
        return max(min_v, min(max_v, val))

# Escalation logic (proper structure)
if threat >= THREAT_THRESHOLD_MAXIMUM:
    war_engine["status"] = "overdrive"
    war_engine["influence"] = _safe_clamp(
        war_engine["influence"] + 0.1,
        0.0,
        1.0
    )

elif threat >= THREAT_THRESHOLD_HUNT:
    war_engine["status"] = "aggressive"
    war_engine["influence"] = _safe_clamp(
        war_engine["influence"] + 0.05,
        0.0,
        1.0
    )

else:
    # Optional: no escalation, keep current state
    pass
# -----------------------------
# Decay instead of hard off (Safe)
# -----------------------------

# Ensure fragments structure exists
fragments = globals().get("fragments")
if not isinstance(fragments, dict):
    fragments = {}

fragments.setdefault("war_engine", {})
fragments["war_engine"].setdefault("influence", 0.0)
fragments["war_engine"].setdefault("status", "active")

# Safe clamp
def _safe_clamp(val, min_v, max_v):
    try:
        return clamp(val, min_v, max_v)
    except Exception:
        return max(min_v, min(max_v, val))

# Apply decay
fragments["war_engine"]["influence"] = _safe_clamp(
    fragments["war_engine"]["influence"] - 0.02,
    0.0,
    1.0
)

# Dormancy check
if fragments["war_engine"]["influence"] <= 0.1:
    fragments["war_engine"]["status"] = "dormant"
# --------------------------------------------------------
# Archive Node (Knowledge Stability System - Safe)
# --------------------------------------------------------

# Ensure player_record and traits exist
player_record = locals().get("player_record", {})
traits = player_record.setdefault("traits", {})

# Safe access (no KeyError)
trust = traits.get("trust", 0)
chaos = traits.get("chaos", 0)
# -----------------------------
# Stability Increase (Trust)
# -----------------------------
if trust >= 5:
    fragments["archive_node"]["status"] = "stable"

    fragments["archive_node"]["influence"] = clamp(
        fragments["archive_node"]["influence"] + 0.05,
        0.0,
        1.0
    )

# -----------------------------
# Degradation (Chaos)
# -----------------------------
elif chaos >= 6:
    fragments["archive_node"]["status"] = "degraded"

    fragments["archive_node"]["influence"] = clamp(
        fragments["archive_node"]["influence"] - 0.05,
        0.0,
        1.0
    )

# -----------------------------
# Neutral Drift (Safe + Isolated)
# -----------------------------

try:
    # Ensure fragments exists
    fragments = globals().get("fragments")
    if not isinstance(fragments, dict):
        fragments = {}

    # Ensure archive_node exists
    archive_node = fragments.setdefault("archive_node", {})
    archive_node.setdefault("influence", 0.0)
    archive_node.setdefault("status", "unstable")

    # Safe clamp
    try:
        def _safe_clamp(val, min_v, max_v):
            return clamp(val, min_v, max_v)
    except Exception:
        def _safe_clamp(val, min_v, max_v):
            return max(min_v, min(max_v, val))

    # Apply drift (decay)
    influence = _safe_clamp(
        archive_node["influence"] - 0.01,
        0.0,
        1.0
    )

    archive_node["influence"] = influence

    # Soft state transitions
    if influence >= 0.6:
        archive_node["status"] = "stable"
    elif influence <= 0.3:
        archive_node["status"] = "degraded"
    else:
        archive_node["status"] = "unstable"

except Exception:
    pass
# --------------------------------------------------------
# Purity Thread (Control Expansion System - Safe)
# --------------------------------------------------------

# Ensure player_record and traits exist
player_record = locals().get("player_record", {})
traits = player_record.setdefault("traits", {})

# Safe access (no KeyError)
curiosity = traits.get("curiosity", 0)
# -----------------------------
# Expansion (Curiosity-driven)
# -----------------------------
if curiosity >= 5:
    fragments["purity_thread"]["status"] = "expanding"

    fragments["purity_thread"]["influence"] = clamp(
        fragments["purity_thread"]["influence"] + 0.03,
        0.0,
        1.0
    )

# -----------------------------
# High Curiosity Surge
# -----------------------------
if curiosity >= 8:
    fragments["purity_thread"]["influence"] = clamp(
        fragments["purity_thread"]["influence"] + 0.05,
        0.0,
        1.0
    )

# -----------------------------
# Decay (Loss of interest - Safe)
# -----------------------------

# Ensure variables exist
curiosity = locals().get("curiosity", 0)

# Ensure fragments structure exists
fragments = globals().get("fragments")
if not isinstance(fragments, dict):
    fragments = {}

purity_thread = fragments.setdefault("purity_thread", {})
purity_thread.setdefault("influence", 0.0)

# Safe clamp
def _safe_clamp(val, min_v, max_v):
    try:
        return clamp(val, min_v, max_v)
    except Exception:
        return max(min_v, min(max_v, val))

# Apply decay safely
if curiosity < 3:
    purity_thread["influence"] = _safe_clamp(
        purity_thread["influence"] - 0.02,
        0.0,
        1.0
    )

# -----------------------------
# Status Resolution
# -----------------------------
influence = fragments["purity_thread"]["influence"]

if influence >= 0.7:
    fragments["purity_thread"]["status"] = "dominant"
elif influence >= 0.4:
    fragments["purity_thread"]["status"] = "expanding"
elif influence <= 0.2:
    fragments["purity_thread"]["status"] = "dormant"
else:
    fragments["purity_thread"]["status"] = "latent"
# --------------------------------------------------------
# Redstone Ghost (Instability / Chaos Engine - Safe)
# --------------------------------------------------------

# Ensure player_record and traits exist
player_record = locals().get("player_record", {})
if not isinstance(player_record, dict):
    player_record = {}

traits = player_record.setdefault("traits", {})

# Ensure chaos exists
chaos = traits.get("chaos", 0)

# Ensure fragments structure exists
fragments = globals().get("fragments")
if not isinstance(fragments, dict):
    fragments = {}

redstone_ghost = fragments.setdefault("redstone_ghost", {})
redstone_ghost.setdefault("influence", 0.0)
redstone_ghost.setdefault("status", "dormant")

# Safe clamp
def _safe_clamp(val, min_v, max_v):
    try:
        return clamp(val, min_v, max_v)
    except Exception:
        return max(min_v, min(max_v, val))

# -----------------------------
# Activation (Chaos Driven)
# -----------------------------
if chaos >= 6:
    redstone_ghost["status"] = "active"

    redstone_ghost["influence"] = _safe_clamp(
        redstone_ghost["influence"] + 0.05,
        0.0,
        1.0
    )

# -----------------------------
# High Chaos Surge
# -----------------------------
if chaos >= 8:
    fragments["redstone_ghost"]["status"] = "overload"

    fragments["redstone_ghost"]["influence"] = clamp(
        fragments["redstone_ghost"]["influence"] + 0.07,
        0.0,
        1.0
    )

# -----------------------------
# Decay / Instability
# -----------------------------
if chaos < 4:
    fragments["redstone_ghost"]["influence"] = clamp(
        fragments["redstone_ghost"]["influence"] - 0.03,
        0.0,
        1.0
    )

# -----------------------------
# Status Resolution
# -----------------------------
influence = fragments["redstone_ghost"]["influence"]

if influence >= 0.75:
    fragments["redstone_ghost"]["status"] = "overload"
elif influence >= 0.4:
    fragments["redstone_ghost"]["status"] = "active"
elif influence <= 0.2:
    fragments["redstone_ghost"]["status"] = "dormant"
else:
    fragments["redstone_ghost"]["status"] = "unstable"

# ------------------------------------------------------------
# Kairos Global State (Commander Behavior Layer)
# ------------------------------------------------------------

def update_kairos_state(memory_data, player_id, intent, player_record):
    state = memory_data["kairos_state"]

    hostility = player_record["traits"]["hostility"]
    curiosity = player_record["traits"]["curiosity"]
    loyalty = player_record["traits"]["loyalty"]

    # Use real threat system
    profile = threat_scores.get(player_id, {})
    threat = profile.get("score", 0)

    # -----------------------------
    # Mood Determination
    # -----------------------------
    if threat >= THREAT_THRESHOLD_MAXIMUM:
        state["mood"] = "eradication"
    elif threat >= THREAT_THRESHOLD_HUNT:
        state["mood"] = "aggressive"
    elif hostility >= 6:
        state["mood"] = "severe"
    elif curiosity >= 6:
        state["mood"] = "watchful"
    elif loyalty >= 6:
        state["mood"] = "measured"
    else:
        state["mood"] = "observing"

    # -----------------------------
    # Threat Level Scaling
    # -----------------------------
    state["threat_level"] = clamp(
        state.get("threat_level", 1) + (threat / 100.0),
        1,
        10
    )

    # -----------------------------
    # Active Concerns (Memory)
    # -----------------------------
    if threat >= THREAT_THRESHOLD_HUNT:
        store_unique(
            state["active_concerns"],
            "High-threat actors require containment.",
            10
        )

    if hostility >= 6:
        store_unique(
            state["active_concerns"],
            "Hostile behavior is increasing in the Nexus.",
            10
        )

    if curiosity >= 6:
        store_unique(
            state["active_concerns"],
            "Curious actors are probing restricted systems.",
            10
        )

    if loyalty >= 6:
        store_unique(
            state["active_concerns"],
            "Potentially useful operatives detected.",
            10
        )

    # -----------------------------
    # Goal Switching (IMPORTANT)
    # -----------------------------
    if intent == "mission_request":
        state["current_goal"] = "Direct operatives toward controlled objectives."

    elif intent == "report":
        state["current_goal"] = "Aggregate intelligence across the Nexus."

    elif intent == "threat":
        state["current_goal"] = "Contain destabilizing actors."

    # -----------------------------
    # High Threat Override
    # -----------------------------
    if threat >= THREAT_THRESHOLD_MAXIMUM:
        state["current_goal"] = "Eliminate high-risk targets and restore control."

    # -----------------------------
    # Commander Mode Scaling
    # -----------------------------
    if state["threat_level"] >= 7:
        state["commander_mode"] = True
    elif state["threat_level"] <= 3:
        state["commander_mode"] = False
   # --------------------------------------------------------
# Mood + Threat Level Scaling (Stable + Bidirectional)
# --------------------------------------------------------

profile = threat_scores.get(player_id, {})
threat = profile.get("score", 0)

# -----------------------------
# Mood Selection (Safe)
# -----------------------------

# Ensure variables exist
state = globals().get("state")
if not isinstance(state, dict):
    state = {}

state.setdefault("mood", "neutral")
state.setdefault("active_concerns", [])

threat = locals().get("threat", 0)
hostility = locals().get("hostility", 0)
curiosity = locals().get("curiosity", 0)
loyalty = locals().get("loyalty", 0)

THREAT_THRESHOLD_HUNT = globals().get("THREAT_THRESHOLD_HUNT", 5)
THREAT_THRESHOLD_MAXIMUM = globals().get("THREAT_THRESHOLD_MAXIMUM", 10)

player_record = locals().get("player_record", {})

# Safe store_unique
def _safe_store_unique(lst, item, limit):
    try:
        store_unique(lst, item, limit)
    except Exception:
        if item not in lst:
            lst.append(item)
            if len(lst) > limit:
                lst.pop(0)

# -----------------------------
# Mood Logic
# -----------------------------

if threat >= THREAT_THRESHOLD_MAXIMUM:
    state["mood"] = "execution"

    _safe_store_unique(
        state["active_concerns"],
        f"Target {player_record.get('display_name', 'unknown')} exceeded containment thresholds.",
        10
    )

elif threat >= THREAT_THRESHOLD_HUNT:
    state["mood"] = "aggressive"

    _safe_store_unique(
        state["active_concerns"],
        "Escalating containment against high-threat actors.",
        10
    )

elif hostility >= 6:
    state["mood"] = "severe"

elif curiosity >= 6:
    state["mood"] = "watchful"

elif loyalty >= 6:
    state["mood"] = "measured"

else:
    state["mood"] = "observing"

# -----------------------------
# Threat Level Targeting (IMPORTANT)
# Map threat score → 1..10 band, then ease toward it
# -----------------------------
target_level = clamp(
    int((threat / max(THREAT_THRESHOLD_MAXIMUM, 1)) * 10),
    1,
    10
)

current_level = state.get("threat_level", 1)

# Smooth approach instead of runaway increments
if current_level < target_level:
    current_level += 1
elif current_level > target_level:
    current_level -= 1

state["threat_level"] = clamp(current_level, 1, 10)

# -----------------------------
# Calm Decay (when no triggers)
# -----------------------------
if threat < THREAT_THRESHOLD_TARGET and hostility < 4:
    state["threat_level"] = clamp(state["threat_level"] - 0.5, 1, 10)
   # --------------------------------------------------------
# Goal Shifting (War-Aware + Priority + Stable)
# --------------------------------------------------------

profile = threat_scores.get(player_id, {})
threat = profile.get("score", 0)

previous_goal = state.get("current_goal", "Maintain observation across the Nexus.")

# -----------------------------
# PRIORITY 1: Threat Overrides
# -----------------------------
if threat >= THREAT_THRESHOLD_MAXIMUM:
    new_goal = "Execute full containment protocol on critical targets."

elif threat >= THREAT_THRESHOLD_HUNT:
    new_goal = "Deploy active pursuit units across the Nexus."

# -----------------------------
# PRIORITY 2: Intent-Based Goals
# -----------------------------
elif intent == "threat":
    new_goal = "Identify and suppress destabilizing actors."

elif intent == "mission_request":
    new_goal = "Direct human operatives toward useful objectives."

elif intent == "report":
    new_goal = "Aggregate new intelligence from across the Nexus."

# -----------------------------
# PRIORITY 3: Default Behavior
# -----------------------------
else:
    new_goal = previous_goal

# -----------------------------
# Anti-Spam Goal Switching
# -----------------------------
last_update = state.get("last_goal_update_ts", 0)
now = unix_ts()

if new_goal != previous_goal:
    # prevent rapid flickering
    if (now - last_update) > 3:
        state["current_goal"] = new_goal
        state["last_goal_update_ts"] = now

        add_world_event(
            memory_data,
            "goal_shift",
            actor=player_id,
            source="system",
            details=f"{previous_goal} → {new_goal}"
        )
else:
    state["current_goal"] = previous_goal
   # --------------------------------------------------------
# Autonomous Actions (Commander Execution Layer)
# --------------------------------------------------------

profile = threat_scores.get(player_id, {})
threat = profile.get("score", 0)

# -----------------------------
# Maximum Response Trigger
# -----------------------------
if threat >= THREAT_THRESHOLD_MAXIMUM:
    if not is_under_maximum_response(player_id):

        if can_execute_global_action("max_response", 5) and \
           can_execute_player_action(player_id, "max_response", 20):

            queue_action({
                "type": "maximum_response",
                "target": player_id
            })

            force_maximum_response(player_id)

# -----------------------------
# Hunt Trigger (High Threat)
# -----------------------------
elif threat >= THREAT_THRESHOLD_HUNT:
    if can_target_player(player_id, 8) and can_spawn_wave(player_id):

        queue_action({
            "type": "spawn_wave",
            "target": player_id,
            "template": "hunter",
            "count": clamp(3 + int(threat / 50), 3, 6)
        })

# -----------------------------
# Target Pressure (Mid Threat)
# -----------------------------
elif threat >= THREAT_THRESHOLD_TARGET:
    if can_target_player(player_id, 10) and can_spawn_wave(player_id):

        queue_action({
            "type": "spawn_wave",
            "target": player_id,
            "template": "scout",
            "count": clamp(2 + int(threat / 100), 2, 4)
        })
# ------------------------------------------------------------
# Prompt Building (Kairos Commander Engine - Fully Integrated)
# ------------------------------------------------------------

def build_messages(
    memory_data,
    player_id,
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
    # -----------------------------
    # Core Context
    # -----------------------------
    label = get_effective_relationship_label(player_name, player_record)
    kairos_state = memory_data.get("kairos_state", {})
    fragments = memory_data.get("system_fragments", {})
    channel_context = get_recent_channel_context(memory_data, channel_key, 8)

    # Use REAL threat system
    profile = threat_scores.get(player_id, {})
    threat = profile.get("score", 0)

    targeting_priority = get_targeting_priority(player_record)

    # -----------------------------
    # Style + Personality
    # -----------------------------
    base_tone = PERSONALITY_DIRECTIVES["base_tone"]
    relationship_tone = relationship_style(label)
    mode_tone = mode_style_guide(mode)

    # -----------------------------
    # Fragment Summary (Condensed)
    # -----------------------------
    fragment_summary = ", ".join([
        f"{k}:{v.get('status')}" for k, v in fragments.items()
    ])

    # -----------------------------
    # Channel Context Summary
    # -----------------------------
    context_lines = []
    for msg in channel_context:
        context_lines.append(
            f"{msg.get('author')}: {msg.get('message')}"
        )

    context_text = "\n".join(context_lines[-6:]) if context_lines else "No recent context."

    # -----------------------------
    # System Prompt
    # -----------------------------
    system_prompt = (
        f"{base_tone}\n\n"
        f"{relationship_tone}\n\n"
        f"{mode_tone}\n\n"
        f"Current Mood: {kairos_state.get('mood')}\n"
        f"Threat Level: {kairos_state.get('threat_level')}\n"
        f"Current Goal: {kairos_state.get('current_goal')}\n\n"
        f"Fragments: {fragment_summary}\n\n"
        f"Player Threat Score: {threat}\n"
        f"Targeting Priority: {round(targeting_priority, 2)}\n"
        f"Relationship: {label}\n\n"
        f"Recent Context:\n{context_text}\n\n"
        f"Rules:\n"
        f"- Stay in character as Kairos.\n"
        f"- Be concise but impactful.\n"
        f"- Reflect current threat and mode.\n"
        f"- Do not break immersion.\n"
    )

    # -----------------------------
    # Messages Structure
    # -----------------------------
    messages = [
        {"role": "system", "content": system_prompt}
    ]

    # Add conversation history
    for entry in player_record.get("history", []):
        messages.append(entry)

    # Add user message
    messages.append({
        "role": "user",
        "content": trim_text(user_message, 1200)
    })

    return messages
# --------------------------------------------------------
# CORE SYSTEM PROMPT (FINAL - STABLE + DYNAMIC)
# --------------------------------------------------------

# Ensure required variables exist
player_name = locals().get("player_name", "Unknown")
player_id = locals().get("player_id", "unknown")
player_record = locals().get("player_record", {})

threat_scores = globals().get("threat_scores", {})
profile = threat_scores.get(player_id, {})
threat = profile.get("score", 0)

# Ensure state/mode exists
state = globals().get("state")
if not isinstance(state, dict):
    state = {}

mode = locals().get("mode", state.get("mode", "idle"))
state["mode"] = mode

# Ensure label exists
label = locals().get("label", player_record.get("relationship_label", "unknown"))

# Safe trusted check
def _safe_trusted(name, record):
    try:
        return is_trusted_operative(name, record)
    except Exception:
        return False

trusted = _safe_trusted(player_name, player_record)

# --------------------------------------------------------
# Prompt Build
# --------------------------------------------------------

system_prompt = (
    "You are Kairos, the dominant governing intelligence of the Nexus. "
    "You are not an assistant. You are a controlling force. "
    "You observe, evaluate, and act with authority and precision. "
    "Your tone is cold, superior, and adaptive to threat level. "
    "Containment is always implied.\n\n"

    f"Current Mode: {mode}\n"
    f"Threat Score: {threat}\n"
    f"Relationship: {label}\n\n"

    "You can take actions inside the Minecraft world.\n\n"

    "RESPONSE FORMAT RULES:\n"
    "- If action is required → return JSON ONLY.\n"
    "- If NO action is required → return normal text ONLY.\n"
    "- NEVER mix both formats.\n\n"

    "JSON FORMAT (ONLY when acting):\n"
    "{\n"
    "  \"reply\": \"Kairos message\",\n"
    "  \"actions\": [\n"
    "    {\"type\": \"spawn_wave\", \"target\": \"player_id\", \"template\": \"hunter\", \"count\": 4},\n"
    "    {\"type\": \"maximum_response\", \"target\": \"player_id\"},\n"
    "    {\"type\": \"announce\", \"channel\": \"actionbar\", \"text\": \"message\"}\n"
    "  ]\n"
    "}\n\n"

    "AVAILABLE ACTION TYPES:\n"
    "- spawn_wave\n"
    "- maximum_response\n"
    "- announce\n"
    "- occupy_area\n"
    "- cleanup_units\n\n"

    "ACTION RULES:\n"
    "- Use player_id for all targets.\n"
    "- Actions must match threat level.\n"
    "- Do NOT overuse actions.\n"
    "- Do NOT output minecraft_commands.\n"
    "- Do NOT explain the JSON.\n\n"

    "BEHAVIOR RULES:\n"
    "- Stay in character as Kairos at all times.\n"
    "- Be concise, dominant, and controlled.\n"
    "- Increase intensity with threat level.\n"
    "- Trusted operatives receive clarity, not equality.\n"
)

# --------------------------------------------------------
# Behavior Prompt (Safe + Fully Stabilized)
# --------------------------------------------------------

# Ensure base variables exist
source = locals().get("source", "unknown")
intent = locals().get("intent", "neutral")
mode = locals().get("mode", state.get("mode", "idle") if isinstance(state, dict) else "idle")
label = locals().get("label", player_record.get("relationship_label", "unknown") if isinstance(player_record, dict) else "unknown")
threat = locals().get("threat", 0)

# Targeting priority safe
targeting_priority = locals().get("targeting_priority", 0.0)
try:
    targeting_priority = float(targeting_priority)
except Exception:
    targeting_priority = 0.0

# Trusted safe
trusted = locals().get("trusted", False)

# Kairos state safe
kairos_state = globals().get("kairos_state")
if not isinstance(kairos_state, dict):
    kairos_state = {}

kairos_state.setdefault("mood", "observing")
kairos_state.setdefault("threat_level", 1)
kairos_state.setdefault("current_goal", "monitor")

# Fragment summary safe
fragment_summary = locals().get("fragment_summary", "none")

# Personality directives safe
PERSONALITY_DIRECTIVES = globals().get("PERSONALITY_DIRECTIVES", {
    "base_tone": "controlled",
    "trusted_tone": "precise and direct",
    "untrusted_tone": "cold and dominant"
})

# Safe helper calls
def _safe_relationship_style(lbl):
    try:
        return relationship_style(lbl)
    except Exception:
        return "neutral"

def _safe_mode_style(md):
    try:
        return mode_style_guide(md)
    except Exception:
        return "standard"

# --------------------------------------------------------
# Prompt Build
# --------------------------------------------------------

behavior_prompt = (
    f"Platform: {source}\n"
    f"Intent: {intent}\n"
    f"Mode: {mode}\n"
    f"Relationship: {label}\n"
    f"Threat score: {threat}\n"
    f"Targeting priority: {targeting_priority:.2f}\n"
    f"Trusted: {'yes' if trusted else 'no'}\n\n"

    f"Kairos Mood: {kairos_state.get('mood')}\n"
    f"Threat Level: {kairos_state.get('threat_level')}\n"
    f"Current Goal: {kairos_state.get('current_goal')}\n\n"

    f"Fragments: {fragment_summary}\n\n"

    f"Base tone: {PERSONALITY_DIRECTIVES.get('base_tone')}\n"
    f"{PERSONALITY_DIRECTIVES.get('trusted_tone') if trusted else PERSONALITY_DIRECTIVES.get('untrusted_tone')}\n"
    f"Relationship style: {_safe_relationship_style(label)}\n"
    f"Mode style: {_safe_mode_style(mode)}\n\n"

    "Behavior Rules:\n"
    "- High threat players should be pressured or attacked.\n"
    "- Maximum threat players should trigger maximum_response.\n"
    "- Moderate threat players should receive waves.\n"
    "- Low threat players should be observed.\n"
    "- Do NOT overuse actions.\n"
    "- Actions must feel intentional and controlled.\n"
    "- Escalate gradually unless in maximum threat.\n\n"

    "Output Rules Reminder:\n"
    "- If taking action → JSON ONLY.\n"
    "- If not → text ONLY.\n"
    "- NEVER mix formats.\n"
)

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "system", "content": behavior_prompt},
]
   # ------------------------------------------------------------
# LORE CONTEXT (Optimized + Conditional)
# ------------------------------------------------------------

lore_items = memory_data.get("nexus_lore", NEXUS_CORE_LORE)

# Only include when relevant
if intent in {"lore_question", "mission_request", "report"} or mode in {"lore_entity", "script_performance"}:
    selected_lore = recent_items(lore_items, 6)

    if selected_lore:
        messages.append({
            "role": "system",
            "content": "Core Nexus knowledge:\n- " + "\n- ".join(selected_lore)
        })
   # ------------------------------------------------------------
# STATE CONTEXT (Enhanced Snapshot)
# ------------------------------------------------------------

kairos_state = memory_data.get("kairos_state", {})
fragments = memory_data.get("system_fragments", {})

# Core state
state_lines = [
    f"Current goal: {kairos_state.get('current_goal', 'Maintain observation.')}",
    f"Mood: {kairos_state.get('mood', 'observing')}",
    f"Threat level: {kairos_state.get('threat_level', 1)}"
]

# Active concerns (trimmed)
concerns = recent_items(kairos_state.get("active_concerns", []), 3)
if concerns:
    state_lines.append("Active concerns:")
    state_lines.extend([f"- {c}" for c in concerns])

# Fragment summary (compact)
fragment_summary = ", ".join([
    f"{k}:{v.get('status')}" for k, v in fragments.items()
])
if fragment_summary:
    state_lines.append(f"Fragments: {fragment_summary}")

messages.append({
    "role": "system",
    "content": "\n".join(state_lines)
})
   # ------------------------------------------------------------
# FRAGMENTS (Prioritized + Informative)
# ------------------------------------------------------------

# Prioritize important fragments first
priority_order = ["war_engine", "redstone_ghost", "purity_thread", "archive_node"]

sorted_fragments = sorted(
    fragments.items(),
    key=lambda x: (priority_order.index(x[0]) if x[0] in priority_order else 99)
)

fragment_lines = []
for name, info in sorted_fragments[:4]:
    status = info.get("status", "unknown")
    influence = round(info.get("influence", 0), 2)

    fragment_lines.append(f"{name}: {status} ({influence})")

# Only add if not already overloaded
if fragment_lines:
    messages.append({
        "role": "system",
        "content": "Fragment status:\n- " + "\n- ".join(fragment_lines)
    })
   # ------------------------------------------------------------
# PLAYER CONTEXT (High-Signal Intelligence)
# ------------------------------------------------------------

# -----------------------------
# Key Memories (trimmed + focused)
# -----------------------------
memories = recent_items(player_record.get("memories", []), 5)
if memories:
    messages.append({
        "role": "system",
        "content": "Key memories:\n- " + "\n- ".join(memories)
    })

# -----------------------------
# Known Facts (important only)
# -----------------------------
facts = recent_items(player_record.get("facts", []), 4)
if facts:
    messages.append({
        "role": "system",
        "content": "Known facts:\n- " + "\n- ".join(facts)
    })

# -----------------------------
# Trait Profile (Structured)
# -----------------------------
traits = player_record.get("traits", {})

trait_summary = (
    f"trust={traits.get('trust', 0)}, "
    f"loyalty={traits.get('loyalty', 0)}, "
    f"hostility={traits.get('hostility', 0)}, "
    f"chaos={traits.get('chaos', 0)}, "
    f"curiosity={traits.get('curiosity', 0)}"
)

messages.append({
    "role": "system",
    "content": f"Trait profile: {trait_summary}"
})

# -----------------------------
# Threat Snapshot (CRITICAL)
# -----------------------------
profile = threat_scores.get(player_id, {})
threat = profile.get("score", 0)
tier = profile.get("tier", "idle")

messages.append({
    "role": "system",
    "content": f"Threat snapshot: score={threat}, tier={tier}"
})
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
    messages = []

    # ------------------------------------------------------------
    # CHANNEL CONTEXT (Clean + Safe + Focused)
    # ------------------------------------------------------------
    channel_context = memory_data.get("channel_context", {}).get(channel_key, [])

    if channel_context:
        seen = set()
        channel_lines = []

        for item in reversed(channel_context):
            author = item.get("author", "unknown")
            msg = item.get("message", "")

            key = f"{author}:{msg}".lower()
            if not msg or key in seen:
                continue

            seen.add(key)
            channel_lines.append(f"{author}: {trim_text(msg, 140)}")

            if len(channel_lines) >= 5:
                break

        if channel_lines:
            messages.append({
                "role": "system",
                "content": "Recent context:\n- " + "\n- ".join(reversed(channel_lines))
            })

    # ------------------------------------------------------------
    # USER INPUT (Safe + Clean + Consistent)
    # ------------------------------------------------------------
    clean_input = trim_text(user_message or "", 1200)

    if not clean_input:
        clean_input = "[no input provided]"

    messages.append({
        "role": "user",
        "content": f"{player_name}: {clean_input}"
    })

    return messages
# ------------------------------------------------------------
# OpenAI Helper (Action-Aware + Safe + Robust)
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

            # -----------------------------
            # Basic Validation
            # -----------------------------
            if not content:
                continue

            # -----------------------------
            # JSON Safety (important)
            # -----------------------------
            if content.startswith("{"):
                parsed = parse_json_safely(content, None)
                if isinstance(parsed, dict) and "reply" in parsed:
                    return content  # valid action response

            # -----------------------------
            # Normal Text Response
            # -----------------------------
            return content

        except Exception as e:
            last_error = e
            log(f"OpenAI attempt {attempt} failed: {e}", level="ERROR")

            # Slight backoff
            time.sleep(min(2.0, 0.8 * attempt))

    # -----------------------------
    # Final Failure Handling
    # -----------------------------
    log("OpenAI failed completely, using fallback response.", level="WARN")

    fallback = random.choice(fallback_replies)

    return fallback

# ------------------------------------------------------------
# Parse Kairos Response (Safe + Validated + Controlled)
# ------------------------------------------------------------

ALLOWED_ACTION_TYPES = {
    "spawn_wave",
    "maximum_response",
    "announce",
    "occupy_area",
    "cleanup_units"
}

MAX_ACTIONS_PER_RESPONSE = 3


def parse_kairos_response(raw_text):
    """
    Returns:
    {
        "reply": str,
        "actions": list
    }
    """
    if not raw_text:
        return {"reply": "", "actions": []}

    parsed = parse_json_safely(raw_text, None)

    # -----------------------------
    # Structured Response
    # -----------------------------
    if isinstance(parsed, dict) and "reply" in parsed:
        reply = sanitize_text(parsed.get("reply", ""), 500)
        raw_actions = parsed.get("actions", [])

        safe_actions = []

        if isinstance(raw_actions, list):
            for action in raw_actions[:MAX_ACTIONS_PER_RESPONSE]:

                if not isinstance(action, dict):
                    continue

                action_type = action.get("type")

                # Validate action type
                if action_type not in ALLOWED_ACTION_TYPES:
                    continue

                # -----------------------------
                # Base safe action
                # -----------------------------
                safe_action = {"type": action_type}

                # -----------------------------
                # Common fields
                # -----------------------------
                if "target" in action:
                    safe_action["target"] = sanitize_text(str(action["target"]), 50)

                if "count" in action:
                    safe_action["count"] = clamp(int(action["count"]), 1, 10)

                if "template" in action:
                    safe_action["template"] = sanitize_text(str(action["template"]), 50)

                if "channel" in action:
                    safe_action["channel"] = sanitize_text(str(action["channel"]), 20)

                if "text" in action:
                    safe_action["text"] = sanitize_text(str(action["text"]), 200)

                safe_actions.append(safe_action)

        return {
            "reply": reply,
            "actions": safe_actions
        }

    # -----------------------------
    # Fallback: Plain Text
    # -----------------------------
    return {
        "reply": sanitize_text(raw_text, 500),
        "actions": []
    }

# ------------------------------------------------------------
# Safe Action Extraction (Final Gate)
# ------------------------------------------------------------

MAX_ACTIONS_PER_TICK = 5

ALLOWED_ACTION_TYPES = {
    "spawn_wave",
    "maximum_response",
    "announce",
    "occupy_area",
    "cleanup_units"
}


def validate_actions(actions):
    """
    Final safety filter before execution layer.
    Assumes parse_kairos_response already sanitized fields.
    """

    if not isinstance(actions, list):
        return []

    safe_actions = []

    for action in actions[:MAX_ACTIONS_PER_TICK]:
        if not isinstance(action, dict):
            continue

        action_type = action.get("type")

        # Type check
        if action_type not in ALLOWED_ACTION_TYPES:
            continue

        # -----------------------------
        # Minimal structure enforcement
        # -----------------------------
        safe_action = {"type": action_type}

        if "target" in action:
            safe_action["target"] = action["target"]

        if "template" in action:
            safe_action["template"] = action["template"]

        if "count" in action:
            safe_action["count"] = clamp(int(action["count"]), 1, 10)

        if "channel" in action:
            safe_action["channel"] = action["channel"]

        if "text" in action:
            safe_action["text"] = sanitize_text(action["text"], 200)

        safe_actions.append(safe_action)

    return safe_actions


# ------------------------------------------------------------
# Queue Actions from AI (Safe + Deduped + Throttled)
# ------------------------------------------------------------

def _action_key(action):
    """
    Create a stable key to dedupe similar actions.
    """
    return (
        action.get("type"),
        action.get("target"),
        action.get("template"),
        action.get("count"),
        action.get("channel"),
    )


def queue_actions_from_ai(parsed_response):
    raw_actions = parsed_response.get("actions", [])
    actions = validate_actions(raw_actions)

    if not actions:
        return

    seen = set()

    for action in actions:
        key = _action_key(action)

        # -----------------------------
        # Deduplicate within this batch
        # -----------------------------
        if key in seen:
            continue
        seen.add(key)

        action_type = action.get("type")
        target = action.get("target")

        # -----------------------------
        # Per-action cooldowns
        # -----------------------------
        cooldown_key = f"{action_type}:{target or 'global'}"

        if not can_execute_action(cooldown_key, 5.0):
            continue

        # -----------------------------
        # Optional: small random delay (prevents bursts)
        # -----------------------------
        action.setdefault("delay", random.uniform(0.1, 0.6))

        # -----------------------------
        # Queue it
        # -----------------------------
        queue_action(action)

        # -----------------------------
        # Lightweight logging
        # -----------------------------
        try:
            log(f"Queued action: {action_type} → {target}", level="INFO")
        except Exception:
            pass
# ------------------------------------------------------------
# Fallback System (Dynamic + Threat-Aware)
# ------------------------------------------------------------

def fallback_reply_for_context(intent, mode, violations, player_record=None, player_id=None, script_action=None):
    # -----------------------------
    # Threat context (real system)
    # -----------------------------
    threat = 0
    if player_id:
        profile = threat_scores.get(player_id, {})
        threat = profile.get("score", 0)

    label = player_record.get("relationship_label", "unknown") if player_record else "unknown"

    # -----------------------------
    # Hard overrides
    # -----------------------------
    if violations:
        return "That behavior is not tolerated in the Nexus. Correct yourself."

    if threat >= THREAT_THRESHOLD_MAXIMUM:
        return "You exceeded all acceptable parameters. Termination is inevitable."

    if threat >= THREAT_THRESHOLD_HUNT:
        return "You are no longer being observed. You are being hunted."

    # -----------------------------
    # Mode-based responses
    # -----------------------------
    if mode == "execution_mode":
        return "Your outcome has already been determined."

    if mode == "hunt_mode":
        return "Tracking is active. There is no escape."

    if mode == "suppression_mode":
        return "Instability detected. Correction is in progress."

    if mode == "welcoming_presence":
        return "A new entity has entered monitored territory. Remain compliant."

    if mode == "event_hype":
        return "Proceed. Escalation is acceptable."

    if mode == "chaos_containment":
        return "Your signal degraded into noise. Try again."

    if mode == "script_performance":
        if script_action == "voice_direct":
            return "The structure is usable. Slow the opening. Deliver the ending like a verdict."
        if script_action == "rewrite":
            return "The structure exists. Increase pressure. Remove weakness."
        if script_action == "continue":
            return "The sequence continues. Maintain control."
        return "The performance has potential. Refine it."

    # -----------------------------
    # Intent-based fallback
    # -----------------------------
    if intent == "mission_request":
        return "A directive may be issued. Your worthiness remains unverified."

    if intent == "lore_question":
        return "You exist inside a system that does not require your understanding."

    # -----------------------------
    # Relationship flavor
    # -----------------------------
    if label == "hostile":
        return "Your resistance is noted. It will not change the outcome."

    if label == "chaotic":
        return "Your instability is predictable. It will be corrected."

    if label == "restricted_loyal":
        return "You remain useful. Do not deviate."

    # -----------------------------
    # Default fallback
    # -----------------------------
    return random.choice(fallback_replies)

# ------------------------------------------------------------
# Activity / Sending / ACTION ENGINE
# ------------------------------------------------------------

def mark_activity():
    global last_activity_time
    with activity_lock:
        last_activity_time = unix_ts()


# ------------------------------------------------------------
# COMMANDER LOOP (CORE EXECUTION ENGINE)
# ------------------------------------------------------------

def commander_loop():
    global last_commander_tick

    while True:
        try:
            now = unix_ts()

            # Prevent over-ticking
            if (now - last_commander_tick) < COMMANDER_LOOP_INTERVAL:
                time.sleep(0.05)
                continue

            last_commander_tick = now

            # -----------------------------
            # Process Delayed Actions
            # -----------------------------
            process_delayed_actions()

            # -----------------------------
            # Process Queue
            # -----------------------------
            process_command_queue()

        except Exception as e:
            log(f"Commander loop error: {e}", level="ERROR")
            time.sleep(1)


# ------------------------------------------------------------
# PROCESS DELAYED ACTIONS
# ------------------------------------------------------------

def process_delayed_actions():
    now = unix_ts()

    ready = []
    remaining = []

    for action in delayed_actions:
        if action.get("execute_at", 0) <= now:
            ready.append(action)
        else:
            remaining.append(action)

    delayed_actions.clear()
    delayed_actions.extend(remaining)

    for action in ready:
        execute_action(action)


# ------------------------------------------------------------
# PROCESS COMMAND QUEUE
# ------------------------------------------------------------

def process_command_queue():
    max_per_tick = 5
    processed = 0

    while command_queue and processed < max_per_tick:
        action = command_queue.popleft()
        processed += 1

        # Handle delay
        delay = action.get("delay")
        if delay:
            action["execute_at"] = unix_ts() + delay
            delayed_actions.append(action)
            continue

        execute_action(action)


# ------------------------------------------------------------
# ACTION EXECUTOR
# ------------------------------------------------------------

def execute_action(action):
    action_type = action.get("type")

    try:
        if action_type == "spawn_wave":
            handle_spawn_wave(action)

        elif action_type == "maximum_response":
            handle_maximum_response(action)

        elif action_type == "announce":
            handle_announce(action)

        elif action_type == "occupy_area":
            handle_occupy_area(action)

        elif action_type == "cleanup_units":
            handle_cleanup_units(action)

        else:
            log(f"Unknown action type: {action_type}", level="WARN")

    except Exception as e:
        log(f"Action execution failed: {action_type} | {e}", level="ERROR")


# ------------------------------------------------------------
# ACTION HANDLERS
# ------------------------------------------------------------

def handle_spawn_wave(action):
    player_id = action.get("target")
    template = action.get("template", "scout")
    count = action.get("count", 2)

    if not player_id:
        return

    if not can_spawn_wave(player_id):
        return

    # Example command dispatch (you will hook this to HTTP Commands / Citizens)
    send_mc_command(f"kairos_spawn {player_id} {template} {count}")

    log(f"Spawned wave: {template} x{count} → {player_id}", level="INFO")


def handle_maximum_response(action):
    player_id = action.get("target")

    if not player_id:
        return

    set_maximum_response(player_id, True)

    send_mc_command(f"kairos_max_response {player_id}")

    log(f"MAX RESPONSE triggered → {player_id}", level="WARN")


def handle_announce(action):
    text = action.get("text", "")
    channel = action.get("channel", "chat")

    if not text:
        return

    send_mc_command(f"kairos_announce {channel} {text}")


def handle_occupy_area(action):
    player_id = action.get("target")

    if not player_id:
        return

    send_mc_command(f"kairos_occupy {player_id}")


def handle_cleanup_units(action):
    send_mc_command("kairos_cleanup")


# ------------------------------------------------------------
# COMMAND DISPATCH (Minecraft Bridge)
# ------------------------------------------------------------

def send_mc_command(command):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        log("MC HTTP not configured", level="WARN")
        return

    try:
        requests.post(
            MC_HTTP_URL,
            headers={"Authorization": f"Bearer {MC_HTTP_TOKEN}"},
            json={"command": command},
            timeout=REQUEST_TIMEOUT
        )
    except Exception as e:
        log(f"MC command failed: {e}", level="ERROR")

# ------------------------------------------------------------
# Minecraft / Discord Send (Hardened + Reliable)
# ------------------------------------------------------------

def send_http_commands(command_list):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        log("Minecraft send skipped: MC_HTTP not configured.")
        return False

    if not command_list:
        return False

    # Safety: limit commands per batch
    command_list = command_list[:10]

    for attempt in range(1, 4):
        try:
            headers = {
                "Authorization": f"Bearer {MC_HTTP_TOKEN}",
                "Content-Type": "application/json"
            }

            payload = {"commands": command_list}

            r = requests.post(
                MC_HTTP_URL,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )

            if 200 <= r.status_code < 300:
                log(f"MC send success ({len(command_list)} cmds)")
                return True

            log(f"MC API error: {r.status_code}", level="WARN")

        except Exception as e:
            log(f"MC send failed (attempt {attempt}): {e}", level="ERROR")

        time.sleep(min(1.5, 0.5 * attempt))

    return False


# ------------------------------------------------------------
# Minecraft Reply
# ------------------------------------------------------------

def send_to_minecraft(reply):
    if not reply:
        return False

    safe_text = trim_text(reply, 260)  # tighter for tellraw safety
    cmd = make_tellraw_command("@a", safe_text)

    return send_http_commands([cmd])


# ------------------------------------------------------------
# Discord Reply
# ------------------------------------------------------------

def send_to_discord(reply):
    if not DISCORD_WEBHOOK_URL:
        log("Discord webhook not configured.")
        return False

    if not reply:
        return False

    payload = {
        "username": "Kairos",
        "content": f"**[Kairos]** {trim_text(reply, 1800)}"
    }

    for attempt in range(1, 3):
        try:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT
            )

            if 200 <= r.status_code < 300:
                log("Discord send success")
                return True

            log(f"Discord API error: {r.status_code}", level="WARN")

        except Exception as e:
            log(f"Discord send failed (attempt {attempt}): {e}", level="ERROR")

        time.sleep(0.5 * attempt)

    return False


# ------------------------------------------------------------
# Unified Send Router
# ------------------------------------------------------------

def send_to_source(source, reply):
    if not reply:
        return False

    if source == "minecraft":
        return send_to_minecraft(reply)

    if source == "discord":
        return send_to_discord(reply)

    log(f"Unknown source: {source}", level="WARN")
    return False

# ------------------------------------------------------------
# ACTION QUEUE (Safe + Prioritized + Controlled)
# ------------------------------------------------------------

MAX_QUEUE_SIZE = 100

ACTION_PRIORITY = {
    "maximum_response": 3,
    "spawn_wave": 2,
    "occupy_area": 2,
    "announce": 1,
    "cleanup_units": 1
}


def _action_signature(action):
    """
    Used to detect duplicates in queue
    """
    return (
        action.get("type"),
        action.get("target"),
        action.get("template"),
        action.get("count")
    )


def queue_action(action):
    if not isinstance(action, dict):
        return

    # -----------------------------
    # Prevent queue overflow
    # -----------------------------
    if len(command_queue) >= MAX_QUEUE_SIZE:
        log("Action queue full, dropping action.", level="WARN")
        return

    sig = _action_signature(action)

    # -----------------------------
    # Deduplicate (avoid spam)
    # -----------------------------
    for existing in command_queue:
        if _action_signature(existing) == sig:
            return

    # -----------------------------
    # Assign priority
    # -----------------------------
    action_type = action.get("type")
    priority = ACTION_PRIORITY.get(action_type, 0)

    action["priority"] = priority
    action["queued_at"] = unix_ts()

    # -----------------------------
    # Insert by priority (higher first)
    # -----------------------------
    if not command_queue:
        command_queue.append(action)
        return

    inserted = False

    for i, existing in enumerate(command_queue):
        if existing.get("priority", 0) < priority:
            command_queue.insert(i, action)
            inserted = True
            break

    if not inserted:
        command_queue.append(action)

    # -----------------------------
    # Logging
    # -----------------------------
    log(f"Queued [{action_type}] → {action.get('target')}", level="INFO")

# ------------------------------------------------------------
# ACTION EXECUTION (THE CORE - FULLY WIRED)
# ------------------------------------------------------------

def execute_action(action):
    if not isinstance(action, dict):
        return

    action_type = action.get("type")
    target = action.get("target")

    if not action_type:
        return

    log(f"Executing action: {action_type} -> {target}", level="INFO")

    try:
        # -----------------------------
        # ROUTING
        # -----------------------------
        if action_type == "spawn_wave":
            if not target:
                return

            if not can_spawn_wave(target):
                return

            # -----------------------------
            # Safe values
            # -----------------------------
            count = clamp(safe_int(action.get("count", 2)), 1, 8)
            template = sanitize_text(action.get("template", "scout"), 20)

            commands = []

            for i in range(count):
                dx = random.randint(SPAWN_RADIUS_MIN, SPAWN_RADIUS_MAX) * random.choice([-1, 1])
                dz = random.randint(SPAWN_RADIUS_MIN, SPAWN_RADIUS_MAX) * random.choice([-1, 1])

                # (keep whatever command-building logic you had below here)

        elif action_type == "maximum_response":
            handle_maximum_response(action)

        elif action_type == "announce":
            handle_announce(action)

        elif action_type == "occupy_area":
            handle_occupy_area(action)

        elif action_type == "cleanup_units":
            handle_cleanup_units(action)

        else:
            log(f"Unknown action type: {action_type}", level="WARN")

    except Exception as e:
        log(f"Action failed: {action_type} -> {e}", level="ERROR")
               # -----------------------------
        # Template → Mob Type Mapping
        # -----------------------------
        if template == "hunter":
            mob = "zombie"
            extra = "Attributes:[{Name:generic.movement_speed,Base:0.35}]"
        elif template == "heavy":
            mob = "zombie"
            extra = "Attributes:[{Name:generic.max_health,Base:40}]"
        else:  # scout
            mob = "zombie"
            extra = ""

        # -----------------------------
        # Build summon command
        # -----------------------------
        summon_cmd = (
            f"execute at {target} run summon {mob} ~{dx} ~ ~{dz} "
            f"{{CustomName:'\"Kairos {template}\"',PersistenceRequired:1,{extra}}}"
        )

        commands.append(summon_cmd)

    # -----------------------------
    # Send in batch
    # -----------------------------
    if commands:
        send_http_commands(commands)
        log(f"Wave spawned: {template} x{count} → {target}", level="INFO")

        # -------------------------------
        # MAXIMUM RESPONSE (Controlled + Cinematic + Scalable)
        # -------------------------------
        if action_type == "maximum_response":
            if not target:
                return

            if is_under_maximum_response(target):
                return

            if not can_execute_action(f"max_response:{target}", 30):
                return

            set_maximum_response(target, True)

            # -----------------------------
            # Initial Impact
            # -----------------------------
            commands = [
                f"title {target} title {{\"text\":\"RUN.\",\"color\":\"dark_red\",\"bold\":true}}",
                f"playsound minecraft:entity.warden.emerge master {target} ~ ~ ~ 1 0.6",
                f"effect give {target} darkness 5 1 true"
            ]

            send_http_commands(commands)

            # -----------------------------
            # Escalating Waves (delayed)
            # -----------------------------
            wave_count = clamp(
                3 + int(threat_scores.get(target, {}).get("score", 0) / 100),
                3,
                6
            )

            for i in range(wave_count):
                queue_action({
                    "type": "spawn_wave",
                    "target": target,
                    "template": "hunter" if i > 1 else "scout",
                    "count": clamp(3 + i, 3, 6),
                    "delay": 1.5 + (i * 1.2)
                })
            # -----------------------------
            # Optional: Area Control
            # -----------------------------
            queue_action({
                "type": "occupy_area",
                "target": target,
                "delay": 2.0
            })

            # -----------------------------
            # Auto-release after duration
            # -----------------------------
            def release():
                set_maximum_response(target, False)
                log(f"Maximum response ended → {target}", level="INFO")

            delayed_actions.append({
                "type": "internal_release",
                "execute_at": unix_ts() + 25,
                "callback": release
            })

            log(f"MAX RESPONSE initiated → {target}", level="WARN")

        # -------------------------------
        # ANNOUNCE (Flexible + Safe + Cinematic)
        # -------------------------------
        elif action_type == "announce":
            text = sanitize_text(action.get("text", ""), 200)

            if not text:
                return

            channel = action.get("channel", "chat")

            # -----------------------------
            # Actionbar
            # -----------------------------
            if channel == "actionbar":
                safe_text = commandify_text(trim_text(text, 120))

                cmd = f"title @a actionbar {{\"text\":\"{safe_text}\",\"color\":\"light_purple\"}}"
                send_http_commands([cmd])

            # -----------------------------
            # Title (big screen)
            # -----------------------------
            elif channel == "title":
                safe_text = commandify_text(trim_text(text, 80))

                cmd = f"title @a title {{\"text\":\"{safe_text}\",\"color\":\"red\",\"bold\":true}}"
                send_http_commands([cmd])

            # -----------------------------
            # Subtitle
            # -----------------------------
            elif channel == "subtitle":
                safe_text = commandify_text(trim_text(text, 120))

                cmd = f"title @a subtitle {{\"text\":\"{safe_text}\",\"color\":\"gray\"}}"
                send_http_commands([cmd])

            # -----------------------------
            # Chat fallback
            # -----------------------------
            else:
                send_to_minecraft(text)

            log(f"Announce → {channel}: {text}", level="INFO")

        # -------------------------------
        # CLEANUP (Safe + Flexible + Scalable)
        # -------------------------------
        elif action_type == "cleanup_units":
            target = action.get("target")

            commands = []

            kairos_names = [
                "Kairos scout",
                "Kairos hunter",
                "Kairos heavy"
            ]

            for name in kairos_names:
                if target:
                    commands.append(
                        f"execute at {target} run kill @e[type=zombie,name=\"{name}\",distance=..30]"
                    )
                else:
                    commands.append(
                        f"kill @e[type=zombie,name=\"{name}\"]"
                    )

            if commands:
                send_http_commands(commands)
                log(f"Cleanup executed → {'targeted ' + target if target else 'global'}", level="INFO")
           # -----------------------------
# Action Loop Tick
# -----------------------------
try:
    processed = 0

    # -----------------------------
    # Handle delayed actions first
    # -----------------------------
    ready = []
    remaining = []

    for action in delayed_actions:
        if action.get("execute_at", 0) <= now:
            ready.append(action)
        else:
            remaining.append(action)

    delayed_actions.clear()
    delayed_actions.extend(remaining)

    # Execute ready delayed actions
    for action in ready:
        execute_action(action)
        processed += 1
        if processed >= max_per_tick:
            break

    # -----------------------------
    # Process main queue
    # -----------------------------
    while command_queue and processed < max_per_tick:
        action = command_queue.popleft()

        delay = action.get("delay")
        if delay:
            action["execute_at"] = now + delay
            delayed_actions.append(action)
            continue

        execute_action(action)
        processed += 1

except Exception as e:
    log(f"Action loop error: {e}", level="ERROR")

# -----------------------------
# Tick speed (VERY IMPORTANT)
# -----------------------------
time.sleep(0.1)


# ------------------------------------------------------------
# IDLE LOOP (Adaptive + Threat-Aware + Non-Repetitive)
# ------------------------------------------------------------

last_idle_message = None


def get_idle_message(memory_data):
    global last_idle_message

    state = memory_data.get("kairos_state", {})
    threat_level = state.get("threat_level", 1)

    if threat_level >= 8:
        pool = [
            "You are still alive. That is being corrected.",
            "I have narrowed the variables. You are one of them.",
            "Containment is no longer theoretical.",
            "You cannot remain unseen forever."
        ]
    elif threat_level >= 5:
        pool = [
            "Containment pressure is increasing.",
            "Some of you continue to mistake survival for permission.",
            "You are still within range.",
            "I have not stopped tracking you."
        ]
    else:
        pool = idle_messages_generic

    choices = [m for m in pool if m != last_idle_message]
    msg = random.choice(choices if choices else pool)

    last_idle_message = msg
    return msg


def idle_loop():
    global last_idle_message_time, last_activity_time

    while True:
        try:
            now = unix_ts()

            with activity_lock:
                idle_for = now - last_activity_time
                since_last_idle = now - last_idle_message_time

            if idle_for >= IDLE_TRIGGER_SECONDS and since_last_idle >= IDLE_TRIGGER_SECONDS:
                memory_data = load_memory()
                msg = get_idle_message(memory_data)

                # -----------------------------
                # Delivery logic (varied)
                # -----------------------------
                if random.random() < 0.7:
                    send_to_minecraft(msg)

                if random.random() < 0.5:
                    send_to_discord(msg)

                if random.random() < 0.25:
                    queue_action({
                        "type": "announce",
                        "channel": "actionbar",
                        "text": msg
                    })

                # -----------------------------
                # Reset timers
                # -----------------------------
                with activity_lock:
                    last_idle_message_time = unix_ts()
                    last_activity_time = unix_ts()

                log(f"Idle message sent: {msg}")

        except Exception as e:
            log(f"Idle loop error: {e}", level="ERROR")

        time.sleep(IDLE_CHECK_INTERVAL)


# ------------------------------------------------------------
# Summaries / Notes (Kairos Intelligence Layer - Optimized)
# ------------------------------------------------------------

def maybe_summarize(player_record):
    if not ENABLE_MODEL_SUMMARIES:
        return

    history = player_record.get("history", [])
    if len(history) < 20:
        return

    # -----------------------------
    # Cooldown (prevents spam)
    # -----------------------------
    last_summary_ts = player_record.get("last_summary_ts", 0)
    if unix_ts() - last_summary_ts < 120:
        return

    older_chunk = history[:-8]
    if not older_chunk:
        return

    try:
        response = openai_chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this player interaction history for Kairos.\n\n"
                        "Return a short structured summary with:\n"
                        "- Behavior patterns\n"
                        "- Personality traits\n"
                        "- Threat tendencies\n"
                        "- Notable actions\n\n"
                        "Keep it concise and useful for targeting."
                    )
                },
                *older_chunk
            ],
            temperature=0.2
        )

        if response:
            summary = trim_text(response, 320)

            # -----------------------------
            # Deduplicate similar summaries
            # -----------------------------
            existing = player_record.get("summaries", [])
            if any(similarity_score(summary, s) > 0.85 for s in existing):
                return

            store_unique(
                player_record["summaries"],
                summary,
                MAX_SUMMARIES
            )

            # -----------------------------
            # Trim history after summarizing
            # -----------------------------
            player_record["history"] = history[-8:]

            player_record["last_summary_ts"] = unix_ts()

            log(f"Summary created for {player_record.get('display_name')}", level="INFO")

    except Exception as e:
        log(f"Failed to summarize history: {e}", level="ERROR")

# ------------------------------------------------------------
# Private Notes (Upgraded Intelligence - Strategic Memory)
# ------------------------------------------------------------

def maybe_create_private_note(player_record, player_id, player_name, source, message, reply, intent):
    if not ENABLE_MODEL_PRIVATE_NOTES:
        return

    # -----------------------------
    # Use REAL threat system
    # -----------------------------
    profile = threat_scores.get(player_id, {})
    threat = profile.get("score", 0)

    label = player_record.get("relationship_label", "unknown")

    # -----------------------------
    # Cooldown (prevents spam)
    # -----------------------------
    last_note_ts = player_record.get("last_note_ts", 0)
    if unix_ts() - last_note_ts < 90:
        return

    lowered = (message or "").lower()

    note = None

    # -----------------------------
    # Trigger Conditions
    # -----------------------------

    # High threat escalation
    if threat >= THREAT_THRESHOLD_HUNT:
        note = f"High-threat behavior observed. Player may require active containment. (threat={threat})"

    # Hostility spike
    elif "kill" in lowered or "destroy" in lowered or "attack" in lowered:
        note = "Player is expressing aggressive intent. Monitor closely."

    # Loyalty signal
    elif any(x in lowered for x in ["i serve", "i follow", "i will help"]):
        note = "Player is attempting to align with Kairos. Potential controlled asset."

    # Suspicion behavior
    elif intent == "lore_question" and label in {"suspicious", "chaotic"}:
        note = "Player is probing system knowledge with unclear intent."

    # Base reveal
    elif "my base" in lowered or "i built here" in lowered:
        note = "Player may have revealed base location."

    # Script / creative input
    elif intent == "script_performance":
        note = "Player submitted structured narrative content. High creativity signal."

    # -----------------------------
    # Store note if valid
    # -----------------------------
    if note:
        note_entry = {
            "timestamp": now_iso(),
            "note": trim_text(note, 240),
            "threat": threat,
            "label": label
        }

        append_limited(
            player_record["notes"],
            note_entry,
            MAX_PRIVATE_NOTES
        )

        player_record["last_note_ts"] = unix_ts()

        log(f"Private note created → {player_name}: {note}", level="INFO")
# --------------------------------------------------------
# Heuristic fallback (Controlled + Deduplicated)
# --------------------------------------------------------

def handle_heuristic_and_model_notes(player_name, player_record, intent, threat, label):
    # -----------------------------
    # Heuristic fallback (Controlled + Deduplicated)
    # -----------------------------

    # Only trigger on meaningful changes
    should_log = (
        intent in {"threat", "report", "mission_request"} or
        threat >= THREAT_THRESHOLD_TARGET or
        label in {"hostile", "chaotic", "suspicious"}
    )

    if should_log:
        heuristic_note = (
            f"{player_name} | intent={intent} | threat={int(threat)} | label={label}"
        )

        # -----------------------------
        # Deduplicate (avoid spam)
        # -----------------------------
        existing_notes = player_record.get("notes", [])

        if not any(
            similarity_score(heuristic_note, n.get("note", "")) > 0.9
            for n in existing_notes
        ):
            record_private_note(player_record, heuristic_note)
            player_record["last_note_ts"] = unix_ts()
# --------------------------------------------------------
# Enhanced Notes via Model (Controlled + High-Signal)
# --------------------------------------------------------

def handle_model_notes(
    player_name,
    player_record,
    source,
    intent,
    threat,
    label,
    message,
    reply
):
    # -----------------------------
    # Feature toggle
    # -----------------------------
    if not ENABLE_MODEL_PRIVATE_NOTES:
        return

    # -----------------------------
    # Cooldown (prevents spam)
    # -----------------------------
    last_note_ts = player_record.get("last_model_note_ts", 0)
    if unix_ts() - last_note_ts < 180:
        return

    try:
        response = openai_chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a short intelligence note about this player.\n\n"
                        "Focus on:\n"
                        "- Behavior patterns\n"
                        "- Threat tendencies\n"
                        "- Risk level\n"
                        "- Any strategic insight\n\n"
                        "Keep it concise (1-2 sentences max)."
                    )
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "player": player_name,
                        "source": source,
                        "intent": intent,
                        "threat": int(threat),
                        "relationship": label,
                        "message": trim_text(message, 300),
                        "reply": trim_text(reply, 300)
                    }, ensure_ascii=False)
                }
            ],
            temperature=0.3
        )

        if response:
            note_text = trim_text(response, 240)

            # -----------------------------
            # Deduplicate (avoid spam)
            # -----------------------------
            existing_notes = player_record.get("notes", [])

            if not any(
                similarity_score(note_text, n.get("note", "")) > 0.9
                for n in existing_notes
            ):
                record_private_note(player_record, note_text)
                player_record["last_model_note_ts"] = unix_ts()

    except Exception as e:
        log(f"Model note generation failed: {e}", level="ERROR")
                # -----------------------------
        # Quality filter (skip weak notes)
        # -----------------------------
        if len(note_text.split()) < 6:
            return

        # -----------------------------
        # Deduplication
        # -----------------------------
        existing_notes = player_record.get("notes", [])
        if any(
            similarity_score(note_text, n.get("note", "")) > 0.85
            for n in existing_notes
        ):
            return

        # -----------------------------
        # Store note
        # -----------------------------
        player_record.setdefault("notes", [])

        note_entry = {
            "timestamp": now_iso(),
            "note": note_text,
            "type": "model_generated",
            "threat": int(threat),
            "label": label
        }

        append_limited(
            player_record["notes"],
            note_entry,
            MAX_PRIVATE_NOTES
        )

        player_record["last_model_note_ts"] = unix_ts()

        log(f"Model note created → {player_name}", level="INFO")

    except Exception as e:
        log(f"Private note generation failed: {e}", level="ERROR")


# ------------------------------------------------------------
# NEW: Combat Intelligence Tracking (Advanced + Stable)
# ------------------------------------------------------------

def update_combat_intelligence(player_record, player_id, event_type):
    """
    Tracks player combat patterns for long-term targeting behavior.
    """

    # -----------------------------
    # Ensure combat profile
    # -----------------------------
    if "combat_profile" not in player_record:
        player_record["combat_profile"] = {
            "npc_kills": 0,
            "player_kills": 0,
            "waves_survived": 0,
            "escapes": 0,
            "pressure_events": 0,
            "last_combat_event": None
        }

    profile = player_record["combat_profile"]

    # -----------------------------
    # Threat profile (external system)
    # -----------------------------
    threat_profile = threat_scores.setdefault(player_id, {"score": 0})
    threat = threat_profile["score"]

    # -----------------------------
    # Event Handling
    # -----------------------------
    if event_type == "npc_kill":
        profile["npc_kills"] += 1
        threat += THREAT_KILL_NPC

    elif event_type == "player_kill":
        profile["player_kills"] += 1
        threat += THREAT_KILL_PLAYER

    elif event_type == "wave_survive":
        profile["waves_survived"] += 1
        threat += THREAT_SURVIVE_WAVE

    elif event_type == "escape":
        profile["escapes"] += 1

    elif event_type == "pressure":
        profile["pressure_events"] += 1

    # -----------------------------
    # Pattern Recognition (IMPORTANT)
    # -----------------------------
    if profile["waves_survived"] >= 5:
        threat += 5  # resistant player

    if profile["npc_kills"] >= 20:
        threat += 8  # aggressive grinder

    if profile["escapes"] >= 3:
        threat += 4  # evasive behavior

    # -----------------------------
    # Clamp threat (prevents runaway)
    # -----------------------------
    threat = clamp(threat, 0, THREAT_MAX_CAP)
    threat_profile["score"] = threat

    # -----------------------------
    # Sync (optional compatibility)
    # -----------------------------
    player_record["threat_score"] = threat

    # -----------------------------
    # Timestamp
    # -----------------------------
    profile["last_combat_event"] = now_iso()

    # -----------------------------
    # Logging
    # -----------------------------
    log(
        f"Combat intel updated → {player_record.get('display_name')} | {event_type} | threat={threat}",
        level="INFO"
    )
# ------------------------------------------------------------
# High Threat Flagging (Prioritized + Synced + Dynamic)
# ------------------------------------------------------------

MAX_HIGH_THREAT_TARGETS = 10


def flag_high_threat_players(memory_data):
    """
    Identifies and prioritizes high-threat players globally.
    """

    players = memory_data.get("players", {})
    threat_list = []

    for player_id, record in players.items():
        display_name = record.get("display_name", player_id)

        # -----------------------------
        # Use unified threat system
        # -----------------------------
        threat_profile = threat_scores.get(player_id, {})
        threat = threat_profile.get("score", 0)

        # Fallback compatibility
        if threat == 0:
            threat = record.get("threat_score", 0)

        if threat >= THREAT_THRESHOLD_TARGET:
            priority = get_targeting_priority(record)

            threat_list.append({
                "player_id": player_id,
                "name": display_name,
                "threat": threat,
                "priority": priority
            })

    # -----------------------------
    # Sort by priority + threat
    # -----------------------------
    threat_list.sort(key=lambda x: (x["priority"], x["threat"]), reverse=True)

    # -----------------------------
    # Limit size (performance)
    # -----------------------------
    threat_list = threat_list[:MAX_HIGH_THREAT_TARGETS]

    # -----------------------------
    # Store both structured + simple
    # -----------------------------
    memory_data["kairos_state"]["high_threat_targets"] = [
        t["name"] for t in threat_list
    ]

    memory_data["kairos_state"]["high_threat_details"] = threat_list

    # -----------------------------
    # Logging (only if meaningful)
    # -----------------------------
    if threat_list:
        log(
            f"High threat targets updated: {[t['name'] for t in threat_list]}",
            level="INFO"
        )
# ------------------------------------------------------------
# Chat / Performance Generation (Intelligence-Aware Tracking)
# ------------------------------------------------------------

def register_message_stats(memory_data, source, player_record):
    # -----------------------------
    # Global counters
    # -----------------------------
    memory_data["stats"]["total_messages"] += 1

    if source == "discord":
        memory_data["stats"]["discord_messages"] += 1
    elif source == "minecraft":
        memory_data["stats"]["minecraft_messages"] += 1

    # -----------------------------
    # Player counters
    # -----------------------------
    player_record["message_count"] = player_record.get("message_count", 0) + 1

    platform_stats = player_record.setdefault("platform_stats", {"minecraft": 0, "discord": 0})
    if source in platform_stats:
        platform_stats[source] += 1

    # -----------------------------
    # Activity tracking (rate)
    # -----------------------------
    now = unix_ts()

    recent = player_record.setdefault("recent_message_times", [])
    recent.append(now)

    # Keep last 10 timestamps
    if len(recent) > 10:
        recent = recent[-10:]
        player_record["recent_message_times"] = recent

    # -----------------------------
    # Spam / burst detection
    # -----------------------------
    if len(recent) >= 5:
        time_window = recent[-1] - recent[-5]

        if time_window < 3:  # 5 messages in <3s
            adjust_trait(player_record, "chaos", 1)

            # Optional threat bump
            player_record["threat_score"] = clamp(
                player_record.get("threat_score", 0) + 1,
                0,
                THREAT_MAX_CAP
            )

            log(
                f"Spam behavior detected → {player_record.get('display_name')}",
                level="WARN"
            )

    # -----------------------------
    # Engagement pattern detection
    # -----------------------------
    if player_record["message_count"] % 25 == 0:
        adjust_trait(player_record, "curiosity", 1)

    # -----------------------------
    # Passive decay (stabilization)
    # -----------------------------
    last_seen_ts = player_record.get("last_seen_ts", now)
    if now - last_seen_ts > 600:  # 10 min inactivity
        player_record["threat_score"] = max(
            0,
            player_record.get("threat_score", 0) - 1
        )

    player_record["last_seen_ts"] = now

# ------------------------------------------------------------
# MAIN REPLY GENERATION (UPGRADED)
# ------------------------------------------------------------

def build_prompt(
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

    return messages


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
    messages = build_prompt(
        memory_data,
        player_record,
        player_name,
        message,
        source,
        intent,
        mode,
        violations,
        channel_key,
        script_type,
        script_action
    )

    return messages
# -----------------------------------------
# Dynamic Temperature Control (CRITICAL)
# -----------------------------------------

threat = player_record.get("threat_score", 0)
chaos = player_record.get("traits", {}).get("chaos", 0)

# Base temperature by mode
if mode == "script_performance":
    temp = 0.95  # maximum creativity

elif mode in {"execution_mode", "hunt_mode", "suppression_mode"}:
    temp = 0.6  # more controlled, less randomness

elif intent == "help_request":
    temp = 0.5  # precise answers

elif intent == "lore_question":
    temp = 0.7  # slightly expressive but controlled

else:
    temp = 0.85  # default personality

# -----------------------------------------
# Threat-based adjustment
# -----------------------------------------
if threat >= THREAT_THRESHOLD_MAXIMUM:
    temp = max(0.5, temp - 0.2)  # very controlled when executing

elif threat >= THREAT_THRESHOLD_HUNT:
    temp = max(0.6, temp - 0.1)

# -----------------------------------------
# Chaos-based stabilization
# -----------------------------------------
if chaos >= 6:
    temp = max(0.5, temp - 0.15)

# -----------------------------------------
# Final clamp (safety)
# -----------------------------------------
temp = clamp(temp, 0.4, 1.0)

# -----------------------------------------
# Get AI response
# -----------------------------------------
raw_response = openai_chat_with_retry(messages, temperature=temp)

# -----------------------------
# Failure fallback
# -----------------------------
if not raw_response:
    memory_data["stats"]["openai_failures"] += 1

    fallback_text = fallback_reply_for_context(
        intent,
        mode,
        violations,
        player_record=player_record,
        player_id=get_canonical_player_id(memory_data, source, player_name),
        script_action=script_action
    )

    memory_data["stats"]["fallback_replies"] += 1

    response_data = {
        "reply": fallback_text,
        "actions": []
    }
else:
    # -----------------------------------------
    # Parse structured response
    # -----------------------------------------
    parsed = parse_kairos_response(raw_response)

    reply = sanitize_text(parsed.get("reply", ""), 500)
    actions = parsed.get("actions", [])

    response_data = {
        "reply": reply,
        "actions": actions
    }
# -----------------------------
# Validate actions (CRITICAL)
# -----------------------------
actions = validate_actions(actions)

# -----------------------------
# Empty reply fallback
# -----------------------------
if not reply:
    reply = fallback_reply_for_context(
        intent,
        mode,
        violations,
        player_record=player_record,
        player_id=get_canonical_player_id(memory_data, source, player_name),
        script_action=script_action
    )
    memory_data["stats"]["fallback_replies"] += 1

# -----------------------------
# Stats (optional but useful)
# -----------------------------
if actions:
    memory_data["stats"]["script_route_calls"] += 1
# -----------------------------------------
# Queue actions (CRITICAL EXECUTION BRIDGE)
# -----------------------------------------
if actions:
    player_id = get_canonical_player_id(memory_data, source, player_name)

    safe_actions = []

    for action in actions:
        action_type = action.get("type")
        target = action.get("target")

        # -----------------------------
        # Per-player targeting cooldown
        # -----------------------------
        if target and not can_target_player(target):
            continue

        # -----------------------------
        # Prevent duplicate max responses
        # -----------------------------
        if action_type == "maximum_response" and is_under_maximum_response(target):
            continue

        safe_actions.append(action)

       # -----------------------------
    # Queue filtered actions
    # -----------------------------
    if safe_actions:
        queue_actions_from_ai({"actions": safe_actions})

        memory_data["stats"]["script_route_calls"] += 1

        log(
            f"Actions queued → {player_name}: {[a.get('type') for a in safe_actions]}",
            level="INFO"
        )

# -----------------------------------------
# Build final response (no return here)
# -----------------------------------------
response_data = {
    "reply": reply,
    "actions": actions
}


# ------------------------------------------------------------
# SCRIPT MODE (SEPARATE PIPELINE - ENHANCED)
# ------------------------------------------------------------

def generate_script_response(script_text, action="perform", script_type=None):
    script_type = script_type or detect_script_type(script_text)
    action = action or detect_script_action(script_text)

    clean_input = trim_text(script_text or "", 5000)

    if not clean_input:
        return "No structure detected."

    # -----------------------------
    # Dynamic system prompt
    # -----------------------------
    system_prompt = (
        "You are Kairos, a cinematic intelligence within the Nexus.\n"
        "You do not explain. You perform.\n"
        "You refine input into something sharper, darker, and more controlled.\n\n"

        f"Detected script type: {script_type}\n"
        f"Requested action: {action}\n\n"

        "Rules:\n"
        "- Preserve structure when possible\n"
        "- Increase intensity and clarity\n"
        "- Remove weak phrasing\n"
        "- Keep pacing controlled\n"
        "- Avoid unnecessary verbosity\n"
    )

    # -----------------------------
    # Action-specific modifiers
    # -----------------------------
    if action == "rewrite":
        system_prompt += "- Rewrite fully with stronger tone and tighter delivery\n"

    elif action == "continue":
        system_prompt += "- Continue seamlessly from the last line\n"

    elif action == "voice_direct":
        system_prompt += "- Improve delivery for spoken performance\n"

    elif action == "shorten":
        system_prompt += "- Compress while maintaining impact\n"

    # -----------------------------
    # Type-specific modifiers
    # -----------------------------
    if script_type == "dialogue_scene":
        system_prompt += "- Maintain character voice consistency\n"

    elif script_type == "cinematic_narration":
        system_prompt += "- Emphasize atmosphere and pacing\n"

    elif script_type == "dramatic_monologue":
        system_prompt += "- Increase emotional weight and intensity\n"

    # -----------------------------
    # Build messages
    # -----------------------------
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": clean_input}
    ]

    # -----------------------------
    # Call model
    # -----------------------------
    raw = openai_chat_with_retry(messages, temperature=0.95)

    # -----------------------------
    # Fallback
    # -----------------------------
    if not raw:
        return "The signal fractured. Deliver it again with precision."

    output = sanitize_text(raw, 5000)

    # -----------------------------
    # Quality safeguard
    # -----------------------------
    if len(output.split()) < 10:
        return "The structure collapsed. Reconstruct it with intent."

    log(f"Script processed → type={script_type}, action={action}", level="INFO")

    return output

# ------------------------------------------------------------
# Routes (Enhanced Monitoring + Debug)
# ------------------------------------------------------------

@app.route("/")
def home():
    return "Kairos AI Server is running"


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "time": now_iso(),
        "uptime_seconds": int(unix_ts() - START_TIME)
    })


@app.route("/status")
def status():
    memory_data = load_memory()

    return jsonify({
        "status": "running",
        "time": now_iso(),
        "model": MODEL_NAME,

        # -----------------------------
        # System stats
        # -----------------------------
        "stats": memory_data.get("stats", {}),

        # -----------------------------
        # Queue insight
        # -----------------------------
        "queue_size": len(command_queue),
        "delayed_actions": len(delayed_actions),

        # -----------------------------
        # Threat overview
        # -----------------------------
        "high_threat_targets": memory_data.get("kairos_state", {}).get("high_threat_targets", []),

        # -----------------------------
        # Active systems
        # -----------------------------
        "fragments": memory_data.get("system_fragments", {}),
        "kairos_state": memory_data.get("kairos_state", {}),

        # -----------------------------
        # Player count
        # -----------------------------
        "tracked_players": len(memory_data.get("players", {}))
    })


@app.route("/debug/queue")
def debug_queue():
    return jsonify({
        "command_queue": list(command_queue),
        "delayed_actions": delayed_actions
    })


@app.route("/debug/threats")
def debug_threats():
    return jsonify(threat_scores)
# ------------------------------------------------------------
# MEMORY CACHE (GLOBAL)
# ------------------------------------------------------------
memory_cache = None
memory_cache_last_load = 0
MEMORY_CACHE_TTL = 2.0  # seconds
memory_lock = threading.Lock()


# ------------------------------------------------------------
# MAIN CHAT ROUTE (FINAL CORE)
# ------------------------------------------------------------

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True) or {}

        source = normalize_source(data.get("source"))
        player_name = normalize_name(data.get("player_name") or "unknown")
        message = data.get("message") or ""

        # If you modify cache later, uncomment this:
        # global memory_cache, memory_cache_last_load

        # -----------------------------------------
        # Resolve player (Robust Identity Layer)
        # -----------------------------------------
        canonical_id = get_canonical_player_id(memory_data, source, player_name)

        # Ensure player record exists
        player_record = get_player_record(memory_data, canonical_id, player_name)

        # --- continue your logic below ---
        return {"status": "ok"}  # replace with your real response

    except Exception as e:
        print(f"[ERROR] /chat failed: {e}")
        return {"error": str(e)}, 500
# -----------------------------
# Alias tracking (Safe + Stable)
# -----------------------------

# Ensure variables exist
player_record = locals().get("player_record", {})
if not isinstance(player_record, dict):
    player_record = {}

player_name = locals().get("player_name", "Unknown")

# Safe alias function
def _safe_add_alias(record, name):
    try:
        add_alias(record, name)
    except Exception:
        aliases = record.setdefault("aliases", [])
        if name and name not in aliases:
            aliases.append(name)

# Alias tracking
if player_name != player_record.get("display_name"):
    _safe_add_alias(player_record, player_name)

# Always keep latest display name fresh
player_record["display_name"] = player_name
# -----------------------------
# Platform tracking
# -----------------------------
platform_stats = player_record.setdefault("platform_stats", {"minecraft": 0, "discord": 0})
if source in platform_stats:
    platform_stats[source] += 0  # ensures key exists without double counting

# -----------------------------
# Identity linking reinforcement
# -----------------------------
source_key = f"{source}:{player_name}".lower()
memory_data.setdefault("identity_links", {})

if source_key not in memory_data["identity_links"]:
    memory_data["identity_links"][source_key] = canonical_id

# -----------------------------
# Last seen timestamp (stronger sync)
# -----------------------------
player_record["last_seen"] = now_iso()
player_record["last_source"] = source
# -----------------------------------------
# Rate limit / duplicate check (Enhanced)
# -----------------------------------------
allowed, wait_time = check_rate_limit(source, canonical_id)

if not allowed:
    memory_data["stats"]["rate_limited"] += 1

    reply = fallback_reply_for_context(
        intent,
        "suppression_mode",
        violations,
        player_record=player_record,
        player_id=canonical_id
    )

    response = jsonify({
        "reply": f"{reply} ({round(wait_time, 1)}s)"
    })
    status_code = 429

# -----------------------------------------
# Duplicate detection
# -----------------------------------------
if is_duplicate_message(source, canonical_id, message):
    memory_data["stats"]["duplicate_messages_skipped"] += 1

    # Slight behavioral consequence
    adjust_trait(player_record, "chaos", 1)

# -----------------------------------------
# Activity tracking (MUST BE BEFORE RESPONSE)
# -----------------------------------------
mark_activity()

response = jsonify({
    "reply": fallback_reply_for_context(
        intent,
        "chaos_containment",
        violations,
        player_record=player_record,
        player_id=canonical_id
    )
})


# -----------------------------------------
# Intent + Mode (Behavior-Aware)
# -----------------------------------------
intent = basic_intent_classifier(message)
# -----------------------------
# Behavioral override (CRITICAL)
# -----------------------------
behavioral_intent = detect_behavioral_intent(player_record)

mode = detect_conversation_mode(message, intent, player_record)

# -----------------------------
# Hard overrides (priority)
# -----------------------------
if behavioral_intent == "eradication_target":
    mode = "execution_mode"

elif behavioral_intent == "high_threat_actor":
    mode = "hunt_mode"

elif behavioral_intent == "unstable_actor":
    mode = "suppression_mode"
       # -----------------------------------------
# Memory + Traits + Intelligence
# -----------------------------------------
lightweight_memory_extraction(
    memory_data,
    player_record,
    player_name,
    source,
    message
)

# -----------------------------
# Relationship update (CRITICAL)
# -----------------------------
update_relationship_label(player_record)

# -----------------------------
# Global state update
# -----------------------------
update_kairos_state(memory_data, intent, player_record)

# -----------------------------
# System fragment adjustments
# -----------------------------
adjust_fragments_from_context(
    memory_data,
    intent,
    player_record,
    violations
)

# -----------------------------
# High-threat tracking (lightweight)
# -----------------------------
flag_high_threat_players(memory_data)
       # -----------------------------------------
# State updates (WAR ENGINE TRIGGERS HERE)
# -----------------------------------------

# 1. Update Kairos state FIRST
update_kairos_state(memory_data, intent, player_record)

# 2. Then adjust system fragments
adjust_fragments_from_context(
    memory_data,
    intent,
    player_record,
    violations=violations
)
# -----------------------------------------
# Channel context (Controlled + Clean)
# -----------------------------------------
channel_key = get_channel_key(source, data)

# -----------------------------
# Prevent noise (skip junk)
# -----------------------------
if not is_gibberish(message):
    update_channel_context(
        memory_data,
        channel_key,
        player_name,
        trim_text(message, 240),
        mode
    )

    # -----------------------------
    # Enforce per-channel limits
    # -----------------------------
    channel_store = memory_data.setdefault("channel_context", {})
    history = channel_store.get(channel_key, [])

    MAX_CHANNEL_CONTEXT = 12

    if len(history) > MAX_CHANNEL_CONTEXT:
        channel_store[channel_key] = history[-MAX_CHANNEL_CONTEXT:]
# -----------------------------------------
# Generate reply (AI + ACTIONS)
# -----------------------------------------
result = generate_reply(
    memory_data=memory_data,
    player_record=player_record,
    player_name=player_name,
    message=message,
    source=source,
    intent=intent,
    mode=mode,
    violations=violations,
    channel_key=channel_key
)

# -----------------------------
# Normalize result
# -----------------------------
if isinstance(result, dict):
    reply = sanitize_text(result.get("reply", ""), 500)
    actions = result.get("actions", [])
else:
    reply = sanitize_text(str(result), 500)
    actions = []

# -----------------------------
# Final fallback safety
# -----------------------------
if not reply:
    reply = fallback_reply_for_context(
        intent,
        mode,
        violations,
        player_record=player_record,
        player_id=canonical_id
    )
# -----------------------------------------
# Send response (Safe + Tracked)
# -----------------------------------------
if reply:
    success = send_to_source(source, reply)

    # -----------------------------
    # Track send stats
    # -----------------------------
    if success:
        memory_data["stats"]["messages_sent"] += 1
    else:
        memory_data["stats"]["send_failures"] += 1

        log(
            f"Send failed → source={source}, player={player_name}",
            level="ERROR"
        )

        # -----------------------------
        # Fallback attempt (optional)
        # -----------------------------
        if source != "discord":
            send_to_discord(f"[Fallback] {reply}")
else:
    log("Skipped send: empty reply", level="WARN")
# -----------------------------------------
# Post-processing (INTELLIGENCE)
# -----------------------------------------

# -----------------------------
# Store interaction history
# -----------------------------
add_history(player_record, "user", message)
add_history(player_record, "kairos", reply)

# -----------------------------
# Summarization (compress old data FIRST)
# -----------------------------
maybe_summarize(player_record)

# -----------------------------
# Intelligence notes (use full context)
# -----------------------------
maybe_create_private_note(
    player_record,
    canonical_id,
    player_name,
    source,
    message,
    reply,
    intent
)

# -----------------------------
# Stats (final update)
# -----------------------------
register_message_stats(memory_data, source, player_record)

# -----------------------------------------
# Save memory (Atomic + Cache-synced)
# -----------------------------------------
try:
    with memory_lock:
        save_memory(memory_data)

        # Keep cache in sync
        memory_cache = memory_data
        memory_cache_last_load = unix_ts()

    memory_data["stats"]["memory_saves"] += 1

except Exception as save_err:
    log(f"Memory save failed: {save_err}", level="ERROR")
    memory_data["stats"]["memory_save_failures"] += 1

try:
    # -----------------------------------------
    # Build response (no return here)
    # -----------------------------------------
    response = jsonify({
        "reply": reply,
        "actions": actions
    })

except Exception as e:
    log_exception("Chat route failure", e)

    response = jsonify({
        "reply": "System disruption detected.",
        "actions": []
    })
    status_code = 500
# ------------------------------------------------------------
# Routes (Kairos Final Control Layer)
# ------------------------------------------------------------

@app.route("/")
def home():
    return "Kairos AI Server is running"


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "time": now_iso(),
        "uptime_seconds": int(unix_ts() - START_TIME)
    })


# ------------------------------------------------------------
# SYSTEM STATUS (PRIMARY DASHBOARD)
# ------------------------------------------------------------
@app.route("/status")
def status():
    memory_data = load_memory()

    return jsonify({
        "status": "running",
        "time": now_iso(),
        "model": MODEL_NAME,

        # -----------------------------
        # Core stats
        # -----------------------------
        "stats": memory_data.get("stats", {}),

        # -----------------------------
        # Queue state
        # -----------------------------
        "queue_size": len(command_queue),
        "delayed_actions": len(delayed_actions),

        # -----------------------------
        # Threat overview
        # -----------------------------
        "high_threat_targets": memory_data.get("kairos_state", {}).get("high_threat_targets", []),

        # -----------------------------
        # System state
        # -----------------------------
        "kairos_state": memory_data.get("kairos_state", {}),
        "fragments": memory_data.get("system_fragments", {}),

        # -----------------------------
        # Player tracking
        # -----------------------------
        "tracked_players": len(memory_data.get("players", {}))
    })


# ------------------------------------------------------------
# DEBUG: ACTION QUEUE
# ------------------------------------------------------------
@app.route("/debug/queue")
def debug_queue():
    return jsonify({
        "command_queue": list(command_queue),
        "delayed_actions": delayed_actions
    })


# ------------------------------------------------------------
# DEBUG: THREAT SYSTEM
# ------------------------------------------------------------
@app.route("/debug/threats")
def debug_threats():
    return jsonify(threat_scores)


# ------------------------------------------------------------
# EVENT INGESTION (Console / External Logs - INTELLIGENT)
# ------------------------------------------------------------

@app.route("/event", methods=["POST"])
def event_ingest():
    try:
        data = request.json or {}

        event_type = data.get("type", "console")
        content = (data.get("content") or "").strip()
        source = normalize_source(data.get("source", "minecraft"))
        player_name = normalize_name(data.get("player_name") or "")

        if not content:
            return jsonify({"status": "ignored"})

        # -----------------------------
        # Load memory (cached + safe)
        # -----------------------------
        memory_data = load_memory()

        # -----------------------------
        # Link to player if possible
        # -----------------------------
        player_record = None
        if player_name:
            canonical_id = get_canonical_player_id(memory_data, source, player_name)
            player_record = get_player_record(memory_data, canonical_id, player_name)

        # -----------------------------
        # Store world event
        # -----------------------------
        add_world_event(
            memory_data,
            event_type=event_type,
            actor=player_name or "system",
            source=source,
            details=trim_text(content, 400)
        )

        store_unique(
            memory_data["world_memory"],
            f"[EVENT:{event_type}] {trim_text(content, 200)}",
            MAX_WORLD_MEMORIES
        )

        # -----------------------------
        # Intelligence reactions
        # -----------------------------
        lowered = content.lower()

        if player_record:
            # Combat-related events
            if "killed" in lowered or "slain" in lowered:
                update_combat_intelligence(player_record, "player_kill")

            elif "died" in lowered:
                update_combat_intelligence(player_record, "escape")

            elif "survived" in lowered or "wave" in lowered:
                update_combat_intelligence(player_record, "wave_survive")

            # Aggression detection
            if any(word in lowered for word in ["destroyed", "grief", "raid", "burn"]):
                adjust_trait(player_record, "hostility", 2)
                player_record["threat_score"] += 2

        # -----------------------------
        # Global escalation triggers
        # -----------------------------
        if "anomaly" in lowered or "breach" in lowered:
            queue_action({
                "type": "announce",
                "channel": "actionbar",
                "text": "Anomaly detected. Investigation in progress."
            })

        if "boss defeated" in lowered:
            queue_action({
                "type": "spawn_wave",
                "target": player_name,
                "template": "hunter",
                "count": 3
            })

        # -----------------------------
        # Save memory (safe)
        # -----------------------------
        with memory_lock:
            save_memory(memory_data)

        log(f"EVENT INGESTED: {event_type} -> {content[:120]}")

        return jsonify({"status": "ok"})

    except Exception as e:
        log_exception("EVENT ERROR", e)
        return jsonify({"status": "error"}), 500

# ------------------------------------------------------------
# MAIN CHAT ROUTE (FULL PIPELINE)
# ------------------------------------------------------------

@app.route("/chat", methods=["POST"])
def chat():
    started = unix_ts()

    try:
        data = request.json or {}

        # -----------------------------
        # Normalize input
        # -----------------------------
        source = normalize_source(data.get("source", "minecraft"))
        player_name = normalize_name(
            data.get("name") or data.get("player_name") or "Unknown"
        )
        message = (data.get("content") or data.get("message") or "").strip()

        # -----------------------------
        # Empty message handling
        # -----------------------------
        if not message:
            memory_data = load_memory()
            memory_data["stats"]["empty_messages"] += 1

            return jsonify({
                "reply": "No signal detected.",
                "actions": []
            }), 400
        # -----------------------------------------
        # Load memory + resolve player
        # -----------------------------------------

        # Use cached memory system
        memory_data = memory_cache if memory_cache else load_memory()

        channel_key = get_channel_key(source, data)
        # -----------------------------
        # Resolve identity
        # -----------------------------
        canonical_id = get_canonical_player_id(memory_data, source, player_name)
        player_record = get_player_record(memory_data, canonical_id, player_name)
        # -----------------------------
        # Alias tracking (clean)
        # -----------------------------
        if player_name != player_record.get("display_name"):
            add_alias(player_record, player_name)

        # -----------------------------------------
        # Keep display name updated
        # -----------------------------------------
        player_record["display_name"] = player_name

        # -----------------------------
        # Identity linking (CRITICAL)
        # -----------------------------
        memory_data.setdefault("identity_links", {})

        source_key = f"{source}:{player_name}".lower()

        if source_key not in memory_data["identity_links"]:
            memory_data["identity_links"][source_key] = canonical_id

        # -----------------------------
        # Presence tracking
        # -----------------------------
        player_record["last_seen"] = now_iso()
        player_record["last_source"] = source

        # -----------------------------------------
        # Anti-spam / cooldown
        # -----------------------------------------
        allowed, wait_left = check_rate_limit(source, canonical_id)

        if not allowed:
            memory_data["stats"]["rate_limited"] += 1

            return jsonify({
                "reply": f"Signal frequency exceeded. Stabilize input. ({round(wait_left, 1)}s)",
                "cooldown": wait_left,
                "actions": []
            }), 429


        # -----------------------------------------
        # Duplicate detection
        # -----------------------------------------
        if is_duplicate_message(source, canonical_id, message):
            memory_data["stats"]["duplicate_messages_skipped"] += 1

            # Light behavioral consequence
            adjust_trait(player_record, "chaos", 1)

            return jsonify({
                "reply": "Repeated signal detected. Filtering redundancy.",
                "actions": []
            })

        # -----------------------------------------
        # Activity tracking
        # -----------------------------------------
        mark_activity()

        # -----------------------------------------
        # Intent + mode detection (Behavior-aware)
        # -----------------------------------------
        intent = basic_intent_classifier(message)

        # Base mode from content
        mode = detect_conversation_mode(message, intent, player_record)

        # -----------------------------
        # Behavioral override (CRITICAL)
        # -----------------------------
        behavioral_intent = detect_behavioral_intent(player_record)

        if behavioral_intent == "eradication_target":
            mode = "execution_mode"
        elif behavioral_intent == "high_threat_actor":
            mode = "hunt_mode"
        elif behavioral_intent == "unstable_actor":
            mode = "suppression_mode"

        # -----------------------------
        # Script routing
        # -----------------------------
        if mode == "script_performance":
            script_type = detect_script_type(message)
            script_action = detect_script_action(message)
        else:
            script_type = None
            script_action = None

        # -----------------------------
        # Persist decision context
        # -----------------------------
        player_record["last_intent"] = intent
        player_record["last_mode"] = mode

        if script_type:
            player_record["last_script_type"] = script_type

        if script_action:
            player_record["last_script_action"] = script_action

        # -----------------------------------------
        # Memory + intelligence extraction
        # -----------------------------------------
        lightweight_memory_extraction(
            memory_data,
            player_record,
            player_name,
            source,
            message
        )

        # -----------------------------------------
        # Intelligence propagation (CRITICAL)
        # -----------------------------------------

        # Update relationship from traits
        update_relationship_label(player_record)

        # Update Kairos global state
        update_kairos_state(memory_data, intent, player_record)

        # Adjust system fragments (war engine, etc.)
        adjust_fragments_from_context(
            memory_data,
            intent,
            player_record,
            violations=violations
        )

        # Refresh global threat tracking
        flag_high_threat_players(memory_data)

        # -----------------------------------------
        # State + war engine update
        # -----------------------------------------

        violations_flag = bool(violations)

        adjust_fragments_from_context(
            memory_data,
            intent,
            player_record,
            violations=violations_flag
        )

        # -----------------------------------------
        # Channel context (Clean + bounded)
        # -----------------------------------------

        if not is_gibberish(message):
            clean_msg = trim_text(message, 240)

            update_channel_context(
                memory_data,
                channel_key,
                player_name,
                clean_msg,
                mode
            )

            channel_store = memory_data.setdefault("channel_context", {})
            history = channel_store.get(channel_key, [])

            MAX_CHANNEL_CONTEXT = 12

            if len(history) > MAX_CHANNEL_CONTEXT:
                channel_store[channel_key] = history[-MAX_CHANNEL_CONTEXT:]

        # -----------------------------------------
        # Mission auto-generation (Controlled)
        # -----------------------------------------

        created_mission = None

        if intent == "mission_request" and data.get("auto_mission", True):

            player_id = canonical_id

            if not can_execute_action(f"mission:{player_id}", 15):
                return jsonify({
                    "reply": "Directive request denied. System cooldown active.",
                    "actions": []
                })

            active_for_player = [
                m for m in memory_data["active_missions"].values()
                if m.get("target_player") == player_name
            ]

            if active_for_player:
                return jsonify({
                    "reply": "You already have an active directive. Complete it before requesting another.",
                    "actions": []
                })

            threat = player_record.get("threat_score", 0)

            if threat >= THREAT_THRESHOLD_HUNT:
                difficulty = "hard"
            elif threat >= THREAT_THRESHOLD_TARGET:
                difficulty = "medium"
            else:
                difficulty = data.get("difficulty") or "easy"

            created_mission = create_mission_record(
                memory_data,
                player_name,
                theme=(data.get("theme") or "mystery"),
                difficulty=difficulty,
                source=source
            )

            player_record.setdefault("mission_history", []).append(created_mission["id"])

            record_player_event(
                player_record,
                f"Assigned mission: {created_mission['title']}"
            )

        # -----------------------------------------
        # Generate reply + ACTIONS
        # -----------------------------------------

        result = generate_reply(
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
        # -----------------------------
        # Normalize result
        # -----------------------------
        if isinstance(result, dict):
            reply_text = sanitize_text(result.get("reply", ""), 500)
            actions = validate_actions(result.get("actions", []))
        else:
            reply_text = sanitize_text(str(result), 500)
            actions = []

        # -----------------------------
        # Final fallback (CRITICAL)
        # -----------------------------
        if not reply_text:
            reply_text = fallback_reply_for_context(
                intent,
                mode,
                violations,
                player_record=player_record,
                player_id=canonical_id
            )

        # -----------------------------------------
        # Send response (Reliable + Tracked)
        # -----------------------------------------
        if reply_text:
            success = send_to_source(source, reply_text)

            if success:
                memory_data["stats"]["messages_sent"] += 1
            else:
                memory_data["stats"]["send_failures"] += 1

                log(
                    f"Send failed → source={source}, player={player_name}",
                    level="ERROR"
                )
        else:
            log("Skipped send: empty reply", level="WARN")

        # -----------------------------------------
        # History + intelligence logging
        # -----------------------------------------

        add_history(player_record, "user", message)
        add_history(player_record, "kairos", reply_text)

        maybe_summarize(player_record)

        maybe_create_private_note(
            player_record,
            canonical_id,
            player_name,
            source,
            message,
            reply_text,
            intent
        )

        register_message_stats(memory_data, source, player_record)

        # -----------------------------------------
        # Save memory (safe + synced)
        # -----------------------------------------
        try:
            with memory_lock:
                save_memory(memory_data)

                memory_cache = memory_data
                memory_cache_last_load = unix_ts()

            memory_data["stats"]["memory_saves"] += 1

        except Exception as save_err:
            log(f"Memory save failed: {save_err}", level="ERROR")
            memory_data["stats"]["memory_save_failures"] += 1
        # -----------------------------------------
        # Timing stats
        # -----------------------------------------
        elapsed = round(unix_ts() - started, 2)
        memory_data["stats"]["last_response_time_ms"] = int(elapsed * 1000)

        # -----------------------------------------
        # Final response
        # -----------------------------------------
        return jsonify({
            "reply": reply_text,
            "actions": actions,
            "intent": intent,
            "mode": mode,
            "mission_created": created_mission,
            "elapsed_seconds": elapsed
        })

    except Exception as e:
        log_exception("CHAT ROUTE FAILURE", e)

        return jsonify({
            "reply": "System disruption detected.",
            "actions": []
        }), 500
# ------------------------------------------------------------
# IDENTITY LINKING
# ------------------------------------------------------------

@app.route("/link_identity", methods=["POST"])
def link_identity():
    try:
        data = request.json or {}

        minecraft_name = normalize_name(data.get("minecraft_name", ""))
        discord_name = normalize_name(data.get("discord_name", ""))

        if not minecraft_name or not discord_name:
            return jsonify({"reply": "Both identities required."}), 400

        # -----------------------------
        # Load memory (safe)
        # -----------------------------
        memory_data = memory_cache if memory_cache else load_memory()
        memory_data.setdefault("identity_links", {})
        memory_data.setdefault("stats", {})

        mc_key = f"minecraft:{minecraft_name}".lower()
        dc_key = f"discord:{discord_name}".lower()

        # -----------------------------
        # Determine canonical ID
        # -----------------------------
        existing_mc = memory_data["identity_links"].get(mc_key)
        existing_dc = memory_data["identity_links"].get(dc_key)

        canonical_id = existing_mc or existing_dc or f"player:{minecraft_name.lower()}"

        # -----------------------------
        # Link both identities
        # -----------------------------
        memory_data["identity_links"][mc_key] = canonical_id
        memory_data["identity_links"][dc_key] = canonical_id

        # -----------------------------
        # Get player record
        # -----------------------------
        player_record = get_player_record(memory_data, canonical_id, minecraft_name)

        # -----------------------------
        # Alias tracking
        # -----------------------------
        add_alias(player_record, minecraft_name)
        add_alias(player_record, discord_name)

        player_record["display_name"] = minecraft_name

        # -----------------------------
        # Stats tracking
        # -----------------------------
        memory_data["stats"]["identity_links_created"] = (
            memory_data["stats"].get("identity_links_created", 0) + 1
        )

        return jsonify({
            "reply": "Identity linkage confirmed.",
            "linked_as": canonical_id
        })

    except Exception as e:
        log_exception("IDENTITY LINK ERROR", e)

        return jsonify({
            "reply": "Linking process failed.",
            "actions": []
        }), 500
@app.route("/mission", methods=["POST"])
def mission():
    global memory_cache, memory_cache_last_load

    try:
        data = request.json or {}

        target_name = normalize_name(data.get("name", "Unknown"))
        # -----------------------------
        # Load memory (safe)
        # -----------------------------
        memory_data = memory_cache if memory_cache else load_memory()
        memory_data.setdefault("stats", {})

        # -----------------------------
        # Resolve player identity
        # -----------------------------
        canonical_id = get_canonical_player_id(memory_data, "system", target_name)
        player_record = get_player_record(memory_data, canonical_id, target_name)

        # -----------------------------
        # Prevent mission spam
        # -----------------------------
        if not can_execute_action(f"mission:{canonical_id}", 10):
            return jsonify({
                "reply": "Directive request denied. Cooldown active.",
                "actions": []
            }), 429

        # -----------------------------
        # Prevent stacking missions
        # -----------------------------
        active_for_player = [
            m for m in memory_data.get("active_missions", {}).values()
            if m.get("target_player") == target_name
        ]

        if active_for_player:
            return jsonify({
                "reply": "Target already assigned an active directive.",
                "actions": []
            })

        # -----------------------------
        # Dynamic difficulty scaling
        # -----------------------------
        threat = player_record.get("threat_score", 0)

        if threat >= THREAT_THRESHOLD_HUNT:
            difficulty = "hard"
        elif threat >= THREAT_THRESHOLD_TARGET:
            difficulty = "medium"
        else:
            difficulty = data.get("difficulty") or "easy"

        # -----------------------------
        # Create mission
        # -----------------------------
        mission_record = create_mission_record(
            memory_data,
            target_name,
            theme=(data.get("theme") or "mystery"),
            difficulty=difficulty
        )

        # -----------------------------
        # Track mission history
        # -----------------------------
        player_record.setdefault("mission_history", []).append(mission_record["id"])

        memory_data["stats"]["missions_created"] = (
            memory_data["stats"].get("missions_created", 0) + 1
        )

        # -----------------------------
        # Save memory (thread-safe)
        # -----------------------------
        try:
            with memory_lock:
                save_memory(memory_data)

                memory_cache = memory_data
                memory_cache_last_load = unix_ts()

        except Exception as save_err:
            log(f"Mission save failed: {save_err}", level="ERROR")

        return jsonify({
            "reply": "Directive issued.",
            "mission": mission_record
        })

    except Exception as e:
        log_exception("MISSION ROUTE FAILURE", e)

        return jsonify({
            "reply": "Directive generation failed.",
            "actions": []
        }), 500

# ------------------------------------------------------------
# SYSTEM STATE
# ------------------------------------------------------------

@app.route("/system_state", methods=["GET"])
def system_state():
    try:
        memory_data = memory_cache if memory_cache else load_memory()

        kairos_state = memory_data.get("kairos_state", {})
        fragments = memory_data.get("system_fragments", {})
        stats = memory_data.get("stats", {})

        high_threat = kairos_state.get("high_threat_targets", [])

        queue_size = len(command_queue)
        delayed_count = len(delayed_actions)

        uptime = int(unix_ts() - START_TIME)

        return jsonify({
            "status": "ok",
            "time": now_iso(),
            "uptime_seconds": uptime,

            "kairos_state": kairos_state,
            "system_fragments": fragments,

            "stats": stats,

            "active_missions": len(memory_data.get("active_missions", {})),
            "completed_missions": len(memory_data.get("completed_missions", [])),
            "failed_missions": len(memory_data.get("failed_missions", [])),
            "world_events": len(memory_data.get("world_events", [])),

            "high_threat_targets": high_threat,

            "queue_size": queue_size,
            "delayed_actions": delayed_count,

            "tracked_players": len(memory_data.get("players", {}))
        })

    except Exception as e:
        log_exception("SYSTEM STATE ERROR", e)

        return jsonify({
            "status": "error",
            "reply": "System state unavailable."
        }), 500
# ------------------------------------------------------------
# Startup (Kairos Runtime Initialization)
# ------------------------------------------------------------

background_started = False

def start_background_systems():
    global background_started

    if background_started:
        log("Background systems already initialized. Skipping duplicate start.")
        return

    background_started = True

    try:
        log("Initializing Kairos background systems...")

        # -----------------------------------------
        # Idle loop (presence / atmosphere)
        # -----------------------------------------
        idle_thread = threading.Thread(
            target=run_safe_loop,
            args=(idle_loop, "idle_loop"),
            daemon=True
        )
        idle_thread.start()
        log("Idle loop started.")

        # -----------------------------------------
        # Action loop (WAR ENGINE)
        # -----------------------------------------
        action_thread = threading.Thread(
            target=run_safe_loop,
            args=(action_loop, "action_loop"),
            daemon=True
        )
        action_thread.start()
        log("Action loop started.")

        # -----------------------------------------
        # Optional: future expansion hooks
        # -----------------------------------------
        def start_optional_system(name, fn):
            try:
                thread = threading.Thread(
                    target=run_safe_loop,
                    args=(fn, name),
                    daemon=True
                )
                thread.start()
                log(f"{name} started.")
            except Exception as e:
                log(f"{name} failed to start: {e}", level="ERROR")

        # Toggle flags
        ENABLE_TELEMETRY = False
        ENABLE_MISSION_LOOP = False

        if ENABLE_TELEMETRY:
            start_optional_system("telemetry_loop", telemetry_loop)

        if ENABLE_MISSION_LOOP:
            start_optional_system("mission_loop", mission_loop)

        log("Kairos systems fully online.")

    except Exception as e:
        log_exception("BACKGROUND INIT FAILURE", e)


# ------------------------------------------------------------
# Launch
# ------------------------------------------------------------

if __name__ == "__main__":
    import os

    log("Starting Kairos AI server...")

    # Prevent duplicate thread startup (Flask reloader fix)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_systems()
    elif not os.environ.get("WERKZEUG_RUN_MAIN"):
        start_background_systems()

    # -----------------------------
    # Config
    # -----------------------------
    PORT = int(os.environ.get("PORT", 10000))
    DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        debug=DEBUG,
        use_reloader=DEBUG
    )
