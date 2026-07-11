"""
repository_bridge.py

F.R.A.C.T.U.R.E Repository Bridge

Monitors the official Repository Chest and validates
artifact submissions before forwarding them to the
artifact processor.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from artifact_processor import process_artifact_submission

# ============================================================
# REPOSITORY LOCATION
# ============================================================

REPOSITORY = {
    "world": "minecraft:overworld",
    "x": -16647,
    "y": 72,
    "z": 9648,
}

# ============================================================
# HELPERS
# ============================================================

def repository_matches(world: str, x: int, y: int, z: int) -> bool:
    return (
        world == REPOSITORY["world"]
        and int(x) == REPOSITORY["x"]
        and int(y) == REPOSITORY["y"]
        and int(z) == REPOSITORY["z"]
    )


def artifact_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    """
    Determines the registered artifact ID from an item.

    Priority:
        1. custom_nbt.artifact_id
        2. artifact_id
        3. lore
        4. display_name
    """

    if not item:
        return None

    nbt = item.get("custom_nbt") or {}

    if isinstance(nbt, dict):
        artifact = nbt.get("artifact_id")
        if artifact:
            return str(artifact).strip().lower()

    artifact = item.get("artifact_id")
    if artifact:
        return str(artifact).strip().lower()

    lore = item.get("lore") or []

    if isinstance(lore, list):
        for line in lore:
            line = str(line).lower()

            if "artifact_" in line:
                start = line.index("artifact_")
                return line[start:].strip()

    name = str(item.get("display_name", "")).lower()

    if "archive access badge" in name:
        return "artifact_001_archive_access_badge"

    return None


# ============================================================
# MAIN ENTRY
# ============================================================

def process_repository_submission(
    *,
    player_name: str,
    world: str,
    x: int,
    y: int,
    z: int,
    item: Dict[str, Any],
) -> Dict[str, Any]:

    if not repository_matches(world, x, y, z):

        return {
            "ok": False,
            "reason": "not_repository",
        }

    artifact_id = artifact_id_from_item(item)

    if artifact_id is None:

        return {
            "ok": False,
            "reason": "not_registered_artifact",
        }

    result = process_artifact_submission(
        player_name=player_name,
        artifact_id=artifact_id,
    )

    return result


# ============================================================
# STATUS
# ============================================================

def repository_status() -> Dict[str, Any]:

    return {
        "online": True,
        "repository": REPOSITORY,
    }
