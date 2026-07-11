"""
inventory_bridge.py

Minecraft inventory interface for Project Nexus.

This module never contains mission logic.

It only knows how to ask Minecraft questions
and request inventory actions through mc_connector.
"""

from __future__ import annotations

from typing import Dict, Any

from mc_connector import send_commands


def player_has_artifact(player: str, artifact_id: str) -> Dict[str, Any]:
    """
    Placeholder inventory scan.

    Version 1 always returns False until the
    Minecraft-side scanner is connected.
    """

    return {
        "success": False,
        "player": player,
        "artifact": artifact_id,
        "found": False,
        "slot": None,
    }


def remove_artifact(player: str, artifact_id: str) -> Dict[str, Any]:
    """
    Removes an artifact from a player's inventory.

    Placeholder for Version 1.
    """

    return {
        "success": False,
        "removed": False,
        "player": player,
        "artifact": artifact_id,
    }


def give_artifact(player: str, artifact_id: str) -> Dict[str, Any]:
    """
    Gives an artifact to a player.

    Placeholder until artifact registry is connected.
    """

    return {
        "success": False,
        "player": player,
        "artifact": artifact_id,
    }


def sync_scoreboards(player: str, clearance: int, operation: str, step: int):
    """
    Sends updated mission values to Minecraft.
    """

    commands = [
        f"scoreboard players set {player} clearance {clearance}",
        f"scoreboard players set {player} operation {int(operation)}",
        f"scoreboard players set {player} mission_step {step}",
    ]

    return send_commands(commands)
