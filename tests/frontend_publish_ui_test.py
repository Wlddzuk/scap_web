"""Static contracts for the no-build publishing dashboard."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def _z_index_value(name):
    match = re.search(rf"--z-{name}:\s*(-?\d+)\s*;", STYLES)
    assert match, f"missing --z-{name} tier"
    return int(match.group(1))


def test_toasts_are_above_every_modal_tier():
    assert _z_index_value("toast") > _z_index_value("modal") > _z_index_value("base")
    assert re.search(
        r"\.toast-container\s*\{[^}]*z-index:\s*var\(--z-toast\)",
        STYLES,
        re.DOTALL,
    )
    assert re.search(
        r"\.tiktok-modal-overlay\s*\{[^}]*z-index:\s*var\(--z-modal\)",
        STYLES,
        re.DOTALL,
    )
    assert re.search(
        r"\.qr-modal-overlay\s*\{[^}]*z-index:\s*var\(--z-modal\)",
        STYLES,
        re.DOTALL,
    )


def test_finished_video_uses_one_four_platform_manual_picker():
    assert "openTikTokPostDialog" not in APP_JS
    assert "submitTikTokPost" not in APP_JS
    assert 'onclick="openShareEverywhereDialog(event, ${article.id})"' in APP_JS
    assert "const platforms = ['tiktok', 'instagram', 'youtube', 'facebook'];" in APP_JS
    assert "Post to selected platforms" in APP_JS
    assert "Upload privately" not in APP_JS
    assert (
        "const selected = !reason && Boolean(retryPlatform && retryPlatform === platform);"
        in APP_JS
    )
    for platform in ("tiktok", "instagram", "youtube", "facebook"):
        assert f'id="platform-connection-{platform}"' in INDEX_HTML


def test_picker_has_inline_error_and_legacy_cancel_actions():
    assert 'id="share-request-error"' in APP_JS
    assert "setShareRequestError(message);" in APP_JS
    assert "/publish/cancel" in APP_JS
    assert "Cancel pending post" in APP_JS


def test_voice_tone_picker_defaults_to_controlled_and_previews_all_presets():
    assert "selectedVoiceToneByArticle[article.id] || 'controlled'" in APP_JS
    assert "Curious energy — strong hook, natural middle, lifted reveal." in APP_JS
    for tone in ("controlled", "energetic", "documentary"):
        assert f"{tone}:" in APP_JS
    assert "data-voice-tone-select" in APP_JS
    assert "previewVoiceTone(event, ${article.id})" in APP_JS
    assert "/api/tts/preview" in APP_JS
    assert "body.voice_tone = voiceTone;" in APP_JS


def test_voice_preview_arms_audio_context_before_waiting_for_server():
    context_position = APP_JS.index("new AudioContextType()")
    fetch_position = APP_JS.index("/api/tts/preview")
    assert context_position < fetch_position
    assert "decodeAudioData(audioBuffer.slice(0))" in APP_JS
    assert ".voice-tone-control" in STYLES
    assert ".voice-tone-description" in STYLES


def test_hook_variants_are_accessible_persisted_choices_with_stale_video_warning():
    assert "async function selectHook(event, articleId, hookIndex)" in APP_JS
    assert "/api/articles/${articleId}/hook" in APP_JS
    assert "body: JSON.stringify({ hook_index: hookIndex })" in APP_JS
    assert 'role="group"' in APP_JS
    assert 'aria-label="Opening hook options"' in APP_JS
    assert 'aria-pressed="${selectedIndex === i ? \'true\' : \'false\'}"' in APP_JS
    assert "article.hook_index_used" in APP_JS
    assert "article.best_hook_index" in APP_JS
    assert "Regenerate the video to render and attribute this hook." in APP_JS
    assert "hook-tag rendered-hook" in APP_JS
    assert ".hook-variant.selected" in STYLES
    assert ".hook-regeneration-note" in STYLES


def test_browser_caption_caps_every_user_visible_field():
    assert ".slice(0, 3)" in APP_JS
    assert ".map(tag => String(tag || '').trim().slice(0, 64))" in APP_JS
    assert "article.search_caption || article.title || ''" in APP_JS
    assert APP_JS.count(").trim().slice(0, 220).trim();") >= 2


def test_color_intensity_is_a_persisted_vivid_default_for_discovery_videos():
    assert 'id="color-intensity-select"' in INDEX_HTML
    assert 'aria-describedby="color-intensity-help"' in INDEX_HTML
    assert '<option value="natural">Natural</option>' in INDEX_HTML
    assert '<option value="vivid">Vivid (Recommended)</option>' in INDEX_HTML
    assert '<option value="electric">Electric (maximum color)</option>' in INDEX_HTML
    assert "Vivid is punchy but balanced." in INDEX_HTML
    assert "Electric is the neon cyan, magenta, and red reference look." in INDEX_HTML
    assert "const DEFAULT_COLOR_INTENSITY = 'vivid';" in APP_JS
    assert "const COLOR_INTENSITY_STORAGE_KEY = 'clipper_color_intensity';" in APP_JS
    assert "localStorage.setItem(COLOR_INTENSITY_STORAGE_KEY, colorIntensity);" in APP_JS
    assert "syncColorIntensityControl();" in APP_JS
    assert "body: JSON.stringify({ color_intensity: colorIntensity })" in APP_JS


def test_article_generation_uses_article_or_global_color_intensity():
    assert "selectedColorIntensityByArticle[article.id]" in APP_JS
    assert "|| article.color_intensity" in APP_JS
    assert "|| getColorIntensityPref()" in APP_JS
    assert 'data-color-intensity-select="${article.id}"' in APP_JS
    assert "onchange=\"selectColorIntensity(${article.id}, this.value)\"" in APP_JS
    assert "body.color_intensity = colorIntensity;" in APP_JS
    assert "generateVideo(articleId, imageSource, colorIntensity);" in APP_JS
    assert ".article-color-intensity-control" in STYLES


def test_color_intensity_assets_share_one_bumped_cache_version():
    versions = re.findall(
        r"/static/(?:styles\.css|app\.js)\?v=([^\"']+)",
        INDEX_HTML,
    )
    assert versions == [
        "20260801-find-articles",
        "20260801-find-articles",
    ]
