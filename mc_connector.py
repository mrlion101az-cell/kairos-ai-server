"""
mc_connector.py
Kairos / Nexus Minecraft Connector Layer
"""

from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any, List, Optional

try:
    import requests
except Exception:
    requests = None


# ============================================================
# CONFIG
# ============================================================

MC_CONNECTOR_DEBUG = os.getenv(
    "MC_CONNECTOR_DEBUG",
    "true"
).lower() == "true"

MC_HTTP_HOST = os.getenv(
    "MC_HTTP_HOST",
    "72.5.46.197"
)

MC_HTTP_PORT = int(
    os.getenv("MC_HTTP_PORT", "8123")
)

MC_HTTP_TIMEOUT = float(
    os.getenv("MC_HTTP_TIMEOUT", "10")
)

MC_HTTP_RETRIES = int(
    os.getenv("MC_HTTP_RETRIES", "3")
)

MC_HTTP_SCHEME = os.getenv(
    "MC_HTTP_SCHEME",
    "http"
)

# ============================================================
# IMPORTANT FIX
# ============================================================
# Your Minecraft bridge uses:
# /execute
#
# NOT:
# /command
# ============================================================

MC_HTTP_ENDPOINT = os.getenv(
    "MC_HTTP_ENDPOINT",
    f"{MC_HTTP_SCHEME}://{MC_HTTP_HOST}:{MC_HTTP_PORT}/execute"
)

DEFAULT_CHAT_TARGET = os.getenv(
    "MC_DEFAULT_CHAT_TARGET",
    "@a"
)

MAX_BATCH_COMMANDS = int(
    os.getenv("MC_MAX_BATCH_COMMANDS", "50")
)


# ============================================================
# LOGGING
# ============================================================

def mc_log(message: str, level: str = "INFO") -> None:
    if MC_CONNECTOR_DEBUG or level in {
        "WARN",
        "ERROR",
        "FATAL"
    }:
        print(
            f"[MC_CONNECTOR {level}] {message}",
            flush=True
        )


def mc_log_exception(
    context: str,
    exc: Exception
) -> None:
    print(
        f"[MC_CONNECTOR ERROR] {context}: {exc}",
        flush=True
    )
    traceback.print_exc()


# ============================================================
# HTTP CORE
# ============================================================

def send_http_command_batch(
    commands: List[str]
) -> bool:

    if not commands:
        return False

    if not requests:
        mc_log(
            "requests library unavailable",
            "ERROR"
        )
        return False

    payload = {
        "commands": commands
    }

    for attempt in range(
        1,
        MC_HTTP_RETRIES + 1
    ):
        try:

            mc_log(
                f"Sending batch to {MC_HTTP_ENDPOINT}"
            )

            response = requests.post(
                MC_HTTP_ENDPOINT,
                json=payload,
                timeout=MC_HTTP_TIMEOUT,
            )

            if response.status_code == 200:
                mc_log(
                    f"MC command batch delivered ({len(commands)} cmds)"
                )
                return True

            mc_log(
                f"HTTP {response.status_code} from MC bridge attempt={attempt}",
                "WARN"
            )

            try:
                mc_log(
                    f"Bridge response: {response.text}",
                    "WARN"
                )
            except Exception:
                pass

        except Exception as exc:
            mc_log_exception(
                f"MC send failed attempt {attempt}",
                exc
            )

        time.sleep(1.0)

    return False


# ============================================================
# COMMAND HELPERS
# ============================================================

def normalize_command(command: Any) -> str:
    text = str(command or "").strip()

    if text.startswith("/"):
        text = text[1:]

    return text


def chunk_commands(
    commands: List[str],
    chunk_size: int = MAX_BATCH_COMMANDS
) -> List[List[str]]:

    chunks = []

    for i in range(
        0,
        len(commands),
        chunk_size
    ):
        chunks.append(
            commands[i:i + chunk_size]
        )

    return chunks


def send_minecraft_commands(
    commands: List[Any]
) -> bool:

    clean_commands = [
        normalize_command(cmd)
        for cmd in commands
        if str(cmd).strip()
    ]

    if not clean_commands:
        return False

    overall_success = True

    for chunk in chunk_commands(clean_commands):

        success = send_http_command_batch(chunk)

        if not success:
            overall_success = False

    return overall_success


# ============================================================
# CHAT / TELLRAW
# ============================================================

def escape_json_text(text: str) -> str:
    return json.dumps(str(text))[1:-1]


def build_tellraw(
    text: str,
    target: str = DEFAULT_CHAT_TARGET,
    color: str = "white",
) -> str:

    escaped = escape_json_text(text)

    return (
        f'tellraw {target} '
        f'{{"text":"{escaped}","color":"{color}"}}'
    )


def send_chat(
    text: str,
    target: str = DEFAULT_CHAT_TARGET,
    color: str = "white",
) -> bool:

    command = build_tellraw(
        text,
        target,
        color
    )

    return send_minecraft_commands([
        command
    ])


def send_to_minecraft(
    text: str,
    player_name: Optional[str] = None,
    color: str = "white",
) -> bool:

    target = (
        player_name
        if player_name
        else DEFAULT_CHAT_TARGET
    )

    return send_chat(
        text,
        target=target,
        color=color
    )


# ============================================================
# TITLES
# ============================================================

def send_title(
    title: str,
    subtitle: Optional[str] = None,
    target: str = DEFAULT_CHAT_TARGET,
) -> bool:

    commands = [
        f'title {target} title {{"text":"{escape_json_text(title)}","color":"gold"}}'
    ]

    if subtitle:
        commands.append(
            f'title {target} subtitle {{"text":"{escape_json_text(subtitle)}","color":"gray"}}'
        )

    return send_minecraft_commands(commands)


def send_actionbar(
    text: str,
    target: str = DEFAULT_CHAT_TARGET,
    color: str = "yellow",
) -> bool:

    cmd = (
        f'title {target} actionbar '
        f'{{"text":"{escape_json_text(text)}","color":"{color}"}}'
    )

    return send_minecraft_commands([
        cmd
    ])


# ============================================================
# SOUND
# ============================================================

def play_sound(
    sound: str,
    target: str = DEFAULT_CHAT_TARGET,
    volume: float = 1.0,
    pitch: float = 1.0,
) -> bool:

    cmd = (
        f'playsound {sound} master {target} ~ ~ ~ '
        f'{volume} {pitch}'
    )

    return send_minecraft_commands([
        cmd
    ])


# ============================================================
# PARTICLES
# ============================================================

def send_particle(
    particle: str,
    x: str = "~",
    y: str = "~",
    z: str = "~",
    dx: float = 0.5,
    dy: float = 0.5,
    dz: float = 0.5,
    speed: float = 0.01,
    count: int = 10,
    target: str = DEFAULT_CHAT_TARGET,
) -> bool:

    cmd = (
        f'particle {particle} '
        f'{x} {y} {z} '
        f'{dx} {dy} {dz} {speed} {count} force {target}'
    )

    return send_minecraft_commands([
        cmd
    ])


# ============================================================
# EFFECTS
# ============================================================

def give_effect(
    effect: str,
    target: str = DEFAULT_CHAT_TARGET,
    seconds: int = 10,
    amplifier: int = 0,
    hide_particles: bool = True,
) -> bool:

    cmd = (
        f'effect give {target} {effect} '
        f'{seconds} {amplifier} '
        f'{"true" if hide_particles else "false"}'
    )

    return send_minecraft_commands([
        cmd
    ])


# ============================================================
# WORLD EVENTS
# ============================================================

def broadcast_world_event(
    message: str,
    sound: Optional[str] = None,
    title: Optional[str] = None,
) -> bool:

    success = True

    if title:
        success &= send_title(
            title,
            subtitle=message
        )

    success &= send_chat(
        f"[WORLD EVENT] {message}",
        color="light_purple"
    )

    if sound:
        success &= play_sound(sound)

    return success


def send_kairos_presence_effect(
    target: str = DEFAULT_CHAT_TARGET
) -> bool:

    commands = [
        build_tellraw(
            "[Kairos] Presence detected.",
            target=target,
            color="dark_red"
        ),
        f'playsound minecraft:entity.warden.heartbeat master {target} ~ ~ ~ 1 0.7',
        f'particle minecraft:sculk_soul ~ ~1 ~ 0.5 1 0.5 0.02 25 force {target}',
    ]

    return send_minecraft_commands(commands)


# ============================================================
# DEBUG
# ============================================================

def connector_self_test() -> bool:

    return send_chat(
        "[MC_CONNECTOR] Self-test successful.",
        color="green"
    )


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":

    print(
        "Running MC connector self-test..."
    )

    connector_self_test()
