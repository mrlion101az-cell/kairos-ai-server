
"""
memory_engine.py
Kairos / Nexus Memory Engine

Purpose:
- Centralized JSON memory storage for the modular Kairos ecosystem.
- Shared by npc_engine.py, ai_core.py, command_bridge.py, war_engine.py,
  continuity_engine.py, telemetry_engine.py, and future systems.
- Contains NO Flask routes.
- Contains NO background loops.
- Contains NO Minecraft/Discord transport.
- Designed to be safe, small, and impossible to crash the whole system.

This file is the long-term memory layer.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================
# CONFIG
# ============================================================

MEMORY_DEBUG = os.getenv("MEMORY_ENGINE_DEBUG", "true").lower() == "true"

DATA_DIR = Path(os.getenv("KAIROS_DATA_DIR", "data"))
MEMORY_DIR = DATA_DIR / "memory"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"

MEMORY_FILE = MEMORY_DIR / "kairos_memory.json"
NPC_MEMORY_FILE = MEMORY_DIR / "npc_memory.json"
WORLD_MEMORY_FILE = MEMORY_DIR / "world_memory.json"
PLAYER_MEMORY_FILE = MEMORY_DIR / "player_memory.json"

MAX_LIST_ITEMS_DEFAULT = int(os.getenv("KAIROS_MEMORY_MAX_LIST_ITEMS", "250"))

_memory_lock = threading.RLock()


# ============================================================
# LOGGING
# ============================================================

def memory_log(message: str, level: str = "INFO") -> None:
    if MEMORY_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[MEMORY_ENGINE {level}] {message}", flush=True)


def memory_log_exception(context: str, exc: Exception) -> None:
    print(f"[MEMORY_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()


# ============================================================
# DIRECTORY INIT
# ============================================================

def ensure_memory_dirs() -> None:
    for folder in [DATA_DIR, MEMORY_DIR, LOG_DIR, BACKUP_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# DEFAULT STRUCTURES
# ============================================================

def default_core_memory() -> Dict[str, Any]:
    return {
        "version": 1,
        "created_at": time.time(),
        "updated_at": time.time(),
        "players": {},
        "npcs": {},
        "world": {
            "events": [],
            "rumors": [],
            "factions": {},
            "regions": {},
        },
        "system": {
            "notes": [],
            "last_errors": [],
        },
    }


def default_npc_memory() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": time.time(),
        "npc_interactions": {},
        "npc_relationships": {},
        "npc_recent_lines": {},
    }


def default_world_memory() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": time.time(),
        "events": [],
        "rumors": [],
        "factions": {},
        "regions": {},
        "occupations": {},
        "threats": {},
    }


def default_player_memory() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": time.time(),
        "players": {},
        "identity_links": {},
    }


def default_for_path(path: Path) -> Dict[str, Any]:
    if path == NPC_MEMORY_FILE:
        return default_npc_memory()
    if path == WORLD_MEMORY_FILE:
        return default_world_memory()
    if path == PLAYER_MEMORY_FILE:
        return default_player_memory()
    return default_core_memory()


# ============================================================
# BASIC JSON IO
# ============================================================

def load_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_memory_dirs()
    default = default if default is not None else default_for_path(path)

    with _memory_lock:
        try:
            if not path.exists():
                save_json(path, default)
                return dict(default)

            text = path.read_text(encoding="utf-8").strip()
            if not text:
                save_json(path, default)
                return dict(default)

            data = json.loads(text)
            if not isinstance(data, dict):
                return dict(default)

            return data

        except Exception as exc:
            memory_log_exception(f"Failed loading {path}", exc)
            backup_corrupt_file(path)
            save_json(path, default)
            return dict(default)


def save_json(path: Path, data: Dict[str, Any]) -> bool:
    ensure_memory_dirs()

    with _memory_lock:
        try:
            data["updated_at"] = time.time()
            tmp_path = path.with_suffix(path.suffix + ".tmp")

            tmp_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            tmp_path.replace(path)
            return True

        except Exception as exc:
            memory_log_exception(f"Failed saving {path}", exc)
            return False


def backup_corrupt_file(path: Path) -> None:
    try:
        if not path.exists():
            return
        stamp = int(time.time())
        backup_path = BACKUP_DIR / f"{path.stem}.corrupt.{stamp}{path.suffix}"
        shutil.copy2(path, backup_path)
        memory_log(f"Backed up corrupt memory file to {backup_path}", "WARN")
    except Exception as exc:
        memory_log_exception("backup_corrupt_file failed", exc)


# ============================================================
# CORE MEMORY HELPERS
# ============================================================

def load_core_memory() -> Dict[str, Any]:
    return load_json(MEMORY_FILE, default_core_memory())


def save_core_memory(data: Dict[str, Any]) -> bool:
    return save_json(MEMORY_FILE, data)


def load_npc_memory() -> Dict[str, Any]:
    return load_json(NPC_MEMORY_FILE, default_npc_memory())


def save_npc_memory(data: Dict[str, Any]) -> bool:
    return save_json(NPC_MEMORY_FILE, data)


def load_world_memory() -> Dict[str, Any]:
    return load_json(WORLD_MEMORY_FILE, default_world_memory())


def save_world_memory(data: Dict[str, Any]) -> bool:
    return save_json(WORLD_MEMORY_FILE, data)


def load_player_memory() -> Dict[str, Any]:
    return load_json(PLAYER_MEMORY_FILE, default_player_memory())


def save_player_memory(data: Dict[str, Any]) -> bool:
    return save_json(PLAYER_MEMORY_FILE, data)


# ============================================================
# LIST HELPERS
# ============================================================

def append_limited(items: List[Any], value: Any, limit: int = MAX_LIST_ITEMS_DEFAULT) -> List[Any]:
    items.append(value)
    if len(items) > limit:
        del items[0 : len(items) - limit]
    return items


# ============================================================
# NPC MEMORY
# ============================================================

def record_npc_interaction(
    npc_name: str,
    player_name: str,
    message: str = "",
    reply: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = load_npc_memory()

    key = str(npc_name or "UnknownNPC").strip()
    player = str(player_name or "unknown").strip()

    data.setdefault("npc_interactions", {})
    data.setdefault("npc_recent_lines", {})

    npc_bucket = data["npc_interactions"].setdefault(key, [])
    recent_lines = data["npc_recent_lines"].setdefault(key, [])

    event = {
        "npc": key,
        "player": player,
        "message": str(message or "")[:500],
        "reply": str(reply or "")[:500],
        "metadata": metadata or {},
        "timestamp": time.time(),
    }

    append_limited(npc_bucket, event, 200)
    if reply:
        append_limited(recent_lines, str(reply), 20)

    save_npc_memory(data)
    return event


def get_npc_recent_interactions(npc_name: str, limit: int = 10) -> List[Dict[str, Any]]:
    data = load_npc_memory()
    items = data.get("npc_interactions", {}).get(str(npc_name), [])
    return list(items[-limit:])


def get_npc_recent_lines(npc_name: str, limit: int = 10) -> List[str]:
    data = load_npc_memory()
    items = data.get("npc_recent_lines", {}).get(str(npc_name), [])
    return list(items[-limit:])


# ============================================================
# PLAYER MEMORY
# ============================================================

def get_player_record(player_name: str) -> Dict[str, Any]:
    data = load_player_memory()

    player = str(player_name or "unknown").strip()
    data.setdefault("players", {})

    record = data["players"].setdefault(player, {
        "name": player,
        "first_seen": time.time(),
        "last_seen": time.time(),
        "memories": [],
        "traits": {},
        "stats": {},
    })

    record["last_seen"] = time.time()
    save_player_memory(data)
    return record


def append_player_memory(player_name: str, note: str, limit: int = 100) -> Dict[str, Any]:
    data = load_player_memory()

    player = str(player_name or "unknown").strip()
    data.setdefault("players", {})

    record = data["players"].setdefault(player, {
        "name": player,
        "first_seen": time.time(),
        "last_seen": time.time(),
        "memories": [],
        "traits": {},
        "stats": {},
    })

    record["last_seen"] = time.time()
    record.setdefault("memories", [])
    append_limited(record["memories"], {
        "text": str(note or "")[:500],
        "timestamp": time.time(),
    }, limit)

    save_player_memory(data)
    return record


# ============================================================
# WORLD MEMORY
# ============================================================

def record_world_event(
    event_type: str,
    description: str,
    location: Optional[str] = None,
    faction: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = load_world_memory()
    data.setdefault("events", [])

    event = {
        "type": str(event_type or "event"),
        "description": str(description or "")[:1000],
        "location": location,
        "faction": faction,
        "metadata": metadata or {},
        "timestamp": time.time(),
    }

    append_limited(data["events"], event, 500)
    save_world_memory(data)
    return event


def record_rumor(
    rumor: str,
    location: Optional[str] = None,
    faction: Optional[str] = None,
    confidence: float = 0.5,
) -> Dict[str, Any]:
    data = load_world_memory()
    data.setdefault("rumors", [])

    item = {
        "rumor": str(rumor or "")[:1000],
        "location": location,
        "faction": faction,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "timestamp": time.time(),
    }

    append_limited(data["rumors"], item, 500)
    save_world_memory(data)
    return item


def get_recent_world_events(limit: int = 10) -> List[Dict[str, Any]]:
    data = load_world_memory()
    return list(data.get("events", [])[-limit:])


def get_recent_rumors(limit: int = 10) -> List[Dict[str, Any]]:
    data = load_world_memory()
    return list(data.get("rumors", [])[-limit:])


# ============================================================
# SYSTEM NOTES / ERRORS
# ============================================================

def record_system_note(note: str, level: str = "INFO") -> Dict[str, Any]:
    data = load_core_memory()
    data.setdefault("system", {})
    data["system"].setdefault("notes", [])

    item = {
        "level": level,
        "note": str(note or "")[:1000],
        "timestamp": time.time(),
    }

    append_limited(data["system"]["notes"], item, 250)
    save_core_memory(data)
    return item


def record_system_error(context: str, error: Any) -> Dict[str, Any]:
    data = load_core_memory()
    data.setdefault("system", {})
    data["system"].setdefault("last_errors", [])

    item = {
        "context": str(context or "unknown"),
        "error": str(error or "")[:1000],
        "timestamp": time.time(),
    }

    append_limited(data["system"]["last_errors"], item, 100)
    save_core_memory(data)
    return item


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    ensure_memory_dirs()
    print("Memory dirs ready.")
    print(record_npc_interaction("CaptainVaros", "RealSociety5107", "click", "Captain Varos: Stay alert."))
    print(record_world_event("test", "Memory engine self-test event.", location="Trojan Kingdom"))
