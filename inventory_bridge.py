"""
inventory_bridge.py

Project Nexus inventory interface.

This module does not contain mission logic.

It receives inventory snapshots/events from Minecraft, normalizes item data,
matches Project Nexus artifacts by:
- custom_data / persistent data
- custom name
- lore
- Minecraft material

It can also request inventory changes through mc_connector.py.

IMPORTANT:
The current HTTP-Commands plugin is confirmed to execute command batches.
Inventory snapshots still need to be POSTed into Kairos by a Minecraft-side
listener, script, command plugin, or future custom plugin. This module is ready
to consume those payloads as soon as they arrive.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

try:
    from mc_connector import send_minecraft_commands
except Exception as exc:
    send_minecraft_commands = None
    print(f"[INVENTORY_BRIDGE ERROR] mc_connector import failed: {exc}", flush=True)


INVENTORY_BRIDGE_DEBUG = os.getenv(
    "INVENTORY_BRIDGE_DEBUG",
    "true",
).lower() == "true"

INVENTORY_CACHE_TTL_SECONDS = float(
    os.getenv("INVENTORY_CACHE_TTL_SECONDS", "30")
)

_LOCK = threading.RLock()
_INVENTORY_CACHE: Dict[str, Dict[str, Any]] = {}


def _log(message: str, level: str = "INFO") -> None:
    if INVENTORY_BRIDGE_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[INVENTORY_BRIDGE {level}] {message}", flush=True)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_text(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"§[0-9A-FK-ORa-fk-or]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _normalized_material(value: Any) -> str:
    material = _normalized_text(value)
    if not material:
        return ""
    if ":" not in material:
        material = f"minecraft:{material}"
    return material


def _normalize_custom_data(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {"raw": raw}

    return {}


def _normalize_lore(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return [_normalized_text(value)] if value.strip() else []

    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        return [
            _normalized_text(item)
            for item in value
            if _clean_text(item)
        ]

    return []


def normalize_item(raw_item: Any, slot: Optional[int] = None) -> Dict[str, Any]:
    """
    Convert different Minecraft/plugin payload shapes into one stable item shape.
    """
    if not isinstance(raw_item, dict):
        raw_item = {}

    components = raw_item.get("components")
    if not isinstance(components, dict):
        components = {}

    meta = raw_item.get("meta")
    if not isinstance(meta, dict):
        meta = {}

    custom_data = (
        raw_item.get("custom_data")
        or raw_item.get("customData")
        or raw_item.get("nbt")
        or raw_item.get("persistent_data")
        or raw_item.get("persistentData")
        or components.get("minecraft:custom_data")
        or components.get("custom_data")
        or meta.get("custom_data")
        or meta.get("persistent_data")
        or {}
    )

    custom_name = (
        raw_item.get("custom_name")
        or raw_item.get("customName")
        or raw_item.get("display_name")
        or raw_item.get("displayName")
        or raw_item.get("name")
        or components.get("minecraft:custom_name")
        or components.get("custom_name")
        or meta.get("display_name")
        or meta.get("displayName")
        or ""
    )

    lore = (
        raw_item.get("lore")
        or components.get("minecraft:lore")
        or components.get("lore")
        or meta.get("lore")
        or []
    )

    material = (
        raw_item.get("material")
        or raw_item.get("type")
        or raw_item.get("item")
        or raw_item.get("id")
        or raw_item.get("minecraft_item")
        or ""
    )

    amount = raw_item.get("amount", raw_item.get("count", 1))

    try:
        amount = max(0, int(amount))
    except Exception:
        amount = 1

    resolved_slot = raw_item.get("slot", slot)
    try:
        resolved_slot = int(resolved_slot) if resolved_slot is not None else None
    except Exception:
        resolved_slot = None

    return {
        "slot": resolved_slot,
        "material": _normalized_material(material),
        "amount": amount,
        "custom_name": _normalized_text(custom_name),
        "lore": _normalize_lore(lore),
        "custom_data": _normalize_custom_data(custom_data),
        "raw": deepcopy(raw_item),
    }


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        source_items = payload
    elif isinstance(payload, dict):
        source_items = (
            payload.get("items")
            or payload.get("inventory")
            or payload.get("contents")
            or payload.get("slots")
            or []
        )

        if isinstance(source_items, dict):
            rebuilt: List[Dict[str, Any]] = []
            for slot_key, item in source_items.items():
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("slot", slot_key)
                    rebuilt.append(item)
            source_items = rebuilt
    else:
        source_items = []

    if not isinstance(source_items, list):
        return []

    return [
        normalize_item(item, slot=index)
        for index, item in enumerate(source_items)
        if isinstance(item, dict)
    ]


def record_inventory_snapshot(
    player_name: str,
    payload: Any,
    *,
    source: str = "minecraft",
) -> Dict[str, Any]:
    """
    Store the latest inventory snapshot received from Minecraft.
    """
    player_name = _clean_text(player_name)
    if not player_name:
        return {
            "ok": False,
            "error": "missing_player_name",
        }

    items = _extract_items(payload)
    snapshot = {
        "player": player_name,
        "source": source,
        "items": items,
        "recorded_at": time.time(),
    }

    with _LOCK:
        _INVENTORY_CACHE[player_name] = snapshot

    _log(f"Snapshot recorded player={player_name} items={len(items)} source={source}")

    return {
        "ok": True,
        "handled": "inventory_snapshot",
        "player": player_name,
        "item_count": len(items),
        "snapshot": deepcopy(snapshot),
    }


def get_inventory_snapshot(
    player_name: str,
    *,
    allow_stale: bool = False,
) -> Optional[Dict[str, Any]]:
    player_name = _clean_text(player_name)

    with _LOCK:
        snapshot = deepcopy(_INVENTORY_CACHE.get(player_name))

    if not snapshot:
        return None

    age = max(0.0, time.time() - float(snapshot.get("recorded_at", 0)))

    if not allow_stale and age > INVENTORY_CACHE_TTL_SECONDS:
        _log(
            f"Snapshot stale player={player_name} age={age:.1f}s "
            f"ttl={INVENTORY_CACHE_TTL_SECONDS:.1f}s",
            "WARN",
        )
        return None

    snapshot["age_seconds"] = age
    return snapshot


def _deep_contains(container: Any, expected: Any) -> bool:
    """
    Partial recursive dictionary match.

    Artifact definitions can require only the custom-data keys they care about.
    """
    if isinstance(expected, dict):
        if not isinstance(container, dict):
            return False

        for key, expected_value in expected.items():
            if key not in container:
                return False
            if not _deep_contains(container[key], expected_value):
                return False
        return True

    if isinstance(expected, list):
        if not isinstance(container, list):
            return False
        return all(
            any(_deep_contains(candidate, required) for candidate in container)
            for required in expected
        )

    return container == expected


def artifact_match_rules(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build stable matching rules from an artifact JSON record.
    """
    match = artifact.get("match")
    if not isinstance(match, dict):
        match = {}

    custom_data = (
        match.get("custom_data")
        or match.get("customData")
        or artifact.get("custom_data")
        or artifact.get("customData")
        or {}
    )

    custom_name = (
        match.get("custom_name")
        or match.get("customName")
        or artifact.get("custom_name")
        or artifact.get("display_name")
        or ""
    )

    lore = (
        match.get("lore")
        or artifact.get("lore")
        or []
    )

    material = (
        match.get("material")
        or match.get("minecraft_item")
        or artifact.get("minecraft_item")
        or artifact.get("material")
        or ""
    )

    require_all_lore = bool(
        match.get("require_all_lore", True)
    )

    return {
        "artifact_id": _clean_text(artifact.get("id")).lower(),
        "material": _normalized_material(material),
        "custom_name": _normalized_text(custom_name),
        "lore": _normalize_lore(lore),
        "custom_data": _normalize_custom_data(custom_data),
        "require_all_lore": require_all_lore,
    }


def item_matches_artifact(
    item: Dict[str, Any],
    artifact: Dict[str, Any],
) -> bool:
    rules = artifact_match_rules(artifact)

    # Prefer stable custom data whenever the artifact defines it.
    expected_custom_data = rules["custom_data"]
    if expected_custom_data:
        if not _deep_contains(item.get("custom_data", {}), expected_custom_data):
            return False

    expected_material = rules["material"]
    if expected_material and item.get("material") != expected_material:
        return False

    expected_name = rules["custom_name"]
    if expected_name and item.get("custom_name") != expected_name:
        return False

    expected_lore = rules["lore"]
    if expected_lore:
        item_lore = item.get("lore", [])

        if rules["require_all_lore"]:
            if not all(line in item_lore for line in expected_lore):
                return False
        elif not any(line in item_lore for line in expected_lore):
            return False

    # Refuse a definition with no matching criteria.
    if not any([
        expected_custom_data,
        expected_material,
        expected_name,
        expected_lore,
    ]):
        return False

    return True


def find_artifact_in_snapshot(
    player_name: str,
    artifact: Dict[str, Any],
    *,
    allow_stale: bool = False,
) -> Dict[str, Any]:
    snapshot = get_inventory_snapshot(
        player_name,
        allow_stale=allow_stale,
    )

    if not snapshot:
        return {
            "ok": False,
            "found": False,
            "player": player_name,
            "artifact_id": artifact.get("id"),
            "reason": "inventory_snapshot_unavailable",
        }

    matches = [
        item
        for item in snapshot.get("items", [])
        if item_matches_artifact(item, artifact)
    ]

    return {
        "ok": True,
        "found": bool(matches),
        "player": player_name,
        "artifact_id": artifact.get("id"),
        "match_count": len(matches),
        "matches": matches,
        "snapshot_age_seconds": snapshot.get("age_seconds", 0),
        "reason": "artifact_found" if matches else "artifact_not_found",
    }


def player_has_artifact(
    player_name: str,
    artifact: Dict[str, Any],
    *,
    allow_stale: bool = False,
) -> Dict[str, Any]:
    return find_artifact_in_snapshot(
        player_name,
        artifact,
        allow_stale=allow_stale,
    )


def build_remove_command(
    player_name: str,
    artifact: Dict[str, Any],
    *,
    amount: int = 1,
) -> Optional[str]:
    """
    Return a safe Minecraft command used to remove the artifact.

    For custom/NBT artifacts, define the exact command in the artifact JSON:

    "inventory": {
      "remove_command": "clear {player} minecraft:iron_nugget[...components...] {amount}"
    }

    This module intentionally does not invent a component predicate because
    Minecraft component syntax and plugin item metadata can differ.
    """
    inventory = artifact.get("inventory")
    if not isinstance(inventory, dict):
        inventory = {}

    template = (
        inventory.get("remove_command")
        or artifact.get("remove_command")
        or ""
    )

    template = _clean_text(template)

    if not template:
        return None

    return template.format(
        player=player_name,
        amount=max(1, int(amount)),
        artifact_id=_clean_text(artifact.get("id")),
    ).lstrip("/")


def remove_artifact(
    player_name: str,
    artifact: Dict[str, Any],
    *,
    amount: int = 1,
) -> Dict[str, Any]:
    command = build_remove_command(
        player_name,
        artifact,
        amount=amount,
    )

    if not command:
        return {
            "ok": False,
            "removed": False,
            "player": player_name,
            "artifact_id": artifact.get("id"),
            "reason": "artifact_remove_command_not_configured",
        }

    if send_minecraft_commands is None:
        return {
            "ok": False,
            "removed": False,
            "player": player_name,
            "artifact_id": artifact.get("id"),
            "reason": "mc_connector_unavailable",
            "command": command,
        }

    delivered = bool(
        send_minecraft_commands([command])
    )

    _log(
        f"Remove request player={player_name} artifact={artifact.get('id')} "
        f"delivered={delivered}"
    )

    return {
        "ok": delivered,
        "removed": delivered,
        "player": player_name,
        "artifact_id": artifact.get("id"),
        "reason": "remove_command_delivered" if delivered else "remove_command_failed",
        "command": command,
    }


def build_give_command(
    player_name: str,
    artifact: Dict[str, Any],
    *,
    amount: int = 1,
) -> Optional[str]:
    inventory = artifact.get("inventory")
    if not isinstance(inventory, dict):
        inventory = {}

    template = (
        inventory.get("give_command")
        or artifact.get("give_command")
        or ""
    )

    template = _clean_text(template)

    if not template:
        return None

    return template.format(
        player=player_name,
        amount=max(1, int(amount)),
        artifact_id=_clean_text(artifact.get("id")),
    ).lstrip("/")


def give_artifact(
    player_name: str,
    artifact: Dict[str, Any],
    *,
    amount: int = 1,
) -> Dict[str, Any]:
    command = build_give_command(
        player_name,
        artifact,
        amount=amount,
    )

    if not command:
        return {
            "ok": False,
            "given": False,
            "player": player_name,
            "artifact_id": artifact.get("id"),
            "reason": "artifact_give_command_not_configured",
        }

    if send_minecraft_commands is None:
        return {
            "ok": False,
            "given": False,
            "player": player_name,
            "artifact_id": artifact.get("id"),
            "reason": "mc_connector_unavailable",
            "command": command,
        }

    delivered = bool(
        send_minecraft_commands([command])
    )

    return {
        "ok": delivered,
        "given": delivered,
        "player": player_name,
        "artifact_id": artifact.get("id"),
        "reason": "give_command_delivered" if delivered else "give_command_failed",
        "command": command,
    }


def inventory_bridge_status() -> Dict[str, Any]:
    with _LOCK:
        players = sorted(_INVENTORY_CACHE.keys())

    return {
        "ok": True,
        "service": "inventory_bridge",
        "cached_players": players,
        "cached_player_count": len(players),
        "cache_ttl_seconds": INVENTORY_CACHE_TTL_SECONDS,
        "mc_connector_available": send_minecraft_commands is not None,
    }
