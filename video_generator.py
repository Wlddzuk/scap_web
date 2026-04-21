"""
TikTok-Style Video Generator - Parallel image generation + fast rendering.
"""

import os
import re
import time
import json
import random
from datetime import datetime
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, vfx
from PIL import Image, ImageDraw
import requests
from dotenv import load_dotenv

load_dotenv()

# Video settings
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

# Hook settings
HOOK_DURATION = 5.0
NUM_HOOK_IMAGES = 4

# Body image count (reduced from 20 for speed)
NUM_BODY_IMAGES = 14

# Parallel image generation workers
MAX_IMAGE_WORKERS = 6

# Timing constraints
MIN_CHUNK_DURATION = 1.2
MAX_CHUNK_DURATION = 3.2
DEFAULT_CHUNK_DURATION = 2.5
DEFAULT_WORDS_PER_CHUNK = 4
RETRY_ATTEMPTS = 2

# Pillow 10+ compatibility
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS


def generate_image_fal(prompt: str, retry_count: int = RETRY_ATTEMPTS) -> Image.Image:
    """Generate image using FAL.ai FLUX model.

    The prompt should already include style directives (via visual_styles.apply_style).
    We append only minimal universal quality tokens.
    """
    import fal_client

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        print("[Image] No FAL_KEY, using gradient")
        return create_gradient_background()

    enhanced_prompt = f"{prompt}, professional quality, sharp detail"

    for attempt in range(retry_count):
        try:
            print(f"[Image] Generating: {prompt[:60]}...")
            result = fal_client.run(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": enhanced_prompt,
                    "image_size": "portrait_16_9",
                    "num_images": 1,
                    "num_inference_steps": 4
                }
            )

            if result and "images" in result and result["images"]:
                image_url = result["images"][0]["url"]
                response = requests.get(image_url, timeout=30)
                img = Image.open(BytesIO(response.content)).convert("RGB")
                img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)
                print("[Image] Generated")
                return img

        except Exception as e:
            print(f"[Image] Attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    print("[Image] Failed, using gradient")
    return create_gradient_background()


def create_gradient_background() -> Image.Image:
    """Fallback gradient background."""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        r, g, b = int(20 + ratio * 10), int(10 + ratio * 20), int(40 + ratio * 50)
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))
    return img


def resize_and_crop_image(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """Resize and center-crop to target dimensions."""
    orig_w, orig_h = img.size
    orig_ratio = orig_w / orig_h
    target_ratio = target_width / target_height

    if orig_ratio > target_ratio:
        new_h, new_w = orig_h, int(orig_h * target_ratio)
        left = (orig_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, new_h))
    else:
        new_w, new_h = orig_w, int(orig_w / target_ratio)
        top = (orig_h - new_h) // 2
        img = img.crop((0, top, new_w, top + new_h))

    return img.resize((target_width, target_height), Image.LANCZOS)


# Kokoro voices, hand-picked for short-form delivery.
# af_nicole is deliberately excluded — it's whispery/ASMR-leaning and kills
# energy on hook-driven TikTok content. Keep it available for voice=... overrides
# if a future use case (bedtime stories, meditation) wants it back.
#
# Map dominant emotion -> voice that fits the vibe. Values are ordered;
# the first entry is the default pick, extras exist for future rotation/A-B.
EMOTION_VOICE_MAP = {
    "shocking":   ["af_bella",  "am_fenrir"],   # expressive, cuts through
    "urgent":     ["am_fenrir", "af_bella"],    # energetic, drives action
    "curious":    ["af_heart",  "bf_emma"],     # warm, inviting
    "triumphant": ["af_bella",  "am_fenrir"],   # bright, confident
    "dark":       ["am_michael","am_fenrir"],   # deeper, serious
    "funny":      ["af_bella",  "af_heart"],    # expressive, playful
}
DEFAULT_VOICE = "af_heart"  # Grade A warm female — safe fallback

# Speed nudges per emotion. TikTok pacing is faster than natural speech;
# 1.0 feels sluggish, 1.15 feels energetic. Don't go above 1.2 — Kokoro
# loses articulation.
EMOTION_SPEED_MAP = {
    "shocking":   1.15,
    "urgent":     1.15,
    "curious":    1.10,
    "triumphant": 1.12,
    "dark":       1.05,  # slower for gravitas
    "funny":      1.12,
}
DEFAULT_SPEED = 1.10


def pick_voice_for_emotion(emotion: str | None) -> tuple[str, float]:
    """Return (voice, speed) for a given dominant_emotion string.

    Falls back to warm-neutral defaults when emotion is missing or unknown.
    Deterministic: same emotion always yields the same (voice, speed) so
    videos of the same vibe sound consistent across regenerations.
    """
    key = (emotion or "").strip().lower()
    voices = EMOTION_VOICE_MAP.get(key)
    voice = voices[0] if voices else DEFAULT_VOICE
    speed = EMOTION_SPEED_MAP.get(key, DEFAULT_SPEED)
    return voice, speed


def generate_tts_kokoro(
    text: str,
    output_path: str,
    voice: str = None,
    emotion: str = None,
    speed: float = None,
) -> str:
    """Generate TTS using Kokoro-82M.

    Voice selection precedence: explicit `voice` arg > emotion mapping > default.
    Speed selection precedence: explicit `speed` arg > emotion mapping > default.
    """
    from kokoro import KPipeline
    import soundfile as sf

    emo_voice, emo_speed = pick_voice_for_emotion(emotion)
    selected_voice = voice or emo_voice
    selected_speed = speed if speed is not None else emo_speed

    print(
        f"[TTS] Using Kokoro-82M | voice={selected_voice} speed={selected_speed} "
        f"emotion={emotion or 'default'}"
    )
    pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    generator = pipeline(text, voice=selected_voice, speed=selected_speed)

    all_audio = []
    for _gs, _ps, audio_chunk in generator:
        all_audio.extend(audio_chunk)

    audio_array = np.array(all_audio, dtype=np.float32)
    wav_path = output_path.replace(".mp3", ".wav")
    sf.write(wav_path, audio_array, 24000)
    print(f"[TTS] Saved: {wav_path} (voice: {selected_voice})")
    return wav_path


def clean_text(text: str) -> str:
    """Remove bracket tags and normalize whitespace."""
    text = re.sub(r"\[.*?\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def chunk_text(text: str, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list:
    """Break text into chunks for visual pacing."""
    words = clean_text(text).split()
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk) if words[i:i + words_per_chunk]]


TIKTOK_STYLES = """
STYLE A - 3D PIXAR/CGI:
Keywords: 3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant

STYLE B - VIBRANT PHOTOGRAPHY:
Keywords: Professional photography, bright natural lighting, saturated colors, high contrast, vivid

STYLE C - BOLD FLAT ILLUSTRATION:
Keywords: Flat illustration, bold colors, modern design, clean lines, vibrant, graphic
"""


def get_groq_client():
    """Get Groq client if API key exists."""
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    return Groq(api_key=api_key) if api_key else None


def select_style_with_groq(title: str, script: str) -> str:
    """Select visual style using AI."""
    client = get_groq_client()
    if not client:
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"

    try:
        print("[Style] Selecting style...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Select BRIGHT, VIBRANT styles. Respond with only keywords."},
                {"role": "user", "content": f"Pick style for:\nTITLE: {title}\nCONTENT: {script[:2000]}\n\n{TIKTOK_STYLES}\n\nRespond with ONLY the style keywords."}
            ],
            temperature=0.5,
            max_tokens=100
        )
        style = response.choices[0].message.content.strip().strip('"\'')
        if "bright" not in style.lower() and "vibrant" not in style.lower():
            style += ", bright, vibrant, eye-catching"
        print(f"[Style] {style[:50]}...")
        return style
    except Exception as e:
        print(f"[Style] Failed: {e}")
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"


def extract_story_subjects(title: str, script: str) -> dict:
    """Extract visual subjects from content."""
    client = get_groq_client()
    default = {"main_subject": title, "visual_keywords": [title.split()[0] if title else "scene"], "setting": "general"}

    if not client:
        return default

    try:
        print("[Subjects] Extracting subjects...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Extract visual subjects. Respond only with valid JSON."},
                {"role": "user", "content": f'Analyze:\nTITLE: {title}\nCONTENT: {script[:3000]}\n\nRespond with JSON: {{"main_subject": "3-5 words", "visual_keywords": ["5 items"], "setting": "location"}}'}
            ],
            temperature=0.3,
            max_tokens=300
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            text = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            text = text.group(1) if text else "{}"
        subjects = json.loads(text)
        print(f"[Subjects] {subjects.get('main_subject', 'unknown')}")
        return subjects
    except Exception as e:
        print(f"[Subjects] Failed: {e}")
        return default


def generate_image_prompts(title: str, script: str, num_prompts: int, style: str, subjects: dict) -> list:
    """Generate image prompts using AI."""
    client = get_groq_client()
    if not client:
        return None

    main_subject = subjects.get("main_subject", title)
    visual_keywords = subjects.get("visual_keywords", [])
    setting = subjects.get("setting", "")
    keywords_str = ", ".join(visual_keywords) if visual_keywords else title

    try:
        print("[Prompts] Generating prompts...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"Generate prompts for '{main_subject}'. Include keywords: {keywords_str}. JSON array only."},
                {"role": "user", "content": f"Generate {num_prompts} image prompts.\nTITLE: {title}\nSETTING: {setting}\nSTYLE: {style}\nSCRIPT: {script[:4000]}\n\nRules: 15-30 words each, vertical 9:16, NO text in images.\nRespond with JSON array: [\"prompt1\", \"prompt2\", ...]"}
            ],
            temperature=0.7,
            max_tokens=2500
        )
        text = response.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            prompts = json.loads(match.group(0))
            if isinstance(prompts, list) and len(prompts) >= num_prompts:
                print(f"[Prompts] Generated {len(prompts)} prompts")
                return prompts[:num_prompts]
        return None
    except Exception as e:
        print(f"[Prompts] Failed: {e}")
        return None


def generate_themed_images(title: str, script: str, num_images: int = NUM_BODY_IMAGES) -> list:
    """Fallback: generate themed images when no scene beats are available."""
    print(f"[Video] Generating {num_images} themed images (fallback path)...")

    subjects = extract_story_subjects(title, script)
    style = select_style_with_groq(title, script)
    prompts = generate_image_prompts(title, script, num_images, style, subjects)

    if not prompts:
        print("[Video] Using fallback prompts")
        keywords = subjects.get("visual_keywords", [title])
        setting = subjects.get("setting", "")
        prompts = [f"{kw}, {setting}, {style}" for kw in (keywords * 5)[:num_images]]

    return _parallel_image_gen(prompts)


def _parallel_image_gen(prompts: list) -> list:
    """Generate N images in parallel. Returns list[PIL.Image] in prompt order."""
    images = [None] * len(prompts)
    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_idx = {
            executor.submit(generate_image_fal, p): i
            for i, p in enumerate(prompts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                images[idx] = future.result()
            except Exception as e:
                print(f"[Video] Image {idx+1} failed: {e}")
                images[idx] = create_gradient_background()
    print(f"[Video] Generated {len(images)} images")
    return images


def generate_scene_images(scenes: list, style_key: str) -> list:
    """Generate one image per scene, style applied consistently."""
    from visual_styles import apply_style

    prompts = [apply_style(s.get("visual", ""), style_key, is_hook=False) for s in scenes]
    print(f"[Video] Generating {len(prompts)} scene images in style '{style_key}'...")
    return _parallel_image_gen(prompts)


def create_hook_clips(title: str, duration: float = HOOK_DURATION, style_key: str = None, opening_visual: str = None) -> list:
    """Create rapid-fire hook sequence. Style-aware when style_key given."""
    from visual_styles import apply_style

    # Anchor on the opening scene visual if we have one; else derive from title.
    anchor = (opening_visual or title).strip().rstrip(",.")
    angle_variations = [
        f"{anchor}, extreme macro close-up, ultra sharp detail",
        f"{anchor}, impossible low-angle looking up, dramatic perspective",
        f"{anchor}, frozen peak action moment, motion blur trails",
        f"{anchor}, stark silhouette against explosive backdrop",
    ]

    if style_key:
        hook_prompts = [apply_style(v, style_key, is_hook=True) for v in angle_variations]
    else:
        # Legacy path (no style): use old generic punch prompts
        hook_prompts = [
            f"extreme macro close-up shot, {title}, ultra sharp detail, dramatic rim lighting, shallow depth of field, cinematic 9:16, hyper-realistic",
            f"impossible camera angle, {title}, bird's eye view mixed with dutch angle, dramatic shadows, high contrast neon accents, surreal perspective",
            f"frozen action moment, {title}, motion blur trails, dynamic energy, explosive composition, vibrant saturated colors, dramatic backlighting",
            f"bold graphic composition, {title}, stark contrast, complementary color explosion, minimalist but striking, professional advertising quality"
        ]

    clip_duration = duration / NUM_HOOK_IMAGES
    print(f"[Hook] Creating {NUM_HOOK_IMAGES} hook images in parallel (style: {style_key or 'legacy'})...")

    # Generate hook images in parallel
    images = [None] * NUM_HOOK_IMAGES
    with ThreadPoolExecutor(max_workers=NUM_HOOK_IMAGES) as executor:
        future_to_idx = {
            executor.submit(generate_image_fal, prompt): i
            for i, prompt in enumerate(hook_prompts[:NUM_HOOK_IMAGES])
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                images[idx] = future.result()
            except Exception:
                images[idx] = create_gradient_background()

    clips = [create_clip(img, clip_duration, zoom_factor=0.05) for img in images]
    print(f"[Hook] Created {len(clips)} clips ({clip_duration:.2f}s each)")
    return clips


def create_clip(image: Image.Image, duration: float, zoom_factor: float = 0.03) -> ImageClip:
    """Create ImageClip with Ken Burns zoom effect."""
    frame = np.array(image)
    clip = ImageClip(frame).set_duration(duration)
    if duration > 0:
        clip = clip.fx(vfx.resize, lambda t: 1.0 + zoom_factor * (t / duration))
    return clip


def compute_durations(chunks: list, total_time: float) -> list:
    """Allocate time per chunk based on word count."""
    if not chunks:
        return []

    raw = [max(MIN_CHUNK_DURATION, min(MAX_CHUNK_DURATION, 0.45 * max(1, len(c.split())))) for c in chunks]
    raw_sum = sum(raw)

    if raw_sum <= 0:
        return [DEFAULT_CHUNK_DURATION] * len(chunks)

    scale = total_time / raw_sum
    durations = [d * scale for d in raw]
    durations[-1] += total_time - sum(durations)  # Fix drift
    return [max(0.05, d) for d in durations]


def compute_scene_durations(scenes: list, total_time: float) -> list:
    """Allocate time per scene proportional to its speech length."""
    if not scenes:
        return []
    weights = [max(1, len((s.get("speech") or "").split())) for s in scenes]
    total_w = sum(weights)
    if total_w <= 0:
        return [total_time / len(scenes)] * len(scenes)
    durations = [total_time * w / total_w for w in weights]
    durations[-1] += total_time - sum(durations)  # fix drift
    return [max(0.3, d) for d in durations]


def generate_video(
    article_id: int,
    title: str,
    script: str,
    scenes: list = None,
    style_key: str = None,
    emotion: str = None,
) -> str:
    """Generate TikTok-style video.

    Preferred path: scenes + style_key + emotion provided (from summarizer).
    Each scene produces one style-consistent image, and images play in
    narrative order for their scene's proportional speech duration.
    `emotion` drives TTS voice and speed selection (see pick_voice_for_emotion).

    Fallback path: no scenes -> legacy themed-image generation with
    chunked text pacing.
    """
    from visual_styles import auto_pick_style

    videos_dir = Path("static/videos")
    videos_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = videos_dir / f"article_{article_id}_{timestamp}.mp4"
    temp_audio_path = videos_dir / f"temp_audio_{article_id}.mp3"

    audio = None
    main_video = None
    clips = []
    actual_audio_path = None
    use_scenes = bool(scenes)

    try:
        print(f"\n{'='*50}")
        print(f"[Video] Generating video for article {article_id}")
        print(f"[Video] Mode: {'scene-based' if use_scenes else 'legacy'} | Style: {style_key or 'auto'}")
        print(f"{'='*50}")

        # Step 1: TTS
        print("[Video] Step 1: Generating voiceover...")
        actual_audio_path = generate_tts_kokoro(
            clean_text(script), str(temp_audio_path), emotion=emotion
        )
        audio = AudioFileClip(actual_audio_path)
        audio_duration = float(audio.duration)
        print(f"[Video] Audio: {audio_duration:.1f}s")

        # Step 2: Resolve style (only matters for scene-based path)
        if use_scenes and not style_key:
            style_key = auto_pick_style(title, script)
            print(f"[Video] Auto-picked style: {style_key}")

        # Step 3: Generate images (parallel)
        if use_scenes:
            print("[Video] Step 3: Generating scene-aligned images...")
            body_images = generate_scene_images(scenes, style_key)
        else:
            print("[Video] Step 3: Generating themed images (legacy)...")
            body_images = generate_themed_images(title, script, num_images=NUM_BODY_IMAGES)

        # Step 4: Hook sequence (style-aware when we have a style)
        print("[Video] Step 4: Creating hook sequence...")
        hook_len = min(HOOK_DURATION, max(2.0, audio_duration * 0.25))
        opening_visual = scenes[0].get("visual") if use_scenes and scenes else None
        hook_clips = create_hook_clips(
            title,
            duration=hook_len,
            style_key=style_key if use_scenes else None,
            opening_visual=opening_visual,
        )
        clips.extend(hook_clips)
        print(f"[Video] Hook: {hook_len:.1f}s")

        # Step 5: Body clips
        print("[Video] Step 5: Creating body clips...")
        remaining = max(0.1, audio_duration - hook_len)

        if use_scenes:
            durations = compute_scene_durations(scenes, remaining)
            for i, scene in enumerate(scenes):
                img = body_images[i] if i < len(body_images) else body_images[-1]
                dur = durations[i] if i < len(durations) else DEFAULT_CHUNK_DURATION
                clips.append(create_clip(img, dur))
        else:
            chunks = chunk_text(script)
            durations = compute_durations(chunks, remaining)
            for i in range(len(chunks)):
                img = body_images[i % len(body_images)]
                dur = durations[i] if i < len(durations) else DEFAULT_CHUNK_DURATION
                clips.append(create_clip(img, dur))

        # Step 6: Assemble
        print("[Video] Step 6: Assembling...")
        main_video = concatenate_videoclips(clips, method="compose")

        if main_video.duration > audio_duration:
            main_video = main_video.subclip(0, audio_duration)
        elif main_video.duration < audio_duration:
            pad = audio_duration - main_video.duration
            last_hold = clips[-1].to_ImageClip(t=max(0.0, clips[-1].duration - 0.01)).set_duration(pad)
            main_video = concatenate_videoclips([main_video, last_hold], method="compose")

        main_video = main_video.set_audio(audio)
        print(f"[Video] Final: {main_video.duration:.1f}s")

        # Step 7: Render with ultrafast preset
        print("[Video] Step 7: Rendering (ultrafast)...")
        main_video.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            preset="ultrafast",
            verbose=False,
            logger=None
        )

        print(f"\n{'='*50}")
        print(f"[Video] SUCCESS: {output_path}")
        print(f"{'='*50}\n")
        return str(output_path)

    except Exception as e:
        print(f"[Video] Error: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        for resource in [audio, main_video]:
            try:
                if resource:
                    resource.close()
            except Exception:
                pass
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        try:
            if actual_audio_path:
                Path(actual_audio_path).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    test_script = """
    Did you know AI is completely transforming how we work?
    In just the last year, we've seen tools that can write code, create art, and even compose music.
    The key takeaway? Those who learn to work WITH AI will have a massive advantage.
    Start experimenting today. Your future self will thank you.
    """
    output = generate_video(article_id=999, title="AI is Changing Everything", script=test_script)
    print(f"Test video: {output}")
