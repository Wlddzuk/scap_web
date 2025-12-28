"""AI Summarization using Google Gemini."""

import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()


def configure_gemini():
    """Configure Gemini API with key from environment."""
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")
    genai.configure(api_key=api_key)


def summarize_article(title: str, content: str) -> dict:
    """
    Generate TL;DR, key bullets, and video script from article content.
    
    Returns:
        dict with keys: tldr, bullets (list), video_script
    """
    configure_gemini()
    
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
    
    # Parse the JSON response
    try:
        # Clean up response text (remove markdown code blocks if present)
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
    except json.JSONDecodeError as e:
        # Fallback: return raw text as tldr if JSON parsing fails
        return {
            'tldr': response.text[:500],
            'bullets': ['Could not parse structured response'],
            'video_script': response.text
        }


if __name__ == '__main__':
    # Test the summarizer
    test_result = summarize_article(
        "Test Article",
        "This is a test article about artificial intelligence and its impact on society. "
        "AI is transforming how we work, live, and interact. Machine learning models are "
        "becoming more sophisticated every day."
    )
    print(json.dumps(test_result, indent=2))
