"""
continuity_engine.py
Kairos / Nexus Continuity Engine

Purpose:
- Handles rumors, lore drift, delayed consequences, world narrative memory.
- Does NOT run Flask.
- Does NOT talk directly to Discord.
- Uses memory_engine for persistence.
- Uses ai_core for generated narrative lines.
- Uses mc_connector only when asked to broadcast.
"""

from __future__ import annotations

import os
import random
import time
import traceback
from typing import Any, Dict, List, Optional

from ai_core import AIContext, generate_ai_response
from memory_engine import (
    record_world_event,
    record_rumor,
    get_recent_world_events,
    get_recent_rumors,
    record_system_error,
)
from mc_connector import broadcast_world_event


CONTINUITY_DEBUG = os.getenv("CONTINUITY_DEBUG", "true").lower() == "true"


def continuity_log(message: str, level: str = "INFO") -> None:
    if CONTINUITY_DEBUG or level in {"WARN", "ERROR", "FATAL"}:
        print(f"[CONTINUITY_ENGINE {level}] {message}", flush=True)


def continuity_log_exception(context: str, exc: Exception) -> None:
    print(f"[CONTINUITY_ENGINE ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()
    try:
        record_system_error(context, str(exc))
    except Exception:
        pass


WORLD_RUMOR_SEEDS = [
    "Travelers claim the roads near Trojan Kingdom feel watched.",
    "A guard swore he heard Kairos speaking through an empty gatehouse.",
    "Merchants are quietly raising prices near unstable regions.",
    "Some citizens believe the kingdom is being profiled by something unseen.",
    "Scouts have begun marking trees with symbols nobody admits carving.",
]


def generate_rumor(
    location: Optional[str] = None,
    faction: Optional[str] = None,
    seed: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Creates and records a world rumor.
    """
    try:
        base = seed or random.choice(WORLD_RUMOR_SEEDS)

        prompt = (
            f"Create one short in-world rumor for the Nexus. "
            f"Location: {location or 'Unknown'}. "
            f"Faction: {faction or 'Unknown'}. "
            f"Seed: {base}. "
            f"Make it atmospheric, grounded, and useful for future quests."
        )

        context = AIContext(
            mode="observer",
            location=location,
            faction=faction,
            recent_events=[e.get("description", "") for e in get_recent_world_events(5)],
        )

        rumor_text = generate_ai_response(prompt, context=context, max_tokens=120)
        rumor = record_rumor(rumor_text, location=location, faction=faction, confidence=0.55)

        continuity_log(f"Rumor generated: {rumor_text}")
        return rumor

    except Exception as exc:
        continuity_log_exception("generate_rumor failed", exc)
        return record_rumor(seed or random.choice(WORLD_RUMOR_SEEDS), location=location, faction=faction)


# Backward-compatible name for old monolith references.
def kairos_continuity_generate_rumor(*args, **kwargs):
    return generate_rumor(*args, **kwargs)


def record_continuity_event(
    event_type: str,
    description: str,
    location: Optional[str] = None,
    faction: Optional[str] = None,
    broadcast: bool = False,
) -> Dict[str, Any]:
    """
    Records continuity event and optionally broadcasts it into Minecraft.
    """
    try:
        event = record_world_event(
            event_type=event_type,
            description=description,
            location=location,
            faction=faction,
        )

        if broadcast:
            broadcast_world_event(description, title=event_type.upper())

        return event

    except Exception as exc:
        continuity_log_exception("record_continuity_event failed", exc)
        return {"type": event_type, "description": description, "error": str(exc), "timestamp": time.time()}


def generate_continuity_summary(limit: int = 8) -> str:
    """
    Produces a short summary of recent world state.
    """
    events = get_recent_world_events(limit)
    rumors = get_recent_rumors(limit)

    prompt = (
        "Summarize the current Nexus continuity state from recent events and rumors. "
        "Keep it short, in-world, and useful for future AI systems."
    )

    context = AIContext(
        mode="observer",
        recent_events=[e.get("description", "") for e in events],
        memories=[r.get("rumor", "") for r in rumors],
    )

    return generate_ai_response(prompt, context=context, max_tokens=180)


def tick_continuity(location: Optional[str] = None, faction: Optional[str] = None) -> Dict[str, Any]:
    """
    One safe manual continuity tick.
    Does not loop forever.
    app.py or a future scheduler may call this.
    """
    try:
        rumor = generate_rumor(location=location, faction=faction)
        summary = generate_continuity_summary()

        return {
            "ok": True,
            "handled": "continuity_tick",
            "rumor": rumor,
            "summary": summary,
        }

    except Exception as exc:
        continuity_log_exception("tick_continuity failed", exc)
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    print(tick_continuity(location="Trojan Kingdom", faction="Trojan Kingdom"))
