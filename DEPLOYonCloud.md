# Deploy to Cloud ☁️

Deploy the Article Scraper MVP so you can generate videos from your phone or anywhere.

---

## 🚀 Recommended: Railway (Easiest)

Railway offers free tier and auto-deploys from GitHub.

### Step 1: Prepare Your Repo
Make sure your GitHub repo is up to date:
```bash
git add .
git commit -m "Prepare for cloud deployment"
git push
```

### Step 2: Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub

### Step 3: Deploy
1. Click **"New Project"**
2. Select **"Deploy from GitHub repo"**
3. Choose `Wlddzuk/scap_web`
4. Railway auto-detects Python and deploys!

### Step 4: Add Environment Variables
In Railway dashboard → Your project → **Variables** tab:

```
OPENROUTER_API_KEY=your_key_here
GROQ_API_KEY=your_key_here
MISTRAL_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
FAL_KEY=your_key_here
```

### Step 5: Get Your URL
Railway gives you a URL like: `https://scap-web-production.up.railway.app`

Open it on your phone! 📱

---

## 🔧 Alternative: Render

### Step 1: Create Render Account
1. Go to [render.com](https://render.com)
2. Sign up with GitHub

### Step 2: Create Web Service
1. Click **"New" → "Web Service"**
2. Connect your GitHub repo
3. Configure:
   - **Name:** `scap-web`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -c gunicorn.conf.py wsgi:app`

### Step 3: Add Environment Variables
Same as Railway (add all your API keys)

### Step 4: Deploy
Click **"Create Web Service"** — Render builds and deploys automatically.

---

## ⚠️ Important Notes

### System Dependencies
Video generation requires **FFmpeg** and **ImageMagick**. 

For Railway/Render, add a `nixpacks.toml` file:
```toml
[phases.setup]
nixPkgs = ["ffmpeg", "imagemagick"]
```

Or add an `apt.txt` file for Render:
```
ffmpeg
imagemagick
```

### Database
The SQLite database works for testing but **won't persist** on some platforms.
For production, consider:
- Railway PostgreSQL (one-click add-on)
- Render PostgreSQL (free tier available)

### Large File Warning
Video generation creates large files. Free tiers have storage limits.
Consider adding cloud storage (S3, Cloudflare R2) for videos in production.

---

## 📱 Access From Phone

Once deployed, just open the URL in your phone's browser:
1. Safari/Chrome → Your Railway/Render URL
2. Bookmark it for quick access
3. Add to Home Screen for app-like experience

---

## 🔄 Auto-Deploy Updates

Both Railway and Render auto-deploy when you push to GitHub:
```bash
git add .
git commit -m "Your changes"
git push
```

Your cloud app updates automatically! ✨
