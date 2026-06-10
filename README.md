# 🆓 Free Games Discord Bot

Monitors [r/FreeGameFindings](https://reddit.com/r/FreeGameFindings) and posts free game deals to your Discord channel automatically.

## Features
- 🎮 Covers **all platforms** — Steam, Epic, GOG, Itch.io, Xbox, PS, Nintendo, Indiegala, etc.
- 🧹 Smart filtering — auto-skips discussion threads and megathreads
- 🚫 **No mentions, no interaction** — posts silently, no one can ping the bot
- 🔄 Runs every 6 hours (4x daily) — catches deals before they expire
- 📦 Dedup cache — never re-posts the same deal

## Deploy to Railway (Free — ~$0/mo)

### 1. Create a GitHub repo
```bash
# From this directory:
git init
git add .
git commit -m "Initial commit"
gh repo create free-games-bot --public --push
```
(Or push to your existing GitHub account any way you like.)

### 2. Create a Discord Application
1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it "Free Games"
3. Go to **Bot** → **Reset Token** → copy the new token
4. **DO NOT use the token I already saw** — reset it since it was pasted in chat
5. Disable **Public Bot** (keeps it out of search)
6. Disable **Message Content Intent** (bot doesn't read messages)

### 3. Deploy on Railway
1. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**
2. Select your `free-games-bot` repo
3. Go to **Variables** → add:
   - `DISCORD_TOKEN` → your new bot token
   - `DISCORD_CHANNEL_ID` → `1514351100050800763`
4. Go to **Settings** → set **Start Command** to `python bot.py`
5. Railway auto-detects Python and installs deps. The bot starts automatically.

### 4. Invite the bot to your server
Replace `YOUR_CLIENT_ID` below (from General Information page in Developer Portal):
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=2048&scope=bot
```
- `permissions=2048` = **Send Messages** + **Read Message History** only
- No mention permissions, no read content, no slash commands

### 5. Lock the channel
In Discord:
1. Right-click the channel → **Edit Channel** → **Permissions**
2. `@everyone` → ❌ **Send Messages**, ❌ **Mention @everyone**, ❌ **Use External Emoji**
3. Only the bot role should have **Send Messages** ✅

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Bot token from Discord Developer Portal |
| `DISCORD_CHANNEL_ID` | ❌ | Channel to post in (default: 1514351100050800763) |

## Local Testing
```bash
pip install discord.py
DISCORD_TOKEN="your_token" python bot.py
```

## Architecture
```
bot.py              # Main bot — RSS fetcher + Discord poster
requirements.txt    # Dependencies (discord.py only)
railway.json        # Railway deploy config
Procfile            # Render/Heroku deploy config
.gitignore
data/               # Auto-created cache dir (DON'T commit)
```

The cache file (`data/seen_games.json`) tracks which Reddit posts have already been posted, so you never get duplicates.
