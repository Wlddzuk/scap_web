"""Safety coverage for stale-video replacement commits."""

from types import SimpleNamespace


def test_old_file_cleanup_failure_keeps_committed_replacement(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCOVERY_ENABLED", "false")
    from scripts import rerender_stale_videos as rerender

    commits = []
    monkeypatch.setattr(
        rerender,
        "db",
        SimpleNamespace(session=SimpleNamespace(commit=lambda: commits.append(True))),
    )
    article = SimpleNamespace(
        video_path="old.mp4",
        video_generated_at=None,
        status="video_done",
    )
    new_path = tmp_path / "replacement.mp4"
    new_path.write_bytes(b"complete replacement")
    old_path = tmp_path / "old.mp4"
    old_path.mkdir()  # unlink() raises IsADirectoryError, exercising cleanup safety.

    size_mb = rerender.commit_replacement(
        article,
        new_path,
        old_path,
        keep_old=False,
    )

    assert commits == [True]
    assert article.video_path == new_path.name
    assert article.status == "video_done"
    assert article.hook_index_used is None
    assert new_path.exists()
    assert size_mb > 0
