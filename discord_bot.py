import os
import asyncio
import time
import threading
from collections import OrderedDict

import discord
import requests
from flask import Flask, request, jsonify

# ============================================================
# KAIROS DISCORD BOT — FULL BRIDGE VERSION
# Replace your entire Discord bot file with this.
#
# Provides:
#   Discord -> Kairos app.py /chat
#   Minecraft/app.py -> Discord through POST /mc_to_discord
#
# Required Render env vars:
#   DISCORD_TOKEN or DISCORD_BOT_TOKEN
#   DISCORD_CHANNEL_ID
#   KAIROS_API_URL=https://kairos-ai-server.onrender.com/chat
#
# Optional:
#   PORT=10000
#   MC_TO_DISCORD_TOKEN=shared secret if you want auth
# ============================================================

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
KAIROS_API_URL = os.getenv("KAIROS_API_URL", "https://kairos-ai-server.onrender.com/chat").strip()
DISCORD_CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_RAW) if DISCORD_CHANNEL_ID_RAW.isdigit() else 0

PORT = int(os.getenv("PORT", "10000"))
REQUEST_TIMEOUT = int(os.getenv("KAIROS_REQUEST_TIMEOUT", "35"))
DEDUP_SECONDS = float(os.getenv("KAIROS_DISCORD_DEDUPE_SECONDS", "12"))
DISCORD_CHUNK_LIMIT = int(os.getenv("DISCORD_CHUNK_LIMIT", "1850"))
MC_TO_DISCORD_TOKEN = (os.getenv("MC_TO_DISCORD_TOKEN") or "").strip()

# Covenant / advanced Kairos protections
BLOCK_KAIROS_SYSTEM_MESSAGES = os.getenv("BLOCK_KAIROS_SYSTEM_MESSAGES", "true").lower() == "true"
ALLOW_DIRECT_KAIROS_CHAT = os.getenv("ALLOW_DIRECT_KAIROS_CHAT", "true").lower() == "true"
DISCORD_TYPING_INDICATOR = os.getenv("DISCORD_TYPING_INDICATOR", "false").lower() == "true"

# Narrative Ops compatibility
NARRATIVE_STATUS_URL = os.getenv(
    "NARRATIVE_STATUS_URL",
    KAIROS_API_URL.rsplit("/chat", 1)[0] + "/kairos/narrative/status" if KAIROS_API_URL.endswith("/chat") else ""
).strip()
NARRATIVE_PLAYER_URL_TEMPLATE = os.getenv(
    "NARRATIVE_PLAYER_URL_TEMPLATE",
    KAIROS_API_URL.rsplit("/chat", 1)[0] + "/kairos/narrative/player/{player}" if KAIROS_API_URL.endswith("/chat") else ""
).strip()

# Discord bridge behavior controls
BRIDGE_NON_TRIGGER_MESSAGES_TO_KAIROS = os.getenv("BRIDGE_NON_TRIGGER_MESSAGES_TO_KAIROS", "true").lower() == "true"
SEND_ERROR_MESSAGES_TO_DISCORD = os.getenv("SEND_ERROR_MESSAGES_TO_DISCORD", "true").lower() == "true"
MAX_DISCORD_REPLY_CHUNKS = int(os.getenv("MAX_DISCORD_REPLY_CHUNKS", "4"))
DISCORD_SEND_DELAY = float(os.getenv("DISCORD_SEND_DELAY", "0.35"))
MC_TO_DISCORD_SEND_DELAY = float(os.getenv("MC_TO_DISCORD_SEND_DELAY", "0.15"))

# Prevent world-event spam / autonomous loops from reaching Discord.
BLOCKED_KAIROS_PREFIXES = (
    "[Kairos]",
    "[HOPE SIGNAL]",
    "[REALITY BLEED]",
    "[Blackline Broadcast]",
    "[COVENANT]",
    "[VIRUS]",
    "[NARRATIVE]",
    "[NARRATIVE OPS]",
    "[BLACK LIQUID]",
    "[COVENANT STAGE]",
    "[SYSTEM]",
)

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN or DISCORD_BOT_TOKEN is missing.")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
http_app = Flask(__name__)

processed_ids = OrderedDict()
processed_fps = OrderedDict()


def log(message):
    print(f"[Kairos Discord Bridge] {message}", flush=True)


def split_text(text, limit=DISCORD_CHUNK_LIMIT):
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < int(limit * 0.4):
            cut = rest.rfind(". ", 0, limit)
        if cut < int(limit * 0.4):
            cut = rest.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()

    if rest:
        chunks.append(rest)
    return chunks


def cleanup_dedupe():
    cutoff = time.time() - DEDUP_SECONDS
    for store in (processed_ids, processed_fps):
        for key, ts in list(store.items()):
            if ts < cutoff:
                store.pop(key, None)
        while len(store) > 700:
            store.popitem(last=False)


def already_processed(message):
    cleanup_dedupe()
    mid = str(message.id)
    fp = f"{message.author.id}:{message.channel.id}:{message.content.strip().lower()}"
    if mid in processed_ids or fp in processed_fps:
        return True
    processed_ids[mid] = time.time()
    processed_fps[fp] = time.time()
    return False


def is_kairos_trigger(message):
    content = (message.content or "").strip().lower()

    if client.user and client.user.mentioned_in(message):
        return True

    return content.startswith((
        "kairos",
        "!kairos",
        "/kairos",
        "hey kairos",
        "yo kairos",
        "ok kairos",
        "okay kairos",
        "kairus",
        "kaiross",
        "kiros",
        "kyros",
    ))


def clean_trigger_text(message):
    content = (message.content or "").strip()

    if client.user:
        content = (
            content
            .replace(f"<@{client.user.id}>", "")
            .replace(f"<@!{client.user.id}>", "")
            .strip()
        )

    lower = content.lower()
    for prefix in (
        "!kairos",
        "/kairos",
        "hey kairos",
        "yo kairos",
        "ok kairos",
        "okay kairos",
        "kaiross",
        "kairus",
        "kiros",
        "kyros",
        "kairos",
    ):
        if lower.startswith(prefix):
            content = content[len(prefix):].strip()
            break

    return content or "Speak."



def _safe_preview(value, limit=500):
    text = str(value or "")
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def is_probably_kairos_system_message(player, message):
    """
    Blocks autonomous Kairos / world-event / Narrative Ops output from bleeding into Discord.
    Direct player chat is still allowed through normal bridge formatting.
    """
    p = str(player or "").strip().lower()
    m = str(message or "").strip()
    ml = m.lower()

    if not m:
        return False

    # If the app or plugin identifies the speaker as Kairos/system, treat it as autonomous output.
    if p in {"kairos", "system", "console", "server", "nexus", "blackline broadcast"}:
        return True

    for prefix in BLOCKED_KAIROS_PREFIXES:
        if m.startswith(prefix):
            return True

    # These phrases are intentionally broad because they are not normal player bridge chatter.
    suspicious_phrases = (
        "reality integrity fluctuation",
        "initial proof",
        "containment pressure increasing",
        "transmission event",
        "transmission events",
        "covenant stage",
        "black liquid",
        "host-vector",
        "host vector",
        "exposure events",
        "the nexus is recording choices",
        "a long experiment does not announce",
        "you are all still early in the test",
        "some stories require months",
        "profile estimate:",
        "primary readings:",
        "correct me if i am wrong",
        "small task, if you are willing",
        "minor assignment:",
        "answer this when you are ready",
        "current profile estimate",
        "blackline broadcast",
        "hope signal",
        "counter-pattern",
        "counter pattern",
    )
    return any(phrase in ml for phrase in suspicious_phrases)


def normalize_kairos_api_payload(data):
    """
    Narrative Ops app.py may return reply, message, text, response, or chunks depending on route/layer.
    This keeps the Discord bridge tolerant without changing app.py.
    """
    if not isinstance(data, dict):
        return {"reply": ""}

    reply = (
        data.get("reply")
        or data.get("message")
        or data.get("text")
        or data.get("response")
        or ""
    )

    if not reply and isinstance(data.get("choices"), list) and data["choices"]:
        try:
            reply = data["choices"][0].get("message", {}).get("content", "")
        except Exception:
            reply = ""

    commands = data.get("commands") or data.get("minecraft_commands") or data.get("actions") or []

    return {
        **data,
        "reply": str(reply or "").strip(),
        "commands": commands,
    }


def trim_reply_chunks(chunks):
    if MAX_DISCORD_REPLY_CHUNKS <= 0:
        return chunks
    if len(chunks) <= MAX_DISCORD_REPLY_CHUNKS:
        return chunks
    kept = chunks[:MAX_DISCORD_REPLY_CHUNKS]
    kept.append("[response shortened to protect Discord stability]")
    return kept


def should_post_message_to_kairos(triggered):
    if triggered:
        return True
    return BRIDGE_NON_TRIGGER_MESSAGES_TO_KAIROS


def post_to_kairos(message, text, triggered):
    payload = {
        "player": message.author.display_name,
        "username": message.author.display_name,
        "message": text,
        "content": text,
        "source": "discord",
        "platform": "discord",
        "message_id": str(message.id),
        "discord_message_id": str(message.id),
        "platform_user_id": str(message.author.id),
        "discord_user_id": str(message.author.id),
        "discord_channel_id": str(message.channel.id),
        "channel_id": str(message.channel.id),
        "reply_allowed": bool(triggered),
        "discord_reply_allowed": bool(triggered),
        "bridge_only": not bool(triggered),
        "narrative_ops_enabled": True,
        "covenant_bridge": True,
    }

    response = requests.post(KAIROS_API_URL, json=payload, timeout=REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise RuntimeError(f"Kairos API HTTP {response.status_code}: {_safe_preview(response.text)}")

    try:
        return normalize_kairos_api_payload(response.json())
    except Exception:
        return {"reply": ""}


async def get_target_channel():
    if not DISCORD_CHANNEL_ID:
        return None

    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        return channel

    try:
        return await client.fetch_channel(DISCORD_CHANNEL_ID)
    except Exception as exc:
        log(f"Could not fetch DISCORD_CHANNEL_ID={DISCORD_CHANNEL_ID}: {exc}")
        return None


async def send_to_discord_channel(text):
    text = str(text or "").strip()
    if not text:
        return False

    channel = await get_target_channel()
    if channel is None:
        log("No Discord target channel found. Check DISCORD_CHANNEL_ID.")
        return False

    for chunk in split_text(text):
        await channel.send(chunk)
        await asyncio.sleep(MC_TO_DISCORD_SEND_DELAY)

    return True


@client.event
async def on_ready():
    log("=" * 72)
    log(f"Online as {client.user}")
    log(f"KAIROS_API_URL={KAIROS_API_URL}")
    log(f"CHANNEL_LOCK={DISCORD_CHANNEL_ID if DISCORD_CHANNEL_ID else 'ALL'}")
    log("HTTP endpoint active: POST /mc_to_discord")
    log(f"BLOCK_KAIROS_SYSTEM_MESSAGES={BLOCK_KAIROS_SYSTEM_MESSAGES}")
    log(f"ALLOW_DIRECT_KAIROS_CHAT={ALLOW_DIRECT_KAIROS_CHAT}")
    log(f"DISCORD_TYPING_INDICATOR={DISCORD_TYPING_INDICATOR}")
    log(f"BRIDGE_NON_TRIGGER_MESSAGES_TO_KAIROS={BRIDGE_NON_TRIGGER_MESSAGES_TO_KAIROS}")
    log(f"NARRATIVE_STATUS_URL={NARRATIVE_STATUS_URL or 'disabled'}")
    log("=" * 72)


@client.event
async def on_message(message):
    if message.author.bot:
        return

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return

    if not message.content or not message.content.strip():
        return

    if already_processed(message):
        return

    triggered = is_kairos_trigger(message)
    user_text = clean_trigger_text(message) if triggered else message.content.strip()

    try:
        if not should_post_message_to_kairos(triggered):
            return

        data = await asyncio.to_thread(post_to_kairos, message, user_text, triggered)

        if data.get("duplicate"):
            return

        reply = str(data.get("reply") or "").strip()

        # Discord only gets Kairos reply when directly triggered.
        # Normal Discord messages still travel to Minecraft through app.py for memory/narrative intake.
        if triggered and reply and ALLOW_DIRECT_KAIROS_CHAT:
            for chunk in trim_reply_chunks(split_text(reply)):
                await message.channel.send(f"**[Kairos]** {chunk}")
                await asyncio.sleep(DISCORD_SEND_DELAY)

    except Exception as exc:
        log(f"Discord -> Kairos ERROR: {exc}")
        if triggered and SEND_ERROR_MESSAGES_TO_DISCORD:
            try:
                await message.channel.send("**[Kairos]** ...connection disrupted.")
            except Exception:
                pass


@http_app.route("/", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "kairos-discord-bridge",
        "discord_ready": client.is_ready(),
        "channel_id": DISCORD_CHANNEL_ID,
        "routes": ["/", "/mc_to_discord", "/narrative_status"],
        "narrative_status_url": NARRATIVE_STATUS_URL,
        "bridge_non_trigger_messages": BRIDGE_NON_TRIGGER_MESSAGES_TO_KAIROS,
    })



@http_app.route("/narrative_status", methods=["GET"])
def narrative_status_proxy():
    if not NARRATIVE_STATUS_URL:
        return jsonify({"ok": False, "error": "NARRATIVE_STATUS_URL unavailable"}), 200
    try:
        r = requests.get(NARRATIVE_STATUS_URL, timeout=REQUEST_TIMEOUT)
        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "status": r.status_code, "body": _safe_preview(r.text)}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200


@http_app.route("/mc_to_discord", methods=["GET"])
def mc_to_discord_get():
    return jsonify({
        "ok": True,
        "endpoint": "/mc_to_discord",
        "method": "POST",
        "example": {"player": "RealSociety5107", "message": "hello"},
    })


@http_app.route("/mc_to_discord", methods=["POST"])
def mc_to_discord():
    try:
        data = request.get_json(silent=True) or {}

        if MC_TO_DISCORD_TOKEN:
            supplied_header = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
            supplied_body = str(data.get("token", "")).strip()

            if supplied_header != MC_TO_DISCORD_TOKEN and supplied_body != MC_TO_DISCORD_TOKEN:
                return jsonify({"ok": False, "error": "unauthorized"}), 401

        player = str(data.get("player") or data.get("username") or data.get("name") or "Minecraft").strip()
        message = str(data.get("message") or data.get("content") or "").strip()

        # ----------------------------------------
        # BLOCK KAIROS AUTONOMOUS / SYSTEM MESSAGES
        # ----------------------------------------
        if BLOCK_KAIROS_SYSTEM_MESSAGES and is_probably_kairos_system_message(player, message):
            return jsonify({
                "ok": True,
                "blocked": True,
                "reason": "kairos_system_or_narrative_message_blocked"
            }), 200

        # NORMAL VALIDATION (must NOT be nested)
        if not message:
            return jsonify({"ok": False, "error": "missing message"}), 400

        safe_player = player.replace("@", "@\u200b")
        safe_message = message.replace("@", "@\u200b")

        formatted = f"**[Minecraft] {safe_player}:** {safe_message}"

        if not client.is_ready():
            log("Minecraft message received before Discord client was ready.")
            return jsonify({"ok": False, "error": "discord client not ready"}), 503

        future = asyncio.run_coroutine_threadsafe(send_to_discord_channel(formatted), client.loop)
        delivered = future.result(timeout=10)

        if delivered:
            log(f"Minecraft -> Discord delivered for {player}.")
            return jsonify({"ok": True, "delivered": True}), 200

        return jsonify({"ok": False, "delivered": False, "error": "channel unavailable"}), 500

    except Exception as exc:
        log(f"Minecraft -> Discord ERROR: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


def run_http_server():
    http_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)




# ============================================================
# KAIROS ENDGAME DISCORD OVERLAY
# Adds compatibility for:
#   - Endgame Continuity Layer
#   - Living Archive
#   - Faction / dossier systems
#   - Delayed consequence routing
#   - Music orchestration requests
#   - Stronger anti-loop / anti-spam protections
# ============================================================

ENDGAME_DISCORD_VERSION = "Kairos Discord Endgame Bridge"

ENABLE_ENDGAME_DISCORD = os.getenv("ENABLE_ENDGAME_DISCORD", "true").lower() == "true"

# Stronger autonomous-event suppression.
BLOCKED_ENDGAME_PHRASES = (
    "living archive",
    "delayed consequence",
    "history ingestion",
    "endgame continuity",
    "classified archive",
    "faction leaning",
    "creator attachment",
    "a player who enters late still enters history already in motion",
    "the archive is not storage",
    "old cities are not old",
    "kairos 2.5",
)

# Prevent repeated Discord narrative loops.
ENDGAME_RECENT_BRIDGE = OrderedDict()
ENDGAME_DEDUPE_SECONDS = float(os.getenv("ENDGAME_DEDUPE_SECONDS", "18"))

# Optional Endgame routes
ENDGAME_STATUS_URL = os.getenv(
    "ENDGAME_STATUS_URL",
    KAIROS_API_URL.rsplit("/chat", 1)[0] + "/kairos/endgame/status" if KAIROS_API_URL.endswith("/chat") else ""
).strip()

ENDGAME_ARCHIVE_URL = os.getenv(
    "ENDGAME_ARCHIVE_URL",
    KAIROS_API_URL.rsplit("/chat", 1)[0] + "/kairos/endgame/archive" if KAIROS_API_URL.endswith("/chat") else ""
).strip()

ENDGAME_FACTIONS_URL = os.getenv(
    "ENDGAME_FACTIONS_URL",
    KAIROS_API_URL.rsplit("/chat", 1)[0] + "/kairos/endgame/factions" if KAIROS_API_URL.endswith("/chat") else ""
).strip()

def endgame_bridge_fingerprint(player, message):
    raw = f"{player}|{message}".lower().strip()
    return raw[:600]

def endgame_recently_seen(player, message):
    now = time.time()
    fp = endgame_bridge_fingerprint(player, message)

    for key, ts in list(ENDGAME_RECENT_BRIDGE.items()):
        if now - ts > ENDGAME_DEDUPE_SECONDS:
            ENDGAME_RECENT_BRIDGE.pop(key, None)

    if fp in ENDGAME_RECENT_BRIDGE:
        return True

    ENDGAME_RECENT_BRIDGE[fp] = now

    while len(ENDGAME_RECENT_BRIDGE) > 1200:
        ENDGAME_RECENT_BRIDGE.popitem(last=False)

    return False

try:
    _ENDGAME_ORIGINAL_IS_PROBABLY_KAIROS_SYSTEM_MESSAGE = is_probably_kairos_system_message
except Exception:
    _ENDGAME_ORIGINAL_IS_PROBABLY_KAIROS_SYSTEM_MESSAGE = None

def is_probably_kairos_system_message(player, message):
    """
    Upgraded filter for Endgame Continuity autonomous behavior.
    """
    try:
        if _ENDGAME_ORIGINAL_IS_PROBABLY_KAIROS_SYSTEM_MESSAGE:
            if _ENDGAME_ORIGINAL_IS_PROBABLY_KAIROS_SYSTEM_MESSAGE(player, message):
                return True
    except Exception:
        pass

    p = str(player or "").lower().strip()
    m = str(message or "").lower().strip()

    if not m:
        return False

    if p in {"kairos", "system", "console", "server", "nexus"}:
        return True

    for phrase in BLOCKED_ENDGAME_PHRASES:
        if phrase in m:
            return True

    return False

# ============================================================
# ENDGAME COMMAND HELPERS
# ============================================================

async def fetch_endgame_status():
    if not ENDGAME_STATUS_URL:
        return None
    try:
        response = await asyncio.to_thread(
            requests.get,
            ENDGAME_STATUS_URL,
            timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            return response.json()
    except Exception as exc:
        log(f"Endgame status fetch failed: {exc}")
    return None

async def fetch_endgame_archive(limit=8):
    if not ENDGAME_ARCHIVE_URL:
        return None
    try:
        response = await asyncio.to_thread(
            requests.get,
            ENDGAME_ARCHIVE_URL,
            params={"limit": limit},
            timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            return response.json()
    except Exception as exc:
        log(f"Endgame archive fetch failed: {exc}")
    return None

async def fetch_endgame_factions():
    if not ENDGAME_FACTIONS_URL:
        return None
    try:
        response = await asyncio.to_thread(
            requests.get,
            ENDGAME_FACTIONS_URL,
            timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            return response.json()
    except Exception as exc:
        log(f"Endgame factions fetch failed: {exc}")
    return None

# ============================================================
# DISCORD COMMAND EXTENSIONS
# ============================================================

try:
    _ENDGAME_ORIGINAL_ON_MESSAGE = on_message
except Exception:
    _ENDGAME_ORIGINAL_ON_MESSAGE = None

@client.event
async def on_message(message):
    # Preserve original protections.
    if message.author.bot:
        return

    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return

    content = (message.content or "").strip()
    lower = content.lower()

    # Prevent feedback loops.
    if endgame_recently_seen(message.author.display_name, content):
        return

    # ========================================================
    # ENDGAME ADMIN / LORE COMMANDS
    # ========================================================

    if lower.startswith("!archive"):
        data = await fetch_endgame_archive(limit=10)

        if not data or not data.get("ok"):
            await message.channel.send("**[Kairos Archive]** archive retrieval failed.")
            return

        entries = data.get("entries", [])
        if not entries:
            await message.channel.send("**[Kairos Archive]** no records available.")
            return

        lines = ["**[Kairos Archive] Recent Entries**"]
        for e in entries[-10:]:
            title = e.get("title", "Unknown")
            kind = e.get("kind", "record")
            lines.append(f"• [{kind}] {title}")

        await message.channel.send("\\n".join(lines[:20]))
        return

    if lower.startswith("!factions"):
        data = await fetch_endgame_factions()

        if not data or not data.get("ok"):
            await message.channel.send("**[Kairos]** faction network unavailable.")
            return

        factions = data.get("factions", {})
        lines = ["**[Kairos] Known Factions**"]

        for key, fac in list(factions.items())[:12]:
            lines.append(f"• {fac.get('name', key)}")

        await message.channel.send("\\n".join(lines[:20]))
        return

    if lower.startswith("!endgame") or lower.startswith("!kairos status"):
        data = await fetch_endgame_status()

        if not data or not data.get("ok"):
            await message.channel.send("**[Kairos]** Endgame systems unreachable.")
            return

        lines = [
            "**[Kairos Endgame Systems]**",
            f"Version: {data.get('version')}",
            f"Archive Entries: {data.get('archive_entries')}",
            f"History Events: {data.get('history_events')}",
            f"Player Dossiers: {data.get('player_dossiers')}",
            f"Factions: {data.get('factions')}",
            f"Locations: {data.get('locations')}",
            f"Delayed Consequences: {data.get('delayed_consequences')}",
        ]

        await message.channel.send("\\n".join(lines))
        return

    # Let original bridge continue handling normal chat + Kairos interactions.
    if _ENDGAME_ORIGINAL_ON_MESSAGE:
        return await _ENDGAME_ORIGINAL_ON_MESSAGE(message)

# ============================================================
# HEALTH ROUTE EXTENSION
# ============================================================

try:
    _ENDGAME_ORIGINAL_HEALTH = health
except Exception:
    _ENDGAME_ORIGINAL_HEALTH = None

@http_app.route("/endgame_health", methods=["GET"])
def endgame_health():
    return jsonify({
        "ok": True,
        "service": ENDGAME_DISCORD_VERSION,
        "discord_ready": client.is_ready(),
        "endgame_enabled": ENABLE_ENDGAME_DISCORD,
        "status_url": ENDGAME_STATUS_URL,
        "archive_url": ENDGAME_ARCHIVE_URL,
        "factions_url": ENDGAME_FACTIONS_URL,
    })

log(f"{ENDGAME_DISCORD_VERSION} armed.")

if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    client.run(DISCORD_TOKEN)
