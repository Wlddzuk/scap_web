import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from flask import Flask

import discovery_web


clipper_app = None
story_finder = None
_module_test_dir = None
_module_env_previous = {}


def setUpModule():
    """Import app-bound discovery code after the complete suite is collected.

    TikTok's route tests configure the singleton Flask app during collection.
    Eagerly importing story_finder here would initialize that singleton too
    early and make the otherwise independent test suite order-dependent.
    """
    global clipper_app, story_finder, _module_test_dir
    _module_test_dir = tempfile.mkdtemp(prefix="clipper-discovery-import-")
    defaults = {
        "DATABASE_URI": f"sqlite:///{os.path.join(_module_test_dir, 'test.db')}",
        "FLASK_SECRET_KEY": "discovery-test-flask-secret",
        "TIKTOK_TOKEN_ENCRYPTION_KEY": "discovery-test-token-secret",
        "TIKTOK_CLIENT_KEY": "test-client-key",
        "TIKTOK_CLIENT_SECRET": "test-client-secret",
        "TIKTOK_REDIRECT_URI": "https://clipper.example/api/tiktok/oauth/callback",
        "SESSION_COOKIE_SECURE": "false",
    }
    for key, value in defaults.items():
        if key not in os.environ:
            _module_env_previous[key] = None
            os.environ[key] = value
    clipper_app = importlib.import_module("app")
    story_finder = importlib.import_module("story_finder")


def tearDownModule():
    for key, previous in _module_env_previous.items():
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
    if _module_test_dir:
        shutil.rmtree(_module_test_dir, ignore_errors=True)


class FakeResponse:
    def __init__(self, status_code=200, url="https://example.test/story", text=""):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )

    def close(self):
        self.closed = True


def forbidden_error():
    response = requests.Response()
    response.status_code = 403
    return requests.HTTPError("Access denied", response=response)


class DiscoveryPreflightTests(unittest.TestCase):
    def test_preflight_uses_browser_headers_without_downloading_body(self):
        candidate = story_finder.StoryCandidate(
            title="Reachable story",
            url="https://example.test/story",
            source="ScienceDaily",
            summary="Useful feed summary",
        )
        response = FakeResponse(url=candidate.url)

        with patch.object(story_finder, "validate_url", side_effect=lambda url: url), patch.object(
            story_finder.requests,
            "get",
            return_value=response,
        ) as request_get:
            story_finder._preflight_candidate(candidate)

        kwargs = request_get.call_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["headers"]["Accept-Language"], "en-US,en;q=0.9")
        self.assertIn("Mozilla/5.0", kwargs["headers"]["User-Agent"])
        self.assertIn("text/html", kwargs["headers"]["Accept"])
        self.assertTrue(response.closed)

    def test_blocked_domain_is_requested_once_and_only_usable_summaries_survive(self):
        long_summary = "A detailed feed summary with scientific context. " * 8
        candidates = [
            story_finder.StoryCandidate(
                title="First",
                url="https://blocked.test/first",
                source="Live Science",
                summary=long_summary,
            ),
            story_finder.StoryCandidate(
                title="Second",
                url="https://blocked.test/second",
                source="Live Science",
                summary=long_summary,
            ),
            story_finder.StoryCandidate(
                title="Third",
                url="https://blocked.test/third",
                source="Live Science",
                summary="Too short",
            ),
        ]
        response = FakeResponse(status_code=403, url=candidates[0].url)

        with patch.object(story_finder, "validate_url", side_effect=lambda url: url), patch.object(
            story_finder.requests,
            "get",
            return_value=response,
        ) as request_get, patch.object(story_finder.logger, "warning") as warning:
            usable = story_finder.preflight_candidates(candidates)

        self.assertEqual([item.title for item in usable], ["First", "Second"])
        self.assertTrue(all(item.use_rss_fallback for item in usable))
        request_get.assert_called_once()
        blocked_logs = [
            call for call in warning.call_args_list if "blocks article scraping" in call.args[0]
        ]
        self.assertEqual(len(blocked_logs), 1)

    def test_unreachable_candidate_is_removed_before_scoring(self):
        reachable = story_finder.StoryCandidate(
            title="Reachable",
            url="https://good.test/story",
            source="Phys.org",
        )
        unreachable = story_finder.StoryCandidate(
            title="Unreachable",
            url="https://down.test/story",
            source="Phys.org",
        )
        responses = [
            FakeResponse(url=reachable.url),
            requests.Timeout("timed out"),
            requests.Timeout("timed out again"),
        ]

        with patch.object(story_finder, "validate_url", side_effect=lambda url: url), patch.object(
            story_finder.requests,
            "get",
            side_effect=responses,
        ), patch.object(story_finder.time, "sleep"):
            result = story_finder.preflight_candidates([reachable, unreachable])

        self.assertEqual(result, [reachable])


class DiscoveryScoringFallbackTests(unittest.TestCase):
    def _candidates(self):
        return [
            story_finder.StoryCandidate(
                title="First science story",
                url="https://example.test/first",
                source="ScienceDaily",
                summary="A visual result with broad public appeal.",
            ),
            story_finder.StoryCandidate(
                title="Second science story",
                url="https://example.test/second",
                source="Live Science",
                summary="A useful but less surprising result.",
            ),
        ]

    def test_groq_permission_failure_falls_back_to_openrouter(self):
        openrouter_payload = json.dumps(
            {
                "scores": [
                    {"id": 0, "score": 92, "reason": "Immediate visual payoff."},
                    {"id": 1, "score": 71, "reason": "Clear but less surprising."},
                ]
            }
        )
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test-groq",
                "OPENROUTER_API_KEY": "test-openrouter",
                "GEMINI_API_KEY": "test-gemini",
            },
        ), patch.object(
            story_finder,
            "_performance_examples",
            return_value={"top_performers": [], "bottom_performers": []},
        ), patch.object(
            story_finder,
            "_groq_scoring_content",
            side_effect=forbidden_error(),
        ) as groq, patch.object(
            story_finder,
            "_openrouter_scoring_content",
            return_value=openrouter_payload,
        ) as openrouter, patch.object(
            story_finder,
            "_gemini_scoring_content",
        ) as gemini:
            ranked = story_finder.score_candidates(self._candidates())

        self.assertEqual([candidate.viral_score for candidate in ranked], [92.0, 71.0])
        self.assertEqual(ranked[0].score_reason, "Immediate visual payoff.")
        groq.assert_called_once()
        openrouter.assert_called_once()
        gemini.assert_not_called()

    def test_all_provider_failures_raise_instead_of_becoming_empty_results(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test-groq",
                "OPENROUTER_API_KEY": "test-openrouter",
                "GEMINI_API_KEY": "test-gemini",
            },
        ), patch.object(
            story_finder,
            "_performance_examples",
            return_value={"top_performers": [], "bottom_performers": []},
        ), patch.object(
            story_finder,
            "_groq_scoring_content",
            side_effect=forbidden_error(),
        ), patch.object(
            story_finder,
            "_openrouter_scoring_content",
            side_effect=RuntimeError("provider unavailable"),
        ), patch.object(
            story_finder,
            "_gemini_scoring_content",
            side_effect=RuntimeError("provider unavailable"),
        ):
            with self.assertRaises(story_finder.StoryScoringError):
                story_finder.score_candidates(self._candidates())

    def test_a_genuinely_empty_feed_does_not_call_any_ranker(self):
        with patch.object(story_finder, "_groq_scoring_content") as groq, patch.object(
            story_finder,
            "_openrouter_scoring_content",
        ) as openrouter, patch.object(
            story_finder,
            "_gemini_scoring_content",
        ) as gemini:
            self.assertEqual(story_finder.score_candidates([]), [])

        groq.assert_not_called()
        openrouter.assert_not_called()
        gemini.assert_not_called()

    def test_gemini_scoring_uses_a_non_versioned_model_alias_by_default(self):
        with patch.dict(os.environ, {"GEMINI_SCORING_MODEL": ""}):
            self.assertEqual(
                story_finder._gemini_scoring_model_name(),
                "gemini-flash-latest",
            )
        with patch.dict(os.environ, {"GEMINI_SCORING_MODEL": "gemini-3.6-flash"}):
            self.assertEqual(
                story_finder._gemini_scoring_model_name(),
                "gemini-3.6-flash",
            )


class DiscoveryWorkerScoringFailureTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="clipper-discovery-scoring-")
        self.state_path = os.path.join(self.test_dir, "discovery.json")
        self.env = patch.dict(os.environ, {"DISCOVERY_STATE_PATH": self.state_path})
        self.env.start()
        self.app = Flask(__name__, instance_path=self.test_dir)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.test_dir)

    def test_scoring_failure_is_not_saved_as_successful_empty_scan(self):
        def mark_running(state):
            state.update({"status": "running", "run_version": 1, "candidates": []})

        discovery_web._update_state(self.app, mark_running)
        owner = discovery_web._try_file_lock(self.app, "scoring-worker-test")
        self.assertIsNotNone(owner)

        with patch.object(
            story_finder,
            "discover_and_score",
            side_effect=story_finder.StoryScoringError("rankers unavailable"),
        ):
            discovery_web._run_discovery_worker(
                self.app,
                owner,
                trigger="manual",
                run_version=1,
            )

        state = discovery_web._read_state(self.app)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["candidates"], [])
        self.assertIn("Stories were found", state["error"])
        self.assertIn("ranking service", state["error"])


class DiscoveryScrapeFallbackTests(unittest.TestCase):
    def test_http_403_uses_long_rss_summary_without_retry(self):
        summary = "Researchers measured a surprising effect in a controlled study. " * 6
        candidate = story_finder.StoryCandidate(
            title="Blocked but usable",
            url="https://blocked.test/story",
            source="Live Science",
            summary=summary,
        )

        with patch.object(
            story_finder,
            "scrape_url_content",
            side_effect=forbidden_error(),
        ) as scrape:
            result = story_finder._scrape_with_retry(candidate)

        scrape.assert_called_once_with(candidate.url)
        self.assertEqual(result["content_source"], "rss_summary")
        self.assertEqual(result["content"], summary.strip())

    def test_http_403_with_short_summary_has_classified_scrape_failure(self):
        candidate = story_finder.StoryCandidate(
            title="Blocked and unusable",
            url="https://blocked.test/story",
            source="Live Science",
            summary="Brief teaser",
        )

        with patch.object(
            story_finder,
            "scrape_url_content",
            side_effect=forbidden_error(),
        ) as scrape:
            with self.assertRaises(story_finder.CandidateScrapeError) as caught:
                story_finder._scrape_with_retry(candidate)

        self.assertEqual(caught.exception.reason, "source_blocked")
        scrape.assert_called_once_with(candidate.url)

    def test_shared_scraper_sends_realistic_accept_headers(self):
        html = "<html><body><article><h1>Story</h1><p>" + ("Science text. " * 30) + "</p></article></body></html>"
        response = FakeResponse(url="https://example.test/story", text=html)

        with patch.object(clipper_app, "validate_url", side_effect=lambda url: url), patch.object(
            clipper_app.requests,
            "get",
            return_value=response,
        ) as request_get:
            result = clipper_app.scrape_url_content(response.url)

        self.assertGreater(len(result["content"]), 100)
        headers = request_get.call_args.kwargs["headers"]
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertIn("text/html", headers["Accept"])
        self.assertEqual(headers["Accept-Language"], "en-US,en;q=0.9")


class DiscoveryFailureStageTests(unittest.TestCase):
    def _run_with_fake_article(
        self,
        *,
        summarize_side_effect=None,
        render_side_effect=None,
        color_intensity="vivid",
    ):
        candidate = story_finder.StoryCandidate(
            title="Pipeline story",
            url="https://example.test/story",
            source="ScienceDaily",
        )
        article = SimpleNamespace(
            id=41,
            url=candidate.url,
            title=candidate.title,
            content="Article body " * 30,
            style=None,
            dominant_emotion=None,
            status="scraped",
            color_intensity="natural",
            viral_score=87.0,
        )
        article_type = MagicMock(return_value=article)
        article_type.query.filter_by.return_value.first.return_value = None
        fake_db = SimpleNamespace(session=MagicMock())
        fake_db.session.get.return_value = article
        summary = {
            "tldr": "Short summary",
            "bullets": ["One"],
            "video_script": "Narration text",
            "hashtags": ["#science"],
        }

        with patch.object(story_finder, "Article", article_type), patch.object(
            story_finder,
            "db",
            fake_db,
        ), patch.object(
            story_finder,
            "_scrape_with_retry",
            return_value={
                "url": candidate.url,
                "title": candidate.title,
                "content": article.content,
                "hero_image": None,
                "site_name": candidate.source,
            },
        ), patch.object(
            story_finder,
            "summarize_article",
            return_value=summary,
            side_effect=summarize_side_effect,
        ), patch.object(
            story_finder,
            "generate_video",
            return_value="unused.mp4",
            side_effect=render_side_effect,
        ) as generate:
            result = story_finder._process_candidate(
                candidate,
                color_intensity=color_intensity,
            )

        self.last_article = article
        self.last_generate = generate
        return result

    def test_summarize_failure_is_classified_and_generic(self):
        result = self._run_with_fake_article(
            summarize_side_effect=RuntimeError("raw provider secret detail")
        )

        self.assertEqual(result["failure_stage"], "summarize")
        self.assertEqual(result["pipeline_error"], "Summary failed: the story could not be summarized.")
        self.assertNotIn("provider", result["pipeline_error"])

    def test_blocked_source_failure_names_scrape_stage_and_logs_traceback(self):
        candidate = story_finder.StoryCandidate(
            title="Blocked story",
            url="https://blocked.test/story",
            source="Live Science",
            summary="Too short",
        )
        article_type = MagicMock()
        article_type.query.filter_by.return_value.first.return_value = None

        with patch.object(story_finder, "Article", article_type), patch.object(
            story_finder,
            "_scrape_with_retry",
            side_effect=story_finder.CandidateScrapeError("source_blocked"),
        ), patch.object(story_finder.logger, "error") as log_error:
            result = story_finder._process_candidate(candidate)

        self.assertEqual(result["failure_stage"], "scrape")
        self.assertEqual(result["failure_code"], "source_blocked")
        self.assertIn("blocked automated access", result["pipeline_error"])
        self.assertTrue(log_error.call_args.kwargs["exc_info"])

    def test_render_failure_is_classified_and_generic(self):
        result = self._run_with_fake_article(
            render_side_effect=RuntimeError("ffmpeg internals")
        )

        self.assertEqual(result["failure_stage"], "render")
        self.assertEqual(result["pipeline_error"], "Video render failed: the video could not be created.")
        self.assertNotIn("ffmpeg", result["pipeline_error"])

    def test_successful_discovery_render_forwards_and_persists_color(self):
        result = self._run_with_fake_article(color_intensity="electric")

        self.assertEqual(result["status"], "video_done")
        self.assertEqual(
            self.last_generate.call_args.kwargs["color_intensity"],
            "electric",
        )
        self.assertEqual(self.last_article.color_intensity, "electric")

    def test_failed_discovery_render_does_not_persist_requested_color(self):
        result = self._run_with_fake_article(
            color_intensity="electric",
            render_side_effect=RuntimeError("render failed"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.last_article.color_intensity, "natural")

    def test_discovery_payload_allow_lists_stage_message(self):
        payload = discovery_web._public_pipeline_failure(
            {
                "status": "failed",
                "article_id": 9,
                "failure_stage": "summarize",
                "error": "raw provider body and key fragment",
            }
        )

        self.assertEqual(payload["failure_stage"], "summarize")
        self.assertEqual(payload["pipeline_error"], "Summary failed: the story could not be summarized.")
        self.assertNotIn("raw provider", str(payload))


class DiscoveryLockCleanupTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="clipper-discovery-locks-")
        self.state_path = os.path.join(self.test_dir, "discovery.json")
        self.env = patch.dict(os.environ, {"DISCOVERY_STATE_PATH": self.state_path})
        self.env.start()
        self.app = Flask(__name__, instance_path=self.test_dir)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.test_dir)

    @unittest.skipIf(discovery_web.fcntl is None, "fcntl is required for safe stale-lock cleanup")
    def test_cleanup_removes_stale_candidate_locks_but_preserves_live_lock(self):
        stale_path = discovery_web._lock_path(self.app, "candidate-stale")
        stale_path.touch()
        live = discovery_web._try_file_lock(self.app, "candidate-live")
        live_path = Path(live.name)
        try:
            removed = discovery_web._cleanup_stale_candidate_locks(self.app)
            self.assertEqual(removed, 1)
            self.assertFalse(stale_path.exists())
            self.assertTrue(live_path.exists())
        finally:
            discovery_web._release_file_lock(live)


if __name__ == "__main__":
    unittest.main()
