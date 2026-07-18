# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Environment:** Python 3.11. Requires `ffmpeg` on PATH (video encoding) and at least one LLM key in `.env` (see `.env.example`). `FAL_KEY` is optional — without it, videos use gradient backgrounds instead of AI images.

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
python video_generator.py   # Generates a test video (hits FAL + Kokoro)
python visual_styles.py     # Prints style list + sample prompt
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
                    └── uses visual_styles.apply_style() for image prompts
```

### The scene contract (summarizer ↔ video_generator)

This is the most important cross-file invariant. `summarizer.py` emits a `scenes[]` array where each scene has `{speech, visual, emotion}`, and **concatenating all `scene.speech` in order must equal `video_script`**. `parse_response()` reconstructs `video_script` from scenes if the model omits it. Downstream:

- `video_generator.generate_scene_images(scenes, style_key)` produces one image per scene via `visual_styles.apply_style(scene.visual, style_key)`.
- `compute_scene_durations()` allocates time per scene proportional to `len(speech.split())` so visuals stay aligned with narration.
- If `scenes` is missing, `generate_video()` falls back to the legacy path: `generate_themed_images()` + `chunk_text()`-based pacing.

When changing the scene schema, update all three: the prompt in `summarizer.get_prompt()`, `parse_response()`'s normalization, and the scene-based branch of `generate_video()`. Also add a column to `models.Article` **and** to `_migrate_schema()` in `app.py` (see below).

### Visual-style separation (WHAT vs HOW)

`scene.visual` describes **what** is on screen (subject, action, setting). `visual_styles.STYLES[key]` provides **how** it looks (medium, lighting, aesthetic). `apply_style(visual, key, is_hook)` composes them. Scene `visual` strings must NOT mention art style or medium — doing so fights the style preset and produces inconsistent imagery. The prompt in `summarizer.py` enforces this ("Do NOT mention art style or medium here").

`auto_pick_style()` calls Groq to choose a style key when the user hasn't overridden and the summarizer didn't set one. Default is `3d_pixar`.

### Multi-provider fallback chain (summarizer)

`summarize_article()` tries providers in order and returns on first success:

1. **Kimi K2** via OpenRouter (primary, best quality/$)
2. **Claude Sonnet 4.6** via OpenRouter (quality fallback)
3. **Groq Llama 3.3 70B** (speed fallback)
4. **Gemini 2.5 Flash** (budget floor)

All four share the same `get_prompt()` and `parse_response()`, so adding a provider means writing a `summarize_with_X()` that returns `parse_response(text)` and inserting it in the chain inside `summarize_article()`. Note: OpenRouter hosts both Kimi and Claude — one key covers two stages.

A **separate** Groq client inside `video_generator.py` and `visual_styles.py` handles style selection, subject extraction, and image-prompt generation. That one falls silently back to hardcoded defaults if `GROQ_API_KEY` is missing.

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

`_parallel_image_gen()` uses a `ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS=6)` and preserves prompt-to-image ordering via an index map. FAL's FLUX-schnell runs at 4 inference steps for speed. Each failed image falls back to a gradient so one timeout doesn't kill the whole video. `create_hook_clips()` parallelizes its 4 hook angles the same way.

## Project-specific conventions

- **Do not burn captions into video frames.** TikTok's native caption generator handles captions; all image prompts and style presets explicitly include "no text no words" directives. `chunk_text()` exists only for visual pacing in the legacy path, not for on-screen text.
- **User-facing errors are generic; full context goes to `logger.error(..., exc_info=True)`.** Don't leak provider error strings to the client.
- **`print("[Tag] ...")` is the logging convention inside `video_generator.py`** (pipeline progress visible in gunicorn logs). `logger.info/error` is used in `app.py` and `summarizer.py`. Don't mix them within a module.
- **Frontend has no build step.** `static/app.js` is plain ES-modern JS served directly. Don't introduce bundlers without a reason.
- **Tests use `tempfile` SQLite per-test via `conftest.py` fixtures** (`client`, `sample_article`, `summarized_article`, `mock_env_vars`). Tests marked `slow` or `integration` may hit real services if env keys are set — prefer `pytest-mock` for external calls.
- **OpenRouter calls send `HTTP-Referer: http://localhost:5050` and `X-Title: Clipper`.** If deploying publicly, update these in `summarizer._call_openrouter()`.
