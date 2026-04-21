/**
 * Clipper Dashboard - Frontend JavaScript
 */

const API_BASE = '';

// ============================================
// State
// ============================================

let articles = [];
let expandedArticles = new Set();
let searchQuery = '';
let initialLoadDone = false;
let availableStyles = [];       // loaded from /api/styles
let selectedStyleByArticle = {}; // { [articleId]: 'manga' } — user override

// ============================================
// API Functions
// ============================================

async function fetchArticles() {
    try {
        const response = await fetch(`${API_BASE}/api/articles`);
        const data = await response.json();
        const prevArticles = articles;
        articles = data.articles;

        // Hide loading skeleton after first load
        if (!initialLoadDone) {
            initialLoadDone = true;
            document.getElementById('loading-skeleton').style.display = 'none';
        }

        // Smart render: only full re-render when data actually changed
        if (articlesChanged(prevArticles, articles)) {
            renderArticles();
        }

        updateStats();
        updateProgressBanner();
    } catch (error) {
        console.error('Error fetching articles:', error);
        if (!initialLoadDone) {
            initialLoadDone = true;
            document.getElementById('loading-skeleton').style.display = 'none';
        }
        showToast('Failed to load articles', 'error');
    }
}

function articlesChanged(prev, next) {
    if (prev.length !== next.length) return true;
    for (let i = 0; i < prev.length; i++) {
        if (prev[i].id !== next[i].id ||
            prev[i].status !== next[i].status ||
            prev[i].video_path !== next[i].video_path ||
            prev[i].tldr !== next[i].tldr) {
            return true;
        }
    }
    return false;
}

async function scrapeUrl(event) {
    event.preventDefault();

    const urlInput = document.getElementById('url-input');
    const scrapeBtn = document.getElementById('scrape-btn');
    const url = urlInput.value.trim();

    if (!url) return;

    scrapeBtn.disabled = true;
    scrapeBtn.textContent = 'Scraping...';
    showToast('Fetching article...', 'info');

    try {
        const response = await fetch(`${API_BASE}/api/scrape-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Article scraped successfully!', 'success');
            urlInput.value = '';
            await fetchArticles();
            if (data.article && data.article.id) {
                expandedArticles.add(data.article.id);
                renderArticles();
            }
        } else {
            showToast(data.error || 'Failed to scrape article', 'error');
        }
    } catch (error) {
        console.error('Error scraping URL:', error);
        showToast('Failed to scrape article', 'error');
    } finally {
        scrapeBtn.disabled = false;
        scrapeBtn.textContent = 'Scrape';
    }
}

async function summarizeArticle(articleId) {
    const btn = document.querySelector(`[data-summarize="${articleId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Summarizing...';
    }

    showToast('Generating AI summary...', 'info');

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}/summarize`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Summary generated!', 'success');
            await fetchArticles();
            expandedArticles.add(articleId);
            renderArticles();
        } else {
            showToast(data.error || 'Failed to summarize', 'error');
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Summarize';
            }
        }
    } catch (error) {
        console.error('Error summarizing article:', error);
        showToast('Failed to summarize article', 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Summarize';
        }
    }
}

async function loadStyles() {
    try {
        const res = await fetch(`${API_BASE}/api/styles`);
        const data = await res.json();
        availableStyles = data.styles || [];
    } catch (err) {
        console.warn('Failed to load styles', err);
    }
}

function selectStyle(articleId, styleKey) {
    selectedStyleByArticle[articleId] = styleKey;
    // Update chip highlighting in place (no full re-render needed)
    const container = document.querySelector(`.style-picker[data-article-id="${articleId}"]`);
    if (container) {
        container.querySelectorAll('.style-chip').forEach(chip => {
            chip.classList.toggle('selected', chip.dataset.styleKey === styleKey);
        });
    }
}

async function generateVideo(articleId) {
    const btn = document.querySelector(`[data-video="${articleId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Generating...';
    }

    const article = articles.find(a => a.id === articleId);
    const chosenStyle = selectedStyleByArticle[articleId] || (article && article.style) || null;

    showToast(`Generating video${chosenStyle ? ' (' + chosenStyle + ')' : ''} — this may take a few minutes`, 'info');

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}/video`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(chosenStyle ? { style: chosenStyle } : {})
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Video generated!', 'success');
            await fetchArticles();
            expandedArticles.add(articleId);
            renderArticles();
        } else {
            showToast(data.error || 'Failed to generate video', 'error');
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Generate Video';
            }
        }
    } catch (error) {
        console.error('Error generating video:', error);
        showToast('Failed to generate video', 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Generate Video';
        }
    }
}

async function deleteArticle(articleId) {
    if (!confirm('Delete this article and its generated content?')) return;

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            expandedArticles.delete(articleId);
            showToast('Article deleted', 'success');
            await fetchArticles();
        } else {
            showToast('Failed to delete article', 'error');
        }
    } catch (error) {
        console.error('Error deleting article:', error);
        showToast('Failed to delete article', 'error');
    }
}

// ============================================
// Search / Filter
// ============================================

function filterArticles() {
    searchQuery = document.getElementById('search-input').value.trim().toLowerCase();
    renderArticles();
}

function getFilteredArticles() {
    if (!searchQuery) return articles;
    return articles.filter(a =>
        a.title.toLowerCase().includes(searchQuery) ||
        (a.site_name && a.site_name.toLowerCase().includes(searchQuery)) ||
        (a.tldr && a.tldr.toLowerCase().includes(searchQuery))
    );
}

// ============================================
// Progress Banner
// ============================================

function updateProgressBanner() {
    const banner = document.getElementById('progress-banner');
    const text = document.getElementById('progress-text');
    const processing = articles.filter(a => ['summarizing', 'generating_video'].includes(a.status));

    if (processing.length > 0) {
        const names = processing.map(a => {
            const label = a.status === 'summarizing' ? 'Summarizing' : 'Generating video for';
            const short = a.title.length > 40 ? a.title.slice(0, 40) + '...' : a.title;
            return `${label}: ${short}`;
        });
        text.textContent = names.join(' | ');
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

// ============================================
// Render Functions
// ============================================

function renderArticles() {
    const container = document.getElementById('articles-container');
    const emptyState = document.getElementById('empty-state');
    const searchBar = document.getElementById('search-bar');
    const sectionHeader = document.getElementById('section-header');
    const sectionTitle = document.getElementById('section-title');
    const scraperForm = document.querySelector('.url-scraper-form');

    // Collapse the hero once the user has articles — full pitch only matters on the empty dashboard.
    if (scraperForm) {
        scraperForm.classList.toggle('compact', articles.length > 0);
    }

    if (articles.length === 0) {
        container.classList.add('hidden');
        searchBar.classList.add('hidden');
        sectionHeader.classList.add('hidden');
        emptyState.classList.remove('hidden');
        return;
    }

    emptyState.classList.add('hidden');
    container.classList.remove('hidden');

    // Show search bar when 3+ articles
    if (articles.length >= 3) {
        searchBar.classList.remove('hidden');
    } else {
        searchBar.classList.add('hidden');
    }

    const filtered = getFilteredArticles();

    // Update section header
    sectionHeader.classList.remove('hidden');
    if (searchQuery && filtered.length !== articles.length) {
        sectionTitle.textContent = `${filtered.length} of ${articles.length} articles`;
    } else {
        sectionTitle.textContent = `${articles.length} article${articles.length !== 1 ? 's' : ''}`;
    }

    const shouldAnimate = container.childElementCount !== filtered.length;

    container.innerHTML = filtered.map(article => renderArticleCard(article)).join('');

    // Add click handlers for expanding cards
    container.querySelectorAll('.article-header').forEach(header => {
        header.addEventListener('click', (e) => {
            if (e.target.closest('button') || e.target.closest('a')) return;
            const card = header.closest('.article-card');
            const articleId = parseInt(card.dataset.articleId);
            toggleExpand(articleId, card);
        });
    });

    // Staggered entry animation (only on content change)
    if (shouldAnimate && window.anime) {
        anime({
            targets: '.article-card',
            translateY: [20, 0],
            opacity: [0, 1],
            delay: anime.stagger(80),
            easing: 'spring(1, 80, 10, 0)'
        });
    }
}

function renderArticleCard(article) {
    const isExpanded = expandedArticles.has(article.id);
    const statusBadges = getStatusBadges(article);
    const formattedDate = new Date(article.scraped_at).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });

    return `
        <div class="article-card ${isExpanded ? 'expanded' : ''}" data-article-id="${article.id}">
            <div class="article-header">
                <div class="article-info">
                    <h3 class="article-title">${escapeHtml(article.title)}</h3>
                    <div class="article-meta">
                        <span>${formattedDate}</span>
                        ${article.site_name ? `<span>${escapeHtml(article.site_name)}</span>` : ''}
                        <a href="${escapeHtml(article.url)}" target="_blank" rel="noopener">View Original</a>
                    </div>
                </div>
                <div class="article-status">
                    ${statusBadges}
                    <span class="expand-chevron">${isExpanded ? '&#9650;' : '&#9660;'}</span>
                </div>
            </div>

            <div class="article-content">
                ${renderSummary(article)}
                ${renderActions(article)}
            </div>
        </div>
    `;
}

function getStatusBadges(article) {
    // Single current-state pill. Priority: failed > processing > completed > scraped.
    if (article.status === 'failed') {
        return '<span class="badge badge-failed">Failed</span>';
    }
    if (article.status === 'generating_video') {
        return '<span class="badge badge-processing">Generating Video</span>';
    }
    if (article.status === 'summarizing') {
        return '<span class="badge badge-processing">Summarizing</span>';
    }
    if (article.video_path) {
        return '<span class="badge badge-video">Video Ready</span>';
    }
    if (article.tldr) {
        return '<span class="badge badge-summarized">Summarized</span>';
    }
    return '<span class="badge badge-scraped">Scraped</span>';
}

function renderStylePicker(article) {
    if (!availableStyles.length) return '';
    const currentStyle = selectedStyleByArticle[article.id] || article.style || null;
    const suggested = article.style;

    return `
        <div class="summary-section">
            <div class="summary-label">
                Visual Style
                ${article.dominant_emotion ? `<span class="emotion-pill">${escapeHtml(article.dominant_emotion)}</span>` : ''}
            </div>
            <div class="style-picker" data-article-id="${article.id}">
                ${availableStyles.map(s => `
                    <button
                        type="button"
                        class="style-chip ${currentStyle === s.key ? 'selected' : ''}"
                        data-style-key="${s.key}"
                        onclick="event.stopPropagation(); selectStyle(${article.id}, '${s.key}')"
                        title="${escapeHtml(s.description)}${suggested === s.key ? ' (AI suggested)' : ''}"
                    >
                        <span class="style-emoji">${s.emoji}</span>
                        <span class="style-name">${escapeHtml(s.name)}</span>
                        ${suggested === s.key ? '<span class="style-suggested-dot" title="AI suggested"></span>' : ''}
                        <span class="style-palette">
                            ${(s.palette || []).slice(0, 4).map(c => `<i style="background:${c}"></i>`).join('')}
                        </span>
                    </button>
                `).join('')}
            </div>
        </div>
    `;
}

function renderHookVariants(article) {
    const variants = article.hook_variants || [];
    if (variants.length === 0) return '';
    return `
        <div class="summary-section">
            <div class="summary-label">Hook Options (AI wrote ${variants.length})</div>
            <ol class="hook-variants">
                ${variants.map((h, i) => `
                    <li class="hook-variant">
                        <span class="hook-index">${i + 1}</span>
                        <span class="hook-text">${escapeHtml(h)}</span>
                    </li>
                `).join('')}
            </ol>
        </div>
    `;
}

function renderSummary(article) {
    if (!article.tldr) {
        return `
            <div class="summary-section">
                <p class="summary-text" style="color: var(--text-muted);">
                    Click "Summarize" to generate an AI summary, key bullets, and a video script.
                </p>
            </div>
        `;
    }

    const bullets = article.bullets || [];
    const hashtags = article.hashtags || [];

    return `
        <div class="summary-section">
            <div class="summary-label">TL;DR</div>
            <p class="summary-text">${escapeHtml(article.tldr)}</p>
        </div>

        <div class="summary-section">
            <div class="summary-label">Key Points</div>
            <ul class="summary-bullets">
                ${bullets.map(b => `<li>${escapeHtml(b)}</li>`).join('')}
            </ul>
        </div>

        ${renderHookVariants(article)}
        ${renderStylePicker(article)}

        ${article.video_script ? `
            <div class="summary-section">
                <div class="summary-label">Video Script</div>
                <div class="video-script">${escapeHtml(article.video_script)}</div>
            </div>
        ` : ''}

        ${article.video_path ? `
            <div class="video-container">
                <video controls preload="none" playsinline>
                    <source src="/videos/${encodeURIComponent(article.video_path)}" type="video/mp4">
                    Your browser does not support the video tag.
                </video>
            </div>
        ` : ''}

        ${hashtags.length > 0 ? `
            <div class="summary-section hashtags-section">
                <div class="summary-label">
                    Hashtags
                    <button class="copy-hashtags-btn" onclick="copyHashtags(event, ${article.id})" title="Copy all hashtags">
                        Copy All
                    </button>
                </div>
                <div class="hashtags-container" data-hashtags-id="${article.id}">
                    ${hashtags.map(tag => `<span class="hashtag-tag">${escapeHtml(tag)}</span>`).join('')}
                </div>
            </div>
        ` : ''}
    `;
}

function renderActions(article) {
    const canSummarize = article.status !== 'summarizing';
    const canGenerateVideo = article.video_script && article.status !== 'generating_video';
    const isProcessing = ['summarizing', 'generating_video'].includes(article.status);

    return `
        <div class="article-actions">
            <button
                class="btn btn-primary"
                data-summarize="${article.id}"
                onclick="summarizeArticle(${article.id})"
                ${!canSummarize || isProcessing ? 'disabled' : ''}
            >
                ${article.tldr ? 'Re-Summarize' : 'Summarize'}
            </button>

            <button
                class="btn btn-success"
                data-video="${article.id}"
                onclick="generateVideo(${article.id})"
                ${!canGenerateVideo || isProcessing ? 'disabled' : ''}
            >
                ${article.video_path ? 'Regenerate Video' : 'Generate Video'}
            </button>

            <button
                class="btn btn-danger"
                onclick="deleteArticle(${article.id})"
                ${isProcessing ? 'disabled' : ''}
            >
                Delete
            </button>
        </div>
    `;
}

function toggleExpand(articleId, cardElement) {
    if (expandedArticles.has(articleId)) {
        expandedArticles.delete(articleId);
        if (cardElement) {
            const content = cardElement.querySelector('.article-content');
            if (content && window.anime) {
                anime({
                    targets: content,
                    height: 0,
                    opacity: 0,
                    duration: 300,
                    easing: 'easeOutQuad',
                    complete: () => renderArticles()
                });
                return;
            }
        }
    } else {
        expandedArticles.add(articleId);
    }

    renderArticles();

    if (expandedArticles.has(articleId) && window.anime) {
        const newCard = document.querySelector(`.article-card[data-article-id="${articleId}"] .article-content`);
        if (newCard) {
            anime({
                targets: newCard,
                height: ['0px', newCard.scrollHeight + 'px'],
                opacity: [0, 1],
                duration: 600,
                easing: 'easeOutElastic(1, .8)'
            });
        }
    }
}

function updateStats() {
    document.getElementById('total-count').textContent = `${articles.length} article${articles.length !== 1 ? 's' : ''}`;
    const videoCount = articles.filter(a => a.video_path).length;
    document.getElementById('video-count').textContent = `${videoCount} video${videoCount !== 1 ? 's' : ''}`;
}

// ============================================
// Toast Notifications
// ============================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');

    const icons = {
        success: '\u2705',
        error: '\u274C',
        info: '\u2139\uFE0F'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${icons[type]}</span>
        <span class="toast-message">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ============================================
// Utility Functions
// ============================================

function copyHashtags(event, articleId) {
    event.stopPropagation();

    const article = articles.find(a => a.id === articleId);
    if (!article || !article.hashtags) return;

    const hashtagsText = article.hashtags.join(' ');

    navigator.clipboard.writeText(hashtagsText).then(() => {
        showToast('Hashtags copied to clipboard!', 'success');

        const btn = event.target.closest('.copy-hashtags-btn');
        if (btn) {
            const originalText = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(() => {
                btn.textContent = originalText;
            }, 2000);
        }
    }).catch(err => {
        console.error('Failed to copy hashtags:', err);
        showToast('Failed to copy hashtags', 'error');
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// Initialize
// ============================================

async function handleBookmarkletHash() {
    const hash = window.location.hash;
    if (hash.startsWith('#scrape=')) {
        try {
            const encodedData = hash.substring(8);
            const data = JSON.parse(decodeURIComponent(encodedData));

            showToast('Receiving article from bookmarklet...', 'info');

            const response = await fetch(`${API_BASE}/api/scrape`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (response.ok) {
                showToast('Article scraped successfully!', 'success');
                history.replaceState(null, '', window.location.pathname);
                await fetchArticles();
                if (result.article && result.article.id) {
                    expandedArticles.add(result.article.id);
                    renderArticles();
                }
            } else {
                showToast(result.error || 'Failed to scrape article', 'error');
            }
        } catch (error) {
            console.error('Error processing bookmarklet data:', error);
            showToast('Failed to process bookmarklet data', 'error');
        }
        history.replaceState(null, '', window.location.pathname);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // Hide empty state initially (show skeleton instead)
    document.getElementById('empty-state').classList.add('hidden');

    loadStyles();
    handleBookmarkletHash();
    fetchArticles();

    // Poll for status updates when articles are processing
    setInterval(() => {
        const hasProcessing = articles.some(a =>
            ['summarizing', 'generating_video'].includes(a.status)
        );
        if (hasProcessing) {
            fetchArticles();
        }
    }, 5000);
});
