"""Phase 0 contract checks: spec is valid, mocks match it, models fit real NEA data."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_schema_validator import OAS30Validator
from openapi_spec_validator import validate
from referencing import Registry, Resource

from drysg.api.app import ApiDeps, create_app
from drysg.api.recommendation import to_recommendation
from drysg.core.models import (
    AlertMessage,
    AreaSnapshot,
    Band,
    Conditions,
    Forecast,
    Reading,
    ScoreResult,
)
from tests.factories import make_weather_snapshot

REPO = Path(__file__).resolve().parents[3]
CONTRACTS = REPO / "docs" / "contracts"
NEA = Path(__file__).resolve().parents[1] / "fixtures" / "nea"


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())


def test_openapi_spec_is_valid(spec):
    validate(spec)


@pytest.mark.parametrize(
    ("mock", "schema"),
    [
        ("recommendation_hang_now.json", "DryingRecommendation"),
        ("recommendation_wait_psi_caution.json", "DryingRecommendation"),
        ("recommendation_do_not_hang_veto.json", "DryingRecommendation"),
        ("laundry_session_active.json", "LaundrySession"),
        ("user_status.json", "UserStatus"),
        ("error_postal_not_found.json", "Error"),
    ],
)
def test_mocks_match_spec(spec, mock, schema):
    registry = Registry().with_resource("urn:drysg", Resource.opaque(spec))
    validator = OAS30Validator(
        {"$ref": f"urn:drysg#/components/schemas/{schema}"}, registry=registry
    )
    validator.validate(json.loads((CONTRACTS / "mocks" / mock).read_text()))


def _first_reading(endpoint: str) -> Reading:
    data = json.loads((NEA / f"{endpoint}.json").read_text())["data"]
    latest = data["readings"][0]
    first = latest["data"][0]
    return Reading(
        value=first["value"],
        observed_at=datetime.fromisoformat(latest["timestamp"]),
        source=first["stationId"],
    )


def test_models_accept_real_nea_shapes():
    fc = json.loads((NEA / "two-hr-forecast.json").read_text())["data"]
    item = fc["items"][0]
    area = fc["area_metadata"][0]
    psi = json.loads((NEA / "psi.json").read_text())["data"]["items"][0]

    conditions = Conditions(
        rainfall_mm=_first_reading("rainfall"),
        humidity_pct=_first_reading("relative-humidity"),
        temp_c=_first_reading("air-temperature"),
        wind_knots=_first_reading("wind-speed"),
        forecast=Forecast(
            text=item["forecasts"][0]["forecast"],
            valid_from=item["valid_period"]["start"],
            valid_to=item["valid_period"]["end"],
        ),
        psi=Reading(
            value=psi["readings"]["psi_twenty_four_hourly"]["central"],
            observed_at=psi["timestamp"],
            source="central",
        ),
    )
    snapshot = AreaSnapshot(
        area=area["name"],
        region="central",
        latitude=area["label_location"]["latitude"],
        longitude=area["label_location"]["longitude"],
        conditions=conditions,
        result=ScoreResult(score=75, band=Band.HANG_NOW),
        updated_at=item["update_timestamp"],
    )
    # Round-trip through JSON, as DynamoDB / SQS serialisation will.
    assert AreaSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_alert_message_example_in_handlers_doc_parses():
    doc = (CONTRACTS / "handlers.md").read_text(encoding="utf-8")
    body = doc.split("```json", 1)[1].split("```", 1)[0]
    msg = AlertMessage.model_validate_json(body)
    assert msg.new_band is Band.DO_NOT_HANG


# --- The implementation must match the spec -----------------------------------

AT = datetime.fromisoformat("2026-10-04T12:35:00+08:00")
MOCK_SCENARIOS = {
    "recommendation_hang_now.json": dict(temp=31.0, humidity=60.0, wind_knots=5.0, psi=48),
    "recommendation_wait_psi_caution.json": dict(
        temp=29.5, humidity=75.0, wind_knots=4.0, psi=132
    ),
    "recommendation_do_not_hang_veto.json": dict(
        rain=0.6, temp=29.0, humidity=82.0, wind_knots=3.0, psi=48
    ),
}


def _validator(spec: dict, schema: str) -> Any:
    registry = Registry().with_resource("urn:drysg", Resource.opaque(spec))
    return OAS30Validator({"$ref": f"urn:drysg#/components/schemas/{schema}"}, registry=registry)


def _recommendation(**conditions) -> dict:
    snapshot = make_weather_snapshot("Bishan", at=AT, **conditions)
    snapshot = snapshot.model_copy(update={"area": "Hougang"})
    return to_recommendation(snapshot, "530123").model_dump(mode="json")


@pytest.mark.parametrize("mock", MOCK_SCENARIOS)
def test_mocks_are_what_the_code_produces(mock):
    """If scoring or the response mapping changes, regenerate the mocks."""
    expected = json.loads((CONTRACTS / "mocks" / mock).read_text())
    assert _recommendation(**MOCK_SCENARIOS[mock]) == expected


def test_mocks_cover_all_three_states():
    states = {
        json.loads((CONTRACTS / "mocks" / name).read_text())["recommendation"]
        for name in MOCK_SCENARIOS
    }
    assert states == {"HANG_NOW", "WAIT", "DO_NOT_HANG"}


@pytest.mark.parametrize(
    "conditions",
    [
        dict(),
        dict(rain=0.6),
        dict(forecast="Thundery Showers"),
        dict(psi=250),
        dict(rain=None, psi=None),  # optional feeds missing -> nulls
        dict(forecast=None),
    ],
)
def test_real_responses_validate_against_the_spec(spec, conditions):
    _validator(spec, "DryingRecommendation").validate(_recommendation(**conditions))


def test_generated_openapi_matches_the_contract_file(spec):
    """FastAPI's own schema must expose the same names the contract documents."""
    app = create_app(lambda: ApiDeps.__new__(ApiDeps))
    generated = app.openapi()
    documented_params = spec["paths"]["/recommendation"]["get"]["parameters"]
    generated_params = generated["paths"]["/recommendation"]["get"]["parameters"]
    assert [p["name"] for p in generated_params] == [p["name"] for p in documented_params]

    for name in ("DryingRecommendation", "WeatherConditions", "StationSources"):
        assert set(generated["components"]["schemas"][name]["properties"]) == set(
            spec["components"]["schemas"][name]["properties"]
        ), name
    assert set(generated["components"]["schemas"]["RecommendationState"]["enum"]) == set(
        spec["components"]["schemas"]["RecommendationState"]["enum"]
    )


def test_error_codes_used_by_the_api_are_in_the_spec(spec):
    error = spec["components"]["schemas"]["Error"]["properties"]["error"]
    documented = set(error["properties"]["code"]["enum"])
    source = (REPO / "services" / "drysg" / "api" / "app.py").read_text()
    used = set(re.findall(r'"([A-Z_]{6,})"', source))
    codes = {c for c in used if c.endswith(("_POSTAL_CODE", "_FOUND", "_UNAVAILABLE", "_ERROR"))}
    assert codes <= documented
