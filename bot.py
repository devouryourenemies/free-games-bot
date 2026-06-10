#!/usr/bin/env python3
"""Free Games Discord Bot — monitors r/FreeGameFindings and posts to your channel."""

import discord
import json
import logging
import os
import re
import html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from discord.ext import commands, tasks
from pathlib import Path
from urllib.request import urlopen, Request

# ── Configuration ───────────────────────────────────────────────────────
TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "1514351100050800763"))
RSS_URL = "https://www.reddit.com/r/FreeGameFindings/.rss"
LOOKBACK_HOURS = 72          # How far back to scan on first-ever run
CHECK_INTERVAL_HOURS = 6     # How often to poll (default: 4x daily)
DATA_DIR = Path("/data") if os.path.exists("/data") else Path("data")
CACHE_FILE = DATA_DIR / "seen_games.json"

# Entries matching these patterns are always skipped (megathreads, etc.)
SKIP_PATTERNS = [
    r"discussion thread", r"mega thread", r"exiled giveaways",
    r"big offers", r"old active", r"weekly discussion",
    r"itch\.io mega", r"welcome to another", r"index",
]

TYPE_EMOJIS = {
    "game": "🎮", "dlc": "📦", "other": "🎁", "bundle": "📚",
    "music": "🎵", "asset": "🖌️", "book": "📖",
}

# Platforms displayed first (in this order), then alphabetical
PLATFORM_ORDER = [
    "Steam", "Epic Games", "Epic Games Mobile", "GOG",
    "itch.io", "Itch.io",
    "Microsoft/Xbox", "Xbox", "PlayStation", "PS",
    "Nintendo", "Indiegala", "Amazon", "PC", "Console",
    "Android", "iOS",
]

# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("free-games-bot")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ── Bot Setup ───────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = False   # Not needed — read-only bot
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ── Cache ───────────────────────────────────────────────────────────────
def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {"seen": [], "last_run": None}
    return {"seen": [], "last_run": None}


def save_cache(seen_ids):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "seen": list(seen_ids),
        "last_run": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


# ── RSS Feed Parser ─────────────────────────────────────────────────────
def fetch_feed():
    """Fetch and parse r/FreeGameFindings RSS feed."""
    headers = {
        "User-Agent": (
            "FreeGamesBot/1.0 (Discord; "
            "+https://github.com/devouryourenemies/free-games-bot)"
        )
    }
    req = Request(RSS_URL, headers=headers)
    with urlopen(req, timeout=30) as resp:
        data = resp.read()

    root = ET.fromstring(data)
    ns = "{http://www.w3.org/2005/Atom}"
    entries = []

    for entry in root.findall(f"{ns}entry"):
        title_el = entry.find(f"{ns}title")
        link_el = entry.find(f"{ns}link")
        published_el = entry.find(f"{ns}published")

        if title_el is None or title_el.text is None:
            continue

        title = re.sub(r"<[^>]+>", "", title_el.text)
        title = html.unescape(title).strip()
        url = link_el.get("href") if link_el is not None else ""
        pub_raw = published_el.text[:25] if published_el is not None and published_el.text else ""

        try:
            pub_time = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pub_time = datetime.now(timezone.utc)

        # Parse: [Platform] (Type) Game Name
        platform = ""
        content_type = ""
        game_name = title
        plat_match = re.match(r"\[(.+?)\]\s*\((.+?)\)\s+(.+)", title)
        if plat_match:
            platform = plat_match.group(1).strip()
            content_type = plat_match.group(2).strip().lower()
            game_name = plat_match.group(3).strip()

        entry_id = url.split("/")[-1] if url else ""
        entries.append({
            "id": entry_id,
            "title": title,
            "url": url,
            "published": pub_time.isoformat(),
            "platform": platform,
            "content_type": content_type,
            "game_name": game_name,
        })

    return entries


def should_skip(entry):
    title_lower = entry["title"].lower()
    return any(re.search(p, title_lower) for p in SKIP_PATTERNS)


# ── Discord Message Builder ─────────────────────────────────────────────
def build_embed(new_entries):
    """Build a Discord embed from new game entries."""
    # Group by platform
    by_platform = {}
    for e in new_entries:
        plat = e["platform"] or "Other"
        by_platform.setdefault(plat, []).append(e)

    # Sort platforms: specified order first, then alphabetical
    def platform_sort_key(p):
        try:
            return (0, PLATFORM_ORDER.index(p))
        except ValueError:
            return (1, p.lower())

    description_parts = []
    for plat in sorted(by_platform.keys(), key=platform_sort_key):
        items = by_platform[plat]
        lines = [f"**{plat}**"]
        for e in items:
            ctype_emoji = TYPE_EMOJIS.get(e["content_type"], "🎮")
            lines.append(
                f"• {ctype_emoji} **{e['game_name']}** — "
                f"[[Link]]({e['url']})"
            )
        description_parts.append("\n".join(lines))

    description = "\n\n".join(description_parts)

    # Discord embed description limit is 4096 chars — truncate if needed
    if len(description) > 4000:
        description = description[:3997] + "..."

    embed = discord.Embed(
        title=f"🆓 **{len(new_entries)} Free Game{'s' if len(new_entries) > 1 else ''} Found!**",
        description=description,
        color=0x00FF88,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="r/FreeGameFindings • New posts since last check")
    return embed


# ── Main Check Logic ────────────────────────────────────────────────────
async def check_free_games():
    """Fetch RSS, find new entries, post to Discord channel."""
    logger.info("Checking for free games…")

    cache = load_cache()
    seen_ids = set(cache.get("seen", []))
    logger.info(f"Cache has {len(seen_ids)} known entries")

    try:
        entries = fetch_feed()
        logger.info(f"Fetched {len(entries)} entries from RSS")
    except Exception as e:
        logger.error(f"Failed to fetch RSS: {e}")
        return

    filtered = [e for e in entries if not should_skip(e)]
    logger.info(f"{len(filtered)} entries after skip filtering")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    new_entries = []
    for entry in filtered:
        if entry["id"] in seen_ids:
            continue
        try:
            pub = datetime.fromisoformat(entry["published"])
        except (ValueError, TypeError):
            pub = datetime.now(timezone.utc)
        # First run: only respect lookback window
        if not seen_ids and pub < cutoff:
            continue
        new_entries.append(entry)

    # Always update seen set so we don't re-process
    for e in filtered:
        seen_ids.add(e["id"])
    save_cache(seen_ids)

    if not new_entries:
        logger.info("No new free games since last check.")
        return

    new_entries.sort(key=lambda e: e["published"], reverse=True)
    logger.info(f"{len(new_entries)} new free games to post!")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logger.error(f"Channel {CHANNEL_ID} not found — bot might need re-inviting")
        return

    embed = build_embed(new_entries)

    try:
        await channel.send(embed=embed)
        logger.info(f"Posted {len(new_entries)} games to channel {CHANNEL_ID}")
    except discord.Forbidden:
        logger.error(f"No permission to send in channel {CHANNEL_ID}")
    except discord.HTTPException as e:
        logger.error(f"Discord API error: {e}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


# ── Scheduled Task ──────────────────────────────────────────────────────
@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def scheduled_check():
    await check_free_games()


@scheduled_check.before_loop
async def before_scheduled_check():
    await bot.wait_until_ready()


# ── Events ──────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Monitoring channel ID: {CHANNEL_ID}")
    logger.info(f"Check interval: every {CHECK_INTERVAL_HOURS} hours")
    # Run first check immediately on connect
    await check_free_games()


@bot.event
async def on_guild_join(guild):
    """When added to a new server, log it."""
    logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")


# ── Startup ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        sys.exit(1)
    bot.run(TOKEN, log_handler=None)
