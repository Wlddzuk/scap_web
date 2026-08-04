import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import discovery_web


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DiscoveryRouteTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="clipper-discovery-routes-")
        self.state_path = os.path.join(self.test_dir, "discovery.json")
        self.env = patch.dict(
            os.environ,
            {
                "DISCOVERY_ENABLED": "false",
                "DISCOVERY_STATE_PATH": self.state_path,
            },
        )
        self.env.start()
        self.app = Flask(__name__, instance_path=self.test_dir)
        self.app.config.update(TESTING=True)
        self.app.register_blueprint(discovery_web.discovery_bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.test_dir)

    def _save_shortlist(self):
        def update(state):
            state.update(
                {
                    "status": "complete",
                    "candidates": [
                        {
                            "candidate_id": "0123456789abcdef",
                            "rank": 1,
                            "title": "A surprising science result",
                            "url": "https://example.test/science",
                            "source": "ScienceDaily",
                            "viral_score": 91.0,
                            "score_reason": "Clear visual payoff and broad curiosity.",
                            "pipeline_status": "ready",
                        }
                    ],
                }
            )

        discovery_web._update_state(self.app, update)

    def test_get_candidates_returns_ranked_shortlist(self):
        self._save_shortlist()

        response = self.client.get("/api/discovery/candidates")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["candidates"][0]["viral_score"], 91.0)
        self.assertEqual(
            payload["candidates"][0]["score_reason"],
            "Clear visual payoff and broad curiosity.",
        )

    def test_run_returns_202_when_starting_background_work(self):
        with patch.object(discovery_web, "start_discovery", return_value=True) as start:
            response = self.client.post("/api/discovery/run")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["started"])
        start.assert_called_once_with(self.app, trigger="manual")

    def test_duplicate_run_is_coalesced_and_still_returns_202(self):
        with patch.object(discovery_web, "start_discovery", return_value=False):
            response = self.client.post("/api/discovery/run")

        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.get_json()["started"])
        self.assertIn("already running", response.get_json()["message"].lower())

    def test_make_video_queues_selected_candidate(self):
        self._save_shortlist()
        candidate = {"candidate_id": "0123456789abcdef", "title": "Story"}
        with patch.object(
            discovery_web,
            "start_candidate_pipeline",
            return_value=("started", candidate),
        ) as start:
            response = self.client.post(
                "/api/discovery/candidates/0123456789abcdef/make-video"
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["started"])
        start.assert_called_once_with(self.app, "0123456789abcdef", "vivid")

    def test_make_video_forwards_selected_color_intensity(self):
        self._save_shortlist()
        candidate = {"candidate_id": "0123456789abcdef", "title": "Story"}
        with patch.object(
            discovery_web,
            "start_candidate_pipeline",
            return_value=("started", candidate),
        ) as start:
            response = self.client.post(
                "/api/discovery/candidates/0123456789abcdef/make-video",
                json={"color_intensity": " Electric "},
            )

        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with(
            self.app,
            "0123456789abcdef",
            "electric",
        )

    def test_make_video_rejects_unknown_color_intensity(self):
        self._save_shortlist()
        with patch.object(discovery_web, "start_candidate_pipeline") as start:
            response = self.client.post(
                "/api/discovery/candidates/0123456789abcdef/make-video",
                json={"color_intensity": "radioactive"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Unknown color intensity",
        )
        start.assert_not_called()

    def test_candidate_worker_threads_color_intensity_to_pipeline(self):
        self._save_shortlist()

        class FakeCandidate:
            def __init__(self, **values):
                self.__dict__.update(values)

        fake_story_finder = types.ModuleType("story_finder")
        fake_story_finder.StoryCandidate = FakeCandidate
        process = unittest.mock.Mock(
            return_value={"status": "video_done", "article_id": 44}
        )
        fake_story_finder._process_candidate = process
        owner = discovery_web._try_file_lock(
            self.app,
            "candidate-worker-color",
        )

        with patch.dict(sys.modules, {"story_finder": fake_story_finder}):
            discovery_web._run_candidate_worker(
                self.app,
                owner,
                "0123456789abcdef",
                discovery_web._read_state(self.app)["candidates"][0],
                0,
                "electric",
            )

        process.assert_called_once()
        self.assertEqual(
            process.call_args.kwargs["color_intensity"],
            "electric",
        )

    def test_make_video_rejects_unknown_or_malformed_candidate(self):
        malformed = self.client.post(
            "/api/discovery/candidates/not-a-candidate/make-video"
        )
        self.assertEqual(malformed.status_code, 404)

        with patch.object(
            discovery_web,
            "start_candidate_pipeline",
            return_value=("missing", None),
        ):
            missing = self.client.post(
                "/api/discovery/candidates/ffffffffffffffff/make-video"
            )
        self.assertEqual(missing.status_code, 404)

    def test_file_lock_prevents_duplicate_worker_ownership(self):
        first = discovery_web._try_file_lock(self.app, "run-test")
        try:
            second = discovery_web._try_file_lock(self.app, "run-test")
            self.assertIsNone(second)
        finally:
            discovery_web._release_file_lock(first)

    def test_new_discovery_clears_old_shortlist_and_versions_the_run(self):
        self._save_shortlist()
        with patch.object(discovery_web, "Thread") as thread_type:
            self.assertTrue(discovery_web.start_discovery(self.app, trigger="manual"))

        state = discovery_web._read_state(self.app)
        self.assertEqual(state["run_version"], 1)
        self.assertEqual(state["candidates"], [])
        self.assertEqual(state["status"], "running")

        worker_args = thread_type.call_args.kwargs["args"]
        self.assertEqual(worker_args[-1], 1)
        discovery_web._release_file_lock(worker_args[1])

    def test_discovery_does_not_replace_shortlist_while_video_is_processing(self):
        self._save_shortlist()

        def mark_processing(state):
            state["candidates"][0]["pipeline_status"] = "processing"

        discovery_web._update_state(self.app, mark_processing)

        with patch.object(discovery_web, "Thread") as thread_type:
            self.assertFalse(discovery_web.start_discovery(self.app, trigger="manual"))

        self.assertFalse(thread_type.called)
        state = discovery_web._read_state(self.app)
        self.assertEqual(state["candidates"][0]["pipeline_status"], "processing")

    def test_candidate_queue_rejects_a_stale_shortlist_version(self):
        self._save_shortlist()
        original_try_file_lock = discovery_web._try_file_lock

        def advance_version_before_lock(flask_app, name):
            def advance(state):
                state["run_version"] = int(state.get("run_version") or 0) + 1

            discovery_web._update_state(flask_app, advance)
            return original_try_file_lock(flask_app, name)

        with patch.object(
            discovery_web,
            "_try_file_lock",
            side_effect=advance_version_before_lock,
        ):
            outcome, candidate = discovery_web.start_candidate_pipeline(
                self.app,
                "0123456789abcdef",
            )

        self.assertEqual(outcome, "missing")
        self.assertIsNone(candidate)
        state = discovery_web._read_state(self.app)
        self.assertEqual(state["candidates"][0]["pipeline_status"], "ready")

    def test_scheduler_is_started_once_with_configured_utc_hour(self):
        created = []

        class FakeScheduler:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.jobs = []
                self.started = False
                created.append(self)

            def add_job(self, function, **kwargs):
                self.jobs.append((function, kwargs))

            def start(self):
                self.started = True

            def shutdown(self, wait=False):
                self.started = False

        class FakeCronTrigger:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        background = types.ModuleType("apscheduler.schedulers.background")
        background.BackgroundScheduler = FakeScheduler
        cron = types.ModuleType("apscheduler.triggers.cron")
        cron.CronTrigger = FakeCronTrigger
        fake_modules = {
            "apscheduler": types.ModuleType("apscheduler"),
            "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
            "apscheduler.schedulers.background": background,
            "apscheduler.triggers": types.ModuleType("apscheduler.triggers"),
            "apscheduler.triggers.cron": cron,
        }

        discovery_web._shutdown_scheduler()
        discovery_web._scheduler_retry_after = 0.0
        try:
            with patch.dict(
                os.environ,
                {
                    "DISCOVERY_ENABLED": "true",
                    "DISCOVERY_HOUR_UTC": "17",
                    "DISCOVERY_STATE_PATH": self.state_path,
                },
            ), patch.dict(sys.modules, fake_modules):
                self.assertTrue(discovery_web.ensure_discovery_scheduler(self.app))
                self.assertTrue(discovery_web.ensure_discovery_scheduler(self.app))

            self.assertEqual(len(created), 1)
            self.assertTrue(created[0].started)
            trigger = created[0].jobs[0][1]["trigger"]
            self.assertEqual(trigger.kwargs["hour"], 17)
            self.assertEqual(created[0].jobs[0][1]["max_instances"], 1)
        finally:
            discovery_web._shutdown_scheduler()
            discovery_web._scheduler_retry_after = 0.0

    def test_scheduler_retries_after_another_worker_releases_ownership(self):
        clock = [100.0]
        discovery_web._shutdown_scheduler()
        discovery_web._scheduler_retry_after = 0.0
        try:
            with patch.dict(os.environ, {"DISCOVERY_ENABLED": "true"}), \
                 patch.object(discovery_web.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(discovery_web, "_try_file_lock", return_value=None) as acquire:
                self.assertFalse(discovery_web.ensure_discovery_scheduler(self.app))
                self.assertEqual(acquire.call_count, 1)

                clock[0] = 110.0
                self.assertFalse(discovery_web.ensure_discovery_scheduler(self.app))
                self.assertEqual(acquire.call_count, 1)

                clock[0] = 131.0
                self.assertFalse(discovery_web.ensure_discovery_scheduler(self.app))
                self.assertEqual(acquire.call_count, 2)
        finally:
            discovery_web._shutdown_scheduler()


class DiscoveryFrontendTests(unittest.TestCase):
    def test_dashboard_exposes_discovery_controls(self):
        markup = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="discovery-run-btn"', markup)
        self.assertIn('id="discovery-candidates"', markup)
        self.assertIn("/api/discovery/run", script)
        self.assertIn("/api/discovery/candidates/", script)
        self.assertIn("data-discovery-video", script)

    def test_article_cards_are_visible_without_animation_frames(self):
        script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        render_articles = script.split("function renderArticles()", 1)[1].split(
            "function renderArticleCard", 1
        )[0]

        self.assertNotIn("opacity: [0, 1]", render_articles)
        self.assertIn("motionEnhancementsAllowed()", render_articles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn(".article-card {\n    opacity: 1;", styles)

    def test_discovery_polling_preserves_focus_and_backs_off(self):
        script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("nextSignature !== discoveryRenderSignature", script)
        self.assertIn("captureDiscoveryFocus(container)", script)
        self.assertIn("restoreDiscoveryFocus(container, focusDescriptor)", script)
        self.assertIn("target.focus({ preventScroll: true })", script)
        self.assertIn("Math.min(discoveryPollDelayMs * 2, DISCOVERY_POLL_MAX_MS)", script)
        self.assertIn("if (document.hidden || !discoveryIsBusy()) return;", script)
        self.assertIn("if (document.hidden) {\n            stopDiscoveryPolling();", script)

    def test_failed_ranking_is_visible_and_not_rendered_as_no_unseen_stories(self):
        script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("previousDiscoveryStatus === 'running' && data.status === 'failed'", script)
        self.assertIn("const isFailed = discoveryState.status === 'failed';", script)
        self.assertIn("discoveryState.error || 'Story discovery failed. Please try again.'", script)
        self.assertIn("container.classList.toggle('hidden', !isComplete && !isFailed);", script)

    def test_empty_dashboard_renders_on_first_fetch_and_hidden_is_global(self):
        markup = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("const isFirstLoad = !initialLoadDone;", script)
        self.assertIn("if (isFirstLoad || articlesChanged", script)
        self.assertIn(".hidden {\n    display: none !important;", styles)
        self.assertIn('/static/app.js?v=', markup)


if __name__ == "__main__":
    unittest.main()
