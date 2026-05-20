"""
war_engine.py
Kairos / Nexus War Engine

Purpose:
- Restores the missing combat / threat / occupation system as its own module.
- Does NOT auto-loop forever.
- Does NOT run Flask.
- Does NOT own Minecraft transport directly except through mc_connector.
- Designed to be called safely by command_bridge.py or future schedulers.

This is the military/pressure organ of Kairos.
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

active_operations: Dict[str, Dict[str, Any]] = {}
last_wave_time: Dict[str, float] = {}


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
    """
    Safe command wave.
    This intentionally does NOT create Citizens NPCs yet.
    It announces pressure and applies light effects.
    Full spawning can be added later once connector/plugin details are stable.
    """
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
    """
    One safe manual tick.
    Does not loop forever.
    """
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
        }

    except Exception as exc:
        war_log_exception("tick_war_engine failed", exc)
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    print(launch_pressure_wave("RealSociety5107", location="Trojan Kingdom", threat_tier="watch"))
