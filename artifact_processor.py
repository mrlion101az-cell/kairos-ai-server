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

Two entry points:
- process_artifact_submission(): Kairos is the authority. Re-checks the
  player's live inventory snapshot and issues the removal command itself
  via mc_connector. Used by repository_bridge.py for integrations where
  nothing has already validated/removed the item.
- process_repository_confirmation(): trusts that the caller (the
  NexusBridge Minecraft plugin, via app.py's /repository_event route)
  already validated the item was present and physically removed it.
  Skips inventory re-check and removal entirely, and only syncs Kairos's
  own story state (clearance, operation, memory restoration, duplicate
  tracking) to match what already happened in-game.
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


# ============================================================
# REPOSITORY CONFIRMATION (NexusBridge Minecraft plugin path)
#
# Called from app.py's /repository_event route. The plugin has
# ALREADY validated the item was present and physically removed it
# from the chest before this function is ever invoked -- so, unlike
# process_artifact_submission() above, this does NOT call
# player_has_artifact() or remove_artifact(). Re-checking inventory
# here would always fail, since nothing currently pushes live
# snapshots to /inventory_event for this flow.
#
# This function's only job is to bring Kairos's own story state
# (fracture_storage) in sync with what the plugin already decided:
# record the artifact, restore the linked memory, and advance
# clearance/operation/mission_step.
# ============================================================

def process_repository_confirmation(
    player_name: str,
    artifact_id: str,
    *,
    memory_id: Optional[str] = None,
    integrity_gain: int = 1,
    grant_clearance: Optional[int] = None,
    next_operation: Optional[str] = None,
    next_step: int = 0,
    completed_operation: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Confirms an artifact submission that the Minecraft plugin already
    validated and physically removed from the player's chest/inventory.

    Returns the same response shape as process_artifact_submission()
    (ok, accepted, duplicate, player, artifact_id, artifact, memory_id,
    memory, record, reason, commands) so build_artifact_response() and
    any other downstream consumer work unmodified.
    """

    player_name = str(player_name or "").strip()
    artifact_id = _clean_id(artifact_id)

    if not player_name or not artifact_id:
        return {
            "ok": False,
            "accepted": False,
            "reason": "missing_player_or_artifact",
            "commands": [],
        }

    artifact = load_artifact(artifact_id) or {}
    record = get_player(player_name)

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
            "record": clean_record,
            "reason": "artifact_already_submitted",
            "commands": _scoreboard_commands(player_name, clean_record),
        }

    resolved_clearance = (
        grant_clearance
        if grant_clearance is not None
        else _safe_int(record.get("clearance"), 1)
    )
    resolved_operation = str(
        next_operation or record.get("current_operation") or "001"
    ).zfill(3)

    updated_clearance = max(
        _safe_int(record.get("clearance"), 1),
        _safe_int(resolved_clearance, 1),
    )

    record = update_player(
        player_name,
        clearance=updated_clearance,
        current_operation=resolved_operation,
        mission_step=_safe_int(next_step, 0),
    )

    if completed_operation:
        record = mark_operation_complete(
            player_name,
            str(completed_operation).zfill(3),
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
        "next_operation": resolved_operation,
        "record": record,
        "reason": "artifact_confirmed_from_minecraft",
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
