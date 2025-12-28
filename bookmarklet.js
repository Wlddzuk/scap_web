/**
 * Article Scraper Bookmarklet (CSP-Bypass Version)
 * 
 * This bookmarklet extracts article content from the current page
 * and sends it to the local Article Scraper backend.
 * 
 * This version bypasses CSP restrictions by opening a popup window.
 * 
 * Usage: Drag the bookmarklet link to your bookmarks bar,
 * then click it when viewing an article to scrape.
 */

(function () {
    // Configuration
    var API_URL = 'http://localhost:5050/api/scrape';
    var DASHBOARD_URL = 'http://localhost:5050';

    // Extract article content (all inline, no eval needed)
    function extractContent() {
        var url = window.location.href;

        // Get title
        var h1 = document.querySelector('h1');
        var titleEl = document.querySelector('[class*="title"]');
        var articleTitle = document.querySelector('article h1, article h2');
        var title = (h1 && h1.textContent ? h1.textContent.trim() : '') ||
            (titleEl && titleEl.textContent ? titleEl.textContent.trim() : '') ||
            (articleTitle && articleTitle.textContent ? articleTitle.textContent.trim() : '') ||
            document.title;

        // Get site name
        var ogSite = document.querySelector('meta[property="og:site_name"]');
        var appName = document.querySelector('meta[name="application-name"]');
        var siteName = (ogSite && ogSite.content) ||
            (appName && appName.content) ||
            window.location.hostname;

        // Get hero image
        var ogImage = document.querySelector('meta[property="og:image"]');
        var articleImg = document.querySelector('article img');
        var heroImg = document.querySelector('[class*="hero"] img, [class*="featured"] img');
        var heroImage = (ogImage && ogImage.content) ||
            (articleImg && articleImg.src) ||
            (heroImg && heroImg.src) ||
            null;

        // Extract main content
        var content = '';
        var articleEl = document.querySelector('article');

        if (!articleEl) {
            articleEl = document.querySelector(
                '[class*="article-body"], [class*="post-content"], ' +
                '[class*="entry-content"], [class*="story-content"], ' +
                '[class*="content-body"], [itemprop="articleBody"], ' +
                '.post, .entry, main'
            );
        }

        if (!articleEl) {
            var candidates = document.querySelectorAll('div, section');
            var maxText = 0;
            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                var text = el.textContent ? el.textContent.trim() : '';
                if (text.length > maxText && text.length < 50000) {
                    maxText = text.length;
                    articleEl = el;
                }
            }
        }

        if (articleEl) {
            var clone = articleEl.cloneNode(true);
            var removeSelectors = [
                'script', 'style', 'nav', 'footer', 'header', 'aside',
                '[class*="comment"]', '[class*="sidebar"]', '[class*="related"]',
                '[class*="share"]', '[class*="social"]', '[class*="newsletter"]',
                '[class*="advertisement"]', '[class*="promo"]', 'iframe'
            ];

            for (var j = 0; j < removeSelectors.length; j++) {
                var toRemove = clone.querySelectorAll(removeSelectors[j]);
                for (var k = 0; k < toRemove.length; k++) {
                    toRemove[k].remove();
                }
            }

            var paragraphs = clone.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li');
            var texts = [];
            for (var m = 0; m < paragraphs.length; m++) {
                var pText = paragraphs[m].textContent ? paragraphs[m].textContent.trim() : '';
                if (pText && pText.length > 20) {
                    texts.push(pText);
                }
            }
            content = texts.join('\n\n');
        }

        if (!content || content.length < 200) {
            content = document.body.textContent
                .replace(/\s+/g, ' ')
                .trim()
                .slice(0, 10000);
        }

        return {
            url: url,
            title: title,
            content: content,
            hero_image: heroImage,
            site_name: siteName
        };
    }

    // Send data via form submission (works around CSP)
    function sendViaForm(data) {
        // Create a hidden form that posts to our server
        var form = document.createElement('form');
        form.method = 'POST';
        form.action = API_URL;
        form.target = '_blank';
        form.style.display = 'none';

        // We can't POST JSON via form, so we'll use a different approach
        // Open dashboard with data in URL hash
        var encodedData = encodeURIComponent(JSON.stringify(data));
        window.open(DASHBOARD_URL + '#scrape=' + encodedData, '_blank');
    }

    // Try fetch first, fall back to form-based approach
    function sendData(data) {
        // Use XMLHttpRequest which has better CSP compatibility
        var xhr = new XMLHttpRequest();
        xhr.open('POST', API_URL, true);
        xhr.setRequestHeader('Content-Type', 'application/json');

        xhr.onreadystatechange = function () {
            if (xhr.readyState === 4) {
                if (xhr.status >= 200 && xhr.status < 300) {
                    alert('✅ Article scraped successfully!\n\nCheck the dashboard at:\n' + DASHBOARD_URL);
                } else {
                    alert('❌ Failed to send article.\n\nError: ' + xhr.status + '\n\nOpening dashboard...');
                    sendViaForm(data);
                }
            }
        };

        xhr.onerror = function () {
            alert('❌ Network error. Opening dashboard with data...');
            sendViaForm(data);
        };

        try {
            xhr.send(JSON.stringify(data));
        } catch (e) {
            alert('❌ CSP blocked request. Opening dashboard...');
            sendViaForm(data);
        }
    }

    // Main execution
    try {
        var data = extractContent();

        if (!data.content || data.content.length < 100) {
            alert('❌ Could not extract article content from this page.');
            return;
        }

        alert('📰 Extracting article:\n\n"' + data.title.slice(0, 50) + '..."\n\nSending to server...');
        sendData(data);

    } catch (error) {
        alert('❌ Error: ' + error.message);
    }
})();
