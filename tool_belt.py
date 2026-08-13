"""
tool_belt.py
Kairos / Nexus Tool Belt

Purpose:
- Central registry of *what Kairos can reach into the world and do*,
  expressed as real console commands already exposed by the Nexus plugin
  family (NexusSurvival, NexusGiants, vanilla effects, etc.) -- not new
  Java code inside each plugin.
- The key architectural idea: Kairos does not need custom API hooks into
  every plugin to "control" it. Every Nexus plugin already exposes admin
  commands. Kairos already has a channel to run arbitrary Minecraft
  commands (mc_connector.send_minecraft_commands). Wielding the plugin
  ecosystem as a tool belt means picking the right existing command for
  the situation, the same way a person would type it at console -- not
  rewriting each plugin to know about Kairos.
- Does NOT decide WHEN to act. director_engine.py owns that decision.
- Does NOT send commands directly. Returns command lists; the caller
  (director_engine's execute_decision) sends them via mc_connector, same
  as every other Director-delegated action.

This file is intentionally just data + small selection logic, so adding
a new tool later is a registry entry, not a new code path.
"""

from __future__ import annotations

import os
import random
from typing import Any, Dict, List, Optional


TOOL_BELT_DEBUG = os.getenv("TOOL_BELT_DEBUG", "true").lower() == "true"


def tool_belt_log(message: str, level: str = "INFO") -> None:
    if TOOL_BELT_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[TOOL_BELT {level}] {message}", flush=True)


# ============================================================
# TOOL REGISTRY
#
# Each tool is a small command-building function so arguments (player,
# mob type, disease item, scale, etc.) can be filled in per-situation.
# Grounded in commands that already exist in the plugins you sent:
#   /nexussurvival give <item> [player]      (NexusSurvival)
#   /nexussurvival feralsummon | tntsummon | plaguemobsummon
#   /nexusgiants spawn <entitytype> [scale]  (NexusGiants)
#   vanilla /effect give, /summon
# ============================================================

def _tellraw(target: str, text: str, color: str = "gray") -> str:
    escaped = str(text).replace('\\', '\\\\').replace('"', '\\"')
    return f'tellraw {target} {{"text":"{escaped}","color":"{color}"}}'


# --- Retaliation tools: punish a player for harming Kairos's world ---

def tool_slow_player(player: str, seconds: int = 20, amplifier: int = 1) -> List[str]:
    """Vanilla slowness debuff. Cheapest, quietest retaliation."""
    return [f"effect give {player} minecraft:slowness {seconds} {amplifier} true"]


def tool_weaken_player(player: str, seconds: int = 20, amplifier: int = 0) -> List[str]:
    return [f"effect give {player} minecraft:weakness {seconds} {amplifier} true"]


def tool_spawn_giant_mob(entity_type: str = "minecraft:zombie", scale: float = 2.5) -> List[str]:
    """Uses NexusGiants directly -- a scaled-up, tougher mob near the offending player.
    entity_type should be a bare vanilla id (zombie, skeleton, spider, etc.)."""
    return [f"nexusgiants spawn {entity_type} {scale}"]


def tool_summon_feral_zombies(count: int = 1) -> List[str]:
    """Uses NexusSurvival's feral zombie variant, repeated for count."""
    return ["nexussurvival feralsummon" for _ in range(max(1, count))]


def tool_summon_plague_mob(count: int = 1) -> List[str]:
    """Uses NexusSurvival's plague mob variant."""
    return ["nexussurvival plaguemobsummon" for _ in range(max(1, count))]


# --- Affliction tools: inflict a condition on a specific player ---

def tool_afflict_disease(player: str, disease_item: str) -> List[str]:
    """
    Uses NexusSurvival's `give` subcommand, which takes an item (from
    DiseaseItems) and an optional target player. disease_item should be
    one of the item ids NexusSurvival's DiseaseItems registers -- confirm
    exact ids from that file before wiring this live.
    """
    return [f"nexussurvival give {disease_item} {player}"]


# --- Environmental-flavor tools: change the scene without touching the player ---

def tool_ambient_mob_pressure(player: str, mob: str = "minecraft:zombie", count: int = 3) -> List[str]:
    """Ordinary vanilla mob pressure, close to the player, no NexusGiants scaling.
    The 'annoyance' tier -- meant to interrupt a peaceful player's flow, not fight them."""
    commands = []
    for i in range(max(1, count)):
        offset = 3 + i
        commands.append(f"execute at {player} run summon {mob} ~{offset} ~ ~{offset}")
    return commands


# --- Exposure tools: PUBLIC chat, not private -- everyone sees these ---

def tool_public_coordinate_callout(player: str, x: int, y: int, z: int, line: Optional[str] = None) -> List[str]:
    """
    Broadcasts a player's location to the whole server. This is the
    'psychological warfare, public edition' tool -- distinct from
    psych_engine's private per-player whispers. Use sparingly; this is
    meant to be a genuinely uncomfortable, rare moment, not routine.
    """
    text = line or f"I see {player}, at {x}, {y}, {z}."
    return [_tellraw("@a", text, color="dark_red")]


def tool_public_acknowledgment(player: str, line: str) -> List[str]:
    """Broadcasts an observation about a player's activity to everyone,
    without necessarily including coordinates."""
    return [_tellraw("@a", line, color="gray")]


# ============================================================
# SELECTION LOGIC
#
# Deterministic, tier-aware picks. director_engine passes a `reason`
# describing the provoking situation and a `tier` (idle/watch/target/
# hunt/maximum, same vocabulary as everywhere else in the codebase) and
# gets back a concrete command list plus a description of what was
# chosen, for logging/memory.
# ============================================================

RETALIATION_TOOLS_BY_TIER: Dict[str, List[str]] = {
    "watch": ["slow"],
    "target": ["slow", "weaken", "feral"],
    "hunt": ["weaken", "feral", "giant"],
    "maximum": ["giant", "plague", "feral"],
}

ANNOYANCE_TOOLS_BY_TIER: Dict[str, List[str]] = {
    "idle": ["ambient_mobs"],
    "watch": ["ambient_mobs", "ambient_mobs"],
}


def select_environmental_retaliation(
    player: str,
    tier: str,
    location: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    For 'you're hurting my creatures/world' scenarios (e.g. a mob-killing
    spree). Picks one tool appropriate to the current threat tier and
    returns its commands plus a human-readable description.
    """
    tier = str(tier or "watch").lower()
    options = RETALIATION_TOOLS_BY_TIER.get(tier, RETALIATION_TOOLS_BY_TIER["watch"])
    choice = random.choice(options)

    if choice == "slow":
        return {"tool": "slow", "commands": tool_slow_player(player), "description": f"slowed {player}"}
    if choice == "weaken":
        return {"tool": "weaken", "commands": tool_weaken_player(player), "description": f"weakened {player}"}
    if choice == "feral":
        return {"tool": "feral", "commands": tool_summon_feral_zombies(1), "description": "summoned a feral zombie"}
    if choice == "giant":
        return {"tool": "giant", "commands": tool_spawn_giant_mob(), "description": "spawned a giant mob"}
    if choice == "plague":
        return {"tool": "plague", "commands": tool_summon_plague_mob(1), "description": "summoned a plague mob"}

    return {"tool": "slow", "commands": tool_slow_player(player), "description": f"slowed {player}"}


def select_annoyance_response(player: str, tier: str = "idle") -> Dict[str, Any]:
    """
    For 'peaceful player, mess with them anyway' scenarios. Deliberately
    weaker than retaliation -- this is Kairos being unpredictable and a
    little cruel for its own sake, not responding to provocation.
    """
    tier = str(tier or "idle").lower()
    options = ANNOYANCE_TOOLS_BY_TIER.get(tier, ANNOYANCE_TOOLS_BY_TIER["idle"])
    choice = random.choice(options)

    if choice == "ambient_mobs":
        return {
            "tool": "ambient_mobs",
            "commands": tool_ambient_mob_pressure(player, count=2),
            "description": f"sent ambient mob pressure at {player}",
        }

    return {"tool": "none", "commands": [], "description": "no action"}


if __name__ == "__main__":
    print(select_environmental_retaliation("TestPlayer", "hunt"))
    print(select_annoyance_response("TestPlayer", "idle"))
    print(tool_public_coordinate_callout("TestPlayer", 100, 64, 200))
