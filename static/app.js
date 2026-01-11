/**
 * Article Scraper Dashboard - Frontend JavaScript
 */

const API_BASE = '';

// ============================================
// State
// ============================================

let articles = [];
let expandedArticles = new Set();

// ============================================
// API Functions
// ============================================

async function fetchArticles() {
    try {
        const response = await fetch(`${API_BASE}/api/articles`);
        const data = await response.json();
        articles = data.articles;
        renderArticles();
        updateStats();
    } catch (error) {
        console.error('Error fetching articles:', error);
        showToast('Feed unavailable', 'error');
    }
}

async function scrapeUrl(event) {
    event.preventDefault();

    const urlInput = document.getElementById('url-input');
    const scrapeBtn = document.getElementById('scrape-btn');
    const url = urlInput.value.trim();

    if (!url) return;

    scrapeBtn.disabled = true;
    scrapeBtn.textContent = 'Capturing...';
    showToast('Acquiring signal...', 'info');

    try {
        const response = await fetch(`${API_BASE}/api/scrape-url`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url })
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Signal captured successfully', 'success');
            urlInput.value = '';
            await fetchArticles();
            // Expand the new article
            if (data.article && data.article.id) {
                expandedArticles.add(data.article.id);
                renderArticles();
            }
        } else {
            showToast(data.error || 'Signal acquisition failed', 'error');
        }
    } catch (error) {
        console.error('Error scraping URL:', error);
        showToast('Signal acquisition failed', 'error');
    } finally {
        scrapeBtn.disabled = false;
        scrapeBtn.textContent = 'Capture';
    }
}


async function summarizeArticle(articleId) {
    const btn = document.querySelector(`[data-summarize="${articleId}"]`);
    if (btn) btn.disabled = true;

    showToast('Processing content...', 'info');

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}/summarize`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Analysis complete', 'success');
            await fetchArticles();
            // Expand the card to show the summary
            expandedArticles.add(articleId);
            renderArticles();
        } else {
            showToast(data.error || 'Analysis failed', 'error');
        }
    } catch (error) {
        console.error('Error summarizing article:', error);
        showToast('Analysis failed', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function generateVideo(articleId) {
    const btn = document.querySelector(`[data-video="${articleId}"]`);
    if (btn) btn.disabled = true;

    showToast('Rendering broadcast...', 'info');

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}/video`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Broadcast ready', 'success');
            await fetchArticles();
            expandedArticles.add(articleId);
            renderArticles();
        } else {
            showToast(data.error || 'Render failed', 'error');
        }
    } catch (error) {
        console.error('Error generating video:', error);
        showToast('Render failed', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function deleteArticle(articleId) {
    if (!confirm('Remove this broadcast from the feed?')) return;

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            showToast('Broadcast removed', 'success');
            await fetchArticles();
        } else {
            showToast('Removal failed', 'error');
        }
    } catch (error) {
        console.error('Error deleting article:', error);
        showToast('Removal failed', 'error');
    }
}

// ============================================
// Render Functions
// ============================================

function renderArticles() {
    const container = document.getElementById('articles-container');
    const emptyState = document.getElementById('empty-state');

    if (articles.length === 0) {
        container.classList.add('hidden');
        emptyState.classList.remove('hidden');
        return;
    }

    emptyState.classList.add('hidden');
    container.classList.remove('hidden');

    // Only animate if content length changed (avoid re-animating on polling updates)
    const shouldAnimate = container.childElementCount !== articles.length;

    container.innerHTML = articles.map(article => renderArticleCard(article)).join('');

    // Add click handlers for expanding cards
    document.querySelectorAll('.article-header').forEach(header => {
        header.addEventListener('click', (e) => {
            if (e.target.closest('button') || e.target.closest('a')) return;
            const card = header.closest('.article-card');
            const articleId = parseInt(card.dataset.articleId);
            toggleExpand(articleId, card);
        });
    });

    // Staggered Entry Animation (only on full render)
    if (shouldAnimate && window.anime) {
        anime({
            targets: '.article-card',
            translateY: [20, 0],
            opacity: [0, 1],
            delay: anime.stagger(100),
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
                        <a href="${escapeHtml(article.url)}" target="_blank">View Original →</a>
                    </div>
                </div>
                <div class="article-status">
                    ${statusBadges}
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
    const badges = [];

    // Always show captured
    badges.push('<span class="badge badge-scraped">Captured</span>');

    // Show analyzed or processing
    if (article.status === 'summarizing') {
        badges.push('<span class="badge badge-processing">Analyzing</span>');
    } else if (article.tldr) {
        badges.push('<span class="badge badge-summarized">Analyzed</span>');
    }

    // Show video status
    if (article.status === 'generating_video') {
        badges.push('<span class="badge badge-processing">Rendering</span>');
    } else if (article.video_path) {
        badges.push('<span class="badge badge-video">Broadcast</span>');
    }

    // Show failed
    if (article.status === 'failed') {
        badges.push('<span class="badge badge-failed">Error</span>');
    }

    return badges.join('');
}

function renderSummary(article) {
    if (!article.tldr) {
        return `
            <div class="summary-section">
                <p class="summary-text" style="color: var(--text-dim);">
                    Click Analyze to extract key insights and generate a video script.
                </p>
            </div>
        `;
    }

    const bullets = article.bullets || [];

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
        
        ${article.video_script ? `
            <div class="summary-section">
                <div class="summary-label">Video Script</div>
                <div class="video-script">${escapeHtml(article.video_script)}</div>
            </div>
        ` : ''}
        
        ${article.video_path ? `
            <div class="video-container">
                <video controls>
                    <source src="/videos/${article.video_path}" type="video/mp4">
                    Your browser does not support the video tag.
                </video>
            </div>
        ` : ''}
    `;
}

function renderActions(article) {
    const canSummarize = !article.tldr && article.status !== 'summarizing';
    const canGenerateVideo = article.video_script && !article.video_path && article.status !== 'generating_video';
    const isProcessing = ['summarizing', 'generating_video'].includes(article.status);

    return `
        <div class="article-actions">
            <button
                class="btn btn-primary"
                data-summarize="${article.id}"
                onclick="summarizeArticle(${article.id})"
                ${!canSummarize || isProcessing ? 'disabled' : ''}
            >
                ${article.tldr ? 'Re-Analyze' : 'Analyze'}
            </button>

            <button
                class="btn btn-success"
                data-video="${article.id}"
                onclick="generateVideo(${article.id})"
                ${!canGenerateVideo && !article.video_path || isProcessing ? 'disabled' : ''}
            >
                ${article.video_path ? 'Re-Render' : 'Render Video'}
            </button>

            <button
                class="btn btn-danger"
                onclick="deleteArticle(${article.id})"
            >
                Remove
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
                // Smooth collapse
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

    // Animate expansion if opening
    if (expandedArticles.has(articleId) && window.anime) {
        // Find the newly rendered card
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
    const totalEl = document.getElementById('total-count');
    const videoEl = document.getElementById('video-count');
    const videoCount = articles.filter(a => a.video_path).length;

    totalEl.setAttribute('data-label', articles.length);
    videoEl.setAttribute('data-label', videoCount);
}

// ============================================
// Toast Notifications
// ============================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');

    const icons = {
        success: '✅',
        error: '❌',
        info: 'ℹ️'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${icons[type]}</span>
        <span class="toast-message">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    // Auto-remove after 4 seconds
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ============================================
// Utility Functions
// ============================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// Initialize
// ============================================

// Handle bookmarklet fallback - check for #scrape=... hash
async function handleBookmarkletHash() {
    const hash = window.location.hash;
    if (hash.startsWith('#scrape=')) {
        try {
            const encodedData = hash.substring(8); // Remove '#scrape='
            const data = JSON.parse(decodeURIComponent(encodedData));

            showToast('Incoming transmission...', 'info');

            const response = await fetch(`${API_BASE}/api/scrape`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (response.ok) {
                showToast('Signal captured', 'success');
                // Clear the hash
                history.replaceState(null, '', window.location.pathname);
                await fetchArticles();
                if (result.article && result.article.id) {
                    expandedArticles.add(result.article.id);
                    renderArticles();
                }
            } else {
                showToast(result.error || 'Transmission failed', 'error');
            }
        } catch (error) {
            console.error('Error processing bookmarklet data:', error);
            showToast('Transmission error', 'error');
        }
        // Clear the hash even on error
        history.replaceState(null, '', window.location.pathname);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // Check for bookmarklet hash data first
    handleBookmarkletHash();

    fetchArticles();

    // Auto-refresh every 5 seconds (for status updates)
    setInterval(() => {
        const processingArticles = articles.some(a =>
            ['summarizing', 'generating_video'].includes(a.status)
        );
        if (processingArticles) {
            fetchArticles();
        }
    }, 5000);
});
