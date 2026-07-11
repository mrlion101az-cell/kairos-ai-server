"""Persistent player progression storage for F.R.A.C.T.U.R.E."""
from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent
STORAGE_FILE = Path(os.getenv("FRACTURE_STORAGE_FILE", str(BASE_DIR / "fracture_players.json"))).expanduser()
_LOCK = threading.RLock()

def _log(message: str, level: str = "INFO") -> None:
    print(f"[FRACTURE_STORAGE {level}] {message}", flush=True)

def _safe_player_key(player_name: str) -> str:
    raw = str(player_name or "").strip() or "unknown"
    return (re.sub(r"[^A-Za-z0-9_\-]", "_", raw)[:64] or "unknown")

def _default_player(player_name: str) -> Dict[str, Any]:
    return {
        "name": str(player_name or "unknown").strip() or "unknown",
        "clearance": 1,
        "current_operation": "001",
        "mission_step": 0,
        "memory_integrity": 3,
        "recovered_artifacts": [],
        "unlocked_memories": [],
        "completed_operations": [],
        "fracture_visits": 0,
    }

def _ensure_parent_dir() -> None:
    STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)

def _read_all() -> Dict[str, Dict[str, Any]]:
    _ensure_parent_dir()
    if not STORAGE_FILE.exists():
        return {}
    try:
        raw = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        _log(f"Failed to read {STORAGE_FILE}: {exc}", "ERROR")
        return {}

def _write_all(data: Dict[str, Dict[str, Any]]) -> None:
    _ensure_parent_dir()
    temp_file = STORAGE_FILE.with_suffix(STORAGE_FILE.suffix + ".tmp")
    temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temp_file.replace(STORAGE_FILE)

def _normalize_record(player_name: str, record: Dict[str, Any]) -> Dict[str, Any]:
    merged = _default_player(player_name)
    merged.update(record or {})
    merged["name"] = str(merged.get("name") or player_name or "unknown")
    merged["clearance"] = max(0, int(merged.get("clearance", 1)))
    merged["current_operation"] = str(merged.get("current_operation") or "001").zfill(3)
    merged["mission_step"] = max(0, int(merged.get("mission_step", 0)))
    merged["memory_integrity"] = max(0, min(100, int(merged.get("memory_integrity", 3))))
    merged["fracture_visits"] = max(0, int(merged.get("fracture_visits", 0)))
    for key in ("recovered_artifacts", "unlocked_memories", "completed_operations"):
        value = merged.get(key, [])
        if not isinstance(value, list):
            value = []
        merged[key] = list(dict.fromkeys(str(item) for item in value if str(item).strip()))
    return merged

def get_player(player_name: str, *, increment_visit: bool = False) -> Dict[str, Any]:
    key = _safe_player_key(player_name)
    with _LOCK:
        data = _read_all()
        record = _normalize_record(player_name, data.get(key, {}))
        if increment_visit:
            record["fracture_visits"] += 1
        data[key] = record
        _write_all(data)
        return deepcopy(record)

def update_player(player_name: str, **changes: Any) -> Dict[str, Any]:
    key = _safe_player_key(player_name)
    with _LOCK:
        data = _read_all()
        record = _normalize_record(player_name, data.get(key, {}))
        for field, value in changes.items():
            if field != "name":
                record[field] = value
        record = _normalize_record(player_name, record)
        data[key] = record
        _write_all(data)
        return deepcopy(record)

def add_artifact(player_name: str, *, artifact_id: str, memory_id: str | None = None, integrity_gain: int = 1) -> Dict[str, Any]:
    artifact_id = str(artifact_id or "").strip().lower()
    memory_id = str(memory_id or "").strip().lower() or None
    if not artifact_id:
        raise ValueError("artifact_id is required")

    key = _safe_player_key(player_name)
    with _LOCK:
        data = _read_all()
        record = _normalize_record(player_name, data.get(key, {}))
        artifacts: List[str] = list(record.get("recovered_artifacts", []))
        memories: List[str] = list(record.get("unlocked_memories", []))
        duplicate = artifact_id in artifacts

        if not duplicate:
            artifacts.append(artifact_id)
            record["recovered_artifacts"] = artifacts
            if memory_id and memory_id not in memories:
                memories.append(memory_id)
                record["unlocked_memories"] = memories
            record["memory_integrity"] = min(100, int(record.get("memory_integrity", 3)) + max(0, int(integrity_gain)))

        record = _normalize_record(player_name, record)
        data[key] = record
        _write_all(data)

        result = deepcopy(record)
        result["artifact_duplicate"] = duplicate
        return result

def mark_operation_complete(player_name: str, operation_id: str) -> Dict[str, Any]:
    operation_id = str(operation_id or "").zfill(3)
    record = get_player(player_name)
    completed = list(record.get("completed_operations", []))
    if operation_id not in completed:
        completed.append(operation_id)
    return update_player(player_name, completed_operations=completed)

def reset_player(player_name: str) -> Dict[str, Any]:
    key = _safe_player_key(player_name)
    with _LOCK:
        data = _read_all()
        record = _default_player(player_name)
        data[key] = record
        _write_all(data)
        return deepcopy(record)

def storage_status() -> Dict[str, Any]:
    with _LOCK:
        data = _read_all()
        return {"ok": True, "storage_file": str(STORAGE_FILE), "player_count": len(data), "exists": STORAGE_FILE.exists()}

_log(f"Using storage file: {STORAGE_FILE}")
