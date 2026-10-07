# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Environment:** Python 3.11. Requires `ffmpeg` on PATH (video encoding) and at least one LLM key in `.env` (see `.env.example`). `FAL_KEY` is required for videos: every scene image comes from FAL, and a render with no generated scene fails rather than shipping blank frames.

```bash
# Install (dev deps include pytest, black, flake8, pylint, bandit, mypy)
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run dev server (port 5050, debug=True)
python app.py

# Run production (two options)
gunicorn -c gunicorn.conf.py wsgi:app
docker-compose up -d

# Tests
pytest tests/ -v                                                  # all
pytest -m security                                                # by marker (security | integration | unit | slow)
pytest tests/test_api.py::TestArticleEndpoints::test_list_articles -v   # single test
pytest --cov=. --cov-report=term-missing                          # coverage

# Smoke-test individual modules (each has a __main__ block)
python summarizer.py        # Runs end-to-end against configured LLMs
python scripts/build_moss_sprites.py   # Re-cut Moss's sprites (add --generate for missing poses, FAL)
```

**Port 5050 is hardcoded** in `app.py`, `gunicorn.conf.py`, `Dockerfile`, and `bookmarklet.js`. If you change it, grep for `5050` and update all of them.

## Architecture

The app is a three-stage pipeline: **scrape → summarize → generate video**. Each stage is a separate module with its own external service dependencies, glued together by Flask endpoints in `app.py` and persisted through a single `Article` row whose `status` field drives the state machine.

```
URL ──scrape──▶ Article(status=scraped)
              │
              │  POST /api/articles/{id}/summarize   (202 + background thread)
              ▼
          summarizer.py ──▶ Article(status=summarized)
              │                       tldr, bullets, video_script, hashtags,
              │                       scenes[], hook_variants[], dominant_emotion, style
              │
              │  POST /api/articles/{id}/video       (202 + background thread)
              ▼
          video_generator.py ──▶ Article(status=video_done, video_path=...)
                    │                 static/videos/article_{id}_{ts}.mp4
                    ├── pixel_scenes.py   one pixel-art image per scene (FAL Z-Image Turbo)
                    └── moss_sprite.py    Moss, the mascot, animated over the scenes in code
```

### The scene contract (summarizer ↔ video_generator)

This is the most important cross-file invariant. `summarizer.py` emits a `scenes[]` array where each scene has `{speech, visual, emotion}`, and **concatenating all `scene.speech` in order must equal `video_script`**. `parse_response()` reconstructs `video_script` from scenes if the model omits it. Downstream:

- `pixel_scenes.generate_scene_images(shots)` produces one image per scene from `scene.visual`.
- `compute_scene_durations()` allocates time per scene proportional to `len(speech.split())` so visuals stay aligned with narration.
- If `scenes` is missing, `generate_video()` builds one scene per `chunk_text()` chunk.

When changing the scene schema, update the prompt in `summarizer.get_prompt()`, `parse_response()`'s normalization, and `generate_video()`. Also add a column to `models.Article` **and** to `_migrate_schema()` in `app.py` (see below).

### One locked look: Pixel Night Lab

There is exactly one visual style (`visual_styles.DEFAULT_STYLE = "pixel_night_lab"`); the direction packet, mascot bible and decision history are in `docs/style-lock/`. `scene.visual` describes **what** is on screen; `pixel_scenes.PIXEL_LOOK` is **how**. Two rules learned the hard way:

- **The scene model (Z-Image Turbo) has no negative prompt and draws every noun it reads.** "Never planets or rings" produced a ringed planet over a lab bench. Keep `build_scene_prompt()` positive-only; `generate_image_fal()` appends the no-text suffix.
- **Never name the mascot in a scene prompt.** Mentioning Moss lettered "Moss" onto objects and produced look-alike mascots. Moss is not in the images at all.

The summarizer is told to write physical, literal, wordless visuals (no charts, labels or metaphors). Older summaries that still describe charts and timelines are rewritten by `pixel_scenes.physicalize_visual()` (Groq) before generation.

### Moss, the animated mascot

`moss_sprite.create_moss_overlay()` composites Moss (an original tardigrade) as a sprite layer between the picture and the captions. Sprites live in `assets/moss/sprites/` (rebuild with `scripts/build_moss_sprites.py`). He hosts the hook, every other scene and the last scene; walks in and out; idles and blinks; and reacts to Whisper word timings (amazed on numbers and magnitude words, thinking on questions, waving on "follow"). Motion is stepped at 12 fps on a 4 px grid to read as a game sprite. His feet sit at `FEET_Y`, above the caption band, which `test_overlay_never_enters_the_caption_band` enforces.

### Multi-provider fallback chain (summarizer)

`summarize_article()` tries providers in order and returns on first success:

1. **Kimi K2** via OpenRouter (primary, best quality/$ — ~$0.005/story)
2. **Qwen3.7 Flash** via OpenRouter (cost fallback — ~$0.0013/story)
3. **Groq Llama 3.3 70B** (speed fallback, free tier)
4. **Gemini 2.5 Flash** (budget floor)

All four share the same `get_prompt()` and `parse_response()`, so adding a provider means writing a `summarize_with_X()` that returns `parse_response(text)` and inserting it in the chain inside `summarize_article()`. Note: OpenRouter hosts both Kimi and Qwen — one key covers two stages.

**Reasoning models need a bigger output budget.** Qwen3.7 Flash, DeepSeek V4 Flash, GLM 4.7 Flash and gpt-oss emit a hidden chain of thought before the first JSON character, and those tokens count against `max_tokens`. At the old 4500 default they truncate mid-string on every call (qwen3.7-flash measured 6,850 reasoning tokens before answering). `_is_reasoning_model()` in `summarizer.py` raises the cap to 16000 and the timeout to 240s. Add any new reasoning model to `_REASONING_MODELS` or it will fail 100% of the time. Such a model can also return a **null** `content` field rather than an error when it runs out of budget, so `_call_openrouter()` checks for empty content before parsing.

A **separate** Groq client (`video_generator.get_groq_client()`) is used by `pixel_scenes.physicalize_visual()` and story discovery. Without `GROQ_API_KEY` the scene rewrite is skipped and the original visual is used.

### Background execution + status polling

`/api/articles/{id}/summarize` and `/api/articles/{id}/video` immediately flip status (`summarizing` / `generating_video`), spawn a daemon `Thread` with a manually-captured `app.app_context()`, and return 202. Both endpoints return 409 if the article is already in a processing state. The frontend polls `/api/articles` and re-renders only when `articlesChanged()` detects an id/status/video_path/tldr diff — this prevents flicker during polling.

Status values (strings in `Article.status`): `scraped → summarizing → summarized → generating_video → video_done`. Any failure sets `failed`.

### Video-gen watchdog (and why there's no Kokoro pre-warm)

`run_video_in_background` wraps the generation call in a `threading.Timer(VIDEO_TIMEOUT_SECONDS, ...)` that flips status to `failed` if the worker thread hasn't finished in time (default 900s, override via `VIDEO_TIMEOUT_SECONDS` env). Python cannot safely kill a thread, so the worker may keep running in the background — on eventual completion we re-check status and **discard the output** if the watchdog already declared failure. This is the only thing keeping the UI from spinning forever when `from kokoro import KPipeline` hangs on a cold torch dispatch init.

Do **not** add a startup pre-warm for Kokoro. It seems helpful (pay the torch cost once up front) but is actively harmful: a hung warmup thread holds Python's import lock, which means the first real request's `from kokoro import KPipeline` blocks forever waiting for the lock. Pre-warm helps only if it completes; when it hangs, it breaks every subsequent request. Let each request pay its own cold-start cost and let the watchdog catch pathological hangs.

### SQLite auto-migration

`_migrate_schema()` in `app.py` runs at startup and adds missing columns idempotently (`scenes`, `hook_variants`, `dominant_emotion`, `style`). When adding a new column, update it in BOTH `models.Article` and the `new_cols` list in `_migrate_schema` — there's no Alembic. For anything beyond adding a nullable column, introduce a real migration tool.

### SSRF prevention

Any server-side URL fetch must go through `validate_url()` in `app.py`, which resolves the hostname and rejects private/loopback/link-local ranges. `scrape_url_content()` re-validates after redirects. The bookmarklet path (`POST /api/scrape`) skips this because the URL was already loaded in the user's browser.

### Parallel image generation

`pixel_scenes.generate_scene_images()` generates one image per scene in a `ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS=6)`, then expands it into per-shot framings. A failed scene reuses the nearest generated scene; if none succeeds the render fails instead of shipping blank frames. A video costs about $0.005 per scene (`pixel_scenes.estimate_video_cost`).

**Scene check** (`scene_check.py`): before stitching, a cheap OpenRouter vision model (`SCENE_CHECK_MODEL`, default `google/gemini-2.5-flash-lite`) looks at each scene image. Lettering, an unasked-for character, or an image that doesn't show the scene's `visual` triggers one regeneration (at most `MAX_SCENE_RETRIES`=3 per video), and the image with fewer issues is kept. Stray objects are advisory only, because the checker kept flagging the style's own energy lines and glows. The checker never blocks a render, and results land in `visual_sources`. Tests run with `SCENE_CHECK=off` (conftest). The Gemini free tier was tried and is unusable for this: 5 requests/min, plus 503s.

## Project-specific conventions

- **Captions are burned in by the renderer, never by the image model.** `create_caption_clips()` draws word-synced (Whisper) captions plus the opening `cover_line` headline. Scene prompts never ask for text, and `generate_image_fal()` appends a no-text suffix. Captions take their spelling from the script (`align_words_to_script()`), not from Whisper's guesses.
- **Pacing:** shots are capped at `MAX_SHOT_DURATION` (2.5s), and consecutive shots of one image must use visibly different framings (`shot_variant()` with `pixel_scenes.FRAMINGS`) with a perceptible push (`BODY_SHOT_ZOOM`). A scene that looks like one frozen still is the main reason viewers swipe.
- **Restarting locally:** the app runs under the `com.scapweb.clipper` LaunchAgent with auto-reload off, so code edits need `launchctl kickstart -k gui/$(id -u)/com.scapweb.clipper`.
- **User-facing errors are generic; full context goes to `logger.error(..., exc_info=True)`.** Don't leak provider error strings to the client.
- **Logging uses `logger.info/error` everywhere**, with `[Tag]` prefixes for pipeline stages (`[Pixel]`, `[Captions]`, `[Music]`).
- **Frontend has no build step.** `static/app.js` is plain ES-modern JS served directly. Don't introduce bundlers without a reason.
- **Tests never spend money:** `tests/conftest.py` blanks `FAL_KEY` (modules call `load_dotenv()`, which never overrides a set variable). Export `FAL_KEY` in the shell only for an intentional integration run. Route tests build their own temp SQLite database per module.
- **OpenRouter calls send `HTTP-Referer: http://localhost:5050` and `X-Title: Clipper`.** If deploying publicly, update these in `summarizer._call_openrouter()`.
