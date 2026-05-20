"""
telemetry_engine.py
Kairos / Nexus Telemetry Engine

Purpose:
- Tracks players, positions, regions, density, stationary/base detection hints.
- Does NOT run loops by itself.
- Stores state through memory_engine/world_state files later.
"""

from __future__ import annotations

import math
import os
import time
import traceback
from typing import Any, Dict, Optional, Tuple

from memory_engine import record_world_event, record_system_error


TELEMETRY_DEBUG = os.getenv("TELEMETRY_DEBUG", "true").lower() == "true"

telemetry_data: Dict[str, Dict[str, Any]] = {}
last_positions: Dict[str, Tuple[str, float, float, float]] = {}
stationary_start: Dict[str, float] = {}
region_density: Dict[str, Dict[str, Any]] = {}


def telemetry_log(message: str, level: str = "INFO") -> None:
    if TELEMETRY_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[TELEMETRY_ENGINE {level}] {message}", flush=True)


def telemetry_log_exception(context: str, exc: Exception) -> None:
    print(f"[TELEMETRY_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()
    try:
        record_system_error(context, str(exc))
    except Exception:
        pass


def region_key(world: str, x: float, z: float, size: int = 32) -> str:
    return f"{world}:{int(x)//size}:{int(z)//size}"


def distance(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)


def update_player_position(
    player: str,
    world: str,
    x: float,
    y: float,
    z: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Main telemetry update point.
    """
    try:
        now = time.time()
        player = str(player or "unknown")
        world = str(world or "world")

        previous = last_positions.get(player)
        moved = True
        movement_distance = None

        if previous:
            prev_world, px, py, pz = previous
            if prev_world == world:
                movement_distance = distance((x, y, z), (px, py, pz))
                moved = movement_distance > 2.0

        if moved:
            stationary_start[player] = now
        else:
            stationary_start.setdefault(player, now)

        stationary_seconds = now - stationary_start.get(player, now)
        key = region_key(world, x, z)

        region = region_density.setdefault(key, {
            "world": world,
            "region": key,
            "density_score": 0.0,
            "visits": 0,
            "last_seen": now,
        })

        region["visits"] += 1
        region["density_score"] = min(200.0, region.get("density_score", 0.0) + 1.0)
        region["last_seen"] = now

        data = {
            "player": player,
            "world": world,
            "x": x,
            "y": y,
            "z": z,
            "region": key,
            "moved": moved,
            "movement_distance": movement_distance,
            "stationary_seconds": stationary_seconds,
            "metadata": metadata or {},
            "timestamp": now,
        }

        telemetry_data[player] = data
        last_positions[player] = (world, x, y, z)

        return data

    except Exception as exc:
        telemetry_log_exception("update_player_position failed", exc)
        return {"ok": False, "error": str(exc)}


def get_player_position(player: str) -> Optional[Dict[str, Any]]:
    return telemetry_data.get(str(player))


def get_region_density(region: str) -> Optional[Dict[str, Any]]:
    return region_density.get(str(region))


def classify_region_density(score: float) -> str:
    if score >= 140:
        return "stronghold"
    if score >= 80:
        return "fortified"
    if score >= 40:
        return "urban"
    if score >= 10:
        return "settled"
    return "frontier"


def detect_base_candidate(player: str, min_stationary_seconds: int = 60) -> Optional[Dict[str, Any]]:
    data = get_player_position(player)
    if not data:
        return None

    if data.get("stationary_seconds", 0) >= min_stationary_seconds:
        event = {
            "player": player,
            "region": data.get("region"),
            "world": data.get("world"),
            "x": data.get("x"),
            "y": data.get("y"),
            "z": data.get("z"),
            "confidence": min(1.0, data.get("stationary_seconds", 0) / 300),
            "timestamp": time.time(),
        }
        return event

    return None


def record_base_if_detected(player: str) -> Optional[Dict[str, Any]]:
    candidate = detect_base_candidate(player)
    if not candidate:
        return None

    record_world_event(
        "base_candidate",
        f"{player} may be anchoring a base in {candidate.get('region')}.",
        location=candidate.get("region"),
        metadata=candidate,
    )

    return candidate


if __name__ == "__main__":
    print(update_player_position("RealSociety5107", "world", 100, 64, 200))
