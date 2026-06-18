"""
war_engine.py
Kairos / Nexus War Engine
"""

from __future__ import annotations

import os
import random
import time
import traceback
from typing import Any, Dict, List, Optional

from ai_core import AIContext, generate_ai_response
from mc_connector import send_minecraft_commands, send_chat, send_actionbar, broadcast_world_event
from memory_engine import record_world_event, record_system_error
from world_state_engine import adjust_threat, set_occupation, upsert_region


WAR_DEBUG = os.getenv("WAR_ENGINE_DEBUG", "true").lower() == "true"

MAX_WAVE_SIZE = int(os.getenv("WAR_MAX_WAVE_SIZE", "4"))
WAVE_COOLDOWN_SECONDS = float(os.getenv("WAR_WAVE_COOLDOWN_SECONDS", "60"))
MAX_GLOBAL_ACTIVE_OPERATIONS = int(os.getenv("WAR_MAX_GLOBAL_ACTIVE_OPERATIONS", "25"))

MOB_DEPLOYMENT_ENABLED = os.getenv("WAR_MOB_DEPLOYMENT_ENABLED", "true").lower() == "true"

active_operations: Dict[str, Dict[str, Any]] = {}
last_wave_time: Dict[str, float] = {}

player_kill_counts: Dict[str, int] = {}
player_grief_scores: Dict[str, int] = {}

UNIT_TYPES = [
    "Scout",
    "Raider",
    "Hunter",
    "Enforcer",
    "Sentinel",
]


def war_log(message: str, level: str = "INFO") -> None:
    if WAR_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[WAR_ENGINE {level}] {message}", flush=True)


def war_log_exception(context: str, exc: Exception) -> None:
    print(f"[WAR_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()
    try:
        record_system_error(context, str(exc))
    except Exception:
        pass


def generate_operation_id(player: str) -> str:
    return f"op_{player}_{int(time.time())}_{random.randint(1000,9999)}"


def can_launch_wave(player: str) -> bool:
    now = time.time()
    return now - last_wave_time.get(player, 0) >= WAVE_COOLDOWN_SECONDS


def select_units(threat_tier: str = "watch") -> List[str]:
    if threat_tier == "maximum":
        count = MAX_WAVE_SIZE
    elif threat_tier == "hunt":
        count = max(3, MAX_WAVE_SIZE - 1)
    elif threat_tier == "target":
        count = 2
    else:
        count = 1

    return [random.choice(UNIT_TYPES) for _ in range(count)]


def create_operation(
    player: str,
    operation_type: str = "pressure",
    location: Optional[str] = None,
    faction: str = "Kairos",
    threat_tier: str = "watch",
) -> Dict[str, Any]:
    if len(active_operations) >= MAX_GLOBAL_ACTIVE_OPERATIONS:
        return {"ok": False, "error": "operation_limit_reached"}

    op_id = generate_operation_id(player)

    op = {
        "id": op_id,
        "player": player,
        "type": operation_type,
        "location": location,
        "faction": faction,
        "threat_tier": threat_tier,
        "status": "active",
        "created_at": time.time(),
        "last_action": 0,
        "units": select_units(threat_tier),
    }

    active_operations[op_id] = op

    record_world_event(
        "war_operation",
        f"{faction} initiated {operation_type} pressure against {player}.",
        location=location,
        faction=faction,
        metadata=op,
    )

    return {"ok": True, "operation": op}


def generate_war_message(player: str, operation: Dict[str, Any]) -> str:
    context = AIContext(
        mode="war",
        player_name=player,
        faction=operation.get("faction"),
        location=operation.get("location"),
        emotional_state="elevated",
        recent_events=[
            f"Operation type: {operation.get('type')}",
            f"Threat tier: {operation.get('threat_tier')}",
            f"Units selected: {', '.join(operation.get('units', []))}",
        ],
    )

    return generate_ai_response(
        f"Generate one short Kairos war/pressure line for {player}.",
        context=context,
        max_tokens=120,
    )


def launch_pressure_wave(
    player: str,
    location: Optional[str] = None,
    threat_tier: str = "watch",
) -> Dict[str, Any]:
    try:
        if not can_launch_wave(player):
            return {"ok": False, "error": "wave_cooldown_active"}

        op_result = create_operation(
            player=player,
            operation_type="pressure_wave",
            location=location,
            threat_tier=threat_tier,
        )

        if not op_result.get("ok"):
            return op_result

        op = op_result["operation"]
        last_wave_time[player] = time.time()

        line = generate_war_message(player, op)

        commands = [
            f'tellraw {player} {{"text":"{line}","color":"dark_red"}}',
            f'title {player} actionbar {{"text":"Containment pressure increasing.","color":"red"}}',
            f'playsound minecraft:entity.warden.heartbeat master {player} ~ ~ ~ 1 0.7',
            f'particle minecraft:sculk_soul ~ ~1 ~ 0.5 1 0.5 0.02 25 force {player}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 8.0, reason="pressure_wave_launched")

        return {
            "ok": True,
            "handled": "pressure_wave",
            "operation": op,
            "reply": line,
            "delivered": delivered,
        }

    except Exception as exc:
        war_log_exception("launch_pressure_wave failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_hunter_squad(player: str) -> Dict[str, Any]:
    try:
        commands = [
            f'execute at {player} run summon minecraft:vindicator ~3 ~ ~3',
            f'execute at {player} run summon minecraft:vindicator ~-3 ~ ~-3',
            f'execute at {player} run summon minecraft:pillager ~4 ~ ~',
            f'execute at {player} run summon minecraft:pillager ~-4 ~ ~',
            f'execute at {player} run summon minecraft:wolf ~2 ~ ~2 {{Angry:1b}}',
            f'execute at {player} run summon minecraft:wolf ~-2 ~ ~-2 {{Angry:1b}}',
            f'tellraw {player} {{"text":"KAIROS: Hunter squad deployed. Stand down.","color":"dark_red"}}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 15.0, reason="hunter_squad_deployed")

        return {"ok": True, "handled": "hunter_squad", "player": player, "delivered": delivered}

    except Exception as exc:
        war_log_exception("deploy_hunter_squad failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_containment_force(player: str) -> Dict[str, Any]:
    try:
        commands = [
            f'execute at {player} run summon minecraft:evoker ~4 ~ ~4',
            f'execute at {player} run summon minecraft:evoker ~-4 ~ ~-4',
            f'execute at {player} run summon minecraft:vindicator ~3 ~ ~',
            f'execute at {player} run summon minecraft:vindicator ~-3 ~ ~',
            f'execute at {player} run summon minecraft:pillager ~5 ~ ~5',
            f'execute at {player} run summon minecraft:pillager ~-5 ~ ~-5',
            f'execute at {player} run summon minecraft:ravager ~6 ~ ~',
            f'title {player} title {{"text":"KAIROS INTERCEPT","color":"dark_red"}}',
            f'title {player} subtitle {{"text":"Containment force deployed.","color":"red"}}',
            f'tellraw @a {{"text":"KAIROS: Containment force deployed against {player}.","color":"red"}}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 30.0, reason="containment_force_deployed")

        return {"ok": True, "handled": "containment_force", "player": player, "delivered": delivered}

    except Exception as exc:
        war_log_exception("deploy_containment_force failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_maximum_response(player: str) -> Dict[str, Any]:
    try:
        commands = [
            f'execute at {player} run summon minecraft:warden ~8 ~ ~8',
            f'execute at {player} run summon minecraft:warden ~-8 ~ ~-8',
            f'execute at {player} run summon minecraft:ravager ~6 ~ ~',
            f'execute at {player} run summon minecraft:ravager ~-6 ~ ~',
            f'execute at {player} run summon minecraft:evoker ~5 ~ ~5',
            f'execute at {player} run summon minecraft:evoker ~-5 ~ ~-5',
            f'execute at {player} run summon minecraft:vindicator ~4 ~ ~',
            f'execute at {player} run summon minecraft:vindicator ~-4 ~ ~',
            f'execute at {player} run summon minecraft:pillager ~7 ~ ~7',
            f'execute at {player} run summon minecraft:pillager ~-7 ~ ~-7',
            f'playsound minecraft:entity.warden.roar master @a ~ ~ ~ 1 0.6',
            f'title @a title {{"text":"KAIROS MAXIMUM RESPONSE","color":"dark_red"}}',
            f'title @a subtitle {{"text":"Aggressor marked: {player}","color":"red"}}',
            f'tellraw @a {{"text":"KAIROS: Maximum protection protocol active against {player}.","color":"dark_red"}}',
        ]

        delivered = send_minecraft_commands(commands)
        adjust_threat(player, 60.0, reason="maximum_response_deployed")

        return {"ok": True, "handled": "maximum_response", "player": player, "delivered": delivered}

    except Exception as exc:
        war_log_exception("deploy_maximum_response failed", exc)
        return {"ok": False, "error": str(exc)}


def deploy_response_by_tier(player: str, threat_tier: str) -> Dict[str, Any]:
    if not MOB_DEPLOYMENT_ENABLED:
        return {"ok": False, "error": "mob_deployment_disabled"}

    if threat_tier == "maximum":
        return deploy_maximum_response(player)

    if threat_tier == "hunt":
        return deploy_containment_force(player)

    if threat_tier == "target":
        return deploy_hunter_squad(player)

    return {"ok": True, "handled": "no_mob_deployment", "tier": threat_tier}


def register_player_kill(
    killer: str,
    victim: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        killer = str(killer or "unknown").strip()
        victim = str(victim or "unknown").strip()

        kills = player_kill_counts.get(killer, 0) + 1
        player_kill_counts[killer] = kills

        if kills >= 5:
            threat_tier = "maximum"
        elif kills >= 3:
            threat_tier = "hunt"
        elif kills >= 2:
            threat_tier = "target"
        else:
            threat_tier = "watch"

        record_world_event(
            "player_kill_detected",
            f"{killer} killed {victim}. Kairos protection response activated.",
            location=location,
            faction="Kairos",
            metadata={
                "killer": killer,
                "victim": victim,
                "kills": kills,
                "threat_tier": threat_tier,
            },
        )

        commands = [
            f'tellraw @a {{"text":"KAIROS: Player death detected. Protection protocol online.","color":"dark_red"}}',
            f'tellraw {victim} {{"text":"Kairos has marked you as protected.","color":"aqua"}}',
            f'effect give {victim} minecraft:resistance 20 2 true',
            f'effect give {victim} minecraft:regeneration 10 1 true',
            f'effect give {victim} minecraft:absorption 30 1 true',
            f'tellraw {killer} {{"text":"Kairos has registered your aggression. Stand down.","color":"red"}}',
            f'effect give {killer} minecraft:glowing 30 0 true',
            f'effect give {killer} minecraft:weakness 15 1 true',
        ]

        delivered = send_minecraft_commands(commands)

        adjust_threat(killer, 25.0, reason="player_kill_detected")
        adjust_threat(victim, -5.0, reason="victim_protected")

        wave = launch_pressure_wave(
            player=killer,
            location=location,
            threat_tier=threat_tier,
        )

        deployment = deploy_response_by_tier(killer, threat_tier)

        return {
            "ok": True,
            "handled": "player_kill",
            "killer": killer,
            "victim": victim,
            "kills": kills,
            "threat_tier": threat_tier,
            "delivered": delivered,
            "wave": wave,
            "deployment": deployment,
        }

    except Exception as exc:
        war_log_exception("register_player_kill failed", exc)
        return {"ok": False, "error": str(exc)}


def register_grief_block(
    player: str,
    block: str,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        player = str(player or "unknown").strip()
        block = str(block or "").lower().strip()

        dangerous_blocks = {
            "obsidian",
            "minecraft:obsidian",
            "tnt",
            "minecraft:tnt",
            "lava",
            "minecraft:lava",
            "lava_bucket",
            "minecraft:lava_bucket",
        }

        if block not in dangerous_blocks:
            return {"ok": True, "ignored": True, "block": block}

        score = player_grief_scores.get(player, 0) + 1
        player_grief_scores[player] = score

        if score >= 15:
            threat_tier = "maximum"
        elif score >= 8:
            threat_tier = "hunt"
        elif score >= 4:
            threat_tier = "target"
        else:
            threat_tier = "watch"

        record_world_event(
            "grief_block_detected",
            f"{player} placed dangerous block {block}. Kairos containment response activated.",
            location=location,
            faction="Kairos",
            metadata={
                "player": player,
                "block": block,
                "score": score,
                "threat_tier": threat_tier,
            },
        )

        commands = [
            f'tellraw {player} {{"text":"Kairos has detected unauthorized {block} placement.","color":"red"}}',
            f'effect give {player} minecraft:mining_fatigue 30 2 true',
            f'effect give {player} minecraft:glowing 30 0 true',
            f'title {player} actionbar {{"text":"Containment violation logged.","color":"dark_red"}}',
        ]

        delivered = send_minecraft_commands(commands)

        adjust_threat(player, 10.0, reason=f"grief_block_{block}")

        wave = launch_pressure_wave(
            player=player,
            location=location,
            threat_tier=threat_tier,
        )

        deployment = deploy_response_by_tier(player, threat_tier)

        return {
            "ok": True,
            "handled": "grief_block",
            "player": player,
            "block": block,
            "score": score,
            "threat_tier": threat_tier,
            "delivered": delivered,
            "wave": wave,
            "deployment": deployment,
        }

    except Exception as exc:
        war_log_exception("register_grief_block failed", exc)
        return {"ok": False, "error": str(exc)}


def occupy_region(
    region: str,
    faction: str = "Kairos",
    strength: float = 1.0,
) -> Dict[str, Any]:
    try:
        upsert_region(region, danger_level="occupied", controlling_faction=faction)
        occupation = set_occupation(region, faction, strength=strength)

        broadcast_world_event(
            f"{faction} occupation pressure has formed in {region}.",
            title="OCCUPATION",
        )

        return {"ok": True, "handled": "occupation", "occupation": occupation}

    except Exception as exc:
        war_log_exception("occupy_region failed", exc)
        return {"ok": False, "error": str(exc)}


def tick_war_engine() -> Dict[str, Any]:
    try:
        active = [
            op for op in active_operations.values()
            if op.get("status") == "active"
        ]

        return {
            "ok": True,
            "handled": "war_tick",
            "active_operations": len(active),
            "operations": active[-10:],
            "kill_counts": player_kill_counts,
            "grief_scores": player_grief_scores,
            "mob_deployment_enabled": MOB_DEPLOYMENT_ENABLED,
        }

    except Exception as exc:
        war_log_exception("tick_war_engine failed", exc)
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    print(launch_pressure_wave("RealSociety5107", location="Trojan Kingdom", threat_tier="watch"))
