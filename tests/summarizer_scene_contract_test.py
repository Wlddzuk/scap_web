"""Regression tests for the summarizer-to-renderer scene contract."""

import json

from summarizer import parse_response


def test_scene_speech_is_source_of_truth_when_script_drifts():
    result = parse_response(
        json.dumps(
            {
                "video_script": "A different narration returned by the provider.",
                "scenes": [
                    {"speech": "First beat.", "visual": "a telescope"},
                    {"speech": "Second beat.", "visual": "a distant galaxy"},
                ],
            }
        )
    )

    scene_script = " ".join(scene["speech"] for scene in result["scenes"])
    assert result["video_script"] == scene_script == "First beat. Second beat."
