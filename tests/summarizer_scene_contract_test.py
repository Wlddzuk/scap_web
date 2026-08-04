"""Regression tests for the summarizer-to-renderer scene contract."""

import json

from models import find_matching_hook_index, valid_hook_index
from summarizer import find_summary_contract_issues, parse_response


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


def test_scene_contract_keeps_discovery_query_separate_from_location_query():
    result = parse_response(
        json.dumps(
            {
                "video_script": "Researchers in Jerusalem found breaks in tumor DNA.",
                "scenes": [{
                    "speech": "Researchers in Jerusalem found breaks in tumor DNA.",
                    "visual": "a tumor DNA strand breaking inside a super-enhancer",
                    "referent": "object",
                    "referent_query": "Hebrew University Jerusalem",
                    "visual_role": "discovery",
                    "evidence_query": "tumor DNA super-enhancer breaks",
                }],
            }
        )
    )

    scene = result["scenes"][0]
    assert scene["referent_query"] == "Hebrew University Jerusalem"
    assert scene["visual_role"] == "discovery"
    assert scene["evidence_query"] == "tumor DNA super-enhancer breaks"


def test_old_scene_contract_derives_an_evidence_query():
    result = parse_response(
        json.dumps(
            {
                "video_script": "The DNA breaks clustered together.",
                "scenes": [{
                    "speech": "The DNA breaks clustered together.",
                    "visual": "a DNA strand with clustered breaks",
                    "referent": "abstract",
                    "focus_label": "CLUSTERED DNA BREAKS",
                    "graphic_payload": "Clustered DNA breaks",
                }],
            }
        )
    )

    scene = result["scenes"][0]
    assert scene["visual_role"] == "discovery"
    assert scene["evidence_query"] == "Clustered DNA breaks"


def test_hook_attribution_requires_an_exact_scene_one_match():
    variants = [
        "First exact hook.",
        "A different hook.",
        "A third hook.",
    ]

    assert find_matching_hook_index(
        variants,
        [{"speech": "  A different hook.  ", "visual": "a lab"}],
    ) == 1
    assert find_matching_hook_index(
        variants,
        [{"speech": "A different hook!", "visual": "a lab"}],
    ) is None
    assert find_matching_hook_index(variants, []) is None


def test_best_hook_index_validation_is_strict_and_in_range():
    variants = ["One", "Two", "Three"]

    assert valid_hook_index(0, variants) == 0
    assert valid_hook_index(2, variants) == 2
    assert valid_hook_index(True, variants) is None
    assert valid_hook_index("1", variants) is None
    assert valid_hook_index(3, variants) is None


def test_parse_response_does_not_coerce_malformed_best_hook_index():
    result = parse_response(
        json.dumps(
            {
                "hook_variants": ["Hook zero.", "Hook one.", "Hook two."],
                "best_hook_index": "1",
                "scenes": [
                    {"speech": "Hook one.", "visual": "a laboratory"}
                ],
                "video_script": "Hook one.",
            }
        )
    )

    assert result["best_hook_index"] == "1"
    assert (
        "best_hook_index must be a strict in-range integer"
        in find_summary_contract_issues(result)
    )


def test_retention_fields_are_normalized_and_cta_is_spoken_last():
    result = parse_response(
        json.dumps(
            {
                "cover_line": "  a planet changes everything today! ",
                "cta_question": "Would you live on this planet.",
                "search_caption": (
                    "what scientists found in the atmosphere of K2-18b"
                ),
                "series_lane": "SPACE",
                "hashtags": [
                    " #Exoplanet ",
                    "#Space Science",
                    "#EXOPLANET",
                    "Astronomy",
                    "#ExtraTag",
                ],
                "scenes": [
                    {
                        "speech": "This planet changed the search for life.",
                        "visual": "an exoplanet passes in front of its star",
                    },
                    {
                        "speech": "Its atmosphere contains carbon molecules.",
                        "visual": "a telescope separates bands of starlight",
                    },
                ],
                "video_script": "Provider copy that should be replaced.",
            }
        )
    )

    assert result["cover_line"] == "A PLANET CHANGES EVERYTHING TODAY"
    assert result["cta_question"] == "Would you live on this planet?"
    assert result["search_caption"] == (
        "what scientists found in the atmosphere of K2-18b."
    )
    assert result["series_lane"] == "space"
    assert result["hashtags"] == ["#Exoplanet", "#SpaceScience", "#Astronomy"]
    assert result["scenes"][-1]["speech"].endswith(result["cta_question"])
    assert result["video_script"].endswith(result["cta_question"])
    assert result["video_script"] == " ".join(
        scene["speech"] for scene in result["scenes"]
    )


def test_hashtag_fallbacks_always_produce_exactly_three_unique_tags():
    result = parse_response(
        json.dumps(
            {
                "series_lane": "future_tech",
                "hashtags": ["AI", "#ai"],
                "cta_question": "Would you trust this machine?",
                "video_script": "The machine learned the task.",
            }
        )
    )

    assert result["hashtags"] == ["#AI", "#FutureTech", "#Technology"]
    assert result["video_script"].endswith("Would you trust this machine?")


def test_spoken_final_question_recovers_missing_cta_field():
    result = parse_response(
        json.dumps(
            {
                "series_lane": "human_body",
                "scenes": [
                    {
                        "speech": "The treatment restored movement.",
                        "visual": "a patient closes their hand",
                    },
                    {
                        "speech": "Would you volunteer for this trial?",
                        "visual": "a consent form beside a neural implant",
                    },
                ],
            }
        )
    )

    assert result["cta_question"] == "Would you volunteer for this trial?"
    assert result["video_script"].endswith(result["cta_question"])
