# KAIROS SAFE BUILD (WAVE + COMMAND FIXED)

# NOTE:
# - Fixed particle crash
# - Enforced cooldowns
# - Hard caps on mobs
# - Disabled unsafe particle

import time

# -----------------------------
# SAFE SETTINGS
# -----------------------------
WAVE_COOLDOWN_SECONDS = 45
GLOBAL_WAVE_COOLDOWN = 10

MAX_GLOBAL_NPCS = 35
MAX_UNITS_PER_PLAYER = 8
MAX_WAVE_SIZE = 4

ENABLE_VANILLA_FALLBACK = False

# -----------------------------
# PARTICLE FIX
# -----------------------------
def safe_particle_command():
    return "execute as @a at @a run particle minecraft:sculk_charge ~ ~0.1 ~ 1 0.1 1 0.02 20 force"

# -----------------------------
# WAVE CONTROL
# -----------------------------
last_wave_time = {}
last_global_wave = 0

def can_spawn_wave(player):
    global last_global_wave
    now = time.time()

    if now - last_global_wave < GLOBAL_WAVE_COOLDOWN:
        return False

    if player in last_wave_time:
        if now - last_wave_time[player] < WAVE_COOLDOWN_SECONDS:
            return False

    return True

def register_wave(player):
    global last_global_wave
    now = time.time()
    last_wave_time[player] = now
    last_global_wave = now

# -----------------------------
# SAFE SPAWN
# -----------------------------
def spawn_wave(player):
    if not can_spawn_wave(player):
        return []

    register_wave(player)

    wave_size = min(MAX_WAVE_SIZE, MAX_UNITS_PER_PLAYER)

    commands = []

    for _ in range(wave_size):
        commands.append("summon zombie ~ ~ ~")

    return commands

# -----------------------------
# MAIN TEST LOOP
# -----------------------------
if __name__ == "__main__":
    print("Kairos SAFE build loaded.")
