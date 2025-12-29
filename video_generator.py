"""Video Generator using Google TTS and MoviePy with AI-generated images."""

import os
import textwrap
import time
from datetime import datetime
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from moviepy.editor import (
    TextClip, ColorClip, ImageClip, CompositeVideoClip, AudioFileClip, concatenate_videoclips
)
from PIL import Image, ImageDraw, ImageFont
import requests
from dotenv import load_dotenv

load_dotenv()


# Video settings for 9:16 (vertical short-form)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 60
FONT_COLOR = 'white'
BG_COLOR = (15, 15, 25)  # Dark blue-gray
SUBTITLE_BG_COLOR = (0, 0, 0, 180)  # Semi-transparent black


def generate_ai_image(prompt: str, retry_count: int = 3) -> Image.Image:
    """
    Generate an image using HuggingFace Inference API.

    Args:
        prompt: Description of the image to generate
        retry_count: Number of retries if generation fails

    Returns:
        PIL Image object
    """
    api_key = os.getenv('HUGGINGFACE_API_KEY')
    if not api_key:
        print("[Image] No HUGGINGFACE_API_KEY found, using solid background")
        # Return a solid background as fallback
        img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
        return img

    # Use FLUX.1-schnell - very fast and good quality (free on HF)
    api_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"

    headers = {"Authorization": f"Bearer {api_key}"}

    # Enhanced prompt for better vertical video images
    enhanced_prompt = f"{prompt}, cinematic, high quality, professional photography, vertical composition, 9:16 aspect ratio"

    for attempt in range(retry_count):
        try:
            print(f"[Image] Generating image (attempt {attempt + 1}/{retry_count}): {prompt[:50]}...")

            response = requests.post(
                api_url,
                headers=headers,
                json={"inputs": enhanced_prompt},
                timeout=60
            )

            if response.status_code == 503:
                # Model is loading, wait and retry
                print("[Image] Model loading, waiting 10s...")
                time.sleep(10)
                continue

            response.raise_for_status()

            # Load image from response
            img = Image.open(BytesIO(response.content))

            # Resize to video dimensions (1080x1920 for 9:16)
            img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)

            print(f"[Image] Generated successfully!")
            return img

        except Exception as e:
            print(f"[Image] Generation failed (attempt {attempt + 1}): {e}")
            if attempt < retry_count - 1:
                time.sleep(5)
            else:
                # Final fallback: solid background
                print("[Image] All attempts failed, using solid background")
                img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
                return img

    # Should never reach here, but just in case
    img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
    return img


def resize_and_crop_image(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """
    Resize and crop image to fit target dimensions while maintaining aspect ratio.
    Uses center crop.
    """
    # Get original dimensions
    orig_width, orig_height = img.size
    orig_ratio = orig_width / orig_height
    target_ratio = target_width / target_height

    if orig_ratio > target_ratio:
        # Image is wider than target, fit height and crop width
        new_height = target_height
        new_width = int(orig_width * (target_height / orig_height))
    else:
        # Image is taller than target, fit width and crop height
        new_width = target_width
        new_height = int(orig_height * (target_width / orig_width))

    # Resize
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Center crop to target dimensions
    left = (new_width - target_width) // 2
    top = (new_height - target_height) // 2
    right = left + target_width
    bottom = top + target_height

    img = img.crop((left, top, right, bottom))

    return img


def ensure_videos_dir():
    """Ensure videos directory exists."""
    videos_dir = Path(__file__).parent / 'videos'
    videos_dir.mkdir(exist_ok=True)
    return videos_dir


def generate_tts(text: str, output_path: str) -> str:
    """Generate TTS audio file from text using Google TTS."""
    tts = gTTS(text=text, lang='en', slow=False)
    tts.save(output_path)
    return output_path


def create_title_card(title: str, duration: float = 3.0) -> CompositeVideoClip:
    """Create an intro title card."""
    # Background
    bg = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=BG_COLOR, duration=duration)
    
    # Title text (wrapped)
    wrapped_title = "\n".join(textwrap.wrap(title, width=25))
    
    try:
        txt = TextClip(
            wrapped_title,
            fontsize=70,
            color='white',
            font='Arial-Bold',
            size=(VIDEO_WIDTH - 100, None),
            method='caption',
            align='center'
        ).set_duration(duration).set_position('center')
    except Exception:
        # Fallback if font not found
        txt = TextClip(
            wrapped_title,
            fontsize=70,
            color='white',
            size=(VIDEO_WIDTH - 100, None),
            method='caption',
            align='center'
        ).set_duration(duration).set_position('center')
    
    return CompositeVideoClip([bg, txt])


def create_subtitle_clip_with_image(text: str, image: Image.Image, duration: float) -> CompositeVideoClip:
    """Create a video clip with AI-generated image background and text overlay."""
    # Save PIL Image to temporary file for MoviePy
    videos_dir = ensure_videos_dir()
    temp_img_path = videos_dir / f'temp_img_{int(time.time() * 1000)}.jpg'

    # Apply slight darkening to image for better text visibility
    darkened_img = Image.new('RGB', image.size)
    darkened_img.paste(image)
    # Add a semi-transparent dark overlay
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 80))
    darkened_img = Image.alpha_composite(darkened_img.convert('RGBA'), overlay).convert('RGB')

    darkened_img.save(temp_img_path, quality=95)

    # Create ImageClip
    bg = ImageClip(str(temp_img_path)).set_duration(duration)

    # Subtitle text (wrapped and positioned at bottom third)
    wrapped_text = "\n".join(textwrap.wrap(text, width=30))

    try:
        txt = TextClip(
            wrapped_text,
            fontsize=FONT_SIZE,
            color=FONT_COLOR,
            font='Arial-Bold',
            size=(VIDEO_WIDTH - 100, None),
            method='caption',
            align='center',
            stroke_color='black',
            stroke_width=3
        ).set_duration(duration).set_position(('center', VIDEO_HEIGHT - 450))
    except Exception:
        # Fallback without bold font
        txt = TextClip(
            wrapped_text,
            fontsize=FONT_SIZE,
            color=FONT_COLOR,
            size=(VIDEO_WIDTH - 100, None),
            method='caption',
            align='center',
            stroke_color='black',
            stroke_width=3
        ).set_duration(duration).set_position(('center', VIDEO_HEIGHT - 450))

    # Clean up temp file after creating clip
    try:
        temp_img_path.unlink()
    except:
        pass

    return CompositeVideoClip([bg, txt])


def extract_image_prompts(script: str, num_images: int = 4) -> list:
    """
    Extract key phrases from script to use as image generation prompts.
    Returns simplified visual descriptions.
    """
    # Remove labels like [HOOK], [BIG IDEA], etc.
    clean_script = script
    for label in ['[HOOK]', '[BIG IDEA]', '[WORKS]', '[CAVEAT]', '[CLOSE]']:
        clean_script = clean_script.replace(label, '')

    # Split into sentences
    sentences = []
    current = ""
    for char in clean_script:
        current += char
        if char in '.!?' and len(current) > 20:
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())

    # Select evenly distributed sentences for image prompts
    if len(sentences) <= num_images:
        selected = sentences
    else:
        step = len(sentences) / num_images
        indices = [int(i * step) for i in range(num_images)]
        selected = [sentences[i] for i in indices]

    # Create visual prompts from sentences
    prompts = []
    for sentence in selected:
        # Extract first few words as a visual concept
        words = sentence.split()[:8]
        prompt = ' '.join(words)
        # Clean up
        prompt = prompt.replace('[', '').replace(']', '')
        prompts.append(prompt)

    return prompts


def generate_video(article_id: int, title: str, script: str, hero_image: str = None) -> str:
    """
    Generate a video from a script with TTS voiceover, AI-generated images, and subtitles.

    Args:
        article_id: ID of the article
        title: Article title for intro card
        script: Video script text
        hero_image: Optional URL to hero image (not used with AI generation)

    Returns:
        Path to the generated MP4 file
    """
    videos_dir = ensure_videos_dir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = videos_dir / f'article_{article_id}_{timestamp}.mp4'
    temp_audio_path = videos_dir / f'temp_audio_{article_id}.mp3'

    try:
        # Step 1: Generate TTS audio
        print(f"[Video] Generating TTS for article {article_id}...")
        generate_tts(script, str(temp_audio_path))

        # Load audio to get duration
        audio = AudioFileClip(str(temp_audio_path))
        audio_duration = audio.duration

        # Step 2: Create title card (3 seconds)
        print(f"[Video] Creating title card...")
        title_clip = create_title_card(title, duration=3.0)

        # Step 3: Split script into sentences
        print(f"[Video] Processing script...")
        sentences = []
        current = ""
        for char in script:
            current += char
            if char in '.!?' and len(current) > 20:
                sentences.append(current.strip())
                current = ""
        if current.strip():
            sentences.append(current.strip())

        if not sentences:
            sentences = [script]

        # Calculate time per sentence
        time_per_sentence = audio_duration / len(sentences)

        # Step 4: Generate AI images based on script content
        print(f"[Video] Generating {len(sentences)} AI images...")
        image_prompts = extract_image_prompts(script, num_images=len(sentences))

        ai_images = []
        for i, prompt in enumerate(image_prompts):
            print(f"[Video] Image {i+1}/{len(image_prompts)}")
            img = generate_ai_image(prompt)
            ai_images.append(img)

        # Step 5: Create clips with AI images and text overlays
        print(f"[Video] Creating video clips with images and text...")
        subtitle_clips = []
        for i, sentence in enumerate(sentences):
            # Use corresponding AI image (or loop if we have fewer images)
            img_index = i % len(ai_images)
            clip = create_subtitle_clip_with_image(sentence, ai_images[img_index], time_per_sentence)
            subtitle_clips.append(clip)

        if subtitle_clips:
            main_content = concatenate_videoclips(subtitle_clips)
        else:
            # Fallback
            img = generate_ai_image(title)
            main_content = create_subtitle_clip_with_image(script, img, audio_duration)

        # Add audio to main content
        main_content = main_content.set_audio(audio)
        
        # Step 4: Concatenate title + main content
        print(f"[Video] Rendering final video...")
        final_video = concatenate_videoclips([title_clip, main_content])
        
        # Write output
        final_video.write_videofile(
            str(output_path),
            fps=FPS,
            codec='libx264',
            audio_codec='aac',
            threads=4,
            preset='medium',
            verbose=False,
            logger=None
        )
        
        # Cleanup
        audio.close()
        final_video.close()
        if temp_audio_path.exists():
            temp_audio_path.unlink()
        
        print(f"[Video] Generated: {output_path}")
        return str(output_path)
        
    except Exception as e:
        print(f"[Video] Error generating video: {e}")
        # Cleanup temp files on error
        if temp_audio_path.exists():
            temp_audio_path.unlink()
        raise


if __name__ == '__main__':
    # Test video generation
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
    print(f"Test video saved to: {output}")
