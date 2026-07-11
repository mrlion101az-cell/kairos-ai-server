"""Permanent F.R.A.C.T.U.R.E. terminal engine.

The terminal owns deterministic mission facts. The NPC AI only controls presentation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fracture_storage import add_artifact, get_player, update_player
from mission_registry import load_artifact, load_mission, stage_for


def _roman(value: int) -> str:
    values = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    n = max(0, int(value))
    out = ""
    for amount, symbol in values:
        while n >= amount:
            out += symbol
            n -= amount
    return out or "NONE"


def build_terminal_context(
    player_name: str,
    incoming_context: Optional[Dict[str, Any]] = None,
    increment_visit: bool = True,
) -> Dict[str, Any]:
    incoming = dict(incoming_context or {})
    record = get_player(player_name, increment_visit=increment_visit)

    # External authoritative values can override storage when supplied later.
    for key in ("clearance", "current_operation", "mission_step", "memory_integrity"):
        if key in incoming and incoming[key] not in (None, ""):
            storage_key = "current_operation" if key == "current_operation" else key
            record[storage_key] = incoming[key]

    # Backward-compatible key used by command_bridge/npc_engine contexts.
    if incoming.get("operation") not in (None, ""):
        record["current_operation"] = str(incoming["operation"]).zfill(3)

    if incoming.get("mission_step") not in (None, ""):
        record["mission_step"] = int(incoming["mission_step"])

    persistable = {k: v for k, v in record.items() if k != "name"}
    update_player(player_name, **persistable)

    mission = load_mission(record.get("current_operation", "001"))
    stage = stage_for(mission, record.get("mission_step", 0))
    clearance = int(record.get("clearance", 1))
    artifacts = list(record.get("recovered_artifacts", []))
    memories = list(record.get("unlocked_memories", []))

    context = dict(incoming)
    context.update({
        "clearance": clearance,
        "clearance_label": f"LEVEL {_roman(clearance)}",
        "operation": str(mission.get("id", record.get("current_operation", "001"))).zfill(3),
        "mission_step": int(record.get("mission_step", 0)),
        "mission_title": mission.get("title", "UNREGISTERED OPERATION"),
        "mission_status": stage.get("status", "UNKNOWN"),
        "destination": stage.get("destination") or mission.get("destination", "UNKNOWN"),
        "directive": stage.get("directive", "Await further instructions."),
        "warning": stage.get("warning") or mission.get("warning", ""),
        "memory_integrity": int(record.get("memory_integrity", 3)),
        "recovered_archives": f"{len(memories)} / 500",
        "recovered_artifacts": artifacts,
        "unlocked_memories": memories,
        "fracture_visits": int(record.get("fracture_visits", 0)),
    })
    return context


def scoreboard_sync_commands(player_name: str, context: Dict[str, Any]) -> List[str]:
    # Safe mirror: Kairos storage remains the source of truth for Fracture.
    return [
        f"scoreboard players set {player_name} clearance {int(context.get('clearance', 1))}",
        f"scoreboard players set {player_name} operation {int(str(context.get('operation', '001')))}",
        f"scoreboard players set {player_name} mission_step {int(context.get('mission_step', 0))}",
    ]


def submit_artifact(player_name: str, artifact_id: str) -> Dict[str, Any]:
    artifact = load_artifact(artifact_id)
    if not artifact:
        return {"ok": False, "error": "unknown_artifact", "artifact_id": artifact_id}

    record = add_artifact(
        player_name,
        artifact_id=artifact["id"],
        memory_id=artifact.get("restores_memory"),
        integrity_gain=int(artifact.get("memory_integrity_gain", 1)),
    )

    if not record.get("artifact_duplicate"):
        completion = artifact.get("progression", {}) or {}
        changes: Dict[str, Any] = {}
        if "grant_clearance" in completion:
            changes["clearance"] = max(int(record.get("clearance", 1)), int(completion["grant_clearance"]))
        if "set_operation" in completion:
            changes["current_operation"] = str(completion["set_operation"]).zfill(3)
        if "set_mission_step" in completion:
            changes["mission_step"] = int(completion["set_mission_step"])
        if changes:
            record = update_player(player_name, **changes)

    context = build_terminal_context(player_name, increment_visit=False)
    return {
        "ok": True,
        "duplicate": bool(record.get("artifact_duplicate")),
        "artifact": artifact,
        "context": context,
        "commands": scoreboard_sync_commands(player_name, context),
    }
