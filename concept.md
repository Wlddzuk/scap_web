```markdown
# CONCEPT.md

## Concept
A browser-first pipeline that turns your website articles into reusable content assets:

1) **Scrape from your browser** (exact rendered page, not server-side scraping)
2) **Summarize** each article into consistent formats
3) **Publish to Google Docs** automatically
4) **Generate videos** only for the articles you choose

The key idea: the system captures *what you see in your browser* (handles JS-rendered pages, logged-in views, dynamic content, etc.), then the backend does the heavy lifting (LLM summary, Google Docs, video rendering).

---

## Why this exists
You have many articles. Copy/paste and manual repurposing is slow and inconsistent.

This system:
- catches articles you want to repurpose,
- creates a clean knowledge archive in Google Docs,
- gives you a fast “turn article into video” workflow.

---

## Users
- **Admin (you)**: scrapes articles, reviews summaries/scripts, triggers video generation.

(Optional later)
- **Editor**: tweaks scripts, approves videos.

---

## Core workflow (MVP)
### 1) Scrape (Browser Extension)
- You open an article page.
- Click **“Scrape this article”**.
- Extension extracts:
  - canonical URL
  - title
  - main cleaned text (headings, paragraphs, lists)
  - images (hero + top inline image URLs)
  - metadata (site, timestamp, language guess)
- Sends payload to backend API.

### 2) Summarize (Backend Worker)
Backend generates:
- **TL;DR** (2–3 sentences)
- **Key bullets** (5–8)
- **Video script draft** (short-form 45–90s)
- (Optional) 3 hook/title variants

### 3) Google Docs (Backend Worker)
Creates a Google Doc per article with a fixed template:
- Title
- Source URL
- TL;DR
- Key bullets
- Video script
- Full extracted text + metadata

### 4) Choose → Video (Manual Trigger)
In a simple dashboard you select articles and click **Generate Video**.

Video output (MVP):
- Template-based render (reliable)
- TTS voiceover + subtitles
- 9:16 short format by default
- MP4 stored in a configured folder (Drive/S3)

---

## Product surfaces
### Browser Extension
- “Scrape this article” button
- Status toast (sent / failed)
- Basic settings: API base URL, token, domain allowlist

### Admin Dashboard (Web)
- Article list + status:
  - scraped ✅
  - summarized ✅
  - doc created ✅ (link)
  - video: not started / rendering / done / failed
- Actions:
  - view details
  - re-run summary
  - re-create doc
  - generate video

---

## Key principles
- **Browser is the scraper.** The backend never needs to “figure out” the final rendered page.
- **Everything is a job with a status.** Scrape, summarize, doc, and video are separate steps.
- **Human stays in control of videos.** You choose what becomes a video.
- **Auditability.** Save extracted text + summary outputs + doc/video URLs for traceability.

---

## MVP boundaries
In scope:
- Chrome/Edge extension scraping
- summarization outputs + script draft
- Google Docs creation (single account)
- manual video generation from a consistent template
- statuses + retries

Out of scope (initially):
- auto-upload to social platforms
- multi-user roles/approvals
- fully AI-generated video scenes (unstable)
- multi-language dubbing

---

## Risks and mitigations
- **Site layout changes break extraction**
  - Mitigation: readability fallback + per-domain rules
- **OAuth complexity for Google Docs**
  - Mitigation: single Google account first, minimal scopes, store tokens securely
- **Video rendering reliability**
  - Mitigation: strict templates, limited scene count, deterministic timing
- **Costs**
  - Mitigation: daily caps, only generate video manually, caching/dedup by URL + hash

---

## Future extensions (Phase 2+)
- Auto-detect new URLs from sitemap/RSS and show “not yet scraped”
- Tag-based control from Google Docs (e.g., `#make-video`)
- Multiple video templates (YouTube 16:9, carousel, long-form)
- Auto-publish + analytics feedback loop
- Multi-language summaries and voiceover

---

## MVP success criteria
- One click in-browser → article appears in dashboard
- Summary + Google Doc produced automatically
- Selected articles can generate MP4 videos reliably
- Failures are visible and recoverable via re-run
```
