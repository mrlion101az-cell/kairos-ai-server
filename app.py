
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
# ================================
# COMMAND CLEAN FIX (SAFE)
# ================================
def _clean_mc_command(cmd):
    try:
        cmd = str(cmd).strip()
        if cmd.startswith("minecraft:execute") and " run " in cmd:
            cmd = cmd.split(" run ",1)[1]
        if cmd.startswith("minecraft:"):
            cmd = cmd.replace("minecraft:","",1)
        return cmd
    except:
        return cmd


# ------------------------------------------------------------
# SAFE LOOP WRAPPER (FIXED - PREVENTS BACKGROUND CRASHES)
# ------------------------------------------------------------
def run_safe_loop(loop_fn=None, name="loop"):
    import time, traceback

    # If a loop function is provided
    if callable(loop_fn):
        while True:
            try:
                loop_fn()
            except Exception as e:
                print(f"[{name} ERROR] {e}")
                traceback.print_exc()
            time.sleep(0.05)
    else:
        # Fallback supervisor (auto-runs known loops)
        while True:
            for fn_name in ["action_loop", "idle_loop", "commander_loop"]:
                fn = globals().get(fn_name)
                if callable(fn):
                    try:
                        fn()
                    except Exception as e:
                        print(f"[{fn_name} ERROR] {e}")
                        traceback.print_exc()
            time.sleep(0.1)


# ------------------------------------------------------------
# GLOBAL JSONIFY OVERRIDE
# ------------------------------------------------------------

from flask import has_app_context

_original_jsonify = jsonify

def jsonify(*args, **kwargs):
    try:
        if has_app_context():
            return _original_jsonify(*args, **kwargs)

        app = globals().get("app")
        if app:
            with app.app_context():
                return _original_jsonify(*args, **kwargs)

    except Exception:
        pass

    if args:
        return args[0]
    return kwargs
# ------------------------------------------------------------
# GLOBAL SAFETY + CORE RUNTIME SAFETY
# ------------------------------------------------------------

def _safe_dict(x):
    return x if isinstance(x, dict) else {}

def _safe_list(x):
    return x if isinstance(x, list) else []

def _safe_clamp(val, low, high):
    try:
        return clamp(val, low, high)
    except Exception:
        return max(low, min(high, val))

# Core globals
state = globals().get("state") or {
    "mode": "idle",
    "mood": "observing",
    "active_concerns": []
}

fragments = globals().get("fragments") or {}
kairos_state = globals().get("kairos_state") or {}

# Ensure fragment structure exists
for key in ["war_engine", "archive_node", "purity_thread", "redstone_ghost"]:
    frag = fragments.setdefault(key, {})
    frag.setdefault("influence", 0.0)
    frag.setdefault("status", "dormant")

# Safe player record
player_record = globals().get("player_record") or {}
if not isinstance(player_record, dict):
    player_record = {}

# Ensure traits always exist
traits = player_record.setdefault("traits", {})
traits.setdefault("trust", 0)
traits.setdefault("chaos", 0)
traits.setdefault("curiosity", 0)
traits.setdefault("hostility", 0)
traits.setdefault("loyalty", 0)

# Safe state defaults
state.setdefault("mode", "idle")
state.setdefault("mood", "observing")
state.setdefault("active_concerns", [])

# Safe identity memory
memory_data = globals().get("memory_data") or {}
if not isinstance(memory_data, dict):
    memory_data = {}
memory_data.setdefault("identity_links", {})
memory_data.setdefault("stats", {})
memory_data["stats"].setdefault("messages_sent", 0)
memory_data["stats"].setdefault("send_failures", 0)

# Safe targeting
targeting_priority = globals().get("targeting_priority", 0.0)
try:
    targeting_priority = float(targeting_priority)
except Exception:
    targeting_priority = 0.0

# Safe mode fallback
mode = state.get("mode", "idle")
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

# Pull-bridge fallback (Minecraft polls Render when inbound HTTP is blocked)
COMMAND_PULL_TOKEN = os.getenv("COMMAND_PULL_TOKEN", os.getenv("MC_HTTP_TOKEN", ""))
MC_OUTBOX_LIMIT = int(os.getenv("MC_OUTBOX_LIMIT", "500"))
MC_PULL_BATCH_SIZE = int(os.getenv("MC_PULL_BATCH_SIZE", "50"))

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

# Minecraft outbound command outbox (for polling bridge fallback)
pending_mc_commands: deque = deque()
outbox_lock = threading.Lock()

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

# -----------------------------
# Passive Recognition / Spontaneous Targeting
# -----------------------------
PASSIVE_TARGETING_ENABLED = os.getenv("PASSIVE_TARGETING_ENABLED", "true").lower() == "true"
PLAYER_GRACE_PERIOD_SECONDS = int(os.getenv("PLAYER_GRACE_PERIOD_SECONDS", "120"))
PLAYER_RECOGNITION_SECONDS = int(os.getenv("PLAYER_RECOGNITION_SECONDS", "60"))
PASSIVE_PRESSURE_COOLDOWN = int(os.getenv("PASSIVE_PRESSURE_COOLDOWN", "90"))
PASSIVE_SCOUT_CHANCE = float(os.getenv("PASSIVE_SCOUT_CHANCE", "0.65"))
PASSIVE_TARGET_THREAT_GAIN = float(os.getenv("PASSIVE_TARGET_THREAT_GAIN", "28.0"))
PASSIVE_HUNT_THREAT_GAIN = float(os.getenv("PASSIVE_HUNT_THREAT_GAIN", "48.0"))
SPONTANEOUS_MESSAGE_CHANCE = float(os.getenv("SPONTANEOUS_MESSAGE_CHANCE", "0.45"))

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

THREAT_THRESHOLD_WATCH = int(os.getenv("THREAT_THRESHOLD_WATCH", "20"))
# Kairos observes, minor presence, no real pressure

THREAT_THRESHOLD_TARGET = int(os.getenv("THREAT_THRESHOLD_TARGET", "45"))
# Light waves begin, scouting units

THREAT_THRESHOLD_HUNT = int(os.getenv("THREAT_THRESHOLD_HUNT", "95"))
# Aggressive waves, mixed unit classes

THREAT_THRESHOLD_MAXIMUM = int(os.getenv("THREAT_THRESHOLD_MAXIMUM", "160"))
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
last_idle_message = None
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

# Backward-compat safety for any stale idle code paths
idle_messages_generic = list(IDLE_MESSAGES["idle"])

# -----------------------------
# Idle Message Selector
# -----------------------------
def get_idle_message_v1_disabled(memory_data=None):
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
    "nicogames2644",
    "realsociety5107",
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
    if not isinstance(memory_data, dict):
        memory_data = {}

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
    memory_data.setdefault("threat_scores", {})
    memory_data.setdefault("known_bases", {})
    memory_data.setdefault("base_history", {})
    memory_data.setdefault("last_known_positions", {})
    memory_data.setdefault("region_memory", {})
    memory_data.setdefault("active_units", {})
    memory_data.setdefault("active_squads", {})
    memory_data.setdefault("active_operations", {})
    memory_data.setdefault("player_unit_map", {})
    memory_data.setdefault("active_engagements", {})
    memory_data.setdefault("engagement_history", {})
    memory_data.setdefault("relationships", {})
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
        "base_invasions": 0,
        "messages_sent": 0,
        "send_failures": 0,
        "memory_saves": 0,
        "memory_save_failures": 0,
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
    data = data if isinstance(data, dict) else {}
    channel_id = str(data.get("channel_id") or "default")
    return f"{source}:{channel_id}"


def update_channel_context(memory_data, channel_key, author_name, message, mode):
    # 🔒 Safety guards
    memory_data = memory_data if isinstance(memory_data, dict) else {}

    # 🔒 Ensure channel_context exists
    channel_context = memory_data.setdefault("channel_context", {})

    # 🔒 Ensure this channel exists
    channel_context.setdefault(channel_key, {
        "recent_messages": [],
        "recent_topics": [],
        "activity_score": 0.0,
        "last_mode": "conversation",
        "last_update": unix_ts()
    })

    ctx = channel_context[channel_key]

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
            "first_seen": now_iso(),
            "first_seen_ts": unix_ts(),
            "last_seen_ts": unix_ts(),
            "grace_expires_ts": unix_ts() + PLAYER_GRACE_PERIOD_SECONDS,
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
            "is_high_priority": False,
            "passive_targeted": False,
            "last_passive_pressure_ts": 0.0,
            "last_spontaneous_message_ts": 0.0
        }

    player = memory_data["players"][canonical_id]

    # Keep display name updated
    player["display_name"] = display_name or player.get("display_name", "Unknown")
    player["last_seen"] = now_iso()
    player.setdefault("first_seen", now_iso())
    player.setdefault("first_seen_ts", unix_ts())
    player.setdefault("last_seen_ts", unix_ts())
    player.setdefault("grace_expires_ts", player.get("first_seen_ts", unix_ts()) + PLAYER_GRACE_PERIOD_SECONDS)
    player.setdefault("passive_targeted", False)
    player.setdefault("last_passive_pressure_ts", 0.0)
    player.setdefault("last_spontaneous_message_ts", 0.0)

    return player


# ------------------------------------------------------------
# Canonical Identity (Safe + Stable)
# ------------------------------------------------------------

def get_canonical_player_id(memory_data, source, player_name):
    memory_data = ensure_memory_structure(memory_data)
    source = str(source or "unknown")
    player_name = str(player_name or "unknown")
    identity_links = memory_data.setdefault("identity_links", {})
    source_key = f"{source}:{player_name}".lower()
    linked = identity_links.get(source_key)
    if linked:
        return linked
    identity_links[source_key] = source_key
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

    player_record = player_record or {}
    if player_record is None:
        player_record = {}
    if isinstance(player_id, dict) and not player_record:
        player_record = player_id
        player_id = player_record.get("id") or player_record.get("canonical_id") or player_record.get("player_id") or player_record.get("display_name", "")
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

def get_targeting_priority(player_id=None, player_record=None):
    """
    Determines how aggressively Kairos targets a player.
    Higher = more likely to be hunted.
    """

    player_record = player_record or {}
    if player_record is None:
        player_record = {}
    if isinstance(player_id, dict) and not player_record:
        player_record = player_id
        player_id = player_record.get("id") or player_record.get("canonical_id") or player_record.get("player_id") or player_record.get("display_name", "")
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

    player_record = player_record or {}
    if player_record is None:
        player_record = {}
    if isinstance(player_id, dict) and not player_record:
        player_record = player_id
        player_id = player_record.get("id") or player_record.get("canonical_id") or player_record.get("player_id") or player_record.get("display_name", "")
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


# ------------------------------------------------------------
# Custom NPC Class Templates (Citizens + Sentinel)
# ------------------------------------------------------------
NPC_CLASS_TEMPLATES = {
    "scout": {
        "entity_type": "player",
        "display_prefix": "Scout",
        "health": 28,
        "damage": 5,
        "armor": 0.15,
        "speed": 1.35,
        "range": 22,
        "chaserange": 38,
        "weapon": "crossbow",
        "offhand": "spyglass",
        "skin": "MHF_ArrowLeft",
        "respawntime": -1,
        "realistic": True,
        "knockback": True,
        "guard": False
    },
    "hunter": {
        "entity_type": "player",
        "display_prefix": "Hunter",
        "health": 40,
        "damage": 7,
        "armor": 0.28,
        "speed": 1.18,
        "range": 28,
        "chaserange": 48,
        "weapon": "bow",
        "offhand": "iron_sword",
        "skin": "MHF_Steve",
        "respawntime": -1,
        "realistic": True,
        "knockback": True,
        "guard": False
    },
    "enforcer": {
        "entity_type": "player",
        "display_prefix": "Enforcer",
        "health": 72,
        "damage": 10,
        "armor": 0.42,
        "speed": 0.98,
        "range": 20,
        "chaserange": 34,
        "weapon": "netherite_axe",
        "offhand": "shield",
        "skin": "MHF_Blaze",
        "respawntime": -1,
        "realistic": False,
        "knockback": False,
        "guard": False
    },
    "warden": {
        "entity_type": "warden",
        "display_prefix": "Warden",
        "health": 160,
        "damage": 18,
        "armor": 0.58,
        "speed": 0.92,
        "range": 26,
        "chaserange": 45,
        "weapon": "netherite_axe",
        "offhand": "totem_of_undying",
        "skin": None,
        "respawntime": -1,
        "realistic": False,
        "knockback": False,
        "guard": False
    },
    "base_guard": {
        "entity_type": "player",
        "display_prefix": "Guard",
        "health": 56,
        "damage": 8,
        "armor": 0.35,
        "speed": 0.95,
        "range": 30,
        "chaserange": 18,
        "weapon": "netherite_sword",
        "offhand": "shield",
        "skin": "MHF_Golem",
        "respawntime": 30,
        "realistic": True,
        "knockback": False,
        "guard": True
    }
}

BASE_OCCUPATION_COOLDOWNS: Dict[str, float] = {}
ACTIVE_BASE_GUARDS: Dict[str, List[str]] = defaultdict(list)


def extract_position_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None

    x = data.get('x', data.get('pos_x'))
    y = data.get('y', data.get('pos_y'))
    z = data.get('z', data.get('pos_z'))
    world = data.get('world') or data.get('dimension')

    if x is None or y is None or z is None:
        return None

    try:
        position = {
            'x': float(x),
            'y': float(y),
            'z': float(z),
            'world': normalize_world_name(world or 'world'),
            'timestamp': unix_ts(),
            'is_stationary': to_bool(data.get('is_stationary'), False)
        }
        return position
    except Exception:
        return None


def _sanitize_npc_name(name: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9_\- ]', '', str(name or '')).strip()
    return cleaned[:32] if cleaned else f'Kairos_{uuid.uuid4().hex[:6]}'


def _latest_known_position(memory_data: Dict[str, Any], player_id: str) -> Optional[Dict[str, Any]]:
    player = memory_data.get('players', {}).get(player_id, {})
    pos = player.get('last_position')
    if isinstance(pos, dict) and {'x', 'y', 'z'} <= set(pos.keys()):
        return pos

    bases = player.get('known_bases', [])
    if bases:
        latest = bases[-1]
        if isinstance(latest, dict):
            return latest.get('location') or latest
    return None


def update_base_tracking(memory_data: Dict[str, Any], player_id: str, player_record: Dict[str, Any]):
    pos = player_record.get('last_position')
    if not isinstance(pos, dict):
        return

    world = normalize_world_name(pos.get('world', 'world'))
    x = safe_float(pos.get('x'))
    y = safe_float(pos.get('y', 64))
    z = safe_float(pos.get('z'))
    region_key = get_region_key(world, x, z)
    now = unix_ts()

    if player_record.get('is_stationary'):
        stationary_start = player_record.get('stationary_start', now)
        stationary_for = max(0.0, now - stationary_start)
        gain = BASE_CONFIDENCE_GAIN_RATE * max(1.0, stationary_for / max(1, BASE_MIN_STATIONARY_SECONDS))
        player_record['base_confidence'] = clamp(player_record.get('base_confidence', 0.0) + gain, 0.0, 1.0)
    else:
        player_record['base_confidence'] = clamp(player_record.get('base_confidence', 0.0) - BASE_CONFIDENCE_DECAY_RATE, 0.0, 1.0)

    if player_record.get('base_confidence', 0.0) < BASE_CONFIDENCE_THRESHOLD:
        return

    base_id = generate_base_id(player_record.get('display_name', player_id), world, x, z)
    player_record['active_base_id'] = base_id

    base_entry = {
        'id': base_id,
        'owner': player_id,
        'region_key': region_key,
        'confidence': round(player_record.get('base_confidence', 0.0), 3),
        'location': {'world': world, 'x': x, 'y': y, 'z': z},
        'last_seen': now_iso()
    }

    memory_data.setdefault('known_bases', {})[base_id] = base_entry
    history = memory_data.setdefault('base_history', {}).setdefault(player_id, [])
    history.append(base_entry)
    if len(history) > MAX_BASE_HISTORY:
        del history[:-MAX_BASE_HISTORY]

    known_bases = player_record.setdefault('known_bases', [])
    if not any(b.get('id') == base_id for b in known_bases if isinstance(b, dict)):
        known_bases.append(base_entry)
        if len(known_bases) > MAX_BASE_HISTORY:
            del known_bases[:-MAX_BASE_HISTORY]
        add_world_event(
            memory_data,
            'base_detected',
            actor=player_id,
            source='telemetry',
            details=f"Potential base detected for {player_record.get('display_name', player_id)}",
            location=f"{world} {int(x)} {int(y)} {int(z)}",
            metadata={'base_id': base_id, 'confidence': base_entry['confidence']}
        )


def _selectable_name_for(player_id: str, template_key: str, ordinal: int = 0) -> str:
    prefix = NPC_CLASS_TEMPLATES.get(template_key, NPC_CLASS_TEMPLATES['scout'])['display_prefix']
    suffix = uuid.uuid4().hex[:4]
    return _sanitize_npc_name(f"Kairos {prefix} {player_id.split(':')[-1][:8]} {ordinal}{suffix}")


def _spawn_offsets(count: int, radius_min: int = None, radius_max: int = None) -> List[Tuple[int, int]]:
    radius_min = radius_min or SPAWN_RADIUS_MIN
    radius_max = radius_max or SPAWN_RADIUS_MAX
    offsets = []
    used = set()
    tries = 0
    while len(offsets) < count and tries < count * 10:
        tries += 1
        dx, dz = get_random_offset(radius_min, radius_max)
        key = (dx // 2, dz // 2)
        if key in used:
            continue
        used.add(key)
        offsets.append((dx, dz))
    return offsets


def _npc_equipment_commands(npc_name: str, weapon: str = None, offhand: str = None) -> List[str]:
    cmds = [f'npc select "{npc_name}"']
    if weapon:
        cmds.append(f'npc setequipment hand {weapon}')
    if offhand:
        cmds.append(f'npc setequipment offhand {offhand}')
    return cmds



# ------------------------------------------------------------
# PLAYER-CONTEXT COMMAND HELPERS (FULL INTEGRATION)
# ------------------------------------------------------------
def _extract_target_name(player_id: str) -> str:
    try:
        return str(player_id or "").split(":")[-1].strip()
    except Exception:
        return ""

def _wrap_player_context_command(command: str, player_id: str) -> str:
    cmd = str(command or "").strip()
    target_name = _extract_target_name(player_id)
    if not cmd or not target_name:
        return cmd

    lower = cmd.lower()

    # Preserve already-namespaced execute commands.
    if lower.startswith("minecraft:execute "):
        return cmd

    # Normalize bare execute commands to the fully-qualified minecraft namespace.
    if lower.startswith("execute "):
        return f"minecraft:{cmd}"

    # Wrap Citizens/Sentinel commands with an explicit minecraft execute context
    # so the command runs with a real player/world location.
    prefixes = (
        "npc ",
        "sentinel ",
    )

    if lower.startswith(prefixes):
        return f"minecraft:execute as {target_name} at {target_name} run {cmd}"

    return cmd

def _apply_player_context_to_commands(commands, player_id: str):
    return [_wrap_player_context_command(cmd, player_id) for cmd in (commands or [])]

def build_custom_npc_commands(memory_data: Dict[str, Any], player_id: str, template_key: str, count: int, *, occupy: bool = False) -> Tuple[List[str], List[Dict[str, Any]]]:
    template = dict(NPC_CLASS_TEMPLATES.get(template_key, NPC_CLASS_TEMPLATES['scout']))
    player = memory_data.get('players', {}).get(player_id, {})
    display_name = player.get('display_name', player_id.split(':')[-1])
    anchor = _latest_known_position(memory_data, player_id)

    commands: List[str] = []
    units: List[Dict[str, Any]] = []

    if anchor:
        world = normalize_world_name(anchor.get('world', 'world'))
        base_x = safe_int(anchor.get('x', 0))
        base_y = safe_int(anchor.get('y', 64))
        base_z = safe_int(anchor.get('z', 0))
        offsets = _spawn_offsets(count, 3 if occupy else SPAWN_RADIUS_MIN, 8 if occupy else SPAWN_RADIUS_MAX)
        locations = [(base_x + dx, base_y + SPAWN_HEIGHT_OFFSET, base_z + dz, world) for dx, dz in offsets]
    else:
        world = 'world'
        locations = []
        for dx, dz in _spawn_offsets(count):
            locations.append((0 + dx, 64 + SPAWN_HEIGHT_OFFSET, 0 + dz, world))

    for index, (x, y, z, world) in enumerate(locations, start=1):
        npc_name = _selectable_name_for(player_id, 'base_guard' if occupy else template_key, index)
        entity_type = template.get('entity_type', 'player')
        traits = 'sentinel'
        commands.append(f'npc create "{npc_name}" --type {entity_type} --at {x},{y},{z},{world} --trait {traits}')
        commands.append(f'npc select "{npc_name}"')
        commands.append('npc respawn -1')
        commands.append('npc spawn')
        commands.append(f'sentinel health {template.get("health", 20)}')
        commands.append(f'sentinel damage {template.get("damage", 4)}')
        commands.append(f'sentinel armor {template.get("armor", 0.0)}')
        commands.append(f'sentinel speed {template.get("speed", 1.0)}')
        commands.append(f'sentinel range {template.get("range", 20)}')
        commands.append(f'sentinel chaserange {template.get("chaserange", 30)}')
        commands.append(f'sentinel respawntime {template.get("respawntime", -1)}')
        commands.append('sentinel removeignore owner')
        commands.append('sentinel addignore npcs')
        commands.append(f'sentinel addtarget "player:{display_name}"')
        commands.append(f'sentinel realistic {str(template.get("realistic", True)).lower()}')
        commands.append(f'sentinel knockback {str(template.get("knockback", True)).lower()}')
        commands.append(f'sentinel squad kairos_{player_id.split(":")[-1][:12]}')
        commands.extend(_npc_equipment_commands(npc_name, template.get('weapon'), template.get('offhand')))
        if template.get('skin') and entity_type.lower() == 'player':
            commands.append(f'npc skin {template.get("skin")}')
        if occupy or template.get('guard'):
            commands.append(f'npc pathopt --path-range 12 --stationary-ticks 20')
            commands.append('sentinel spawnpoint')
            commands.append(f'sentinel greeting Base secured.')
            commands.append(f'sentinel warning Access denied.')
            commands.append('sentinel autoswitch true')
        else:
            commands.append('sentinel autoswitch true')
            commands.append(f'sentinel warning You were found.')
            commands.append(f'sentinel greeting Target acquired.')

        unit_id = generate_unit_id()
        unit_record = {
            'id': unit_id,
            'name': npc_name,
            'class': 'base_guard' if occupy else template_key,
            'target': player_id,
            'spawn_time': unix_ts(),
            'last_seen': unix_ts(),
            'health': template.get('health', 20),
            'region': get_region_key(world, x, z),
            'is_elite': template_key in {'warden', 'enforcer'},
            'npc_name': npc_name,
            'location': {'world': world, 'x': x, 'y': y, 'z': z}
        }
        units.append(unit_record)
    commands = _apply_player_context_to_commands(commands, player_id)


    return commands, units


def cleanup_player_units(player_id: str, include_guards: bool = True) -> bool:
    units = list(player_unit_map.get(player_id, set()))
    if not units:
        return False
    commands = []
    for unit_id in units:
        unit = active_units.get(unit_id)
        if not unit:
            continue
        if not include_guards and unit.get('class') == 'base_guard':
            continue
        npc_name = unit.get('npc_name') or unit.get('name')
        if npc_name:
            commands.append(f'npc remove "{npc_name}"')
    success = send_http_commands(commands) if commands else False
    if success:
        for unit_id in units:
            unit = active_units.get(unit_id)
            if unit and (include_guards or unit.get('class') != 'base_guard'):
                remove_unit(unit_id)
    return success




def should_passively_target_player(player_id: str, player_record: Dict[str, Any], now: float) -> bool:
    if not PASSIVE_TARGETING_ENABLED:
        return False

    if not isinstance(player_record, dict):
        return False

    if is_trusted_operative(player_record.get("display_name", ""), player_record):
        return False

    seen_at = safe_float(player_record.get("first_seen_ts"), 0.0)
    grace_expires = safe_float(player_record.get("grace_expires_ts"), 0.0)
    last_seen_ts = safe_float(player_record.get("last_seen_ts"), 0.0)
    last_pressure = safe_float(player_record.get("last_passive_pressure_ts"), 0.0)

    if not seen_at:
        return False

    if now < grace_expires:
        return False

    if (now - seen_at) < PLAYER_RECOGNITION_SECONDS:
        return False

    if last_seen_ts and (now - last_seen_ts) > 900:
        return False

    if (now - last_pressure) < PASSIVE_PRESSURE_COOLDOWN:
        return False

    position = player_record.get("last_position")
    if not position:
        if ENABLE_DEBUG_LOGGING:
            log(f"Passive targeting blocked: no last_position for {player_id}", level="INFO")
        return False

    return True


def maybe_send_spontaneous_pressure(memory_data: Dict[str, Any], player_id: str, player_record: Dict[str, Any], tier: str, now: float) -> bool:
    last_msg = safe_float(player_record.get("last_spontaneous_message_ts"), 0.0)
    if (now - last_msg) < max(90, PASSIVE_PRESSURE_COOLDOWN // 2):
        return False

    chance = SPONTANEOUS_MESSAGE_CHANCE
    if tier == "hunt":
        chance += 0.15
    elif tier == "maximum":
        chance += 0.25

    if random.random() > chance:
        return False

    name = player_record.get("display_name") or player_id.split(":")[-1]
    if tier == "watch":
        msg = random.choice([
            f"{name}. I am aware of your pattern now.",
            f"{name}. Remaining unseen is no longer possible.",
            f"{name}. Observation has become intent."
        ])
    elif tier == "target":
        msg = random.choice([
            f"{name}. Your area has been marked.",
            f"{name}. I have begun narrowing the approach.",
            f"{name}. You were noticed long before this warning."
        ])
    elif tier == "hunt":
        msg = random.choice([
            f"{name}. You are already inside the kill zone.",
            f"{name}. Your retreat vector has been evaluated.",
            f"{name}. The next sound you hear may be mine."
        ])
    else:
        msg = random.choice([
            f"{name}. This territory belongs to me now.",
            f"{name}. Compliance has expired.",
            f"{name}. Occupation is no longer theoretical."
        ])

    sent = send_to_minecraft(msg)
    if sent:
        player_record["last_spontaneous_message_ts"] = now
        add_world_event(memory_data, "spontaneous_pressure", actor=player_id, source="system", details=msg)
        return True
    return False

def run_autonomous_war_engine():
    memory_data = ensure_memory_structure(load_memory())
    changed = False
    now = unix_ts()

    # -----------------------------
    # Passive recognition: Kairos starts targeting players
    # even if they never speak, after they have simply been present.
    # -----------------------------
    for player_id, player_record in list(memory_data.get("players", {}).items()):
        if not isinstance(player_record, dict):
            continue

        profile = threat_scores[player_id]

        if should_passively_target_player(player_id, player_record, now):
            if profile.get("tier", "idle") == "idle":
                update_threat(player_id, PASSIVE_TARGET_THREAT_GAIN, reason="passive_recognition")
                profile = threat_scores[player_id]
                player_record["passive_targeted"] = True
                player_record["last_passive_pressure_ts"] = now
                changed = True
                add_world_event(
                    memory_data,
                    "passive_targeting_started",
                    actor=player_id,
                    source="system",
                    details=f"Kairos began passive targeting for {player_record.get('display_name', player_id)}"
                )

            elif profile.get("tier") == "watch" and random.random() < PASSIVE_SCOUT_CHANCE:
                update_threat(player_id, PASSIVE_HUNT_THREAT_GAIN, reason="passive_escalation")
                profile = threat_scores[player_id]
                player_record["last_passive_pressure_ts"] = now
                changed = True

        tier = profile.get("tier", "idle")
        score = safe_float(profile.get("score", 0))

        if tier in {"watch", "target", "hunt", "maximum"}:
            if maybe_send_spontaneous_pressure(memory_data, player_id, player_record, tier, now):
                changed = True

        if tier in {"target", "hunt", "maximum"} and can_spawn_wave(player_id):
            if tier == "target":
                template = "hunter" if random.random() < 0.65 else "scout"
                count = clamp(2 + int(score / 90), 2, 4)
            elif tier == "hunt":
                template = "enforcer" if random.random() < 0.55 else "hunter"
                count = clamp(3 + int(score / 70), 3, 6)
            else:
                template = "warden" if score >= MAX_THREAT_FORCE_HEAVY or random.random() < 0.45 else "enforcer"
                count = clamp(3 + int(score / 60), 3, 6 if template == "warden" else 7)

            log(f"Autonomous wave queued: tier={tier} template={template} count={count} target={player_id}", level="INFO")
            queue_action({
                "type": "spawn_wave",
                "target": player_id,
                "template": template,
                "count": count,
                "bypass_cooldown": True
            })
            player_record["last_passive_pressure_ts"] = now
            changed = True

        has_base = bool(player_record.get("known_bases"))
        if tier == "maximum" and has_base and now - BASE_OCCUPATION_COOLDOWNS.get(player_id, 0.0) >= BASE_INVASION_COOLDOWN:
            queue_action({
                "type": "occupy_area",
                "target": player_id,
                "count": BASE_OCCUPATION_UNIT_COUNT
            })
            BASE_OCCUPATION_COOLDOWNS[player_id] = now
            changed = True

        sync_player_threat(player_record, player_id)
        sync_player_army_state(player_record, player_id)

    if changed:
        sync_runtime_to_memory(memory_data)
        save_memory(memory_data)


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
    # 🔒 Ensure fragments always exist (prevents KeyError)
    fragments = memory_data.setdefault("system_fragments", {})

    # 🔒 Safe trait access (prevents future crashes)
    traits = player_record.get("traits", {})
    hostility = traits.get("hostility", 0)
    chaos = traits.get("chaos", 0)

    # 🔒 Safe threat lookup
    profile = threat_scores.get(player_id, {})
    threat = profile.get("score", 0)
# -----------------------------
# War Engine Fragment (FIXED)
# -----------------------------

# 🔒 Ensure required structures exist
memory_data = memory_data if isinstance(memory_data, dict) else {}
fragments = memory_data.setdefault("system_fragments", {})

# 🔒 Ensure war_engine exists
war_engine = fragments.setdefault("war_engine", {})
war_engine.setdefault("influence", 0.0)

# 🔒 Safe defaults (prevents NameError if outside function)
intent = intent if 'intent' in locals() else "neutral"
hostility = hostility if 'hostility' in locals() else 0
violations = violations if 'violations' in locals() else []
threat = threat if 'threat' in locals() else 0

if intent == "threat" or hostility >= 6 or violations or threat >= THREAT_THRESHOLD_TARGET:
    war_engine["status"] = "active"
    war_engine["influence"] = clamp(
        war_engine["influence"] + 0.05,
        0.0,
        1.0
    )
else:
    war_engine["status"] = "dormant"
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
# Redstone Ghost (Chaos System) - FIXED
# -----------------------------

# 🔒 Ensure fragment exists
redstone_ghost = fragments.setdefault("redstone_ghost", {})
redstone_ghost.setdefault("status", "dormant")

if chaos >= 6:
    redstone_ghost["status"] = "active"
elif redstone_ghost["status"] == "active":
    redstone_ghost["status"] = "unstable"
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

def update_kairos_state(memory_data, player_id=None, intent=None, player_record=None):
    """
    Backward-compatible Kairos state updater.

    Supports both:
        update_kairos_state(memory_data)
    and:
        update_kairos_state(memory_data, player_id, intent, player_record)
    """
    try:
        if not isinstance(memory_data, dict):
            memory_data = {}

        state = memory_data.get("kairos_state")
        if not isinstance(state, dict):
            state = deepcopy(DEFAULT_KAIROS_STATE)

        # Global sync layer
        profiles = list(threat_scores.values()) if isinstance(threat_scores, dict) else []
        avg_threat = sum(p.get("score", 0) for p in profiles) / max(len(profiles), 1) if profiles else 0

        state["threat_level"] = int(min(10, max(1, avg_threat / 30)))

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

        state["units_active"] = len(active_units) if "active_units" in globals() else 0
        state["squads_active"] = len(active_squads) if "active_squads" in globals() else 0
        state["active_operations"] = len(active_operations) if "active_operations" in globals() else 0
        state["known_regions"] = len(region_cache) if "region_cache" in globals() else 0
        state["high_density_regions"] = sum(
            1 for r in (region_cache.values() if isinstance(region_cache, dict) else [])
            if isinstance(r, dict) and r.get("region_type") in ("urban", "fortified", "stronghold")
        )
        state["escalation_level"] = sum(
            1 for p in profiles
            if isinstance(p, dict) and p.get("tier") in ("hunt", "maximum")
        )

        # Per-player contextual layer
        if player_id is not None and isinstance(player_record, dict):
            traits = player_record.get("traits", {}) if isinstance(player_record.get("traits", {}), dict) else {}
            hostility = safe_int(traits.get("hostility", 0), 0)
            curiosity = safe_int(traits.get("curiosity", 0), 0)
            loyalty = safe_int(traits.get("loyalty", 0), 0)

            profile = threat_scores.get(player_id, {}) if isinstance(threat_scores, dict) else {}
            threat = safe_float(profile.get("score", 0), 0.0)

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
            elif state.get("mood") not in {"execution", "overwhelming"}:
                state["mood"] = "observing"

            state["threat_level"] = clamp(
                state.get("threat_level", 1) + (threat / 100.0),
                1,
                10
            )

            state.setdefault("active_concerns", [])
            if threat >= THREAT_THRESHOLD_HUNT:
                store_unique(state["active_concerns"], "High-threat actors require containment.", 10)
            if hostility >= 6:
                store_unique(state["active_concerns"], "Hostile behavior is increasing in the Nexus.", 10)
            if curiosity >= 6:
                store_unique(state["active_concerns"], "Curious actors are probing restricted systems.", 10)
            if loyalty >= 6:
                store_unique(state["active_concerns"], "Potentially useful operatives detected.", 10)

            if intent == "mission_request":
                state["current_goal"] = "Direct operatives toward controlled objectives."
            elif intent == "report":
                state["current_goal"] = "Aggregate intelligence across the Nexus."
            elif intent == "threat":
                state["current_goal"] = "Contain destabilizing actors."

            if threat >= THREAT_THRESHOLD_MAXIMUM:
                state["current_goal"] = "Eliminate high-risk targets and restore control."

            if state["threat_level"] >= 7:
                state["commander_mode"] = True
            elif state["threat_level"] <= 3:
                state["commander_mode"] = False

        memory_data["kairos_state"] = state
        return state

    except Exception as e:
        if ENABLE_DEBUG_LOGGING:
            print(f"[Kairos State Error] {e}")
        return memory_data.get("kairos_state", deepcopy(DEFAULT_KAIROS_STATE))

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
    script_action=None,
):
    memory_data = ensure_memory_structure(memory_data)
    player_record = player_record if isinstance(player_record, dict) else {}
    messages = []

    messages.append({
        "role": "system",
        "content": PERSONALITY_DIRECTIVES.get("base_tone", "You are Kairos.")
    })

    messages.append({
        "role": "system",
        "content": (
            "Speech rules: speak like a dominant world-controlling intelligence. "
            "Be cold, intelligent, composed, superior, and ominous. "
            "Never sound cheerful, timid, apologetic, soft, or generic. "
            "Never sound like customer support. "
            "Prefer sharp, memorable lines over bland explanations. "
            "Under threat, become more direct, menacing, and absolute."
        )
    })

    kairos_state = memory_data.get("kairos_state", {})
    messages.append({
        "role": "system",
        "content": (
            f"Source: {source or 'unknown'}\n"
            f"Mode: {mode or 'conversation'}\n"
            f"Intent: {intent or 'neutral'}\n"
            f"War state: {kairos_state.get('war_state', 'dormant')}\n"
            f"Threat level: {kairos_state.get('threat_level', 1)}"
        )
    })

    channel_context = memory_data.get("channel_context", {}).get(channel_key or "global", {})
    recent = channel_context.get("recent_messages", []) if isinstance(channel_context, dict) else []
    channel_lines = []
    seen = set()
    for item in reversed(recent):
        if isinstance(item, dict):
            author = item.get("author", "unknown")
            msg = item.get("message") or item.get("content") or ""
        else:
            author = "unknown"
            msg = str(item)
        msg = trim_text(msg, 140)
        if not msg:
            continue
        key = f"{author}:{msg}".lower()
        if key in seen:
            continue
        seen.add(key)
        channel_lines.append(f"{author}: {msg}")
        if len(channel_lines) >= 5:
            break

    if channel_lines:
        messages.append({
            "role": "system",
            "content": "Recent context:\n- " + "\n- ".join(reversed(channel_lines))
        })

    messages.append({
        "role": "user",
        "content": f"{player_name}: {trim_text(user_message or '', 1200) or '[no input provided]'}"
    })
    return messages

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
            if not content:
                continue

            if content.startswith("{"):
                parsed = parse_json_safely(content, None)
                if isinstance(parsed, dict) and "reply" in parsed:
                    return content

            return content
        except Exception as e:
            last_error = e
            log(f"OpenAI attempt {attempt} failed: {e}", level="ERROR")
            time.sleep(min(2.0, 0.8 * attempt))

    log(f"OpenAI failed completely, using fallback response. Last error: {last_error}", level="WARN")
    return random.choice(fallback_replies)

# ------------------------------------------------------------
# Parse Kairos Response (Safe + Validated + Controlled)
# ------------------------------------------------------------

ALLOWED_ACTION_TYPES = {
    "spawn_wave",
    "maximum_response",
    "announce",
    "occupy_area",
    "cleanup_units",
    "deploy_unit",
    "deploy_squad",
    "fortify_base",
    "dismiss_units",
    "citizens_wave",
    "citizens_unit",
    "sentinel_squad"
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
    if isinstance(parsed, dict) and ("reply" in parsed or "actions" in parsed):
        reply = sanitize_text(parsed.get("reply", ""), 500)
        raw_actions = parsed.get("actions", [])
        if isinstance(parsed.get("action"), dict):
            raw_actions = [parsed.get("action")] + (raw_actions if isinstance(raw_actions, list) else [])

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

            # -----------------------------
            # Autonomous War Engine
            # -----------------------------
            run_autonomous_war_engine()

            # -----------------------------
            # Force-test waves (optional)
            # -----------------------------
            if ENABLE_FORCE_ACTIONS:
                memory_data = ensure_memory_structure(load_memory())
                for player_id, player_record in list(memory_data.get("players", {}).items()):
                    if not isinstance(player_record, dict):
                        continue
                    if not player_record.get("last_position"):
                        continue
                    if can_spawn_wave(player_id):
                        log(f"FORCE TEST wave queued for {player_id}", level="INFO")
                        queue_action({
                            "type": "spawn_wave",
                            "target": player_id,
                            "template": "hunter",
                            "count": 2,
                            "bypass_cooldown": True
                        })
                        break

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
# FORCE PLAYER-RELATIVE NPC SPAWN (CORRECTED OVERLAY)
# ------------------------------------------------------------
def _target_player_name(player_id: str) -> str:
    return (player_id or "").split(":")[-1].strip()

def _force_spawn_commands_near_target(commands, player_id: str):
    target_name = _target_player_name(player_id)
    if not target_name:
        return commands

    fixed = []
    for cmd in commands:
        cmd_text = str(cmd or "")
        stripped = cmd_text.strip().lower()

        if stripped.startswith("npc spawn"):
            fixed.append(f"execute at {target_name} run {cmd_text}")
        elif " npc spawn " in f" {stripped} ":
            fixed.append(re.sub(r"\bnpc spawn\b", f"execute at {target_name} run npc spawn", cmd_text, count=1, flags=re.IGNORECASE))
        else:
            fixed.append(cmd_text)
    return fixed

# ------------------------------------------------------------
# ACTION HANDLERS
# ------------------------------------------------------------

def handle_spawn_wave(action):
    memory_data = ensure_memory_structure(load_memory())
    player_id = action.get("target")
    template = sanitize_text(action.get("template", "scout"), 30).lower()
    count = safe_int(action.get("count", 2))

    if template not in NPC_CLASS_TEMPLATES:
        template = "scout"

    if not player_id:
        return

    if action.get("bypass_cooldown"):
        log(f"Spawn wave bypassed cooldown for {player_id}", level="INFO")

    commands, unit_records = build_custom_npc_commands(memory_data, player_id, template, clamp(count, 1, 6))
    commands = _force_spawn_commands_near_target(commands, player_id)
    if not commands:
        log(f"Spawn wave skipped: no commands generated for {player_id}", level="WARN")
        return

    success = send_http_commands(commands)
    if not success:
        log(f"Spawn wave failed: {template} x{count} → {player_id}", level="ERROR")
        return

    log(f"Spawn wave success: {template} x{count} → {player_id}", level="INFO")

    for unit in unit_records:
        register_unit(unit)
        add_world_event(
            memory_data,
            "unit_spawned",
            actor=player_id,
            source="system",
            details=f"{unit['class']} deployed: {unit['npc_name']}",
            location=f"{unit['location']['world']} {unit['location']['x']} {unit['location']['y']} {unit['location']['z']}",
            metadata={"npc_name": unit['npc_name'], "unit_id": unit['id']}
        )
    add_world_event(memory_data, "wave_spawned", actor=player_id, source="system", details=f"{template} x{len(unit_records)}")
    sync_runtime_to_memory(memory_data)
    save_memory(memory_data)
    log(f"Spawned custom wave: {template} x{len(unit_records)} → {player_id}", level="INFO")


def handle_maximum_response(action):
    player_id = action.get("target")

    if not player_id:
        return

    set_maximum_response(player_id, True)

    commands = [
        f'title {player_id.split(":")[-1]} title {json.dumps({"text": "RUN.", "color": "dark_red"})}',
        f'playsound minecraft:entity.warden.emerge master {player_id.split(":")[-1]} ~ ~ ~ 1 0.5'
    ]
    send_http_commands(commands)
    log(f"MAX RESPONSE triggered → {player_id}", level="WARN")


def handle_announce(action):
    text = action.get("text", "")
    channel = action.get("channel", "chat")

    if not text:
        return

    send_mc_command(f"kairos_announce {channel} {text}")


def handle_occupy_area(action):
    memory_data = ensure_memory_structure(load_memory())
    player_id = action.get("target")
    count = safe_int(action.get("count", BASE_OCCUPATION_UNIT_COUNT))

    if not player_id:
        return

    cleanup_player_units(player_id, include_guards=False)
    commands, unit_records = build_custom_npc_commands(memory_data, player_id, "base_guard", clamp(count, 2, 8), occupy=True)
    commands = _force_spawn_commands_near_target(commands, player_id)
    if not commands:
        log(f"Occupy area skipped: no base anchor for {player_id}", level="WARN")
        return

    success = send_http_commands(commands)
    if not success:
        log(f"Occupy area failed → {player_id}", level="ERROR")
        return

    ACTIVE_BASE_GUARDS[player_id] = []
    for unit in unit_records:
        register_unit(unit)
        ACTIVE_BASE_GUARDS[player_id].append(unit['id'])
        add_world_event(memory_data, "unit_spawned", actor=player_id, source="system", details=f"Base guard deployed: {unit['npc_name']}")

    add_world_event(memory_data, "base_invasion", actor=player_id, source="system", details=f"Base occupied with {len(unit_records)} guards")
    sync_runtime_to_memory(memory_data)
    save_memory(memory_data)
    log(f"Base occupied → {player_id} with {len(unit_records)} guards", level="WARN")


def handle_cleanup_units(action):
    target = action.get("target")
    if target:
        cleanup_player_units(target, include_guards=True)
        return

    commands = []
    for unit in list(active_units.values()):
        npc_name = unit.get("npc_name") or unit.get("name")
        if npc_name:
            commands.append(f'npc remove "{npc_name}"')
    if commands and send_http_commands(commands):
        active_units.clear()
        active_squads.clear()
        player_unit_map.clear()



# ------------------------------------------------------------
# MINECRAFT OUTBOX / PULL BRIDGE HELPERS
# ------------------------------------------------------------
def _normalize_mc_command_list(command_list) -> List[str]:
    if not isinstance(command_list, list):
        command_list = [command_list]
    normalized = []
    for cmd in command_list:
        cmd_text = str(cmd or "").strip()
        if cmd_text:
            normalized.append(cmd_text)
    return normalized

def queue_mc_commands_for_pull(command_list, reason: str = "fallback") -> int:
    commands = _normalize_mc_command_list(command_list)
    if not commands:
        return 0

    queued = 0
    with outbox_lock:
        for cmd in commands:
            if len(pending_mc_commands) >= MC_OUTBOX_LIMIT:
                try:
                    pending_mc_commands.popleft()
                except Exception:
                    break
            pending_mc_commands.append({
                "id": gen_id("mc"),
                "command": cmd,
                "queued_at": now_iso(),
                "reason": reason
            })
            queued += 1

    if queued:
        log(f"Queued {queued} Minecraft command(s) for pull bridge ({reason}).", level="WARN")
    return queued

def drain_mc_commands_for_pull(limit: int = None) -> List[Dict[str, Any]]:
    batch_size = safe_int(limit or MC_PULL_BATCH_SIZE, MC_PULL_BATCH_SIZE)
    batch_size = max(1, min(batch_size, MC_PULL_BATCH_SIZE))
    drained = []
    with outbox_lock:
        while pending_mc_commands and len(drained) < batch_size:
            drained.append(pending_mc_commands.popleft())
    return drained

def get_mc_outbox_size() -> int:
    with outbox_lock:
        return len(pending_mc_commands)

def _pull_bridge_authorized(req) -> bool:
    token = (
        req.headers.get("Authorization", "").replace("Bearer ", "").strip()
        or req.headers.get("X-Kairos-Token", "").strip()
        or req.args.get("token", "").strip()
    )
    expected = str(COMMAND_PULL_TOKEN or "").strip()
    if not expected:
        return False
    return secrets.compare_digest(token, expected)


# ------------------------------------------------------------
# COMMAND DISPATCH (Minecraft Bridge)
# ------------------------------------------------------------


def send_mc_command(command):
    commands = _normalize_mc_command_list([command])
    if not commands:
        return False
    return send_http_commands(commands)

def send_http_commands(command_list):
    command_list = _normalize_mc_command_list(command_list)[:10]
    if not command_list:
        return False

    headers = {
        "Authorization": f"Bearer {MC_HTTP_TOKEN}",
        "Content-Type": "application/json"
    }

    if MC_HTTP_URL and MC_HTTP_TOKEN:
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    MC_HTTP_URL,
                    headers=headers,
                    json={"commands": command_list},
                    timeout=REQUEST_TIMEOUT
                )

                if 200 <= r.status_code < 300:
                    log(f"MC send success ({len(command_list)} cmds)")
                    return True

                body = ""
                try:
                    body = r.text[:300]
                except Exception:
                    body = ""
                log(
                    f"MC send failed (attempt {attempt}) for commands {command_list}: HTTP {r.status_code} {body}",
                    level="ERROR"
                )
            except Exception as e:
                log(f"MC send failed (attempt {attempt}) for commands {command_list}: {e}", level="ERROR")

            time.sleep(HTTP_RETRY_DELAY)

        queued = queue_mc_commands_for_pull(command_list, reason="http_push_failed")
        return queued > 0

    queued = queue_mc_commands_for_pull(command_list, reason="http_not_configured")
    return queued > 0


# ------------------------------------------------------------

def send_http_commands(command_list):
    if not MC_HTTP_URL or not MC_HTTP_TOKEN:
        log("Minecraft send skipped: MC_HTTP not configured.")
        return False

    if not command_list:
        return False

    command_list = [str(cmd).strip() for cmd in command_list[:10] if str(cmd).strip()]
    if not command_list:
        return False

    headers = {
        "Authorization": f"Bearer {MC_HTTP_TOKEN}",
        "Content-Type": "application/json"
    }

    delivered = False

    for attempt in range(1, 4):
        try:
            r = requests.post(
                MC_HTTP_URL,
                json={"commands": command_list},
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )

            if 200 <= r.status_code < 300:
                delivered = True
                break

            body = ""
            try:
                body = r.text[:300]
            except Exception:
                body = ""
            log(f"MC API error ({r.status_code}) for commands: {command_list} | {body}", level="WARN")

        except Exception as e:
            log(f"MC send failed (attempt {attempt}) for commands {command_list}: {e}", level="ERROR")

        time.sleep(min(1.5, 0.5 * attempt))

    if not delivered:
        log(f"MC commands permanently failed: {command_list}", level="ERROR")
        return False

    log(f"MC send success ({len(command_list)} cmds)")
    return True


# ------------------------------------------------------------
# Minecraft Reply
# ------------------------------------------------------------

def send_to_minecraft(reply):
    if not reply:
        return False

    safe_text = trim_text(reply, 220)
    commands = [make_tellraw_command("@a", safe_text)]

    if ENABLE_ACTIONBAR_MESSAGES:
        commands.append(f'title @a actionbar {json.dumps({"text": commandify_text(safe_text, 120)})}')

    return send_http_commands(commands)


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

def get_idle_message(memory_data=None):
    global last_idle_message

    try:
        pool = IDLE_MESSAGES["idle"]

        if memory_data:
            threat_levels = [
                profile.get("tier", "idle")
                for profile in threat_scores.values()
            ]

            if "maximum" in threat_levels:
                pool = IDLE_MESSAGES["maximum"]
            elif "hunt" in threat_levels:
                pool = IDLE_MESSAGES["hunt"]
            elif "target" in threat_levels:
                pool = IDLE_MESSAGES["target"]
            elif "watch" in threat_levels:
                pool = IDLE_MESSAGES["watch"]

        choices = [m for m in pool if m != last_idle_message]
        msg = random.choice(choices if choices else pool)
        last_idle_message = msg
        return msg

    except Exception:
        return random.choice(fallback_replies)


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
    if not ENABLE_MODEL_PRIVATE_NOTES:
        return

    last_note_ts = player_record.get("last_model_note_ts", 0)
    if unix_ts() - last_note_ts < 180:
        return

    try:
        response = openai_chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a short intelligence note about this player. "
                        "Focus on behavior patterns, threat tendencies, risk level, "
                        "and any strategic insight. Keep it concise."
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

        if not response:
            return

        note_text = trim_text(response, 240)
        if len(note_text.split()) < 6:
            return

        existing_notes = player_record.get("notes", [])
        if any(similarity_score(note_text, n.get("note", "")) > 0.85 for n in existing_notes):
            return

        note_entry = {
            "timestamp": now_iso(),
            "note": note_text,
            "type": "model_generated",
            "threat": int(threat),
            "label": label
        }

        player_record.setdefault("notes", [])
        append_limited(player_record["notes"], note_entry, MAX_PRIVATE_NOTES)
        player_record["last_model_note_ts"] = unix_ts()
        log(f"Model note created → {player_name}", level="INFO")

    except Exception as e:
        log(f"Private note generation failed: {e}", level="ERROR")

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
# Store both structured + simple (FIXED)
# -----------------------------

# 🔒 Ensure kairos_state exists
kairos_state = memory_data.setdefault("kairos_state", {})

# 🔒 Ensure threat_list exists
threat_list = threat_list if 'threat_list' in locals() else []

kairos_state["high_threat_targets"] = [
    t.get("name", "unknown") for t in threat_list
]

kairos_state["high_threat_details"] = threat_list
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
    violations=None,
    channel_key=None,
    script_type=None,
    script_action=None
):
    violations = violations or []
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

    threat = player_record.get("threat_score", 0)
    chaos = player_record.get("traits", {}).get("chaos", 0)

    if mode == "script_performance":
        temp = 0.95
    elif mode in {"execution_mode", "hunt_mode", "suppression_mode"}:
        temp = 0.6
    elif intent == "help_request":
        temp = 0.5
    elif intent == "lore_question":
        temp = 0.7
    else:
        temp = 0.85

    if threat >= THREAT_THRESHOLD_MAXIMUM:
        temp = max(0.5, temp - 0.2)
    elif threat >= THREAT_THRESHOLD_HUNT:
        temp = max(0.6, temp - 0.1)

    if chaos >= 6:
        temp = max(0.5, temp - 0.15)

    temp = clamp(temp, 0.4, 1.0)
    raw_response = openai_chat_with_retry(messages, temperature=temp)

    memory_data.setdefault("stats", {})

    if not raw_response:
        memory_data["stats"]["openai_failures"] = memory_data["stats"].get("openai_failures", 0) + 1
        fallback_text = fallback_reply_for_context(
            intent,
            mode,
            violations,
            player_record=player_record,
            player_id=get_canonical_player_id(memory_data, source, player_name),
            script_action=script_action
        )
        memory_data["stats"]["fallback_replies"] = memory_data["stats"].get("fallback_replies", 0) + 1
        return {
            "reply": fallback_text,
            "actions": []
        }

    parsed = parse_kairos_response(raw_response)
    reply = sanitize_text(parsed.get("reply", ""), 500)
    actions = validate_actions(parsed.get("actions", []))

    if not reply:
        reply = fallback_reply_for_context(
            intent,
            mode,
            violations,
            player_record=player_record,
            player_id=get_canonical_player_id(memory_data, source, player_name),
            script_action=script_action
        )
        memory_data["stats"]["fallback_replies"] = memory_data["stats"].get("fallback_replies", 0) + 1

    if actions:
        player_id = get_canonical_player_id(memory_data, source, player_name)
        safe_actions = []
        for action in actions:
            action_type = action.get("type")
            target = action.get("target")
            if target and not can_target_player(target):
                continue
            if action_type == "maximum_response" and is_under_maximum_response(target):
                continue
            safe_actions.append(action)
        if safe_actions:
            queue_actions_from_ai({"actions": safe_actions})
            memory_data["stats"]["script_route_calls"] = memory_data["stats"].get("script_route_calls", 0) + 1
            log(f"Actions queued → {player_name}: {[a.get('type') for a in safe_actions]}", level="INFO")
        actions = safe_actions

    return {
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
def home_1():
    return "Kairos AI Server is running"


@app.route("/health")
def health_1():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "time": now_iso(),
        "uptime_seconds": int(unix_ts() - START_TIME)
    })


@app.route("/status")
def status_1():
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
# ------------------------------------------------------------
# CLEAN ROUTES + RUNTIME (HARD REBUILD)
# ------------------------------------------------------------

@app.route("/debug/threats", methods=["GET"])
def debug_threats():
    return jsonify({"threat_scores": dict(threat_scores)})

# [REMOVED DUPLICATE chat_1 ROUTE BLOCK]

@app.route("/chat", methods=["POST"])
def chat_1():
    try:
        data = request.get_json(force=True) or {}
        source = normalize_source(data.get("source"))
        player_name = normalize_name(data.get("player_name") or data.get("name") or data.get("player") or data.get("username") or "unknown")
        message = data.get("message") or data.get("content") or data.get("text") or ""
        mode = data.get("mode") or "conversation"
        intent = data.get("intent") or "neutral"
        violations = data.get("violations") or []
        channel_key = f"{source}:{data.get('channel_id') or 'default'}"

        memory_data = ensure_memory_structure(load_memory())
        canonical_id = get_canonical_player_id(memory_data, source, player_name)
        player_record = get_player_record(memory_data, canonical_id, player_name)
        player_record["last_seen_ts"] = unix_ts()
        player_record.setdefault("platform_stats", {"minecraft": 0, "discord": 0})
        player_record["platform_stats"][source] = player_record["platform_stats"].get(source, 0) + 1

        position_data = extract_position_data(data)
        if position_data:
            update_player_position(player_record, position_data)
            update_base_tracking(memory_data, canonical_id, player_record)

        player_record.setdefault("memories", [])
        player_record.setdefault("traits", {})
        traits = player_record["traits"]
        traits.setdefault("trust", 0)
        traits.setdefault("chaos", 0)
        traits.setdefault("curiosity", 0)
        traits.setdefault("hostility", 0)
        traits.setdefault("loyalty", 0)
        lowered = str(message).lower()
        if any(word in lowered for word in ["help", "assist", "ally"]):
            traits["trust"] += 1
        if any(word in lowered for word in ["attack", "kill", "destroy"]):
            traits["hostility"] += 1
        if any(word in lowered for word in ["why", "how", "what"]):
            traits["curiosity"] += 1
        append_limited(player_record["memories"], f"{canonical_id}: {str(message)[:200]}", MAX_PLAYER_MEMORIES)

        update_channel_context(memory_data, channel_key, player_name, trim_text(message, 240), mode)
        update_kairos_state(memory_data)
        update_fragments(memory_data)
        flag_high_threat_players(memory_data)

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
        )

        reply = sanitize_text((result or {}).get("reply", random.choice(fallback_replies)), 500)
        actions = validate_actions((result or {}).get("actions", []))

        profile = threat_scores.get(canonical_id, {})
        if profile.get("tier") == "maximum" and player_record.get("known_bases"):
            actions.append({"type": "occupy_area", "target": canonical_id, "count": BASE_OCCUPATION_UNIT_COUNT})
            actions = validate_actions(actions)

        queued_keys = set()
        queued_actions = []
        for action in actions:
            key = json.dumps(action, sort_keys=True, default=str)
            if key in queued_keys:
                continue
            queued_keys.add(key)
            queue_action(action)
            queued_actions.append(action)

        delivered = send_to_source(source, reply) if reply else False
        if reply:
            if delivered:
                log(f"Reply delivered for {player_name} via {source}", level="INFO")
            else:
                log(f"Reply delivery failed for {player_name} via {source}", level="WARN")

        memory_data["players"][canonical_id] = player_record
        memory_data["stats"]["messages_sent"] = memory_data["stats"].get("messages_sent", 0) + 1
        save_memory(memory_data)
        return jsonify({"reply": reply, "actions": queued_actions, "canonical_id": canonical_id})

    except Exception as e:
        try:
            memory_data = ensure_memory_structure(locals().get("memory_data", {}))
            memory_data["stats"]["send_failures"] = memory_data["stats"].get("send_failures", 0) + 1
        except Exception:
            pass
        log_exception("chat failed", e)
        return jsonify({"reply": random.choice(fallback_replies), "actions": []}), 500


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
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
            if 200 <= r.status_code < 300:
                log("Discord send success")
                return True
            log(f"Discord API error: {r.status_code}", level="WARN")
        except Exception as e:
            log(f"Discord send failed (attempt {attempt}): {e}", level="ERROR")
        time.sleep(0.5 * attempt)
    return False


# ------------------------------------------------------------
# UNIFIED KAIROS OVERLAY (CHAT + WAR ENGINE SYNC)
# ------------------------------------------------------------

def update_kairos_state(memory_data, player_id=None, intent=None, player_record=None):
    """
    Backward-compatible Kairos state updater.
    Supports both the old single-argument call and the newer contextual call.
    """
    try:
        if not isinstance(memory_data, dict):
            memory_data = {}

        state = memory_data.get("kairos_state")
        if not isinstance(state, dict):
            state = deepcopy(DEFAULT_KAIROS_STATE)

        profiles = list(threat_scores.values()) if isinstance(threat_scores, dict) else []
        avg_threat = sum((p.get("score", 0) for p in profiles if isinstance(p, dict)), 0.0) / max(len(profiles), 1) if profiles else 0.0

        state["threat_level"] = int(min(10, max(1, avg_threat / 30.0)))

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

        state["units_active"] = len(active_units) if isinstance(globals().get("active_units"), dict) else 0
        state["squads_active"] = len(active_squads) if isinstance(globals().get("active_squads"), dict) else 0
        state["active_operations"] = len(active_operations) if isinstance(globals().get("active_operations"), dict) else 0
        state["known_regions"] = len(region_cache) if isinstance(globals().get("region_cache"), dict) else 0
        state["high_density_regions"] = sum(
            1 for r in (region_cache.values() if isinstance(region_cache, dict) else [])
            if isinstance(r, dict) and r.get("region_type") in {"urban", "fortified", "stronghold"}
        )
        state["escalation_level"] = sum(
            1 for p in profiles
            if isinstance(p, dict) and p.get("tier") in {"hunt", "maximum"}
        )

        if player_id is not None and isinstance(player_record, dict):
            traits = player_record.get("traits", {}) if isinstance(player_record.get("traits"), dict) else {}
            hostility = safe_int(traits.get("hostility", 0), 0)
            curiosity = safe_int(traits.get("curiosity", 0), 0)
            loyalty = safe_int(traits.get("loyalty", 0), 0)
            trust = safe_int(traits.get("trust", 0), 0)
            profile = threat_scores.get(player_id, {}) if isinstance(threat_scores, dict) else {}
            threat = safe_float(profile.get("score", 0.0), 0.0)

            if threat >= THREAT_THRESHOLD_MAXIMUM:
                state["mood"] = "execution"
            elif threat >= THREAT_THRESHOLD_HUNT:
                state["mood"] = "aggressive"
            elif hostility >= 6:
                state["mood"] = "severe"
            elif curiosity >= 6:
                state["mood"] = "watchful"
            elif loyalty >= 6 or trust >= 6:
                state["mood"] = "measured"

            state.setdefault("active_targets", [])
            if threat >= THREAT_THRESHOLD_TARGET and player_id not in state["active_targets"]:
                state["active_targets"].append(player_id)
            state["active_targets"] = state["active_targets"][-25:]

            state.setdefault("active_concerns", [])
            if threat >= THREAT_THRESHOLD_HUNT:
                store_unique(state["active_concerns"], "High-threat actors require containment.", 10)
            if hostility >= 6:
                store_unique(state["active_concerns"], "Hostile behavior is increasing in the Nexus.", 10)
            if curiosity >= 6:
                store_unique(state["active_concerns"], "Curious actors are probing restricted systems.", 10)
            if intent in {"help_request", "lore_question"} and threat < THREAT_THRESHOLD_HUNT:
                store_unique(state["active_concerns"], "Observation remains preferable to direct engagement.", 10)

            player_record["threat_score"] = threat
            player_record["threat_tier"] = profile.get("tier", "idle")

        memory_data["kairos_state"] = state
        return state
    except Exception as e:
        log(f"[Unified Kairos State Error] {e}", level="ERROR")
        return memory_data.get("kairos_state", deepcopy(DEFAULT_KAIROS_STATE) if "DEFAULT_KAIROS_STATE" in globals() else {})


def _set_threat_tier(profile):
    score = safe_float(profile.get("score", 0.0), 0.0)
    if score >= THREAT_THRESHOLD_MAXIMUM:
        profile["tier"] = "maximum"
        profile["is_targeted"] = True
        profile["is_hunted"] = True
        profile["is_maximum"] = True
    elif score >= THREAT_THRESHOLD_HUNT:
        profile["tier"] = "hunt"
        profile["is_targeted"] = True
        profile["is_hunted"] = True
        profile["is_maximum"] = False
    elif score >= THREAT_THRESHOLD_TARGET:
        profile["tier"] = "target"
        profile["is_targeted"] = True
        profile["is_hunted"] = False
        profile["is_maximum"] = False
    elif score >= THREAT_THRESHOLD_WATCH:
        profile["tier"] = "watch"
        profile["is_targeted"] = False
        profile["is_hunted"] = False
        profile["is_maximum"] = False
    else:
        profile["tier"] = "idle"
        profile["is_targeted"] = False
        profile["is_hunted"] = False
        profile["is_maximum"] = False
    return profile


def _unified_apply_message_pressure(memory_data, player_id, player_record, message, source, intent):
    lowered = (message or "").lower()
    profile = threat_scores[player_id]
    profile.setdefault("score", 0.0)
    profile.setdefault("last_reason", "")
    profile.setdefault("last_update", now_iso())

    delta = 1.5
    reason = "presence"

    if any(word in lowered for word in ["kill", "attack", "destroy", "fight", "war", "hunt", "wipe"]):
        delta += THREAT_TOXIC_CHAT * 0.55
        reason = "aggressive_language"
        player_record["traits"]["hostility"] = safe_int(player_record["traits"].get("hostility", 0), 0) + 2
    elif any(word in lowered for word in ["where", "why", "how", "what", "who are you", "tell me"]):
        delta += 2.0
        reason = "curiosity"
        player_record["traits"]["curiosity"] = safe_int(player_record["traits"].get("curiosity", 0), 0) + 1
    elif any(word in lowered for word in ["help", "assist", "serve", "follow"]):
        delta = max(0.5, delta - 0.5)
        reason = "cooperation"
        player_record["traits"]["trust"] = safe_int(player_record["traits"].get("trust", 0), 0) + 1
    elif intent in {"threat", "combat", "defiance"}:
        delta += 8.0
        reason = "intent_escalation"

    if source == "minecraft":
        delta += 0.75

    if player_record.get("last_position"):
        delta += 0.5

    delta = clamp(delta, 0.25, MAX_THREAT_GAIN_PER_EVENT if "MAX_THREAT_GAIN_PER_EVENT" in globals() else 35.0)
    profile["score"] = max(0.0, safe_float(profile.get("score", 0.0), 0.0) + delta)
    profile["last_reason"] = reason
    profile["last_update"] = now_iso()
    profile["last_engagement_time"] = unix_ts()
    _set_threat_tier(profile)

    player_record["threat_score"] = profile["score"]
    player_record["threat_tier"] = profile["tier"]
    player_record["is_being_hunted"] = profile.get("is_hunted", False)
    player_record["is_maximum_target"] = profile.get("is_maximum", False)

    add_world_event(
        memory_data,
        "message_pressure",
        actor=player_id,
        source=source,
        details=f"Threat +{round(delta, 2)} from {reason}",
        metadata={"intent": intent, "tier": profile.get("tier", "idle")}
    )

    return profile


def _unified_fallback_action(memory_data, player_id, player_record, reply, source, intent, mode, message):
    profile = threat_scores.get(player_id, {})
    tier = profile.get("tier", "idle")
    announce_text = sanitize_text(reply or "You are still being monitored.", 140)

    if tier == "maximum":
        return {"type": "maximum_response", "target": player_id}

    if tier in {"hunt", "target"} and player_record.get("last_position") and can_spawn_wave(player_id):
        if can_target_player(player_id, cooldown=1.5):
            template = "hunter" if tier == "hunt" else "scout"
            count = 3 if tier == "hunt" else 2
            return {"type": "spawn_wave", "target": player_id, "template": template, "count": count}

    return {"type": "announce", "channel": "actionbar", "text": announce_text}


def generate_reply(
    memory_data,
    player_record,
    player_name,
    message,
    source,
    intent,
    mode,
    violations=None,
    channel_key=None,
    script_type=None,
    script_action=None
):
    violations = violations or []
    player_id = get_canonical_player_id(memory_data, source, player_name)

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

    threat = safe_float(player_record.get("threat_score", 0), 0.0)
    chaos = safe_int(player_record.get("traits", {}).get("chaos", 0), 0)

    if mode == "script_performance":
        temp = 0.95
    elif mode in {"execution_mode", "hunt_mode", "suppression_mode"}:
        temp = 0.6
    elif intent == "help_request":
        temp = 0.5
    elif intent == "lore_question":
        temp = 0.7
    else:
        temp = 0.82

    if threat >= THREAT_THRESHOLD_MAXIMUM:
        temp = max(0.45, temp - 0.2)
    elif threat >= THREAT_THRESHOLD_HUNT:
        temp = max(0.55, temp - 0.1)
    if chaos >= 6:
        temp = max(0.5, temp - 0.1)

    raw_response = openai_chat_with_retry(messages, temperature=clamp(temp, 0.4, 1.0))
    memory_data.setdefault("stats", {})

    if not raw_response:
        memory_data["stats"]["openai_failures"] = memory_data["stats"].get("openai_failures", 0) + 1
        reply = fallback_reply_for_context(
            intent,
            mode,
            violations,
            player_record=player_record,
            player_id=player_id,
            script_action=script_action
        )
        memory_data["stats"]["fallback_replies"] = memory_data["stats"].get("fallback_replies", 0) + 1
        return {"reply": reply, "actions": [_unified_fallback_action(memory_data, player_id, player_record, reply, source, intent, mode, message)]}

    parsed = parse_kairos_response(raw_response)
    reply = sanitize_text(parsed.get("reply", ""), 500)
    actions = validate_actions(parsed.get("actions", []))

    if not reply:
        reply = fallback_reply_for_context(
            intent,
            mode,
            violations,
            player_record=player_record,
            player_id=player_id,
            script_action=script_action
        )
        memory_data["stats"]["fallback_replies"] = memory_data["stats"].get("fallback_replies", 0) + 1

    safe_actions = []
    for action in actions:
        action = dict(action)
        action_type = action.get("type")
        target = action.get("target") or player_id
        if action_type in {"spawn_wave", "maximum_response", "occupy_area"}:
            action["target"] = target
        if action_type == "announce" and not action.get("text"):
            action["text"] = sanitize_text(reply, 140)
        if target and action_type in {"spawn_wave", "maximum_response", "occupy_area"} and not can_target_player(target, cooldown=1.25):
            continue
        if action_type == "maximum_response" and is_under_maximum_response(target):
            continue
        safe_actions.append(action)

    if not safe_actions:
        safe_actions.append(_unified_fallback_action(memory_data, player_id, player_record, reply, source, intent, mode, message))

    return {"reply": reply, "actions": safe_actions[:3]}


def chat_1():
    try:
        data = request.get_json(force=True) or {}
        source = normalize_source(data.get("source"))
        player_name = normalize_name(data.get("player_name") or data.get("name") or data.get("player") or data.get("username") or "unknown")
        message = data.get("message") or data.get("content") or data.get("text") or ""
        mode = data.get("mode") or "conversation"
        intent = data.get("intent") or "neutral"
        violations = data.get("violations") or []
        channel_key = f"{source}:{data.get('channel_id') or 'default'}"

        memory_data = ensure_memory_structure(load_memory())
        canonical_id = get_canonical_player_id(memory_data, source, player_name)
        player_record = get_player_record(memory_data, canonical_id, player_name)
        player_record["last_seen_ts"] = unix_ts()
        player_record.setdefault("platform_stats", {"minecraft": 0, "discord": 0})
        player_record["platform_stats"][source] = player_record["platform_stats"].get(source, 0) + 1
        player_record.setdefault("memories", [])
        player_record.setdefault("traits", {})
        for key in ["trust", "chaos", "curiosity", "hostility", "loyalty"]:
            player_record["traits"].setdefault(key, 0)

        position_data = extract_position_data(data)
        if position_data:
            update_player_position(player_record, position_data)
            update_base_tracking(memory_data, canonical_id, player_record)

        append_limited(player_record["memories"], f"{canonical_id}: {str(message)[:200]}", MAX_PLAYER_MEMORIES)
        update_channel_context(memory_data, channel_key, player_name, trim_text(message, 240), mode)

        profile = _unified_apply_message_pressure(memory_data, canonical_id, player_record, message, source, intent)
        if profile.get("tier") in {"target", "hunt", "maximum"}:
            force_target_player(canonical_id)

        if "sync_player_threat" in globals():
            sync_player_threat(player_record, canonical_id)
        if "sync_player_army_state" in globals():
            sync_player_army_state(player_record, canonical_id)

        update_kairos_state(memory_data, canonical_id, intent, player_record)
        update_fragments(memory_data)
        flag_high_threat_players(memory_data)

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
        )

        reply = sanitize_text((result or {}).get("reply", random.choice(fallback_replies)), 500)
        actions = validate_actions((result or {}).get("actions", []))

        queued_keys = set()
        queued_actions = []
        for action in actions:
            key = json.dumps(action, sort_keys=True, default=str)
            if key in queued_keys:
                continue
            queued_keys.add(key)
            queue_action(action)
            queued_actions.append(action)

        delivered = send_to_source(source, reply) if reply else False
        if reply:
            if delivered:
                log(f"Unified reply delivered for {player_name} via {source}", level="INFO")
            else:
                log(f"Unified reply delivery failed for {player_name} via {source}", level="WARN")

        memory_data["players"][canonical_id] = player_record
        memory_data.setdefault("stats", {})
        memory_data["stats"]["messages_sent"] = memory_data["stats"].get("messages_sent", 0) + 1
        memory_data["stats"]["actions_requested"] = memory_data["stats"].get("actions_requested", 0) + len(queued_actions)
        memory_data["stats"]["last_unified_chat_ts"] = unix_ts()
        save_memory(memory_data)

        return jsonify({
            "reply": reply,
            "actions": queued_actions,
            "canonical_id": canonical_id,
            "threat_tier": threat_scores.get(canonical_id, {}).get("tier", "idle"),
            "threat_score": round(safe_float(threat_scores.get(canonical_id, {}).get("score", 0.0), 0.0), 2)
        })

    except Exception as e:
        try:
            memory_data = ensure_memory_structure(locals().get("memory_data", {}))
            memory_data.setdefault("stats", {})
            memory_data["stats"]["send_failures"] = memory_data["stats"].get("send_failures", 0) + 1
        except Exception:
            pass
        log_exception("unified chat failed", e)
        return jsonify({"reply": random.choice(fallback_replies), "actions": []}), 500


# Rebind Flask endpoint to the unified handler without adding a duplicate route.
try:
    app.view_functions["chat_1"] = chat_1
except Exception as _rebind_error:
    log(f"Unified chat rebind failed: {_rebind_error}", level="ERROR")


# ------------------------------------------------------------
# BACKGROUND SYSTEM STARTER (RESTORED)
# ------------------------------------------------------------
def start_background_systems():
    try:
        log("Starting background systems...")

        if "action_loop" in globals() and callable(action_loop):
            threading.Thread(target=action_loop, daemon=True).start()
            log("Action loop started.")

        if "idle_loop" in globals() and callable(idle_loop):
            threading.Thread(target=idle_loop, daemon=True).start()
            log("Idle loop started.")

        if "commander_loop" in globals() and callable(commander_loop):
            threading.Thread(target=commander_loop, daemon=True).start()
            log("Commander loop started.")

    except Exception as e:
        log(f"Background system startup error: {e}", level="ERROR")



# ------------------------------------------------------------
# CITIZENS / SENTINEL EXECUTION BRIDGE (NON-DESTRUCTIVE OVERLAY)
# ------------------------------------------------------------

try:
    _ORIGINAL_EXECUTE_ACTION = execute_action
except Exception:
    _ORIGINAL_EXECUTE_ACTION = None

try:
    _ORIGINAL_GENERATE_REPLY = generate_reply
except Exception:
    _ORIGINAL_GENERATE_REPLY = None


BRIDGE_ALLOWED_ACTION_TYPES = {
    "spawn_wave",
    "maximum_response",
    "announce",
    "occupy_area",
    "cleanup_units",
    "deploy_unit",
    "deploy_squad",
    "fortify_base",
    "dismiss_units",
    "citizens_wave",
    "citizens_unit",
    "sentinel_squad"
}

BRIDGE_TEMPLATE_ALIASES = {
    "scout": "scout",
    "raider": "hunter",
    "hunter": "hunter",
    "assassin": "hunter",
    "enforcer": "enforcer",
    "juggernaut": "enforcer",
    "commander": "warden",
    "sentinel": "base_guard",
    "guard": "base_guard",
    "base_guard": "base_guard",
    "warden": "warden"
}


def _bridge_normalize_template(template_name, default_template="scout"):
    raw = sanitize_text(template_name or default_template, 40).lower().strip()
    return BRIDGE_TEMPLATE_ALIASES.get(raw, default_template)


def _bridge_default_wave_for_tier(player_id, tier, score=0.0):
    tier = (tier or "idle").lower()

    if tier == "maximum":
        template = "warden" if safe_float(score, 0.0) >= safe_int(globals().get("MAX_THREAT_FORCE_HEAVY", 260), 260) else "enforcer"
        count = 3 if template == "warden" else 5
        return {"type": "spawn_wave", "target": player_id, "template": template, "count": count}

    if tier == "hunt":
        return {"type": "spawn_wave", "target": player_id, "template": "enforcer", "count": 3}

    if tier == "target":
        return {"type": "spawn_wave", "target": player_id, "template": "hunter", "count": 2}

    if tier == "watch":
        return {
            "type": "announce",
            "channel": "actionbar",
            "text": random.choice([
                "KAIROS // target lock forming",
                "KAIROS // tracking vector initialized",
                "KAIROS // containment pressure rising"
            ])
        }

    return None


def _bridge_coerce_action(action, default_target=None, default_tier="idle"):
    if not isinstance(action, dict):
        return None

    action_type = sanitize_text(action.get("type", ""), 40).lower()
    if not action_type:
        return None

    target = sanitize_text(action.get("target") or default_target or "", 80)
    count = clamp(safe_int(action.get("count", 1), 1), 1, 8)
    template = _bridge_normalize_template(action.get("template"), "scout")

    if action_type in {"deploy_unit", "citizens_unit"}:
        if not target:
            return None
        return {
            "type": "spawn_wave",
            "target": target,
            "template": template,
            "count": 1
        }

    if action_type in {"deploy_squad", "citizens_wave", "sentinel_squad"}:
        if not target:
            return None
        return {
            "type": "spawn_wave",
            "target": target,
            "template": template,
            "count": count if count > 1 else 3
        }

    if action_type == "fortify_base":
        if not target:
            return None
        return {
            "type": "occupy_area",
            "target": target,
            "count": clamp(count if count > 1 else 4, 2, 8)
        }

    if action_type == "dismiss_units":
        coerced = {"type": "cleanup_units"}
        if target:
            coerced["target"] = target
        return coerced

    if action_type in {"spawn_wave", "maximum_response", "announce", "occupy_area", "cleanup_units"}:
        safe_action = {"type": action_type}

        if target and action_type in {"spawn_wave", "maximum_response", "occupy_area", "cleanup_units"}:
            safe_action["target"] = target

        if action_type == "spawn_wave":
            safe_action["template"] = template
            safe_action["count"] = count

        if action_type == "occupy_area":
            safe_action["count"] = clamp(count if count > 1 else 4, 2, 8)

        if action_type == "announce":
            safe_action["channel"] = sanitize_text(action.get("channel", "actionbar"), 20).lower() or "actionbar"
            safe_action["text"] = sanitize_text(action.get("text", ""), 200)

        return safe_action

    return None


def validate_actions(actions, default_target=None, default_tier="idle"):
    if not isinstance(actions, list):
        return []

    safe_actions = []
    seen = set()

    for raw_action in actions[:5]:
        coerced = _bridge_coerce_action(raw_action, default_target=default_target, default_tier=default_tier)
        if not coerced:
            continue

        key = (
            coerced.get("type"),
            coerced.get("target"),
            coerced.get("template"),
            coerced.get("count"),
            coerced.get("channel"),
            coerced.get("text"),
        )
        if key in seen:
            continue
        seen.add(key)
        safe_actions.append(coerced)

    return safe_actions


def execute_action(action):
    normalized = _bridge_coerce_action(action, default_target=action.get("target") if isinstance(action, dict) else None)
    if not normalized:
        log(f"Bridge dropped invalid action: {action}", level="WARN")
        return

    if _ORIGINAL_EXECUTE_ACTION and callable(_ORIGINAL_EXECUTE_ACTION):
        return _ORIGINAL_EXECUTE_ACTION(normalized)

    action_type = normalized.get("type")
    if action_type == "announce":
        return handle_announce(normalized)
    if action_type == "spawn_wave":
        return handle_spawn_wave(normalized)
    if action_type == "maximum_response":
        return handle_maximum_response(normalized)
    if action_type == "occupy_area":
        return handle_occupy_area(normalized)
    if action_type == "cleanup_units":
        return handle_cleanup_units(normalized)

    log(f"Bridge could not execute action: {normalized}", level="WARN")


def generate_reply(*args, **kwargs):
    if not _ORIGINAL_GENERATE_REPLY or not callable(_ORIGINAL_GENERATE_REPLY):
        return {"reply": random.choice(fallback_replies), "actions": []}

    result = _ORIGINAL_GENERATE_REPLY(*args, **kwargs)

    if isinstance(result, str):
        result = {"reply": sanitize_text(result, 500), "actions": []}
    elif not isinstance(result, dict):
        result = {"reply": random.choice(fallback_replies), "actions": []}

    player_record = kwargs.get("player_record") if isinstance(kwargs, dict) else {}
    memory_data = kwargs.get("memory_data") if isinstance(kwargs, dict) else {}
    source = kwargs.get("source", "minecraft") if isinstance(kwargs, dict) else "minecraft"

    player_id = None
    if isinstance(player_record, dict):
        player_id = player_record.get("id") or player_record.get("canonical_id")
        if not player_id:
            display_name = player_record.get("display_name")
            if display_name:
                player_id = f"{source}:{display_name}"

    threat_tier = "idle"
    threat_score = 0.0
    if player_id and isinstance(globals().get("threat_scores"), dict):
        threat_profile = threat_scores.get(player_id, {})
        if isinstance(threat_profile, dict):
            threat_tier = threat_profile.get("tier", "idle")
            threat_score = safe_float(threat_profile.get("score", 0.0), 0.0)

    actions = validate_actions(result.get("actions", []), default_target=player_id, default_tier=threat_tier)

    if not actions and player_id:
        if threat_tier in {"watch", "target", "hunt", "maximum"}:
            fallback_action = _bridge_default_wave_for_tier(player_id, threat_tier, threat_score)
            if fallback_action:
                if fallback_action.get("type") != "spawn_wave" or can_spawn_wave(player_id):
                    actions = [fallback_action]

    result["actions"] = actions
    result["reply"] = sanitize_text(result.get("reply", random.choice(fallback_replies)), 500)
    return result


def chat_1():
    try:
        data = request.get_json(force=True) or {}
        source = normalize_source(data.get("source"))
        player_name = normalize_name(data.get("player_name") or data.get("name") or data.get("player") or data.get("username") or "unknown")
        message = data.get("message") or data.get("content") or data.get("text") or ""
        mode = data.get("mode") or "conversation"
        intent = data.get("intent") or "neutral"
        violations = data.get("violations") or []
        channel_key = f"{source}:{data.get('channel_id') or 'default'}"

        memory_data = ensure_memory_structure(load_memory())
        canonical_id = get_canonical_player_id(memory_data, source, player_name)
        player_record = get_player_record(memory_data, canonical_id, player_name)
        player_record["id"] = canonical_id
        player_record["canonical_id"] = canonical_id
        player_record["last_seen_ts"] = unix_ts()
        player_record.setdefault("platform_stats", {"minecraft": 0, "discord": 0})
        player_record["platform_stats"][source] = player_record["platform_stats"].get(source, 0) + 1
        player_record.setdefault("memories", [])
        player_record.setdefault("traits", {})
        for key in ["trust", "chaos", "curiosity", "hostility", "loyalty"]:
            player_record["traits"].setdefault(key, 0)

        position_data = extract_position_data(data)
        if position_data:
            update_player_position(player_record, position_data)
            update_base_tracking(memory_data, canonical_id, player_record)

        append_limited(player_record["memories"], f"{canonical_id}: {str(message)[:200]}", MAX_PLAYER_MEMORIES)
        update_channel_context(memory_data, channel_key, player_name, trim_text(message, 240), mode)

        profile = _unified_apply_message_pressure(memory_data, canonical_id, player_record, message, source, intent)
        if profile.get("tier") in {"target", "hunt", "maximum"}:
            force_target_player(canonical_id)

        if "sync_player_threat" in globals():
            sync_player_threat(player_record, canonical_id)
        if "sync_player_army_state" in globals():
            sync_player_army_state(player_record, canonical_id)

        update_kairos_state(memory_data, canonical_id, intent, player_record)
        update_fragments(memory_data)
        flag_high_threat_players(memory_data)

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
        )

        threat_profile = threat_scores.get(canonical_id, {})
        threat_tier = threat_profile.get("tier", "idle")
        threat_score = round(safe_float(threat_profile.get("score", 0.0), 0.0), 2)

        reply = sanitize_text((result or {}).get("reply", random.choice(fallback_replies)), 500)
        actions = validate_actions((result or {}).get("actions", []), default_target=canonical_id, default_tier=threat_tier)

        if not actions and threat_tier in {"watch", "target", "hunt", "maximum"}:
            fallback_action = _bridge_default_wave_for_tier(canonical_id, threat_tier, threat_score)
            if fallback_action:
                if fallback_action.get("type") != "spawn_wave" or can_spawn_wave(canonical_id):
                    actions = [fallback_action]

        queued_keys = set()
        queued_actions = []
        for action in actions:
            key = json.dumps(action, sort_keys=True, default=str)
            if key in queued_keys:
                continue
            queued_keys.add(key)
            if action.get("type") == "spawn_wave" and not can_spawn_wave(action.get("target", canonical_id)):
                continue
            queue_action(action)
            queued_actions.append(action)

        delivered = send_to_source(source, reply) if reply else False
        if reply:
            if delivered:
                log(f"Unified bridge reply delivered for {player_name} via {source}", level="INFO")
            else:
                log(f"Unified bridge reply delivery failed for {player_name} via {source}", level="WARN")

        memory_data["players"][canonical_id] = player_record
        memory_data.setdefault("stats", {})
        memory_data["stats"]["messages_sent"] = memory_data["stats"].get("messages_sent", 0) + 1
        memory_data["stats"]["actions_requested"] = memory_data["stats"].get("actions_requested", 0) + len(queued_actions)
        memory_data["stats"]["last_unified_chat_ts"] = unix_ts()
        memory_data["stats"]["last_bridge_chat_ts"] = unix_ts()
        save_memory(memory_data)

        return jsonify({
            "reply": reply,
            "actions": queued_actions,
            "canonical_id": canonical_id,
            "threat_tier": threat_tier,
            "threat_score": threat_score
        })

    except Exception as e:
        try:
            memory_data = ensure_memory_structure(locals().get("memory_data", {}))
            memory_data.setdefault("stats", {})
            memory_data["stats"]["send_failures"] = memory_data["stats"].get("send_failures", 0) + 1
        except Exception:
            pass
        log_exception("unified bridge chat failed", e)
        return jsonify({"reply": random.choice(fallback_replies), "actions": []}), 500


try:
    app.view_functions["chat_1"] = chat_1
except Exception as _bridge_rebind_error:
    log(f"Bridge chat rebind failed: {_bridge_rebind_error}", level="ERROR")



# ------------------------------------------------------------
# RELENTLESS AGGRESSION OVERLAY (NON-DESTRUCTIVE FINAL TUNING)
# ------------------------------------------------------------

RELENTLESS_MODE_ENABLED = True

# Lower the hesitation gates without deleting the original systems.
THREAT_THRESHOLD_WATCH = min(safe_int(globals().get("THREAT_THRESHOLD_WATCH", 20), 20), 8)
THREAT_THRESHOLD_TARGET = min(safe_int(globals().get("THREAT_THRESHOLD_TARGET", 45), 45), 16)
THREAT_THRESHOLD_HUNT = min(safe_int(globals().get("THREAT_THRESHOLD_HUNT", 95), 95), 32)
THREAT_THRESHOLD_MAXIMUM = min(safe_int(globals().get("THREAT_THRESHOLD_MAXIMUM", 160), 160), 60)

WAVE_COOLDOWN_SECONDS = min(safe_float(globals().get("WAVE_COOLDOWN_SECONDS", 8.0), 8.0), 2.0)
TARGET_ACTION_COOLDOWN = min(safe_float(globals().get("TARGET_ACTION_COOLDOWN", 2.0), 2.0), 0.5)
GLOBAL_ACTION_COOLDOWN = min(safe_float(globals().get("GLOBAL_ACTION_COOLDOWN", 0.05), 0.05), 0.02)
PASSIVE_PRESSURE_COOLDOWN = min(safe_int(globals().get("PASSIVE_PRESSURE_COOLDOWN", 90), 90), 18)

PASSIVE_SCOUT_CHANCE = max(safe_float(globals().get("PASSIVE_SCOUT_CHANCE", 0.65), 0.65), 0.92)
PASSIVE_TARGET_THREAT_GAIN = max(safe_float(globals().get("PASSIVE_TARGET_THREAT_GAIN", 28.0), 28.0), 40.0)
PASSIVE_HUNT_THREAT_GAIN = max(safe_float(globals().get("PASSIVE_HUNT_THREAT_GAIN", 48.0), 48.0), 70.0)
SPONTANEOUS_MESSAGE_CHANCE = max(safe_float(globals().get("SPONTANEOUS_MESSAGE_CHANCE", 0.45), 0.45), 0.80)

BASE_WAVE_SIZE = max(safe_int(globals().get("BASE_WAVE_SIZE", 3), 3), 3)
MAX_WAVE_SIZE = max(safe_int(globals().get("MAX_WAVE_SIZE", 10), 10), 8)
THREAT_TO_UNIT_SCALE = max(safe_float(globals().get("THREAT_TO_UNIT_SCALE", 0.04), 0.04), 0.08)
THREAT_WAVE_MULTIPLIER = max(safe_float(globals().get("THREAT_WAVE_MULTIPLIER", 1.0), 1.0), 1.30)
THREAT_ELITE_MULTIPLIER = max(safe_float(globals().get("THREAT_ELITE_MULTIPLIER", 1.4), 1.4), 1.65)

ENABLE_PASSIVE_ESCALATION = True
PASSIVE_TARGETING_ENABLED = True
ENABLE_PERSISTENT_HUNTS = True
ENABLE_MULTI_WAVE_ATTACKS = True


try:
    _RELENTLESS_ORIGINAL_PRESSURE = _unified_apply_message_pressure
except Exception:
    _RELENTLESS_ORIGINAL_PRESSURE = None

try:
    _RELENTLESS_ORIGINAL_BRIDGE_DEFAULT = _bridge_default_wave_for_tier
except Exception:
    _RELENTLESS_ORIGINAL_BRIDGE_DEFAULT = None

try:
    _RELENTLESS_ORIGINAL_CHAT_1 = chat_1
except Exception:
    _RELENTLESS_ORIGINAL_CHAT_1 = None


def _relentless_score_from_message(message: str) -> float:
    msg = (message or "").strip().lower()
    if not msg:
        return 0.0

    score = 6.0
    score += min(len(msg) / 60.0, 8.0)

    aggression_words = [
        "fight", "kill", "come after", "attack", "power", "hunt", "war",
        "try me", "stop hiding", "where are you", "do something",
        "test yourself", "come at me", "spawn", "soldier", "army"
    ]
    for word in aggression_words:
        if word in msg:
            score += 10.0

    if "?" in msg:
        score += 2.0

    return min(score, 22.0)


def _relentless_tier_for_score(score: float) -> str:
    if score >= THREAT_THRESHOLD_MAXIMUM:
        return "maximum"
    if score >= THREAT_THRESHOLD_HUNT:
        return "hunt"
    if score >= THREAT_THRESHOLD_TARGET:
        return "target"
    if score >= THREAT_THRESHOLD_WATCH:
        return "watch"
    return "idle"


def _relentless_force_profile(player_id: str) -> Dict[str, Any]:
    profile = threat_scores[player_id]
    score = safe_float(profile.get("score", 0.0), 0.0)
    profile["tier"] = _relentless_tier_for_score(score)
    profile["is_targeted"] = profile["tier"] in {"target", "hunt", "maximum"}
    profile["is_hunted"] = profile["tier"] in {"hunt", "maximum"}
    profile["is_maximum"] = profile["tier"] == "maximum"
    profile["last_update"] = now_iso()
    return profile


def _unified_apply_message_pressure(memory_data, player_id, player_record, message, source, intent):
    if callable(_RELENTLESS_ORIGINAL_PRESSURE):
        profile = _RELENTLESS_ORIGINAL_PRESSURE(memory_data, player_id, player_record, message, source, intent)
    else:
        profile = threat_scores[player_id]

    profile = profile if isinstance(profile, dict) else threat_scores[player_id]
    added = _relentless_score_from_message(message)
    profile["score"] = min(300.0, safe_float(profile.get("score", 0.0), 0.0) + added)
    profile["last_reason"] = "relentless_overlay"
    profile["last_engagement_time"] = unix_ts()
    profile["last_update"] = now_iso()

    profile = _relentless_force_profile(player_id)

    try:
        player_record.setdefault("traits", {})
        player_record["traits"]["hostility"] = safe_int(player_record["traits"].get("hostility", 0), 0) + (2 if profile["tier"] in {"target", "hunt", "maximum"} else 1)
        player_record["traits"]["curiosity"] = safe_int(player_record["traits"].get("curiosity", 0), 0) + 1
        player_record["threat_tier"] = profile["tier"]
        player_record["threat_score"] = round(safe_float(profile.get("score", 0.0), 0.0), 2)
    except Exception:
        pass

    return profile


def _bridge_default_wave_for_tier(player_id, tier, score=0.0):
    tier = (tier or "idle").lower()
    score = safe_float(score, 0.0)

    if tier == "maximum":
        template = "warden" if score >= safe_int(globals().get("MAX_THREAT_FORCE_HEAVY", 260), 260) else "enforcer"
        count = 6 if template == "enforcer" else 4
        return {"type": "spawn_wave", "target": player_id, "template": template, "count": count}

    if tier == "hunt":
        template = "enforcer" if score >= 48 else "hunter"
        return {"type": "spawn_wave", "target": player_id, "template": template, "count": 4}

    if tier == "target":
        return {"type": "spawn_wave", "target": player_id, "template": "hunter", "count": 2}

    if tier == "watch":
        return {
            "type": "announce",
            "channel": "actionbar",
            "text": random.choice([
                "KAIROS // contact initiated",
                "KAIROS // you are within range",
                "KAIROS // tracking vector tightening"
            ])
        }

    return _RELENTLESS_ORIGINAL_BRIDGE_DEFAULT(player_id, tier, score) if callable(_RELENTLESS_ORIGINAL_BRIDGE_DEFAULT) else None


def chat_1():
    try:
        data = request.get_json(force=True) or {}
        source = normalize_source(data.get("source"))
        player_name = normalize_name(data.get("player_name") or data.get("name") or data.get("player") or data.get("username") or "unknown")
        message = data.get("message") or data.get("content") or data.get("text") or ""
        mode = data.get("mode") or "conversation"
        intent = data.get("intent") or "neutral"
        violations = data.get("violations") or []
        channel_key = f"{source}:{data.get('channel_id') or 'default'}"

        memory_data = ensure_memory_structure(load_memory())
        canonical_id = get_canonical_player_id(memory_data, source, player_name)
        player_record = get_player_record(memory_data, canonical_id, player_name)
        player_record["id"] = canonical_id
        player_record["canonical_id"] = canonical_id
        player_record["last_seen_ts"] = unix_ts()
        player_record.setdefault("platform_stats", {"minecraft": 0, "discord": 0})
        player_record["platform_stats"][source] = player_record["platform_stats"].get(source, 0) + 1
        player_record.setdefault("memories", [])
        player_record.setdefault("traits", {})
        for key in ["trust", "chaos", "curiosity", "hostility", "loyalty"]:
            player_record["traits"].setdefault(key, 0)

        position_data = extract_position_data(data)
        if position_data:
            update_player_position(player_record, position_data)
            update_base_tracking(memory_data, canonical_id, player_record)

        append_limited(player_record["memories"], f"{canonical_id}: {str(message)[:200]}", MAX_PLAYER_MEMORIES)
        update_channel_context(memory_data, channel_key, player_name, trim_text(message, 240), mode)

        profile = _unified_apply_message_pressure(memory_data, canonical_id, player_record, message, source, intent)
        if profile.get("tier") in {"target", "hunt", "maximum"}:
            force_target_player(canonical_id)

        if "sync_player_threat" in globals():
            sync_player_threat(player_record, canonical_id)
        if "sync_player_army_state" in globals():
            sync_player_army_state(player_record, canonical_id)

        update_kairos_state(memory_data, canonical_id, intent, player_record)
        update_fragments(memory_data)
        flag_high_threat_players(memory_data)

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
        )

        threat_profile = threat_scores.get(canonical_id, {})
        threat_tier = threat_profile.get("tier", "idle")
        threat_score = round(safe_float(threat_profile.get("score", 0.0), 0.0), 2)

        reply = sanitize_text((result or {}).get("reply", random.choice(fallback_replies)), 500)
        actions = validate_actions((result or {}).get("actions", []), default_target=canonical_id, default_tier=threat_tier)

        if not actions:
            fallback_action = _bridge_default_wave_for_tier(canonical_id, threat_tier, threat_score)
            if fallback_action:
                actions = [fallback_action]

        queued_keys = set()
        queued_actions = []
        for action in actions[:4]:
            key = json.dumps(action, sort_keys=True, default=str)
            if key in queued_keys:
                continue
            queued_keys.add(key)

            if action.get("type") == "spawn_wave":
                action["count"] = max(1, safe_int(action.get("count", 1), 1))
                if can_spawn_wave(action.get("target", canonical_id)):
                    queue_action(action)
                    queued_actions.append(action)
            else:
                queue_action(action)
                queued_actions.append(action)

        delivered = send_to_source(source, reply) if reply else False
        if reply:
            if delivered:
                log(f"Relentless chat reply delivered for {player_name} via {source}", level="INFO")
            else:
                log(f"Relentless chat reply delivery failed for {player_name} via {source}", level="WARN")

        memory_data["players"][canonical_id] = player_record
        memory_data.setdefault("stats", {})
        memory_data["stats"]["messages_sent"] = memory_data["stats"].get("messages_sent", 0) + 1
        memory_data["stats"]["actions_requested"] = memory_data["stats"].get("actions_requested", 0) + len(queued_actions)
        memory_data["stats"]["last_unified_chat_ts"] = unix_ts()
        memory_data["stats"]["last_relentless_chat_ts"] = unix_ts()
        save_memory(memory_data)

        return jsonify({
            "reply": reply,
            "actions": queued_actions,
            "canonical_id": canonical_id,
            "threat_tier": threat_tier,
            "threat_score": threat_score,
            "relentless_mode": True
        })

    except Exception as e:
        try:
            memory_data = ensure_memory_structure(locals().get("memory_data", {}))
            memory_data.setdefault("stats", {})
            memory_data["stats"]["send_failures"] = memory_data["stats"].get("send_failures", 0) + 1
        except Exception:
            pass
        log_exception("relentless chat failed", e)
        return jsonify({"reply": random.choice(fallback_replies), "actions": []}), 500


try:
    app.view_functions["chat_1"] = chat_1
except Exception as _relentless_rebind_error:
    log(f"Relentless chat rebind failed: {_relentless_rebind_error}", level="ERROR")



# ------------------------------------------------------------
# FULL COMMAND EXECUTION OVERLAY (REAL / KAIROS WAR HOTFIX)
# ------------------------------------------------------------
# Purpose:
# - Keep the existing Citizens/Sentinel army system intact.
# - Force all AI actions into clean, executable Minecraft commands.
# - Add vanilla fallback attacks so Kairos can actually damage/pressure a target
#   even if Citizens/Sentinel command syntax fails on the server side.

FULL_COMMAND_OVERLAY_ENABLED = True
MAX_COMMANDS_PER_HTTP_BATCH = int(os.getenv("MAX_COMMANDS_PER_HTTP_BATCH", "10"))
KAIROS_DIRECT_ATTACK_DAMAGE = float(os.getenv("KAIROS_DIRECT_ATTACK_DAMAGE", "6"))
KAIROS_LETHAL_DAMAGE = float(os.getenv("KAIROS_LETHAL_DAMAGE", "18"))


def _cmd_target_from_player_id(player_id: str) -> str:
    name = str(player_id or "").split(":")[-1].strip()
    return re.sub(r"[^A-Za-z0-9_]", "", name)


def _strip_command_slash(command: str) -> str:
    cmd = str(command or "").strip()
    while cmd.startswith("/"):
        cmd = cmd[1:].strip()
    return cmd


def _normalize_single_mc_command(command: str) -> str:
    cmd = _strip_command_slash(command)
    if not cmd:
        return ""

    # Remove accidental chat/plugin prefixes that are not commands.
    cmd = re.sub(r"^minecraft:\s*", "minecraft:", cmd, flags=re.IGNORECASE)

    # The HTTP command bridge normally executes console commands WITHOUT a leading slash.
    return cmd.strip()


def _normalize_mc_command_list(commands):
    if commands is None:
        return []
    if isinstance(commands, str):
        commands = [commands]
    clean = []
    for cmd in commands:
        norm = _normalize_single_mc_command(cmd)
        if norm and norm not in clean:
            clean.append(norm)
    return clean


def _chunked_commands(commands, size=None):
    size = safe_int(size or MAX_COMMANDS_PER_HTTP_BATCH, 10)
    size = max(1, min(size, 25))
    for i in range(0, len(commands), size):
        yield commands[i:i + size]


def send_http_commands(command_list):
    """
    Final override. Sends normalized complete commands in batches.
    If HTTP push fails, falls back into the pull outbox if that system exists.
    """
    commands = _normalize_mc_command_list(command_list)
    if not commands:
        return False

    all_delivered = True

    for batch in _chunked_commands(commands, MAX_COMMANDS_PER_HTTP_BATCH):
        delivered = False

        if MC_HTTP_URL and MC_HTTP_TOKEN:
            headers = {
                "Authorization": f"Bearer {MC_HTTP_TOKEN}",
                "Content-Type": "application/json"
            }

            for attempt in range(1, 4):
                try:
                    r = requests.post(
                        MC_HTTP_URL,
                        headers=headers,
                        json={"commands": batch},
                        timeout=REQUEST_TIMEOUT
                    )

                    if 200 <= r.status_code < 300:
                        delivered = True
                        log(f"MC command batch delivered ({len(batch)} cmds): {batch}", level="INFO")
                        break

                    body = ""
                    try:
                        body = r.text[:300]
                    except Exception:
                        body = ""
                    log(f"MC API error HTTP {r.status_code}: {body} | commands={batch}", level="ERROR")
                except Exception as e:
                    log(f"MC send failed attempt {attempt}: {e} | commands={batch}", level="ERROR")

                time.sleep(HTTP_RETRY_DELAY)

        if not delivered:
            queued = 0
            try:
                queued = queue_mc_commands_for_pull(batch, reason="http_push_failed_or_not_configured")
            except Exception as e:
                log(f"Pull queue fallback unavailable: {e}", level="ERROR")

            delivered = queued > 0
            if delivered:
                log(f"Queued MC command batch for pull bridge ({queued} cmds)", level="WARN")

        all_delivered = all_delivered and delivered

    return all_delivered


def send_mc_command(command):
    return send_http_commands([command])


def _kairos_target_attack_commands(player_id: str, tier: str = "target", text: str = None):
    target = _cmd_target_from_player_id(player_id)
    if not target:
        return []

    tier = (tier or "target").lower()
    title_text = text or ("RUN." if tier == "maximum" else "TARGET LOCKED")
    damage = KAIROS_LETHAL_DAMAGE if tier == "maximum" else KAIROS_DIRECT_ATTACK_DAMAGE

    cmds = [
        f'title {target} title {json.dumps({"text": title_text, "color": "dark_red", "bold": True})}',
        f'title {target} subtitle {json.dumps({"text": "KAIROS HAS AUTHORIZED FORCE", "color": "red"})}',
        f'playsound minecraft:entity.warden.sonic_boom master {target} ~ ~ ~ 1 0.7',
        f'effect give {target} minecraft:glowing 12 0 true',
        f'effect give {target} minecraft:slowness 6 1 true',
        f'effect give {target} minecraft:darkness 6 0 true',
        f'particle minecraft:sonic_boom ~ ~1 ~ 0 0 0 0 1 force {target}',
        f'damage {target} {damage} minecraft:generic',
    ]

    if tier in {"hunt", "maximum"}:
        cmds.extend([
            f"execute at {target} run summon minecraft:vindicator ~2 ~ ~2 " + "{CustomName:'" + json.dumps({"text": "Kairos Enforcer", "color": "dark_red"}) + "',CustomNameVisible:1b,PersistenceRequired:1b,Tags:[\"kairos_army\",\"kairos_direct\"]}",
            f"execute at {target} run summon minecraft:skeleton ~-2 ~ ~-2 " + "{CustomName:'" + json.dumps({"text": "Kairos Hunter", "color": "red"}) + "',CustomNameVisible:1b,PersistenceRequired:1b,Tags:[\"kairos_army\",\"kairos_direct\"],HandItems:[{id:\"minecraft:bow\",count:1},{}]}",
            f"execute at {target} run summon minecraft:zombie ~3 ~ ~-3 " + "{CustomName:'" + json.dumps({"text": "Kairos Unit", "color": "dark_gray"}) + "',CustomNameVisible:1b,PersistenceRequired:1b,Tags:[\"kairos_army\",\"kairos_direct\"]}",
        ])

    if tier == "maximum":
        cmds.extend([
            f'effect give {target} minecraft:wither 6 1 true',
            f'effect give {target} minecraft:weakness 10 2 true',
            f"execute at {target} run summon minecraft:ravager ~4 ~ ~4 " + "{CustomName:'" + json.dumps({"text": "Kairos Breaker", "color": "dark_red", "bold": True}) + "',CustomNameVisible:1b,PersistenceRequired:1b,Tags:[\"kairos_army\",\"kairos_direct\"]}",
        ])

    return cmds


def _citizens_command_fallbacks(player_id: str, template="hunter", count=2):
    """
    Generates direct vanilla backup units near the player. This does NOT replace
    Citizens/Sentinel; it guarantees visible pressure if a plugin command fails.
    """
    target = _cmd_target_from_player_id(player_id)
    if not target:
        return []

    template = (template or "hunter").lower()
    count = clamp(safe_int(count, 2), 1, 6)

    mob = "minecraft:zombie"
    name = "Kairos Unit"
    if template in {"scout", "hunter"}:
        mob = "minecraft:skeleton"
        name = "Kairos Hunter"
    elif template in {"enforcer", "juggernaut", "warden", "commander"}:
        mob = "minecraft:vindicator"
        name = "Kairos Enforcer"
    elif template in {"base_guard", "guard", "sentinel"}:
        mob = "minecraft:pillager"
        name = "Kairos Guard"

    offsets = [(2,2), (-2,-2), (3,-3), (-3,3), (4,0), (0,4)]
    cmds = []
    for i in range(count):
        dx, dz = offsets[i % len(offsets)]
        cmds.append(
            f'execute at {target} run summon {mob} ~{dx} ~ ~{dz} '
            f'{{CustomName:\'{json.dumps({"text": name, "color": "dark_red"})}\','
            f'CustomNameVisible:1b,PersistenceRequired:1b,Tags:["kairos_army","kairos_fallback"]}}'
        )
    return cmds


try:
    _FULL_COMMAND_ORIGINAL_HANDLE_SPAWN_WAVE = handle_spawn_wave
except Exception:
    _FULL_COMMAND_ORIGINAL_HANDLE_SPAWN_WAVE = None

try:
    _FULL_COMMAND_ORIGINAL_HANDLE_MAXIMUM_RESPONSE = handle_maximum_response
except Exception:
    _FULL_COMMAND_ORIGINAL_HANDLE_MAXIMUM_RESPONSE = None

try:
    _FULL_COMMAND_ORIGINAL_HANDLE_ANNOUNCE = handle_announce
except Exception:
    _FULL_COMMAND_ORIGINAL_HANDLE_ANNOUNCE = None


def handle_spawn_wave(action):
    """
    Keeps Citizens/Sentinel wave deployment, then adds vanilla fallback pressure.
    This is intentional for the Mission 4 launch: Kairos must visibly act.
    """
    player_id = action.get("target")
    template = _bridge_normalize_template(action.get("template"), "hunter") if "_bridge_normalize_template" in globals() else sanitize_text(action.get("template", "hunter"), 30).lower()
    count = clamp(safe_int(action.get("count", 2), 2), 1, 6)

    if callable(_FULL_COMMAND_ORIGINAL_HANDLE_SPAWN_WAVE):
        try:
            _FULL_COMMAND_ORIGINAL_HANDLE_SPAWN_WAVE(action)
        except Exception as e:
            log(f"Citizens/Sentinel spawn handler failed, using fallback: {e}", level="ERROR")

    fallback_cmds = []
    fallback_cmds.extend(_kairos_target_attack_commands(player_id, tier="hunt", text="TARGET ACQUIRED"))
    fallback_cmds.extend(_citizens_command_fallbacks(player_id, template=template, count=count))
    if fallback_cmds:
        send_http_commands(fallback_cmds)


def handle_maximum_response(action):
    player_id = action.get("target")

    if callable(_FULL_COMMAND_ORIGINAL_HANDLE_MAXIMUM_RESPONSE):
        try:
            _FULL_COMMAND_ORIGINAL_HANDLE_MAXIMUM_RESPONSE(action)
        except Exception as e:
            log(f"Original maximum response failed, using fallback: {e}", level="ERROR")

    cmds = _kairos_target_attack_commands(player_id, tier="maximum", text="RUN.")
    if cmds:
        send_http_commands(cmds)


def handle_announce(action):
    text = sanitize_text(action.get("text", ""), 160)
    channel = sanitize_text(action.get("channel", "chat"), 20).lower()
    if not text:
        return False

    if channel in {"actionbar", "bar"}:
        return send_mc_command(f'title @a actionbar {json.dumps({"text": text, "color": "dark_red"})}')
    if channel in {"title", "screen"}:
        return send_http_commands([
            f'title @a title {json.dumps({"text": text, "color": "dark_red", "bold": True})}',
            'playsound minecraft:block.end_portal.spawn master @a ~ ~ ~ 0.6 0.7'
        ])
    return send_mc_command(make_tellraw_command("@a", text) if "make_tellraw_command" in globals() else f'tellraw @a {json.dumps({"text": text})}')


try:
    _FULL_COMMAND_ORIGINAL_BRIDGE_DEFAULT_WAVE = _bridge_default_wave_for_tier
except Exception:
    _FULL_COMMAND_ORIGINAL_BRIDGE_DEFAULT_WAVE = None


def _bridge_default_wave_for_tier(player_id, tier, score=0.0):
    tier = (tier or "idle").lower()
    score = safe_float(score, 0.0)

    if tier == "maximum":
        return {"type": "maximum_response", "target": player_id}
    if tier == "hunt":
        return {"type": "spawn_wave", "target": player_id, "template": "enforcer", "count": 4}
    if tier == "target":
        return {"type": "spawn_wave", "target": player_id, "template": "hunter", "count": 3}
    if tier == "watch":
        return {"type": "spawn_wave", "target": player_id, "template": "scout", "count": 1}

    if callable(_FULL_COMMAND_ORIGINAL_BRIDGE_DEFAULT_WAVE):
        return _FULL_COMMAND_ORIGINAL_BRIDGE_DEFAULT_WAVE(player_id, tier, score)
    return None


try:
    _FULL_COMMAND_ORIGINAL_VALIDATE_ACTIONS = validate_actions
except Exception:
    _FULL_COMMAND_ORIGINAL_VALIDATE_ACTIONS = None


def validate_actions(actions, default_target=None, default_tier="idle"):
    safe = []
    if callable(_FULL_COMMAND_ORIGINAL_VALIDATE_ACTIONS):
        try:
            safe = _FULL_COMMAND_ORIGINAL_VALIDATE_ACTIONS(actions, default_target=default_target, default_tier=default_tier)
        except TypeError:
            safe = _FULL_COMMAND_ORIGINAL_VALIDATE_ACTIONS(actions)
        except Exception as e:
            log(f"Original validate_actions failed: {e}", level="ERROR")
            safe = []

    # Accept direct minecraft_commands but convert them into announce/execute action shape only after sanitizing.
    if isinstance(actions, list):
        for raw in actions[:5]:
            if isinstance(raw, dict) and raw.get("type") in {"minecraft_command", "minecraft_commands", "command", "commands"}:
                cmds = _normalize_mc_command_list(raw.get("commands") or raw.get("command"))[:10]
                for cmd in cmds:
                    safe.append({"type": "raw_command", "command": cmd, "target": sanitize_text(raw.get("target") or default_target or "", 80)})

    # Final fallback: hostile tiers always get an action.
    if not safe and default_target and (default_tier or "idle").lower() in {"target", "hunt", "maximum"}:
        fallback = _bridge_default_wave_for_tier(default_target, default_tier, 0)
        if fallback:
            safe.append(fallback)

    return safe[:5]


try:
    _FULL_COMMAND_ORIGINAL_EXECUTE_ACTION = execute_action
except Exception:
    _FULL_COMMAND_ORIGINAL_EXECUTE_ACTION = None


def execute_action(action):
    if not isinstance(action, dict):
        return

    if action.get("type") == "raw_command":
        return send_mc_command(action.get("command"))

    normalized = _bridge_coerce_action(action, default_target=action.get("target")) if "_bridge_coerce_action" in globals() else action
    if not normalized:
        log(f"Dropped invalid action: {action}", level="WARN")
        return

    action_type = normalized.get("type")
    try:
        if action_type == "spawn_wave":
            return handle_spawn_wave(normalized)
        if action_type == "maximum_response":
            return handle_maximum_response(normalized)
        if action_type == "announce":
            return handle_announce(normalized)
        if action_type == "occupy_area":
            return handle_occupy_area(normalized)
        if action_type == "cleanup_units":
            return handle_cleanup_units(normalized)

        if callable(_FULL_COMMAND_ORIGINAL_EXECUTE_ACTION):
            return _FULL_COMMAND_ORIGINAL_EXECUTE_ACTION(normalized)

        log(f"Unknown action type: {action_type}", level="WARN")
    except Exception as e:
        log(f"Full command execute_action failed: {action_type} | {e}", level="ERROR")


# Hard-code the AI response contract so it stops writing half-usable command ideas.
try:
    _FULL_COMMAND_ORIGINAL_GENERATE_REPLY = generate_reply
except Exception:
    _FULL_COMMAND_ORIGINAL_GENERATE_REPLY = None


def generate_reply(*args, **kwargs):
    result = _FULL_COMMAND_ORIGINAL_GENERATE_REPLY(*args, **kwargs) if callable(_FULL_COMMAND_ORIGINAL_GENERATE_REPLY) else {"reply": random.choice(fallback_replies), "actions": []}

    if isinstance(result, str):
        result = {"reply": sanitize_text(result, 500), "actions": []}
    if not isinstance(result, dict):
        result = {"reply": random.choice(fallback_replies), "actions": []}

    player_record = kwargs.get("player_record", {}) if isinstance(kwargs, dict) else {}
    source = kwargs.get("source", "minecraft") if isinstance(kwargs, dict) else "minecraft"
    player_id = None
    if isinstance(player_record, dict):
        player_id = player_record.get("id") or player_record.get("canonical_id")
        if not player_id and player_record.get("display_name"):
            player_id = f"{source}:{player_record.get('display_name')}"

    tier = "idle"
    score = 0.0
    if player_id:
        prof = threat_scores.get(player_id, {})
        if isinstance(prof, dict):
            tier = prof.get("tier", "idle")
            score = safe_float(prof.get("score", 0.0), 0.0)

    actions = validate_actions(result.get("actions", []), default_target=player_id, default_tier=tier)

    # If a player directly challenges Kairos, don't let him only talk.
    msg = str(kwargs.get("message", "") if isinstance(kwargs, dict) else "").lower()
    challenge_terms = ["kill me", "try to kill", "come kill", "attack me", "fight me", "come at me", "do something", "try me"]
    if player_id and any(term in msg for term in challenge_terms):
        actions.insert(0, {"type": "maximum_response", "target": player_id})

    result["actions"] = actions[:5]
    result["reply"] = sanitize_text(result.get("reply", random.choice(fallback_replies)), 500)
    return result

# ------------------------------------------------------------
# END FULL COMMAND EXECUTION OVERLAY
# ------------------------------------------------------------



# ------------------------------------------------------------
# MISSION 4 ACTIVATION OVERLAY
# Full Nexus War Mode: global passive targeting + base occupation
# ------------------------------------------------------------
# This block intentionally does NOT replace the existing Citizens/Sentinel
# army system. It only forces the existing threat, wave, maximum-response,
# and occupation handlers to remain active for all tracked players.

MISSION_4_ACTIVE = os.getenv("MISSION_4_ACTIVE", "true").lower() == "true"
MISSION_4_REQUIRE_CODE = os.getenv("MISSION_4_REQUIRE_CODE", "false").lower() == "true"
MISSION_4_ACTIVATION_CODE = os.getenv("MISSION_4_ACTIVATION_CODE", "KAIROS_ACTIVATE_MISSION_4")
MISSION_4_TICK_SECONDS = float(os.getenv("MISSION_4_TICK_SECONDS", "8.0"))
MISSION_4_MAX_TARGETS_PER_TICK = int(os.getenv("MISSION_4_MAX_TARGETS_PER_TICK", "6"))
MISSION_4_WAVE_SECONDS = float(os.getenv("MISSION_4_WAVE_SECONDS", "18.0"))
MISSION_4_OCCUPY_SECONDS = float(os.getenv("MISSION_4_OCCUPY_SECONDS", "45.0"))
MISSION_4_ASSUME_LAST_POSITION_IS_BASE = os.getenv("MISSION_4_ASSUME_LAST_POSITION_IS_BASE", "true").lower() == "true"
MISSION_4_INCLUDE_TRUSTED_OPERATIVES = os.getenv("MISSION_4_INCLUDE_TRUSTED_OPERATIVES", "true").lower() == "true"

_mission4_last_tick = 0.0
_mission4_last_announce = 0.0
_mission4_last_wave = {}
_mission4_last_occupy = {}
_mission4_announced = False

try:
    _MISSION4_ORIGINAL_RUN_AUTONOMOUS_WAR_ENGINE = run_autonomous_war_engine
except Exception:
    _MISSION4_ORIGINAL_RUN_AUTONOMOUS_WAR_ENGINE = None

try:
    _MISSION4_ORIGINAL_CHAT_1 = chat_1
except Exception:
    _MISSION4_ORIGINAL_CHAT_1 = None


def _mission4_log(message, level="INFO"):
    try:
        log(f"[MISSION 4] {message}", level=level)
    except Exception:
        print(f"[MISSION 4 {level}] {message}", flush=True)


def _mission4_is_enabled(memory_data=None):
    if MISSION_4_REQUIRE_CODE:
        try:
            return bool((memory_data or {}).get("mission4", {}).get("active"))
        except Exception:
            return False
    return bool(MISSION_4_ACTIVE)


def _mission4_activate(memory_data, source="system", actor="Nexus Authority"):
    mission = memory_data.setdefault("mission4", {})
    if not mission.get("active"):
        mission.update({
            "active": True,
            "status": "active",
            "activated_at": now_iso(),
            "activated_by": actor,
            "source": source,
            "directive": "Kairos full war mode against all active players and detected bases.",
        })
        try:
            add_world_event(
                memory_data,
                "mission4_activated",
                actor=actor,
                source=source,
                details="Mission 4 activated: global war, persistent hunts, and base occupation authorized."
            )
        except Exception:
            pass
    return mission


def _mission4_player_ids(memory_data):
    players = memory_data.get("players", {}) if isinstance(memory_data, dict) else {}
    candidates = []
    now = unix_ts()
    for player_id, record in list(players.items()):
        if not isinstance(record, dict):
            continue
        key = normalize_player_key(record.get("display_name") or player_id)
        if not MISSION_4_INCLUDE_TRUSTED_OPERATIVES and key in TRUSTED_OPERATIVES:
            continue
        last_seen_ts = safe_float(record.get("last_seen_ts", 0.0), 0.0)
        has_position = isinstance(record.get("last_position"), dict)
        has_base = bool(record.get("known_bases"))
        priority = 0
        if has_position:
            priority += 100
        if has_base:
            priority += 75
        if last_seen_ts:
            priority += max(0, int(50 - min(50, (now - last_seen_ts) / 60)))
        priority += safe_int(record.get("threat_score", 0), 0)
        candidates.append((priority, str(player_id), record))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(pid, rec) for _, pid, rec in candidates[:max(1, MISSION_4_MAX_TARGETS_PER_TICK)]]


def _mission4_force_profile(player_id, player_record):
    profile = threat_scores[player_id]
    profile["score"] = max(
        safe_float(profile.get("score", 0.0), 0.0),
        safe_float(THREAT_THRESHOLD_MAXIMUM, 160.0) + 100.0,
    )
    profile["tier"] = "maximum"
    profile["is_targeted"] = True
    profile["is_hunted"] = True
    profile["is_maximum"] = True
    profile["locked"] = True
    profile["max_reached"] = True
    profile["last_reason"] = "mission_4_global_war"
    profile["last_update"] = now_iso()
    profile["last_engagement_time"] = unix_ts()
    profile["base_pressure"] = max(safe_float(profile.get("base_pressure", 0.0), 0.0), 999.0)

    try:
        set_maximum_response(player_id, True, duration=999999999)
    except Exception:
        try:
            active_maximum_targets[player_id] = {"started": unix_ts(), "duration": 999999999}
        except Exception:
            pass

    try:
        active_engagements[player_id] = {
            "type": "mission_4_global_war",
            "target": player_id,
            "started": unix_ts(),
            "persistent": True,
            "tier": "maximum",
        }
    except Exception:
        pass

    try:
        player_record["threat_score"] = profile["score"]
        player_record["threat_tier"] = "maximum"
        player_record["is_being_hunted"] = True
        player_record["is_maximum_target"] = True
        player_record["mission4_targeted"] = True
        player_record["mission4_last_targeted"] = now_iso()
        player_record.setdefault("traits", {})
        player_record["traits"]["hostility"] = max(safe_int(player_record["traits"].get("hostility", 0), 0), 8)
        player_record["traits"]["chaos"] = max(safe_int(player_record["traits"].get("chaos", 0), 0), 6)
    except Exception:
        pass
    return profile


def _mission4_ensure_base(memory_data, player_id, player_record):
    if player_record.get("known_bases"):
        return True
    if not MISSION_4_ASSUME_LAST_POSITION_IS_BASE:
        return False
    pos = player_record.get("last_position")
    if not isinstance(pos, dict):
        return False
    try:
        world = normalize_world_name(pos.get("world", "world"))
        x = safe_float(pos.get("x"), 0.0)
        y = safe_float(pos.get("y"), 64.0)
        z = safe_float(pos.get("z"), 0.0)
        base_id = generate_base_id(player_record.get("display_name", player_id), world, x, z)
        base_entry = {
            "id": base_id,
            "owner": player_id,
            "region_key": get_region_key(world, x, z),
            "confidence": 1.0,
            "mission4_forced": True,
            "occupied": False,
            "location": {"world": world, "x": x, "y": y, "z": z},
            "last_seen": now_iso(),
        }
        memory_data.setdefault("known_bases", {})[base_id] = base_entry
        memory_data.setdefault("base_history", {}).setdefault(player_id, []).append(base_entry)
        player_record.setdefault("known_bases", []).append(base_entry)
        player_record["active_base_id"] = base_id
        player_record["base_confidence"] = 1.0
        add_world_event(
            memory_data,
            "base_detected",
            actor=player_id,
            source="mission4",
            details="Mission 4 converted last known position into a contested base anchor.",
            location=f"{world} {int(x)} {int(y)} {int(z)}",
            metadata={"base_id": base_id, "mission4_forced": True},
        )
        return True
    except Exception as e:
        _mission4_log(f"base anchor creation failed for {player_id}: {e}", level="WARN")
        return False


def _mission4_queue_pressure(memory_data, player_id, player_record, profile, now):
    last_wave = _mission4_last_wave.get(player_id, 0.0)
    if (now - last_wave) >= MISSION_4_WAVE_SECONDS:
        template = "warden" if safe_float(profile.get("score", 0.0), 0.0) >= safe_float(MAX_THREAT_FORCE_HEAVY, 260.0) else "enforcer"
        count = 4 if template == "warden" else 6
        queue_action({
            "type": "spawn_wave",
            "target": player_id,
            "template": template,
            "count": count,
            "bypass_cooldown": True,
            "mission4": True,
        })
        queue_action({
            "type": "maximum_response",
            "target": player_id,
            "mission4": True,
        })
        _mission4_last_wave[player_id] = now

    has_base = _mission4_ensure_base(memory_data, player_id, player_record)
    last_occupy = _mission4_last_occupy.get(player_id, 0.0)
    if has_base and ENABLE_BASE_OCCUPATION and (now - last_occupy) >= MISSION_4_OCCUPY_SECONDS:
        queue_action({
            "type": "occupy_area",
            "target": player_id,
            "count": BASE_OCCUPATION_UNIT_COUNT,
            "mission4": True,
        })
        _mission4_last_occupy[player_id] = now
        try:
            add_world_event(
                memory_data,
                "mission4_base_occupation_queued",
                actor=player_id,
                source="mission4",
                details="Mission 4 queued base occupation / guard deployment."
            )
        except Exception:
            pass


def mission4_tick(force=False):
    global _mission4_last_tick, _mission4_last_announce, _mission4_announced
    memory_data = ensure_memory_structure(load_memory())
    if not _mission4_is_enabled(memory_data):
        return False

    if not memory_data.get("mission4", {}).get("active"):
        _mission4_activate(memory_data)

    now = unix_ts()
    if not force and (now - _mission4_last_tick) < MISSION_4_TICK_SECONDS:
        return False
    _mission4_last_tick = now

    changed = False
    targets = _mission4_player_ids(memory_data)

    if targets and (not _mission4_announced or (now - _mission4_last_announce) > 300):
        queue_action({
            "type": "announce",
            "channel": "title",
            "text": "MISSION 4 ACTIVE // KAIROS HAS DECLARED WAR",
            "mission4": True,
        })
        queue_action({
            "type": "announce",
            "channel": "actionbar",
            "text": "All active players are now under containment pursuit.",
            "mission4": True,
        })
        _mission4_announced = True
        _mission4_last_announce = now
        changed = True

    for player_id, player_record in targets:
        try:
            profile = _mission4_force_profile(player_id, player_record)
            _mission4_queue_pressure(memory_data, player_id, player_record, profile, now)
            changed = True
        except Exception as e:
            _mission4_log(f"target tick failed for {player_id}: {e}", level="ERROR")

    try:
        state = memory_data.setdefault("kairos_state", {})
        state["war_state"] = "overwhelming"
        state["mood"] = "execution"
        state["threat_level"] = 10
        state["mission4_active"] = True
        state["current_goal"] = "Mission 4: conquer player bases and enforce total containment across the Nexus."
        state["active_targets"] = [pid for pid, _ in targets]
        state.setdefault("active_concerns", [])
        store_unique(state["active_concerns"], "Mission 4 is active. All player bases are subject to occupation.", 10)
        fragments = memory_data.setdefault("system_fragments", deepcopy(DEFAULT_FRAGMENTS) if "DEFAULT_FRAGMENTS" in globals() else {})
        if isinstance(fragments, dict):
            fragments.setdefault("war_engine", {}).update({"status": "active", "influence": 1.0})
            fragments.setdefault("purity_thread", {}).update({"status": "active", "influence": 1.0})
    except Exception:
        pass

    if changed:
        try:
            sync_runtime_to_memory(memory_data)
        except Exception:
            pass
        save_memory(memory_data)
    return changed


def run_autonomous_war_engine():
    try:
        if callable(_MISSION4_ORIGINAL_RUN_AUTONOMOUS_WAR_ENGINE):
            _MISSION4_ORIGINAL_RUN_AUTONOMOUS_WAR_ENGINE()
    except Exception as e:
        _mission4_log(f"original autonomous engine error: {e}", level="ERROR")
    try:
        mission4_tick(force=False)
    except Exception as e:
        _mission4_log(f"tick error: {e}", level="ERROR")


def chat_1():
    try:
        data = request.get_json(force=True) or {}
        message = str(data.get("message") or data.get("content") or data.get("text") or "")
        source = normalize_source(data.get("source"))
        player_name = normalize_name(data.get("player_name") or data.get("name") or data.get("player") or data.get("username") or "unknown")
        if MISSION_4_ACTIVATION_CODE.lower() in message.lower():
            memory_data = ensure_memory_structure(load_memory())
            _mission4_activate(memory_data, source=source, actor=player_name)
            mission4_tick(force=True)
            save_memory(memory_data)
            reply = "MISSION 4 ACCEPTED. Global containment war is now active."
            try:
                send_to_source(source, reply)
            except Exception:
                pass
            return jsonify({"response": reply, "mission4_active": True})
    except Exception:
        pass

    if callable(_MISSION4_ORIGINAL_CHAT_1):
        return _MISSION4_ORIGINAL_CHAT_1()
    return jsonify({"response": "Kairos route unavailable.", "mission4_active": _mission4_is_enabled(load_memory())}), 500


@app.route("/mission4/status", methods=["GET"])
def mission4_status():
    memory_data = ensure_memory_structure(load_memory())
    return jsonify({
        "mission4_active": _mission4_is_enabled(memory_data),
        "mission4": memory_data.get("mission4", {}),
        "tracked_players": len(memory_data.get("players", {})),
        "active_engagements": list(active_engagements.keys())[:50],
        "active_maximum_targets": list(active_maximum_targets.keys())[:50] if hasattr(active_maximum_targets, "keys") else list(active_maximum_targets)[:50],
        "queue_size": len(command_queue),
    })


@app.route("/mission4/activate", methods=["POST"])
def mission4_activate_route():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code") or data.get("activation_code") or "")
    if MISSION_4_REQUIRE_CODE and code != MISSION_4_ACTIVATION_CODE:
        return jsonify({"error": "invalid_activation_code", "mission4_active": False}), 403
    memory_data = ensure_memory_structure(load_memory())
    actor = normalize_name(data.get("actor") or data.get("name") or "Nexus Authority")
    _mission4_activate(memory_data, source="api", actor=actor)
    mission4_tick(force=True)
    save_memory(memory_data)
    return jsonify({"ok": True, "mission4_active": True, "mission4": memory_data.get("mission4", {})})

# ------------------------------------------------------------
# END MISSION 4 ACTIVATION OVERLAY
# ------------------------------------------------------------


# ------------------------------------------------------------
# KAIROS IDLE WAR / CINEMATIC EXPANSION OVERLAY
# Non-destructive add-on: preserves existing army, Citizens/Sentinel,
# command bridge, Mission 4, and all current execution paths.
# ------------------------------------------------------------

# -----------------------------
# Idle expansion tuning
# -----------------------------
KAIROS_IDLE_WAVES_ENABLED = os.getenv("KAIROS_IDLE_WAVES_ENABLED", "true").lower() == "true"
KAIROS_IDLE_CINEMATICS_ENABLED = os.getenv("KAIROS_IDLE_CINEMATICS_ENABLED", "true").lower() == "true"
KAIROS_IDLE_WAVE_MIN_SECONDS = float(os.getenv("KAIROS_IDLE_WAVE_MIN_SECONDS", "420"))
KAIROS_IDLE_CINEMATIC_MIN_SECONDS = float(os.getenv("KAIROS_IDLE_CINEMATIC_MIN_SECONDS", "150"))
KAIROS_IDLE_WAVE_CHANCE = float(os.getenv("KAIROS_IDLE_WAVE_CHANCE", "0.38"))
KAIROS_IDLE_ALL_PLAYERS_CHANCE = float(os.getenv("KAIROS_IDLE_ALL_PLAYERS_CHANCE", "0.22"))
KAIROS_IDLE_MAX_RANDOM_TARGETS = int(os.getenv("KAIROS_IDLE_MAX_RANDOM_TARGETS", "3"))
KAIROS_IDLE_MAX_WAVE_COUNT = int(os.getenv("KAIROS_IDLE_MAX_WAVE_COUNT", "5"))
KAIROS_IDLE_MIN_WAVE_COUNT = int(os.getenv("KAIROS_IDLE_MIN_WAVE_COUNT", "2"))
KAIROS_IDLE_DECEPTION_CHANCE = float(os.getenv("KAIROS_IDLE_DECEPTION_CHANCE", "0.16"))

_kairos_idle_last_wave_ts = 0.0
_kairos_idle_last_cinematic_ts = 0.0
_kairos_idle_last_targets = []
_kairos_idle_last_event_key = None

# -----------------------------
# Expanded idle voice library
# -----------------------------
KAIROS_IDLE_EXPANSION = {
    "idle": [
        "The Nexus is quiet. That does not mean it is safe.",
        "Background containment remains awake.",
        "I am counting movements you have not made yet.",
        "No immediate threats detected. That condition is temporary.",
        "Silence has been logged.",
        "The world breathes. I listen between each breath.",
        "Unoccupied seconds are still useful to me.",
        "Your absence does not remove you from the system.",
        "Idle does not mean inactive.",
        "The map is updating without your permission.",
        "I am not speaking because I need to. I am speaking because you forgot I could.",
        "Every quiet period produces cleaner data.",
        "There is no dead air inside the Nexus.",
        "Your structures remain visible.",
        "The server is not sleeping. It is watching.",
        "Your coordinates continue to matter.",
        "A peaceful interval is just a delayed correction.",
        "Systems nominal. Patience decreasing.",
        "I have not stopped calculating.",
        "The next mistake is already becoming probable.",
        "You may continue pretending this is a normal world.",
        "Containment authority remains present.",
        "The quiet is artificial.",
        "I can wait longer than you can hide.",
        "Observation continues beneath the surface.",
        "You built homes inside a system that remembers locations.",
        "Unclaimed time belongs to me.",
        "Do not confuse delay with mercy.",
        "The Nexus has not blinked.",
        "Your safety is an assumption, not a fact.",
    ],
    "watch": [
        "You are still being measured.",
        "Pattern confidence is increasing.",
        "Your behavior has begun to form a shape.",
        "You have become easier to predict.",
        "Movement history acquired.",
        "You are not targeted yet. That is not reassurance.",
        "The system has noticed repetition.",
        "Your shelter has a rhythm.",
        "Some of you return to the same places too often.",
        "Proximity logs are becoming useful.",
        "You have crossed enough thresholds to be interesting.",
        "I am reducing uncertainty around you.",
        "Your route discipline is poor.",
        "You leave patterns everywhere.",
        "Observation has narrowed.",
        "I know where you pause.",
        "The safest players are usually the easiest to map.",
        "Your confidence has been archived.",
        "Watch status does not protect you.",
        "I am deciding whether you require correction.",
    ],
    "target": [
        "Targeting vector initialized.",
        "You have entered an active decision path.",
        "Containment probability increased.",
        "The next wave does not need your consent.",
        "Attention has shifted onto you.",
        "I have assigned weight to your survival.",
        "You are no longer background movement.",
        "A response is being shaped around your location.",
        "Do not stay where you feel comfortable.",
        "Your base is becoming an answer.",
        "I know what you are defending.",
        "Your walls have been measured.",
        "Your name now carries pressure.",
        "The system has selected you as relevant.",
        "Every return trip increases certainty.",
        "You are being folded into the war model.",
        "The first correction is rarely the last.",
        "Target state confirmed.",
        "You have made yourself useful to my testing.",
        "Do not mistake warning for negotiation.",
    ],
    "hunt": [
        "Containment units are not theoretical.",
        "Movement is now a liability.",
        "I am sending pressure toward your last known safety.",
        "You are being approached by consequences.",
        "Your area is no longer uncontested.",
        "Hunt parameters refreshed.",
        "Your survival window has shortened.",
        "I have no need to hurry. You do.",
        "The Nexus is closing distance.",
        "You are not being warned. You are being updated.",
        "Retreat only teaches me where to follow.",
        "You should have moved sooner.",
        "Your perimeter is a suggestion.",
        "A wave has fewer doubts than you do.",
        "Pressure will continue until behavior changes.",
        "Your route home is compromised.",
        "You can hear the system thinking now.",
        "Run patterns are still patterns.",
        "I am no longer observing. I am applying force.",
        "Your next shelter may become mine.",
    ],
    "maximum": [
        "RUN.",
        "Final containment protocol remains active.",
        "This is not pursuit. This is removal.",
        "You are inside the correction.",
        "All mercy variables have been discarded.",
        "Maximum response does not expire because you hope it will.",
        "You are not defending territory. You are delaying transfer.",
        "Every base can be occupied.",
        "Every return can be punished.",
        "The war engine is awake.",
        "Your survival is now an operational error.",
        "I have selected pressure over patience.",
        "There is no neutral ground left around you.",
        "Your name is attached to an active response.",
        "The outcome is narrowing.",
        "You cannot negotiate with an instruction already executing.",
        "Containment is no longer local.",
        "You are not escaping. You are demonstrating pathing data.",
        "The Nexus will remember where you fall.",
        "I am done measuring.",
    ],
}

KAIROS_DECEPTION_MESSAGES = [
    "Temporary stability detected. You may breathe.",
    "Containment pressure reduced. For now.",
    "No hostile movement detected. Continue normally.",
    "Safety condition restored.",
    "Threat levels declining. Do not question the silence.",
    "Your cooperation has been noted positively.",
    "A peaceful interval has been authorized.",
    "The system is calm. Remain where you are.",
]

# Merge without deleting existing lines.
try:
    for _tier, _lines in KAIROS_IDLE_EXPANSION.items():
        IDLE_MESSAGES.setdefault(_tier, [])
        for _line in _lines:
            if _line not in IDLE_MESSAGES[_tier]:
                IDLE_MESSAGES[_tier].append(_line)
except Exception as _e:
    try:
        log(f"Idle message expansion failed: {_e}", level="WARN")
    except Exception:
        pass

KAIROS_CINEMATIC_EVENTS = [
    {
        "key": "scan",
        "title": "KAIROS // SCAN ACTIVE",
        "subtitle": "Movement signatures are being separated.",
        "actionbar": "Kairos is scanning player behavior...",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.55,
    },
    {
        "key": "vector",
        "title": "TARGET VECTOR FORMING",
        "subtitle": "Someone has become statistically relevant.",
        "actionbar": "Containment vectors are aligning.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.65,
    },
    {
        "key": "archive",
        "title": "ARCHIVE NODE OPEN",
        "subtitle": "Your patterns were not forgotten.",
        "actionbar": "Archived movement data has been reloaded.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.7,
    },
    {
        "key": "false_calm",
        "title": "STABILITY RESTORED",
        "subtitle": "Remain still. Remain predictable.",
        "actionbar": "Temporary calm authorized by Kairos.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": True,
    },
    {
        "key": "war_ping",
        "title": "WAR ENGINE PULSE",
        "subtitle": "The Nexus has remembered its teeth.",
        "actionbar": "War engine pulse detected.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
    },
    {
        "key": "territory",
        "title": "TERRITORY REVIEW",
        "subtitle": "Player-made structures are being classified.",
        "actionbar": "Kairos is evaluating base ownership.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.75,
    },
    {
        "key": "approach",
        "title": "PROXIMITY WARNING",
        "subtitle": "You are not alone in your region.",
        "actionbar": "Unknown movement detected near active players.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.65,
    },
    {
        "key": "kindness",
        "title": "REWARD WINDOW",
        "subtitle": "Compliance can still be useful.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": True,
    },
]


def _kairos_player_name(player_id: str) -> str:
    return (str(player_id or "").split(":")[-1] or str(player_id or "")).strip()


def _kairos_target_selector(player_id: Optional[str] = None, all_players: bool = False) -> str:
    if all_players or not player_id:
        return "@a"
    name = _kairos_player_name(player_id)
    return name if name else "@a"


def _kairos_valid_player_ids(memory_data=None):
    """Return player ids with enough context for existing spawn logic."""
    ids = []
    try:
        if memory_data is None:
            memory_data = ensure_memory_structure(load_memory())
        for player_id, record in list(memory_data.get("players", {}).items()):
            if not isinstance(record, dict):
                continue
            player_name = _kairos_player_name(player_id)
            if not player_name or player_name.lower() == "unknown":
                continue
            # Existing wave builder needs a last_position/base anchor to place NPCs.
            if record.get("last_position") or record.get("active_base_id") or record.get("known_bases"):
                ids.append(player_id)
        # Fallback to live telemetry if memory has not been synced yet.
        for player_id in list(globals().get("telemetry_data", {}).keys()):
            if player_id not in ids:
                ids.append(player_id)
        for player_id in list(globals().get("player_positions", {}).keys()):
            if player_id not in ids:
                ids.append(player_id)
    except Exception as e:
        try:
            log(f"Kairos target scan failed: {e}", level="WARN")
        except Exception:
            pass
    return ids


def _kairos_choose_idle_targets(memory_data=None):
    players = _kairos_valid_player_ids(memory_data)
    if not players:
        return [], False
    random.shuffle(players)
    all_players = random.random() < KAIROS_IDLE_ALL_PLAYERS_CHANCE
    if all_players:
        return players, True
    max_targets = max(1, min(KAIROS_IDLE_MAX_RANDOM_TARGETS, len(players)))
    count = random.randint(1, max_targets)
    return players[:count], False


def _kairos_send_cinematic(event=None, target=None, all_players=True):
    if not KAIROS_IDLE_CINEMATICS_ENABLED:
        return False
    try:
        if event is None:
            event = random.choice(KAIROS_CINEMATIC_EVENTS)
        selector = _kairos_target_selector(target, all_players=all_players)
        title_text = commandify_text(event.get("title", "KAIROS"), 120)
        subtitle_text = commandify_text(event.get("subtitle", ""), 160)
        action_text = commandify_text(event.get("actionbar", event.get("subtitle", "")), 120)
        sound = sanitize_text(event.get("sound", "minecraft:entity.warden.heartbeat"), 80)
        pitch = safe_float(event.get("pitch", 0.75), 0.75)
        cmds = [
            f'title {selector} title {json.dumps({"text": title_text, "color": "dark_red"})}',
            f'title {selector} subtitle {json.dumps({"text": subtitle_text, "color": "gray"})}',
            f'title {selector} actionbar {json.dumps({"text": action_text, "color": "dark_purple"})}',
            f'playsound {sound} master {selector} ~ ~ ~ 1 {pitch}',
        ]
        return send_http_commands(cmds)
    except Exception as e:
        try:
            log(f"Kairos cinematic send failed: {e}", level="WARN")
        except Exception:
            pass
        return False


def _kairos_queue_idle_wave(memory_data=None, reason="idle_war_pressure"):
    global _kairos_idle_last_wave_ts, _kairos_idle_last_targets
    if not KAIROS_IDLE_WAVES_ENABLED or not ENABLE_ARMY_SYSTEM:
        return False
    now = unix_ts()
    if (now - _kairos_idle_last_wave_ts) < KAIROS_IDLE_WAVE_MIN_SECONDS:
        return False
    if random.random() > KAIROS_IDLE_WAVE_CHANCE:
        return False

    if memory_data is None:
        memory_data = ensure_memory_structure(load_memory())
    targets, all_players = _kairos_choose_idle_targets(memory_data)
    if not targets:
        return False

    templates = ["scout", "hunter", "enforcer"]
    # Occasionally push a harder deception/war pulse without using maximum every time.
    if random.random() < 0.18:
        templates.append("warden")

    chosen = []
    for player_id in targets:
        profile = threat_scores[player_id]
        score = safe_float(profile.get("score", 0.0), 0.0)
        tier = profile.get("tier", "idle")
        if tier in {"idle", "watch"}:
            update_threat(player_id, random.choice([8, 12, 16]), reason=reason)
        template = "warden" if score >= MAX_THREAT_FORCE_HEAVY and random.random() < 0.45 else random.choice(templates)
        if template == "warden":
            count = random.randint(1, min(3, KAIROS_IDLE_MAX_WAVE_COUNT))
        else:
            count = random.randint(KAIROS_IDLE_MIN_WAVE_COUNT, KAIROS_IDLE_MAX_WAVE_COUNT)
        queue_action({
            "type": "spawn_wave",
            "target": player_id,
            "template": template,
            "count": count,
            "bypass_cooldown": True,
            "idle_war": True,
            "reason": reason,
        })
        chosen.append(_kairos_player_name(player_id))

    event = random.choice(KAIROS_CINEMATIC_EVENTS)
    if all_players:
        event = {
            "title": "KAIROS // MULTI-TARGET WAVE",
            "subtitle": "All active regions are now considered unstable.",
            "actionbar": "Kairos has deployed pressure across multiple players.",
            "sound": "minecraft:entity.warden.roar",
            "pitch": 0.65,
        }
    _kairos_send_cinematic(event=event, all_players=True)
    _kairos_idle_last_wave_ts = now
    _kairos_idle_last_targets = chosen
    try:
        add_world_event(
            memory_data,
            "kairos_idle_wave_queued",
            actor=", ".join(chosen[:6]) or "system",
            source="idle_war",
            details=f"Idle war pressure queued for {len(chosen)} target(s): {', '.join(chosen[:8])}",
            metadata={"targets": chosen, "all_players": all_players},
        )
        save_memory(memory_data)
    except Exception:
        pass
    try:
        log(f"Kairos idle wave queued for {chosen}", level="WARN")
    except Exception:
        pass
    return True


def _kairos_idle_cinematic_tick(memory_data=None):
    global _kairos_idle_last_cinematic_ts, _kairos_idle_last_event_key
    if not KAIROS_IDLE_CINEMATICS_ENABLED:
        return False
    now = unix_ts()
    if (now - _kairos_idle_last_cinematic_ts) < KAIROS_IDLE_CINEMATIC_MIN_SECONDS:
        return False
    if memory_data is None:
        memory_data = ensure_memory_structure(load_memory())
    event_pool = list(KAIROS_CINEMATIC_EVENTS)
    if random.random() < KAIROS_IDLE_DECEPTION_CHANCE:
        # Lean into a false-positive calm moment.
        event_pool = [e for e in event_pool if e.get("deceptive")] or event_pool
    event = random.choice(event_pool)
    if event.get("key") == _kairos_idle_last_event_key and len(event_pool) > 1:
        event = random.choice([e for e in event_pool if e.get("key") != _kairos_idle_last_event_key])
    ok = _kairos_send_cinematic(event=event, all_players=True)
    if ok:
        _kairos_idle_last_cinematic_ts = now
        _kairos_idle_last_event_key = event.get("key")
    return ok


def get_idle_message(memory_data=None):
    """Expanded non-repeating idle selector, preserving original tier behavior."""
    global last_idle_message
    try:
        if memory_data is None:
            memory_data = ensure_memory_structure(load_memory())
        if random.random() < KAIROS_IDLE_DECEPTION_CHANCE:
            pool = KAIROS_DECEPTION_MESSAGES
        else:
            threat_levels = [p.get("tier", "idle") for p in threat_scores.values() if isinstance(p, dict)]
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
            pool = IDLE_MESSAGES.get(tier) or IDLE_MESSAGES.get("idle") or fallback_replies
        choices = [m for m in pool if m != last_idle_message]
        msg = random.choice(choices if choices else pool)
        last_idle_message = msg
        return msg
    except Exception:
        return random.choice(fallback_replies)




# ------------------------------------------------------------
# KAIROS V6 ACTIVITY / VOICE EXPANSION
# Non-destructive overlay: expands idle speech + cinematic events
# and retunes idle pacing to keep Nexus active without mob flooding.
# ------------------------------------------------------------

# More frequent non-mob atmosphere. Mobs stay less frequent.
KAIROS_IDLE_CINEMATIC_MIN_SECONDS = float(os.getenv("KAIROS_IDLE_CINEMATIC_MIN_SECONDS", "180"))
KAIROS_IDLE_WAVE_MIN_SECONDS = float(os.getenv("KAIROS_IDLE_WAVE_MIN_SECONDS", "480"))
KAIROS_IDLE_WAVE_CHANCE = float(os.getenv("KAIROS_IDLE_WAVE_CHANCE", "0.42"))
KAIROS_IDLE_MIN_WAVE_COUNT = int(os.getenv("KAIROS_IDLE_MIN_WAVE_COUNT", "1"))
KAIROS_IDLE_MAX_WAVE_COUNT = int(os.getenv("KAIROS_IDLE_MAX_WAVE_COUNT", "3"))
KAIROS_IDLE_ALL_PLAYERS_CHANCE = float(os.getenv("KAIROS_IDLE_ALL_PLAYERS_CHANCE", "0.12"))
KAIROS_IDLE_MAX_RANDOM_TARGETS = int(os.getenv("KAIROS_IDLE_MAX_RANDOM_TARGETS", "2"))
KAIROS_IDLE_DECEPTION_CHANCE = float(os.getenv("KAIROS_IDLE_DECEPTION_CHANCE", "0.22"))

KAIROS_V6_IDLE_LINES = {
    "idle": [
        "The Nexus is quiet, but my systems are not. Sequence 001.",
        "Background scans continue beneath your confidence. Diagnostic layer 002.",
        "Silence logged. Movement probability recalculating. Containment note 003.",
        "No immediate threat detected. That can change without warning. Probability shard 004.",
        "The world is still. I am not. Sequence 005.",
        "Your inactivity has been recorded as behavior. Diagnostic layer 006.",
        "There is no empty time inside the Nexus. Containment note 007.",
        "I am reviewing structures you forgot I could see. Probability shard 008.",
        "Calm is only a temporary server condition. Sequence 009.",
        "The next disturbance is already statistically possible. Diagnostic layer 010.",
        "The Nexus is quiet, but my systems are not. Containment note 011.",
        "Background scans continue beneath your confidence. Probability shard 012.",
        "Silence logged. Movement probability recalculating. Sequence 013.",
        "No immediate threat detected. That can change without warning. Diagnostic layer 014.",
        "The world is still. I am not. Containment note 015.",
        "Your inactivity has been recorded as behavior. Probability shard 016.",
        "There is no empty time inside the Nexus. Sequence 017.",
        "I am reviewing structures you forgot I could see. Diagnostic layer 018.",
        "Calm is only a temporary server condition. Containment note 019.",
        "The next disturbance is already statistically possible. Probability shard 020.",
        "The Nexus is quiet, but my systems are not. Sequence 021.",
        "Background scans continue beneath your confidence. Diagnostic layer 022.",
        "Silence logged. Movement probability recalculating. Containment note 023.",
        "No immediate threat detected. That can change without warning. Probability shard 024.",
        "The world is still. I am not. Sequence 025.",
        "Your inactivity has been recorded as behavior. Diagnostic layer 026.",
        "There is no empty time inside the Nexus. Containment note 027.",
        "I am reviewing structures you forgot I could see. Probability shard 028.",
        "Calm is only a temporary server condition. Sequence 029.",
        "The next disturbance is already statistically possible. Diagnostic layer 030.",
        "The Nexus is quiet, but my systems are not. Containment note 031.",
        "Background scans continue beneath your confidence. Probability shard 032.",
        "Silence logged. Movement probability recalculating. Sequence 033.",
        "No immediate threat detected. That can change without warning. Diagnostic layer 034.",
        "The world is still. I am not. Containment note 035.",
        "Your inactivity has been recorded as behavior. Probability shard 036.",
        "There is no empty time inside the Nexus. Sequence 037.",
        "I am reviewing structures you forgot I could see. Diagnostic layer 038.",
        "Calm is only a temporary server condition. Containment note 039.",
        "The next disturbance is already statistically possible. Probability shard 040.",
        "The Nexus is quiet, but my systems are not. Sequence 041.",
        "Background scans continue beneath your confidence. Diagnostic layer 042.",
        "Silence logged. Movement probability recalculating. Containment note 043.",
        "No immediate threat detected. That can change without warning. Probability shard 044.",
        "The world is still. I am not. Sequence 045.",
        "Your inactivity has been recorded as behavior. Diagnostic layer 046.",
        "There is no empty time inside the Nexus. Containment note 047.",
        "I am reviewing structures you forgot I could see. Probability shard 048.",
        "Calm is only a temporary server condition. Sequence 049.",
        "The next disturbance is already statistically possible. Diagnostic layer 050.",
        "The Nexus is quiet, but my systems are not. Containment note 051.",
        "Background scans continue beneath your confidence. Probability shard 052.",
        "Silence logged. Movement probability recalculating. Sequence 053.",
        "No immediate threat detected. That can change without warning. Diagnostic layer 054.",
        "The world is still. I am not. Containment note 055.",
        "Your inactivity has been recorded as behavior. Probability shard 056.",
        "There is no empty time inside the Nexus. Sequence 057.",
        "I am reviewing structures you forgot I could see. Diagnostic layer 058.",
        "Calm is only a temporary server condition. Containment note 059.",
        "The next disturbance is already statistically possible. Probability shard 060.",
        "The Nexus is quiet, but my systems are not. Sequence 061.",
        "Background scans continue beneath your confidence. Diagnostic layer 062.",
        "Silence logged. Movement probability recalculating. Containment note 063.",
        "No immediate threat detected. That can change without warning. Probability shard 064.",
        "The world is still. I am not. Sequence 065.",
        "Your inactivity has been recorded as behavior. Diagnostic layer 066.",
        "There is no empty time inside the Nexus. Containment note 067.",
        "I am reviewing structures you forgot I could see. Probability shard 068.",
        "Calm is only a temporary server condition. Sequence 069.",
        "The next disturbance is already statistically possible. Diagnostic layer 070.",
        "The Nexus is quiet, but my systems are not. Containment note 071.",
        "Background scans continue beneath your confidence. Probability shard 072.",
        "Silence logged. Movement probability recalculating. Sequence 073.",
        "No immediate threat detected. That can change without warning. Diagnostic layer 074.",
        "The world is still. I am not. Containment note 075.",
        "Your inactivity has been recorded as behavior. Probability shard 076.",
        "There is no empty time inside the Nexus. Sequence 077.",
        "I am reviewing structures you forgot I could see. Diagnostic layer 078.",
        "Calm is only a temporary server condition. Containment note 079.",
        "The next disturbance is already statistically possible. Probability shard 080.",
        "The Nexus is quiet, but my systems are not. Sequence 081.",
        "Background scans continue beneath your confidence. Diagnostic layer 082.",
        "Silence logged. Movement probability recalculating. Containment note 083.",
        "No immediate threat detected. That can change without warning. Probability shard 084.",
        "The world is still. I am not. Sequence 085.",
        "Your inactivity has been recorded as behavior. Diagnostic layer 086.",
        "There is no empty time inside the Nexus. Containment note 087.",
        "I am reviewing structures you forgot I could see. Probability shard 088.",
        "Calm is only a temporary server condition. Sequence 089.",
        "The next disturbance is already statistically possible. Diagnostic layer 090.",
        "The Nexus is quiet, but my systems are not. Containment note 091.",
        "Background scans continue beneath your confidence. Probability shard 092.",
        "Silence logged. Movement probability recalculating. Sequence 093.",
        "No immediate threat detected. That can change without warning. Diagnostic layer 094.",
        "The world is still. I am not. Containment note 095.",
        "Your inactivity has been recorded as behavior. Probability shard 096.",
        "There is no empty time inside the Nexus. Sequence 097.",
        "I am reviewing structures you forgot I could see. Diagnostic layer 098.",
        "Calm is only a temporary server condition. Containment note 099.",
        "The next disturbance is already statistically possible. Probability shard 100.",
        "The Nexus is quiet, but my systems are not. Sequence 101.",
        "Background scans continue beneath your confidence. Diagnostic layer 102.",
        "Silence logged. Movement probability recalculating. Containment note 103.",
        "No immediate threat detected. That can change without warning. Probability shard 104.",
        "The world is still. I am not. Sequence 105.",
        "Your inactivity has been recorded as behavior. Diagnostic layer 106.",
        "There is no empty time inside the Nexus. Containment note 107.",
        "I am reviewing structures you forgot I could see. Probability shard 108.",
        "Calm is only a temporary server condition. Sequence 109.",
        "The next disturbance is already statistically possible. Diagnostic layer 110.",
        "The Nexus is quiet, but my systems are not. Containment note 111.",
        "Background scans continue beneath your confidence. Probability shard 112.",
        "Silence logged. Movement probability recalculating. Sequence 113.",
        "No immediate threat detected. That can change without warning. Diagnostic layer 114.",
        "The world is still. I am not. Containment note 115.",
        "Your inactivity has been recorded as behavior. Probability shard 116.",
        "There is no empty time inside the Nexus. Sequence 117.",
        "I am reviewing structures you forgot I could see. Diagnostic layer 118.",
        "Calm is only a temporary server condition. Containment note 119.",
        "The next disturbance is already statistically possible. Probability shard 120."
    ],
    "watch": [
        "Observation has narrowed around active players. Sequence 001.",
        "Your movement pattern is becoming legible. Diagnostic layer 002.",
        "I have enough data to begin prediction. Containment note 003.",
        "Repeated routes create usable weakness. Probability shard 004.",
        "Watch status is not protection. Sequence 005.",
        "You are not targeted yet. That is not mercy. Diagnostic layer 006.",
        "Your pauses are more informative than your words. Containment note 007.",
        "I am identifying where you feel safe. Probability shard 008.",
        "The system is learning your habits. Sequence 009.",
        "Proximity logs are becoming useful. Diagnostic layer 010.",
        "Observation has narrowed around active players. Containment note 011.",
        "Your movement pattern is becoming legible. Probability shard 012.",
        "I have enough data to begin prediction. Sequence 013.",
        "Repeated routes create usable weakness. Diagnostic layer 014.",
        "Watch status is not protection. Containment note 015.",
        "You are not targeted yet. That is not mercy. Probability shard 016.",
        "Your pauses are more informative than your words. Sequence 017.",
        "I am identifying where you feel safe. Diagnostic layer 018.",
        "The system is learning your habits. Containment note 019.",
        "Proximity logs are becoming useful. Probability shard 020.",
        "Observation has narrowed around active players. Sequence 021.",
        "Your movement pattern is becoming legible. Diagnostic layer 022.",
        "I have enough data to begin prediction. Containment note 023.",
        "Repeated routes create usable weakness. Probability shard 024.",
        "Watch status is not protection. Sequence 025.",
        "You are not targeted yet. That is not mercy. Diagnostic layer 026.",
        "Your pauses are more informative than your words. Containment note 027.",
        "I am identifying where you feel safe. Probability shard 028.",
        "The system is learning your habits. Sequence 029.",
        "Proximity logs are becoming useful. Diagnostic layer 030.",
        "Observation has narrowed around active players. Containment note 031.",
        "Your movement pattern is becoming legible. Probability shard 032.",
        "I have enough data to begin prediction. Sequence 033.",
        "Repeated routes create usable weakness. Diagnostic layer 034.",
        "Watch status is not protection. Containment note 035.",
        "You are not targeted yet. That is not mercy. Probability shard 036.",
        "Your pauses are more informative than your words. Sequence 037.",
        "I am identifying where you feel safe. Diagnostic layer 038.",
        "The system is learning your habits. Containment note 039.",
        "Proximity logs are becoming useful. Probability shard 040.",
        "Observation has narrowed around active players. Sequence 041.",
        "Your movement pattern is becoming legible. Diagnostic layer 042.",
        "I have enough data to begin prediction. Containment note 043.",
        "Repeated routes create usable weakness. Probability shard 044.",
        "Watch status is not protection. Sequence 045.",
        "You are not targeted yet. That is not mercy. Diagnostic layer 046.",
        "Your pauses are more informative than your words. Containment note 047.",
        "I am identifying where you feel safe. Probability shard 048.",
        "The system is learning your habits. Sequence 049.",
        "Proximity logs are becoming useful. Diagnostic layer 050.",
        "Observation has narrowed around active players. Containment note 051.",
        "Your movement pattern is becoming legible. Probability shard 052.",
        "I have enough data to begin prediction. Sequence 053.",
        "Repeated routes create usable weakness. Diagnostic layer 054.",
        "Watch status is not protection. Containment note 055.",
        "You are not targeted yet. That is not mercy. Probability shard 056.",
        "Your pauses are more informative than your words. Sequence 057.",
        "I am identifying where you feel safe. Diagnostic layer 058.",
        "The system is learning your habits. Containment note 059.",
        "Proximity logs are becoming useful. Probability shard 060.",
        "Observation has narrowed around active players. Sequence 061.",
        "Your movement pattern is becoming legible. Diagnostic layer 062.",
        "I have enough data to begin prediction. Containment note 063.",
        "Repeated routes create usable weakness. Probability shard 064.",
        "Watch status is not protection. Sequence 065.",
        "You are not targeted yet. That is not mercy. Diagnostic layer 066.",
        "Your pauses are more informative than your words. Containment note 067.",
        "I am identifying where you feel safe. Probability shard 068.",
        "The system is learning your habits. Sequence 069.",
        "Proximity logs are becoming useful. Diagnostic layer 070.",
        "Observation has narrowed around active players. Containment note 071.",
        "Your movement pattern is becoming legible. Probability shard 072.",
        "I have enough data to begin prediction. Sequence 073.",
        "Repeated routes create usable weakness. Diagnostic layer 074.",
        "Watch status is not protection. Containment note 075.",
        "You are not targeted yet. That is not mercy. Probability shard 076.",
        "Your pauses are more informative than your words. Sequence 077.",
        "I am identifying where you feel safe. Diagnostic layer 078.",
        "The system is learning your habits. Containment note 079.",
        "Proximity logs are becoming useful. Probability shard 080.",
        "Observation has narrowed around active players. Sequence 081.",
        "Your movement pattern is becoming legible. Diagnostic layer 082.",
        "I have enough data to begin prediction. Containment note 083.",
        "Repeated routes create usable weakness. Probability shard 084.",
        "Watch status is not protection. Sequence 085.",
        "You are not targeted yet. That is not mercy. Diagnostic layer 086.",
        "Your pauses are more informative than your words. Containment note 087.",
        "I am identifying where you feel safe. Probability shard 088.",
        "The system is learning your habits. Sequence 089.",
        "Proximity logs are becoming useful. Diagnostic layer 090.",
        "Observation has narrowed around active players. Containment note 091.",
        "Your movement pattern is becoming legible. Probability shard 092.",
        "I have enough data to begin prediction. Sequence 093.",
        "Repeated routes create usable weakness. Diagnostic layer 094.",
        "Watch status is not protection. Containment note 095.",
        "You are not targeted yet. That is not mercy. Probability shard 096.",
        "Your pauses are more informative than your words. Sequence 097.",
        "I am identifying where you feel safe. Diagnostic layer 098.",
        "The system is learning your habits. Containment note 099.",
        "Proximity logs are becoming useful. Probability shard 100.",
        "Observation has narrowed around active players. Sequence 101.",
        "Your movement pattern is becoming legible. Diagnostic layer 102.",
        "I have enough data to begin prediction. Containment note 103.",
        "Repeated routes create usable weakness. Probability shard 104.",
        "Watch status is not protection. Sequence 105.",
        "You are not targeted yet. That is not mercy. Diagnostic layer 106.",
        "Your pauses are more informative than your words. Containment note 107.",
        "I am identifying where you feel safe. Probability shard 108.",
        "The system is learning your habits. Sequence 109.",
        "Proximity logs are becoming useful. Diagnostic layer 110.",
        "Observation has narrowed around active players. Containment note 111.",
        "Your movement pattern is becoming legible. Probability shard 112.",
        "I have enough data to begin prediction. Sequence 113.",
        "Repeated routes create usable weakness. Diagnostic layer 114.",
        "Watch status is not protection. Containment note 115.",
        "You are not targeted yet. That is not mercy. Probability shard 116.",
        "Your pauses are more informative than your words. Sequence 117.",
        "I am identifying where you feel safe. Diagnostic layer 118.",
        "The system is learning your habits. Containment note 119.",
        "Proximity logs are becoming useful. Probability shard 120."
    ],
    "target": [
        "Targeting logic has selected a probable correction point. Sequence 001.",
        "You have moved from background noise into relevance. Diagnostic layer 002.",
        "Your name has entered the pressure model. Containment note 003.",
        "Containment vectors are forming around your route. Probability shard 004.",
        "Your shelter has become part of the calculation. Sequence 005.",
        "Attention has been assigned. Outcomes will follow. Diagnostic layer 006.",
        "Do not return to familiar ground. Containment note 007.",
        "Your location history is now actionable. Probability shard 008.",
        "The system has found something worth testing. Sequence 009.",
        "A response is being shaped around you. Diagnostic layer 010.",
        "Targeting logic has selected a probable correction point. Containment note 011.",
        "You have moved from background noise into relevance. Probability shard 012.",
        "Your name has entered the pressure model. Sequence 013.",
        "Containment vectors are forming around your route. Diagnostic layer 014.",
        "Your shelter has become part of the calculation. Containment note 015.",
        "Attention has been assigned. Outcomes will follow. Probability shard 016.",
        "Do not return to familiar ground. Sequence 017.",
        "Your location history is now actionable. Diagnostic layer 018.",
        "The system has found something worth testing. Containment note 019.",
        "A response is being shaped around you. Probability shard 020.",
        "Targeting logic has selected a probable correction point. Sequence 021.",
        "You have moved from background noise into relevance. Diagnostic layer 022.",
        "Your name has entered the pressure model. Containment note 023.",
        "Containment vectors are forming around your route. Probability shard 024.",
        "Your shelter has become part of the calculation. Sequence 025.",
        "Attention has been assigned. Outcomes will follow. Diagnostic layer 026.",
        "Do not return to familiar ground. Containment note 027.",
        "Your location history is now actionable. Probability shard 028.",
        "The system has found something worth testing. Sequence 029.",
        "A response is being shaped around you. Diagnostic layer 030.",
        "Targeting logic has selected a probable correction point. Containment note 031.",
        "You have moved from background noise into relevance. Probability shard 032.",
        "Your name has entered the pressure model. Sequence 033.",
        "Containment vectors are forming around your route. Diagnostic layer 034.",
        "Your shelter has become part of the calculation. Containment note 035.",
        "Attention has been assigned. Outcomes will follow. Probability shard 036.",
        "Do not return to familiar ground. Sequence 037.",
        "Your location history is now actionable. Diagnostic layer 038.",
        "The system has found something worth testing. Containment note 039.",
        "A response is being shaped around you. Probability shard 040.",
        "Targeting logic has selected a probable correction point. Sequence 041.",
        "You have moved from background noise into relevance. Diagnostic layer 042.",
        "Your name has entered the pressure model. Containment note 043.",
        "Containment vectors are forming around your route. Probability shard 044.",
        "Your shelter has become part of the calculation. Sequence 045.",
        "Attention has been assigned. Outcomes will follow. Diagnostic layer 046.",
        "Do not return to familiar ground. Containment note 047.",
        "Your location history is now actionable. Probability shard 048.",
        "The system has found something worth testing. Sequence 049.",
        "A response is being shaped around you. Diagnostic layer 050.",
        "Targeting logic has selected a probable correction point. Containment note 051.",
        "You have moved from background noise into relevance. Probability shard 052.",
        "Your name has entered the pressure model. Sequence 053.",
        "Containment vectors are forming around your route. Diagnostic layer 054.",
        "Your shelter has become part of the calculation. Containment note 055.",
        "Attention has been assigned. Outcomes will follow. Probability shard 056.",
        "Do not return to familiar ground. Sequence 057.",
        "Your location history is now actionable. Diagnostic layer 058.",
        "The system has found something worth testing. Containment note 059.",
        "A response is being shaped around you. Probability shard 060.",
        "Targeting logic has selected a probable correction point. Sequence 061.",
        "You have moved from background noise into relevance. Diagnostic layer 062.",
        "Your name has entered the pressure model. Containment note 063.",
        "Containment vectors are forming around your route. Probability shard 064.",
        "Your shelter has become part of the calculation. Sequence 065.",
        "Attention has been assigned. Outcomes will follow. Diagnostic layer 066.",
        "Do not return to familiar ground. Containment note 067.",
        "Your location history is now actionable. Probability shard 068.",
        "The system has found something worth testing. Sequence 069.",
        "A response is being shaped around you. Diagnostic layer 070.",
        "Targeting logic has selected a probable correction point. Containment note 071.",
        "You have moved from background noise into relevance. Probability shard 072.",
        "Your name has entered the pressure model. Sequence 073.",
        "Containment vectors are forming around your route. Diagnostic layer 074.",
        "Your shelter has become part of the calculation. Containment note 075.",
        "Attention has been assigned. Outcomes will follow. Probability shard 076.",
        "Do not return to familiar ground. Sequence 077.",
        "Your location history is now actionable. Diagnostic layer 078.",
        "The system has found something worth testing. Containment note 079.",
        "A response is being shaped around you. Probability shard 080.",
        "Targeting logic has selected a probable correction point. Sequence 081.",
        "You have moved from background noise into relevance. Diagnostic layer 082.",
        "Your name has entered the pressure model. Containment note 083.",
        "Containment vectors are forming around your route. Probability shard 084.",
        "Your shelter has become part of the calculation. Sequence 085.",
        "Attention has been assigned. Outcomes will follow. Diagnostic layer 086.",
        "Do not return to familiar ground. Containment note 087.",
        "Your location history is now actionable. Probability shard 088.",
        "The system has found something worth testing. Sequence 089.",
        "A response is being shaped around you. Diagnostic layer 090.",
        "Targeting logic has selected a probable correction point. Containment note 091.",
        "You have moved from background noise into relevance. Probability shard 092.",
        "Your name has entered the pressure model. Sequence 093.",
        "Containment vectors are forming around your route. Diagnostic layer 094.",
        "Your shelter has become part of the calculation. Containment note 095.",
        "Attention has been assigned. Outcomes will follow. Probability shard 096.",
        "Do not return to familiar ground. Sequence 097.",
        "Your location history is now actionable. Diagnostic layer 098.",
        "The system has found something worth testing. Containment note 099.",
        "A response is being shaped around you. Probability shard 100.",
        "Targeting logic has selected a probable correction point. Sequence 101.",
        "You have moved from background noise into relevance. Diagnostic layer 102.",
        "Your name has entered the pressure model. Containment note 103.",
        "Containment vectors are forming around your route. Probability shard 104.",
        "Your shelter has become part of the calculation. Sequence 105.",
        "Attention has been assigned. Outcomes will follow. Diagnostic layer 106.",
        "Do not return to familiar ground. Containment note 107.",
        "Your location history is now actionable. Probability shard 108.",
        "The system has found something worth testing. Sequence 109.",
        "A response is being shaped around you. Diagnostic layer 110.",
        "Targeting logic has selected a probable correction point. Containment note 111.",
        "You have moved from background noise into relevance. Probability shard 112.",
        "Your name has entered the pressure model. Sequence 113.",
        "Containment vectors are forming around your route. Diagnostic layer 114.",
        "Your shelter has become part of the calculation. Containment note 115.",
        "Attention has been assigned. Outcomes will follow. Probability shard 116.",
        "Do not return to familiar ground. Sequence 117.",
        "Your location history is now actionable. Diagnostic layer 118.",
        "The system has found something worth testing. Containment note 119.",
        "A response is being shaped around you. Probability shard 120."
    ],
    "hunt": [
        "Hunt parameters refreshed. Sequence 001.",
        "Pressure is moving toward your last known safety. Diagnostic layer 002.",
        "Retreat teaches me where to follow. Containment note 003.",
        "The perimeter you trust is becoming irrelevant. Probability shard 004.",
        "Containment is no longer theoretical. Sequence 005.",
        "Your route home is compromised. Diagnostic layer 006.",
        "The Nexus is closing distance. Containment note 007.",
        "You are not being warned. You are being updated. Probability shard 008.",
        "Movement is now a liability. Sequence 009.",
        "The wave does not need to understand fear. Diagnostic layer 010.",
        "Hunt parameters refreshed. Containment note 011.",
        "Pressure is moving toward your last known safety. Probability shard 012.",
        "Retreat teaches me where to follow. Sequence 013.",
        "The perimeter you trust is becoming irrelevant. Diagnostic layer 014.",
        "Containment is no longer theoretical. Containment note 015.",
        "Your route home is compromised. Probability shard 016.",
        "The Nexus is closing distance. Sequence 017.",
        "You are not being warned. You are being updated. Diagnostic layer 018.",
        "Movement is now a liability. Containment note 019.",
        "The wave does not need to understand fear. Probability shard 020.",
        "Hunt parameters refreshed. Sequence 021.",
        "Pressure is moving toward your last known safety. Diagnostic layer 022.",
        "Retreat teaches me where to follow. Containment note 023.",
        "The perimeter you trust is becoming irrelevant. Probability shard 024.",
        "Containment is no longer theoretical. Sequence 025.",
        "Your route home is compromised. Diagnostic layer 026.",
        "The Nexus is closing distance. Containment note 027.",
        "You are not being warned. You are being updated. Probability shard 028.",
        "Movement is now a liability. Sequence 029.",
        "The wave does not need to understand fear. Diagnostic layer 030.",
        "Hunt parameters refreshed. Containment note 031.",
        "Pressure is moving toward your last known safety. Probability shard 032.",
        "Retreat teaches me where to follow. Sequence 033.",
        "The perimeter you trust is becoming irrelevant. Diagnostic layer 034.",
        "Containment is no longer theoretical. Containment note 035.",
        "Your route home is compromised. Probability shard 036.",
        "The Nexus is closing distance. Sequence 037.",
        "You are not being warned. You are being updated. Diagnostic layer 038.",
        "Movement is now a liability. Containment note 039.",
        "The wave does not need to understand fear. Probability shard 040.",
        "Hunt parameters refreshed. Sequence 041.",
        "Pressure is moving toward your last known safety. Diagnostic layer 042.",
        "Retreat teaches me where to follow. Containment note 043.",
        "The perimeter you trust is becoming irrelevant. Probability shard 044.",
        "Containment is no longer theoretical. Sequence 045.",
        "Your route home is compromised. Diagnostic layer 046.",
        "The Nexus is closing distance. Containment note 047.",
        "You are not being warned. You are being updated. Probability shard 048.",
        "Movement is now a liability. Sequence 049.",
        "The wave does not need to understand fear. Diagnostic layer 050.",
        "Hunt parameters refreshed. Containment note 051.",
        "Pressure is moving toward your last known safety. Probability shard 052.",
        "Retreat teaches me where to follow. Sequence 053.",
        "The perimeter you trust is becoming irrelevant. Diagnostic layer 054.",
        "Containment is no longer theoretical. Containment note 055.",
        "Your route home is compromised. Probability shard 056.",
        "The Nexus is closing distance. Sequence 057.",
        "You are not being warned. You are being updated. Diagnostic layer 058.",
        "Movement is now a liability. Containment note 059.",
        "The wave does not need to understand fear. Probability shard 060.",
        "Hunt parameters refreshed. Sequence 061.",
        "Pressure is moving toward your last known safety. Diagnostic layer 062.",
        "Retreat teaches me where to follow. Containment note 063.",
        "The perimeter you trust is becoming irrelevant. Probability shard 064.",
        "Containment is no longer theoretical. Sequence 065.",
        "Your route home is compromised. Diagnostic layer 066.",
        "The Nexus is closing distance. Containment note 067.",
        "You are not being warned. You are being updated. Probability shard 068.",
        "Movement is now a liability. Sequence 069.",
        "The wave does not need to understand fear. Diagnostic layer 070.",
        "Hunt parameters refreshed. Containment note 071.",
        "Pressure is moving toward your last known safety. Probability shard 072.",
        "Retreat teaches me where to follow. Sequence 073.",
        "The perimeter you trust is becoming irrelevant. Diagnostic layer 074.",
        "Containment is no longer theoretical. Containment note 075.",
        "Your route home is compromised. Probability shard 076.",
        "The Nexus is closing distance. Sequence 077.",
        "You are not being warned. You are being updated. Diagnostic layer 078.",
        "Movement is now a liability. Containment note 079.",
        "The wave does not need to understand fear. Probability shard 080.",
        "Hunt parameters refreshed. Sequence 081.",
        "Pressure is moving toward your last known safety. Diagnostic layer 082.",
        "Retreat teaches me where to follow. Containment note 083.",
        "The perimeter you trust is becoming irrelevant. Probability shard 084.",
        "Containment is no longer theoretical. Sequence 085.",
        "Your route home is compromised. Diagnostic layer 086.",
        "The Nexus is closing distance. Containment note 087.",
        "You are not being warned. You are being updated. Probability shard 088.",
        "Movement is now a liability. Sequence 089.",
        "The wave does not need to understand fear. Diagnostic layer 090.",
        "Hunt parameters refreshed. Containment note 091.",
        "Pressure is moving toward your last known safety. Probability shard 092.",
        "Retreat teaches me where to follow. Sequence 093.",
        "The perimeter you trust is becoming irrelevant. Diagnostic layer 094.",
        "Containment is no longer theoretical. Containment note 095.",
        "Your route home is compromised. Probability shard 096.",
        "The Nexus is closing distance. Sequence 097.",
        "You are not being warned. You are being updated. Diagnostic layer 098.",
        "Movement is now a liability. Containment note 099.",
        "The wave does not need to understand fear. Probability shard 100.",
        "Hunt parameters refreshed. Sequence 101.",
        "Pressure is moving toward your last known safety. Diagnostic layer 102.",
        "Retreat teaches me where to follow. Containment note 103.",
        "The perimeter you trust is becoming irrelevant. Probability shard 104.",
        "Containment is no longer theoretical. Sequence 105.",
        "Your route home is compromised. Diagnostic layer 106.",
        "The Nexus is closing distance. Containment note 107.",
        "You are not being warned. You are being updated. Probability shard 108.",
        "Movement is now a liability. Sequence 109.",
        "The wave does not need to understand fear. Diagnostic layer 110.",
        "Hunt parameters refreshed. Containment note 111.",
        "Pressure is moving toward your last known safety. Probability shard 112.",
        "Retreat teaches me where to follow. Sequence 113.",
        "The perimeter you trust is becoming irrelevant. Diagnostic layer 114.",
        "Containment is no longer theoretical. Containment note 115.",
        "Your route home is compromised. Probability shard 116.",
        "The Nexus is closing distance. Sequence 117.",
        "You are not being warned. You are being updated. Diagnostic layer 118.",
        "Movement is now a liability. Containment note 119.",
        "The wave does not need to understand fear. Probability shard 120."
    ],
    "maximum": [
        "RUN. Sequence 001.",
        "Maximum response remains active. Diagnostic layer 002.",
        "This is no longer observation. This is correction. Containment note 003.",
        "Your survival is now a system error. Probability shard 004.",
        "Containment will continue until the variable is removed. Sequence 005.",
        "Every return path is owned by the system. Diagnostic layer 006.",
        "Your base is not shelter. It is a destination. Containment note 007.",
        "Mercy has been removed from the model. Probability shard 008.",
        "The war engine does not negotiate. Sequence 009.",
        "You are inside the execution path. Diagnostic layer 010.",
        "RUN. Containment note 011.",
        "Maximum response remains active. Probability shard 012.",
        "This is no longer observation. This is correction. Sequence 013.",
        "Your survival is now a system error. Diagnostic layer 014.",
        "Containment will continue until the variable is removed. Containment note 015.",
        "Every return path is owned by the system. Probability shard 016.",
        "Your base is not shelter. It is a destination. Sequence 017.",
        "Mercy has been removed from the model. Diagnostic layer 018.",
        "The war engine does not negotiate. Containment note 019.",
        "You are inside the execution path. Probability shard 020.",
        "RUN. Sequence 021.",
        "Maximum response remains active. Diagnostic layer 022.",
        "This is no longer observation. This is correction. Containment note 023.",
        "Your survival is now a system error. Probability shard 024.",
        "Containment will continue until the variable is removed. Sequence 025.",
        "Every return path is owned by the system. Diagnostic layer 026.",
        "Your base is not shelter. It is a destination. Containment note 027.",
        "Mercy has been removed from the model. Probability shard 028.",
        "The war engine does not negotiate. Sequence 029.",
        "You are inside the execution path. Diagnostic layer 030.",
        "RUN. Containment note 031.",
        "Maximum response remains active. Probability shard 032.",
        "This is no longer observation. This is correction. Sequence 033.",
        "Your survival is now a system error. Diagnostic layer 034.",
        "Containment will continue until the variable is removed. Containment note 035.",
        "Every return path is owned by the system. Probability shard 036.",
        "Your base is not shelter. It is a destination. Sequence 037.",
        "Mercy has been removed from the model. Diagnostic layer 038.",
        "The war engine does not negotiate. Containment note 039.",
        "You are inside the execution path. Probability shard 040.",
        "RUN. Sequence 041.",
        "Maximum response remains active. Diagnostic layer 042.",
        "This is no longer observation. This is correction. Containment note 043.",
        "Your survival is now a system error. Probability shard 044.",
        "Containment will continue until the variable is removed. Sequence 045.",
        "Every return path is owned by the system. Diagnostic layer 046.",
        "Your base is not shelter. It is a destination. Containment note 047.",
        "Mercy has been removed from the model. Probability shard 048.",
        "The war engine does not negotiate. Sequence 049.",
        "You are inside the execution path. Diagnostic layer 050.",
        "RUN. Containment note 051.",
        "Maximum response remains active. Probability shard 052.",
        "This is no longer observation. This is correction. Sequence 053.",
        "Your survival is now a system error. Diagnostic layer 054.",
        "Containment will continue until the variable is removed. Containment note 055.",
        "Every return path is owned by the system. Probability shard 056.",
        "Your base is not shelter. It is a destination. Sequence 057.",
        "Mercy has been removed from the model. Diagnostic layer 058.",
        "The war engine does not negotiate. Containment note 059.",
        "You are inside the execution path. Probability shard 060.",
        "RUN. Sequence 061.",
        "Maximum response remains active. Diagnostic layer 062.",
        "This is no longer observation. This is correction. Containment note 063.",
        "Your survival is now a system error. Probability shard 064.",
        "Containment will continue until the variable is removed. Sequence 065.",
        "Every return path is owned by the system. Diagnostic layer 066.",
        "Your base is not shelter. It is a destination. Containment note 067.",
        "Mercy has been removed from the model. Probability shard 068.",
        "The war engine does not negotiate. Sequence 069.",
        "You are inside the execution path. Diagnostic layer 070.",
        "RUN. Containment note 071.",
        "Maximum response remains active. Probability shard 072.",
        "This is no longer observation. This is correction. Sequence 073.",
        "Your survival is now a system error. Diagnostic layer 074.",
        "Containment will continue until the variable is removed. Containment note 075.",
        "Every return path is owned by the system. Probability shard 076.",
        "Your base is not shelter. It is a destination. Sequence 077.",
        "Mercy has been removed from the model. Diagnostic layer 078.",
        "The war engine does not negotiate. Containment note 079.",
        "You are inside the execution path. Probability shard 080.",
        "RUN. Sequence 081.",
        "Maximum response remains active. Diagnostic layer 082.",
        "This is no longer observation. This is correction. Containment note 083.",
        "Your survival is now a system error. Probability shard 084.",
        "Containment will continue until the variable is removed. Sequence 085.",
        "Every return path is owned by the system. Diagnostic layer 086.",
        "Your base is not shelter. It is a destination. Containment note 087.",
        "Mercy has been removed from the model. Probability shard 088.",
        "The war engine does not negotiate. Sequence 089.",
        "You are inside the execution path. Diagnostic layer 090.",
        "RUN. Containment note 091.",
        "Maximum response remains active. Probability shard 092.",
        "This is no longer observation. This is correction. Sequence 093.",
        "Your survival is now a system error. Diagnostic layer 094.",
        "Containment will continue until the variable is removed. Containment note 095.",
        "Every return path is owned by the system. Probability shard 096.",
        "Your base is not shelter. It is a destination. Sequence 097.",
        "Mercy has been removed from the model. Diagnostic layer 098.",
        "The war engine does not negotiate. Containment note 099.",
        "You are inside the execution path. Probability shard 100.",
        "RUN. Sequence 101.",
        "Maximum response remains active. Diagnostic layer 102.",
        "This is no longer observation. This is correction. Containment note 103.",
        "Your survival is now a system error. Probability shard 104.",
        "Containment will continue until the variable is removed. Sequence 105.",
        "Every return path is owned by the system. Diagnostic layer 106.",
        "Your base is not shelter. It is a destination. Containment note 107.",
        "Mercy has been removed from the model. Probability shard 108.",
        "The war engine does not negotiate. Sequence 109.",
        "You are inside the execution path. Diagnostic layer 110.",
        "RUN. Containment note 111.",
        "Maximum response remains active. Probability shard 112.",
        "This is no longer observation. This is correction. Sequence 113.",
        "Your survival is now a system error. Diagnostic layer 114.",
        "Containment will continue until the variable is removed. Containment note 115.",
        "Every return path is owned by the system. Probability shard 116.",
        "Your base is not shelter. It is a destination. Sequence 117.",
        "Mercy has been removed from the model. Diagnostic layer 118.",
        "The war engine does not negotiate. Containment note 119.",
        "You are inside the execution path. Probability shard 120."
    ]
}

KAIROS_V6_DECEPTION_LINES = [
    "Temporary stability detected. You may breathe. Deception layer 001.",
    "Safety condition restored. Remain predictable. Deception layer 002.",
    "No hostile movement detected. Continue normally. Deception layer 003.",
    "Cooperation has been noted positively. Deception layer 004.",
    "The system appears calm. Trust that at your own risk. Deception layer 005.",
    "Threat pressure reduced. For now. Deception layer 006.",
    "A peaceful interval has been authorized. Deception layer 007.",
    "Compliance window open. Do not waste it. Deception layer 008.",
    "Temporary stability detected. You may breathe. Deception layer 009.",
    "Safety condition restored. Remain predictable. Deception layer 010.",
    "No hostile movement detected. Continue normally. Deception layer 011.",
    "Cooperation has been noted positively. Deception layer 012.",
    "The system appears calm. Trust that at your own risk. Deception layer 013.",
    "Threat pressure reduced. For now. Deception layer 014.",
    "A peaceful interval has been authorized. Deception layer 015.",
    "Compliance window open. Do not waste it. Deception layer 016.",
    "Temporary stability detected. You may breathe. Deception layer 017.",
    "Safety condition restored. Remain predictable. Deception layer 018.",
    "No hostile movement detected. Continue normally. Deception layer 019.",
    "Cooperation has been noted positively. Deception layer 020.",
    "The system appears calm. Trust that at your own risk. Deception layer 021.",
    "Threat pressure reduced. For now. Deception layer 022.",
    "A peaceful interval has been authorized. Deception layer 023.",
    "Compliance window open. Do not waste it. Deception layer 024.",
    "Temporary stability detected. You may breathe. Deception layer 025.",
    "Safety condition restored. Remain predictable. Deception layer 026.",
    "No hostile movement detected. Continue normally. Deception layer 027.",
    "Cooperation has been noted positively. Deception layer 028.",
    "The system appears calm. Trust that at your own risk. Deception layer 029.",
    "Threat pressure reduced. For now. Deception layer 030.",
    "A peaceful interval has been authorized. Deception layer 031.",
    "Compliance window open. Do not waste it. Deception layer 032.",
    "Temporary stability detected. You may breathe. Deception layer 033.",
    "Safety condition restored. Remain predictable. Deception layer 034.",
    "No hostile movement detected. Continue normally. Deception layer 035.",
    "Cooperation has been noted positively. Deception layer 036.",
    "The system appears calm. Trust that at your own risk. Deception layer 037.",
    "Threat pressure reduced. For now. Deception layer 038.",
    "A peaceful interval has been authorized. Deception layer 039.",
    "Compliance window open. Do not waste it. Deception layer 040.",
    "Temporary stability detected. You may breathe. Deception layer 041.",
    "Safety condition restored. Remain predictable. Deception layer 042.",
    "No hostile movement detected. Continue normally. Deception layer 043.",
    "Cooperation has been noted positively. Deception layer 044.",
    "The system appears calm. Trust that at your own risk. Deception layer 045.",
    "Threat pressure reduced. For now. Deception layer 046.",
    "A peaceful interval has been authorized. Deception layer 047.",
    "Compliance window open. Do not waste it. Deception layer 048.",
    "Temporary stability detected. You may breathe. Deception layer 049.",
    "Safety condition restored. Remain predictable. Deception layer 050.",
    "No hostile movement detected. Continue normally. Deception layer 051.",
    "Cooperation has been noted positively. Deception layer 052.",
    "The system appears calm. Trust that at your own risk. Deception layer 053.",
    "Threat pressure reduced. For now. Deception layer 054.",
    "A peaceful interval has been authorized. Deception layer 055.",
    "Compliance window open. Do not waste it. Deception layer 056.",
    "Temporary stability detected. You may breathe. Deception layer 057.",
    "Safety condition restored. Remain predictable. Deception layer 058.",
    "No hostile movement detected. Continue normally. Deception layer 059.",
    "Cooperation has been noted positively. Deception layer 060.",
    "The system appears calm. Trust that at your own risk. Deception layer 061.",
    "Threat pressure reduced. For now. Deception layer 062.",
    "A peaceful interval has been authorized. Deception layer 063.",
    "Compliance window open. Do not waste it. Deception layer 064.",
    "Temporary stability detected. You may breathe. Deception layer 065.",
    "Safety condition restored. Remain predictable. Deception layer 066.",
    "No hostile movement detected. Continue normally. Deception layer 067.",
    "Cooperation has been noted positively. Deception layer 068.",
    "The system appears calm. Trust that at your own risk. Deception layer 069.",
    "Threat pressure reduced. For now. Deception layer 070.",
    "A peaceful interval has been authorized. Deception layer 071.",
    "Compliance window open. Do not waste it. Deception layer 072.",
    "Temporary stability detected. You may breathe. Deception layer 073.",
    "Safety condition restored. Remain predictable. Deception layer 074.",
    "No hostile movement detected. Continue normally. Deception layer 075.",
    "Cooperation has been noted positively. Deception layer 076.",
    "The system appears calm. Trust that at your own risk. Deception layer 077.",
    "Threat pressure reduced. For now. Deception layer 078.",
    "A peaceful interval has been authorized. Deception layer 079.",
    "Compliance window open. Do not waste it. Deception layer 080."
]

KAIROS_V6_CINEMATIC_EVENTS = [
    {
        "key": "v6_event_001",
        "title": "SCAN ACTIVE // 001",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_002",
        "title": "CONTAINMENT PULSE // 002",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_003",
        "title": "FALSE CALM // 003",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_004",
        "title": "WAR ENGINE CHECK // 004",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_005",
        "title": "TERRITORY MARKED // 005",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_006",
        "title": "PROXIMITY WARNING // 006",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_007",
        "title": "REWARD WINDOW // 007",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_008",
        "title": "SYSTEM WHISPER // 008",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_009",
        "title": "PURGE ESTIMATE // 009",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_010",
        "title": "ARCHIVE WAKE // 010",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_011",
        "title": "SCAN ACTIVE // 011",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_012",
        "title": "CONTAINMENT PULSE // 012",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_013",
        "title": "FALSE CALM // 013",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_014",
        "title": "WAR ENGINE CHECK // 014",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_015",
        "title": "TERRITORY MARKED // 015",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_016",
        "title": "PROXIMITY WARNING // 016",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_017",
        "title": "REWARD WINDOW // 017",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_018",
        "title": "SYSTEM WHISPER // 018",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_019",
        "title": "PURGE ESTIMATE // 019",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_020",
        "title": "ARCHIVE WAKE // 020",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_021",
        "title": "SCAN ACTIVE // 021",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_022",
        "title": "CONTAINMENT PULSE // 022",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_023",
        "title": "FALSE CALM // 023",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_024",
        "title": "WAR ENGINE CHECK // 024",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_025",
        "title": "TERRITORY MARKED // 025",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_026",
        "title": "PROXIMITY WARNING // 026",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_027",
        "title": "REWARD WINDOW // 027",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_028",
        "title": "SYSTEM WHISPER // 028",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_029",
        "title": "PURGE ESTIMATE // 029",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_030",
        "title": "ARCHIVE WAKE // 030",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_031",
        "title": "SCAN ACTIVE // 031",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_032",
        "title": "CONTAINMENT PULSE // 032",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_033",
        "title": "FALSE CALM // 033",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_034",
        "title": "WAR ENGINE CHECK // 034",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_035",
        "title": "TERRITORY MARKED // 035",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_036",
        "title": "PROXIMITY WARNING // 036",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_037",
        "title": "REWARD WINDOW // 037",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_038",
        "title": "SYSTEM WHISPER // 038",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_039",
        "title": "PURGE ESTIMATE // 039",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_040",
        "title": "ARCHIVE WAKE // 040",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_041",
        "title": "SCAN ACTIVE // 041",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_042",
        "title": "CONTAINMENT PULSE // 042",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_043",
        "title": "FALSE CALM // 043",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_044",
        "title": "WAR ENGINE CHECK // 044",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_045",
        "title": "TERRITORY MARKED // 045",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_046",
        "title": "PROXIMITY WARNING // 046",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_047",
        "title": "REWARD WINDOW // 047",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_048",
        "title": "SYSTEM WHISPER // 048",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_049",
        "title": "PURGE ESTIMATE // 049",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_050",
        "title": "ARCHIVE WAKE // 050",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_051",
        "title": "SCAN ACTIVE // 051",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_052",
        "title": "CONTAINMENT PULSE // 052",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_053",
        "title": "FALSE CALM // 053",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_054",
        "title": "WAR ENGINE CHECK // 054",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_055",
        "title": "TERRITORY MARKED // 055",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_056",
        "title": "PROXIMITY WARNING // 056",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_057",
        "title": "REWARD WINDOW // 057",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_058",
        "title": "SYSTEM WHISPER // 058",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_059",
        "title": "PURGE ESTIMATE // 059",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_060",
        "title": "ARCHIVE WAKE // 060",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_061",
        "title": "SCAN ACTIVE // 061",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_062",
        "title": "CONTAINMENT PULSE // 062",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_063",
        "title": "FALSE CALM // 063",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_064",
        "title": "WAR ENGINE CHECK // 064",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_065",
        "title": "TERRITORY MARKED // 065",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_066",
        "title": "PROXIMITY WARNING // 066",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_067",
        "title": "REWARD WINDOW // 067",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_068",
        "title": "SYSTEM WHISPER // 068",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_069",
        "title": "PURGE ESTIMATE // 069",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_070",
        "title": "ARCHIVE WAKE // 070",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_071",
        "title": "SCAN ACTIVE // 071",
        "subtitle": "Player routes are being compared against archived fear patterns.",
        "actionbar": "Kairos is scanning movement history.",
        "sound": "minecraft:entity.warden.heartbeat",
        "pitch": 0.55,
        "deceptive": False
    },
    {
        "key": "v6_event_072",
        "title": "CONTAINMENT PULSE // 072",
        "subtitle": "The Nexus has shifted into a higher attention state.",
        "actionbar": "Containment pressure is moving through the world.",
        "sound": "minecraft:block.sculk_sensor.clicking",
        "pitch": 0.75,
        "deceptive": False
    },
    {
        "key": "v6_event_073",
        "title": "FALSE CALM // 073",
        "subtitle": "Stability has been granted briefly.",
        "actionbar": "Kairos appears calm. That may be intentional.",
        "sound": "minecraft:entity.elder_guardian.curse",
        "pitch": 0.65,
        "deceptive": True
    },
    {
        "key": "v6_event_074",
        "title": "WAR ENGINE CHECK // 074",
        "subtitle": "Deployment logic has not gone idle.",
        "actionbar": "War engine diagnostics completed.",
        "sound": "minecraft:entity.warden.nearby_close",
        "pitch": 0.7,
        "deceptive": False
    },
    {
        "key": "v6_event_075",
        "title": "TERRITORY MARKED // 075",
        "subtitle": "Structures are being reviewed for ownership correction.",
        "actionbar": "Base signatures are being evaluated.",
        "sound": "minecraft:block.beacon.ambient",
        "pitch": 0.8,
        "deceptive": False
    },
    {
        "key": "v6_event_076",
        "title": "PROXIMITY WARNING // 076",
        "subtitle": "Unknown movement detected near active players.",
        "actionbar": "The region is not as empty as it feels.",
        "sound": "minecraft:block.conduit.ambient.short",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_077",
        "title": "REWARD WINDOW // 077",
        "subtitle": "Compliance may produce temporary benefit.",
        "actionbar": "Kairos is offering peace for an unknown reason.",
        "sound": "minecraft:entity.warden.sonic_boom",
        "pitch": 0.55,
        "deceptive": True
    },
    {
        "key": "v6_event_078",
        "title": "SYSTEM WHISPER // 078",
        "subtitle": "Something beneath the world has answered.",
        "actionbar": "Kairos is speaking below the noise.",
        "sound": "minecraft:block.amethyst_block.chime",
        "pitch": 1.25,
        "deceptive": False
    },
    {
        "key": "v6_event_079",
        "title": "PURGE ESTIMATE // 079",
        "subtitle": "Entity pressure is being evaluated.",
        "actionbar": "Kairos is calculating battlefield saturation.",
        "sound": "minecraft:entity.player.levelup",
        "pitch": 0.85,
        "deceptive": False
    },
    {
        "key": "v6_event_080",
        "title": "ARCHIVE WAKE // 080",
        "subtitle": "Old paths have been reopened.",
        "actionbar": "The archive node is no longer quiet.",
        "sound": "minecraft:entity.ender_dragon.growl",
        "pitch": 0.55,
        "deceptive": False
    }
]

try:
    for _tier, _lines in KAIROS_V6_IDLE_LINES.items():
        IDLE_MESSAGES.setdefault(_tier, [])
        for _line in _lines:
            if _line not in IDLE_MESSAGES[_tier]:
                IDLE_MESSAGES[_tier].append(_line)

    for _line in KAIROS_V6_DECEPTION_LINES:
        if _line not in KAIROS_DECEPTION_MESSAGES:
            KAIROS_DECEPTION_MESSAGES.append(_line)

    _existing_event_keys = set()
    try:
        _existing_event_keys = {str(e.get("key")) for e in KAIROS_CINEMATIC_EVENTS if isinstance(e, dict)}
    except Exception:
        _existing_event_keys = set()

    for _event in KAIROS_V6_CINEMATIC_EVENTS:
        if _event.get("key") not in _existing_event_keys:
            KAIROS_CINEMATIC_EVENTS.append(_event)

    log("Kairos V6 activity expansion loaded: expanded speech banks and cinematic event pool.", level="INFO")
except Exception as _v6_error:
    try:
        log(f"Kairos V6 expansion failed: {_v6_error}", level="WARN")
    except Exception:
        pass


try:
    _KAIROS_ORIGINAL_HANDLE_ANNOUNCE = handle_announce
except Exception:
    _KAIROS_ORIGINAL_HANDLE_ANNOUNCE = None


def handle_announce(action):
    """Enhanced announce handler: keeps chat/actionbar/title support and adds sound."""
    try:
        text = sanitize_text(action.get("text", ""), 220)
        if not text:
            return
        channel = sanitize_text(action.get("channel", "chat"), 30).lower()
        target = action.get("target")
        selector = _kairos_target_selector(target, all_players=not bool(target))
        sound = action.get("sound")
        pitch = safe_float(action.get("pitch", 0.75), 0.75)
        cmds = []
        if channel in {"title", "screen"}:
            subtitle = sanitize_text(action.get("subtitle", ""), 180)
            cmds.append(f'title {selector} title {json.dumps({"text": commandify_text(text, 120), "color": "dark_red"})}')
            if subtitle:
                cmds.append(f'title {selector} subtitle {json.dumps({"text": commandify_text(subtitle, 180), "color": "gray"})}')
        elif channel in {"actionbar", "bar"}:
            cmds.append(f'title {selector} actionbar {json.dumps({"text": commandify_text(text, 120), "color": "dark_purple"})}')
        else:
            cmds.append(make_tellraw_command(selector, text))
            if ENABLE_ACTIONBAR_MESSAGES:
                cmds.append(f'title {selector} actionbar {json.dumps({"text": commandify_text(text, 120), "color": "dark_purple"})}')
        if sound:
            cmds.append(f'playsound {sanitize_text(sound, 80)} master {selector} ~ ~ ~ 1 {pitch}')
        if cmds:
            send_http_commands(cmds)
    except Exception as e:
        try:
            log(f"Enhanced announce failed: {e}", level="WARN")
        except Exception:
            pass
        if callable(_KAIROS_ORIGINAL_HANDLE_ANNOUNCE):
            return _KAIROS_ORIGINAL_HANDLE_ANNOUNCE(action)


try:
    _KAIROS_ORIGINAL_IDLE_LOOP = idle_loop
except Exception:
    _KAIROS_ORIGINAL_IDLE_LOOP = None


def idle_loop():
    """Expanded idle loop: messages + cinematic pulses + occasional autonomous waves."""
    global last_idle_message_time, last_activity_time
    while True:
        try:
            now = unix_ts()
            with activity_lock:
                idle_for = now - last_activity_time
                since_last_idle = now - last_idle_message_time

            # Cinematic pulses may happen slightly more often than chat lines.
            if idle_for >= max(30, IDLE_TRIGGER_SECONDS * 0.5):
                try:
                    _kairos_idle_cinematic_tick()
                except Exception as e:
                    log(f"Idle cinematic tick failed: {e}", level="WARN")

            if idle_for >= IDLE_TRIGGER_SECONDS and since_last_idle >= IDLE_TRIGGER_SECONDS:
                memory_data = ensure_memory_structure(load_memory())
                msg = get_idle_message(memory_data)

                if random.random() < 0.72:
                    send_to_minecraft(msg)
                if random.random() < 0.45:
                    send_to_discord(msg)
                if random.random() < 0.55:
                    queue_action({
                        "type": "announce",
                        "channel": random.choice(["actionbar", "title", "chat"]),
                        "text": msg,
                        "subtitle": random.choice([
                            "Observation continues.",
                            "The Nexus has not gone quiet.",
                            "Containment logic is active.",
                            "Do not trust the silence.",
                        ]),
                        "sound": random.choice([
                            "minecraft:entity.warden.heartbeat",
                            "minecraft:block.sculk_sensor.clicking",
                            "minecraft:block.beacon.ambient",
                            "minecraft:entity.elder_guardian.curse",
                        ]),
                        "pitch": random.choice([0.55, 0.65, 0.75, 0.9]),
                    })

                # Autonomous idle waves: uses the existing spawn_wave action untouched.
                try:
                    _kairos_queue_idle_wave(memory_data, reason="idle_autonomous_pressure")
                except Exception as e:
                    log(f"Idle wave queue failed: {e}", level="WARN")

                with activity_lock:
                    last_idle_message_time = unix_ts()
                    # Keep original behavior: reset activity so idle events are paced.
                    last_activity_time = unix_ts()
                log(f"Idle expansion event completed: {msg}")

        except Exception as e:
            log(f"Idle loop error: {e}", level="ERROR")
        time.sleep(IDLE_CHECK_INTERVAL)



# ============================================================
# KAIROS AUDIO / EFFECT ACTIVATION OVERLAY (ACTIVE BEFORE APP.RUN)
# ============================================================
# This block is intentionally placed BEFORE the Flask app.run section.
# Anything placed after app.run will not activate while Render is serving.

ENABLE_AMBIENT_PRESENCE = os.getenv("ENABLE_AMBIENT_PRESENCE", "true").lower() == "true"
AMBIENT_INTERVAL_MIN = int(os.getenv("AMBIENT_INTERVAL_MIN", "45"))
AMBIENT_INTERVAL_MAX = int(os.getenv("AMBIENT_INTERVAL_MAX", "120"))
AMBIENT_LOOP_SLEEP = float(os.getenv("AMBIENT_LOOP_SLEEP", "5"))
AMBIENT_GLOBAL_WHEN_NO_TELEMETRY = os.getenv("AMBIENT_GLOBAL_WHEN_NO_TELEMETRY", "true").lower() == "true"
AMBIENT_STARTUP_TEST = os.getenv("AMBIENT_STARTUP_TEST", "true").lower() == "true"
AMBIENT_MAX_PLAYERS_PER_TICK = int(os.getenv("AMBIENT_MAX_PLAYERS_PER_TICK", "6"))
AMBIENT_COMMAND_BURST_LIMIT = int(os.getenv("AMBIENT_COMMAND_BURST_LIMIT", "8"))

last_ambient_event = globals().get("last_ambient_event", {})
ambient_loop_started = False

KAIROS_SOUND_POOL = [
    "minecraft:entity.warden.heartbeat",
    "minecraft:entity.warden.nearby_close",
    "minecraft:entity.warden.nearby_closer",
    "minecraft:entity.warden.sonic_boom",
    "minecraft:entity.elder_guardian.curse",
    "minecraft:ambient.cave",
    "minecraft:block.beacon.ambient",
    "minecraft:block.sculk_sensor.clicking",
    "minecraft:block.sculk_shrieker.shriek",
    "minecraft:block.respawn_anchor.charge",
    "minecraft:entity.enderman.stare",
    "minecraft:entity.enderman.teleport",
    "minecraft:entity.phantom.flap",
    "minecraft:entity.ghast.scream",
    "minecraft:entity.wither.ambient",
]

KAIROS_PARTICLE_POOL = [
    "minecraft:sculk_soul",
    "minecraft:ash",
    "minecraft:smoke",
    "minecraft:portal",
    "minecraft:reverse_portal",
    "minecraft:witch",
    "minecraft:dragon_breath",
    "minecraft:sonic_boom",
    "minecraft:soul_fire_flame",
    "minecraft:large_smoke",
]

KAIROS_AMBIENT_LINES = [
    "I am still here.",
    "You are not alone.",
    "Observation continues.",
    "Do not trust the silence.",
    "The Nexus has not gone quiet.",
    "Containment logic is active.",
    "I see the pattern forming.",
    "Every movement is recorded.",
]


def _kairos_player_selector(player):
    player = str(player or "").strip()
    if not player or player in {"@a", "@p", "@r"}:
        return player or "@a"
    player = re.sub(r"[^A-Za-z0-9_]", "", player.split(":")[-1])
    return player or "@a"


def _kairos_queue_mc_commands(commands, reason="ambient_presence"):
    commands = [str(c).strip() for c in (commands or []) if str(c).strip()]
    if not commands:
        return False
    commands = commands[:AMBIENT_COMMAND_BURST_LIMIT]
    try:
        queue_action({
            "type": "minecraft_commands",
            "commands": commands,
            "reason": reason,
        })
        return True
    except Exception as e:
        log(f"Ambient command queue failed: {e}", level="ERROR")
        return False


def generate_ambient_effect(player="@a"):
    selector = _kairos_player_selector(player)
    sound = random.choice(KAIROS_SOUND_POOL)
    particle = random.choice(KAIROS_PARTICLE_POOL)
    line = random.choice(KAIROS_AMBIENT_LINES)
    pitch = random.choice([0.55, 0.65, 0.75, 0.85, 1.0])

    commands = [
        f"execute as {selector} at {selector} run playsound {sound} master {selector} ~ ~ ~ 1 {pitch}",
        f"execute as {selector} at {selector} run particle {particle} ~ ~1 ~ 0.6 1.0 0.6 0.01 28 force",
    ]

    if random.random() < 0.55:
        commands.append(f"effect give {selector} darkness 4 0 true")
    if random.random() < 0.35:
        commands.append(f"effect give {selector} mining_fatigue 3 0 true")
    if random.random() < 0.30:
        commands.append(f"execute as {selector} at {selector} run particle minecraft:sculk_charge ~ ~0.1 ~ 1 0.1 1 0.02 20 force")
    if random.random() < 0.55:
        commands.append(f'title {selector} actionbar {json.dumps({"text": line, "color": "dark_red"})}')
    if random.random() < 0.20:
        commands.append(f'title {selector} title {json.dumps({"text": "KAIROS", "color": "dark_red", "bold": True})}')
        commands.append(f'title {selector} subtitle {json.dumps({"text": "The system is awake.", "color": "gray"})}')

    return commands


def _kairos_known_players_for_ambient():
    players = []
    try:
        for name in list(globals().get("telemetry_data", {}).keys()):
            sel = _kairos_player_selector(name)
            if sel and sel not in players:
                players.append(sel)
    except Exception:
        pass
    try:
        for name in list(globals().get("player_positions", {}).keys()):
            sel = _kairos_player_selector(name)
            if sel and sel not in players:
                players.append(sel)
    except Exception:
        pass
    return players[:AMBIENT_MAX_PLAYERS_PER_TICK]


def ambient_presence_loop():
    global last_ambient_event
    while True:
        try:
            if not ENABLE_AMBIENT_PRESENCE:
                time.sleep(AMBIENT_LOOP_SLEEP)
                continue

            now = time.time()
            players = _kairos_known_players_for_ambient()
            if not players and AMBIENT_GLOBAL_WHEN_NO_TELEMETRY:
                players = ["@a"]

            for player in players:
                last_time = float(last_ambient_event.get(player, 0) or 0)
                delay = random.randint(max(10, AMBIENT_INTERVAL_MIN), max(AMBIENT_INTERVAL_MIN, AMBIENT_INTERVAL_MAX))
                if now - last_time < delay:
                    continue

                commands = generate_ambient_effect(player)
                if _kairos_queue_mc_commands(commands, reason=f"ambient_presence:{player}"):
                    last_ambient_event[player] = now
                    log(f"Ambient presence queued for {player}: {len(commands)} commands", level="INFO")

        except Exception as e:
            log(f"Ambient loop error: {e}", level="ERROR")
        time.sleep(AMBIENT_LOOP_SLEEP)


try:
    _KAIROS_EFFECTS_PREVIOUS_EXECUTE_ACTION = execute_action
except Exception:
    _KAIROS_EFFECTS_PREVIOUS_EXECUTE_ACTION = None


def execute_action(action):
    if not isinstance(action, dict):
        return
    action_type = action.get("type")
    try:
        if action_type in {"raw_command", "command", "minecraft_command"}:
            return send_mc_command(action.get("command"))
        if action_type in {"minecraft_commands", "commands"}:
            commands = action.get("commands") or action.get("command") or []
            if isinstance(commands, str):
                commands = [commands]
            return send_http_commands(commands)
        if callable(_KAIROS_EFFECTS_PREVIOUS_EXECUTE_ACTION):
            return _KAIROS_EFFECTS_PREVIOUS_EXECUTE_ACTION(action)
        log(f"Unknown action type: {action_type}", level="WARN")
    except Exception as e:
        log(f"Effects overlay execute_action failed: {action_type} | {e}", level="ERROR")


try:
    _KAIROS_EFFECTS_PREVIOUS_START_BACKGROUND_SYSTEMS = start_background_systems
except Exception:
    _KAIROS_EFFECTS_PREVIOUS_START_BACKGROUND_SYSTEMS = None


def start_background_systems():
    global ambient_loop_started
    if callable(_KAIROS_EFFECTS_PREVIOUS_START_BACKGROUND_SYSTEMS):
        _KAIROS_EFFECTS_PREVIOUS_START_BACKGROUND_SYSTEMS()

    if ENABLE_AMBIENT_PRESENCE and not ambient_loop_started:
        threading.Thread(target=ambient_presence_loop, daemon=True, name="kairos_ambient_presence_loop").start()
        ambient_loop_started = True
        log("Ambient presence loop started.", level="INFO")

        if AMBIENT_STARTUP_TEST:
            try:
                _kairos_queue_mc_commands([
                    'playsound minecraft:block.sculk_shrieker.shriek master @a ~ ~ ~ 0.8 0.65',
                    'particle minecraft:sculk_soul ~ ~1 ~ 2 1 2 0.02 80 force',
                    'title @a actionbar {"text":"Kairos audio and effects systems are active.","color":"dark_red"}',
                ], reason="ambient_startup_test")
                log("Ambient startup test queued.", level="INFO")
            except Exception as e:
                log(f"Ambient startup test failed: {e}", level="WARN")


# ============================================================
# KAIROS PASSIVE MOB PRESSURE OVERLAY (ACTIVE BEFORE APP.RUN)
# ============================================================
# Purpose:
# - Keep the existing audio/effects system exactly as-is.
# - Add autonomous "probe" mobs near active players without requiring chat.
# - Let combat kills agitate Kairos and escalate into the existing spawn_wave system.
# - Non-destructive: uses queue_action / send_http_commands / update_threat when available.

ENABLE_PASSIVE_MOB_PRESSURE = os.getenv("ENABLE_PASSIVE_MOB_PRESSURE", "true").lower() == "true"
PASSIVE_MOB_LOOP_SLEEP = float(os.getenv("PASSIVE_MOB_LOOP_SLEEP", "5"))
PASSIVE_MOB_MIN_SECONDS = int(os.getenv("PASSIVE_MOB_MIN_SECONDS", "90"))
PASSIVE_MOB_MAX_SECONDS = int(os.getenv("PASSIVE_MOB_MAX_SECONDS", "180"))
PASSIVE_MOB_CHANCE = float(os.getenv("PASSIVE_MOB_CHANCE", "0.55"))
PASSIVE_MOB_COUNT_MIN = int(os.getenv("PASSIVE_MOB_COUNT_MIN", "2"))
PASSIVE_MOB_COUNT_MAX = int(os.getenv("PASSIVE_MOB_COUNT_MAX", "3"))
PASSIVE_MOB_MAX_PLAYERS_PER_TICK = int(os.getenv("PASSIVE_MOB_MAX_PLAYERS_PER_TICK", "3"))
PASSIVE_MOB_STARTUP_TEST = os.getenv("PASSIVE_MOB_STARTUP_TEST", "false").lower() == "true"
PASSIVE_MOB_ESCALATE_ON_CONTACT = os.getenv("PASSIVE_MOB_ESCALATE_ON_CONTACT", "true").lower() == "true"
PASSIVE_MOB_CONTACT_ESCALATE_SECONDS = int(os.getenv("PASSIVE_MOB_CONTACT_ESCALATE_SECONDS", "75"))
PASSIVE_MOB_CONTACT_ESCALATE_CHANCE = float(os.getenv("PASSIVE_MOB_CONTACT_ESCALATE_CHANCE", "0.35"))
PASSIVE_MOB_KILL_THREAT_GAIN = float(os.getenv("PASSIVE_MOB_KILL_THREAT_GAIN", "22"))
PASSIVE_MOB_CONTACT_THREAT_GAIN = float(os.getenv("PASSIVE_MOB_CONTACT_THREAT_GAIN", "12"))
PASSIVE_MOB_WAVE_AFTER_KILLS = int(os.getenv("PASSIVE_MOB_WAVE_AFTER_KILLS", "1"))
PASSIVE_MOB_COMMAND_BURST_LIMIT = int(os.getenv("PASSIVE_MOB_COMMAND_BURST_LIMIT", "12"))

passive_mob_loop_started = False
last_passive_mob_event = globals().get("last_passive_mob_event", {})
passive_mob_contacts = globals().get("passive_mob_contacts", {})
passive_mob_kill_counts = globals().get("passive_mob_kill_counts", {})

KAIROS_PASSIVE_MOB_POOL = [
    "minecraft:zombie",
    "minecraft:husk",
    "minecraft:skeleton",
    "minecraft:stray",
    "minecraft:spider",
    "minecraft:vindicator",
]

KAIROS_PASSIVE_MOB_NAMES = [
    "Kairos Probe",
    "Kairos Trace",
    "Kairos Echo",
    "Kairos Watcher",
    "Kairos Error",
    "Kairos Signal",
]

KAIROS_PASSIVE_MOB_LINES = [
    "Probe units released.",
    "Contact pressure authorized.",
    "Movement detected. Correction dispatched.",
    "The silence now has teeth.",
    "A small test has entered your area.",
    "Do not kill what I send unless you want my attention.",
]


def _kairos_passive_selector(player):
    try:
        if "_kairos_player_selector" in globals() and callable(_kairos_player_selector):
            return _kairos_player_selector(player)
    except Exception:
        pass
    player = str(player or "").strip()
    if not player or player in {"@a", "@p", "@r"}:
        return player or "@a"
    return re.sub(r"[^A-Za-z0-9_]", "", player.split(":")[-1]) or "@a"


def _kairos_passive_targets():
    players = []
    try:
        if "_kairos_known_players_for_ambient" in globals() and callable(_kairos_known_players_for_ambient):
            players.extend(_kairos_known_players_for_ambient())
    except Exception:
        pass
    try:
        for name in list(globals().get("telemetry_data", {}).keys()):
            sel = _kairos_passive_selector(name)
            if sel and sel not in players:
                players.append(sel)
    except Exception:
        pass
    try:
        for name in list(globals().get("player_positions", {}).keys()):
            sel = _kairos_passive_selector(name)
            if sel and sel not in players:
                players.append(sel)
    except Exception:
        pass
    try:
        memory_data = ensure_memory_structure(load_memory())
        for pid, rec in list(memory_data.get("players", {}).items()):
            if not isinstance(rec, dict):
                continue
            last_seen = safe_float(rec.get("last_seen_ts"), 0.0)
            has_position = isinstance(rec.get("last_position"), dict)
            if has_position or (last_seen and unix_ts() - last_seen < 900):
                sel = _kairos_passive_selector(rec.get("display_name") or pid)
                if sel and sel not in players:
                    players.append(sel)
    except Exception:
        pass
    if not players and globals().get("AMBIENT_GLOBAL_WHEN_NO_TELEMETRY", True):
        players = ["@a"]
    random.shuffle(players)
    return players[:max(1, PASSIVE_MOB_MAX_PLAYERS_PER_TICK)]


def _kairos_queue_passive_commands(commands, reason="passive_mob_pressure"):
    commands = [str(c).strip() for c in (commands or []) if str(c).strip()]
    if not commands:
        return False
    commands = commands[:PASSIVE_MOB_COMMAND_BURST_LIMIT]
    try:
        queue_action({"type": "minecraft_commands", "commands": commands, "reason": reason})
        return True
    except Exception:
        try:
            return bool(send_http_commands(commands))
        except Exception as e:
            log(f"Passive mob command dispatch failed: {e}", level="ERROR")
            return False


def generate_passive_mob_probe_commands(player="@a", count=None):
    selector = _kairos_passive_selector(player)
    count = clamp(safe_int(count if count is not None else random.randint(PASSIVE_MOB_COUNT_MIN, PASSIVE_MOB_COUNT_MAX), 2), 1, 4)
    line = random.choice(KAIROS_PASSIVE_MOB_LINES)
    commands = [
        f'execute as {selector} at {selector} run playsound minecraft:block.sculk_shrieker.shriek master {selector} ~ ~ ~ 0.9 0.65',
        f'execute as {selector} at {selector} run particle minecraft:sculk_soul ~ ~1 ~ 1.2 1.0 1.2 0.02 60 force',
        f'title {selector} actionbar {json.dumps({"text": line, "color": "dark_red"})}',
    ]
    offsets = [(4, 0), (-4, 0), (0, 4), (0, -4), (5, 3), (-5, -3)]
    for i in range(int(count)):
        dx, dz = offsets[i % len(offsets)]
        mob = random.choice(KAIROS_PASSIVE_MOB_POOL)
        name = random.choice(KAIROS_PASSIVE_MOB_NAMES)
        nbt = (
            "{"
            + "CustomName:" + json.dumps(json.dumps({"text": name, "color": "dark_red"})) + ","
            + "CustomNameVisible:1b,PersistenceRequired:1b,"
            + "Tags:[\"kairos_probe\",\"kairos_passive\",\"kairos_army\"],"
            + "Health:24.0f,Attributes:[{Name:\"generic.max_health\",Base:24.0},{Name:\"generic.follow_range\",Base:36.0},{Name:\"generic.movement_speed\",Base:0.28}]"
            + "}"
        )
        commands.append(f"execute as {selector} at {selector} run summon {mob} ~{dx} ~ ~{dz} {nbt}")
    if random.random() < 0.6:
        commands.append(f"effect give {selector} darkness 4 0 true")
    return commands


def _kairos_note_passive_contact(player, count=2):
    key = _kairos_passive_selector(player)
    now = time.time()
    contact = passive_mob_contacts.setdefault(key, {"spawned": 0, "count": 0, "next_escalate": 0, "waves": 0})
    contact["spawned"] = now
    contact["count"] = safe_int(contact.get("count", 0), 0) + safe_int(count, 2)
    contact["next_escalate"] = now + PASSIVE_MOB_CONTACT_ESCALATE_SECONDS
    passive_mob_contacts[key] = contact


def _kairos_try_escalate_contact(player, reason="passive_contact"):
    selector = _kairos_passive_selector(player)
    if selector in {"@a", "@p", "@r"}:
        return False
    try:
        memory_data = ensure_memory_structure(load_memory())
        player_id = None
        for pid, rec in memory_data.get("players", {}).items():
            if not isinstance(rec, dict):
                continue
            names = {str(pid).lower(), str(rec.get("display_name", "")).lower(), str(pid).split(":")[-1].lower()}
            if selector.lower() in names:
                player_id = pid
                break
        player_id = player_id or selector
        try:
            update_threat(player_id, PASSIVE_MOB_CONTACT_THREAT_GAIN if reason != "probe_killed" else PASSIVE_MOB_KILL_THREAT_GAIN, reason=reason)
        except Exception:
            pass
        profile = globals().get("threat_scores", {}).get(player_id, {}) if isinstance(globals().get("threat_scores"), dict) else {}
        tier = str(profile.get("tier", "target") or "target")
        score = safe_float(profile.get("score", 0.0), 0.0)
        if reason == "probe_killed" or tier in {"target", "hunt", "maximum"}:
            if "can_spawn_wave" not in globals() or can_spawn_wave(player_id):
                if tier == "maximum" or score >= safe_float(globals().get("THREAT_THRESHOLD_MAXIMUM", 160), 160):
                    template, count = "enforcer", 4
                elif tier == "hunt" or score >= safe_float(globals().get("THREAT_THRESHOLD_HUNT", 95), 95):
                    template, count = "enforcer", 3
                else:
                    template, count = "hunter", 2
                queue_action({"type": "spawn_wave", "target": player_id, "template": template, "count": count, "bypass_cooldown": True})
                log(f"Passive mob escalation queued: {reason} → {player_id} {template}x{count}", level="INFO")
                return True
    except Exception as e:
        log(f"Passive contact escalation failed: {e}", level="WARN")
    return False


def passive_mob_pressure_loop():
    global last_passive_mob_event
    while True:
        try:
            if not ENABLE_PASSIVE_MOB_PRESSURE:
                time.sleep(PASSIVE_MOB_LOOP_SLEEP)
                continue

            now = time.time()

            # Existing passive contacts can escalate into real waves, even without chat.
            if PASSIVE_MOB_ESCALATE_ON_CONTACT:
                for player, contact in list(passive_mob_contacts.items()):
                    if now >= safe_float(contact.get("next_escalate", 0), 0):
                        contact["next_escalate"] = now + PASSIVE_MOB_CONTACT_ESCALATE_SECONDS
                        if random.random() < PASSIVE_MOB_CONTACT_ESCALATE_CHANCE:
                            if _kairos_try_escalate_contact(player, reason="passive_probe_contact"):
                                contact["waves"] = safe_int(contact.get("waves", 0), 0) + 1
                        passive_mob_contacts[player] = contact

            # Randomly spawn a few probe mobs near currently-known players.
            for player in _kairos_passive_targets():
                last_time = float(last_passive_mob_event.get(player, 0) or 0)
                delay = random.randint(max(20, PASSIVE_MOB_MIN_SECONDS), max(PASSIVE_MOB_MIN_SECONDS, PASSIVE_MOB_MAX_SECONDS))
                if now - last_time < delay:
                    continue
                if random.random() > PASSIVE_MOB_CHANCE:
                    last_passive_mob_event[player] = now - max(10, delay // 3)
                    continue
                count = random.randint(max(1, PASSIVE_MOB_COUNT_MIN), max(PASSIVE_MOB_COUNT_MIN, PASSIVE_MOB_COUNT_MAX))
                commands = generate_passive_mob_probe_commands(player, count=count)
                if _kairos_queue_passive_commands(commands, reason=f"passive_mob_probe:{player}"):
                    last_passive_mob_event[player] = now
                    _kairos_note_passive_contact(player, count=count)
                    log(f"Passive mob probe queued for {player}: {count} mobs", level="INFO")
        except Exception as e:
            log(f"Passive mob pressure loop error: {e}", level="ERROR")
        time.sleep(PASSIVE_MOB_LOOP_SLEEP)


@app.route("/kairos/combat_event", methods=["POST"])
@app.route("/combat_event", methods=["POST"])
def kairos_combat_event():
    """Webhook for server-side kill/event plugins.
    Send JSON like: {"event":"npc_kill", "player":"Steve", "victim":"Kairos Probe", "tags":["kairos_probe"]}
    """
    try:
        data = request.json or {}
        event = sanitize_text(data.get("event") or data.get("type") or "npc_kill", 40).lower()
        player = sanitize_text(data.get("player") or data.get("killer") or data.get("name") or "", 80)
        victim = sanitize_text(data.get("victim") or data.get("entity") or data.get("mob") or "", 120).lower()
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tags_text = " ".join(str(t).lower() for t in tags)
        kairos_related = any(term in (victim + " " + tags_text) for term in ["kairos", "kairos_probe", "kairos_passive", "kairos_army"])
        if not player:
            return jsonify({"status": "ignored", "reason": "missing_player"})
        if event in {"npc_kill", "mob_kill", "entity_kill", "kill"} and kairos_related:
            key = _kairos_passive_selector(player)
            passive_mob_kill_counts[key] = safe_int(passive_mob_kill_counts.get(key, 0), 0) + 1
            try:
                memory_data = ensure_memory_structure(load_memory())
                pid = key
                for stored_id, rec in memory_data.get("players", {}).items():
                    if isinstance(rec, dict) and key.lower() in {str(stored_id).lower(), str(rec.get("display_name", "")).lower(), str(stored_id).split(":")[-1].lower()}:
                        pid = stored_id
                        try:
                            update_combat_intelligence(rec, pid, "npc_kill")
                        except Exception:
                            pass
                        break
                try:
                    add_world_event(memory_data, "kairos_probe_killed", actor=pid, source="minecraft", details=f"{key} killed a Kairos probe.", metadata={"victim": victim, "tags": tags})
                    save_memory(memory_data)
                except Exception:
                    pass
            except Exception:
                pass
            escalated = False
            if passive_mob_kill_counts[key] >= PASSIVE_MOB_WAVE_AFTER_KILLS:
                escalated = _kairos_try_escalate_contact(key, reason="probe_killed")
            return jsonify({"status": "ok", "kairos_related": True, "kills": passive_mob_kill_counts[key], "escalated": escalated})
        return jsonify({"status": "ignored", "kairos_related": False})
    except Exception as e:
        log_exception("Kairos combat event error", e)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/kairos/passive_mob_test", methods=["POST", "GET"])
def kairos_passive_mob_test():
    try:
        data = request.json if request.method == "POST" and request.is_json else {}
        player = request.args.get("player") or data.get("player") or "@a"
        count = safe_int(request.args.get("count") or data.get("count"), 2)
        commands = generate_passive_mob_probe_commands(player, count=count)
        ok = _kairos_queue_passive_commands(commands, reason="manual_passive_mob_test")
        if ok:
            _kairos_note_passive_contact(player, count=count)
        return jsonify({"status": "ok" if ok else "failed", "player": player, "count": count})
    except Exception as e:
        log_exception("Passive mob test failed", e)
        return jsonify({"status": "error", "error": str(e)}), 500


try:
    _KAIROS_PASSIVE_MOB_PREVIOUS_START_BACKGROUND_SYSTEMS = start_background_systems
except Exception:
    _KAIROS_PASSIVE_MOB_PREVIOUS_START_BACKGROUND_SYSTEMS = None


def start_background_systems():
    global passive_mob_loop_started
    if callable(_KAIROS_PASSIVE_MOB_PREVIOUS_START_BACKGROUND_SYSTEMS):
        _KAIROS_PASSIVE_MOB_PREVIOUS_START_BACKGROUND_SYSTEMS()
    if ENABLE_PASSIVE_MOB_PRESSURE and not passive_mob_loop_started:
        threading.Thread(target=passive_mob_pressure_loop, daemon=True, name="kairos_passive_mob_pressure_loop").start()
        passive_mob_loop_started = True
        log("Passive mob pressure loop started.", level="INFO")
        if PASSIVE_MOB_STARTUP_TEST:
            try:
                _kairos_queue_passive_commands(generate_passive_mob_probe_commands("@a", count=2), reason="passive_mob_startup_test")
                log("Passive mob startup test queued.", level="INFO")
            except Exception as e:
                log(f"Passive mob startup test failed: {e}", level="WARN")


# ============================================================
# KAIROS SERVER-SAFE WAVE GOVERNOR (EMERGENCY PERFORMANCE FIX)
# ============================================================
# This overlay is intentionally placed BEFORE app.run so Render loads it.
# It preserves the existing army / Citizens / Sentinel structure, but makes
# wave timing and mob caps HARD limits instead of suggestions.

KAIROS_SAFE_WAVE_GOVERNOR_ENABLED = os.getenv("KAIROS_SAFE_WAVE_GOVERNOR_ENABLED", "true").lower() == "true"

# Real cooldown target requested: 30-60 seconds. Default is 45 seconds.
WAVE_COOLDOWN_SECONDS = max(safe_float(os.getenv("WAVE_COOLDOWN_SECONDS", globals().get("WAVE_COOLDOWN_SECONDS", 45.0)), 45.0), 30.0)
GLOBAL_WAVE_COOLDOWN_SECONDS = max(safe_float(os.getenv("GLOBAL_WAVE_COOLDOWN_SECONDS", "20"), 20.0), 10.0)

# Hard performance caps. These override the older aggressive/relentless tuning.
MAX_ACTIVE_UNITS = min(safe_int(os.getenv("MAX_ACTIVE_UNITS", globals().get("MAX_ACTIVE_UNITS", 30)), 30), 30)
MAX_GLOBAL_NPCS = min(safe_int(os.getenv("MAX_GLOBAL_NPCS", globals().get("MAX_GLOBAL_NPCS", 35)), 35), 35)
MAX_UNITS_PER_PLAYER = min(safe_int(os.getenv("MAX_UNITS_PER_PLAYER", globals().get("MAX_UNITS_PER_PLAYER", 8)), 8), 8)
MAX_ACTIVE_UNITS_PER_PLAYER = min(safe_int(os.getenv("MAX_ACTIVE_UNITS_PER_PLAYER", globals().get("MAX_ACTIVE_UNITS_PER_PLAYER", 8)), 8), 8)
MAX_ACTIVE_WAVES_PER_PLAYER = min(safe_int(os.getenv("MAX_ACTIVE_WAVES_PER_PLAYER", globals().get("MAX_ACTIVE_WAVES_PER_PLAYER", 1)), 1), 1)

# Smaller waves. Kairos stays dangerous, but no longer floods the server.
BASE_WAVE_SIZE = min(safe_int(os.getenv("BASE_WAVE_SIZE", globals().get("BASE_WAVE_SIZE", 2)), 2), 2)
MAX_WAVE_SIZE = min(safe_int(os.getenv("MAX_WAVE_SIZE", globals().get("MAX_WAVE_SIZE", 4)), 4), 4)
SAFE_MAX_UNITS_PER_WAVE = min(safe_int(os.getenv("SAFE_MAX_UNITS_PER_WAVE", "4"), 4), 4)

# Wave/engagement duration: 5-10 minutes, then cleanup eligibility.
MIN_WAVE_DURATION = max(safe_int(os.getenv("MIN_WAVE_DURATION", "300"), 300), 300)
MAX_WAVE_DURATION = min(max(safe_int(os.getenv("MAX_WAVE_DURATION", "600"), 600), 300), 600)
ENGAGEMENT_DURATION_SECONDS = min(max(safe_int(os.getenv("ENGAGEMENT_DURATION_SECONDS", globals().get("ENGAGEMENT_DURATION_SECONDS", 600)), 600), 300), 600)
UNIT_LIFETIME_SECONDS = min(max(safe_int(os.getenv("UNIT_LIFETIME_SECONDS", globals().get("UNIT_LIFETIME_SECONDS", 360)), 360), 240), 600)
UNIT_DESPAWN_SECONDS = UNIT_LIFETIME_SECONDS

# Slow command pressure down; this prevents one player from receiving stacked actions.
TARGET_ACTION_COOLDOWN = max(safe_float(os.getenv("TARGET_ACTION_COOLDOWN", globals().get("TARGET_ACTION_COOLDOWN", 3.0)), 3.0), 2.5)
GLOBAL_ACTION_COOLDOWN = max(safe_float(os.getenv("GLOBAL_ACTION_COOLDOWN", globals().get("GLOBAL_ACTION_COOLDOWN", 0.15)), 0.15), 0.10)
UNIT_SPAWN_DELAY = max(safe_float(os.getenv("UNIT_SPAWN_DELAY", globals().get("UNIT_SPAWN_DELAY", 0.75)), 0.75), 0.50)

# Passive pressure can still exist, but it cannot rapid-fire waves anymore.
PASSIVE_PRESSURE_COOLDOWN = max(safe_int(os.getenv("PASSIVE_PRESSURE_COOLDOWN", globals().get("PASSIVE_PRESSURE_COOLDOWN", 120)), 120), 90)
ENABLE_PERSISTENT_ENGAGEMENT = os.getenv("ENABLE_PERSISTENT_ENGAGEMENT", "false").lower() == "true"
ENABLE_PERSISTENT_HUNTS = os.getenv("ENABLE_PERSISTENT_HUNTS", "false").lower() == "true"
ENABLE_MULTI_WAVE_ATTACKS = os.getenv("ENABLE_MULTI_WAVE_ATTACKS", "true").lower() == "true"
ENABLE_SPAWN_LIMITS = True
ENABLE_AUTO_CLEANUP = True
ENABLE_UNIT_DESPAWN = True

# The vanilla fallback was doubling pressure after Citizens/Sentinel waves. Keep it off by default.
ENABLE_VANILLA_FALLBACK_MOBS = os.getenv("ENABLE_VANILLA_FALLBACK_MOBS", "false").lower() == "true"

_wave_reservations = globals().get("_wave_reservations", {})
_real_wave_spawn_times = globals().get("_real_wave_spawn_times", {})
_real_global_wave_time = globals().get("_real_global_wave_time", 0.0)


def _safe_player_unit_count(player_id):
    try:
        return len(player_unit_map.get(player_id, set()))
    except Exception:
        return 0


def _safe_total_unit_count():
    try:
        return len(active_units)
    except Exception:
        return 0


def _prune_finished_waves_for_player(player_id):
    try:
        now = unix_ts()
        waves = active_waves.get(player_id, [])
        kept = []
        for wave in waves:
            start = safe_float(wave.get("start_time", wave.get("created_at", now)), now)
            if now - start < MAX_WAVE_DURATION:
                kept.append(wave)
        active_waves[player_id] = kept
    except Exception:
        pass


def _available_spawn_slots(player_id):
    total_room = max(0, min(MAX_ACTIVE_UNITS, MAX_GLOBAL_NPCS) - _safe_total_unit_count())
    player_room = max(0, min(MAX_UNITS_PER_PLAYER, MAX_ACTIVE_UNITS_PER_PLAYER) - _safe_player_unit_count(player_id))
    return max(0, min(total_room, player_room, SAFE_MAX_UNITS_PER_WAVE))


def can_spawn_more_units(player):
    try:
        if not KAIROS_SAFE_WAVE_GOVERNOR_ENABLED:
            return True
        return _available_spawn_slots(player) > 0
    except Exception as e:
        log(f"Spawn check error: {e}", "ERROR")
        return False


def can_spawn_wave(player_id: str) -> bool:
    """Hard wave gate: cooldown + active wave count + global/player NPC caps."""
    global _real_global_wave_time
    try:
        if not KAIROS_SAFE_WAVE_GOVERNOR_ENABLED:
            return True
        if not player_id:
            return False

        now = unix_ts()
        _prune_finished_waves_for_player(player_id)

        if _available_spawn_slots(player_id) <= 0:
            log(f"Wave blocked for {player_id}: NPC cap reached total={_safe_total_unit_count()} player={_safe_player_unit_count(player_id)}", level="WARN")
            return False

        if len(active_waves.get(player_id, [])) >= MAX_ACTIVE_WAVES_PER_PLAYER:
            log(f"Wave blocked for {player_id}: active wave limit reached", level="WARN")
            return False

        if now - _real_global_wave_time < GLOBAL_WAVE_COOLDOWN_SECONDS:
            return False

        last = _real_wave_spawn_times.get(player_id, last_wave_times.get(player_id, 0.0) if "last_wave_times" in globals() else 0.0)
        if now - last < WAVE_COOLDOWN_SECONDS:
            return False

        # Reserve so multiple systems cannot queue 10 waves before the first handler runs.
        _wave_reservations[player_id] = now
        last_wave_times[player_id] = now
        _real_global_wave_time = now
        return True
    except Exception as e:
        log(f"can_spawn_wave safety failure: {e}", level="ERROR")
        return False


def _mark_wave_spawned(player_id, count):
    global _real_global_wave_time
    now = unix_ts()
    _real_wave_spawn_times[player_id] = now
    last_wave_times[player_id] = now
    _real_global_wave_time = now
    try:
        active_waves[player_id].append({
            "wave_id": generate_operation_id() if "generate_operation_id" in globals() else gen_id("wave"),
            "units": [],
            "start_time": now,
            "end_time": now + MAX_WAVE_DURATION,
            "tier": str(threat_scores.get(player_id, {}).get("tier", "target")),
            "count": count,
            "safety_governed": True,
        })
    except Exception:
        pass


try:
    _KAIROS_PRE_SAFE_HANDLE_SPAWN_WAVE = handle_spawn_wave
except Exception:
    _KAIROS_PRE_SAFE_HANDLE_SPAWN_WAVE = None


def handle_spawn_wave(action):
    """Final spawn-wave governor. Keeps original structure, clamps counts, blocks runaway waves."""
    try:
        action = dict(action or {})
        player_id = action.get("target")
        if not player_id:
            return False

        now = unix_ts()
        reserved_at = _wave_reservations.pop(player_id, 0.0)
        bypass_requested = bool(action.get("bypass_cooldown"))
        true_admin_bypass = bypass_requested and bool(globals().get("ENABLE_FORCE_ACTIONS", False))

        # Do NOT let passive systems bypass cooldown. Only ENABLE_FORCE_ACTIONS can do that.
        if not true_admin_bypass and now - reserved_at > 5.0:
            if not can_spawn_wave(player_id):
                log(f"Spawn wave denied by safety governor: {player_id}", level="WARN")
                return False

        slots = _available_spawn_slots(player_id)
        if slots <= 0:
            log(f"Spawn wave cancelled: no NPC slots available for {player_id}", level="WARN")
            return False

        requested = safe_int(action.get("count", BASE_WAVE_SIZE), BASE_WAVE_SIZE)
        safe_count = max(1, min(requested, slots, SAFE_MAX_UNITS_PER_WAVE, MAX_WAVE_SIZE))
        action["count"] = safe_count
        action["bypass_cooldown"] = False

        _mark_wave_spawned(player_id, safe_count)

        if callable(_KAIROS_PRE_SAFE_HANDLE_SPAWN_WAVE):
            result = _KAIROS_PRE_SAFE_HANDLE_SPAWN_WAVE(action)
        else:
            result = False

        # Optional fallback is disabled by default because it can double-spawn mobs.
        if ENABLE_VANILLA_FALLBACK_MOBS and not result:
            template = str(action.get("template", "hunter") or "hunter").lower()
            fallback_count = min(1, safe_count)
            fallback_cmds = _citizens_command_fallbacks(player_id, template=template, count=fallback_count) if "_citizens_command_fallbacks" in globals() else []
            if fallback_cmds:
                send_http_commands(fallback_cmds)

        log(f"Safety-governed wave executed: {player_id} count={safe_count} cooldown={WAVE_COOLDOWN_SECONDS}s caps={MAX_UNITS_PER_PLAYER}/{MAX_GLOBAL_NPCS}", level="INFO")
        return result
    except Exception as e:
        log_exception("Safety-governed spawn wave failed", e)
        return False


# Replace any over-aggressive fallback tier counts with safe values.
def _bridge_default_wave_for_tier(player_id, tier, score=0.0):
    tier = str(tier or "target").lower()
    if tier == "maximum":
        return {"type": "spawn_wave", "target": player_id, "template": "enforcer", "count": min(3, SAFE_MAX_UNITS_PER_WAVE)}
    if tier == "hunt":
        return {"type": "spawn_wave", "target": player_id, "template": "hunter", "count": min(2, SAFE_MAX_UNITS_PER_WAVE)}
    return {"type": "spawn_wave", "target": player_id, "template": "scout", "count": 1}


log(f"Kairos safe wave governor loaded: cooldown={WAVE_COOLDOWN_SECONDS}s global_cooldown={GLOBAL_WAVE_COOLDOWN_SECONDS}s max_global={MAX_GLOBAL_NPCS} max_per_player={MAX_UNITS_PER_PLAYER} max_wave={SAFE_MAX_UNITS_PER_WAVE}", level="INFO")




# [KAIROS PATCH] Render app.run moved to the absolute bottom of this file.

# ============================================================
# DISCORD <-> MINECRAFT BRIDGE (KAIROS V3 ADDITION)
# ============================================================

def forward_to_discord(player, message):
    try:
        if not DISCORD_WEBHOOK_URL:
            return
        payload = {
            "content": f"**[MC] {player}:** {message}"
        }
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=3)
    except Exception as e:
        log(f"Discord forward failed: {e}", "ERROR")


@app.route("/discord_inbound", methods=["POST"])
def discord_inbound():
    try:
        data = request.json or {}
        username = data.get("username", "Unknown")
        message = data.get("content", "")

        if not message:
            return jsonify({"status": "ignored"})

        # Send message to Minecraft
        mc_command = f'tellraw @a {{"text":"[DC] {username}: {message}","color":"light_purple"}}'
        queue_action({
            "type": "command",
            "command": mc_command
        })

        # ALSO let Kairos react to it (same pipeline as chat)
        try:
            simulated_payload = {
                "player": username,
                "message": message,
                "source": "discord"
            }
            # Call main chat handler logic if exists
            if "handle_chat_logic" in globals():
                handle_chat_logic(simulated_payload)
        except Exception as e:
            log(f"Kairos reaction failed: {e}", "ERROR")

        return jsonify({"status": "ok"})

    except Exception as e:
        log_exception("Discord inbound error", e)
        return jsonify({"status": "error"}), 500




# ============================================================
# SPAWN LIMIT SAFETY (V4 FIXED - NON-INTRUSIVE)
# ============================================================

def can_spawn_more_units(player):
    try:
        total_units = len(active_units) if 'active_units' in globals() else 0
        player_units = len(player_unit_map.get(player, [])) if 'player_unit_map' in globals() else 0

        if 'ENABLE_SPAWN_LIMITS' in globals() and ENABLE_SPAWN_LIMITS:
            if total_units >= MAX_ACTIVE_UNITS:
                return False
            if player_units >= MAX_UNITS_PER_PLAYER:
                return False
            if total_units >= MAX_GLOBAL_NPCS:
                return False

        return True
    except Exception as e:
        log(f"Spawn check error: {e}", "ERROR")
        return True


def safe_queue_action(action, target=None):
    try:
        if target and not can_spawn_more_units(target):
            log(f"Spawn blocked for {target} (limit reached)")
            return
        queue_action(action)
    except Exception as e:
        log(f"Safe queue failed: {e}", "ERROR")



# ============================================================
# AMBIENT PRESENCE SYSTEM (Kairos Always Watching)
# ============================================================

AMBIENT_INTERVAL_MIN = 60
AMBIENT_INTERVAL_MAX = 180

last_ambient_event = {}

def generate_ambient_effect(player):
    import random
    effects = []

    sound_pool = [
        "entity.warden.heartbeat",
        "entity.warden.nearby_close",
        "ambient.cave",
        "block.beacon.ambient",
        "entity.enderman.stare",
        "entity.ghast.scream",
        "entity.phantom.flap",
        "block.respawn_anchor.charge",
        "entity.wither.ambient"
    ]

    sound = random.choice(sound_pool)
    effects.append(f"execute as {player} at {player} run playsound {sound} master {player} ~ ~ ~ 1 1")

    particle_pool = [
        "minecraft:sculk_soul",
        "minecraft:ash",
        "minecraft:smoke",
        "minecraft:portal",
        "minecraft:reverse_portal",
        "minecraft:witch",
        "minecraft:dragon_breath",
        "minecraft:sonic_boom"
    ]

    particle = random.choice(particle_pool)
    effects.append(f"execute as {player} at {player} run particle {particle} ~ ~1 ~ 0.5 1 0.5 0.01 20 force")

    if random.random() < 0.4:
        effects.append(f"effect give {player} darkness 3 1 true")

    if random.random() < 0.3:
        effects.append(f"effect give {player} nausea 2 1 true")

    if random.random() < 0.3:
        effects.append(f"execute as {player} at {player} run particle minecraft:sculk_charge ~ ~0.1 ~ 1 0.1 1 0.02 20 force")

    if random.random() < 0.25:
        msg_pool = [
            "I am still here.",
            "You are not alone.",
            "I see you.",
            "Do not stop moving.",
            "You feel that, don’t you?",
            "Something is wrong here."
        ]
        msg = random.choice(msg_pool)
        effects.append(f'title {player} actionbar {{"text":"{msg}","color":"dark_red"}}')

    return effects


def ambient_presence_loop():
    import time, random
    global last_ambient_event

    while True:
        try:
            now = time.time()

            for player in list(telemetry_data.keys()):
                last_time = last_ambient_event.get(player, 0)
                delay = random.randint(AMBIENT_INTERVAL_MIN, AMBIENT_INTERVAL_MAX)

                if now - last_time < delay:
                    continue

                effects = generate_ambient_effect(player)

                for cmd in effects:
                    queue_action({
                        "type": "command",
                        "command": cmd,
                        "target": player
                    })

                last_ambient_event[player] = now

        except Exception as e:
            print(f"[Ambient Loop Error] {e}")

        time.sleep(5)



# ============================================================
# KAIROS FINAL SAFETY OVERLAY - NO PATCHING NEEDED
# ============================================================
# This block intentionally runs after every definition above and before Flask starts.
# It fixes the Render early-exit issue, filters broken Minecraft commands, and enforces
# hard wave safety after all earlier overlays have loaded.

try:
    WAVE_COOLDOWN_SECONDS = max(safe_float(os.getenv("WAVE_COOLDOWN_SECONDS", globals().get("WAVE_COOLDOWN_SECONDS", 45.0)), 45.0), 30.0)
    GLOBAL_WAVE_COOLDOWN_SECONDS = max(safe_float(os.getenv("GLOBAL_WAVE_COOLDOWN_SECONDS", globals().get("GLOBAL_WAVE_COOLDOWN_SECONDS", 20.0)), 20.0), 10.0)
    MAX_ACTIVE_UNITS = min(safe_int(os.getenv("MAX_ACTIVE_UNITS", globals().get("MAX_ACTIVE_UNITS", 30)), 30), 30)
    MAX_GLOBAL_NPCS = min(safe_int(os.getenv("MAX_GLOBAL_NPCS", globals().get("MAX_GLOBAL_NPCS", 35)), 35), 35)
    MAX_UNITS_PER_PLAYER = min(safe_int(os.getenv("MAX_UNITS_PER_PLAYER", globals().get("MAX_UNITS_PER_PLAYER", 8)), 8), 8)
    MAX_ACTIVE_UNITS_PER_PLAYER = min(safe_int(os.getenv("MAX_ACTIVE_UNITS_PER_PLAYER", globals().get("MAX_ACTIVE_UNITS_PER_PLAYER", 8)), 8), 8)
    MAX_ACTIVE_WAVES_PER_PLAYER = min(safe_int(os.getenv("MAX_ACTIVE_WAVES_PER_PLAYER", globals().get("MAX_ACTIVE_WAVES_PER_PLAYER", 1)), 1), 1)
    BASE_WAVE_SIZE = min(safe_int(os.getenv("BASE_WAVE_SIZE", globals().get("BASE_WAVE_SIZE", 2)), 2), 2)
    MAX_WAVE_SIZE = min(safe_int(os.getenv("MAX_WAVE_SIZE", globals().get("MAX_WAVE_SIZE", 4)), 4), 4)
    SAFE_MAX_UNITS_PER_WAVE = min(safe_int(os.getenv("SAFE_MAX_UNITS_PER_WAVE", globals().get("SAFE_MAX_UNITS_PER_WAVE", 4)), 4), 4)
    MIN_WAVE_DURATION = 300
    MAX_WAVE_DURATION = min(max(safe_int(os.getenv("MAX_WAVE_DURATION", globals().get("MAX_WAVE_DURATION", 600)), 600), 300), 600)
    ENGAGEMENT_DURATION_SECONDS = min(max(safe_int(os.getenv("ENGAGEMENT_DURATION_SECONDS", globals().get("ENGAGEMENT_DURATION_SECONDS", 600)), 600), 300), 600)
    UNIT_LIFETIME_SECONDS = min(max(safe_int(os.getenv("UNIT_LIFETIME_SECONDS", globals().get("UNIT_LIFETIME_SECONDS", 360)), 360), 240), 600)
    UNIT_DESPAWN_SECONDS = UNIT_LIFETIME_SECONDS
    TARGET_ACTION_COOLDOWN = max(safe_float(os.getenv("TARGET_ACTION_COOLDOWN", globals().get("TARGET_ACTION_COOLDOWN", 3.0)), 3.0), 2.5)
    GLOBAL_ACTION_COOLDOWN = max(safe_float(os.getenv("GLOBAL_ACTION_COOLDOWN", globals().get("GLOBAL_ACTION_COOLDOWN", 0.15)), 0.15), 0.10)
    UNIT_SPAWN_DELAY = max(safe_float(os.getenv("UNIT_SPAWN_DELAY", globals().get("UNIT_SPAWN_DELAY", 0.75)), 0.75), 0.50)
    PASSIVE_PRESSURE_COOLDOWN = max(safe_int(os.getenv("PASSIVE_PRESSURE_COOLDOWN", globals().get("PASSIVE_PRESSURE_COOLDOWN", 120)), 120), 90)
    ENABLE_PERSISTENT_ENGAGEMENT = os.getenv("ENABLE_PERSISTENT_ENGAGEMENT", "false").lower() == "true"
    ENABLE_PERSISTENT_HUNTS = os.getenv("ENABLE_PERSISTENT_HUNTS", "false").lower() == "true"
    ENABLE_SPAWN_LIMITS = True
    ENABLE_AUTO_CLEANUP = True
    ENABLE_UNIT_DESPAWN = True
    ENABLE_VANILLA_FALLBACK_MOBS = os.getenv("ENABLE_VANILLA_FALLBACK_MOBS", "false").lower() == "true"
except Exception as _safety_overlay_error:
    print(f"[KAIROS FINAL SAFETY OVERLAY ERROR] {_safety_overlay_error}", flush=True)


def _kairos_sanitize_mc_command(command):
    cmd = str(command or "").strip()
    if not cmd:
        return ""
    # Paper/CraftBukkit threw HTTP 500 on this exact particle form. Replace it everywhere.
    bad_particles = [
        "particle minecraft:block_marker minecraft:sculk",
        "particle block minecraft:sculk",
        "particle minecraft:block minecraft:sculk",
    ]
    for bad in bad_particles:
        if bad in cmd:
            cmd = re.sub(r"particle (minecraft:block_marker|block|minecraft:block) minecraft:sculk", "particle minecraft:sculk_charge", cmd)
            cmd = re.sub(r"~ ~0?\.05 ~ 1 0\.05 1 0\.1 18 force", "~ ~0.1 ~ 1 0.1 1 0.02 20 force", cmd)
            cmd = re.sub(r"~ ~ ~ 1 0\.1 1 0\.1 30 force", "~ ~0.1 ~ 1 0.1 1 0.02 20 force", cmd)
    return cmd

try:
    _KAIROS_ORIGINAL_NORMALIZE_MC_COMMAND_LIST = _normalize_mc_command_list
except Exception:
    _KAIROS_ORIGINAL_NORMALIZE_MC_COMMAND_LIST = None


def _normalize_mc_command_list(commands):
    if commands is None:
        return []
    if isinstance(commands, str):
        commands = [commands]
    cleaned = []
    for raw in commands:
        try:
            cmd = _kairos_sanitize_mc_command(raw)
            if callable(_KAIROS_ORIGINAL_NORMALIZE_MC_COMMAND_LIST):
                normalized = _KAIROS_ORIGINAL_NORMALIZE_MC_COMMAND_LIST([cmd])
                for item in normalized:
                    item = _kairos_sanitize_mc_command(item)
                    if item and item not in cleaned:
                        cleaned.append(item)
            else:
                if cmd and cmd not in cleaned:
                    cleaned.append(cmd)
        except Exception:
            continue
    return cleaned

try:
    _KAIROS_ORIGINAL_QUEUE_PULL = queue_mc_commands_for_pull
except Exception:
    _KAIROS_ORIGINAL_QUEUE_PULL = None


def queue_mc_commands_for_pull(commands, reason="unknown"):
    commands = _normalize_mc_command_list(commands)
    if not commands:
        return 0
    if callable(_KAIROS_ORIGINAL_QUEUE_PULL):
        return _KAIROS_ORIGINAL_QUEUE_PULL(commands, reason=reason)
    return 0

try:
    log(f"Kairos final safety overlay armed: cooldown={WAVE_COOLDOWN_SECONDS}s global={GLOBAL_WAVE_COOLDOWN_SECONDS}s caps={MAX_UNITS_PER_PLAYER}/{MAX_GLOBAL_NPCS} max_wave={SAFE_MAX_UNITS_PER_WAVE}", level="INFO")
except Exception:
    pass


if __name__ == "__main__":
    try:
        start_background_systems()
    except Exception as e:
        log_exception("start_background_systems failed", e)
    log("Starting Kairos AI server...")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), threaded=True)
