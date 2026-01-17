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

# Video timing constraints
MIN_CHUNK_DURATION = 1.5  # Minimum seconds per chunk
MAX_CHUNK_DURATION = 3.5  # Maximum seconds per chunk
DEFAULT_CHUNK_DURATION = 2.5  # Fallback if no chunks

# Image generation settings
IMAGE_DARKEN_FACTOR = 0.85  # Slightly darken for text readability (0.0-1.0, higher = brighter)
DEFAULT_WORDS_PER_CHUNK = 4  # Words per text chunk for TikTok pacing
RETRY_ATTEMPTS = 2  # Number of retry attempts for image generation


# ============================================
# FAL.ai Image Generation  
# ============================================

def generate_image_fal(prompt: str, retry_count: int = RETRY_ATTEMPTS) -> Image.Image:
    """
    Generate an image using FAL.ai FLUX.1-dev model.
    Returns PIL Image or solid background as fallback.
    """
    import fal_client
    
    fal_key = os.getenv('FAL_KEY')
    if not fal_key:
        print("[Image] No FAL_KEY found, using gradient background")
        return create_gradient_background()
    
    # Enhance prompt for vertical video B-roll - BRIGHT and eye-catching for TikTok
    enhanced_prompt = f"{prompt}, vibrant bright colors, high contrast, eye-catching, clean composition, vertical 9:16, professional quality, no text no words"
    
    for attempt in range(retry_count):
        try:
            print(f"[Image] Generating: {prompt[:40]}...")
            
            result = fal_client.run(
                "fal-ai/flux/schnell",  # Cheapest option: $0.003/MP vs $0.025/MP for dev
                arguments={
                    "prompt": enhanced_prompt,
                    "image_size": "portrait_16_9",
                    "num_images": 1,
                    "num_inference_steps": 4  # schnell is optimized for 1-4 steps
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


def darken_image(img: Image.Image, factor: float = IMAGE_DARKEN_FACTOR) -> Image.Image:
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
# Script Cleaning
# ============================================

def clean_script_for_tts(script: str) -> str:
    """
    Remove structural tags like [HOOK], [BIG IDEA], [WORKS], [CAVEAT], [CLOSE] 
    from the script before TTS generation so they aren't spoken.
    """
    # Remove all bracketed tags
    clean_text = re.sub(r'\[.*?\]', '', script)
    # Clean up extra whitespace
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text


# ============================================
# TikTok-Style Text Chunking
# ============================================

def chunk_text_for_tiktok(text: str, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list:
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


# ============================================
# AI-Powered Style Selection (TikTok-Optimized)
# ============================================

# Three TikTok-friendly style categories - all bright and eye-catching
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
    """
    Use Groq AI to analyze the article and select the best TikTok-friendly visual style.
    Chooses between 3 bright, eye-catching styles based on story mood.
    """
    from groq import Groq
    
    api_key = os.getenv('GROQ_API_KEY')
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

DECISION PROCESS:
1. What is the TOPIC? (tech, science, news, lifestyle, business, etc.)
2. What is the MOOD? (inspiring, educational, exciting, serious, fun)
3. Who is the AUDIENCE? (Gen-Z, professionals, general public)

EXAMPLES:
- AI/Tech article → STYLE A (3D Pixar) or STYLE C (Flat Illustration)
- Health/Fitness → STYLE B (Vibrant Photography)
- Business/Finance → STYLE C (Bold Flat Illustration)
- Science discovery → STYLE A (3D Pixar/CGI)
- News/Current events → STYLE B (Vibrant Photography)
- Trending/Viral topic → STYLE C (Bold Flat Illustration)

Respond with ONLY the style keywords. Example responses:
- "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"
- "Professional photography, bright natural lighting, saturated colors, high contrast, vivid"
- "Flat illustration, bold colors, modern design, clean lines, vibrant, graphic"
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
        
        chosen_style = response.choices[0].message.content.strip()
        chosen_style = chosen_style.strip('"\'')
        
        # Ensure we always add brightness keywords
        if "bright" not in chosen_style.lower() and "vibrant" not in chosen_style.lower():
            chosen_style += ", bright, vibrant, eye-catching"
        
        print(f"[Style] ✅ Selected style: {chosen_style}")
        return chosen_style
        
    except Exception as e:
        print(f"[Style] ⚠️ Style selection failed: {e}, using default bright 3D style")
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"


def extract_story_subjects(title: str, script: str) -> dict:
    """
    Step 1 of image generation: Extract core subjects from the story.
    This ensures images are anchored to what the article is ACTUALLY about.
    
    Returns:
        dict with keys: main_subject, visual_keywords, setting
    """
    from groq import Groq
    import json
    
    api_key = os.getenv('GROQ_API_KEY')
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

YOUR TASK:
Identify the CONCRETE, VISUAL subjects that images should show.

EXAMPLES:
- Article about Greenland melting → main_subject: "Greenland ice sheet", visual_keywords: ["glaciers", "ice", "arctic ocean", "polar landscape", "melting ice"]
- Article about bacteria → main_subject: "bacteria and microbes", visual_keywords: ["microscope", "petri dish", "cells", "laboratory", "scientists"]
- Article about SpaceX → main_subject: "SpaceX rockets", visual_keywords: ["rocket launch", "spacecraft", "Elon Musk", "space", "launchpad"]

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
        
        # Parse JSON response
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
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
    """
    Use Groq AI to generate image prompts ANCHORED to the story's core subjects.
    
    The script is now included so Groq understands the narrative flow and can
    generate prompts that match specific story beats.
    
    Args:
        title: Article title
        script: Full video script (used to understand narrative beats)
        num_prompts: Number of image prompts to generate
        style: Visual style to apply
        subjects: Dict with main_subject, visual_keywords, setting (from extract_story_subjects)
    
    Returns:
        List of image prompt strings, or None if generation fails
    """
    from groq import Groq
    import json
    import re as regex_module
    
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        print("[Prompts] ⚠️ No GROQ_API_KEY, using fallback prompts")
        return None
    
    # Use default style if not provided
    if not style:
        style = "cinematic, photorealistic, professional photography"
    
    # Build subject constraints
    if subjects:
        main_subject = subjects.get('main_subject', title)
        visual_keywords = subjects.get('visual_keywords', [])
        setting = subjects.get('setting', 'general environment')
        keywords_str = ', '.join(visual_keywords) if visual_keywords else title
    else:
        main_subject = title
        visual_keywords = [title]
        keywords_str = title
        setting = "relevant environment"
    
    def parse_json_response(text: str) -> list:
        """Robustly parse JSON from LLM response, handling common issues."""
        text = text.strip()
        
        # Remove markdown code fences (```json ... ``` or ``` ... ```)
        if '```' in text:
            # Find content between fences
            fence_match = regex_module.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if fence_match:
                text = fence_match.group(1).strip()
            else:
                # Fallback: just remove the fences
                text = regex_module.sub(r'```(?:json)?', '', text).strip()
        
        # Try to find JSON array in the text (handles leading/trailing text)
        array_match = regex_module.search(r'\[[\s\S]*\]', text)
        if array_match:
            text = array_match.group(0)
        
        # Clean up common issues
        text = text.strip()
        
        return json.loads(text)
    
    def validate_prompts(prompts: list, visual_keywords: list, setting: str) -> tuple:
        """
        Validate that prompts include required keywords and relate to setting.
        Returns (is_valid, list of invalid prompt indices).
        """
        if not isinstance(prompts, list):
            return False, []
        
        invalid_indices = []
        keywords_lower = [kw.lower() for kw in visual_keywords] if visual_keywords else []
        setting_lower = setting.lower() if setting else ""
        
        for i, prompt in enumerate(prompts):
            if not isinstance(prompt, str):
                invalid_indices.append(i)
                continue
            
            prompt_lower = prompt.lower()
            
            # Check if at least one visual keyword is present
            has_keyword = any(kw in prompt_lower for kw in keywords_lower) if keywords_lower else True
            
            # Check if setting or related terms are present (more lenient check)
            setting_words = setting_lower.split() if setting_lower else []
            has_setting = any(word in prompt_lower for word in setting_words if len(word) > 3) if setting_words else True
            
            if not has_keyword:
                invalid_indices.append(i)
        
        return len(invalid_indices) == 0, invalid_indices
    
    def build_prompt(script_content: str, main_subject: str, setting: str, keywords_str: str, style: str, num_prompts: int, is_retry: bool = False) -> str:
        """Build the Groq prompt, optionally with stricter instructions for retry."""
        
        # Truncate script if too long but keep enough context
        script_excerpt = script_content[:4000] if len(script_content) > 4000 else script_content
        
        strictness_prefix = ""
        if is_retry:
            strictness_prefix = """⚠️ CRITICAL: Your previous response was invalid. This time you MUST:
- Include AT LEAST ONE of the required visual keywords in EVERY prompt
- Make EVERY image directly about the main subject
- DO NOT generate any generic or abstract images

"""
        
        return f"""{strictness_prefix}Generate {num_prompts} image prompts for a TikTok-style video.

=== ARTICLE INFORMATION ===
TITLE: {title}
MAIN SUBJECT: {main_subject}
SETTING/LOCATION: {setting}

=== FULL SCRIPT (understand the narrative flow) ===
{script_excerpt}

=== REQUIRED VISUAL KEYWORDS ===
At least ONE of these MUST appear in EVERY image prompt:
{keywords_str}

=== VISUAL STYLE ===
Apply this style to ALL images: {style}

=== YOUR TASK ===
Generate {num_prompts} image prompts that:
1. FOLLOW THE SCRIPT'S NARRATIVE - each prompt should match a story beat/section
2. Include at least ONE required visual keyword in EVERY prompt
3. Are set in/related to: {setting}
4. Apply the visual style: {style}
5. Are 15-30 words each
6. Have NO text/words in images. NO SIGNS. NO LABELS.
7. Use vertical 9:16 composition
8. Are SPECIFIC and CONCRETE, not abstract.
9. **QUALITY BOOSTERS**: Append "4k, cinematic lighting, trending on artstation, masterpiece" to every prompt.

=== BAD EXAMPLES (NEVER generate these) ===
- "dramatic landscape with moody lighting" (too generic)
- "person in contemplation" (not about the subject)
- "abstract energy flowing" (too vague)
- "inspiring scene" (meaningless)
- "text showing statistics" (NO TEXT allowed)

=== GOOD EXAMPLES ===
For an article about "Greenland ice melting":
- "massive glacier calving into arctic ocean, huge ice chunks crashing into water, {style}, 4k, cinematic lighting"
- "aerial drone view of Greenland ice sheet with bright blue melt pools and crevasses, {style}, hyperrealistic"
- "scientist in red parka taking ice core samples on vast white glacier, {style}, sharp focus"

Respond with ONLY a valid JSON array of exactly {num_prompts} string prompts:
["prompt 1", "prompt 2", ...]"""

    try:
        print("[Prompts] 🧠 Generating story-aware image prompts...")
        print(f"[Prompts] 🎨 Style: {style}")
        print(f"[Prompts] 📌 Must include: {keywords_str}")
        print(f"[Prompts] 📖 Using script ({len(script)} chars) for narrative context")
        
        client = Groq(api_key=api_key)
        
        # First attempt
        prompt = build_prompt(script, main_subject, setting, keywords_str, style, num_prompts, is_retry=False)
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"You are a visual director generating image prompts for a video about '{main_subject}'. Read the full script to understand the story, then generate prompts that match the narrative beats. Every prompt MUST include something from: {keywords_str}. Respond ONLY with a valid JSON array."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        text = response.choices[0].message.content.strip()
        
        try:
            prompts = parse_json_response(text)
        except json.JSONDecodeError as e:
            print(f"[Prompts] ⚠️ JSON parse failed: {e}")
            return None
        
        if not isinstance(prompts, list) or len(prompts) < num_prompts:
            print(f"[Prompts] ⚠️ Invalid response format (got {len(prompts) if isinstance(prompts, list) else 'non-list'})")
            return None
        
        prompts = prompts[:num_prompts]
        
        # Validate prompts
        is_valid, invalid_indices = validate_prompts(prompts, visual_keywords, setting)
        
        if is_valid:
            print(f"[Prompts] ✅ Generated {len(prompts)} validated story-aware prompts")
            return prompts
        
        # Retry with stricter instructions
        print(f"[Prompts] ⚠️ {len(invalid_indices)} prompts failed validation, retrying with stricter instructions...")
        
        retry_prompt = build_prompt(script, main_subject, setting, keywords_str, style, num_prompts, is_retry=True)
        
        retry_response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"CRITICAL: You MUST generate image prompts where EVERY single prompt contains at least one of these keywords: {keywords_str}. The prompts must be about '{main_subject}' set in '{setting}'. Respond ONLY with a valid JSON array."},
                {"role": "user", "content": retry_prompt}
            ],
            temperature=0.5,  # Lower temperature for more focused output
            max_tokens=2000
        )
        
        retry_text = retry_response.choices[0].message.content.strip()
        
        try:
            retry_prompts = parse_json_response(retry_text)
        except json.JSONDecodeError:
            print("[Prompts] ⚠️ Retry JSON parse failed, returning None for fallback")
            return None
        
        if not isinstance(retry_prompts, list) or len(retry_prompts) < num_prompts:
            print("[Prompts] ⚠️ Retry response invalid, returning None for fallback")
            return None
        
        retry_prompts = retry_prompts[:num_prompts]
        
        # Validate retry
        is_valid_retry, _ = validate_prompts(retry_prompts, visual_keywords, setting)
        
        if is_valid_retry:
            print(f"[Prompts] ✅ Retry succeeded! Generated {len(retry_prompts)} validated prompts")
            return retry_prompts
        else:
            print("[Prompts] ⚠️ Retry still invalid, returning None to trigger fallback")
            return None
            
    except Exception as e:
        print(f"[Prompts] ⚠️ Groq failed: {e}, using fallback")
        return None


def get_fallback_prompts(num_prompts: int = 10) -> list:
    """Fallback generic prompts if Groq fails."""
    fallback = [
        "dramatic cinematic landscape with moody golden hour lighting",
        "person in contemplation looking at horizon, silhouette",
        "abstract visualization of connection and energy flowing",
        "emotional moment between people, warm lighting",
        "inspiring scene of discovery and realization",
        "nature scene symbolizing growth and transformation",
        "close-up of hands reaching toward light",
        "atmospheric scene with fog and mystery",
        "powerful sunrise breaking through clouds",
        "serene moment of peace and understanding"
    ]
    return fallback[:num_prompts]


def generate_themed_images(title: str, script: str, num_images: int = 10) -> list:
    """
    Generate themed images anchored to the story's core subjects.
    
    Flow:
    1. Extract core subjects (what the story is LITERALLY about)
    2. Select visual style
    3. Generate image prompts anchored to subjects
    4. Generate images with FAL.ai
    """
    print(f"[Video] Generating {num_images} contextual images...")
    
    # Step 0: Extract what the story is LITERALLY about
    subjects = extract_story_subjects(title, script)
    
    # Step 1: AI selects the best visual style for this content
    style = select_style_with_groq(title, script)
    
    # Step 2: Get AI-generated prompts anchored to subjects + style
    prompts = generate_image_prompts_with_groq(title, script, num_images, style=style, subjects=subjects)
    
    # Fallback if Groq fails - use subject keywords
    if not prompts:
        print("[Video] Using fallback prompts with subject anchoring")
        keywords = subjects.get('visual_keywords', [title])
        setting = subjects.get('setting', '')
        prompts = [f"{kw}, {setting}, {style}" for kw in (keywords * 3)[:num_images]]
    
    # Step 3: Generate images using FAL.ai with contextual prompts
    images = []
    for i, prompt in enumerate(prompts):
        print(f"[Video] Image {i+1}/{num_images}: {prompt[:50]}...")
        img = generate_image_fal(prompt)
        images.append(img)
    
    print(f"[Video] ✅ Generated {len(images)} images about '{subjects.get('main_subject', title)}'")
    return images


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
    darkened = darken_image(image, factor=IMAGE_DARKEN_FACTOR)
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
        
        # Clean script to remove [HOOK], [BIG IDEA], etc. tags before TTS
        clean_script = clean_script_for_tts(script)
        print(f"[Video] Script cleaned: removed structural tags")
        
        print("[Video] Step 1: Generating voiceover...")
        actual_audio_path = generate_tts_kokoro(clean_script, str(temp_audio_path))
        
        # Load audio to get duration
        audio = AudioFileClip(actual_audio_path)
        audio_duration = audio.duration
        print(f"[Video] Audio duration: {audio_duration:.1f}s")
        
        # Step 2: Pre-generate themed images based on article topic
        print("[Video] Step 2: Pre-generating themed images...")
        themed_images = generate_themed_images(title, script, num_images=10)
        
        # Step 3: Chunk text for TikTok pacing
        print("[Video] Step 3: Chunking text for visual pacing...")
        chunks = chunk_text_for_tiktok(script, words_per_chunk=DEFAULT_WORDS_PER_CHUNK)
        print(f"[Video] Created {len(chunks)} visual segments")
        
        # Calculate time per chunk
        time_per_chunk = audio_duration / len(chunks) if chunks else DEFAULT_CHUNK_DURATION
        time_per_chunk = max(MIN_CHUNK_DURATION, min(MAX_CHUNK_DURATION, time_per_chunk))
        
        # Step 4: Create video clips using themed images
        print(f"[Video] Step 4: Creating {len(chunks)} clips with themed images...")
        
        clips = []
        for i in range(len(chunks)):
            # Cycle through themed images evenly
            image_index = i % len(themed_images)
            current_image = themed_images[image_index]
            
            clip = create_clip_with_broll(current_image, time_per_chunk)
            clips.append(clip)
        
        # Step 5: Concatenate all clips
        print("[Video] Step 5: Assembling video...")
        main_video = concatenate_videoclips(clips)
        
        # Adjust video to match audio duration
        if main_video.duration != audio_duration:
            if main_video.duration > audio_duration:
                main_video = main_video.subclip(0, audio_duration)
            # If video is shorter, the last frame will hold
        
        # Add audio
        main_video = main_video.set_audio(audio)
        
        # Step 6: Render final video
        print("[Video] Step 6: Rendering final video...")
        try:
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

            print(f"\n{'='*50}")
            print(f"[Video] ✅ SUCCESS! Video saved to: {output_path}")
            print(f"{'='*50}\n")

            return str(output_path)

        finally:
            # Always cleanup resources, even if rendering fails
            try:
                audio.close()
            except Exception as e:
                print(f"[Video] Warning: Failed to close audio: {e}")

            try:
                main_video.close()
            except Exception as e:
                print(f"[Video] Warning: Failed to close main video: {e}")

            for clip in clips:
                try:
                    clip.close()
                except Exception as e:
                    print(f"[Video] Warning: Failed to close clip: {e}")

            # Cleanup temp audio file
            try:
                Path(actual_audio_path).unlink()
            except Exception as e:
                print(f"[Video] Warning: Failed to delete temp audio: {e}")
        
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
