# Discord Search Bot — Setup Guide

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

## 2. Create a Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application** → give it a name
3. Go to **Bot** tab → click **Add Bot**
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent**
   - **Server Members Intent** (optional)
5. Copy the **Token** (you'll need this)
6. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Read Messages/View Channels`, `Read Message History`, `Send Messages`, `Embed Links`
7. Copy the generated URL and open it to invite the bot to your server

## 3. Configure Environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:
```
DISCORD_TOKEN=your_bot_token_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

Get your Anthropic API key from: https://console.anthropic.com/

## 4. Run the Bot

```bash
python bot.py
```

The bot will log in and sync slash commands. This may take up to 1 hour to propagate globally, but usually happens within a minute for your own server.

## 5. Use It

In any channel where the bot has access:

- `/search query:github link` — search current channel
- `/search query:meeting notes channel:#general limit:1000` — search specific channel
- `/search-multi query:deployment script` — search all channels
- `/search-help` — show help

## Notes

- The bot scans up to 2000 messages per channel (Discord API limit per request)
- Large channels may take 10-30 seconds to search
- Claude analyzes the messages semantically, so natural language queries work well
- The bot skips its own messages and empty messages automatically
