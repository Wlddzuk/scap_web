# Clipper - Article to TikTok Video Generator + Discord Bot
# Optimized for Oracle Cloud Free Tier (ARM64)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (ffmpeg for video, espeak for TTS fallback)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p instance static/videos static/carousels

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5050/api/health', timeout=5)"

# Run the Discord bot (which also starts Flask in background).
# For a Flask-only deployment use: gunicorn -c gunicorn.conf.py wsgi:app
CMD ["python", "discord_bot.py"]
