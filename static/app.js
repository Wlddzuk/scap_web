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
const DEFAULT_VISUAL_STYLE = 'illustrated_science';
let selectedVoiceToneByArticle = {}; // { [articleId]: 'controlled' | 'energetic' | 'documentary' }
let selectedColorIntensityByArticle = {}; // { [articleId]: 'natural' | 'vivid' | 'electric' }
let voicePreviewAudioContext = null;
let activeVoicePreviewSource = null;
let activeVoicePreviewAudio = null;
let activeVoicePreviewObjectUrl = null;
const VOICE_TONES = {
    controlled: {
        label: 'Controlled',
        description: 'Curious energy — strong hook, natural middle, lifted reveal.'
    },
    energetic: {
        label: 'Energetic',
        description: 'Brighter and faster for playful, high-momentum stories.'
    },
    documentary: {
        label: 'Documentary',
        description: 'Measured and authoritative for serious or complex stories.'
    }
};
const COLOR_INTENSITIES = {
    natural: 'Natural',
    vivid: 'Vivid (Recommended)',
    electric: 'Electric (maximum color)'
};
const COLOR_INTENSITY_STORAGE_KEY = 'clipper_color_intensity';
const DEFAULT_COLOR_INTENSITY = 'vivid';
let platformConnections = {
    tiktok: { configured: false, connected: false },
    instagram: { configured: false, connected: false },
    youtube: { configured: false, connected: false },
    facebook: { configured: false, connected: false }
};
let generationBudget = null;
let generationBudgetRequestInFlight = false;
let generationBudgetLastLoadedAt = 0;
let discoveryState = {
    status: 'idle',
    running: false,
    candidates: [],
    count: 0,
    error: null
};
let discoveryRenderSignature = '';
let discoveryRequestInFlight = false;
let discoveryPollTimer = null;
let discoveryPollDelayMs = 5000;
const DISCOVERY_POLL_MIN_MS = 5000;
const DISCOVERY_POLL_MAX_MS = 30000;
const GENERATION_BUDGET_REFRESH_MS = 120000;

function motionEnhancementsAllowed() {
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    return Boolean(window.anime) && !document.hidden && !reduced;
}

function discoveryIsBusy(state = discoveryState) {
    return Boolean(state.running) || (state.candidates || []).some(
        candidate => ['queued', 'processing'].includes(candidate.pipeline_status)
    );
}

function discoverySignature(state) {
    return JSON.stringify({
        status: state.status,
        running: Boolean(state.running),
        error: state.error || null,
        runVersion: state.run_version || 0,
        candidates: state.candidates || []
    });
}

function setTextIfChanged(element, value) {
    if (element.textContent !== value) element.textContent = value;
}

// ============================================
// API Functions
// ============================================

async function fetchArticles() {
    try {
        const response = await fetch(`${API_BASE}/api/articles`);
        const data = await response.json();
        const prevArticles = articles;
        const isFirstLoad = !initialLoadDone;
        articles = data.articles;
        const generationJustFinished = prevArticles.some(previous => {
            if (!['generating_video', 'generating_carousel'].includes(previous.status)) return false;
            const current = articles.find(article => article.id === previous.id);
            return current && !['generating_video', 'generating_carousel'].includes(current.status);
        });

        // Hide loading skeleton after first load
        if (!initialLoadDone) {
            initialLoadDone = true;
            document.getElementById('loading-skeleton').style.display = 'none';
        }

        // Smart render: only full re-render when data actually changed
        if (isFirstLoad || articlesChanged(prevArticles, articles)) {
            renderArticles();
        }

        updateStats();
        updateProgressBanner();
        if (generationJustFinished) loadGenerationBudget(true);
    } catch (error) {
        console.error('Error fetching articles:', error);
        if (!initialLoadDone) {
            initialLoadDone = true;
            document.getElementById('loading-skeleton').style.display = 'none';
        }
        showToast('Failed to load articles', 'error');
    }
}

function formatBudgetUsd(value) {
    if (value === null || value === undefined || value === '') return null;
    const amount = Number(value);
    if (!Number.isFinite(amount)) return null;
    const digits = amount >= 100 ? 0 : amount >= 10 ? 1 : 2;
    return `$${amount.toFixed(digits)}`;
}

function generationBudgetProviderMeta(providerId, provider) {
    const links = {
        fal: ['FAL', 'https://fal.ai/dashboard/billing'],
        openrouter: ['OpenRouter', 'https://openrouter.ai/credits'],
        groq: ['Groq', 'https://console.groq.com/dashboard/usage'],
        gemini: ['Gemini', 'https://aistudio.google.com/app/billing']
    };
    const [name, fallbackUrl] = links[providerId] || [providerId, '#'];
    const url = provider && provider.dashboard_url ? provider.dashboard_url : fallbackUrl;
    const balance = formatBudgetUsd(provider && provider.balance_usd);
    let value = 'Not configured';
    let detail = 'No API key found';
    let tone = 'muted';

    if (provider && provider.available && balance) {
        value = `${balance} left`;
        detail = providerId === 'openrouter' && provider.key_usage_usd !== null && provider.key_usage_usd !== undefined && Number.isFinite(Number(provider.key_usage_usd))
            ? `${formatBudgetUsd(provider.key_usage_usd)} used by this key`
            : 'Live provider balance';
        tone = provider.severity === 'critical'
            ? 'critical'
            : provider.severity === 'low'
                ? 'warning'
                : 'ready';
    } else if (provider && provider.configured) {
        const openRouterUsage = providerId === 'openrouter'
            ? formatBudgetUsd(provider.key_usage_usd)
            : null;
        value = openRouterUsage
            ? `${openRouterUsage} used`
            : providerId === 'groq' || providerId === 'gemini'
                ? 'Configured'
                : 'Balance unavailable';
        detail = providerId === 'fal'
            ? 'Live balance lookup is temporarily unavailable'
            : providerId === 'openrouter'
                ? provider.key_limit_remaining_usd !== null && provider.key_limit_remaining_usd !== undefined
                    ? `${formatBudgetUsd(provider.key_limit_remaining_usd)} left on this API key`
                    : 'Live balance lookup is temporarily unavailable'
                : 'Usage is available in the provider dashboard';
        tone = 'muted';
    }

    return { name, url, value, detail, tone };
}

function renderGenerationBudget() {
    const label = document.getElementById('generation-budget-label');
    const dot = document.getElementById('generation-budget-dot');
    const summary = document.getElementById('generation-budget-summary');
    const providersContainer = document.getElementById('generation-budget-providers');
    const estimate = document.getElementById('generation-budget-estimate');
    const updated = document.getElementById('generation-budget-updated');
    if (!label || !dot || !summary || !providersContainer || !estimate || !updated) return;

    if (!generationBudget) {
        label.textContent = 'API budget';
        dot.dataset.tone = 'loading';
        return;
    }

    const providers = generationBudget.providers || {};
    const fal = providers.fal || {};
    const openrouter = providers.openrouter || {};
    const falBalance = formatBudgetUsd(fal.balance_usd);
    const openRouterBalance = formatBudgetUsd(openrouter.balance_usd);
    label.textContent = falBalance && openRouterBalance
        ? `FAL ${falBalance} · OR ${openRouterBalance}`
        : falBalance
            ? `FAL ${falBalance}`
            : openRouterBalance
                ? `OR ${openRouterBalance}`
                : 'API budget';

    const status = generationBudget.status || 'unavailable';
    const severity = generationBudget.severity || 'unavailable';
    dot.dataset.tone = severity === 'critical'
        ? 'critical'
        : severity === 'low'
            ? 'warning'
            : severity === 'ready'
                ? 'ready'
                : 'muted';
    summary.className = `generation-budget-summary ${severity}`;
    summary.textContent = severity === 'critical'
        ? 'Critical: top up before the next batch'
        : severity === 'low'
            ? 'Low balance: keep an eye on the next generation'
            : status === 'ready'
                ? 'Ready to generate'
                : status === 'limited'
                    ? 'Balance may be too low for a full generation'
                    : 'Generation is configured; live balances are unavailable';

    providersContainer.innerHTML = ['fal', 'openrouter', 'groq', 'gemini'].map(providerId => {
        const meta = generationBudgetProviderMeta(providerId, providers[providerId] || {});
        return `
            <a class="generation-budget-provider" href="${meta.url}" target="_blank" rel="noopener noreferrer">
                <span class="generation-budget-provider-dot ${meta.tone}"></span>
                <span class="generation-budget-provider-copy">
                    <strong>${meta.name}</strong>
                    <small>${escapeHtml(meta.detail)}</small>
                </span>
                <span class="generation-budget-provider-value">${escapeHtml(meta.value)}</span>
            </a>
        `;
    }).join('');

    const estimates = generationBudget.estimates || {};
    const standard = formatBudgetUsd(estimates.standard_video_usd);
    const motion = formatBudgetUsd(estimates.max_motion_video_usd);
    let videosLeft = '';
    if (generationBudget.limiting_balance_usd !== null && generationBudget.limiting_balance_usd !== undefined && Number.isFinite(Number(generationBudget.limiting_balance_usd)) && Number(estimates.standard_video_usd) > 0) {
        const count = Math.floor(Number(generationBudget.limiting_balance_usd) / Number(estimates.standard_video_usd));
        videosLeft = ` · roughly ${count} standard video${count === 1 ? '' : 's'} left`;
    }
    estimate.textContent = standard && motion
        ? `Estimated next video: ${standard} standard, up to ${motion} with motion${videosLeft}.`
        : 'Generation cost estimates are temporarily unavailable.';

    const fetched = generationBudget.fetched_at ? new Date(generationBudget.fetched_at) : null;
    updated.textContent = fetched && !Number.isNaN(fetched.getTime())
        ? `Updated ${fetched.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${generationBudget.cached ? ' · cached' : ''}`
        : 'Balance status unavailable';
}

async function loadGenerationBudget(force = false) {
    if (generationBudgetRequestInFlight) return;
    generationBudgetRequestInFlight = true;
    try {
        const suffix = force ? '?refresh=1' : '';
        const response = await fetch(`${API_BASE}/api/generation-budget${suffix}`);
        if (!response.ok) throw new Error('Budget endpoint unavailable');
        generationBudget = await response.json();
        generationBudgetLastLoadedAt = Date.now();
        renderGenerationBudget();
    } catch (error) {
        console.warn('Failed to load generation budget', error);
        if (!generationBudget) renderGenerationBudget();
        const updated = document.getElementById('generation-budget-updated');
        if (updated) updated.textContent = 'Could not refresh balances';
    } finally {
        generationBudgetRequestInFlight = false;
    }
}

function toggleGenerationBudget(event) {
    if (event) event.stopPropagation();
    const trigger = document.getElementById('generation-budget-trigger');
    const panel = document.getElementById('generation-budget-panel');
    if (!trigger || !panel) return;
    const willOpen = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !willOpen);
    trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    if (willOpen && Date.now() - generationBudgetLastLoadedAt > GENERATION_BUDGET_REFRESH_MS) {
        loadGenerationBudget(true);
    }
}

function closeGenerationBudget() {
    const trigger = document.getElementById('generation-budget-trigger');
    const panel = document.getElementById('generation-budget-panel');
    if (!trigger || !panel) return;
    panel.classList.add('hidden');
    trigger.setAttribute('aria-expanded', 'false');
}

function refreshGenerationBudget(event) {
    if (event) event.stopPropagation();
    const updated = document.getElementById('generation-budget-updated');
    if (updated) updated.textContent = 'Refreshing balances\u2026';
    loadGenerationBudget(true);
}

function articlesChanged(prev, next) {
    if (prev.length !== next.length) return true;
    for (let i = 0; i < prev.length; i++) {
        if (prev[i].id !== next[i].id ||
            prev[i].status !== next[i].status ||
            prev[i].video_path !== next[i].video_path ||
            prev[i].carousel_dir !== next[i].carousel_dir ||
            prev[i].tiktok_publish_status !== next[i].tiktok_publish_status ||
            prev[i].tiktok_publish_error !== next[i].tiktok_publish_error ||
            prev[i].hook_index_used !== next[i].hook_index_used ||
            prev[i].best_hook_index !== next[i].best_hook_index ||
            prev[i].video_script !== next[i].video_script ||
            JSON.stringify(prev[i].platform_posts || []) !== JSON.stringify(next[i].platform_posts || []) ||
            prev[i].tldr !== next[i].tldr) {
            return true;
        }
    }
    return false;
}

async function loadPublisherStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/publishers/status`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load publishing connections');
        platformConnections = {
            ...platformConnections,
            ...(data.platforms || {})
        };
        renderPublisherConnections();
    } catch (error) {
        console.warn('Failed to load publishing connection status', error);
        // Keep TikTok connection visibility working during a rolling deploy
        // where the legacy route may appear before the unified endpoint.
        try {
            const response = await fetch(`${API_BASE}/api/tiktok/status`);
            if (response.ok) {
                platformConnections.tiktok = await response.json();
                renderPublisherConnections();
            }
        } catch (legacyError) {
            console.warn('Failed to load TikTok connection status', legacyError);
        }
    }
}

function loadTikTokStatus() {
    return loadPublisherStatus();
}

function platformAccountLabel(platform, connection) {
    if (platform === 'tiktok') {
        return connection.creator_nickname || connection.creator_username || 'Connected';
    }
    if (platform === 'instagram') {
        return connection.username || connection.account_name || connection.page_name || 'Connected';
    }
    if (platform === 'facebook') {
        return connection.page_name || connection.username || connection.account_name || 'Connected Page';
    }
    return connection.channel_title || connection.channel_name || connection.username ||
        connection.email || 'Connected';
}

function platformNeedsReconnect(connection) {
    return Boolean(
        connection.needs_reconsent ||
        connection.needs_reconnect ||
        connection.reconnect_required ||
        connection.expired
    );
}

function platformExpiryMessage(connection) {
    if (!connection) return '';
    if (connection.expired) return 'Token expired';
    if (!connection.expiry_warning && !connection.token_expiry_warning) return '';
    const days = Number(connection.days_until_expiry);
    return Number.isFinite(days)
        ? `Token expires in ${days} day${days === 1 ? '' : 's'}`
        : 'Token expires soon';
}

function renderPlatformConnection(platform) {
    const connection = platformConnections[platform] || { configured: false, connected: false };
    const container = document.getElementById(`platform-connection-${platform}`);
    const label = document.getElementById(`platform-connection-${platform}-label`);
    const button = document.getElementById(`platform-connection-${platform}-btn`);
    if (!container || !label || !button) return;

    const reconnect = platformNeedsReconnect(connection);
    const expiryWarning = platformExpiryMessage(connection);
    container.classList.toggle('connected', Boolean(connection.connected) && !reconnect);
    container.classList.toggle('warning', Boolean(expiryWarning) || reconnect);

    if (connection.connected) {
        const accountName = platformAccountLabel(platform, connection);
        label.textContent = reconnect
            ? 'Reconnect required'
            : expiryWarning
                ? 'Token expires soon'
                : accountName;
        button.textContent = reconnect ? 'Reconnect' : 'Disconnect';
        button.disabled = false;
        button.title = reconnect
            ? connection.reconnect_reason || `Reconnect ${platform} to continue publishing`
            : expiryWarning || `Disconnect ${platform}`;
        button.onclick = reconnect
            ? () => connectPlatform(platform)
            : () => disconnectPlatform(platform);
        container.title = expiryWarning || accountName;
    } else if (!connection.configured) {
        label.textContent = 'Setup needed';
        button.textContent = 'Connect';
        button.disabled = false;
        button.title = connection.reason || connection.setup_reason ||
            `Missing: ${(connection.missing_config || []).join(', ')}`;
        button.onclick = () => connectPlatform(platform);
    } else {
        label.textContent = 'Not connected';
        button.textContent = 'Connect';
        button.disabled = false;
        button.title = `Connect ${platform}`;
        button.onclick = () => connectPlatform(platform);
    }
}

function renderPublisherConnections() {
    ['tiktok', 'instagram', 'youtube', 'facebook'].forEach(renderPlatformConnection);
}

function connectPlatform(platform) {
    const connection = platformConnections[platform] || {};
    if (!connection.configured) {
        const missing = (connection.missing_config || []).join(', ');
        const reason = connection.reason || connection.setup_reason ||
            (missing ? `Missing configuration: ${missing}` : 'Provider setup is incomplete');
        showToast(`${platformDisplayName(platform)} cannot connect yet. ${reason}`, 'error');
        return;
    }
    window.location.href = connection.oauth_start_url || `/api/${platform}/oauth/start`;
}

async function disconnectPlatform(platform) {
    const name = platform[0].toUpperCase() + platform.slice(1);
    if (!confirm(`Disconnect this ${name} account from Clipper?`)) return;
    try {
        const connection = platformConnections[platform] || {};
        const response = await fetch(connection.disconnect_url || `/api/${platform}/disconnect`, { method: 'POST' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Disconnect failed');
        showToast(`${name} disconnected`, 'success');
        await loadPublisherStatus();
    } catch (error) {
        showToast(error.message || `Failed to disconnect ${name}`, 'error');
    }
}

async function fetchDiscoveryCandidates(quiet = false) {
    if (discoveryRequestInFlight) return false;
    discoveryRequestInFlight = true;
    const previousDiscoveryStatus = discoveryState.status;
    const previousStatuses = new Map(
        (discoveryState.candidates || []).map(candidate => [candidate.candidate_id, candidate.pipeline_status])
    );

    try {
        const response = await fetch(`${API_BASE}/api/discovery/candidates`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load story discovery');

        const nextSignature = discoverySignature(data);
        const changed = nextSignature !== discoveryRenderSignature;
        discoveryState = data;
        if (changed) {
            discoveryRenderSignature = nextSignature;
            renderDiscovery();
        }
        if (previousDiscoveryStatus === 'running' && data.status === 'failed') {
            showToast(data.error || 'Story discovery failed. Please try again.', 'error');
        }

        let pipelineCompleted = false;
        for (const candidate of discoveryState.candidates || []) {
            const previous = previousStatuses.get(candidate.candidate_id);
            if (
                ['queued', 'processing'].includes(previous) &&
                !['queued', 'processing'].includes(candidate.pipeline_status)
            ) {
                pipelineCompleted = true;
            }
            if (['queued', 'processing'].includes(previous) && candidate.pipeline_status === 'video_done') {
                showToast('Discovery video is ready', 'success');
            } else if (['queued', 'processing'].includes(previous) && candidate.pipeline_status === 'failed') {
                showToast(candidate.pipeline_error || 'Video creation failed. Check the failed stage below.', 'error');
            }
        }
        if (pipelineCompleted) await fetchArticles();
        return changed;
    } catch (error) {
        console.error('Error loading discovery candidates:', error);
        if (!quiet) showToast('Failed to load story discovery', 'error');
        return false;
    } finally {
        discoveryRequestInFlight = false;
    }
}

function stopDiscoveryPolling() {
    if (discoveryPollTimer !== null) {
        clearTimeout(discoveryPollTimer);
        discoveryPollTimer = null;
    }
}

function scheduleDiscoveryPoll({ reset = false } = {}) {
    stopDiscoveryPolling();
    if (reset) discoveryPollDelayMs = DISCOVERY_POLL_MIN_MS;
    if (document.hidden || !discoveryIsBusy()) return;

    discoveryPollTimer = setTimeout(async () => {
        discoveryPollTimer = null;
        const changed = await fetchDiscoveryCandidates(true);
        discoveryPollDelayMs = changed
            ? DISCOVERY_POLL_MIN_MS
            : Math.min(discoveryPollDelayMs * 2, DISCOVERY_POLL_MAX_MS);
        scheduleDiscoveryPoll();
    }, discoveryPollDelayMs);
}

async function runStoryDiscovery() {
    const button = document.getElementById('discovery-run-btn');
    if (button) button.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/discovery/run`, { method: 'POST' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not start story discovery');
        showToast(data.started ? 'Scanning today\'s science stories…' : 'Story discovery is already running', 'info');
        await fetchDiscoveryCandidates(true);
        scheduleDiscoveryPoll({ reset: true });
    } catch (error) {
        console.error('Error starting discovery:', error);
        showToast('Failed to start story discovery', 'error');
        if (button) button.disabled = false;
    }
}

function focusStoryDiscovery() {
    const button = document.getElementById('discovery-run-btn');
    if (!button) return;
    button.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => button.focus({ preventScroll: true }), 450);
}

async function makeDiscoveryVideo(candidateId) {
    const button = document.querySelector(`[data-discovery-video="${candidateId}"]`);
    if (button) {
        button.disabled = true;
        button.textContent = 'Starting…';
    }

    try {
        const colorIntensity = getColorIntensityPref();
        const response = await fetch(
            `${API_BASE}/api/discovery/candidates/${candidateId}/make-video`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ color_intensity: colorIntensity })
            }
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not start video creation');
        showToast(data.started ? 'Video creation started' : 'This story is already being processed', 'info');
        await fetchDiscoveryCandidates(true);
        scheduleDiscoveryPoll({ reset: true });
    } catch (error) {
        console.error('Error starting discovery video:', error);
        showToast(error.message || 'Failed to start video creation', 'error');
        if (button) {
            button.disabled = false;
            button.textContent = 'Make video';
        }
    }
}

function captureDiscoveryFocus(container) {
    const active = document.activeElement;
    if (!active || !container.contains(active)) return null;
    const card = active.closest('[data-discovery-candidate]');
    if (!card) return null;
    const action = active.matches('[data-discovery-video]')
        ? 'video'
        : active.matches('[data-discovery-source]')
            ? 'source'
            : null;
    return action ? { candidateId: card.dataset.discoveryCandidate, action } : null;
}

function restoreDiscoveryFocus(container, descriptor) {
    if (!descriptor) return;
    const card = container.querySelector(
        `[data-discovery-candidate="${descriptor.candidateId}"]`
    );
    if (!card) return;
    const target = card.querySelector(
        descriptor.action === 'video' ? '[data-discovery-video]' : '[data-discovery-source]'
    );
    if (target && !target.disabled) target.focus({ preventScroll: true });
}

function renderDiscovery() {
    const button = document.getElementById('discovery-run-btn');
    const status = document.getElementById('discovery-status');
    const container = document.getElementById('discovery-candidates');
    if (!button || !status || !container) return;

    const candidates = discoveryState.candidates || [];
    const hasPipelineWork = candidates.some(
        candidate => ['queued', 'processing'].includes(candidate.pipeline_status)
    );
    button.disabled = Boolean(discoveryState.running) || hasPipelineWork;
    setTextIfChanged(
        button,
        discoveryState.running
            ? 'Finding stories…'
            : hasPipelineWork
                ? 'Video in progress…'
                : 'Find today\'s stories'
    );

    let statusMessage;
    if (discoveryState.running) {
        statusMessage = 'Scanning science feeds and ranking the strongest stories. This usually takes under a minute.';
    } else if (discoveryState.status === 'failed') {
        statusMessage = discoveryState.error || 'Story discovery failed. Please try again.';
    } else if (discoveryState.status === 'complete') {
        statusMessage = candidates.length
            ? `${candidates.length} ranked stor${candidates.length === 1 ? 'y' : 'ies'} found.`
            : 'No unseen stories were found today. Try again later.';
    } else {
        statusMessage = 'Ready to scan today\'s science stories.';
    }
    setTextIfChanged(status, statusMessage);

    const focusDescriptor = captureDiscoveryFocus(container);
    if (!candidates.length) {
        const isComplete = discoveryState.status === 'complete';
        const isFailed = discoveryState.status === 'failed';
        container.innerHTML = isComplete
            ? '<div class="discovery-empty">No unseen stories are waiting right now.</div>'
            : isFailed
                ? `<div class="discovery-empty">${escapeHtml(
                    discoveryState.error || 'Story discovery failed. Please try again.'
                )}</div>`
                : '';
        container.classList.toggle('hidden', !isComplete && !isFailed);
        restoreDiscoveryFocus(container, focusDescriptor);
        return;
    }

    container.classList.remove('hidden');
    container.innerHTML = candidates.map(candidate => {
        const score = Math.round(Number(candidate.viral_score) || 0);
        const pipelineStatus = candidate.pipeline_status || 'ready';
        const isProcessing = ['queued', 'processing'].includes(pipelineStatus);
        const isDone = pipelineStatus === 'video_done';
        const isSkipped = pipelineStatus === 'skipped';
        const failedStage = candidate.failure_stage || (candidate.result && candidate.result.failure_stage);
        const pipelineError = candidate.pipeline_error || (candidate.result && candidate.result.pipeline_error);
        const buttonLabel = isProcessing
            ? 'Making video…'
            : isDone
                ? 'Video ready'
                : isSkipped
                    ? 'Already added'
                    : pipelineStatus === 'failed'
                        ? 'Retry video'
                        : 'Make video';

        return `
            <article class="discovery-card" data-discovery-candidate="${candidate.candidate_id}">
                <div class="discovery-rank" aria-label="Rank ${candidate.rank || ''}">${candidate.rank || '–'}</div>
                <div class="discovery-score" aria-label="Viral score ${score} out of 100">
                    <strong>${score}</strong><span>/100</span>
                </div>
                <div class="discovery-story">
                    <h3>${escapeHtml(candidate.title)}</h3>
                    <div class="discovery-source-row">
                        <span>${escapeHtml(candidate.source || 'Science feed')}</span>
                        <a href="${escapeAttribute(candidate.url)}" target="_blank" rel="noopener noreferrer"
                            data-discovery-source>Read source</a>
                    </div>
                    <p class="discovery-reason" title="${escapeAttribute(candidate.score_reason || '')}">
                        ${escapeHtml(candidate.score_reason || 'Strong fit for a short science story.')}
                    </p>
                    ${pipelineStatus === 'failed' ? `
                        <p class="discovery-failure">
                            <strong>${escapeHtml(failedStage ? `${failedStage[0].toUpperCase() + failedStage.slice(1)} failed` : 'Video creation failed')}:</strong>
                            ${escapeHtml(pipelineError || 'This story could not be processed. You can retry it.')}
                        </p>
                    ` : ''}
                </div>
                <button type="button" class="btn btn-secondary discovery-video-btn"
                    data-discovery-video="${candidate.candidate_id}"
                    onclick="makeDiscoveryVideo('${candidate.candidate_id}')"
                    ${discoveryState.running || isProcessing || isDone || isSkipped ? 'disabled' : ''}>
                    ${buttonLabel}
                </button>
            </article>
        `;
    }).join('');
    restoreDiscoveryFocus(container, focusDescriptor);
}

async function scrapeUrl(event) {
    event.preventDefault();

    const urlInput = document.getElementById('url-input');
    const scrapeBtn = document.getElementById('scrape-btn');
    const url = urlInput.value.trim();

    if (!url) return;

    scrapeBtn.disabled = true;
    scrapeBtn.classList.add('is-loading');
    scrapeBtn.innerHTML = 'Scraping...<div class="btn-loading-bar"></div>';

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
        scrapeBtn.classList.remove('is-loading');
        scrapeBtn.innerHTML = 'Scrape';
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

async function selectHook(event, articleId, hookIndex) {
    if (event) event.stopPropagation();
    const picker = document.querySelector(
        `.hook-variants[data-article-id="${articleId}"]`
    );
    const buttons = picker ? Array.from(picker.querySelectorAll('.hook-variant')) : [];
    buttons.forEach(button => {
        button.disabled = true;
    });

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}/hook`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hook_index: hookIndex })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not select this hook');

        const articleIndex = articles.findIndex(article => article.id === articleId);
        if (articleIndex !== -1 && data.article) {
            articles[articleIndex] = data.article;
        }
        expandedArticles.add(articleId);
        renderArticles();
        showToast(
            data.message || `Hook ${hookIndex + 1} selected`,
            data.requires_regeneration ? 'info' : 'success'
        );
    } catch (error) {
        console.error('Hook selection failed:', error);
        showToast(error.message || 'Could not select this hook', 'error');
        buttons.forEach(button => {
            button.disabled = false;
        });
    }
}

function selectVoiceTone(articleId, voiceTone) {
    if (!VOICE_TONES[voiceTone]) return;
    selectedVoiceToneByArticle[articleId] = voiceTone;
    const description = document.querySelector(
        `[data-voice-tone-description="${articleId}"]`
    );
    if (description) description.textContent = VOICE_TONES[voiceTone].description;
}

function stopActiveVoicePreview() {
    if (activeVoicePreviewSource) {
        try {
            activeVoicePreviewSource.stop();
        } catch (_error) {
            // The source may already have ended.
        }
        activeVoicePreviewSource = null;
    }
    if (activeVoicePreviewAudio) {
        activeVoicePreviewAudio.pause();
        activeVoicePreviewAudio = null;
    }
    if (activeVoicePreviewObjectUrl) {
        URL.revokeObjectURL(activeVoicePreviewObjectUrl);
        activeVoicePreviewObjectUrl = null;
    }
}

async function previewVoiceTone(event, articleId) {
    if (event) event.stopPropagation();
    const button = event && event.currentTarget;
    const select = document.querySelector(`[data-voice-tone-select="${articleId}"]`);
    const voiceTone = select && VOICE_TONES[select.value] ? select.value : 'controlled';
    selectedVoiceToneByArticle[articleId] = voiceTone;

    stopActiveVoicePreview();
    if (button) {
        button.disabled = true;
        button.textContent = 'Loading…';
    }

    try {
        const AudioContextType = window.AudioContext || window.webkitAudioContext;
        if (AudioContextType) {
            if (!voicePreviewAudioContext || voicePreviewAudioContext.state === 'closed') {
                voicePreviewAudioContext = new AudioContextType();
            }
            if (voicePreviewAudioContext.state === 'suspended') {
                await voicePreviewAudioContext.resume();
            }
        }

        const response = await fetch(`${API_BASE}/api/tts/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice_tone: voiceTone })
        });

        if (!response.ok) {
            let data = {};
            try {
                data = await response.json();
            } catch (_error) {
                // The generic fallback below covers non-JSON server errors.
            }
            throw new Error(data.error || 'Voice preview is unavailable right now.');
        }

        const audioBuffer = await response.arrayBuffer();
        if (voicePreviewAudioContext) {
            const decoded = await voicePreviewAudioContext.decodeAudioData(audioBuffer.slice(0));
            const source = voicePreviewAudioContext.createBufferSource();
            source.buffer = decoded;
            source.connect(voicePreviewAudioContext.destination);
            activeVoicePreviewSource = source;
            source.onended = () => {
                if (activeVoicePreviewSource === source) activeVoicePreviewSource = null;
                if (button && button.isConnected) {
                    button.disabled = false;
                    button.textContent = '▶ Preview';
                }
            };
            source.start(0);
        } else {
            const blob = new Blob([audioBuffer], { type: 'audio/wav' });
            activeVoicePreviewObjectUrl = URL.createObjectURL(blob);
            const audio = new Audio(activeVoicePreviewObjectUrl);
            activeVoicePreviewAudio = audio;
            audio.addEventListener('ended', () => {
                if (activeVoicePreviewAudio === audio) activeVoicePreviewAudio = null;
                if (activeVoicePreviewObjectUrl) {
                    URL.revokeObjectURL(activeVoicePreviewObjectUrl);
                    activeVoicePreviewObjectUrl = null;
                }
                if (button && button.isConnected) {
                    button.disabled = false;
                    button.textContent = '▶ Preview';
                }
            }, { once: true });
            await audio.play();
        }

        if (button) button.textContent = 'Playing…';
    } catch (error) {
        console.error('Voice preview failed:', error);
        stopActiveVoicePreview();
        showToast(
            error.message || 'Voice preview is unavailable right now. Please try again.',
            'error'
        );
        if (button && button.isConnected) {
            button.disabled = false;
            button.textContent = '▶ Preview';
        }
    }
}

function getVideoHookPref() {
    // localStorage value is the source of truth; checkbox is its UI mirror.
    return localStorage.getItem('clipper_video_hook') === '1';
}

function onHookToggleChange() {
    const cb = document.getElementById('video-hook-toggle');
    if (!cb) return;
    localStorage.setItem('clipper_video_hook', cb.checked ? '1' : '0');
}

function syncHookToggle() {
    const cb = document.getElementById('video-hook-toggle');
    if (cb) cb.checked = getVideoHookPref();
}

function normalizeColorIntensity(value) {
    return Object.hasOwn(COLOR_INTENSITIES, value)
        ? value
        : DEFAULT_COLOR_INTENSITY;
}

function getColorIntensityPref() {
    return normalizeColorIntensity(localStorage.getItem(COLOR_INTENSITY_STORAGE_KEY));
}

function onColorIntensityChange() {
    const select = document.getElementById('color-intensity-select');
    if (!select) return;
    const colorIntensity = normalizeColorIntensity(select.value);
    select.value = colorIntensity;
    localStorage.setItem(COLOR_INTENSITY_STORAGE_KEY, colorIntensity);
}

function syncColorIntensityControl() {
    const select = document.getElementById('color-intensity-select');
    if (select) select.value = getColorIntensityPref();
}

function articleColorIntensity(article) {
    return normalizeColorIntensity(
        selectedColorIntensityByArticle[article.id]
        || article.color_intensity
        || getColorIntensityPref()
    );
}

function selectColorIntensity(articleId, value) {
    const colorIntensity = normalizeColorIntensity(value);
    selectedColorIntensityByArticle[articleId] = colorIntensity;
    const select = document.querySelector(
        `[data-color-intensity-select="${articleId}"]`
    );
    if (select) select.value = colorIntensity;
}

function renderColorIntensityOptions(selectedValue) {
    const selected = normalizeColorIntensity(selectedValue);
    return Object.entries(COLOR_INTENSITIES).map(([value, label]) => `
        <option value="${value}" ${selected === value ? 'selected' : ''}>
            ${escapeHtml(label)}
        </option>
    `).join('');
}

async function generateVideo(articleId, imageSource = 'ai', requestedColorIntensity = null) {
    const btn = document.querySelector(`[data-video="${articleId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Generating...';
    }

    const article = articles.find(a => a.id === articleId);
    const chosenStyle = selectedStyleByArticle[articleId] || DEFAULT_VISUAL_STYLE;
    const voiceTone = selectedVoiceToneByArticle[articleId] || 'controlled';
    const useVideoHook = getVideoHookPref();
    const colorIntensity = normalizeColorIntensity(
        requestedColorIntensity
        || (article && articleColorIntensity(article))
        || getColorIntensityPref()
    );

    const hookLabel = useVideoHook ? ' · AI video hook' : '';
    const voiceLabel = VOICE_TONES[voiceTone].label;
    showToast(
        `Generating video · ${voiceLabel} voice · ${COLOR_INTENSITIES[colorIntensity]} color${chosenStyle ? ' · ' + chosenStyle : ''}${hookLabel} — this may take a few minutes`,
        'info'
    );

    try {
        const body = {};
        if (chosenStyle) body.style = chosenStyle;
        body.use_video_hook = useVideoHook;
        body.image_source = imageSource;
        body.voice_tone = voiceTone;
        body.color_intensity = colorIntensity;

        const response = await fetch(`${API_BASE}/api/articles/${articleId}/video`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
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

async function generateCarousel(articleId, imageSource = 'ai') {
    showToast('Generating photo carousel - this may take a few minutes', 'info');

    try {
        const response = await fetch(`${API_BASE}/api/articles/${articleId}/carousel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_source: imageSource })
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Photo carousel generated!', 'success');
            await fetchArticles();
            expandedArticles.add(articleId);
            renderArticles();
        } else {
            showToast(data.error || 'Failed to generate carousel', 'error');
        }
    } catch (error) {
        console.error('Error generating carousel:', error);
        showToast('Failed to generate carousel', 'error');
    }
}

function generateOutput(articleId) {
    const formatSelect = document.querySelector(`[data-format-select="${articleId}"]`);
    const sourceSelect = document.querySelector(`[data-source-select="${articleId}"]`);
    const colorIntensitySelect = document.querySelector(
        `[data-color-intensity-select="${articleId}"]`
    );
    const format = formatSelect ? formatSelect.value : 'video';
    const imageSource = sourceSelect ? sourceSelect.value : 'ai';
    const colorIntensity = normalizeColorIntensity(
        colorIntensitySelect ? colorIntensitySelect.value : getColorIntensityPref()
    );
    selectedColorIntensityByArticle[articleId] = colorIntensity;
    if (format === 'carousel') {
        generateCarousel(articleId, imageSource);
    } else {
        generateVideo(articleId, imageSource, colorIntensity);
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
// QR Code Modal
// ============================================

function showQrModal(articleId, title, type = 'carousel') {
    // Remove existing modal
    closeQrModal();

    const modal = document.createElement('div');
    modal.id = 'qr-modal';
    modal.className = 'qr-modal-overlay';
    modal.onclick = (e) => { if (e.target === modal) closeQrModal(); };

    const shortTitle = title.length > 50 ? title.slice(0, 50) + '...' : title;
    const qrUrl = type === 'video'
        ? `/api/articles/${articleId}/video/qr`
        : `/api/articles/${articleId}/carousel/qr`;

    const steps = type === 'video'
        ? `<div class="qr-step">1️⃣ Scan QR → opens mobile page</div>
           <div class="qr-step">2️⃣ Tap "Save Video" → saves to Camera Roll</div>
           <div class="qr-step">3️⃣ Open TikTok → Create → Upload from Camera Roll</div>`
        : `<div class="qr-step">1️⃣ Scan QR → opens mobile page</div>
           <div class="qr-step">2️⃣ Long-press each image → "Save to Photos"</div>
           <div class="qr-step">3️⃣ Open TikTok → Photo Mode → select from Camera Roll</div>`;

    const typeLabel = type === 'video' ? 'Video' : 'Photo Carousel';

    modal.innerHTML = `
        <div class="qr-modal-content">
            <button class="qr-modal-close" onclick="closeQrModal()">&times;</button>
            <div class="qr-modal-icon">📱</div>
            <h3 class="qr-modal-title">Send ${typeLabel} to Phone</h3>
            <p class="qr-modal-subtitle">${shortTitle}</p>
            <div class="qr-modal-code">
                <img src="${qrUrl}" alt="QR Code" class="qr-img">
            </div>
            <p class="qr-modal-instructions">
                Scan this QR code with your iPhone camera.<br>
                Make sure your phone is on the <strong>same WiFi</strong> network.
            </p>
            <div class="qr-modal-steps">
                ${steps}
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add('active'));
}

function closeQrModal() {
    const modal = document.getElementById('qr-modal');
    if (modal) {
        modal.classList.remove('active');
        setTimeout(() => modal.remove(), 200);
    }
}

// ============================================
// Manual multi-platform publishing
// ============================================

function platformDisplayName(platform) {
    return {
        tiktok: 'TikTok',
        instagram: 'Instagram',
        youtube: 'YouTube',
        facebook: 'Facebook'
    }[platform] || platform;
}

function formatPublishStatus(status) {
    const labels = {
        AWAITING_APPROVAL: 'Old pending request — cancel to retry',
        CANCELLED: 'Cancelled',
        QUEUED: 'Queued',
        ACCEPTED: 'Accepted',
        INITIALIZING: 'Preparing upload',
        UPLOADING: 'Uploading video',
        PROCESSING_UPLOAD: 'Processing upload',
        PROCESSING_DOWNLOAD: 'Processing video',
        PROCESSING_CONTAINER: 'Processing Reel',
        CONTAINER_CREATED: 'Preparing Reel',
        IN_PROGRESS: 'Processing',
        FINISHED: 'Ready to publish',
        PUBLISHING: 'Publishing',
        PUBLISHED: 'Published',
        PUBLISH_COMPLETE: 'Published',
        FAILED: 'Failed',
        SEND_TO_USER_INBOX: 'Sent to TikTok inbox'
    };
    return labels[status] || String(status || 'Unknown').replaceAll('_', ' ').toLowerCase();
}

function suggestedShareCaption(article) {
    const hashtags = Array.isArray(article.hashtags)
        ? article.hashtags
            .slice(0, 3)
            .map(tag => String(tag || '').trim().slice(0, 64))
            .filter(Boolean)
            .join(' ')
        : '';
    const searchCaption = String(
        article.search_caption || article.title || ''
    ).trim().slice(0, 220).trim();
    const ctaQuestion = String(
        article.cta_question || ''
    ).trim().slice(0, 220).trim();
    return [
        searchCaption,
        ctaQuestion,
        hashtags
    ].filter(Boolean).join('\n\n');
}

function sharePlatformDisabledReason(platform, connection) {
    if (!connection || !connection.configured) {
        const missing = (connection && connection.missing_config || []).join(', ');
        return connection && (connection.reason || connection.setup_reason) ||
            (missing
                ? `Missing configuration: ${missing}`
                : (connection && connection.requirements) || 'Provider setup is incomplete');
    }
    if (platformNeedsReconnect(connection)) {
        return connection.reconnect_reason || 'Reconnect the account first';
    }
    if (!connection.connected) {
        return connection.reason || connection.connection_reason ||
            `Connect ${platformDisplayName(platform)} first`;
    }
    if (connection.publishing_available === false) {
        return connection.unavailable_reason || connection.publish_blocked_reason ||
            connection.reason || connection.requirements || 'Publishing is not available yet';
    }
    return '';
}

function closeShareModal() {
    const modal = document.getElementById('share-modal');
    if (!modal) return;
    modal.classList.remove('active');
    setTimeout(() => modal.remove(), 200);
}

async function openShareEverywhereDialog(event, articleId, retryPlatform = null) {
    if (event) event.stopPropagation();
    const article = articles.find(item => item.id === articleId);
    if (!article || !article.video_path) return;

    showToast('Loading your publishing accounts…', 'info');
    try {
        await loadPublisherStatus();
        let tiktokContext = null;
        if (platformConnections.tiktok && platformConnections.tiktok.connected) {
            try {
                const response = await fetch('/api/tiktok/creator-info', { method: 'POST' });
                const data = await response.json();
                if (response.ok) tiktokContext = data;
            } catch (error) {
                console.warn('Could not refresh TikTok creator settings', error);
            }
        }
        renderShareEverywhereDialog(article, tiktokContext, retryPlatform);
    } catch (error) {
        showToast(error.message || 'Could not open publishing', 'error');
    }
}

function renderShareEverywhereDialog(article, tiktokContext = null, retryPlatform = null) {
    closeShareModal();
    const modal = document.createElement('div');
    modal.id = 'share-modal';
    modal.className = 'tiktok-modal-overlay share-modal-overlay';
    modal.onclick = (event) => { if (event.target === modal) closeShareModal(); };

    const privacyLabels = {
        PUBLIC_TO_EVERYONE: 'Everyone',
        MUTUAL_FOLLOW_FRIENDS: 'Friends',
        FOLLOWER_OF_CREATOR: 'Followers',
        SELF_ONLY: 'Only you'
    };
    const creator = tiktokContext && tiktokContext.creator || {};
    const publicPostingEnabled = Boolean(tiktokContext && tiktokContext.public_posting_enabled);
    const privacyOptions = creator.privacy_level_options || [];
    const creatorAccountAppearsPublic = privacyOptions.includes('PUBLIC_TO_EVERYONE');
    const caption = suggestedShareCaption(article);
    const platforms = ['tiktok', 'instagram', 'youtube', 'facebook'];
    const platformCards = platforms.map(platform => {
        const connection = platformConnections[platform] || {};
        const reason = sharePlatformDisabledReason(platform, connection);
        // Publishing is always explicit. A normal Post flow starts with no
        // destinations selected; only a single-platform retry is preselected.
        const selected = !reason && Boolean(retryPlatform && retryPlatform === platform);
        const lockedByRetry = Boolean(retryPlatform && retryPlatform !== platform);
        const icon = { tiktok: '♪', instagram: '◎', youtube: '▶', facebook: 'f' }[platform];
        const helper = lockedByRetry
            ? `Retrying ${platformDisplayName(retryPlatform)} only`
            : reason || platformExpiryMessage(connection) || 'Ready to publish';
        const canConnect = reason && (!connection.connected || platformNeedsReconnect(connection));
        const connectLabel = platformNeedsReconnect(connection) ? 'Reconnect' : 'Connect';
        return `
            <div class="share-platform-card ${reason ? 'unavailable' : ''} ${lockedByRetry ? 'retry-locked' : ''}">
                <label class="share-platform-toggle">
                    <input type="checkbox" name="share-platform" value="${platform}" ${selected ? 'checked' : ''} ${reason || lockedByRetry ? 'disabled' : ''}>
                    <span class="share-platform-icon ${platform}">${icon}</span>
                    <span class="share-platform-copy">
                        <strong>${platformDisplayName(platform)}</strong>
                        <small>${escapeHtml(helper)}</small>
                    </span>
                    <span class="share-platform-check" aria-hidden="true">✓</span>
                </label>
                ${canConnect ? `
                    <button type="button" class="share-platform-connect"
                        onclick="event.stopPropagation(); connectPlatform('${platform}')">${connectLabel}</button>
                ` : ''}
            </div>
        `;
    }).join('');
    const tiktokPrivacyOptions = privacyOptions.length ? privacyOptions : ['SELF_ONLY'];
    modal.dataset.tiktokAccountBlocked =
        !publicPostingEnabled && creatorAccountAppearsPublic ? 'true' : 'false';

    modal.innerHTML = `
        <div class="tiktok-modal-content share-modal-content">
            <button class="qr-modal-close" onclick="closeShareModal()">&times;</button>
            <div class="tiktok-modal-heading">
                <div class="tiktok-mark share-mark">↗</div>
                <div>
                    <h3>${retryPlatform ? `Retry ${platformDisplayName(retryPlatform)}` : 'Post video'}</h3>
                    <p>Choose each destination yourself, then confirm once.</p>
                </div>
            </div>
            <div class="share-platform-grid">${platformCards}</div>
            <form id="share-post-form" onsubmit="submitShareEverywhere(event, ${article.id})">
                <label class="tiktok-field share-caption-field">
                    <span>Shared caption</span>
                    <textarea id="share-caption" required>${escapeHtml(caption)}</textarea>
                    <small class="share-counters">
                        <span data-counter-platform="tiktok">TikTok <b>${caption.length}</b>/2,200</span>
                        <span data-counter-platform="instagram">Instagram <b>${caption.length}</b>/2,200</span>
                        <span data-counter-platform="youtube">YouTube description <b>${caption.length}</b>/5,000</span>
                        <span data-counter-platform="facebook">Facebook <b>${caption.length}</b>/2,200</span>
                    </small>
                </label>
                <label class="tiktok-field share-youtube-title" data-options-platform="youtube">
                    <span>YouTube title</span>
                    <input id="share-youtube-title" type="text" value="${escapeHtml(article.title)}" required>
                    <small><span id="share-youtube-title-count">${article.title.length}</span>/100</small>
                </label>

                <div class="share-platform-options" data-options-platform="tiktok">
                    <div class="share-options-heading">TikTok settings</div>
                    ${!publicPostingEnabled && platformConnections.tiktok.connected ? `
                        <div class="tiktok-account-requirement ${creatorAccountAppearsPublic ? 'blocking' : ''}">
                            <strong>${creatorAccountAppearsPublic ? 'Account change required before upload' : 'Unaudited-app requirement'}</strong>
                            <span>${creatorAccountAppearsPublic
                                ? 'Set the TikTok account to Private, then reopen this window.'
                                : 'TikTok requires the account to remain Private and this post to use Only you until the app passes audit.'}</span>
                        </div>
                    ` : ''}
                    <label class="tiktok-field">
                        <span>Who can watch this video?</span>
                        <select id="tiktok-privacy" required>
                            ${tiktokPrivacyOptions.map(option => {
                                const locked = !publicPostingEnabled && option !== 'SELF_ONLY';
                                return `<option value="${escapeHtml(option)}" ${locked ? 'disabled' : ''} ${option === 'SELF_ONLY' ? 'selected' : ''}>${escapeHtml(privacyLabels[option] || option)}${locked ? ' · requires TikTok audit' : ''}</option>`;
                            }).join('')}
                        </select>
                    </label>
                    <fieldset class="tiktok-fieldset">
                        <legend>Allow people to</legend>
                        <label><input type="checkbox" id="tiktok-comments" ${creator.comment_disabled ? 'disabled' : ''}> Comment</label>
                        <label><input type="checkbox" id="tiktok-duet" ${creator.duet_disabled ? 'disabled' : ''}> Duet</label>
                        <label><input type="checkbox" id="tiktok-stitch" ${creator.stitch_disabled ? 'disabled' : ''}> Stitch</label>
                    </fieldset>
                    <fieldset class="tiktok-fieldset">
                        <legend>Content disclosure</legend>
                        <label><input type="checkbox" id="tiktok-own-brand"> Promotes my own brand or business</label>
                        <label><input type="checkbox" id="tiktok-branded-content"> Paid partnership or third-party brand</label>
                    </fieldset>
                    <label class="tiktok-consent">
                        <input type="checkbox" id="tiktok-consent">
                        <span>By posting, I agree to TikTok's <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener">Music Usage Confirmation</a>.</span>
                    </label>
                </div>

                <div class="share-platform-options" data-options-platform="instagram">
                    <div class="share-options-heading">Instagram settings</div>
                    <label class="tiktok-consent">
                        <input type="checkbox" id="instagram-share-to-feed" checked>
                        <span>Also show the Reel in the main Instagram feed.</span>
                    </label>
                    <small class="share-platform-note">Instagram fetches the finished MP4 from a temporary signed HTTPS link.</small>
                </div>

                <div class="share-platform-options" data-options-platform="youtube">
                    <div class="share-options-heading">YouTube settings</div>
                    <label class="tiktok-field">
                        <span>Visibility</span>
                        <select id="youtube-privacy">
                            <option value="private" selected>Private</option>
                            <option value="unlisted">Unlisted</option>
                            <option value="public">Public</option>
                        </select>
                        <small>Unverified API projects are restricted to private uploads.</small>
                    </label>
                </div>

                <div class="share-platform-options" data-options-platform="facebook">
                    <div class="share-options-heading">Facebook settings</div>
                    <small class="share-platform-note">The Reel will publish to the connected Facebook Page, not a personal profile.</small>
                </div>

                <div class="share-submit-note">Each platform records its own result. One failure will not cancel the others.</div>
                <div class="share-character-warning hidden" id="share-validation-error" role="alert"></div>
                <div class="share-request-error hidden" id="share-request-error" role="alert" aria-live="assertive"></div>
                <div class="tiktok-modal-actions">
                    <button type="button" class="btn btn-action" onclick="closeShareModal()">Cancel</button>
                    <button type="submit" class="btn btn-action btn-tiktok btn-share-everywhere" id="share-submit">${retryPlatform ? 'Retry selected platform' : 'Post to selected platforms'}</button>
                </div>
            </form>
            <div class="share-results hidden" id="share-results" aria-live="polite"></div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.querySelectorAll('input[name="share-platform"]').forEach(input => input.addEventListener('change', updateShareDialogValidation));
    document.getElementById('share-caption').addEventListener('input', updateShareDialogValidation);
    document.getElementById('share-youtube-title').addEventListener('input', updateShareDialogValidation);
    document.getElementById('tiktok-consent').addEventListener('change', updateShareDialogValidation);
    updateShareDialogValidation();
    requestAnimationFrame(() => modal.classList.add('active'));
}

function selectedSharePlatforms() {
    return [...document.querySelectorAll('input[name="share-platform"]:checked')].map(input => input.value);
}

function updateShareDialogValidation() {
    const caption = document.getElementById('share-caption');
    const youtubeTitle = document.getElementById('share-youtube-title');
    const warning = document.getElementById('share-validation-error');
    const submit = document.getElementById('share-submit');
    if (!caption || !youtubeTitle || !warning || !submit) return;
    setShareRequestError('');

    const selected = selectedSharePlatforms();
    const limits = { tiktok: 2200, instagram: 2200, youtube: 5000, facebook: 2200 };
    const issues = [];
    if (!caption.value.trim()) issues.push('Add a caption before posting');
    document.querySelectorAll('[data-counter-platform]').forEach(counter => {
        const platform = counter.dataset.counterPlatform;
        const countNode = counter.querySelector('b');
        if (countNode) countNode.textContent = caption.value.length;
        const over = selected.includes(platform) && caption.value.length > limits[platform];
        counter.classList.toggle('over-limit', over);
        if (over) issues.push(`${platformDisplayName(platform)} caption is ${caption.value.length - limits[platform]} characters too long`);
    });
    document.getElementById('share-youtube-title-count').textContent = youtubeTitle.value.length;
    if (selected.includes('youtube') && youtubeTitle.value.length > 100) {
        issues.push(`YouTube title is ${youtubeTitle.value.length - 100} characters too long`);
    }
    if (selected.includes('youtube') && !youtubeTitle.value.trim()) {
        issues.push('Add a YouTube title');
    }
    if (selected.includes('tiktok') && !document.getElementById('tiktok-consent').checked) {
        issues.push('TikTok music usage consent is required');
    }
    const modal = document.getElementById('share-modal');
    if (
        selected.includes('tiktok') &&
        modal &&
        modal.dataset.tiktokAccountBlocked === 'true'
    ) {
        issues.push('Make the connected TikTok account private, then reopen this window');
    }
    if (!selected.length) issues.push('Select at least one connected platform');

    warning.classList.toggle('hidden', issues.length === 0);
    warning.textContent = issues.length ? issues.join('. ') + '.' : '';
    submit.disabled = issues.length > 0;
    document.querySelectorAll('[data-options-platform]').forEach(section => {
        section.classList.toggle('inactive', !selected.includes(section.dataset.optionsPlatform));
    });
}

function setShareRequestError(message) {
    const region = document.getElementById('share-request-error');
    if (!region) return;
    region.textContent = message || '';
    region.classList.toggle('hidden', !message);
}

function safePublishUrl(value) {
    if (!value) return null;
    try {
        const parsed = new URL(value, window.location.origin);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
    } catch (error) {
        return null;
    }
}

function renderShareResults(articleId, results) {
    const container = document.getElementById('share-results');
    const form = document.getElementById('share-post-form');
    if (!container) return;
    const iconByPlatform = { tiktok: '♪', instagram: '◎', youtube: '▶', facebook: 'f' };
    const rows = Object.entries(results || {}).map(([platform, result]) => {
        const status = result.status || (result.accepted ? 'ACCEPTED' : 'FAILED');
        const failed = !result.accepted || status === 'FAILED';
        const permalink = safePublishUrl(result.permalink);
        return `
            <div class="share-result-row ${failed ? 'failed' : 'accepted'}">
                <span class="share-platform-icon ${platform}">${iconByPlatform[platform] || '↗'}</span>
                <span class="share-result-copy">
                    <strong>${platformDisplayName(platform)}</strong>
                    <small>${escapeHtml(formatPublishStatus(status))}${result.error ? ` · ${escapeHtml(result.error)}` : ''}</small>
                </span>
                ${permalink ? `<a href="${escapeHtml(permalink)}" target="_blank" rel="noopener noreferrer">View post</a>` : ''}
                ${failed ? `<button type="button" onclick="openShareEverywhereDialog(event, ${articleId}, '${platform}')">Retry</button>` : '<span class="share-result-ok">✓</span>'}
            </div>
        `;
    }).join('');
    if (form) form.classList.add('hidden');
    container.classList.remove('hidden');
    container.innerHTML = `
        <div class="share-results-heading">
            <div><strong>Publishing results</strong><small>Each platform continues independently.</small></div>
            <button type="button" class="btn btn-action" onclick="closeShareModal()">Done</button>
        </div>
        ${rows || '<div class="share-result-row failed">No platform result was returned.</div>'}
    `;
}

async function submitShareEverywhere(event, articleId) {
    event.preventDefault();
    updateShareDialogValidation();
    const submit = document.getElementById('share-submit');
    if (!submit || submit.disabled) return;
    const platforms = selectedSharePlatforms();
    const caption = document.getElementById('share-caption').value;

    submit.disabled = true;
    submit.textContent = 'Sharing…';
    setShareRequestError('');
    try {
        const response = await fetch(`/api/articles/${articleId}/publish`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                platforms,
                caption,
                options: {
                    tiktok: {
                        title: caption,
                        privacy_level: document.getElementById('tiktok-privacy').value,
                        allow_comment: document.getElementById('tiktok-comments').checked,
                        allow_duet: document.getElementById('tiktok-duet').checked,
                        allow_stitch: document.getElementById('tiktok-stitch').checked,
                        brand_organic_toggle: document.getElementById('tiktok-own-brand').checked,
                        brand_content_toggle: document.getElementById('tiktok-branded-content').checked,
                        consent: document.getElementById('tiktok-consent').checked
                    },
                    instagram: {
                        share_to_feed: document.getElementById('instagram-share-to-feed').checked
                    },
                    youtube: {
                        title: document.getElementById('share-youtube-title').value,
                        description: caption,
                        privacy_status: document.getElementById('youtube-privacy').value
                    },
                    facebook: {
                        caption
                    }
                }
            })
        });
        let data = {};
        try {
            data = await response.json();
        } catch (error) {
            console.warn('Publishing response was not JSON', error);
        }
        if (!response.ok && response.status !== 207) throw new Error(data.error || 'Publishing request failed');

        const index = articles.findIndex(article => article.id === articleId);
        if (index !== -1 && data.article) articles[index] = data.article;
        renderArticles();
        renderShareResults(articleId, data.results || {});
        const accepted = Object.values(data.results || {}).filter(result => result.accepted).length;
        const failed = Object.keys(data.results || {}).length - accepted;
        showToast(
            failed ? `${accepted} platform${accepted === 1 ? '' : 's'} accepted; ${failed} needs attention` : 'Publishing started for the selected platforms',
            failed ? 'info' : 'success'
        );
    } catch (error) {
        const message = error.message || 'Publishing failed';
        setShareRequestError(message);
        showToast(message, 'error');
        submit.disabled = false;
        submit.textContent = 'Post to selected platforms';
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
    const bannerContainer = document.querySelector('.progress-banner-container');
    const banner = document.getElementById('progress-banner');
    const text = document.getElementById('progress-text');
    const processing = articles.filter(a => ['summarizing', 'generating_video', 'generating_carousel'].includes(a.status));

    if (processing.length > 0) {
        const names = processing.map(a => {
            let label = 'Processing';
            if (a.status === 'summarizing') label = 'Summarizing';
            else if (a.status === 'generating_video') label = 'Generating video for';
            else if (a.status === 'generating_carousel') label = 'Generating carousel for';
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
    if (shouldAnimate && motionEnhancementsAllowed()) {
        anime({
            targets: '.article-card',
            // Cards are CSS-visible by default. Motion is an enhancement only,
            // so a suspended animation frame can never strand them at opacity 0.
            translateY: [12, 0],
            delay: anime.stagger(80),
            duration: 360,
            easing: 'easeOutCubic'
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

function normalizedPlatformPosts(article) {
    const posts = article.platform_posts;
    const normalized = {};
    if (Array.isArray(posts)) {
        posts.forEach(post => {
            if (post && post.platform) normalized[post.platform] = post;
        });
    } else if (posts && typeof posts === 'object') {
        Object.entries(posts).forEach(([platform, post]) => {
            normalized[platform] = { platform, ...(post || {}) };
        });
    }
    if (!normalized.tiktok && article.tiktok_publish_status) {
        normalized.tiktok = {
            platform: 'tiktok',
            status: article.tiktok_publish_status,
            external_id: article.tiktok_publish_id,
            error: article.tiktok_publish_error,
            published_at: article.tiktok_published_at
        };
    }
    return normalized;
}

function platformPostIsPending(status) {
    return [
        'QUEUED', 'ACCEPTED', 'INITIALIZING', 'UPLOADING',
        'PROCESSING_UPLOAD', 'PROCESSING_DOWNLOAD', 'CONTAINER_CREATED',
        'PROCESSING_CONTAINER', 'IN_PROGRESS', 'FINISHED', 'PUBLISHING', 'PROCESSING'
    ].includes(status);
}

function platformPostIsPublished(status) {
    return ['PUBLISHED', 'PUBLISH_COMPLETE', 'SEND_TO_USER_INBOX'].includes(status);
}

function platformPostCanRetry(status) {
    return ['FAILED', 'CANCELLED'].includes(status);
}

async function cancelPendingPublish(event, articleId) {
    if (event) event.stopPropagation();
    const button = event && event.currentTarget;
    if (button) {
        button.disabled = true;
        button.textContent = 'Cancelling…';
    }
    try {
        const response = await fetch(`/api/articles/${articleId}/publish/cancel`, {
            method: 'POST'
        });
        let data = {};
        try {
            data = await response.json();
        } catch (error) {
            console.warn('Cancel response was not JSON', error);
        }
        if (!response.ok) {
            throw new Error(data.error || 'Could not cancel this pending post');
        }
        const index = articles.findIndex(article => article.id === articleId);
        if (index !== -1 && data.article) articles[index] = data.article;
        renderArticles();
        showToast(data.message || 'Pending post cancelled. You can post it manually now.', 'success');
    } catch (error) {
        showToast(error.message || 'Could not cancel this pending post', 'error');
        if (button) {
            button.disabled = false;
            button.textContent = 'Cancel pending post';
        }
    }
}

function renderPlatformPostStates(article) {
    const posts = normalizedPlatformPosts(article);
    const platformOrder = ['tiktok', 'instagram', 'youtube', 'facebook'];
    const rows = Object.values(posts)
        .filter(post => platformOrder.includes(post.platform))
        .sort((a, b) => platformOrder.indexOf(a.platform) - platformOrder.indexOf(b.platform))
        .map(post => {
        const platform = post.platform || 'platform';
        const status = post.status || 'UNKNOWN';
        const failed = status === 'FAILED';
        const cancelled = status === 'CANCELLED';
        const awaitingCancellation = status === 'AWAITING_APPROVAL';
        const permalink = safePublishUrl(post.permalink);
        return `
            <div class="platform-post-state ${failed ? 'failed' : cancelled ? 'cancelled' : platformPostIsPublished(status) ? 'published' : ''}">
                <span><strong>${platformDisplayName(platform)}:</strong> ${escapeHtml(formatPublishStatus(status))}${post.error ? ` · ${escapeHtml(post.error)}` : ''}</span>
                ${permalink ? `<a href="${escapeHtml(permalink)}" target="_blank" rel="noopener noreferrer">View post</a>` : ''}
                ${platformPostCanRetry(status) ? `<button type="button" onclick="openShareEverywhereDialog(event, ${article.id}, '${platform}')">Retry</button>` : ''}
                ${awaitingCancellation ? `<button type="button" class="cancel-pending-post" onclick="cancelPendingPublish(event, ${article.id})">Cancel pending post</button>` : ''}
            </div>
        `;
    }).join('');
    return rows ? `<div class="platform-post-list">${rows}</div>` : '';
}

function getStatusBadges(article) {
    const posts = normalizedPlatformPosts(article);
    const supportedPlatforms = ['tiktok', 'instagram', 'youtube', 'facebook'];
    const platformBadges = Object.values(posts).filter(
        post => supportedPlatforms.includes(post.platform)
    ).map(post => {
        const name = platformDisplayName(post.platform);
        if (platformPostIsPublished(post.status)) {
            return `<span class="badge badge-platform badge-${post.platform}">${name} Posted</span>`;
        }
        if (platformPostIsPending(post.status)) {
            return `<span class="badge badge-processing">${name} Processing</span>`;
        }
        if (post.status === 'FAILED') {
            return `<span class="badge badge-failed">${name} Failed</span>`;
        }
        if (post.status === 'AWAITING_APPROVAL') {
            return `<span class="badge badge-failed">${name} Needs cancel</span>`;
        }
        return '';
    }).join('');

    // Single current-state pill. Priority: failed > processing > completed > scraped.
    if (article.status === 'failed') {
        return '<span class="badge badge-failed">Failed</span>' + platformBadges;
    }
    if (article.status === 'generating_video') {
        return '<span class="badge badge-processing">Generating Video</span>' + platformBadges;
    }
    if (article.status === 'generating_carousel') {
        return '<span class="badge badge-processing">Generating Carousel</span>' + platformBadges;
    }
    if (article.status === 'summarizing') {
        return '<span class="badge badge-processing">Summarizing</span>' + platformBadges;
    }
    if (article.video_path) {
        return '<span class="badge badge-video">Video Ready</span>' + platformBadges;
    }
    if (article.carousel_dir) {
        return '<span class="badge badge-carousel">Carousel Ready</span>' + platformBadges;
    }
    if (article.tldr) {
        return '<span class="badge badge-summarized">Summarized</span>' + platformBadges;
    }
    return '<span class="badge badge-scraped">Scraped</span>' + platformBadges;
}

function renderStylePicker(article) {
    if (!availableStyles.length) return '';
    const currentStyle = selectedStyleByArticle[article.id] || DEFAULT_VISUAL_STYLE;
    const suggested = DEFAULT_VISUAL_STYLE;

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
    const variants = Array.isArray(article.hook_variants) ? article.hook_variants : [];
    if (variants.length === 0) return '';
    const firstSceneSpeech = article.scenes && article.scenes[0]
        ? String(article.scenes[0].speech || '').trim()
        : '';
    const inferredIndex = variants.findIndex(
        hook => typeof hook === 'string' && hook.trim() === firstSceneSpeech
    );
    // Scene one is selected for the next render. hook_index_used is reserved
    // for the MP4 that most recently completed successfully.
    const selectedIndex = inferredIndex >= 0
        ? inferredIndex
        : (Number.isInteger(article.hook_index_used)
            ? article.hook_index_used
            : null);
    const bestIndex = Number.isInteger(article.best_hook_index)
        ? article.best_hook_index
        : null;
    const isProcessing = ['summarizing', 'generating_video', 'generating_carousel']
        .includes(article.status);
    return `
        <div class="summary-section">
            <div class="summary-label">Opening Hook</div>
            <p class="hook-picker-help">Choose the first line, then generate the video.</p>
            <div
                class="hook-variants"
                data-article-id="${article.id}"
                role="group"
                aria-label="Opening hook options"
            >
                ${variants.map((h, i) => `
                    <button
                        type="button"
                        class="hook-variant ${selectedIndex === i ? 'selected' : ''}"
                        data-hook-index="${i}"
                        aria-pressed="${selectedIndex === i ? 'true' : 'false'}"
                        onclick="selectHook(event, ${article.id}, ${i})"
                        ${isProcessing ? 'disabled' : ''}
                    >
                        <span class="hook-index">${i + 1}</span>
                        <span class="hook-text">${escapeHtml(h)}</span>
                        <span class="hook-tags">
                            ${bestIndex === i ? '<span class="hook-tag ai-pick">AI pick</span>' : ''}
                            ${selectedIndex === i ? '<span class="hook-tag selected-hook">Selected</span>' : ''}
                            ${article.video_path && article.hook_index_used === i && selectedIndex !== i
                                ? '<span class="hook-tag rendered-hook">Rendered</span>'
                                : ''}
                        </span>
                    </button>
                `).join('')}
            </div>
            ${article.video_path && article.hook_index_used !== selectedIndex ? `
                <p class="hook-regeneration-note">
                    This selection updates the script, not the existing video.
                    Regenerate the video to render and attribute this hook.
                </p>
            ` : ''}
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
            <div class="video-transfer-row">
                <a href="/videos/${encodeURIComponent(article.video_path)}" 
                   class="btn btn-action btn-carousel-dl" download>
                    📦 Download Video
                </a>
                <button class="btn btn-action btn-video-qr" 
                        onclick="showQrModal(${article.id}, '${escapeHtml(article.title)}', 'video')">
                    📱 Send to Phone
                </button>
                <button class="btn btn-action btn-tiktok btn-post"
                        onclick="openShareEverywhereDialog(event, ${article.id})">
                    ↗ Post
                </button>
            </div>
            ${renderPlatformPostStates(article)}
        ` : ''}

        ${article.carousel_dir ? `
            <div class="carousel-preview">
                <div class="carousel-header-row">
                    <div class="carousel-label">Photo Carousel (${6} slides)</div>
                    <div class="carousel-actions-row">
                        <a href="/api/articles/${article.id}/carousel/download" 
                           class="btn btn-action btn-carousel-dl" download>
                            📦 Download ZIP
                        </a>
                        <button class="btn btn-action btn-carousel-qr" 
                                onclick="showQrModal(${article.id}, '${escapeHtml(article.title)}')">
                            📱 Send to Phone
                        </button>
                    </div>
                </div>
                <div class="carousel-thumbnails">
                    ${[1, 2, 3, 4, 5, 6].map(i => `
                        <img src="/carousels/${article.id}/slide_${i}.png" 
                             alt="Slide ${i}" 
                             class="carousel-thumb"
                             loading="lazy"
                             onclick="window.open(this.src, '_blank')">
                    `).join('')}
                </div>
                ${article.carousel_audio ? `
                    <audio controls preload="metadata" class="carousel-audio">
                        <source src="/carousels/${article.id}/${article.carousel_audio}">
                    </audio>
                ` : ''}
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

        ${renderSubstackSection(article)}
    `;
}

function renderActions(article) {
    const canSummarize = article.status !== 'summarizing';
    const canGenerate = article.video_script && !['generating_video', 'generating_carousel'].includes(article.status);
    const isProcessing = ['summarizing', 'generating_video', 'generating_carousel'].includes(article.status);
    const voiceTone = selectedVoiceToneByArticle[article.id] || 'controlled';
    const colorIntensity = articleColorIntensity(article);

    return `
        <div class="article-actions">
            <button
                class="btn btn-action"
                data-summarize="${article.id}"
                onclick="summarizeArticle(${article.id})"
                ${!canSummarize || isProcessing ? 'disabled' : ''}
            >
                ${article.tldr ? 'Re-Summarize' : 'Summarize'}
            </button>

            <div class="generate-group">
                <label class="voice-tone-control">
                    <span class="voice-tone-label">Voice tone</span>
                    <span class="voice-tone-row">
                        <select
                            class="output-format-select voice-tone-select"
                            data-voice-tone-select="${article.id}"
                            onchange="selectVoiceTone(${article.id}, this.value)"
                            ${!canGenerate || isProcessing ? 'disabled' : ''}
                        >
                            ${Object.entries(VOICE_TONES).map(([key, preset]) => `
                                <option value="${key}" ${voiceTone === key ? 'selected' : ''}>
                                    ${escapeHtml(preset.label)}
                                </option>
                            `).join('')}
                        </select>
                        <button
                            type="button"
                            class="btn btn-action voice-preview-btn"
                            onclick="previewVoiceTone(event, ${article.id})"
                            ${!canGenerate || isProcessing ? 'disabled' : ''}
                        >
                            ▶ Preview
                        </button>
                    </span>
                    <span
                        class="voice-tone-description"
                        data-voice-tone-description="${article.id}"
                    >${escapeHtml(VOICE_TONES[voiceTone].description)}</span>
                </label>
                <label class="color-intensity-control article-color-intensity-control">
                    <span class="color-intensity-label">Color intensity</span>
                    <select
                        class="output-format-select color-intensity-select"
                        data-color-intensity-select="${article.id}"
                        aria-describedby="color-intensity-help-${article.id}"
                        onchange="selectColorIntensity(${article.id}, this.value)"
                        ${!canGenerate || isProcessing ? 'disabled' : ''}
                    >
                        ${renderColorIntensityOptions(colorIntensity)}
                    </select>
                    <span
                        class="color-intensity-help"
                        id="color-intensity-help-${article.id}"
                    >Vivid is punchy but balanced. Electric is the neon cyan, magenta, and red reference look.</span>
                </label>
                <select class="output-format-select" data-format-select="${article.id}" ${!canGenerate || isProcessing ? 'disabled' : ''}>
                    <option value="video">Classic Video</option>
                    <option value="carousel">Photo Carousel</option>
                </select>
                <select class="output-format-select" data-source-select="${article.id}" ${!canGenerate || isProcessing ? 'disabled' : ''}>
                    <option value="ai">🤖 AI Images</option>
                    <option value="mixed">🛰️ Mixed Real + AI</option>
                    <option value="stock">📷 Stock Photos</option>
                </select>
                <button
                    class="btn btn-action btn-success"
                    onclick="generateOutput(${article.id})"
                    ${!canGenerate || isProcessing ? 'disabled' : ''}
                >
                    Generate
                </button>
            </div>

            <button
                class="btn btn-action btn-danger"
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
            if (content && motionEnhancementsAllowed()) {
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

    if (expandedArticles.has(articleId) && motionEnhancementsAllowed()) {
        const newCard = document.querySelector(`.article-card[data-article-id="${articleId}"] .article-content`);
        if (newCard) {
            anime({
                targets: newCard,
                height: ['0px', newCard.scrollHeight + 'px'],
                opacity: [0, 1],
                duration: 600,
                easing: 'easeOutElastic(1, .8)',
                complete: function () {
                    newCard.style.height = 'auto';
                }
            });
        }
    }
}

function updateStats() {
    const totalCountEl = document.getElementById('total-count');
    const videoCountEl = document.getElementById('video-count');

    const prevTotal = totalCountEl.textContent;
    const newTotal = `${articles.length} article${articles.length !== 1 ? 's' : ''}`;

    const videoCount = articles.filter(a => a.video_path).length;
    const prevVideo = videoCountEl.textContent;
    const newVideo = `${videoCount} video${videoCount !== 1 ? 's' : ''}`;

    totalCountEl.textContent = newTotal;
    videoCountEl.textContent = newVideo;

    // Update carousel count in header if element exists
    const carouselCountEl = document.getElementById('carousel-count');
    if (carouselCountEl) {
        const carouselCount = articles.filter(a => a.carousel_dir).length;
        carouselCountEl.textContent = `${carouselCount} carousel${carouselCount !== 1 ? 's' : ''}`;
    }

    if (motionEnhancementsAllowed()) {
        if (prevTotal !== newTotal) {
            anime({
                targets: totalCountEl,
                scale: [1.2, 1],
                duration: 800,
                easing: 'spring(1, 80, 10, 0)'
            });
        }
        if (prevVideo !== newVideo) {
            anime({
                targets: videoCountEl,
                scale: [1.2, 1],
                duration: 800,
                easing: 'spring(1, 80, 10, 0)'
            });
        }
    }
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

function renderSubstackSection(article) {
    if (!article.tldr) return '';

    if (article.substack_post) {
        return `
            <div class="substack-section">
                <div class="summary-label">
                    Substack Post
                    <div class="substack-actions-inline">
                        <button class="copy-substack-btn" onclick="copySubstackPost(event, ${article.id})">
                            Copy Post
                        </button>
                        <button class="regenerate-substack-btn" id="substack-btn-${article.id}" onclick="generateSubstackPost(event, ${article.id}, true)" title="Regenerate with latest prompt">
                            Regenerate
                        </button>
                    </div>
                </div>
                <textarea class="substack-preview" readonly>${escapeHtml(article.substack_post)}</textarea>
            </div>
        `;
    }

    return `
        <div class="substack-section substack-generate">
            <div class="summary-label">Substack Post</div>
            <p class="substack-hint">Turn this story into a long-form newsletter your readers will love — with everyday analogies and a conversational tone.</p>
            <button class="btn btn-secondary" id="substack-btn-${article.id}" onclick="generateSubstackPost(event, ${article.id})">
                Generate Substack Post
            </button>
        </div>
    `;
}

async function generateSubstackPost(event, articleId, regenerate = false) {
    event.stopPropagation();
    const btn = document.getElementById(`substack-btn-${articleId}`);
    const originalText = btn ? btn.textContent : 'Generate Substack Post';
    if (btn) {
        btn.disabled = true;
        btn.textContent = regenerate ? 'Regenerating…' : 'Generating…';
    }

    try {
        const url = `/api/articles/${articleId}/substack${regenerate ? '?regenerate=1' : ''}`;
        const response = await fetch(url, { method: 'POST' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Generation failed');

        // Update in-memory array and re-render the card
        const idx = articles.findIndex(a => a.id === articleId);
        if (idx !== -1) articles[idx] = data.article;

        const card = document.querySelector(`.article-card[data-article-id="${articleId}"]`);
        if (card) {
            const contentEl = card.querySelector('.article-content');
            if (contentEl) {
                contentEl.querySelector('.substack-section').outerHTML = renderSubstackSection(data.article);
            }
        }
        showToast(regenerate ? 'Substack post regenerated!' : 'Substack post ready!', 'success');
    } catch (err) {
        console.error('Substack generation failed:', err);
        showToast('Failed to generate Substack post', 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }
}

function copySubstackPost(event, articleId) {
    event.stopPropagation();
    const article = articles.find(a => a.id === articleId);
    if (!article || !article.substack_post) return;

    navigator.clipboard.writeText(article.substack_post).then(() => {
        showToast('Substack post copied to clipboard!', 'success');
        const btn = event.target.closest('.copy-substack-btn');
        if (btn) {
            const original = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = original; }, 2000);
        }
    }).catch(() => showToast('Failed to copy post', 'error'));
}

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

function escapeAttribute(text) {
    return escapeHtml(String(text || ''))
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
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

    syncHookToggle();
    syncColorIntensityControl();
    loadStyles();
    loadPublisherStatus();
    loadGenerationBudget();
    fetchDiscoveryCandidates(true).finally(() => scheduleDiscoveryPoll({ reset: true }));
    handleBookmarkletHash();
    fetchArticles();

    const query = new URLSearchParams(window.location.search);
    if (query.get('tiktok') === 'connected') {
        showToast('TikTok account connected', 'success');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('tiktok') === 'error') {
        showToast('TikTok connection failed. Check the app configuration and try again.', 'error');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('instagram') === 'connected') {
        showToast('Instagram account connected', 'success');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('instagram') === 'error') {
        showToast('Instagram connection failed. Check the Meta app configuration and try again.', 'error');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('youtube') === 'connected') {
        showToast('YouTube channel connected', 'success');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('youtube') === 'error') {
        showToast('YouTube connection failed. Check the Google app configuration and try again.', 'error');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('facebook') === 'connected') {
        showToast('Facebook Page connected', 'success');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    } else if (query.get('facebook') === 'error') {
        showToast('Facebook connection failed. Check the Meta app configuration and try again.', 'error');
        history.replaceState(null, '', window.location.pathname + window.location.hash);
    }

    // Poll for status updates when articles are processing
    setInterval(() => {
        if (document.hidden) return;

        const hasProcessing = articles.some(a =>
            ['summarizing', 'generating_video', 'generating_carousel'].includes(a.status)
        );
        const remotePublishingBusy = articles.some(article =>
            Object.values(normalizedPlatformPosts(article)).some(post => platformPostIsPending(post.status))
        );
        if (hasProcessing || remotePublishingBusy) {
            // The server owns provider status polling; the dashboard refreshes
            // only while local generation or a selected platform is processing.
            fetchArticles();
        }
    }, 5000);

    setInterval(() => {
        if (!document.hidden) loadGenerationBudget();
    }, GENERATION_BUDGET_REFRESH_MS);

    document.addEventListener('click', event => {
        const budget = document.getElementById('generation-budget');
        if (budget && !budget.contains(event.target)) closeGenerationBudget();
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeGenerationBudget();
            closeShareModal();
        }
    });

    document.addEventListener('visibilitychange', async () => {
        if (document.hidden) {
            stopDiscoveryPolling();
            return;
        }
        if (Date.now() - generationBudgetLastLoadedAt > GENERATION_BUDGET_REFRESH_MS) {
            loadGenerationBudget();
        }
        await fetchDiscoveryCandidates(true);
        scheduleDiscoveryPoll({ reset: true });
    });
});
