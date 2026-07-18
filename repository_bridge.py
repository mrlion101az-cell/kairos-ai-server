"""
repository_bridge.py

F.R.A.C.T.U.R.E Repository Bridge

Monitors the official Repository Chest and validates
artifact submissions before forwarding them to the
artifact processor.

This is the "Kairos is the authority" path: it expects a raw item
(already normalized by inventory_bridge.normalize_item, i.e. with
material/custom_name/lore/custom_data keys) plus a location, checks
that location against the real repository chest, resolves which
registered artifact the item matches, and forwards to
artifact_processor.process_artifact_submission() -- which will itself
re-check the player's live inventory snapshot and issue the removal
command via mc_connector.

This is a SEPARATE path from the NexusBridge Minecraft plugin's
/repository_event route, which already validates and removes the item
itself and calls process_repository_confirmation() instead. Use this
module for any integration where Kairos, not the plugin, must be the
one deciding whether the item is really there.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

from artifact_processor import process_artifact_submission

try:
    from inventory_bridge import artifact_match_rules, item_matches_artifact
except Exception as exc:
    artifact_match_rules = None
    item_matches_artifact = None
    print(f"[REPOSITORY_BRIDGE ERROR] inventory_bridge import failed: {exc}", flush=True)

# ============================================================
# REPOSITORY LOCATION
#
# Must match the repository.locations entry in the NexusBridge
# plugin's config.yml on the Minecraft server exactly (world name,
# not a namespaced key -- the plugin sends the plain world name,
# e.g. "nexsus", not "minecraft:overworld").
# ============================================================

REPOSITORY = {
    "world": "nexsus",
    "x": -16647,
    "y": 72,
    "z": 9648,
}

# Directory containing artifact_*.json definitions, same files the
# Minecraft plugin bundles under plugins/NexusBridge/artifacts/.
ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


# ============================================================
# HELPERS
# ============================================================

def repository_matches(world: str, x: int, y: int, z: int) -> bool:
    world = str(world or "").strip().lower()
    expected_world = str(REPOSITORY["world"]).strip().lower()

    # Accept both a bare world name ("nexsus") and a namespaced key
    # ("minecraft:nexsus"), in case a future caller sends either form.
    if ":" in world:
        world = world.split(":", 1)[1]

    return (
        world == expected_world
        and int(x) == REPOSITORY["x"]
        and int(y) == REPOSITORY["y"]
        and int(z) == REPOSITORY["z"]
    )


def _load_all_artifacts() -> List[Dict[str, Any]]:
    """
    Loads every artifact_*.json in ARTIFACTS_DIR. Returns an empty list
    (rather than raising) if the directory is missing or a file fails
    to parse, so one bad file can't take down repository matching.
    """
    artifacts: List[Dict[str, Any]] = []

    if not os.path.isdir(ARTIFACTS_DIR):
        print(f"[REPOSITORY_BRIDGE WARN] artifacts directory not found: {ARTIFACTS_DIR}", flush=True)
        return artifacts

    for path in glob.glob(os.path.join(ARTIFACTS_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("id"):
                artifacts.append(data)
        except Exception as exc:
            print(f"[REPOSITORY_BRIDGE WARN] failed to parse {path}: {exc}", flush=True)

    return artifacts


def artifact_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    """
    Determines the registered artifact ID from a normalized item
    (the shape inventory_bridge.normalize_item() produces: material,
    custom_name, lore, custom_data).

    Priority:
        1. custom_data.artifact_id (exact tag written by the plugin's
           ArtifactFactory, when present)
        2. Full match against every known artifact's material +
           custom_name + lore, using the same matching engine
           inventory_bridge already uses elsewhere.
    """

    if not item:
        return None

    custom_data = item.get("custom_data") or {}
    if isinstance(custom_data, dict):
        tagged = custom_data.get("artifact_id")
        if tagged:
            return str(tagged).strip().lower()

    if item_matches_artifact is None:
        print("[REPOSITORY_BRIDGE WARN] inventory_bridge matcher unavailable; cannot resolve artifact.", flush=True)
        return None

    for artifact in _load_all_artifacts():
        if item_matches_artifact(item, artifact):
            return str(artifact.get("id")).strip().lower()

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
        "known_artifacts": len(_load_all_artifacts()),
    }
