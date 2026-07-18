"""Persistent player progression storage for F.R.A.C.T.U.R.E."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_FILE = Path("data/memory/fracture_players.json")
_LOCK = threading.RLock()


def _default_root() -> Dict[str, Any]:
    return {"version": 1, "updated_at": time.time(), "players": {}, "global": {"restored_artifacts": []}}


def _default_player(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "clearance": 1,
        "current_operation": "001",
        "mission_step": 0,
        "fracture_visits": 0,
        "recovered_artifacts": [],
        "unlocked_memories": [],
        "completed_operations": [],
        "memory_integrity": 3,
        "first_seen": time.time(),
        "last_seen": time.time(),
    }


def _load() -> Dict[str, Any]:
    with _LOCK:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not DATA_FILE.exists():
            root = _default_root()
            _save(root)
            return root
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else _default_root()
        except Exception:
            return _default_root()


def _save(data: Dict[str, Any]) -> None:
    with _LOCK:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = time.time()
        tmp = DATA_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DATA_FILE)


def get_player(name: str, increment_visit: bool = False) -> Dict[str, Any]:
    clean = str(name or "traveler").strip()
    data = _load()
    players = data.setdefault("players", {})
    record = players.setdefault(clean, _default_player(clean))
    record["last_seen"] = time.time()
    if increment_visit:
        record["fracture_visits"] = int(record.get("fracture_visits", 0)) + 1
    _save(data)
    return dict(record)


def update_player(name: str, **changes: Any) -> Dict[str, Any]:
    clean = str(name or "traveler").strip()
    data = _load()
    players = data.setdefault("players", {})
    record = players.setdefault(clean, _default_player(clean))
    for key, value in changes.items():
        record[key] = value
    record["last_seen"] = time.time()
    _save(data)
    return dict(record)


def add_artifact(name: str, artifact_id: str, memory_id: Optional[str] = None, integrity_gain: int = 1) -> Dict[str, Any]:
    data = _load()
    players = data.setdefault("players", {})
    record = players.setdefault(name, _default_player(name))
    artifacts: List[str] = record.setdefault("recovered_artifacts", [])
    memories: List[str] = record.setdefault("unlocked_memories", [])
    duplicate = artifact_id in artifacts
    if not duplicate:
        artifacts.append(artifact_id)
        if memory_id and memory_id not in memories:
            memories.append(memory_id)
        record["memory_integrity"] = min(100, int(record.get("memory_integrity", 3)) + max(0, integrity_gain))
    record["last_seen"] = time.time()
    _save(data)
    result = dict(record)
    result["artifact_duplicate"] = duplicate
    return result


def mark_operation_complete(name: str, operation_id: str) -> Dict[str, Any]:
    """
    Records that a player has fully completed a given operation (mission).
    Idempotent: completing the same operation twice only records it once.

    Used by artifact_processor.py after an artifact submission finishes
    updating clearance/current_operation/mission_step, to log which
    operation was just finished (as opposed to current_operation, which
    points at whichever operation is now active/next).
    """
    clean = str(name or "traveler").strip()
    op = str(operation_id or "").strip().zfill(3)

    data = _load()
    players = data.setdefault("players", {})
    record = players.setdefault(clean, _default_player(clean))

    completed: List[str] = record.setdefault("completed_operations", [])
    if op and op not in completed:
        completed.append(op)

    record["last_seen"] = time.time()
    _save(data)
    return dict(record)
