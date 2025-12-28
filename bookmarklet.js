/**
 * Article Scraper Bookmarklet (CSP-Proof Version)
 * 
 * This bookmarklet extracts article content and opens the dashboard
 * with the data embedded in the URL - completely bypassing CSP restrictions.
 * 
 * How it works:
 * 1. Extracts content from the current page (DOM access is never blocked)
 * 2. Encodes the data in a URL hash
 * 3. Opens the dashboard which automatically submits the data
 * 
 * Usage: Drag the bookmarklet link to your bookmarks bar,
 * then click it when viewing an article to scrape.
 */

(function () {
    var DASHBOARD_URL = 'http://localhost:5050';

    // Extract article content (DOM access works even with strict CSP)
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
                '[class*="advertisement"]', '[class*="promo"]', 'iframe',
                '[class*="paywall"]', '[class*="subscribe"]', '[class*="signup"]'
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

    // Main execution
    try {
        var data = extractContent();

        if (!data.content || data.content.length < 100) {
            alert('❌ Could not extract article content from this page.\n\nTry selecting the article text manually and copying it.');
            return;
        }

        // Truncate content if too long for URL (max ~32KB to be safe)
        if (data.content.length > 30000) {
            data.content = data.content.slice(0, 30000) + '\n\n[Content truncated...]';
        }

        // Show confirmation with title
        var titlePreview = data.title.length > 50 ? data.title.slice(0, 50) + '...' : data.title;

        // Encode data and open dashboard
        var encodedData = encodeURIComponent(JSON.stringify(data));
        var dashboardUrl = DASHBOARD_URL + '#scrape=' + encodedData;

        // Open dashboard in new tab - this will automatically submit the data
        window.open(dashboardUrl, '_blank');

        alert('✅ Article extracted!\n\n"' + titlePreview + '"\n\nOpening dashboard to save...');

    } catch (error) {
        alert('❌ Error extracting article:\n\n' + error.message);
    }
})();
