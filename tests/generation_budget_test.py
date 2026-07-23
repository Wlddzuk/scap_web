import json
import os
import unittest
from unittest.mock import patch

import requests

import generation_budget
import video_generator


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class GenerationBudgetTests(unittest.TestCase):
    def setUp(self):
        generation_budget.clear_generation_budget_cache()

    def tearDown(self):
        generation_budget.clear_generation_budget_cache()

    def test_live_balances_use_normal_inference_keys(self):
        responses = {
            generation_budget.FAL_USER_BALANCE_URL: FakeResponse(5.074),
            generation_budget.OPENROUTER_CREDITS_URL: FakeResponse(
                {"data": {"total_credits": 10, "total_usage": 8.37728692}}
            ),
            generation_budget.OPENROUTER_KEY_URL: FakeResponse(
                {
                    "data": {
                        "usage": 3.25,
                        "limit": 10,
                        "limit_remaining": 6.75,
                        "limit_reset": "monthly",
                    }
                }
            ),
        }

        def fake_get(url, **kwargs):
            return responses[url]

        configured = {
            "FAL_KEY": "fal-inference-secret",
            "OPENROUTER_API_KEY": "or-inference-secret",
            "GROQ_API_KEY": "groq-secret",
            "GEMINI_API_KEY": "gemini-secret",
        }
        with patch.dict(os.environ, configured, clear=True), patch.object(
            generation_budget.requests,
            "get",
            side_effect=fake_get,
        ) as get:
            payload = generation_budget.get_generation_budget()

        fal = payload["providers"]["fal"]
        openrouter = payload["providers"]["openrouter"]
        self.assertTrue(fal["available"])
        self.assertEqual(fal["balance_usd"], 5.074)
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(openrouter["available"])
        self.assertAlmostEqual(openrouter["balance_usd"], 1.62271308)
        self.assertEqual(openrouter["key_usage_usd"], 3.25)
        self.assertEqual(openrouter["key_limit_remaining_usd"], 6.75)
        self.assertAlmostEqual(payload["limiting_balance_usd"], 1.62271308)
        self.assertEqual(fal["severity"], "ready")
        self.assertEqual(openrouter["severity"], "low")
        self.assertEqual(payload["severity"], "low")
        self.assertTrue(payload["standard_video_affordable"])
        self.assertTrue(payload["max_motion_video_affordable"])

        fal_call = next(
            call for call in get.call_args_list
            if call.args[0] == generation_budget.FAL_USER_BALANCE_URL
        )
        self.assertEqual(
            fal_call.kwargs["headers"]["Authorization"],
            "Key fal-inference-secret",
        )
        credits_call = next(
            call for call in get.call_args_list
            if call.args[0] == generation_budget.OPENROUTER_CREDITS_URL
        )
        key_usage_call = next(
            call for call in get.call_args_list
            if call.args[0] == generation_budget.OPENROUTER_KEY_URL
        )
        self.assertEqual(
            credits_call.kwargs["headers"]["Authorization"],
            "Bearer or-inference-secret",
        )
        self.assertEqual(
            key_usage_call.kwargs["headers"]["Authorization"],
            "Bearer or-inference-secret",
        )
        serialized = json.dumps(payload)
        for secret in configured.values():
            self.assertNotIn(secret, serialized)

    def test_fal_admin_billing_key_remains_an_optional_override(self):
        configured = {
            "FAL_KEY": "fal-inference-secret",
            "FAL_ADMIN_BILLING_KEY": "fal-admin-secret",
        }
        with patch.dict(os.environ, configured, clear=True), patch.object(
            generation_budget.requests,
            "get",
            return_value=FakeResponse(
                {"credits": {"current_balance": "8.125", "currency": "usd"}}
            ),
        ) as get:
            payload = generation_budget.get_generation_budget()

        self.assertEqual(payload["providers"]["fal"]["balance_usd"], 8.125)
        self.assertEqual(get.call_args.args[0], generation_budget.FAL_ADMIN_BILLING_URL)
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"],
            "Key fal-admin-secret",
        )

    def test_fal_admin_failure_falls_back_to_normal_key(self):
        def fake_get(url, **kwargs):
            if url == generation_budget.FAL_ADMIN_BILLING_URL:
                raise requests.HTTPError("raw admin failure")
            return FakeResponse(4.75)

        configured = {
            "FAL_KEY": "fal-inference-secret",
            "FAL_ADMIN_KEY": "legacy-admin-secret",
        }
        with patch.dict(os.environ, configured, clear=True), patch.object(
            generation_budget.requests,
            "get",
            side_effect=fake_get,
        ) as get:
            payload = generation_budget.get_generation_budget()

        self.assertEqual(payload["providers"]["fal"]["balance_usd"], 4.75)
        self.assertEqual(
            [call.args[0] for call in get.call_args_list],
            [
                generation_budget.FAL_ADMIN_BILLING_URL,
                generation_budget.FAL_USER_BALANCE_URL,
            ],
        )

    def test_failed_provider_lookups_are_unavailable_and_never_zero(self):
        configured = {
            "FAL_KEY": "fal-secret",
            "OPENROUTER_API_KEY": "or-secret",
        }
        with patch.dict(os.environ, configured, clear=True), patch.object(
            generation_budget.requests,
            "get",
            side_effect=requests.ConnectionError("raw upstream detail"),
        ), self.assertLogs(generation_budget.logger, level="WARNING") as logs:
            payload = generation_budget.get_generation_budget()

        fal = payload["providers"]["fal"]
        openrouter = payload["providers"]["openrouter"]
        self.assertFalse(fal["available"])
        self.assertIsNone(fal["balance_usd"])
        self.assertFalse(openrouter["available"])
        self.assertIsNone(openrouter["balance_usd"])
        self.assertIsNone(openrouter["key_limit_remaining_usd"])
        self.assertNotIn("raw upstream detail", json.dumps(payload))
        self.assertNotIn("raw upstream detail", "\n".join(logs.output))
        for secret in configured.values():
            self.assertNotIn(secret, "\n".join(logs.output))
        self.assertEqual(payload["status"], "unavailable")

    def test_unexpected_fal_body_is_unavailable_not_zero(self):
        with patch.dict(
            os.environ,
            {"FAL_KEY": "fal-secret"},
            clear=True,
        ), patch.object(
            generation_budget.requests,
            "get",
            return_value=FakeResponse({"balance": 9.5}),
        ), self.assertLogs(generation_budget.logger, level="WARNING"):
            payload = generation_budget.get_generation_budget()

        fal = payload["providers"]["fal"]
        self.assertFalse(fal["available"])
        self.assertIsNone(fal["balance_usd"])
        self.assertEqual(fal["severity"], "unavailable")

    def test_groq_and_gemini_report_configured_without_inventing_balances(self):
        with patch.dict(
            os.environ,
            {"GROQ_API_KEY": "groq-secret", "GEMINI_API_KEY": "gemini-secret"},
            clear=True,
        ):
            payload = generation_budget.get_generation_budget()

        for provider_name in ("groq", "gemini"):
            provider = payload["providers"][provider_name]
            self.assertTrue(provider["configured"])
            self.assertIsNone(provider["available"])
            self.assertEqual(provider["status"], "configured")
            self.assertIsNone(provider["balance_usd"])
            self.assertEqual(provider["quota_status"], "check_provider_dashboard")

    def test_estimates_come_from_video_generator_constants(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = generation_budget.get_generation_budget()

        estimates = payload["estimates"]
        expected_maximum = min(
            video_generator.MAX_VIDEO_ESTIMATED_COST_USD,
            video_generator.BASE_VIDEO_ESTIMATED_COST_USD
            + video_generator.MAX_VIDEO_CLIPS_PER_VIDEO
            * video_generator.VIDEO_CLIP_ESTIMATED_COST_USD,
        )
        self.assertEqual(
            estimates["standard_video_usd"],
            video_generator.BASE_VIDEO_ESTIMATED_COST_USD,
        )
        self.assertEqual(estimates["max_motion_video_usd"], expected_maximum)
        self.assertEqual(
            estimates["max_motion_clips"],
            video_generator.MAX_VIDEO_CLIPS_PER_VIDEO,
        )

    def test_smaller_readable_balance_controls_affordability(self):
        responses = {
            generation_budget.FAL_USER_BALANCE_URL: FakeResponse(8),
            generation_budget.OPENROUTER_CREDITS_URL: FakeResponse(
                {"data": {"total_credits": 10, "total_usage": 9.8}}
            ),
            generation_budget.OPENROUTER_KEY_URL: FakeResponse(
                {"data": {"usage": 9.8, "limit": None, "limit_remaining": None}}
            ),
        }

        with patch.dict(
            os.environ,
            {"FAL_KEY": "fal-secret", "OPENROUTER_API_KEY": "or-secret"},
            clear=True,
        ), patch.object(
            generation_budget.requests,
            "get",
            side_effect=lambda url, **kwargs: responses[url],
        ):
            payload = generation_budget.get_generation_budget()

        self.assertAlmostEqual(payload["limiting_balance_usd"], 0.2)
        self.assertTrue(payload["standard_video_affordable"])
        self.assertFalse(payload["max_motion_video_affordable"])
        self.assertEqual(payload["severity"], "critical")

    def test_custom_thresholds_control_provider_and_overall_severity(self):
        responses = {
            generation_budget.FAL_USER_BALANCE_URL: FakeResponse(5.074),
            generation_budget.OPENROUTER_CREDITS_URL: FakeResponse(
                {"data": {"total_credits": 10, "total_usage": 8.37728692}}
            ),
            generation_budget.OPENROUTER_KEY_URL: FakeResponse(
                {"data": {"usage": 8.37728692}}
            ),
        }
        configured = {
            "FAL_KEY": "fal-secret",
            "OPENROUTER_API_KEY": "or-secret",
            "BUDGET_LOW_USD": "5",
            "BUDGET_CRITICAL_USD": "2",
        }
        with patch.dict(os.environ, configured, clear=True), patch.object(
            generation_budget.requests,
            "get",
            side_effect=lambda url, **kwargs: responses[url],
        ):
            payload = generation_budget.get_generation_budget()

        self.assertEqual(payload["thresholds"], {"low_usd": 5.0, "critical_usd": 2.0})
        self.assertEqual(payload["providers"]["fal"]["severity"], "ready")
        self.assertEqual(payload["providers"]["openrouter"]["severity"], "critical")
        self.assertEqual(payload["severity"], "critical")

    def test_budget_is_cached_for_sixty_seconds(self):
        first_payload = {
            "status": "ready",
            "cache_ttl_seconds": 60,
            "fetched_at": "2026-07-22T12:00:00+00:00",
            "providers": {},
            "estimates": {},
        }
        with patch.object(
            generation_budget,
            "_build_payload",
            return_value=first_payload,
        ) as build:
            first = generation_budget.get_generation_budget()
            second = generation_budget.get_generation_budget()

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        build.assert_called_once_with()

    def test_force_refresh_bypasses_the_sixty_second_cache(self):
        payload = {
            "status": "unavailable",
            "cache_ttl_seconds": 60,
            "fetched_at": "2026-07-22T12:00:00+00:00",
            "providers": {},
            "estimates": {},
        }
        with patch.object(
            generation_budget,
            "_build_payload",
            return_value=payload,
        ) as build:
            first = generation_budget.get_generation_budget()
            refreshed = generation_budget.get_generation_budget(force=True)

        self.assertFalse(first["cached"])
        self.assertFalse(refreshed["cached"])
        self.assertEqual(build.call_count, 2)

    def test_exact_fal_balance_below_standard_estimate_is_limited(self):
        with patch.dict(
            os.environ,
            {"FAL_KEY": "fal-secret"},
            clear=True,
        ), patch.object(
            generation_budget.requests,
            "get",
            return_value=FakeResponse(0.01),
        ):
            payload = generation_budget.get_generation_budget()

        self.assertEqual(payload["status"], "limited")
        self.assertFalse(payload["standard_video_affordable"])

    def test_endpoint_returns_only_the_safe_budget_payload(self):
        # Import lazily so this focused test module does not initialize the
        # application before other route tests install their database/env setup.
        import app as clipper_app

        client = clipper_app.app.test_client()
        safe_payload = {
            "status": "ready",
            "cached": False,
            "cache_ttl_seconds": 60,
            "providers": {
                "fal": {"available": True, "balance_usd": 12.5},
            },
            "estimates": {"standard_video_usd": 0.05},
        }
        with patch.object(
            clipper_app,
            "get_generation_budget",
            return_value=safe_payload,
        ) as budget:
            response = client.get("/api/generation-budget?refresh=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), safe_payload)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        budget.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
