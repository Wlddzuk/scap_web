# AI-Powered Video Generation with FAL.ai & Kokoro TTS

## Overview

Your video generator creates **TikTok-style short-form videos** with:
- **FAL.ai FLUX.1-dev** for cinematic AI-generated B-roll images
- **Kokoro-82M** for natural, high-quality TTS voiceover
- **Fast-paced text** (3-4 word chunks, TikTok style)
- **Visual changes every 2-3 seconds** for engagement
- **9:16 vertical format** for TikTok/Instagram/YouTube Shorts

## How It Works

### 1. Text Chunking
The script is split into 3-4 word chunks for TikTok-style pacing, with each chunk displayed for 2-3 seconds.

### 2. B-roll Image Generation
For each chunk, FAL.ai generates context-aware images:
- Automatic keyword extraction maps text to visual concepts
- Cinematic dark moody atmosphere
- Vertical 9:16 composition
- Images reused every 3rd chunk to reduce API calls

### 3. Kokoro TTS Voiceover
High-quality voice synthesis using Kokoro-82M:
- Natural female voice (`af_heart`)
- 1.05x speed for engaging pace
- Falls back to gTTS if Kokoro unavailable

### 4. Final Output
All clips assembled with:
- B-roll visuals darkened for text readability
- Synchronized voiceover audio
- MP4 output optimized for social media

## Setup Instructions

### Step 1: Get FAL.ai API Key (Free Tier Available)

1. **Create account**: Go to [FAL.ai](https://fal.ai)
2. **Get API key**: Visit [Dashboard → Keys](https://fal.ai/dashboard/keys)
3. **Create new key**: Copy and save it

### Step 2: Install Kokoro TTS

```bash
pip install kokoro soundfile numpy
```

The first run will download the Kokoro-82M model (~160MB).

### Step 3: Configure Environment

Create or update your `.env` file:

```env
# Required for video B-roll image generation
FAL_KEY=your_fal_key_here

# At least ONE of these is required for summarization
OPENROUTER_API_KEY=your_key_here
GROQ_API_KEY=your_key_here
MISTRAL_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
```

### Step 4: Test It

```bash
python video_generator.py
```

This generates a test video at `static/videos/article_999_*.mp4`.

## Cost & Limits

### FAL.ai
- **Free tier**: ~$0.50 credits to start
- **Cost**: ~$0.01-0.03 per image
- **Speed**: 3-5 seconds per image
- **Quality**: High (FLUX.1-dev is state-of-the-art)

### Kokoro TTS
- **Cost**: FREE (local model)
- **Speed**: Fast (runs on CPU)
- **Quality**: Natural, broadcast-quality voice

### Expected Usage
- 3-5 unique images per video (reused across chunks)
- If you generate 10 videos/day = 30-50 images
- Approximately $0.30-1.50/day on FAL.ai

## Fallback Behavior

### Image Generation
If FAL.ai API is unavailable or not configured:
- System falls back to stylish gradient backgrounds
- Video generation continues without errors
- Console shows: `[Image] No FAL_KEY found, using gradient background`

### TTS Voice
If Kokoro fails to load:
- System falls back to gTTS (Google Text-to-Speech)
- Console shows: `[TTS] Kokoro failed: ..., using gTTS...`

## Customization Options

### Change Image Model

In `video_generator.py`, line ~69:

```python
# Current: FLUX.1-dev (high quality)
result = fal_client.run("fal-ai/flux/dev", ...)

# Alternative: FLUX.1-schnell (faster, lower quality)
result = fal_client.run("fal-ai/flux/schnell", ...)
```

### Adjust Text Pacing

In `video_generator.py`, line ~389:

```python
# Current: 4 words per chunk
chunks = chunk_text_for_tiktok(script, words_per_chunk=4)

# Slower pacing: 6 words per chunk
chunks = chunk_text_for_tiktok(script, words_per_chunk=6)
```

### Modify Image Darkening

In `video_generator.py`, line ~320:

```python
# Current: 70% brightness (darker for text)
darkened = darken_image(image, factor=0.7)

# Lighter: 80% brightness
darkened = darken_image(image, factor=0.8)

# Darker: 50% brightness
darkened = darken_image(image, factor=0.5)
```

### Change TTS Voice

In `video_generator.py`, line ~162:

```python
# Current: American female heart voice
generator = pipeline(text, voice='af_heart', speed=1.05)

# Alternative voices:
# 'af_bella' - American female Bella
# 'am_adam' - American male Adam
# 'bf_emma' - British female Emma
```

## Troubleshooting

### "FAL_KEY not found"
- Check `.env` file has `FAL_KEY=...` (not `FAL_API_KEY`)
- Restart your application after adding the key

### Images Look Generic
- The system extracts keywords automatically
- Check `extract_visual_keywords()` function to add domain-specific mappings
- Prompts are enhanced with cinematic styling automatically

### Kokoro Fails to Load
- Ensure `pip install kokoro soundfile numpy` completed
- First run downloads the model (~160MB)
- System falls back to gTTS automatically

### Video Generation Fails
- Check console output for specific errors
- Ensure MoviePy is installed: `pip install moviepy`
- Verify FAL.ai API key is valid

## Console Output

When generating videos, you'll see:

```
==================================================
[Video] Generating TikTok-style video for article 4
==================================================
[Video] Step 1: Generating voiceover...
[TTS] Using Kokoro-82M for voice...
[TTS] Audio saved: static/videos/temp_audio_4.wav
[Video] Audio duration: 15.2s
[Video] Step 2: Chunking text for TikTok pacing...
[Video] Created 12 text chunks
[Video] Step 3: Generating 12 B-roll images...
[Video] Chunk 1/12: 'Did you know AI...'
[Image] Generating: artificial intelligence neural...
[Image] Generated successfully!
...
[Video] Step 4: Assembling video...
[Video] Step 5: Rendering final video...
==================================================
[Video] SUCCESS! Video saved to: static/videos/article_4_20251229_211411.mp4
==================================================
```

## Architecture

```
Script Input
    ↓
┌───────────────────────┐
│  Text Chunking        │  → 3-4 words per chunk
└───────────────────────┘
    ↓
┌───────────────────────┐
│  Kokoro TTS           │  → High-quality voiceover
└───────────────────────┘
    ↓
┌───────────────────────┐
│  FAL.ai FLUX.1-dev    │  → B-roll images (every 3rd chunk)
└───────────────────────┘
    ↓
┌───────────────────────┐
│  MoviePy Composition  │  → Assemble clips + audio
└───────────────────────┘
    ↓
MP4 Output (1080x1920, 30fps)
```
