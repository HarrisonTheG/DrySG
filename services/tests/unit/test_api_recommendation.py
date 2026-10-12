"""GET /recommendation: postal code -> OneMap -> nearest area -> cached snapshot."""

import re
from datetime import timedelta
from typing import Any

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from drysg.api.app import ApiDeps, AreaCatalogue, ThrottledRefresh, create_app
from drysg.core.locations import PostalLocation
from drysg.data import DataUnavailableError, LocationService, OneMapClient, UpstreamError
from drysg.db import AreaSnapshotRepository, PostalCacheRepository
from tests.factories import NOW, make_weather_snapshot

BISHAN_POSTAL = "570150"


@pytest.fixture
def database():
    with mock_aws():
        resource: Any = boto3.resource(
            "dynamodb",
            region_name="ap-southeast-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        for name, key in (("snapshots", "area"), ("postals", "postal_code")):
            resource.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
            )
        yield resource


class OneMapStub:
    """Answers OneMap searches; every postal code resolves next to Bishan unless set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.found = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        postal = request.url.params["searchVal"]
        results = (
            [
                {
                    "POSTAL": postal,
                    "LATITUDE": "1.3550",
                    "LONGITUDE": "103.8450",
                    "ADDRESS": f"1 EXAMPLE ROAD SINGAPORE {postal}",
                }
            ]
            if self.found
            else []
        )
        return httpx.Response(200, json={"found": len(results), "results": results})


@pytest.fixture
def onemap():
    return OneMapStub()


@pytest.fixture
def snapshots(database):
    return AreaSnapshotRepository(database, "snapshots")


def cache_postal_in_bishan(database):
    """A previously resolved postal code, as left in PostalCache by an earlier request."""
    PostalCacheRepository(database, "postals").save(
        PostalLocation(
            postal_code=BISHAN_POSTAL,
            latitude=1.355,
            longitude=103.845,
            address="1 EXAMPLE ROAD",
            area="Bishan",
            cached_at=NOW,
        )
    )


def build_client(
    database,
    snapshots,
    onemap,
    *,
    token: str | None = "token",
    refresh=None,
    **kwargs,
):
    http = httpx.Client(transport=httpx.MockTransport(onemap))
    deps = ApiDeps(
        snapshots=snapshots,
        locations=LocationService(
            OneMapClient(http, token=token), PostalCacheRepository(database, "postals")
        ),
        catalogue=AreaCatalogue(snapshots),
        refresh=refresh,
        clock=lambda: NOW,
    )
    return TestClient(create_app(lambda: deps, **kwargs), raise_server_exceptions=False)


@pytest.fixture
def client(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan"), make_weather_snapshot("Bedok")])
    return build_client(database, snapshots, onemap)


def get(client, postal=BISHAN_POSTAL):
    return client.get("/recommendation", params={"postalCode": postal})


# --- Success ------------------------------------------------------------------


def test_returns_recommendation_for_postal_code(client):
    response = get(client)
    assert response.status_code == 200
    body = response.json()
    assert body["postalCode"] == BISHAN_POSTAL
    assert body["areaName"] == "Bishan"  # OneMap point is next to Bishan, not Bedok
    assert body["recommendation"] in {"HANG_NOW", "WAIT", "DO_NOT_HANG"}
    assert 0 <= body["score"] <= 100
    assert body["rainVeto"] is False
    assert body["airQualityWarning"] is False
    assert body["percentileDescription"].startswith(f"Drier than {body['score']}%")


def test_response_matches_the_frontend_contract_keys(client):
    body = get(client).json()
    assert set(body) == {
        "postalCode",
        "areaName",
        "score",
        "recommendation",
        "summary",
        "percentileDescription",
        "updatedAt",
        "conditions",
        "rainVeto",
        "airQualityWarning",
        "stationSources",
    }
    assert set(body["conditions"]) == {
        "rainfallMm",
        "temperatureC",
        "humidityPercent",
        "windSpeedMs",
        "forecast",
        "psi",
        "psiBand",
    }
    assert set(body["stationSources"]) == {"rainfall", "humidity", "temperature", "wind"}


def test_conditions_and_units(client):
    conditions = get(client).json()["conditions"]
    assert conditions["temperatureC"] == 31.0
    assert conditions["humidityPercent"] == 70.0
    assert conditions["windSpeedMs"] == 3.1  # 6 kt * 0.514444 = 3.09 m/s, one decimal
    assert conditions["rainfallMm"] == 0.0
    assert conditions["forecast"] == "Fair (Day)"
    assert (conditions["psi"], conditions["psiBand"]) == (48.0, "Good")


def test_station_sources_are_reported(client):
    assert get(client).json()["stationSources"] == {
        "rainfall": "S07",
        "humidity": "S109",
        "temperature": "S24",
        "wind": "S81",
    }


def test_timestamp_uses_singapore_offset(client):
    assert re.search(r"\+08:00$", get(client).json()["updatedAt"])


def test_postal_code_is_trimmed(client):
    assert get(client, f" {BISHAN_POSTAL} ").status_code == 200


def test_score_matches_scorer(client):
    from drysg.core.scoring import compute_score

    expected = compute_score(make_weather_snapshot("Bishan").conditions)
    body = get(client).json()
    assert body["score"] == expected.score


# --- Rain veto and air quality ------------------------------------------------


def test_rain_vetoes_the_recommendation(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan", rain=0.6)])
    body = get(build_client(database, snapshots, onemap)).json()
    assert body["rainVeto"] is True
    assert body["recommendation"] == "DO_NOT_HANG"
    assert body["percentileDescription"] is None
    assert "Do not hang" in body["summary"]
    assert 0 <= body["score"] <= 100  # score is still returned


def test_forecast_rain_vetoes(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan", rain=0.0, forecast="Thundery Showers")])
    body = get(build_client(database, snapshots, onemap)).json()
    assert (body["rainVeto"], body["recommendation"]) == (True, "DO_NOT_HANG")


def test_unhealthy_air_warns_without_changing_recommendation(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan", psi=48)])
    clean = get(build_client(database, snapshots, onemap)).json()
    snapshots.save_all([make_weather_snapshot("Bishan", psi=150)])
    hazy = get(build_client(database, snapshots, onemap)).json()
    assert hazy["airQualityWarning"] is True
    assert hazy["conditions"]["psiBand"] == "Unhealthy"
    assert "Air quality is unhealthy (PSI 150)" in hazy["summary"]
    assert (hazy["score"], hazy["recommendation"]) == (clean["score"], clean["recommendation"])


def test_missing_optional_feeds_are_null(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan", rain=None, psi=None)])
    body = get(build_client(database, snapshots, onemap)).json()
    assert body["conditions"]["rainfallMm"] is None
    assert (body["conditions"]["psi"], body["conditions"]["psiBand"]) == (None, None)
    assert body["stationSources"]["rainfall"] is None
    assert body["airQualityWarning"] is False


# --- Input validation ---------------------------------------------------------


@pytest.mark.parametrize(
    "postal", ["", " ", "12345", "1234567", "abcdef", "57015a", "５７０１５０"]
)
def test_invalid_postal_code_is_400(client, postal):
    response = get(client, postal)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_POSTAL_CODE"


def test_missing_postal_code_is_400(client):
    response = client.get("/recommendation")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_POSTAL_CODE"


# --- Upstream and data problems -----------------------------------------------


def test_unknown_postal_code_is_404(database, snapshots, onemap, client):
    onemap.found = False
    response = get(client, "999999")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "POSTAL_CODE_NOT_FOUND",
        "message": "No area found for postal code 999999",
    }


def test_onemap_outage_is_503_not_404(client, onemap):
    onemap.status = 500
    response = get(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_missing_onemap_token_is_503(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan")])
    response = get(build_client(database, snapshots, onemap, token=None))
    assert response.status_code == 503
    assert onemap.requests == []  # never calls OneMap without a token


def test_stale_snapshot_is_503(database, snapshots, onemap):
    old = NOW - timedelta(minutes=31)
    snapshots.save_all([make_weather_snapshot("Bishan", at=old)])
    response = get(build_client(database, snapshots, onemap))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_snapshot_just_inside_the_limit_is_served(database, snapshots, onemap):
    snapshots.save_all([make_weather_snapshot("Bishan", at=NOW - timedelta(minutes=30))])
    assert get(build_client(database, snapshots, onemap)).status_code == 200


def test_area_without_snapshot_is_503(database, snapshots, onemap):
    cache_postal_in_bishan(database)
    snapshots.save_all([make_weather_snapshot("Bedok")])  # Bishan has no snapshot
    response = get(build_client(database, snapshots, onemap))
    assert response.status_code == 503


def test_postal_lookup_is_cached(client, onemap):
    get(client)
    get(client)
    assert len(onemap.requests) == 1


# --- Cold start fallback ------------------------------------------------------


def test_cold_start_refreshes_once_when_area_has_no_snapshot(database, snapshots, onemap):
    cache_postal_in_bishan(database)
    snapshots.save_all([make_weather_snapshot("Bedok")])
    calls = []

    def refresh():
        calls.append(1)
        snapshots.save_all([make_weather_snapshot("Bishan")])

    response = get(build_client(database, snapshots, onemap, refresh=refresh))
    assert response.status_code == 200
    assert response.json()["areaName"] == "Bishan"
    assert len(calls) == 1


def test_cold_start_with_empty_catalogue_refreshes_then_resolves(database, snapshots, onemap):
    calls = []

    def refresh():
        calls.append(1)
        snapshots.save_all([make_weather_snapshot("Bishan"), make_weather_snapshot("Bedok")])

    response = get(build_client(database, snapshots, onemap, refresh=refresh))
    assert response.status_code == 200
    assert len(calls) == 1


def test_cold_start_with_failing_refresh_is_500_not_a_wrong_answer(database, snapshots, onemap):
    def refresh():
        raise RuntimeError("boom")

    response = get(build_client(database, snapshots, onemap, refresh=refresh))
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    "error",
    [
        UpstreamError("NEA timed out"),
        DataUnavailableError("No fresh scoring inputs are available"),
        ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem"),
    ],
)
def test_cold_start_refresh_without_data_is_503(database, snapshots, onemap, error):
    cache_postal_in_bishan(database)
    snapshots.save_all([make_weather_snapshot("Bedok")])  # catalogue exists, Bishan has none

    def refresh():
        raise error

    response = get(build_client(database, snapshots, onemap, refresh=refresh))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_empty_catalogue_without_refresh_is_503(database, snapshots, onemap):
    assert get(build_client(database, snapshots, onemap)).status_code == 503


# --- Misc ---------------------------------------------------------------------


def test_unexpected_error_is_a_generic_500(database, snapshots, onemap):
    def broken():
        raise RuntimeError("secret detail")

    app = create_app(broken)
    response = TestClient(app, raise_server_exceptions=False).get(
        "/recommendation", params={"postalCode": BISHAN_POSTAL}
    )
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Something went wrong"}
    }


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_allows_the_configured_origin(database, snapshots, onemap):
    client = build_client(
        database, snapshots, onemap, cors_origins=["https://app.example.com"]
    )
    snapshots.save_all([make_weather_snapshot("Bishan")])
    response = client.get(
        "/recommendation",
        params={"postalCode": BISHAN_POSTAL},
        headers={"Origin": "https://app.example.com"},
    )
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


def test_unexpected_500_still_has_cors_headers(database, snapshots, onemap):
    def broken():
        raise RuntimeError("boom")

    app = create_app(broken, cors_origins=["https://app.example.com"])
    response = TestClient(app, raise_server_exceptions=False).get(
        "/recommendation",
        params={"postalCode": BISHAN_POSTAL},
        headers={"Origin": "https://app.example.com"},
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


def test_cold_start_refresh_is_throttled_across_requests(database, snapshots, onemap):
    # The area never gets a snapshot, so every request would otherwise refetch NEA.
    cache_postal_in_bishan(database)
    snapshots.save_all([make_weather_snapshot("Bedok")])
    calls = []
    client = build_client(
        database, snapshots, onemap, refresh=ThrottledRefresh(lambda: calls.append(1))
    )
    assert [get(client).status_code for _ in range(3)] == [503, 503, 503]
    assert len(calls) == 1


def test_throttled_refresh_runs_again_after_the_interval():
    now = [0.0]
    calls = []
    refresh = ThrottledRefresh(lambda: calls.append(1), min_interval_s=60, clock=lambda: now[0])
    refresh()
    now[0] = 59
    refresh()
    now[0] = 61
    refresh()
    assert len(calls) == 2


def test_throttled_refresh_does_not_retry_a_failure_immediately():
    calls = []

    def failing():
        calls.append(1)
        raise RuntimeError("boom")

    refresh = ThrottledRefresh(failing, min_interval_s=60, clock=lambda: 0.0)
    with pytest.raises(RuntimeError):
        refresh()
    refresh()  # throttled: no second attempt, no exception
    assert len(calls) == 1


def test_snapshot_missing_required_inputs_is_503_not_500(database, snapshots, onemap):
    snapshot = make_weather_snapshot("Bishan")
    snapshot.conditions.temp_c = None  # as written by older code
    snapshots.save_all([snapshot])
    response = get(build_client(database, snapshots, onemap))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_docs_can_be_disabled_for_deployment(database, snapshots, onemap):
    local = build_client(database, snapshots, onemap)
    deployed = build_client(database, snapshots, onemap, enable_docs=False)
    assert local.get("/docs").status_code == 200
    assert deployed.get("/docs").status_code == 404
    assert deployed.get("/openapi.json").status_code == 404
    assert deployed.get("/health").status_code == 200
