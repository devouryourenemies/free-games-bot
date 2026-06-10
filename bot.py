#!/usr/bin/env python3
"""Free Games Discord Bot — powered by r/FreeGameFindings + gg.deals links."""

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
from urllib.parse import quote
from urllib.request import urlopen, Request

# ── Configuration ───────────────────────────────────────────────────────
TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "1514351100050800763"))
RSS_URL = "https://www.reddit.com/r/FreeGameFindings/.rss"
LOOKBACK_HOURS = 72
CHECK_INTERVAL_HOURS = 24
DATA_DIR = Path("/data") if os.path.exists("/data") else Path("data")
CACHE_FILE = DATA_DIR / "seen_games.json"

SKIP_PATTERNS = [
    r"discussion thread", r"mega thread", r"exiled giveaways",
    r"big offers", r"old active", r"weekly discussion",
    r"itch\.io mega", r"welcome to another", r"index",
]

PLATFORM_COLORS = {
    "steam": 0x1B2838,
    "epic": 0x313131,
    "gog": 0x8B2FC9,
    "itch": 0xFA5C5C,
    "itch.io": 0xFA5C5C,
    "xbox": 0x107C10,
    "playstation": 0x003087,
    "ps": 0x003087,
    "nintendo": 0xE60012,
    "indiegala": 0xE87D2F,
    "microsoft": 0x00A4EF,
    "amazon": 0xFF9900,
}

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
intents.message_content = False
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


# ── RSS Parser ──────────────────────────────────────────────────────────
def fetch_feed():
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


def gg_deals_url(game_name):
    """Build a gg.deals search URL for the given game name."""
    clean_name = re.sub(r'[^\w\s-]', '', game_name).strip()
    if not clean_name:
        return None
    return f"https://gg.deals/search/?q={quote(clean_name)}"


def platform_emoji(platform):
    """Map platform names to Discord emoji-like text icons."""
    p = platform.lower()
    icons = {
        "steam": "<:steam:1324567890123456789>",
    }
    # Use text emojis since custom emoji IDs would differ per server
    emoji_map = {
        "steam": "🟦",
        "epic": "⭐",
        "gog": "🟣",
        "itch.io": "🟥",
        "itch": "🟥",
        "xbox": "🟩",
        "playstation": "🔵",
        "ps": "🔵",
        "nintendo": "🔴",
        "indiegala": "🟠",
        "microsoft": "🟦",
        "amazon": "🟧",
        "pc": "💻",
        "console": "🎮",
        "android": "📱",
        "ios": "📱",
    }
    for key, emoji in emoji_map.items():
        if key in p:
            return emoji
    return "🎮"


def game_url_icon(content_type):
    icons = {"game": "🎮", "dlc": "📦", "other": "🎁", "bundle": "📚"}
    return icons.get(content_type, "🎮")


# ── Embed Builder ───────────────────────────────────────────────────────
def build_embeds(new_entries):
    """Build shiny Discord embeds with gg.deals links."""
    # Sort newest first
    new_entries.sort(key=lambda e: e["published"], reverse=True)

    embed_color = 0xFF6B35  # gg.deals orange

    # Build a clean compact field per game
    embeds = []
    current_embed = None
    field_count = 0

    for entry in new_entries:
        game = entry["game_name"] or entry["title"]
        plat = entry["platform"] or "Other"
        ctype = entry["content_type"] or "game"
        plut = platform_emoji(plat)
        cicon = game_url_icon(ctype)

        # Build gg.deals link
        gglink = gg_deals_url(game)
        if gglink:
            link_text = f"[gg.deals]({gglink})"
        else:
            link_text = f"[Reddit]({entry['url']})"

        # Format time ago
        try:
            pub = datetime.fromisoformat(entry["published"])
            now = datetime.now(timezone.utc)
            diff = now - pub
            if diff.days > 0:
                time_str = f"{diff.days}d ago"
            elif diff.seconds >= 3600:
                time_str = f"{diff.seconds // 3600}h ago"
            else:
                time_str = f"{diff.seconds // 60}m ago"
        except:
            time_str = "recent"

        # Build embed field value
        field_value = (
            f"**Platform:** {plut} {plat}  •  **Type:** {ctype.upper()}  •  **{time_str}**\n"
            f"**Check prices on:** {link_text}"
        )

        # Start new embed if needed (max 25 fields per embed)
        if current_embed is None or field_count >= 25:
            if current_embed:
                current_embed.set_footer(
                    text="gg.deals • Free Games Tracker",
                    icon_url="https://gg.deals/favicon.ico"
                )
                embeds.append(current_embed)
            current_embed = discord.Embed(
                title="🆓 **Free Games Found!**",
                description=f"**{len(new_entries)}** new free games & DLCs available now",
                color=embed_color,
                timestamp=datetime.now(timezone.utc),
            )
            current_embed.set_thumbnail(
                url="https://gg.deals/favicon-128x128.png"
            )
            field_count = 0

        current_embed.add_field(
            name=f"{cicon} **{game}**",
            value=field_value,
            inline=False,
        )
        field_count += 1

    # Add the last embed
    if current_embed:
        current_embed.set_footer(
            text="gg.deals • Free Games Tracker",
            icon_url="https://gg.deals/favicon.ico"
        )
        embeds.append(current_embed)

    return embeds


# ── Main Check ─────────────────────────────────────────────────────────
async def check_free_games():
    logger.info("🔍 Checking for free games…")

    cache = load_cache()
    seen_ids = set(cache.get("seen", []))

    try:
        entries = fetch_feed()
        logger.info(f"📡 Fetched {len(entries)} entries from RSS")
    except Exception as e:
        logger.error(f"❌ Failed to fetch RSS: {e}")
        return

    filtered = [e for e in entries if not should_skip(e)]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    new_entries = []
    for entry in filtered:
        if entry["id"] in seen_ids:
            continue
        try:
            pub = datetime.fromisoformat(entry["published"])
        except (ValueError, TypeError):
            pub = datetime.now(timezone.utc)
        if not seen_ids and pub < cutoff:
            continue
        new_entries.append(entry)

    for e in filtered:
        seen_ids.add(e["id"])
    save_cache(seen_ids)

    if not new_entries:
        logger.info("✅ No new free games since last check.")
        return

    logger.info(f"🎯 {len(new_entries)} new free games!")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logger.error(f"❌ Channel {CHANNEL_ID} not found")
        return

    embeds = build_embeds(new_entries)

    try:
        for embed in embeds:
            await channel.send(embed=embed)
        logger.info(f"✅ Posted {len(new_entries)} games in {len(embeds)} embeds")
    except discord.Forbidden:
        logger.error("❌ No permission to send in channel")
    except discord.HTTPException as e:
        logger.error(f"❌ Discord API error: {e}")
    except Exception as e:
        logger.error(f"❌ Failed: {e}")


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
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"📬 Monitoring channel ID: {CHANNEL_ID}")
    logger.info(f"⏰ Check interval: every {CHECK_INTERVAL_HOURS} hours")
    await check_free_games()


@bot.event
async def on_guild_join(guild):
    logger.info(f"🏠 Joined guild: {guild.name} (ID: {guild.id})")


# ── Startup ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        sys.exit(1)
    bot.run(TOKEN, log_handler=None)
