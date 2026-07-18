"""
Photo Carousel Generator - 6 AI images with text overlays + voiceover.

Produces TikTok Photo Mode assets: 6 PNG slides + 1 WAV audio file.
Reuses image/TTS/AI helpers from video_generator.py.
"""

import os
import re
import json
import logging
import shutil
import time
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
import tts_engine

logger = logging.getLogger(__name__)

from video_generator import (
    generate_image_fal,
    clean_text,
    resize_and_crop_image,
    get_groq_client,
    create_gradient_background,
    search_pexels_images,
    _extract_search_keywords,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    MAX_IMAGE_WORKERS,
)

load_dotenv()

# Carousel settings
NUM_SLIDES = 6
CAROUSEL_IMAGE_STEPS = 8  # Higher quality than video (4 steps)

# Text overlay settings
TEXT_MARGIN = 60
TEXT_PADDING = 30
MAX_FONT_SIZE = 54
MIN_FONT_SIZE = 32
LINE_SPACING = 10


# ============================================================
# AI: Extract key points for slide captions
# ============================================================

def extract_key_points(title: str, script: str, num_points: int = NUM_SLIDES) -> list:
    """Use Groq to extract exactly N key points (one per slide)."""
    client = get_groq_client()
    if not client:
        # Fallback: chunk the script into N parts
        words = clean_text(script).split()
        chunk_size = max(1, len(words) // num_points)
        return [
            " ".join(words[i:i + chunk_size])[:80]
            for i in range(0, len(words), chunk_size)
        ][:num_points]

    try:
        logger.info("[Carousel] Extracting key points...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Extract exactly {num_points} short, punchy key points from "
                        f"the content. Each point should be a bold caption for a TikTok "
                        f"photo slide (max 12 words each). Respond only with a JSON array."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"TITLE: {title}\n\nSCRIPT:\n{script[:4000]}\n\n"
                        f"Return a JSON array of exactly {num_points} strings."
                    ),
                },
            ],
            temperature=0.5,
            max_tokens=500,
        )
        text = response.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            points = json.loads(match.group(0))
            if isinstance(points, list) and len(points) >= num_points:
                logger.info(f"[Carousel] Got {len(points)} key points")
                return [str(p) for p in points[:num_points]]
    except Exception as e:
        logger.info(f"[Carousel] Key point extraction failed: {e}")

    # Fallback
    words = clean_text(script).split()
    chunk_size = max(1, len(words) // num_points)
    return [
        " ".join(words[i:i + chunk_size])[:80]
        for i in range(0, len(words), chunk_size)
    ][:num_points]


# ============================================================
# AI: Generate image prompts
# ============================================================

def generate_carousel_prompts(title: str, script: str, num_prompts: int = NUM_SLIDES) -> list:
    """Generate image prompts optimized for high-quality photo carousel."""
    client = get_groq_client()
    if not client:
        return [f"{title}, vibrant, high quality, professional photography" for _ in range(num_prompts)]

    try:
        logger.info("[Carousel] Generating image prompts...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate stunning, high-quality image prompts for a TikTok photo carousel. "
                        "Each image should tell part of the story visually. "
                        "Use vivid descriptions. NO text in images. Respond with JSON array only."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Generate {num_prompts} image prompts for a photo carousel.\n"
                        f"TITLE: {title}\n"
                        f"SCRIPT: {script[:4000]}\n\n"
                        f"Rules:\n"
                        f"- 20-35 words each for rich detail\n"
                        f"- Vertical 9:16 composition\n"
                        f"- NO text, words, or letters in images\n"
                        f"- Professional photography / cinematic style\n"
                        f"- High contrast, vibrant, eye-catching\n"
                        f"- Each prompt should depict a different aspect of the story\n\n"
                        f'Respond with JSON array: ["prompt1", "prompt2", ...]'
                    ),
                },
            ],
            temperature=0.7,
            max_tokens=2500,
        )
        text = response.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            prompts = json.loads(match.group(0))
            if isinstance(prompts, list) and len(prompts) >= num_prompts:
                logger.info(f"[Carousel] Generated {len(prompts)} prompts")
                return [str(p) for p in prompts[:num_prompts]]
    except Exception as e:
        logger.info(f"[Carousel] Prompt generation failed: {e}")

    return [f"{title}, vibrant, high quality, professional photography" for _ in range(num_prompts)]


# ============================================================
# Image generation with higher quality
# ============================================================

def generate_carousel_image(prompt: str) -> Image.Image:
    """Generate a single carousel image at higher quality (more steps)."""
    import fal_client

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        logger.info("No FAL_KEY, using gradient")
        return create_gradient_background()

    enhanced_prompt = (
        f"{prompt}, stunning photography, ultra high quality, sharp detail, "
        f"vibrant bright colors, high contrast, cinematic lighting, "
        f"vertical 9:16, professional quality, no text no words no letters"
    )

    for attempt in range(2):
        try:
            logger.info(f"Generating image: {prompt[:50]}...")
            result = fal_client.run(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": enhanced_prompt,
                    "image_size": "portrait_16_9",
                    "num_images": 1,
                    "num_inference_steps": CAROUSEL_IMAGE_STEPS,
                },
            )

            if result and "images" in result and result["images"]:
                image_url = result["images"][0]["url"]
                response = requests.get(image_url, timeout=30)
                img = Image.open(BytesIO(response.content)).convert("RGB")
                img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)
                logger.info("Image generated")
                return img

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    logger.info("Failed, using gradient")
    return create_gradient_background()


# ============================================================
# Text overlay
# ============================================================

def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Try to load a bold system font, falling back gracefully."""
    font_candidates = [
        "/System/Library/Fonts/Helvetica.ttc",          # macOS
        "/System/Library/Fonts/SFProDisplay-Bold.otf",   # macOS modern
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = font.getbbox(test_line)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines if lines else [text]


def add_text_overlay(img: Image.Image, text: str) -> Image.Image:
    """Add bold caption text at the bottom of the image with dark gradient backdrop."""
    img = img.copy()
    draw = ImageDraw.Draw(img)
    max_text_width = VIDEO_WIDTH - (TEXT_MARGIN * 2)

    # Find best font size that fits
    font_size = MAX_FONT_SIZE
    font = _get_font(font_size)
    lines = _wrap_text(text, font, max_text_width)

    while len(lines) > 3 and font_size > MIN_FONT_SIZE:
        font_size -= 4
        font = _get_font(font_size)
        lines = _wrap_text(text, font, max_text_width)

    # Calculate text block height
    line_heights = []
    for line in lines:
        bbox = font.getbbox(line)
        line_heights.append(bbox[3] - bbox[1])
    total_text_height = sum(line_heights) + LINE_SPACING * (len(lines) - 1)

    # Draw gradient backdrop at bottom
    gradient_height = total_text_height + TEXT_PADDING * 4
    gradient_top = VIDEO_HEIGHT - gradient_height
    for y in range(gradient_top, VIDEO_HEIGHT):
        alpha = int(180 * ((y - gradient_top) / gradient_height))
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(0, 0, 0, min(alpha, 180)))

    # Position text centered at bottom
    text_y = VIDEO_HEIGHT - total_text_height - TEXT_PADDING * 2

    for line in lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        text_x = (VIDEO_WIDTH - line_width) // 2

        # Draw text shadow
        shadow_offset = 3
        draw.text(
            (text_x + shadow_offset, text_y + shadow_offset),
            line, font=font, fill=(0, 0, 0)
        )
        draw.text(
            (text_x - shadow_offset, text_y + shadow_offset),
            line, font=font, fill=(0, 0, 0)
        )

        # Draw main text
        draw.text((text_x, text_y), line, font=font, fill=(255, 255, 255))
        text_y += line_heights[lines.index(line)] + LINE_SPACING

    return img


# ============================================================
# Main orchestrator
# ============================================================

def generate_carousel(article_id: int, title: str, script: str, image_source: str = "ai") -> dict:
    """
    Generate a TikTok Photo Carousel: 6 PNG slides + 1 WAV voice file.

    Args:
        image_source: 'ai' for FAL.ai, 'stock' for Pexels stock photos.

    Returns:
        dict with keys: carousel_dir (str), carousel_audio (str), slides (list of paths)
    """
    carousels_base = Path("static/carousels")
    output_dir = carousels_base / str(article_id)

    # Clean previous run
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"\n{'='*50}")
        logger.info(f"[Carousel] Generating carousel for article {article_id} (images: {image_source})")
        logger.info(f"{'='*50}")

        # Step 1: Extract key points for text overlays
        logger.info("[Carousel] Step 1: Extracting key points...")
        key_points = extract_key_points(title, script, NUM_SLIDES)
        logger.info(f"[Carousel] {len(key_points)} key points extracted")

        # Step 2 & 3: Generate images
        if image_source == "stock":
            logger.info(f"[Carousel] Step 2-3: Fetching {NUM_SLIDES} stock photos from Pexels...")
            keywords = _extract_search_keywords(title, script)
            images = []
            per_keyword = max(1, NUM_SLIDES // len(keywords))
            for kw in keywords:
                if len(images) >= NUM_SLIDES:
                    break
                needed = min(per_keyword, NUM_SLIDES - len(images))
                images.extend(search_pexels_images(kw, needed))
            while len(images) < NUM_SLIDES:
                images.extend(search_pexels_images(title, NUM_SLIDES - len(images)))
            images = images[:NUM_SLIDES]
        else:
            # AI-generated images
            logger.info("[Carousel] Step 2: Generating image prompts...")
            prompts = generate_carousel_prompts(title, script, NUM_SLIDES)

            logger.info(f"[Carousel] Step 3: Generating {NUM_SLIDES} images (higher quality)...")
            images = [None] * NUM_SLIDES
            with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
                future_to_idx = {
                    executor.submit(generate_carousel_image, prompt): i
                    for i, prompt in enumerate(prompts)
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        images[idx] = future.result()
                    except Exception as e:
                        logger.info(f"[Carousel] Image {idx + 1} failed: {e}")
                        images[idx] = create_gradient_background()

        # Step 4: Add text overlays
        logger.info("[Carousel] Step 4: Adding text overlays...")
        slide_paths = []
        for i, (img, caption) in enumerate(zip(images, key_points)):
            slide_with_text = add_text_overlay(img, caption)
            slide_path = output_dir / f"slide_{i + 1}.png"
            slide_with_text.save(str(slide_path), "PNG", optimize=True)
            slide_paths.append(str(slide_path))
            logger.info(f"[Carousel] Saved slide {i + 1}: {caption[:40]}...")

        # Step 5: Generate voiceover
        logger.info("[Carousel] Step 5: Generating voiceover...")
        audio_path = str(output_dir / "voiceover.mp3")
        actual_audio_path = tts_engine.synthesize(clean_text(script), audio_path)
        # Copy to the expected location if the engine wrote elsewhere.
        audio_filename = Path(actual_audio_path).name
        final_audio_path = str(output_dir / audio_filename)
        if actual_audio_path != final_audio_path:
            shutil.copy2(actual_audio_path, final_audio_path)
            # Remove temp file
            try:
                Path(actual_audio_path).unlink(missing_ok=True)
            except OSError:
                pass

        logger.info(f"\n{'='*50}")
        logger.info(f"[Carousel] SUCCESS: {output_dir}")
        logger.info(f"[Carousel]   Slides: {len(slide_paths)}")
        logger.info(f"[Carousel]   Audio: {audio_filename}")
        logger.info(f"{'='*50}\n")

        return {
            "carousel_dir": str(article_id),
            "carousel_audio": audio_filename,
            "slides": slide_paths,
        }

    except Exception as e:
        logger.error(f"Carousel generation failed: {e}", exc_info=True)
        # Clean up on failure
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise
