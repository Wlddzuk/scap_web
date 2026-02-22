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
    """Generate image using FAL.ai FLUX model."""
    import fal_client

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        print("[Image] No FAL_KEY, using gradient")
        return create_gradient_background()

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


# High-quality Kokoro voices (Grade B- or better)
KOKORO_VOICES = [
    "af_heart",   # Grade A - warm female
    "af_bella",   # Grade A- - expressive female
    "af_nicole",  # Grade B- - clear female
    "bf_emma",    # Grade B- - British female
    "am_fenrir",  # Grade C+ - best male
    "am_michael", # Grade C+ - professional male
]


def generate_tts_kokoro(text: str, output_path: str, voice: str = None) -> str:
    """Generate TTS using Kokoro-82M only (no fallback)."""
    from kokoro import KPipeline
    import soundfile as sf

    selected_voice = voice or random.choice(KOKORO_VOICES)
    print(f"[TTS] Using Kokoro-82M with voice: {selected_voice}")
    pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    generator = pipeline(text, voice=selected_voice, speed=1.05)

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
    """Generate themed images for video body using parallel workers."""
    print(f"[Video] Generating {num_images} images in parallel (max {MAX_IMAGE_WORKERS} workers)...")

    subjects = extract_story_subjects(title, script)
    style = select_style_with_groq(title, script)
    prompts = generate_image_prompts(title, script, num_images, style, subjects)

    if not prompts:
        print("[Video] Using fallback prompts")
        keywords = subjects.get("visual_keywords", [title])
        setting = subjects.get("setting", "")
        prompts = [f"{kw}, {setting}, {style}" for kw in (keywords * 5)[:num_images]]

    # Parallel image generation
    images = [None] * len(prompts)
    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_idx = {
            executor.submit(generate_image_fal, prompt): i
            for i, prompt in enumerate(prompts)
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


def create_hook_clips(title: str, duration: float = HOOK_DURATION) -> list:
    """Create rapid-fire hook sequence using parallel image generation."""
    hook_prompts = [
        f"extreme macro close-up shot, {title}, ultra sharp detail, dramatic rim lighting, shallow depth of field, cinematic 9:16, hyper-realistic",
        f"impossible camera angle, {title}, bird's eye view mixed with dutch angle, dramatic shadows, high contrast neon accents, surreal perspective",
        f"frozen action moment, {title}, motion blur trails, dynamic energy, explosive composition, vibrant saturated colors, dramatic backlighting",
        f"bold graphic composition, {title}, stark contrast, complementary color explosion, minimalist but striking, professional advertising quality"
    ]

    clip_duration = duration / NUM_HOOK_IMAGES
    print(f"[Hook] Creating {NUM_HOOK_IMAGES} hook images in parallel...")

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


def generate_video(article_id: int, title: str, script: str) -> str:
    """Generate TikTok-style video with parallel image generation."""
    videos_dir = Path("static/videos")
    videos_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = videos_dir / f"article_{article_id}_{timestamp}.mp4"
    temp_audio_path = videos_dir / f"temp_audio_{article_id}.mp3"

    audio = None
    main_video = None
    clips = []
    actual_audio_path = None

    try:
        print(f"\n{'='*50}")
        print(f"[Video] Generating video for article {article_id}")
        print(f"{'='*50}")

        # Step 1: TTS
        print("[Video] Step 1: Generating voiceover...")
        actual_audio_path = generate_tts_kokoro(clean_text(script), str(temp_audio_path))
        audio = AudioFileClip(actual_audio_path)
        audio_duration = float(audio.duration)
        print(f"[Video] Audio: {audio_duration:.1f}s")

        # Step 2: Generate images (parallel)
        print("[Video] Step 2: Generating images...")
        themed_images = generate_themed_images(title, script, num_images=NUM_BODY_IMAGES)

        # Step 3: Chunk for pacing
        print("[Video] Step 3: Chunking script...")
        chunks = chunk_text(script)
        print(f"[Video] {len(chunks)} chunks")

        # Step 4: Hook clips (parallel)
        print("[Video] Step 4: Creating hook sequence...")
        hook_len = min(HOOK_DURATION, max(2.0, audio_duration * 0.25))
        hook_clips = create_hook_clips(title, duration=hook_len)
        clips.extend(hook_clips)
        print(f"[Video] Hook: {hook_len:.1f}s")

        # Step 5: Body clips
        print("[Video] Step 5: Creating body clips...")
        remaining = max(0.1, audio_duration - hook_len)
        durations = compute_durations(chunks, remaining)

        for i in range(len(chunks)):
            img = themed_images[i % len(themed_images)]
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
