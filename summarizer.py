"""AI Summarization using Groq (primary) with Gemini fallback."""

import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


def summarize_with_groq(title: str, content: str) -> dict:
    """Generate summary using Groq API (fast & generous free tier)."""
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment variables")
    
    client = Groq(api_key=api_key)
    
    prompt = f"""You are a content summarizer. Given the following article, generate:

1. **TL;DR**: A concise 2-3 sentence summary capturing the main point.

2. **Key Bullets**: 5-8 bullet points with the most important takeaways.

3. **Video Script**: A short-form video script (45-90 seconds when read aloud) that:
   - Opens with a hook
   - Covers the main points engagingly
   - Ends with a takeaway or call-to-action
   - Uses conversational, punchy language suitable for TikTok/Reels/Shorts

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content[:8000]}

Respond in this exact JSON format (no markdown, just raw JSON):
{{
    "tldr": "Your 2-3 sentence summary here",
    "bullets": [
        "First key point",
        "Second key point",
        "Third key point",
        "Fourth key point",
        "Fifth key point"
    ],
    "video_script": "Your 45-90 second video script here. Write it as flowing speech, not bullet points."
}}
"""
    
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that responds only in valid JSON format."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=2000
    )
    
    text = response.choices[0].message.content.strip()
    
    # Clean up response text (remove markdown code blocks if present)
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    text = text.strip()
    
    result = json.loads(text)
    return {
        'tldr': result.get('tldr', ''),
        'bullets': result.get('bullets', []),
        'video_script': result.get('video_script', '')
    }


def summarize_with_gemini(title: str, content: str) -> dict:
    """Fallback: Generate summary using Google Gemini."""
    import google.generativeai as genai
    
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    prompt = f"""You are a content summarizer. Given the following article, generate:

1. **TL;DR**: A concise 2-3 sentence summary capturing the main point.

2. **Key Bullets**: 5-8 bullet points with the most important takeaways.

3. **Video Script**: A short-form video script (45-90 seconds when read aloud) that:
   - Opens with a hook
   - Covers the main points engagingly
   - Ends with a takeaway or call-to-action
   - Uses conversational, punchy language suitable for TikTok/Reels/Shorts

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content[:8000]}

Respond in this exact JSON format (no markdown, just raw JSON):
{{
    "tldr": "Your 2-3 sentence summary here",
    "bullets": [
        "First key point",
        "Second key point",
        "Third key point",
        "Fourth key point",
        "Fifth key point"
    ],
    "video_script": "Your 45-90 second video script here. Write it as flowing speech, not bullet points."
}}
"""
    
    response = model.generate_content(prompt)
    text = response.text.strip()
    
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    text = text.strip()
    
    result = json.loads(text)
    return {
        'tldr': result.get('tldr', ''),
        'bullets': result.get('bullets', []),
        'video_script': result.get('video_script', '')
    }


def summarize_article(title: str, content: str) -> dict:
    """
    Generate TL;DR, key bullets, and video script from article content.
    Uses Groq as primary, falls back to Gemini if Groq fails.
    
    Returns:
        dict with keys: tldr, bullets (list), video_script
    """
    # Try Groq first (faster, more generous free tier)
    try:
        print("[Summarizer] Trying Groq...")
        return summarize_with_groq(title, content)
    except Exception as groq_error:
        print(f"[Summarizer] Groq failed: {groq_error}")
    
    # Fallback to Gemini
    try:
        print("[Summarizer] Falling back to Gemini...")
        return summarize_with_gemini(title, content)
    except Exception as gemini_error:
        print(f"[Summarizer] Gemini also failed: {gemini_error}")
        raise Exception(f"All AI providers failed. Groq: {groq_error}, Gemini: {gemini_error}")


if __name__ == '__main__':
    # Test the summarizer
    test_result = summarize_article(
        "Test Article",
        "This is a test article about artificial intelligence and its impact on society. "
        "AI is transforming how we work, live, and interact. Machine learning models are "
        "becoming more sophisticated every day."
    )
    print(json.dumps(test_result, indent=2))
