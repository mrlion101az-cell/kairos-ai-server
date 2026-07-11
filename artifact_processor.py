"""
Generic artifact processing engine for F.R.A.C.T.U.R.E.

Purpose:
- Validate an artifact against the active mission
- Prevent duplicate submissions
- Restore linked memories
- Increase memory integrity
- Grant clearance
- Advance the player to the next operation
- Return deterministic Minecraft commands and story context

This module is data-driven.
It reads mission, artifact, and memory JSON files through mission_registry.py.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fracture_storage import (
    add_artifact,
    get_player,
    mark_operation_complete,
    update_player,
)
from mission_registry import (
    load_artifact,
    load_memory,
    load_mission,
)

from inventory_bridge import (
    player_has_artifact,
    remove_artifact,
)


def _clean_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _scoreboard_commands(player_name: str, record: Dict[str, Any]) -> list[str]:
    return [
        f"scoreboard players set {player_name} clearance {_safe_int(record.get('clearance'), 1)}",
        f"scoreboard players set {player_name} operation {_safe_int(record.get('current_operation'), 1)}",
        f"scoreboard players set {player_name} mission_step {_safe_int(record.get('mission_step'), 0)}",
    ]


def process_artifact_submission(
    player_name: str,
    artifact_id: str,
    *,
    operation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process one artifact submission for one player.

    Returns:
    {
        "ok": bool,
        "accepted": bool,
        "duplicate": bool,
        "reason": str,
        "player": str,
        "artifact": {...},
        "memory": {...} | None,
        "mission": {...},
        "record": {...},
        "commands": [...]
    }
    """

    player_name = str(player_name or "").strip()
    artifact_id = _clean_id(artifact_id)

    if not player_name:
        return {
            "ok": False,
            "accepted": False,
            "reason": "missing_player_name",
            "commands": [],
        }

    if not artifact_id:
        return {
            "ok": False,
            "accepted": False,
            "player": player_name,
            "reason": "missing_artifact_id",
            "commands": [],
        }

    record = get_player(player_name)
    active_operation = str(
        operation_id
        or record.get("current_operation")
        or "001"
    ).zfill(3)

    mission = load_mission(active_operation)
    artifact = load_artifact(artifact_id)

    if not artifact:
        return {
            "ok": False,
            "accepted": False,
            "player": player_name,
            "artifact_id": artifact_id,
            "mission": mission,
            "reason": "unknown_artifact",
            "commands": [],
        }

    completion = mission.get("completion", {}) or {}
    required_artifact = _clean_id(completion.get("required_artifact"))

    inventory_check = player_has_artifact(player_name, artifact)

    if not inventory_check.get("found"):
        return {
            "ok": True,
            "accepted": False,
            "player": player_name,
            "artifact_id": artifact_id,
            "artifact": artifact,
            "mission": mission,
            "reason": "artifact_not_present_in_inventory",
            "inventory": inventory_check,
            "commands": [],
        }


    if required_artifact and artifact_id != required_artifact:
        return {
            "ok": True,
            "accepted": False,
            "player": player_name,
            "artifact_id": artifact_id,
            "artifact": artifact,
            "mission": mission,
            "reason": "artifact_not_required_for_active_operation",
            "required_artifact": required_artifact,
            "commands": [],
        }

    memory_id = _clean_id(
        artifact.get("restores_memory")
        or completion.get("restore_memory")
    ) or None

    integrity_gain = _safe_int(
        artifact.get("memory_integrity_gain"),
        1,
    )

    remove_result = remove_artifact(player_name, artifact)

    if not remove_result.get("removed"):
        return {
            "ok": False,
            "accepted": False,
            "player": player_name,
            "artifact_id": artifact_id,
            "artifact": artifact,
            "mission": mission,
            "reason": "artifact_remove_failed",
            "inventory": remove_result,
            "commands": [],
        }

    record = add_artifact(
        player_name,
        artifact_id=artifact_id,
        memory_id=memory_id,
        integrity_gain=integrity_gain,
    )

    duplicate = bool(record.get("artifact_duplicate"))

    if duplicate:
        clean_record = dict(record)
        clean_record.pop("artifact_duplicate", None)

        return {
            "ok": True,
            "accepted": False,
            "duplicate": True,
            "player": player_name,
            "artifact_id": artifact_id,
            "artifact": artifact,
            "mission": mission,
            "record": clean_record,
            "reason": "artifact_already_submitted",
            "commands": _scoreboard_commands(player_name, clean_record),
        }

    grant_clearance = _safe_int(
        completion.get(
            "grant_clearance",
            artifact.get("progression", {}).get("grant_clearance"),
        ),
        _safe_int(record.get("clearance"), 1),
    )

    next_operation = str(
        completion.get(
            "unlock_operation",
            artifact.get("progression", {}).get("set_operation"),
        )
        or record.get("current_operation")
        or active_operation
    ).zfill(3)

    next_step = _safe_int(
        artifact.get("progression", {}).get("set_mission_step"),
        0,
    )

    updated_clearance = max(
        _safe_int(record.get("clearance"), 1),
        grant_clearance,
    )

    record = update_player(
        player_name,
        clearance=updated_clearance,
        current_operation=next_operation,
        mission_step=next_step,
    )

    record = mark_operation_complete(
        player_name,
        active_operation,
    )

    memory = load_memory(memory_id) if memory_id else None

    return {
        "ok": True,
        "accepted": True,
        "duplicate": False,
        "player": player_name,
        "artifact_id": artifact_id,
        "artifact": artifact,
        "memory_id": memory_id,
        "memory": memory,
        "mission": mission,
        "completed_operation": active_operation,
        "next_operation": next_operation,
        "record": record,
        "reason": "artifact_accepted_and_progression_updated",
        "commands": _scoreboard_commands(player_name, record),
    }


def build_artifact_response(result: Dict[str, Any]) -> str:
    """
    Build deterministic Fracture dialogue from a submission result.
    This is optional presentation text for command_bridge or fracture_terminal.
    """

    if not isinstance(result, dict):
        return "Artifact processing failed."

    if not result.get("ok"):
        reason = result.get("reason", "unknown_error")
        if reason == "unknown_artifact":
            return (
                "Artifact scan failed... "
                "Object not recognized by the Project Nexus archive."
            )
        return f"Artifact processing failed... {reason}."

    if result.get("duplicate"):
        artifact = result.get("artifact", {}) or {}
        name = artifact.get("display_name") or result.get("artifact_id") or "artifact"
        return (
            f"{name} already exists in the recovered archive. "
            "No additional clearance has been authorized."
        )

    if not result.get("accepted"):
        required = result.get("required_artifact")
        reason = result.get("reason")
        if reason == "artifact_not_present_in_inventory":
            return (
                "Inventory scan complete... Required artifact not detected. "
                "Return after recovery."
            )
        if required:
            return (
                "Artifact rejected... "
                f"Current operation requires {required}."
            )
        return "Artifact rejected... incompatible with the active operation."

    artifact = result.get("artifact", {}) or {}
    memory = result.get("memory", {}) or {}
    record = result.get("record", {}) or {}

    artifact_name = artifact.get("display_name") or result.get("artifact_id") or "Recovered Artifact"
    memory_title = memory.get("title") or "ARCHIVE FRAGMENT"
    clearance = record.get("clearance", "UNKNOWN")
    next_operation = result.get("next_operation", record.get("current_operation", "UNKNOWN"))

    return (
        f"Artifact verified: {artifact_name}. "
        f"Archive restored: {memory_title}. "
        f"Clearance Level {clearance} authorized. "
        f"Operation {next_operation} unlocked."
    )
