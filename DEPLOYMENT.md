# Deployment Guide

This guide covers deploying the Article Scraper MVP to production using Docker or traditional hosting.

---

## Prerequisites

- Python 3.11+
- Docker & Docker Compose (for containerized deployment)
- At least 1 AI API key (OpenRouter, Groq, Mistral, or Gemini)
- FAL.ai API key (optional, for video B-roll generation)

---

## Quick Start (Development)

### 1. Clone and Setup

```bash
git clone <your-repo-url>
cd scap_web
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

### 4. Run Development Server

```bash
python app.py
```

Visit http://localhost:5050

---

## Production Deployment

### Option 1: Docker Deployment (Recommended)

#### Build and Run

```bash
# Build the image
docker build -t article-scraper:latest .

# Run with Docker Compose
docker-compose up -d
```

#### Configuration

Edit `docker-compose.yml` or create `.env` file:

```env
# Required: At least ONE summarization API
OPENROUTER_API_KEY=your_key_here
GROQ_API_KEY=your_key_here
MISTRAL_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here

# Optional: Video B-roll images
FAL_KEY=your_fal_key_here

# Production CORS
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

# Database (uses SQLite by default)
DATABASE_URI=sqlite:////app/instance/database.db
```

#### Health Checks

```bash
# Check container health
docker ps

# View logs
docker-compose logs -f

# Test health endpoint
curl http://localhost:5050/api/health
```

---

### Option 2: Traditional Deployment

#### Using Gunicorn (Production WSGI Server)

```bash
# Install dependencies
pip install -r requirements.txt

# Run with Gunicorn
gunicorn -c gunicorn.conf.py wsgi:app
```

#### Using Systemd (Linux)

Create `/etc/systemd/system/article-scraper.service`:

```ini
[Unit]
Description=Article Scraper MVP
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/article-scraper
Environment="PATH=/var/www/article-scraper/.venv/bin"
ExecStart=/var/www/article-scraper/.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable article-scraper
sudo systemctl start article-scraper
sudo systemctl status article-scraper
```

---

## Security Checklist

Before deploying to production:

- [ ] **Set CORS_ORIGINS** to your actual domains (not `*`)
- [ ] **Use HTTPS** - Never run production over HTTP
- [ ] **Rotate API keys** - Use production-specific keys
- [ ] **Set strong database password** (if using PostgreSQL/MySQL)
- [ ] **Enable firewall** - Only expose necessary ports
- [ ] **Set up monitoring** - Use tools like Sentry, DataDog, etc.
- [ ] **Configure backups** - Regular database and video backups
- [ ] **Review logs** - Set up centralized logging
- [ ] **Rate limiting** - Consider adding Nginx/CloudFlare rate limiting
- [ ] **Update dependencies** - Run `pip-audit` or `safety check`

---

## Reverse Proxy Setup (Nginx)

### Nginx Configuration

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Proxy to application
    location / {
        proxy_pass http://localhost:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for long-running video generation
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
    }

    # Serve static files directly
    location /static/ {
        alias /var/www/article-scraper/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Serve videos with longer timeout
    location /videos/ {
        alias /var/www/article-scraper/static/videos/;
        expires 1d;
        add_header Cache-Control "public";
    }
}
```

---

## Monitoring & Logging

### Application Logs

Logs are written to stdout by default. Configure log aggregation:

```python
# In app.py, update logging config:
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/article-scraper/app.log'),
        logging.StreamHandler()
    ]
)
```

### Metrics to Monitor

- **API Response Times** - Track `/api/summarize` and `/api/video` endpoints
- **Error Rates** - 4xx and 5xx responses
- **Queue Depth** - Number of pending video generation jobs
- **Disk Usage** - Monitor `static/videos/` directory
- **API Quota** - Track usage of AI APIs (Groq, FAL.ai, etc.)

### Health Check Endpoint

```bash
# Application health
curl http://localhost:5050/api/health

# Expected response:
# {"status": "healthy", "timestamp": "2026-01-01T00:00:00"}
```

---

## Database Migrations

### SQLite (Default)

Backup before updates:

```bash
# Backup database
cp instance/database.db instance/database.db.backup

# If schema changes, may need to recreate
python -c "from app import app, db; \
    with app.app_context(): db.create_all()"
```

### PostgreSQL (Production Alternative)

Update `.env`:

```env
DATABASE_URI=postgresql://user:password@localhost/article_scraper
```

Install driver:

```bash
pip install psycopg2-binary
```

---

## Scaling Considerations

### Horizontal Scaling

For high traffic, consider:

1. **Separate video generation** - Move to background queue (Celery + Redis)
2. **Load balancer** - Use Nginx/HAProxy for multiple app instances
3. **CDN for videos** - Offload static video serving to S3 + CloudFront
4. **Database** - Migrate from SQLite to PostgreSQL

### Background Jobs

Example Celery setup (optional):

```python
# celery_app.py
from celery import Celery

celery = Celery('article_scraper')
celery.config_from_object('celeryconfig')

@celery.task
def generate_video_async(article_id, title, script):
    from video_generator import generate_video
    return generate_video(article_id, title, script)
```

---

## Troubleshooting

### Issue: Video generation fails

**Solution:**
1. Check FAL_KEY is set correctly
2. Verify MoviePy/ffmpeg installation
3. Check disk space in `static/videos/`
4. Review logs for specific errors

### Issue: Summarization fails

**Solution:**
1. Verify at least one API key is configured
2. Check API quota/rate limits
3. Test each API independently
4. Review logs for API-specific errors

### Issue: CORS errors in production

**Solution:**
1. Verify `CORS_ORIGINS` includes your frontend domain
2. Check for trailing slashes (https://app.com vs https://app.com/)
3. Ensure HTTPS is used (mixed content blocks requests)

### Issue: High memory usage

**Solution:**
1. Video generation is memory-intensive
2. Limit concurrent video jobs
3. Increase server RAM or use job queue
4. Clean up old videos regularly

---

## Rollback Procedure

If deployment fails:

```bash
# Docker
docker-compose down
docker-compose up -d --force-recreate

# Systemd
sudo systemctl stop article-scraper
# Restore previous version
sudo systemctl start article-scraper

# Database
cp instance/database.db.backup instance/database.db
```

---

## Support & Maintenance

### Regular Tasks

- **Weekly:** Review logs for errors
- **Monthly:** Update dependencies, rotate API keys
- **Quarterly:** Review and clean old videos, database vacuum

### Updating Dependencies

```bash
# Check for security updates
pip install pip-audit
pip-audit

# Update dependencies
pip install -U -r requirements.txt

# Run tests
pytest tests/

# If tests pass, deploy
```

---

## Production Checklist Summary

✅ **Before Deployment:**
- [ ] All tests passing (`pytest tests/`)
- [ ] Environment variables configured
- [ ] CORS whitelist set
- [ ] SSL certificates installed
- [ ] Firewall configured
- [ ] Monitoring setup
- [ ] Backup strategy defined

✅ **After Deployment:**
- [ ] Health check passes
- [ ] Can create article
- [ ] Can generate summary
- [ ] Can generate video
- [ ] CORS works from frontend
- [ ] Logs are collecting
- [ ] Monitoring alerts configured

---

For issues or questions, see [SECURITY_IMPROVEMENTS.md](SECURITY_IMPROVEMENTS.md) for security details.
