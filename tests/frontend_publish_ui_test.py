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
