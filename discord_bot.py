"""
Clipper Discord Bot — 3-channel video generation workflow.

Channel flow:
  1. INPUT channel   — Paste article URLs here
  2. PROCESSING channel — Bot posts progress updates
  3. OUTPUT channel  — Final video is delivered here

Also runs the Flask API in a background thread so the web dashboard stays accessible.
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Configuration
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_INPUT = int(os.getenv("DISCORD_CHANNEL_INPUT", "0"))
CHANNEL_PROCESSING = int(os.getenv("DISCORD_CHANNEL_PROCESSING", "0"))
CHANNEL_OUTPUT = int(os.getenv("DISCORD_CHANNEL_OUTPUT", "0"))
DISCOVERY_ENABLED = os.getenv("DISCOVERY_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
try:
    DISCOVERY_HOUR_UTC = min(23, max(0, int(os.getenv("DISCOVERY_HOUR_UTC", "9"))))
except ValueError:
    DISCOVERY_HOUR_UTC = 9
try:
    DISCOVERY_TOP_N = max(1, int(os.getenv("DISCOVERY_TOP_N", "2")))
except ValueError:
    DISCOVERY_TOP_N = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("clipper.bot")

# ============================================================
# Flask app (imported for DB + pipeline access)
# ============================================================

from app import (
    app,
    db,
    scrape_url_content,
    start_tiktok_status_poller,
)
from models import (
    Article,
    find_matching_hook_index,
    valid_hook_index,
)
from summarizer import summarize_article
from video_generator import generate_video
from tts_preview import format_results_table, render_previews
from story_finder import discover_and_score, format_shortlist, run_discovery

# ============================================================
# Discord Bot
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Track active jobs to avoid duplicates
active_jobs = set()
discovery_task = None
# ``python app.py`` sets this before connecting the embedded bot because its
# APScheduler is then the single owner of daily discovery.
DISCOVERY_SCHEDULER_MANAGED_EXTERNALLY = False

# URL regex
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


async def process_article_url(
    url: str,
    trigger_message: discord.Message,
    viral_score: float = None,
):
    """Full pipeline: scrape → summarize → generate video → post to output channel."""
    processing_channel = bot.get_channel(CHANNEL_PROCESSING)
    output_channel = bot.get_channel(CHANNEL_OUTPUT)

    if not processing_channel or not output_channel:
        logger.error("Processing or output channel not found")
        return

    # React to the original message to show we picked it up
    try:
        await trigger_message.add_reaction("\u2699\ufe0f")  # gear emoji
    except Exception:
        pass

    progress_msg = await processing_channel.send(
        f"**Processing:** {url}\n> Scraping article..."
    )

    try:
        # Step 1: Scrape
        with app.app_context():
            # Check if already exists
            existing = Article.query.filter_by(url=url).first()
            if existing and viral_score is not None:
                existing.viral_score = viral_score
                db.session.commit()
            if existing and existing.video_path:
                video_file = Path("static/videos") / existing.video_path
                if video_file.exists():
                    await progress_msg.edit(
                        content=f"**Already processed:** {url}\n> Re-posting existing video."
                    )
                    await _post_video(
                        output_channel,
                        existing,
                        video_file,
                    )
                    await trigger_message.add_reaction("\u2705")
                    return

            scraped = scrape_url_content(url)
            if not scraped["content"] or len(scraped["content"]) < 100:
                await progress_msg.edit(
                    content=f"**Failed:** {url}\n> Could not extract enough content from this page."
                )
                await trigger_message.add_reaction("\u274c")
                return

            # Save to DB
            if existing:
                article = existing
            else:
                article = Article(
                    url=scraped["url"],
                    title=scraped["title"],
                    content=scraped["content"],
                    hero_image=scraped["hero_image"],
                    site_name=scraped["site_name"],
                    status="scraped",
                    viral_score=viral_score,
                )
                db.session.add(article)
                db.session.commit()

        await progress_msg.edit(
            content=(
                f"**Processing:** {scraped['title'][:80]}\n"
                f"> Scraped! Generating AI summary..."
            )
        )

        # Step 2: Summarize
        with app.app_context():
            from visual_styles import STYLES as VISUAL_STYLES

            article = db.session.get(Article, article.id)
            result = summarize_article(article.title, article.content)
            article.tldr = result["tldr"]
            article.bullets = json.dumps(result["bullets"])
            article.video_script = result["video_script"]
            article.hashtags = json.dumps(result.get("hashtags", []))
            article.cover_line = result.get("cover_line") or None
            article.cta_question = result.get("cta_question") or None
            article.search_caption = result.get("search_caption") or None
            article.series_lane = result.get("series_lane") or None

            # Engagement metadata (scene-based generation)
            scenes = result.get("scenes") or []
            article.scenes = json.dumps(scenes) if scenes else None
            hook_variants = result.get("hook_variants") or []
            article.hook_variants = json.dumps(hook_variants) if hook_variants else None
            article.best_hook_index = valid_hook_index(
                result.get("best_hook_index"),
                hook_variants,
            )
            article.hook_index_used = None
            article.dominant_emotion = result.get("dominant_emotion") or None
            suggested = result.get("suggested_style")
            if suggested and suggested in VISUAL_STYLES:
                article.style = suggested

            article.status = "summarized"
            article.summarized_at = datetime.now(timezone.utc)
            db.session.commit()

            script = article.video_script
            title = article.title
            article_id = article.id
            article_scenes = scenes or None
            article_style = article.style
            article_emotion = article.dominant_emotion
            article_cover_line = article.cover_line
            article_series_lane = article.series_lane
            article_hero_image = article.hero_image

        await progress_msg.edit(
            content=(
                f"**Processing:** {title[:80]}\n"
                f"> Summarized! Generating video (this takes 2-5 min)..."
            )
        )

        # Step 3: Generate video
        with app.app_context():
            video_path = generate_video(
                article_id=article_id,
                title=title,
                script=script,
                image_source="ai",
                scenes=article_scenes,
                style_key=article_style,
                emotion=article_emotion,
                cover_line=article_cover_line,
                series_lane=article_series_lane,
                hero_image=article_hero_image,
            )

            article = db.session.get(Article, article_id)
            relative_path = os.path.basename(video_path)
            article.video_path = relative_path
            article.hook_index_used = find_matching_hook_index(
                hook_variants,
                article_scenes or [],
            )
            article.status = "video_done"
            article.video_generated_at = datetime.now(timezone.utc)
            db.session.commit()

        await progress_msg.edit(
            content=(
                f"**Done:** {title[:80]}\n"
                f"> Video generated! Posting to output channel..."
            )
        )

        # Step 4: Post to output channel
        with app.app_context():
            article = db.session.get(Article, article_id)
            video_file = Path("static/videos") / article.video_path
            await _post_video(
                output_channel,
                article,
                video_file,
            )

        # Mark success
        await trigger_message.add_reaction("\u2705")  # checkmark
        await progress_msg.edit(
            content=f"**Complete:** {title[:80]}\n> Video posted to <#{CHANNEL_OUTPUT}>"
        )

    except Exception as e:
        logger.error(f"Pipeline failed for {url}: {e}", exc_info=True)
        await progress_msg.edit(
            content=f"**Error processing:** {url}\n> {str(e)[:200]}"
        )
        try:
            await trigger_message.add_reaction("\u274c")
        except Exception:
            pass

    finally:
        active_jobs.discard(url)


async def _post_video(
    channel: discord.TextChannel,
    article,
    video_file: Path,
):
    """Post the final video with metadata to the output channel."""
    with app.app_context():
        hashtags = []
        if article.hashtags:
            try:
                hashtags = json.loads(article.hashtags)
            except (json.JSONDecodeError, TypeError):
                pass

        hashtag_str = " ".join(hashtags) if hashtags else ""
        tldr = article.tldr or ""

        caption = (
            f"**{article.title}**\n\n"
            f"{tldr}\n\n"
            f"{hashtag_str}\n\n"
            f"Source: {article.url}"
        )

    # Discord file upload limit is 25MB (or 50MB for boosted servers)
    file_size = video_file.stat().st_size
    if file_size > 25 * 1024 * 1024:
        message = await channel.send(
            f"{caption}\n\n> Video too large for Discord upload ({file_size / 1024 / 1024:.1f}MB). "
            f"Access it from the web dashboard."
        )
    else:
        message = await channel.send(
            content=caption,
            file=discord.File(str(video_file), filename=f"{article.title[:50]}.mp4"),
        )

    return message


# ============================================================
# Event Handlers
# ============================================================

async def _scheduled_discovery_run():
    """Run discovery off the event loop and post stored videos/results."""
    processing_channel = bot.get_channel(CHANNEL_PROCESSING)
    output_channel = bot.get_channel(CHANNEL_OUTPUT)
    if not processing_channel or not output_channel:
        logger.error("Discovery channels are unavailable")
        return

    progress = await processing_channel.send(
        f"**Daily discovery:** collecting and scoring science stories for "
        f"{DISCOVERY_TOP_N} video(s)..."
    )
    try:
        candidates = await asyncio.to_thread(discover_and_score)
        await progress.edit(
            content=(
                "**Daily discovery shortlist:**\n"
                + format_shortlist(candidates, limit=6)
            )[:1950]
        )
        if not candidates:
            return

        result = await asyncio.to_thread(
            run_discovery,
            DISCOVERY_TOP_N,
            candidates,
        )
        completed = 0
        failed = 0
        for item in result["results"]:
            if item["status"] != "video_done":
                failed += 1
                continue
            with app.app_context():
                article = db.session.get(Article, item["article_id"])
                video_file = (
                    Path("static/videos") / article.video_path
                    if article and article.video_path
                    else None
                )
            if article and video_file and video_file.exists():
                await _post_video(
                    output_channel,
                    article,
                    video_file,
                )
                completed += 1

        await processing_channel.send(
            f"**Daily discovery complete:** {completed} video(s) posted, "
            f"{failed} failed/skipped."
        )
    except Exception as exc:
        logger.error("Scheduled discovery failed: %s", exc, exc_info=True)
        await progress.edit(
            content=f"**Daily discovery failed:** `{str(exc)[:180]}`"
        )


async def _discovery_scheduler():
    """Sleep until the configured UTC hour and run once per day."""
    while not bot.is_closed():
        now = datetime.now(timezone.utc)
        next_run = now.replace(
            hour=DISCOVERY_HOUR_UTC,
            minute=0,
            second=0,
            microsecond=0,
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        delay = max(1.0, (next_run - now).total_seconds())
        logger.info("Next automatic discovery run: %s", next_run.isoformat())
        await asyncio.sleep(delay)
        await _scheduled_discovery_run()


def _discord_discovery_scheduler_enabled():
    return DISCOVERY_ENABLED and not DISCOVERY_SCHEDULER_MANAGED_EXTERNALLY


@bot.event
async def on_ready():
    global discovery_task
    logger.info(f"Clipper bot connected as {bot.user} (ID: {bot.user.id})")
    logger.info(f"  Input channel:      {CHANNEL_INPUT}")
    logger.info(f"  Processing channel:  {CHANNEL_PROCESSING}")
    logger.info(f"  Output channel:      {CHANNEL_OUTPUT}")
    start_tiktok_status_poller()
    if (
        _discord_discovery_scheduler_enabled()
        and (discovery_task is None or discovery_task.done())
    ):
        discovery_task = asyncio.create_task(
            _discovery_scheduler(),
            name="clipper-daily-discovery",
        )
        logger.info(
            "Daily discovery enabled at %02d:00 UTC (top %d)",
            DISCOVERY_HOUR_UTC,
            DISCOVERY_TOP_N,
        )


@bot.event
async def on_message(message: discord.Message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # Commands should work in the input channel without also triggering URL jobs.
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # Only process messages in the input channel
    if message.channel.id != CHANNEL_INPUT:
        await bot.process_commands(message)
        return

    # Find URLs in the message
    urls = URL_PATTERN.findall(message.content)
    if not urls:
        return

    for url in urls:
        # Skip if already processing this URL
        if url in active_jobs:
            await message.add_reaction("\u23f3")  # hourglass
            continue

        active_jobs.add(url)
        # Run the pipeline in a background task so we don't block the bot
        bot.loop.create_task(process_article_url(url, message))


# ============================================================
# Manual commands (work in any channel the bot can see)
# ============================================================

@bot.command(name="generate")
async def cmd_generate(ctx: commands.Context, url: str = None):
    """Manually trigger video generation for a URL. Usage: !generate <url>"""
    if not url:
        await ctx.send("Usage: `!generate <url>`")
        return

    if url in active_jobs:
        await ctx.send("This URL is already being processed.")
        return

    active_jobs.add(url)
    await ctx.send(f"Starting pipeline for: {url}")
    bot.loop.create_task(process_article_url(url, ctx.message))


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    """Show current processing status."""
    if active_jobs:
        jobs_list = "\n".join(f"  - {u}" for u in active_jobs)
        await ctx.send(f"**Active jobs ({len(active_jobs)}):**\n{jobs_list}")
    else:
        await ctx.send("No active jobs. Paste a URL in the input channel to start.")


@bot.command(name="stats")
async def cmd_stats(ctx: commands.Context):
    """Show article/video statistics."""
    with app.app_context():
        total = Article.query.count()
        videos = Article.query.filter(Article.video_path.isnot(None)).count()
        await ctx.send(
            f"**Clipper Stats:**\n"
            f"  Articles: {total}\n"
            f"  Videos: {videos}\n"
            f"  Active jobs: {len(active_jobs)}"
        )


@bot.command(name="voices")
async def cmd_voices(ctx: commands.Context):
    """Render and post short previews for every available TTS engine/voice."""
    progress = await ctx.send(
        "Generating voice previews. Kokoro takes a moment; Qwen3 can take several minutes on CPU..."
    )
    try:
        results = await asyncio.to_thread(render_previews)
        table = format_results_table(results)
        await progress.edit(content=f"**Voice preview results:**\n```text\n{table[:1800]}\n```")

        posted = 0
        for result in results:
            if result["status"] != "ok" or not result["path"]:
                continue
            preview_path = Path(result["path"])
            if not preview_path.exists():
                continue
            if preview_path.stat().st_size > 8 * 1024 * 1024:
                await ctx.send(
                    f"Skipped **{result['engine']} / {result['voice']}** because the preview exceeds 8MB."
                )
                continue
            await ctx.send(
                content=f"**{result['engine']} — {result['voice']}**",
                file=discord.File(str(preview_path), filename=preview_path.name),
            )
            posted += 1

        if not posted:
            await ctx.send("No preview files were generated. Check the table above and bot logs.")
    except Exception as exc:
        logger.error("Voice preview command failed: %s", exc, exc_info=True)
        await progress.edit(content=f"Voice preview generation failed: `{str(exc)[:180]}`")


@bot.command(name="discover")
async def cmd_discover(ctx: commands.Context, top_n: int = None):
    """Score current science stories and process the highest-ranked unseen URLs."""
    requested_top_n = DISCOVERY_TOP_N if top_n is None else max(1, min(5, top_n))
    processing_channel = bot.get_channel(CHANNEL_PROCESSING)
    if not processing_channel:
        await ctx.send("The configured processing channel is unavailable.")
        return

    progress = await processing_channel.send(
        "**Manual discovery:** collecting RSS and r/science candidates..."
    )
    try:
        candidates = await asyncio.to_thread(discover_and_score)
        await progress.edit(
            content=(
                "**Scored science shortlist:**\n"
                + format_shortlist(candidates, limit=8)
                + f"\n\nProcessing the top {min(requested_top_n, len(candidates))}."
            )[:1950]
        )
        if not candidates:
            return

        for candidate in candidates[:requested_top_n]:
            if candidate.url in active_jobs:
                await processing_channel.send(
                    f"Skipping an already-active discovery job: <{candidate.url}>"
                )
                continue
            active_jobs.add(candidate.url)
            await process_article_url(
                candidate.url,
                ctx.message,
                viral_score=candidate.viral_score,
            )
    except Exception as exc:
        logger.error("Manual discovery failed: %s", exc, exc_info=True)
        await progress.edit(
            content=f"**Discovery failed:** `{str(exc)[:180]}`"
        )


# ============================================================
# Run Flask in background + Discord bot
# ============================================================

def run_flask():
    """Run Flask API server in a background thread."""
    app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)


def main():
    if not DISCORD_TOKEN or DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
        logger.error(
            "DISCORD_BOT_TOKEN not set! "
            "Get one from https://discord.com/developers/applications"
        )
        logger.info("Starting Flask-only mode (no Discord bot)...")
        app.run(host="0.0.0.0", port=5050, debug=True)
        return

    if not all([CHANNEL_INPUT, CHANNEL_PROCESSING, CHANNEL_OUTPUT]):
        logger.error(
            "Discord channel IDs not configured! Set DISCORD_CHANNEL_INPUT, "
            "DISCORD_CHANNEL_PROCESSING, DISCORD_CHANNEL_OUTPUT in .env"
        )
        return

    # Start Flask API in a background thread
    logger.info("Starting Flask API server on port 5050...")
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Start Discord bot (blocking)
    logger.info("Starting Discord bot...")
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
