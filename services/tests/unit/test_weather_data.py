"""Weather normalization must preserve geography, units and source freshness."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from drysg.core.scoring import compute_score
from drysg.data import DataUnavailableError, normalize_weather

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "nea"
NOW = datetime.fromisoformat("2026-10-04T00:50:00+08:00")


@pytest.fixture
def payloads():
    return {p.stem: json.loads(p.read_text()) for p in FIXTURES.glob("*.json")}


def test_recorded_feeds_produce_area_conditions(payloads):
    areas = normalize_weather(payloads, now=NOW)
    ang_mo_kio = next(a for a in areas if a.area == "Ang Mo Kio")
    assert len(areas) == len(payloads["two-hr-forecast"]["data"]["area_metadata"])
    assert ang_mo_kio.conditions.forecast.text == "Fair (Night)"
    assert ang_mo_kio.conditions.wind_knots.value == 2.8
    assert ang_mo_kio.conditions.wind_knots.source == "S109"
    assert ang_mo_kio.conditions.psi.source == ang_mo_kio.region


def test_nearest_station_is_selected_per_metric_and_latest_reading(payloads):
    data = payloads["rainfall"]["data"]
    data["stations"] = [
        {"id": "near", "location": {"latitude": 1.375, "longitude": 103.839}},
        {"id": "far", "location": {"latitude": 1.3, "longitude": 103.7}},
    ]
    data["readings"] = [
        {
            "timestamp": NOW.isoformat(),
            "data": [
                {"stationId": "far", "value": 10},
                {"stationId": "near", "value": 0.5},
            ],
        },
        {
            "timestamp": (NOW - timedelta(minutes=5)).isoformat(),
            "data": [
                {"stationId": "near", "value": 0},
            ],
        },
    ]
    area = next(
        a for a in normalize_weather(payloads, now=NOW) if a.area == "Ang Mo Kio"
    )
    assert area.conditions.rainfall_mm.source == "near"
    assert area.conditions.rainfall_mm.value == 0.5
    assert area.conditions.humidity_pct.source != "near"


@pytest.mark.parametrize("age", [timedelta(minutes=16), timedelta(minutes=-1)])
def test_stale_and_future_observations_are_excluded(payloads, age):
    for row in payloads["rainfall"]["data"]["readings"]:
        row["timestamp"] = (NOW - age).isoformat()
    assert all(
        a.conditions.rainfall_mm is None for a in normalize_weather(payloads, now=NOW)
    )


def test_forecast_expiry_and_psi_freshness(payloads):
    for item in payloads["two-hr-forecast"]["data"]["items"]:
        item["valid_period"]["end"] = NOW.isoformat()
    for item in payloads["psi"]["data"]["items"]:
        item["timestamp"] = (NOW - timedelta(hours=3)).isoformat()
    areas = normalize_weather(payloads, now=NOW)
    assert all(a.conditions.forecast is None for a in areas)
    assert all(a.conditions.psi is None for a in areas)
    assert any(a.conditions.humidity_pct is not None for a in areas)


def test_missing_optional_feeds_still_normalize(payloads):
    del payloads["psi"]
    del payloads["relative-humidity"]
    areas = normalize_weather(payloads, now=NOW)
    assert areas
    assert all(a.conditions.psi is None for a in areas)
    assert all(a.conditions.humidity_pct is None for a in areas)


def test_wrong_units_and_invalid_values_are_not_scored(payloads):
    payloads["wind-speed"]["data"]["readingUnit"] = "km/h"
    for row in payloads["relative-humidity"]["data"]["readings"]:
        for reading in row["data"]:
            reading["value"] = 101
    areas = normalize_weather(payloads, now=NOW)
    assert all(a.conditions.wind_knots is None for a in areas)
    assert all(a.conditions.humidity_pct is None for a in areas)


def test_missing_catalogue_fails_without_mutating_input(payloads):
    del payloads["two-hr-forecast"]
    before = copy.deepcopy(payloads)
    with pytest.raises(DataUnavailableError):
        normalize_weather(payloads, now=NOW)
    assert payloads == before


def test_naive_clock_is_rejected(payloads):
    with pytest.raises(ValueError, match="timezone"):
        normalize_weather(payloads, now=NOW.replace(tzinfo=None))


def test_partially_invalid_catalogue_aborts_instead_of_losing_areas(payloads):
    payloads["two-hr-forecast"]["data"]["area_metadata"][0]["label_location"] = {}
    with pytest.raises(DataUnavailableError):
        normalize_weather(payloads, now=NOW)


def test_unknown_station_null_and_negative_readings_are_excluded(payloads):
    data = payloads["rainfall"]["data"]
    for batch in data["readings"]:
        batch["data"] = [
            {"stationId": "unknown", "value": 500},
            {"stationId": "S109", "value": None},
            {"stationId": "S104", "value": -1},
        ]
    assert all(
        area.conditions.rainfall_mm is None
        for area in normalize_weather(payloads, now=NOW)
    )


def test_future_forecasts_and_psi_are_excluded(payloads):
    for item in payloads["two-hr-forecast"]["data"]["items"]:
        item["update_timestamp"] = (NOW + timedelta(minutes=1)).isoformat()
    for item in payloads["psi"]["data"]["items"]:
        item["timestamp"] = (NOW + timedelta(hours=1)).isoformat()
    areas = normalize_weather(payloads, now=NOW)
    assert all(area.conditions.forecast is None for area in areas)
    assert all(area.conditions.psi is None for area in areas)


def test_freshness_window_is_configurable(payloads):
    for batch in payloads["rainfall"]["data"]["readings"]:
        batch["timestamp"] = (NOW - timedelta(minutes=20)).isoformat()
    areas = normalize_weather(
        payloads, now=NOW, observation_max_age=timedelta(minutes=25)
    )
    assert all(area.conditions.rainfall_mm is not None for area in areas)


@pytest.mark.parametrize("bad_record", ["reading", "station", "batch"])
def test_one_malformed_record_cannot_hide_valid_rain(payloads, bad_record):
    data = payloads["rainfall"]["data"]
    for batch in data["readings"]:
        for reading in batch["data"]:
            reading["value"] = 1
    if bad_record == "reading":
        data["readings"][0]["data"][0]["value"] = "invalid"
    elif bad_record == "station":
        data["stations"][0]["location"]["latitude"] = "invalid"
    else:
        data["readings"].append({"timestamp": "invalid", "data": []})
    areas = normalize_weather(payloads, now=NOW)
    assert all(area.conditions.rainfall_mm is not None for area in areas)
    assert all(area.conditions.rainfall_mm.value == 1 for area in areas)
    assert all(compute_score(area.conditions).veto for area in areas)
