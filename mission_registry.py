"""Data-driven mission, artifact, and memory registries for F.R.A.C.T.U.R.E."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

MISSIONS_DIR = Path("missions")
ARTIFACTS_DIR = Path("artifacts")
MEMORIES_DIR = Path("memories")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def load_mission(operation: Any) -> Dict[str, Any]:
    op = str(operation or "001").zfill(3)
    return _load_json(MISSIONS_DIR / f"{op}.json") or {
        "id": op,
        "title": "UNREGISTERED OPERATION",
        "required_clearance": 1,
        "destination": "Arrival Terminal 01",
        "warning": "Mission registry entry unavailable.",
        "stages": {"0": {"status": "UNAVAILABLE", "directive": "Report the missing mission record to an administrator."}},
    }


def stage_for(mission: Dict[str, Any], step: Any) -> Dict[str, Any]:
    stages = mission.get("stages", {}) or {}
    key = str(step if step is not None else 0)
    return stages.get(key) or stages.get("0") or {"status": "UNKNOWN", "directive": "Await further instructions."}


def load_artifact(artifact_id: str) -> Optional[Dict[str, Any]]:
    return _load_json(ARTIFACTS_DIR / f"{artifact_id.lower()}.json")


def load_memory(memory_id: str) -> Optional[Dict[str, Any]]:
    return _load_json(MEMORIES_DIR / f"{memory_id.lower()}.json")
