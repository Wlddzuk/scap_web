"""
TikTok-Style Video Generator with FAL.ai Image Generation and Kokoro TTS.

Your requested changes:
- ✅ NO darkening (removed darken_image usage)
- ✅ NO on-screen text (removed TextClip + overlays)
- ✅ Video is visuals-only + voiceover
- ✅ Keeps: hook clip, memory-safe ImageClip (no temp files), subtle motion, pacing based on script
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path
from io import BytesIO

import numpy as np
from moviepy.editor import (
    ImageClip,
    AudioFileClip,
    concatenate_videoclips,
    vfx,
)
from PIL import Image, ImageDraw

# Pillow 10.0+ compatibility fix: ANTIALIAS was removed, use LANCZOS instead
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

import requests
from dotenv import load_dotenv

load_dotenv()

# Video settings for 9:16 (vertical TikTok/Reels/Shorts)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
HOOK_DURATION = 3.0  # First seconds are the hook

# Video timing constraints
MIN_CHUNK_DURATION = 1.2
MAX_CHUNK_DURATION = 3.2
DEFAULT_CHUNK_DURATION = 2.5

# Image generation settings
DEFAULT_WORDS_PER_CHUNK = 4
RETRY_ATTEMPTS = 2


# ============================================
# FAL.ai Image Generation
# ============================================

def generate_image_fal(prompt: str, retry_count: int = RETRY_ATTEMPTS) -> Image.Image:
    """
    Generate an image using FAL.ai FLUX model.
    Returns PIL Image or gradient background as fallback.
    """
    import fal_client

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        print("[Image] No FAL_KEY found, using gradient background")
        return create_gradient_background()

    # Bright, eye-catching for TikTok (no text)
    enhanced_prompt = (
        f"{prompt}, vibrant bright colors, high contrast, eye-catching, "
        f"clean composition, vertical 9:16, professional quality, no text no words"
    )

    for attempt in range(retry_count):
        try:
            print(f"[Image] Generating: {prompt[:40]}...")

            result = fal_client.run(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": enhanced_prompt,
                    "image_size": "portrait_16_9",
                    "num_images": 1,
                    "num_inference_steps": 4
                }
            )

            if result and "images" in result and len(result["images"]) > 0:
                image_url = result["images"][0]["url"]
                response = requests.get(image_url, timeout=30)
                img = Image.open(BytesIO(response.content)).convert("RGB")

                img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)

                print("[Image] ✅ Generated successfully!")
                return img

        except Exception as e:
            print(f"[Image] ⚠️ Attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    print("[Image] ❌ All attempts failed, using gradient")
    return create_gradient_background()


def create_gradient_background() -> Image.Image:
    """Create a stylish gradient background as fallback."""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw = ImageDraw.Draw(img)

    # Dark purple to blue gradient (kept as fallback only)
    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        r = int(20 + ratio * 10)
        g = int(10 + ratio * 20)
        b = int(40 + ratio * 50)
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))

    return img


def resize_and_crop_image(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """Resize and center-crop image to target dimensions."""
    orig_width, orig_height = img.size
    orig_ratio = orig_width / orig_height
    target_ratio = target_width / target_height

    if orig_ratio > target_ratio:
        # wider -> crop width
        new_height = orig_height
        new_width = int(new_height * target_ratio)
        left = (orig_width - new_width) // 2
        img = img.crop((left, 0, left + new_width, new_height))
    else:
        # taller -> crop height
        new_width = orig_width
        new_height = int(new_width / target_ratio)
        top = (orig_height - new_height) // 2
        img = img.crop((0, top, new_width, top + new_height))

    return img.resize((target_width, target_height), Image.LANCZOS)


# ============================================
# Kokoro TTS (High-Quality Voice)
# ============================================

def generate_tts_kokoro(text: str, output_path: str) -> str:
    """
    Generate TTS using Kokoro-82M.
    Falls back to gTTS if Kokoro fails.
    """
    try:
        from kokoro import KPipeline
        import soundfile as sf
        import numpy as np

        print("[TTS] Using Kokoro-82M for voice...")

        pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        generator = pipeline(text, voice="af_heart", speed=1.05)

        all_audio = []
        for _, (_, _, audio) in enumerate(generator):
            all_audio.extend(audio)

        audio_array = np.array(all_audio, dtype=np.float32)
        wav_path = output_path.replace(".mp3", ".wav")
        sf.write(wav_path, audio_array, 24000)

        print(f"[TTS] ✅ Audio saved: {wav_path}")
        return wav_path

    except Exception as e:
        print(f"[TTS] ⚠️ Kokoro failed: {e}, using gTTS...")
        from gtts import gTTS
        tts = gTTS(text=text, lang="en", slow=False)
        tts.save(output_path)
        return output_path


# ============================================
# Script Cleaning
# ============================================

def clean_script_for_tts(script: str) -> str:
    """Remove bracket tags like [HOOK] from TTS so they aren't spoken."""
    clean_text = re.sub(r"\[.*?\]", "", script)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    return clean_text


# ============================================
# TikTok-Style Chunking (for pacing only)
# ============================================

def chunk_text_for_tiktok(text: str, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list:
    """Break text into chunks to pace visual changes (NO captions will be shown)."""
    clean_text = re.sub(r"\[.*?\]", "", text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    words = clean_text.split()
    chunks = []

    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        if chunk:
            chunks.append(chunk)

    return chunks


# ============================================
# AI-Powered Style Selection + Subjects + Prompts
# (Your original logic kept; unchanged except where necessary)
# ============================================

TIKTOK_STYLES = """
STYLE A - 3D PIXAR/CGI:
Best for: Educational, explainer, science, technology, business content
Look: Clean, colorful, professional 3D renders like Pixar/DreamWorks movies
Keywords: 3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant

STYLE B - VIBRANT PHOTOGRAPHY:
Best for: News, lifestyle, health, fitness, nature, real-world topics
Look: Bright professional photography with saturated colors, high contrast
Keywords: Professional photography, bright natural lighting, saturated colors, high contrast, vivid

STYLE C - BOLD FLAT ILLUSTRATION:
Best for: Tech, business, trending topics, infographics, modern content
Look: Modern flat design with bold colors, clean lines, minimalist but eye-catching
Keywords: Flat illustration, bold colors, modern design, clean lines, vibrant, graphic
"""


def select_style_with_groq(title: str, script: str) -> str:
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[Style] ⚠️ No GROQ_API_KEY, using default bright 3D style")
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"

    try:
        print("[Style] 🎨 Analyzing content for TikTok-friendly style...")
        client = Groq(api_key=api_key)

        prompt = f"""You are a TikTok/Instagram visual director. Pick the BEST style for maximum engagement.

ARTICLE TITLE: {title}

ARTICLE CONTENT (excerpt):
{script[:2000]}

AVAILABLE STYLES (pick ONE):
{TIKTOK_STYLES}

Respond with ONLY the style keywords.
"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You select visual styles for TikTok videos. Always choose BRIGHT, VIBRANT, eye-catching styles. Respond with only the style keywords."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=100
        )

        chosen_style = response.choices[0].message.content.strip().strip('"\'')
        if "bright" not in chosen_style.lower() and "vibrant" not in chosen_style.lower():
            chosen_style += ", bright, vibrant, eye-catching"

        print(f"[Style] ✅ Selected style: {chosen_style}")
        return chosen_style

    except Exception as e:
        print(f"[Style] ⚠️ Style selection failed: {e}, using default bright 3D style")
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"


def extract_story_subjects(title: str, script: str) -> dict:
    from groq import Groq
    import json

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[Subjects] ⚠️ No GROQ_API_KEY, using title as subject")
        return {
            "main_subject": title,
            "visual_keywords": [title.split()[0] if title else "scene"],
            "setting": "general environment"
        }

    try:
        print("[Subjects] 🔍 Extracting core subjects from story...")
        client = Groq(api_key=api_key)

        prompt = f"""Analyze this article and identify what it is LITERALLY about.

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{script[:3000]}

Respond with ONLY valid JSON:
{{
    "main_subject": "what this article is literally about in 3-5 words",
    "visual_keywords": ["5 concrete things that should appear in images"],
    "setting": "where this story takes place (location/environment)"
}}"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You extract visual subjects from articles. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=300
        )

        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        subjects = json.loads(text)

        print(f"[Subjects] ✅ Main subject: {subjects.get('main_subject', 'unknown')}")
        print(f"[Subjects] 📷 Visual keywords: {subjects.get('visual_keywords', [])}")
        print(f"[Subjects] 🌍 Setting: {subjects.get('setting', 'unknown')}")

        return subjects

    except Exception as e:
        print(f"[Subjects] ⚠️ Extraction failed: {e}, using title")
        return {
            "main_subject": title,
            "visual_keywords": [title],
            "setting": "general"
        }


def generate_image_prompts_with_groq(title: str, script: str, num_prompts: int = 10, style: str = None, subjects: dict = None) -> list:
    from groq import Groq
    import json
    import re as regex_module

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[Prompts] ⚠️ No GROQ_API_KEY, using fallback prompts")
        return None

    if not style:
        style = "cinematic, photorealistic, professional photography"

    if subjects:
        main_subject = subjects.get("main_subject", title)
        visual_keywords = subjects.get("visual_keywords", [])
        setting = subjects.get("setting", "general environment")
        keywords_str = ", ".join(visual_keywords) if visual_keywords else title
    else:
        main_subject = title
        visual_keywords = [title]
        keywords_str = title
        setting = "relevant environment"

    def parse_json_response(text: str) -> list:
        text = text.strip()
        if "```" in text:
            fence_match = regex_module.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if fence_match:
                text = fence_match.group(1).strip()
            else:
                text = regex_module.sub(r"```(?:json)?", "", text).strip()

        array_match = regex_module.search(r"\[[\s\S]*\]", text)
        if array_match:
            text = array_match.group(0)

        return json.loads(text)

    def validate_prompts(prompts: list, visual_keywords: list) -> tuple:
        if not isinstance(prompts, list):
            return False, []
        invalid = []
        keywords_lower = [kw.lower() for kw in visual_keywords] if visual_keywords else []
        for i, p in enumerate(prompts):
            if not isinstance(p, str):
                invalid.append(i)
                continue
            pl = p.lower()
            if keywords_lower and not any(kw in pl for kw in keywords_lower):
                invalid.append(i)
        return len(invalid) == 0, invalid

    def build_prompt(is_retry: bool = False) -> str:
        script_excerpt = script[:4000] if len(script) > 4000 else script
        strict = ""
        if is_retry:
            strict = """⚠️ CRITICAL: Your previous response was invalid.
This time EVERY prompt MUST include at least ONE required keyword. No generic prompts.

"""
        return f"""{strict}Generate {num_prompts} image prompts for a TikTok-style video.

TITLE: {title}
MAIN SUBJECT: {main_subject}
SETTING/LOCATION: {setting}

FULL SCRIPT:
{script_excerpt}

REQUIRED VISUAL KEYWORDS (at least one in EVERY prompt):
{keywords_str}

STYLE (apply to all):
{style}

Rules:
- 15–30 words each
- vertical 9:16 composition
- NO text/words in images
- concrete, specific, tied to story beats

Respond with ONLY a valid JSON array of exactly {num_prompts} strings:
["prompt 1", "prompt 2", ...]
"""

    try:
        print("[Prompts] 🧠 Generating story-aware image prompts...")
        client = Groq(api_key=api_key)

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"You are a visual director generating prompts for '{main_subject}'. Every prompt MUST include at least one of: {keywords_str}. Respond ONLY with a JSON array."},
                {"role": "user", "content": build_prompt(is_retry=False)}
            ],
            temperature=0.7,
            max_tokens=2000
        )

        prompts = parse_json_response(response.choices[0].message.content.strip())
        if not isinstance(prompts, list) or len(prompts) < num_prompts:
            return None

        prompts = prompts[:num_prompts]
        ok, bad = validate_prompts(prompts, visual_keywords)
        if ok:
            print(f"[Prompts] ✅ Generated {len(prompts)} validated prompts")
            return prompts

        print(f"[Prompts] ⚠️ {len(bad)} prompts failed validation, retrying...")
        retry = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"CRITICAL: EVERY prompt must contain at least one of: {keywords_str}. Respond ONLY with JSON array."},
                {"role": "user", "content": build_prompt(is_retry=True)}
            ],
            temperature=0.5,
            max_tokens=2000
        )

        retry_prompts = parse_json_response(retry.choices[0].message.content.strip())
        if not isinstance(retry_prompts, list) or len(retry_prompts) < num_prompts:
            return None

        retry_prompts = retry_prompts[:num_prompts]
        ok2, _ = validate_prompts(retry_prompts, visual_keywords)
        if ok2:
            print(f"[Prompts] ✅ Retry succeeded with {len(retry_prompts)} prompts")
            return retry_prompts

        return None

    except Exception as e:
        print(f"[Prompts] ⚠️ Groq failed: {e}, using fallback")
        return None


def generate_themed_images(title: str, script: str, num_images: int = 10) -> list:
    print(f"[Video] Generating {num_images} contextual images...")

    subjects = extract_story_subjects(title, script)
    style = select_style_with_groq(title, script)

    prompts = generate_image_prompts_with_groq(title, script, num_images, style=style, subjects=subjects)

    if not prompts:
        print("[Video] Using fallback prompts with subject anchoring")
        keywords = subjects.get("visual_keywords", [title])
        setting = subjects.get("setting", "")
        prompts = [f"{kw}, {setting}, {style}" for kw in (keywords * 3)[:num_images]]

    images = []
    for i, prompt in enumerate(prompts):
        print(f"[Video] Image {i+1}/{num_images}: {prompt[:50]}...")
        img = generate_image_fal(prompt)
        images.append(img)

    print(f"[Video] ✅ Generated {len(images)} images about '{subjects.get('main_subject', title)}'")
    return images


# ============================================
# Video Clip Creation (NO TEXT, NO DARKEN)
# ============================================

def create_clip_with_broll(image: Image.Image, duration: float) -> ImageClip:
    """
    Create ImageClip directly from memory (no temp files).
    Adds subtle motion to reduce swipe-offs.
    """
    frame = np.array(image)  # keep original brightness
    clip = ImageClip(frame).set_duration(duration)

    # Subtle Ken Burns zoom-in
    if duration > 0:
        clip = clip.fx(vfx.resize, lambda t: 1.0 + 0.03 * (t / duration))

    return clip


def create_hook_clip(title: str, duration: float = HOOK_DURATION) -> ImageClip:
    """Hook visual only (no text)."""
    hook_prompt = f"dramatic attention-grabbing scene about: {title}, cinematic lighting, intense atmosphere, close-up, dynamic composition"
    hook_image = generate_image_fal(hook_prompt)
    return create_clip_with_broll(hook_image, duration)


def ensure_videos_dir() -> Path:
    videos_dir = Path("static/videos")
    videos_dir.mkdir(parents=True, exist_ok=True)
    return videos_dir


def compute_weighted_durations(chunks: list, total_time: float) -> list:
    """
    Allocate time per chunk based on word count, clamp, then scale to fit total_time.
    (Used only to pace visual changes; no captions shown.)
    """
    if not chunks:
        return []

    raw = []
    for c in chunks:
        wc = max(1, len(c.split()))
        d = 0.45 * wc
        d = max(MIN_CHUNK_DURATION, min(MAX_CHUNK_DURATION, d))
        raw.append(d)

    raw_sum = sum(raw)
    if raw_sum <= 0:
        return [DEFAULT_CHUNK_DURATION for _ in chunks]

    scale = total_time / raw_sum
    durations = [d * scale for d in raw]

    # force exact sum
    drift = total_time - sum(durations)
    durations[-1] += drift

    # safety
    return [max(0.05, d) for d in durations]


# ============================================
# Main Video Generation
# ============================================

def generate_video(article_id: int, title: str, script: str, hero_image: str = None) -> str:
    """
    Generate a TikTok-style video:
    - visuals only (no on-screen text)
    - voiceover audio
    - hook visuals first
    - pacing based on script chunking
    """
    videos_dir = ensure_videos_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = videos_dir / f"article_{article_id}_{timestamp}.mp4"
    temp_audio_path = videos_dir / f"temp_audio_{article_id}.mp3"

    audio = None
    main_video = None
    clips = []
    actual_audio_path = None

    try:
        print(f"\n{'='*50}")
        print(f"[Video] 🎬 Generating TikTok-style video for article {article_id}")
        print(f"{'='*50}")

        # Step 1: TTS
        clean_script = clean_script_for_tts(script)
        print("[Video] Step 1: Generating voiceover...")
        actual_audio_path = generate_tts_kokoro(clean_script, str(temp_audio_path))

        audio = AudioFileClip(actual_audio_path)
        audio_duration = float(audio.duration)
        print(f"[Video] Audio duration: {audio_duration:.1f}s")

        # Step 2: Images
        print("[Video] Step 2: Pre-generating themed images...")
        themed_images = generate_themed_images(title, script, num_images=10)

        # Step 3: Chunk for pacing (NOT captions)
        print("[Video] Step 3: Chunking script for pacing...")
        chunks = chunk_text_for_tiktok(script, words_per_chunk=DEFAULT_WORDS_PER_CHUNK)
        print(f"[Video] Created {len(chunks)} pacing chunks")

        # Step 4: Hook clip (visual only)
        hook_len = min(HOOK_DURATION, max(1.0, audio_duration * 0.2))
        clips.append(create_hook_clip(title, duration=hook_len))

        # Remaining time
        remaining = max(0.1, audio_duration - hook_len)
        durations = compute_weighted_durations(chunks, remaining)

        # Step 5: Build visual-only clips
        print("[Video] Step 4: Creating visual clips...")
        for i in range(len(chunks)):
            img = themed_images[i % len(themed_images)]
            dur = durations[i] if i < len(durations) else DEFAULT_CHUNK_DURATION
            clips.append(create_clip_with_broll(img, dur))

        # Step 6: Assemble
        print("[Video] Step 5: Assembling video...")
        main_video = concatenate_videoclips(clips, method="compose")

        # Match duration to audio exactly
        if main_video.duration > audio_duration:
            main_video = main_video.subclip(0, audio_duration)
        elif main_video.duration < audio_duration:
            pad = audio_duration - main_video.duration
            # Freeze last frame to fill any tiny gap
            last_hold = clips[-1].to_ImageClip(t=max(0.0, clips[-1].duration - 0.01)).set_duration(pad)
            main_video = concatenate_videoclips([main_video, last_hold], method="compose")

        main_video = main_video.set_audio(audio)

        # Step 7: Render
        print("[Video] Step 6: Rendering final video...")
        main_video.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            preset="medium",
            verbose=False,
            logger=None
        )

        print(f"\n{'='*50}")
        print(f"[Video] ✅ SUCCESS! Video saved to: {output_path}")
        print(f"{'='*50}\n")

        return str(output_path)

    except Exception as e:
        print(f"[Video] ❌ Error generating video: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        # Cleanup resources safely
        try:
            if audio:
                audio.close()
        except Exception as e:
            print(f"[Video] Warning: Failed to close audio: {e}")

        try:
            if main_video:
                main_video.close()
        except Exception as e:
            print(f"[Video] Warning: Failed to close main video: {e}")

        for c in clips:
            try:
                c.close()
            except Exception as e:
                print(f"[Video] Warning: Failed to close clip: {e}")

        # Cleanup temp audio
        try:
            if actual_audio_path:
                Path(actual_audio_path).unlink()
        except Exception as e:
            print(f"[Video] Warning: Failed to delete temp audio: {e}")


# ============================================
# Test
# ============================================

if __name__ == "__main__":
    test_script = """
    Did you know AI is completely transforming how we work?
    In just the last year, we've seen tools that can write code, create art, and even compose music.
    The key takeaway? Those who learn to work WITH AI will have a massive advantage.
    Start experimenting today. Your future self will thank you.
    """

    output = generate_video(
        article_id=999,
        title="AI is Changing Everything",
        script=test_script
    )
    print(f"Test video: {output}")
