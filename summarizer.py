"""AI Summarization using OpenRouter (primary), Groq, and Gemini fallbacks."""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()


# Shared prompt template
def get_prompt(title: str, content: str) -> str:
    return f"""You are a scriptwriter. Write a COMPLETE 60-90 second video script in plain adult English (15+), based ONLY on the article text.

Also generate a TL;DR, key bullets, and 5 hashtags for social media.

=== TIME + LENGTH ===
- Target length: 75 seconds (acceptable 60-90 seconds).
- Word count: 160-210 words.
- Short sentences. No filler.

=== STRUCTURE (exact) ===
1) [HOOK] (1 sentence) - Punchy opener
2) [BIG IDEA] (2-3 sentences) - Main concept
3) [WORKS] (3-5 fast bullet-like lines) - The "what works" list
4) [CAVEAT] (1-2 sentences) - One uncertainty or limitation
5) [CLOSE] (1 sentence) - Memorable takeaway

=== COMPLETENESS RULE (must follow) ===
- Identify the article's "must-include" major points (max 8).
- Your script MUST mention ALL of them, even if briefly.
- If you can't fit all 8 into 210 words, compress, don't delete.

=== EVIDENCE DISCIPLINE ===
- If something is correlation/observational, say "linked to" or "associated with."
- If it's speculation, label it "still speculative."

=== DELIVERY ===
- No jargon. If you must use a technical term, define it in 6-10 words immediately.

=== HASHTAGS ===
- Generate exactly 5 hashtags relevant to the article topic.
- Each hashtag must start with # and be a single word or CamelCase compound (e.g., #ArtificialIntelligence).
- Make them relevant for social media engagement (TikTok, Instagram, Twitter).

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
    "video_script": "Your script with [HOOK] [BIG IDEA] [WORKS] [CAVEAT] [CLOSE] labels",
    "hashtags": [
        "#Hashtag1",
        "#Hashtag2",
        "#Hashtag3",
        "#Hashtag4",
        "#Hashtag5"
    ],
    "coverage_checklist": [
        "Point 1 covered",
        "Point 2 covered",
        "Point 3 covered"
    ]
}}
"""


def parse_response(text: str) -> dict:
    """Parse and clean up AI response to extract JSON."""
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
        'video_script': result.get('video_script', ''),
        'hashtags': result.get('hashtags', [])
    }


def summarize_with_openrouter(title: str, content: str) -> dict:
    """Generate summary using OpenRouter API (access to many models)."""
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found in environment variables")
    
    prompt = get_prompt(title, content)
    
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5050",
            "X-Title": "Article Scraper"
        },
        json={
            "model": "meta-llama/llama-3.3-70b-instruct",  # Great free model
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that responds only in valid JSON format."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        },
        timeout=60
    )
    
    response.raise_for_status()
    data = response.json()
    
    if 'error' in data:
        raise Exception(data['error'].get('message', 'Unknown OpenRouter error'))
    
    text = data['choices'][0]['message']['content'].strip()
    return parse_response(text)


def summarize_with_groq(title: str, content: str) -> dict:
    """Generate summary using Groq API (fast & generous free tier)."""
    from groq import Groq
    
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment variables")
    
    client = Groq(api_key=api_key)
    prompt = get_prompt(title, content)
    
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
    return parse_response(text)


def summarize_with_mistral(title: str, content: str) -> dict:
    """Generate summary using Mistral API."""
    api_key = os.getenv('MISTRAL_API_KEY')
    if not api_key:
        raise ValueError("MISTRAL_API_KEY not found in environment variables")
    
    prompt = get_prompt(title, content)
    
    response = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": "mistral-large-latest",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that responds only in valid JSON format."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        },
        timeout=60
    )
    
    response.raise_for_status()
    data = response.json()
    
    if 'error' in data:
        raise Exception(data['error'].get('message', 'Unknown Mistral error'))
    
    text = data['choices'][0]['message']['content'].strip()
    return parse_response(text)

def summarize_with_gemini(title: str, content: str) -> dict:
    """Fallback: Generate summary using Google Gemini."""
    import google.generativeai as genai
    
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = get_prompt(title, content)
    
    response = model.generate_content(prompt)
    text = response.text.strip()
    return parse_response(text)


def summarize_article(title: str, content: str) -> dict:
    """
    Generate TL;DR, key bullets, and video script from article content.
    Uses OpenRouter → Groq → Mistral → Gemini fallback chain.
    
    Returns:
        dict with keys: tldr, bullets (list), video_script
    """
    errors = {}
    
    # Try OpenRouter first (most models, reliable)
    try:
        print("[Summarizer] Trying OpenRouter...")
        return summarize_with_openrouter(title, content)
    except Exception as e:
        errors['openrouter'] = str(e)
        print(f"[Summarizer] OpenRouter failed: {e}")
    
    # Try Groq second (fast, generous free tier)
    try:
        print("[Summarizer] Trying Groq...")
        return summarize_with_groq(title, content)
    except Exception as e:
        errors['groq'] = str(e)
        print(f"[Summarizer] Groq failed: {e}")
    
    # Try Mistral third
    try:
        print("[Summarizer] Trying Mistral...")
        return summarize_with_mistral(title, content)
    except Exception as e:
        errors['mistral'] = str(e)
        print(f"[Summarizer] Mistral failed: {e}")
    
    # Fallback to Gemini
    try:
        print("[Summarizer] Falling back to Gemini...")
        return summarize_with_gemini(title, content)
    except Exception as e:
        errors['gemini'] = str(e)
        print(f"[Summarizer] Gemini also failed: {e}")
        raise Exception(f"All AI providers failed: {errors}")


if __name__ == '__main__':
    # Test the summarizer
    test_result = summarize_article(
        "Test Article",
        "This is a test article about artificial intelligence and its impact on society. "
        "AI is transforming how we work, live, and interact. Machine learning models are "
        "becoming more sophisticated every day."
    )
    print(json.dumps(test_result, indent=2))
