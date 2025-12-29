"""
TikTok-Style Video Generator with FAL.ai Image Generation and Kokoro TTS.

Features:
- Fast-paced text (3-4 word chunks)
- Visual changes every 2-3 seconds  
- Context-aware B-roll images
- Dramatic visual hook
- Kokoro-82M for high-quality TTS
"""

import os
import re
import time
import textwrap
from datetime import datetime
from pathlib import Path
from io import BytesIO

from moviepy.editor import (
    TextClip, ColorClip, ImageClip, CompositeVideoClip, 
    AudioFileClip, concatenate_videoclips
)
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import requests
from dotenv import load_dotenv

load_dotenv()


# Video settings for 9:16 (vertical TikTok/Reels/Shorts)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
CHUNK_DURATION = 2.0  # Seconds per visual (image changes every 2-3s)
HOOK_DURATION = 3.0   # First 3 seconds are the hook

# Text styling
FONT_SIZE = 72
FONT_COLOR = 'white'
STROKE_WIDTH = 4
BG_COLOR = (10, 10, 20)  # Dark fallback


# ============================================
# FAL.ai Image Generation  
# ============================================

def generate_image_fal(prompt: str, retry_count: int = 2) -> Image.Image:
    """
    Generate an image using FAL.ai FLUX.1-dev model.
    Returns PIL Image or solid background as fallback.
    """
    import fal_client
    
    fal_key = os.getenv('FAL_KEY')
    if not fal_key:
        print("[Image] No FAL_KEY found, using gradient background")
        return create_gradient_background()
    
    # Enhance prompt for vertical video B-roll
    enhanced_prompt = f"{prompt}, cinematic dramatic lighting, dark moody atmosphere, vertical composition 9:16, professional photography, no text no words"
    
    for attempt in range(retry_count):
        try:
            print(f"[Image] Generating: {prompt[:40]}...")
            
            result = fal_client.run(
                "fal-ai/flux/dev",
                arguments={
                    "prompt": enhanced_prompt,
                    "image_size": "portrait_16_9",
                    "num_images": 1
                }
            )
            
            if result and 'images' in result and len(result['images']) > 0:
                image_url = result['images'][0]['url']
                
                # Download image
                response = requests.get(image_url, timeout=30)
                img = Image.open(BytesIO(response.content))
                
                # Resize to video dimensions
                img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)
                
                print(f"[Image] ✅ Generated successfully!")
                return img
                
        except Exception as e:
            print(f"[Image] ⚠️ Attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    
    print("[Image] ❌ All attempts failed, using gradient")
    return create_gradient_background()


def create_gradient_background() -> Image.Image:
    """Create a stylish gradient background as fallback."""
    img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw = ImageDraw.Draw(img)
    
    # Dark purple to blue gradient
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
        # Image is wider - crop width
        new_height = orig_height
        new_width = int(new_height * target_ratio)
        left = (orig_width - new_width) // 2
        img = img.crop((left, 0, left + new_width, new_height))
    else:
        # Image is taller - crop height
        new_width = orig_width
        new_height = int(new_width / target_ratio)
        top = (orig_height - new_height) // 2
        img = img.crop((0, top, new_width, top + new_height))
    
    return img.resize((target_width, target_height), Image.LANCZOS)


def darken_image(img: Image.Image, factor: float = 0.6) -> Image.Image:
    """Darken image for better text readability."""
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Brightness(img)
    return enhancer.enhance(factor)


# ============================================
# Kokoro TTS (High-Quality Voice)
# ============================================

def generate_tts_kokoro(text: str, output_path: str) -> str:
    """
    Generate TTS using Kokoro-82M for natural, high-quality voice.
    Falls back to gTTS if Kokoro fails.
    """
    try:
        from kokoro import KPipeline
        import soundfile as sf
        import numpy as np
        
        print("[TTS] Using Kokoro-82M for voice...")
        
        pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
        
        # Generate with natural female voice
        generator = pipeline(text, voice='af_heart', speed=1.05)
        
        all_audio = []
        for i, (gs, ps, audio) in enumerate(generator):
            all_audio.extend(audio)
        
        audio_array = np.array(all_audio, dtype=np.float32)
        wav_path = output_path.replace('.mp3', '.wav')
        sf.write(wav_path, audio_array, 24000)
        
        print(f"[TTS] ✅ Audio saved: {wav_path}")
        return wav_path
        
    except Exception as e:
        print(f"[TTS] ⚠️ Kokoro failed: {e}, using gTTS...")
        from gtts import gTTS
        tts = gTTS(text=text, lang='en', slow=False)
        tts.save(output_path)
        return output_path


# ============================================
# TikTok-Style Text Chunking
# ============================================

def chunk_text_for_tiktok(text: str, words_per_chunk: int = 4) -> list:
    """
    Break text into small chunks for TikTok-style pacing.
    Each chunk appears for ~2-3 seconds.
    """
    # Clean the text
    clean_text = re.sub(r'\[.*?\]', '', text)  # Remove [HOOK] etc
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    words = clean_text.split()
    chunks = []
    
    for i in range(0, len(words), words_per_chunk):
        chunk = ' '.join(words[i:i + words_per_chunk])
        if chunk:
            chunks.append(chunk)
    
    return chunks


def extract_visual_keywords(text: str) -> list:
    """
    Extract keywords for B-roll image generation.
    Maps common words to visual concepts.
    """
    visual_mappings = {
        # Science/Tech
        'scientist': 'scientist in modern laboratory with glowing equipment',
        'scientists': 'scientists working in futuristic lab',
        'research': 'researcher analyzing data on holographic screens',
        'study': 'academic study with books and digital displays',
        'experiment': 'dramatic scientific experiment with light beams',
        
        # Space/Universe
        'universe': 'vast cosmic galaxy with stars and nebulae',
        'space': 'deep space with galaxies and cosmic dust',
        'cosmos': 'cosmic nebula with swirling stars',
        'galaxy': 'spiral galaxy with brilliant stars',
        'simulation': 'digital matrix code reality simulation',
        
        # Technology
        'computer': 'futuristic computer with holographic display',
        'ai': 'artificial intelligence neural network visualization',
        'technology': 'advanced futuristic technology interface',
        'digital': 'digital world with flowing data streams',
        'code': 'programming code on glowing screens',
        
        # Abstract
        'reality': 'abstract reality bending visual',
        'consciousness': 'ethereal consciousness visualization',
        'future': 'futuristic cityscape with flying vehicles',
        'mystery': 'mysterious dark atmospheric scene',
        'question': 'philosophical question mark in cosmic setting',
    }
    
    keywords = []
    text_lower = text.lower()
    
    for keyword, visual in visual_mappings.items():
        if keyword in text_lower:
            keywords.append(visual)
    
    return keywords


def get_broll_prompt_for_chunk(chunk: str, article_title: str) -> str:
    """
    Generate a B-roll image prompt based on text chunk content.
    """
    # Check for visual keywords in chunk
    visuals = extract_visual_keywords(chunk)
    if visuals:
        return visuals[0]
    
    # Check title for theme
    title_visuals = extract_visual_keywords(article_title)
    if title_visuals:
        return title_visuals[0]
    
    # Generic dramatic visual
    return f"dramatic cinematic visualization of: {chunk}"


# ============================================
# Video Clip Creation
# ============================================

def create_text_overlay(text: str, duration: float, position: str = 'center') -> TextClip:
    """
    Create a bold, TikTok-style text overlay.
    """
    # Wrap text for mobile viewing
    wrapped = '\n'.join(textwrap.wrap(text.upper(), width=15))
    
    try:
        txt = TextClip(
            wrapped,
            fontsize=FONT_SIZE,
            color=FONT_COLOR,
            font='Arial-Bold',
            size=(VIDEO_WIDTH - 80, None),
            method='caption',
            align='center',
            stroke_color='black',
            stroke_width=STROKE_WIDTH
        ).set_duration(duration)
    except:
        txt = TextClip(
            wrapped,
            fontsize=FONT_SIZE,
            color=FONT_COLOR,
            size=(VIDEO_WIDTH - 80, None),
            method='caption',
            align='center',
            stroke_color='black',
            stroke_width=STROKE_WIDTH
        ).set_duration(duration)
    
    # Position in lower third
    if position == 'center':
        return txt.set_position(('center', VIDEO_HEIGHT - 500))
    return txt.set_position(('center', 'center'))


def create_clip_with_broll(image: Image.Image, duration: float) -> ImageClip:
    """
    Create a video clip with B-roll image (no text overlay).
    Just visuals + voiceover audio.
    """
    videos_dir = ensure_videos_dir()
    temp_img_path = videos_dir / f'temp_img_{int(time.time() * 1000)}.jpg'
    
    # Slight darkening for cinematic look
    darkened = darken_image(image, factor=0.7)
    darkened.save(temp_img_path, quality=95)
    
    # Create image clip
    bg = ImageClip(str(temp_img_path)).set_duration(duration)
    
    # Cleanup temp file
    try:
        temp_img_path.unlink()
    except:
        pass
    
    return bg


def create_hook_clip(title: str, duration: float = 3.0) -> ImageClip:
    """
    Create a dramatic hook for the first 3 seconds.
    """
    # Generate dramatic hook image
    hook_prompt = f"dramatic attention-grabbing visual for: {title}, epic cinematic lighting, intense atmosphere"
    hook_image = generate_image_fal(hook_prompt)
    
    return create_clip_with_broll(hook_image, duration)


# ============================================
# Main Video Generation
# ============================================

def ensure_videos_dir() -> Path:
    """Ensure videos directory exists."""
    videos_dir = Path('static/videos')
    videos_dir.mkdir(parents=True, exist_ok=True)
    return videos_dir


def generate_video(article_id: int, title: str, script: str, hero_image: str = None) -> str:
    """
    Generate a TikTok-style video from article script.
    
    Features:
    - Dramatic hook in first 3 seconds
    - Text chunks (3-4 words) for fast pacing
    - Visual changes every 2-3 seconds
    - Context-aware B-roll images
    - High-quality Kokoro TTS voiceover
    """
    videos_dir = ensure_videos_dir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = videos_dir / f'article_{article_id}_{timestamp}.mp4'
    temp_audio_path = videos_dir / f'temp_audio_{article_id}.mp3'
    
    try:
        # Step 1: Generate TTS audio
        print(f"\n{'='*50}")
        print(f"[Video] 🎬 Generating TikTok-style video for article {article_id}")
        print(f"{'='*50}")
        
        print("[Video] Step 1: Generating voiceover...")
        actual_audio_path = generate_tts_kokoro(script, str(temp_audio_path))
        
        # Load audio to get duration
        audio = AudioFileClip(actual_audio_path)
        audio_duration = audio.duration
        print(f"[Video] Audio duration: {audio_duration:.1f}s")
        
        # Step 2: Chunk text for TikTok pacing
        print("[Video] Step 2: Chunking text for TikTok pacing...")
        chunks = chunk_text_for_tiktok(script, words_per_chunk=4)
        print(f"[Video] Created {len(chunks)} text chunks")
        
        # Calculate time per chunk
        time_per_chunk = audio_duration / len(chunks) if chunks else 2.5
        time_per_chunk = max(1.5, min(3.5, time_per_chunk))  # Clamp between 1.5-3.5s
        
        # Step 3: Generate B-roll images for each chunk
        print(f"[Video] Step 3: Generating {len(chunks)} B-roll images...")
        
        clips = []
        for i, chunk in enumerate(chunks):
            print(f"[Video] Chunk {i+1}/{len(chunks)}: '{chunk[:30]}...'")
            
            # Generate B-roll for this chunk
            broll_prompt = get_broll_prompt_for_chunk(chunk, title)
            
            # Reuse some images to reduce API calls (every 3rd chunk gets new image)
            if i % 3 == 0 or i < 3:
                current_image = generate_image_fal(broll_prompt)
            
            clip = create_clip_with_broll(current_image, time_per_chunk)
            clips.append(clip)
        
        # Step 4: Concatenate all clips
        print("[Video] Step 4: Assembling video...")
        main_video = concatenate_videoclips(clips)
        
        # Adjust video to match audio duration
        if main_video.duration != audio_duration:
            if main_video.duration > audio_duration:
                main_video = main_video.subclip(0, audio_duration)
            # If video is shorter, the last frame will hold
        
        # Add audio
        main_video = main_video.set_audio(audio)
        
        # Step 5: Render final video
        print("[Video] Step 5: Rendering final video...")
        main_video.write_videofile(
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
        main_video.close()
        for clip in clips:
            clip.close()
        
        try:
            Path(actual_audio_path).unlink()
        except:
            pass
        
        print(f"\n{'='*50}")
        print(f"[Video] ✅ SUCCESS! Video saved to: {output_path}")
        print(f"{'='*50}\n")
        
        return str(output_path)
        
    except Exception as e:
        print(f"[Video] ❌ Error generating video: {e}")
        import traceback
        traceback.print_exc()
        raise


# ============================================
# Test
# ============================================

if __name__ == '__main__':
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
