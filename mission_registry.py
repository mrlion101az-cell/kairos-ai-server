"""Data-driven mission, artifact, and memory registries for F.R.A.C.T.U.R.E."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

# --------------------------------------------------
# Resolve paths relative to THIS file, not the
# current working directory.
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

MISSIONS_DIR = BASE_DIR / "missions"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
MEMORIES_DIR = BASE_DIR / "memories"

print(f"[MISSION_REGISTRY] Base directory : {BASE_DIR}")
print(f"[MISSION_REGISTRY] Missions dir  : {MISSIONS_DIR}")
print(f"[MISSION_REGISTRY] Artifacts dir : {ARTIFACTS_DIR}")
print(f"[MISSION_REGISTRY] Memories dir  : {MEMORIES_DIR}")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        print(f"[MISSION_REGISTRY] Loading: {path}")

        if not path.exists():
            print(f"[MISSION_REGISTRY] File NOT FOUND: {path}")
            return None

        raw = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(raw, dict):
            print(f"[MISSION_REGISTRY] JSON is not an object: {path}")
            return None

        print(f"[MISSION_REGISTRY] Successfully loaded: {path.name}")
        return raw

    except Exception as e:
        print(f"[MISSION_REGISTRY ERROR] {path}")
        print(e)
        return None


def load_mission(operation: Any) -> Dict[str, Any]:
    op = str(operation or "001").zfill(3)

    mission_file = MISSIONS_DIR / f"{op}.json"

    mission = _load_json(mission_file)

    if mission:
        print(f"[MISSION_REGISTRY] Mission {op} loaded.")
        return mission

    print(f"[MISSION_REGISTRY] Mission {op} NOT FOUND. Using fallback.")

    return {
        "id": op,
        "title": "UNREGISTERED OPERATION",
        "required_clearance": 1,
        "destination": "Arrival Terminal 01",
        "warning": "Mission registry entry unavailable.",
        "stages": {
            "0": {
                "status": "UNAVAILABLE",
                "directive": "Report the missing mission record to an administrator."
            }
        }
    }


def stage_for(mission: Dict[str, Any], step: Any) -> Dict[str, Any]:
    stages = mission.get("stages", {}) or {}

    key = str(step if step is not None else 0)

    stage = (
        stages.get(key)
        or stages.get("0")
        or {
            "status": "UNKNOWN",
            "directive": "Await further instructions."
        }
    )

    print(f"[MISSION_REGISTRY] Stage {key} -> {stage.get('status')}")

    return stage


def load_artifact(artifact_id: str) -> Optional[Dict[str, Any]]:
    filename = f"{artifact_id.lower()}.json"
    return _load_json(ARTIFACTS_DIR / filename)


def load_memory(memory_id: str) -> Optional[Dict[str, Any]]:
    filename = f"{memory_id.lower()}.json"
    return _load_json(MEMORIES_DIR / filename)
