# AI-Powered Video Generation with HuggingFace

## Overview

Your video generator now creates **professional short-form videos** with:
- ✅ **AI-generated images** using HuggingFace FLUX.1-schnell model
- ✅ **Multiple images per video** (one per sentence/scene)
- ✅ **Text overlays** synchronized with content
- ✅ **Google TTS voiceover** (gTTS)
- ✅ **9:16 vertical format** for TikTok/Instagram/YouTube Shorts

## How It Works

### 1. Script Analysis
The system splits your video script into sentences and extracts key phrases to use as image generation prompts.

### 2. AI Image Generation
For each sentence, it generates a unique AI image using HuggingFace's FLUX.1-schnell model:
- Fast generation (~5-10 seconds per image)
- High quality, cinematic style
- Automatically formatted to 9:16 (1080x1920)
- Free tier: very generous limits

### 3. Video Composition
Each image is:
- Slightly darkened for better text visibility
- Overlaid with the sentence text at the bottom
- Synchronized with the TTS audio timing

### 4. Final Output
All scenes are combined with:
- Title card intro (3 seconds)
- Main content with images + text + voiceover
- MP4 output optimized for social media

## Setup Instructions

### Step 1: Get HuggingFace API Key (Free)

1. **Create account**: Go to [HuggingFace](https://huggingface.co/join)
2. **Get API token**: Visit [Settings → Access Tokens](https://huggingface.co/settings/tokens)
3. **Create new token**: Click "New token"
   - Name: `scap_web_videos`
   - Type: `Read` (default)
4. **Copy the token**: Save it for the next step

### Step 2: Configure Environment

Create or update your `.env` file:

```env
# Required for video image generation
HUGGINGFACE_API_KEY=hf_your_token_here

# At least ONE of these is required for summarization
OPENROUTER_API_KEY=your_key_here
GROQ_API_KEY=your_key_here
MISTRAL_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
```

### Step 3: Test It

Run the test script:

```bash
python video_generator.py
```

This will generate a test video at `videos/article_999_*.mp4` with AI-generated images.

## What Changed

### Before (Solid Backgrounds)
- Text on solid color backgrounds
- Static, boring visuals
- Limited engagement

### After (AI Images)
- Dynamic AI-generated imagery
- Professional cinematic look
- Each scene has unique visuals
- Much more engaging content

## Cost & Limits

### HuggingFace Free Tier
- **Cost**: FREE
- **Limits**: ~1000 requests/day (plenty for your use case)
- **Speed**: 5-10 seconds per image
- **Quality**: High (FLUX.1-schnell is a state-of-the-art model)

### Expected Usage
- 5-10 images per video
- If you generate 10 videos/day = 50-100 images
- Well within free tier limits

## Fallback Behavior

If HuggingFace API is unavailable or not configured:
- System automatically falls back to solid color backgrounds
- Video generation continues without errors
- You'll see console messages: `[Image] No HUGGINGFACE_API_KEY found, using solid background`

## Customization Options

### Change Image Model

In `video_generator.py`, line 50:

```python
# Current: Fast and good quality
api_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"

# Alternative options:
# - Stable Diffusion XL: "stabilityai/stable-diffusion-xl-base-1.0"
# - Realistic Vision: "SG161222/Realistic_Vision_V6.0_B1_noVAE"
```

### Adjust Number of Images

In `video_generator.py`, line 327:

```python
# Current: One image per sentence
image_prompts = extract_image_prompts(script, num_images=len(sentences))

# Fixed number (e.g., always 5 images):
image_prompts = extract_image_prompts(script, num_images=5)
```

### Modify Image Darkening

In `video_generator.py`, line 189:

```python
# Current: Semi-transparent dark overlay (0-255, higher = darker)
overlay = Image.new('RGBA', image.size, (0, 0, 0, 80))

# Darker for better text contrast:
overlay = Image.new('RGBA', image.size, (0, 0, 0, 120))

# Lighter:
overlay = Image.new('RGBA', image.size, (0, 0, 0, 50))
```

## Troubleshooting

### "Model is loading" Messages
- HuggingFace models sometimes need to warm up
- System automatically waits and retries (up to 3 times)
- Usually resolves in 10-20 seconds

### Images Look Wrong
- Check the prompt extraction in console logs
- Prompts are taken from first 8 words of each sentence
- You can manually adjust in `extract_image_prompts()` function

### Rate Limit Errors
- Free tier is very generous
- If you hit limits, wait a few minutes
- Or switch to a different HuggingFace model

### Video Generation Fails
- Check console output for specific errors
- Ensure FFmpeg and ImageMagick are installed
- Verify API key is correct in `.env`

## Example Output

With the test script about AI, you'll get:
- **Title card**: "AI is Changing Everything" (3 seconds)
- **Scene 1**: AI-generated image of "AI transforming work" + text overlay
- **Scene 2**: AI-generated image of "tools writing code creating art" + text overlay
- **Scene 3**: AI-generated image of "learn to work with AI" + text overlay
- **Scene 4**: AI-generated image of "start experimenting today" + text overlay

All synchronized with natural-sounding TTS voiceover!

## Next Steps

1. ✅ Get your HuggingFace API key
2. ✅ Add it to `.env`
3. ✅ Test with `python video_generator.py`
4. ✅ Use the dashboard to generate videos for real articles
5. 🎉 Share your AI-powered videos!

## Questions?

Check the console output when generating videos - it shows detailed progress:
- `[Video] Generating TTS...`
- `[Video] Generating 4 AI images...`
- `[Image] Generating image: ...`
- `[Video] Creating video clips...`
- `[Video] Rendering final video...`

This helps you understand what's happening and debug any issues.
