"""Video Generator using Google TTS and MoviePy."""

import os
import textwrap
from datetime import datetime
from pathlib import Path

from gtts import gTTS
from moviepy.editor import (
    TextClip, ColorClip, CompositeVideoClip, AudioFileClip, concatenate_videoclips
)
from PIL import Image, ImageDraw, ImageFont
import requests


# Video settings for 9:16 (vertical short-form)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 60
FONT_COLOR = 'white'
BG_COLOR = (15, 15, 25)  # Dark blue-gray
SUBTITLE_BG_COLOR = (0, 0, 0, 180)  # Semi-transparent black


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


def create_subtitle_clip(text: str, duration: float) -> CompositeVideoClip:
    """Create a video clip with subtitles."""
    # Background
    bg = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=BG_COLOR, duration=duration)
    
    # Subtitle text (wrapped and positioned at bottom)
    wrapped_text = "\n".join(textwrap.wrap(text, width=30))
    
    try:
        txt = TextClip(
            wrapped_text,
            fontsize=FONT_SIZE,
            color=FONT_COLOR,
            font='Arial',
            size=(VIDEO_WIDTH - 80, None),
            method='caption',
            align='center',
            stroke_color='black',
            stroke_width=2
        ).set_duration(duration).set_position(('center', VIDEO_HEIGHT - 400))
    except Exception:
        txt = TextClip(
            wrapped_text,
            fontsize=FONT_SIZE,
            color=FONT_COLOR,
            size=(VIDEO_WIDTH - 80, None),
            method='caption',
            align='center'
        ).set_duration(duration).set_position(('center', VIDEO_HEIGHT - 400))
    
    return CompositeVideoClip([bg, txt])


def generate_video(article_id: int, title: str, script: str, hero_image: str = None) -> str:
    """
    Generate a video from a script with TTS voiceover and subtitles.
    
    Args:
        article_id: ID of the article
        title: Article title for intro card
        script: Video script text
        hero_image: Optional URL to hero image
        
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
        
        # Step 3: Create main content clip with subtitles
        print(f"[Video] Creating subtitle clips...")
        
        # Split script into sentences for better subtitle display
        sentences = []
        current = ""
        for char in script:
            current += char
            if char in '.!?' and len(current) > 20:
                sentences.append(current.strip())
                current = ""
        if current.strip():
            sentences.append(current.strip())
        
        # Calculate time per sentence
        time_per_sentence = audio_duration / len(sentences) if sentences else audio_duration
        
        # Create clips for each sentence
        subtitle_clips = []
        for sentence in sentences:
            clip = create_subtitle_clip(sentence, time_per_sentence)
            subtitle_clips.append(clip)
        
        if subtitle_clips:
            main_content = concatenate_videoclips(subtitle_clips)
        else:
            main_content = create_subtitle_clip(script, audio_duration)
        
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
