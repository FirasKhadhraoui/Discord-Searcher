import os
import re
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import google.generativeai as genai
from dotenv import load_dotenv


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Suppress logs


def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# Start health check server in background thread
threading.Thread(target=run_health_server, daemon=True).start()

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in your .env file")

# Set up Gemini if API key is provided
gemini = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini = genai.GenerativeModel("gemini-2.0-flash")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def fetch_messages(channel: discord.TextChannel, limit: int = 500) -> list[dict]:
    messages = []
    async for msg in channel.history(limit=limit, oldest_first=False):
        if msg.author.bot or not msg.content.strip():
            continue
        messages.append({
            "id": str(msg.id),
            "author": msg.author.display_name,
            "content": msg.content,
            "timestamp": msg.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            "url": msg.jump_url,
            "attachments": [a.url for a in msg.attachments],
        })
    return messages


def keyword_search(query: str, messages: list[dict]) -> list[dict]:
    """Search messages using keyword matching — no API needed."""
    query_lower = query.lower()
    keywords = query_lower.split()

    results = []
    for msg in messages:
        content_lower = msg["content"].lower()
        author_lower = msg["author"].lower()
        attachments_lower = " ".join(msg["attachments"]).lower()
        full_text = f"{content_lower} {author_lower} {attachments_lower}"

        score = 0

        # Exact phrase match (highest score)
        if query_lower in full_text:
            score += 10

        # URL/link detection
        urls = re.findall(r'https?://\S+', msg["content"])
        if urls:
            for kw in keywords:
                if any(kw in url.lower() for url in urls):
                    score += 5

        # Individual keyword matches
        for kw in keywords:
            if kw in content_lower:
                score += 2
            if kw in author_lower:
                score += 1

        # Partial word matches
        for kw in keywords:
            if len(kw) >= 4:
                for word in content_lower.split():
                    if kw in word:
                        score += 1
                        break

        if score > 0:
            results.append((score, msg))

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in results[:10]]


def search_with_gemini(query: str, messages: list[dict]) -> list[dict]:
    """Use Gemini AI to find relevant messages."""
    lines = []
    for msg in messages:
        line = f"[ID:{msg['id']}] [{msg['timestamp']}] {msg['author']}: {msg['content']}"
        if msg["attachments"]:
            line += f" [Attachments: {', '.join(msg['attachments'])}]"
        lines.append(line)
    messages_text = "\n".join(lines)

    # Truncate if too long
    if len(messages_text) > 500_000:
        messages_text = messages_text[:500_000]

    prompt = f"""You are a Discord message search assistant.
Find messages that match the search query.

Each message is formatted as:
[ID:message_id] [timestamp] author: content

Rules:
- Return ONLY the IDs of matching messages, one per line, in format: MATCH:message_id
- After all matches, write a brief summary
- If no matches, say NO_MATCHES and explain why
- Match by topic, keywords, links, usernames, or semantic meaning
- Rank by relevance, return at most 10 matches

Search query: "{query}"

Messages:
{messages_text}"""

    response = gemini.generate_content(prompt)
    response_text = response.text

    # Parse matched IDs
    matched_ids = set()
    for line in response_text.split("\n"):
        line = line.strip()
        if line.startswith("MATCH:"):
            matched_ids.add(line.replace("MATCH:", "").strip())

    matched = [m for m in messages if m["id"] in matched_ids]

    # Fall back to keyword search if Gemini found nothing
    if not matched:
        matched = keyword_search(query, messages)

    return matched


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Syncing slash commands...")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(name="lawej", description="Search channel history by keywords")
@app_commands.describe(
    query="What to search for (keywords, links, names, etc.)",
    channel="Channel to search (defaults to current channel)",
    limit="Number of messages to scan (default: 500, max: 2000)",
)
async def search(
    interaction: discord.Interaction,
    query: str,
    channel: Optional[discord.TextChannel] = None,
    limit: Optional[int] = 500,
):
    limit = max(1, min(limit or 500, 2000))
    target_channel = channel or interaction.channel

    await interaction.response.defer(thinking=True)

    try:
        status_msg = await interaction.followup.send(
            f"🔍 Fetching up to **{limit}** messages from {target_channel.mention}...",
            wait=True,
        )

        messages = await fetch_messages(target_channel, limit=limit)

        if not messages:
            await status_msg.edit(content="❌ No messages found in that channel.")
            return

        if gemini:
            await status_msg.edit(content=f"🤖 Searching **{len(messages)}** messages with Gemini AI...")
        else:
            await status_msg.edit(content=f"🔎 Searching **{len(messages)}** messages...")

        if gemini:
            loop = asyncio.get_event_loop()
            matched = await loop.run_in_executor(None, search_with_gemini, query, messages)
        else:
            matched = keyword_search(query, messages)

        embed = discord.Embed(
            title=f"🔍 Search Results: \"{query}\"",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Searched {len(messages)} messages in #{target_channel.name}")

        if matched:
            results_text = ""
            for i, msg in enumerate(matched, 1):
                content_preview = msg["content"][:120]
                if len(msg["content"]) > 120:
                    content_preview += "..."
                line = (
                    f"**{i}.** [{msg['author']} — {msg['timestamp']}]({msg['url']})\n"
                    f"> {content_preview}\n"
                )
                if len(results_text) + len(line) > 4000:
                    break
                results_text += line

            embed.add_field(
                name=f"📌 {len(matched)} Match(es) Found",
                value=results_text,
                inline=False,
            )
        else:
            embed.add_field(
                name="No Matches",
                value="No messages matched your query. Try different keywords.",
                inline=False,
            )
            embed.color = discord.Color.orange()

        await status_msg.edit(content=None, embed=embed)

    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to read messages in that channel.")
    except Exception as e:
        print(f"Search error: {e}")
        await interaction.followup.send(f"❌ An error occurred: `{str(e)[:200]}`")


@bot.tree.command(name="lawej-multi", description="Search across multiple channels")
@app_commands.describe(
    query="What to search for",
    limit_per_channel="Messages to scan per channel (default: 200, max: 500)",
)
async def search_multi(
    interaction: discord.Interaction,
    query: str,
    limit_per_channel: Optional[int] = 200,
):
    limit_per_channel = max(1, min(limit_per_channel or 200, 500))

    await interaction.response.defer(thinking=True)

    try:
        readable_channels = [
            ch for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).read_message_history
        ]

        if not readable_channels:
            await interaction.followup.send("❌ No readable channels found.")
            return

        status_msg = await interaction.followup.send(
            f"🔍 Scanning **{len(readable_channels)}** channels ({limit_per_channel} msgs each)...",
            wait=True,
        )

        all_messages = []
        for ch in readable_channels:
            try:
                msgs = await fetch_messages(ch, limit=limit_per_channel)
                for m in msgs:
                    m["channel_name"] = ch.name
                all_messages.extend(msgs)
            except Exception:
                pass

        if not all_messages:
            await status_msg.edit(content="❌ No messages found across channels.")
            return

        await status_msg.edit(content=f"🔎 Searching **{len(all_messages)}** messages...")

        matched = keyword_search(query, all_messages)

        embed = discord.Embed(
            title=f"🔍 Multi-Channel Search: \"{query}\"",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(
            text=f"Searched {len(all_messages)} messages across {len(readable_channels)} channels"
        )

        if matched:
            results_text = ""
            for i, msg in enumerate(matched, 1):
                content_preview = msg["content"][:100]
                if len(msg["content"]) > 100:
                    content_preview += "..."
                channel_tag = f"#{msg.get('channel_name', '?')} • " if "channel_name" in msg else ""
                line = (
                    f"**{i}.** [{channel_tag}{msg['author']} — {msg['timestamp']}]({msg['url']})\n"
                    f"> {content_preview}\n"
                )
                if len(results_text) + len(line) > 4000:
                    break
                results_text += line

            embed.add_field(
                name=f"📌 {len(matched)} Match(es) Found",
                value=results_text,
                inline=False,
            )
        else:
            embed.add_field(name="No Matches", value="Nothing matched your query.", inline=False)
            embed.color = discord.Color.orange()

        await status_msg.edit(content=None, embed=embed)

    except Exception as e:
        print(f"Multi-search error: {e}")
        await interaction.followup.send(f"❌ An error occurred: `{str(e)[:200]}`")


@bot.tree.command(name="lawej-help", description="Show how to use the search bot")
async def search_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Lawej Search Bot — Help",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="/lawej <query>",
        value=(
            "Search the current (or specified) channel's history.\n"
            "**Options:**\n"
            "• `channel` — which channel to search (default: current)\n"
            "• `limit` — messages to scan (default: 500, max: 2000)\n\n"
            "**Examples:**\n"
            "• `/lawej query:github`\n"
            "• `/lawej query:meeting notes limit:1000`\n"
            "• `/lawej query:john channel:#general`"
        ),
        inline=False,
    )
    embed.add_field(
        name="/lawej-multi <query>",
        value=(
            "Search across **all channels** in the server.\n"
            "• `limit_per_channel` — messages per channel (default: 200, max: 500)\n\n"
            "**Examples:**\n"
            "• `/lawej-multi query:invite link`\n"
            "• `/lawej-multi query:script limit_per_channel:300`"
        ),
        inline=False,
    )
    embed.add_field(
        name="💡 Search Tips",
        value=(
            "• Search by **keywords**: `error logs`\n"
            "• Search for **links**: `github.com`, `docs.google`\n"
            "• Search by **name**: `john`\n"
            "• Multiple words = searches for all of them\n"
            "• Results are ranked by relevance"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
