"""The bundled Experience scenarios stay consistent with each other and with the scoring code."""

import json
import re
from pathlib import Path

import pytest

from drysg.core.scoring import SCORE_METHOD
from drysg.experience.replay import CATEGORIES, FRAME_COUNT
from drysg.experience.scenarios import SCENARIOS_PATH, load_scenarios

WEB_COPY = Path(__file__).resolve().parents[3] / "web" / "src" / "data" / "experienceScenarios.json"


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def test_the_api_and_the_website_ship_the_same_file():
    assert WEB_COPY.read_text(encoding="utf-8") == SCENARIOS_PATH.read_text(encoding="utf-8")


def test_scores_come_from_the_current_scoring_method(document):
    # A rebuilt percentile table changes every score: rerun scripts/build_experience_scenarios.py.
    assert document["scoreMethod"] == SCORE_METHOD


def test_every_scenario_is_a_full_hour_with_a_real_postal_code(document):
    assert document["scenarios"]
    ids = [s["id"] for s in document["scenarios"]]
    assert len(ids) == len(set(ids))
    for scenario in document["scenarios"]:
        assert scenario["category"] in CATEGORIES
        assert len(scenario["frames"]) == FRAME_COUNT
        assert scenario["postalCodes"]
        assert all(re.fullmatch(r"[0-9]{6}", code) for code in scenario["postalCodes"])


def test_each_category_matches_what_its_frames_show(document):
    for s in document["scenarios"]:
        vetoes = [f["rainVeto"] for f in s["frames"]]
        states = {f["recommendation"] for f in s["frames"]}
        match s["category"]:
            case "hang_now":
                assert states == {"HANG_NOW"}
            case "wait":
                assert states == {"WAIT"}
            case "poor_drying":
                assert states == {"DO_NOT_HANG"} and not any(vetoes)
            case "raining":
                assert all(vetoes)
            case "rain_arrives":
                assert not any(vetoes[:3]) and any(vetoes)
            case "rain_clears":
                assert vetoes[0] and not any(vetoes[-4:])


def test_alerts_describe_the_frame_they_fire_on(document):
    for s in document["scenarios"]:
        for alert in s["alerts"]:
            frame = s["frames"][alert["frame"]]
            assert 1 <= alert["frame"] < len(s["frames"])
            assert alert["newScore"] == frame["score"]
            assert alert["newVeto"] == frame["rainVeto"]
            assert alert["snapshotTs"][:16] == frame["updatedAt"][:16]
        if s["category"] == "rain_arrives":
            first_rain = next(i for i, f in enumerate(s["frames"]) if f["rainVeto"])
            assert s["alerts"][0]["frame"] == first_rain


def test_the_api_loads_the_bundle():
    scenarios = load_scenarios()
    assert scenarios
    assert all(s.alerts is not None for s in scenarios.values())
