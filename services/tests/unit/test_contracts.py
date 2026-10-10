"""Phase 0 contract checks: spec is valid, mocks match it, models fit real NEA data."""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from openapi_schema_validator import OAS30Validator
from openapi_spec_validator import validate
from referencing import Registry, Resource

from drysg.core.models import (
    AlertMessage,
    AreaSnapshot,
    Band,
    Conditions,
    Forecast,
    Reading,
    ScoreResult,
    SubScores,
)

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
        ("recommendation_hang_now.json", "Recommendation"),
        ("recommendation_wait_psi_caution.json", "Recommendation"),
        ("recommendation_do_not_hang_veto.json", "Recommendation"),
        ("monitoring_session_active.json", "MonitoringSession"),
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
        result=ScoreResult(score=75, band=Band.HANG_NOW, sub_scores=SubScores(rain=100)),
        updated_at=item["update_timestamp"],
    )
    # Round-trip through JSON, as DynamoDB / SQS serialisation will.
    assert AreaSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_alert_message_example_in_handlers_doc_parses():
    doc = (CONTRACTS / "handlers.md").read_text()
    body = doc.split("```json", 1)[1].split("```", 1)[0]
    msg = AlertMessage.model_validate_json(body)
    assert msg.new_band is Band.DO_NOT_HANG
