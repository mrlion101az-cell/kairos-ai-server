"""
world_state_engine.py
Kairos / Nexus World State Engine

Purpose:
- Central persistent-ish world state model for factions, threats, regions, occupations.
- Small, safe, and modular.
- Heavy war actions belong in war_engine.py.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from memory_engine import load_world_memory, save_world_memory, record_world_event


def get_world_state() -> Dict[str, Any]:
    data = load_world_memory()
    data.setdefault("factions", {})
    data.setdefault("regions", {})
    data.setdefault("occupations", {})
    data.setdefault("threats", {})
    return data


def save_world_state(data: Dict[str, Any]) -> bool:
    return save_world_memory(data)


def upsert_faction(
    key: str,
    name: Optional[str] = None,
    alignment: str = "neutral",
    influence: float = 0.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = get_world_state()
    factions = data.setdefault("factions", {})

    item = factions.setdefault(key, {
        "key": key,
        "name": name or key,
        "alignment": alignment,
        "influence": influence,
        "metadata": {},
        "created_at": time.time(),
    })

    item["name"] = name or item.get("name") or key
    item["alignment"] = alignment or item.get("alignment", "neutral")
    item["influence"] = float(influence if influence is not None else item.get("influence", 0.0))
    item["metadata"].update(metadata or {})
    item["updated_at"] = time.time()

    save_world_state(data)
    return item


def upsert_region(
    key: str,
    name: Optional[str] = None,
    danger_level: str = "unknown",
    controlling_faction: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = get_world_state()
    regions = data.setdefault("regions", {})

    item = regions.setdefault(key, {
        "key": key,
        "name": name or key,
        "danger_level": danger_level,
        "controlling_faction": controlling_faction,
        "metadata": {},
        "created_at": time.time(),
    })

    item["name"] = name or item.get("name") or key
    item["danger_level"] = danger_level or item.get("danger_level", "unknown")
    item["controlling_faction"] = controlling_faction or item.get("controlling_faction")
    item["metadata"].update(metadata or {})
    item["updated_at"] = time.time()

    save_world_state(data)
    return item


def set_threat(player: str, score: float, reason: str = "") -> Dict[str, Any]:
    data = get_world_state()
    threats = data.setdefault("threats", {})

    item = threats.setdefault(player, {
        "player": player,
        "score": 0.0,
        "tier": "idle",
        "history": [],
    })

    item["score"] = max(0.0, float(score))

    if item["score"] >= 160:
        item["tier"] = "maximum"
    elif item["score"] >= 95:
        item["tier"] = "hunt"
    elif item["score"] >= 45:
        item["tier"] = "target"
    elif item["score"] >= 20:
        item["tier"] = "watch"
    else:
        item["tier"] = "idle"

    item.setdefault("history", []).append({
        "score": item["score"],
        "tier": item["tier"],
        "reason": reason,
        "timestamp": time.time(),
    })

    item["history"] = item["history"][-100:]
    item["updated_at"] = time.time()

    save_world_state(data)
    return item


def adjust_threat(player: str, amount: float, reason: str = "") -> Dict[str, Any]:
    current = get_world_state().get("threats", {}).get(player, {}).get("score", 0.0)
    return set_threat(player, current + amount, reason=reason)


def set_occupation(
    region: str,
    faction: str,
    strength: float = 1.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = get_world_state()
    occupations = data.setdefault("occupations", {})

    item = {
        "region": region,
        "faction": faction,
        "strength": float(strength),
        "metadata": metadata or {},
        "timestamp": time.time(),
    }

    occupations[region] = item
    save_world_state(data)

    record_world_event(
        "occupation",
        f"{faction} established occupation pressure in {region}.",
        location=region,
        faction=faction,
        metadata=item,
    )

    return item


if __name__ == "__main__":
    print(upsert_faction("trojan_kingdom", name="Trojan Kingdom", alignment="guarded"))
