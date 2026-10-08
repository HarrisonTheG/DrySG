"""Local integration tests for weather persistence and public data services."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from pydantic import ValidationError

from drysg.core.locations import AreaLocation, PostalLocation
from drysg.data import (
    DataUnavailableError,
    IngestionService,
    LocationService,
    NeaClient,
    OneMapClient,
    PostalCodeNotFoundError,
)
from drysg.db import AreaSnapshotRepository, PostalCacheRepository

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime.fromisoformat("2026-10-04T00:50:00+08:00")


@pytest.fixture
def database():
    with mock_aws():
        resource = boto3.resource(
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


def fixture_response(request):
    name = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(
        200, json=json.loads((FIXTURES / "nea" / f"{name}.json").read_text())
    )


def test_ingestion_roundtrip_previous_scores_and_request_count(database):
    requests = []

    def respond(request):
        requests.append(request)
        return fixture_response(request)

    repo = AreaSnapshotRepository(database, "snapshots")
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        service = IngestionService(NeaClient(http), repo)
        first = service.run(now=NOW)
        second = service.run(now=NOW + timedelta(minutes=1))
    assert len(requests) == 12
    assert first.snapshots and len(first.snapshots) == len(second.snapshots)
    assert all(item.prev_score is None for item in first.snapshots)
    previous = {item.area: item for item in first.snapshots}
    for snapshot in second.snapshots:
        assert repo.get(snapshot.area) == snapshot
        assert snapshot.prev_score == previous[snapshot.area].result.score
        assert snapshot.prev_band == previous[snapshot.area].result.band
        assert snapshot.updated_at == NOW + timedelta(minutes=1)
        assert snapshot.conditions.wind_knots.observed_at < snapshot.updated_at
    item = database.Table("snapshots").get_item(Key={"area": "Ang Mo Kio"})["Item"]
    assert isinstance(item["latitude"], Decimal)
    assert isinstance(item["updated_at"], str)
    assert {area.area for area in repo.list_areas()} == set(previous)
    assert repo.get("not an area") is None


def test_failed_refresh_keeps_previous_snapshot(database):
    repo = AreaSnapshotRepository(database, "snapshots")
    with httpx.Client(transport=httpx.MockTransport(fixture_response)) as http:
        service = IngestionService(NeaClient(http), repo)
        first = service.run(now=NOW)
        with pytest.raises(DataUnavailableError):
            service.run(now=NOW + timedelta(hours=5))
    assert repo.get(first.snapshots[0].area) == first.snapshots[0]
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(503))
        ) as http,
        pytest.raises(DataUnavailableError),
    ):
        IngestionService(NeaClient(http), repo).run(now=NOW)
    assert repo.get(first.snapshots[0].area) == first.snapshots[0]


@pytest.mark.parametrize("has_forecast", [True, False])
def test_partial_feed_failure_preserves_isaac_scoring(database, has_forecast):
    def respond(request):
        if request.url.path.endswith("rainfall"):
            return httpx.Response(503)
        response = fixture_response(request)
        if request.url.path.endswith("two-hr-forecast") and not has_forecast:
            payload = response.json()
            payload["data"]["items"] = []
            return httpx.Response(200, json=payload)
        return response

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = IngestionService(
            NeaClient(http),
            AreaSnapshotRepository(database, "snapshots"),
        ).run(now=NOW)
    assert result.missing_feeds == ("rainfall",)
    assert all(s.conditions.rainfall_mm is None for s in result.snapshots)
    if has_forecast:
        assert all(s.result.sub_scores.rain is not None for s in result.snapshots)
    else:
        assert all(s.result.degraded and s.result.score <= 69 for s in result.snapshots)


def test_database_failure_propagates(database):
    # A missing table is a persistence error, never a successful empty refresh.
    with httpx.Client(transport=httpx.MockTransport(fixture_response)) as http:
        service = IngestionService(
            NeaClient(http), AreaSnapshotRepository(database, "missing")
        )
        with pytest.raises(ClientError):
            service.run(now=NOW)


def test_postal_resolution_cache_and_nearest_area(database):
    requests = []
    payload = json.loads((FIXTURES / "onemap/search_018956.json").read_text())
    payload.pop("error")

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    areas = [
        AreaLocation(area="Far", latitude=1.4, longitude=103.7),
        AreaLocation(area="Marina", latitude=1.28, longitude=103.86),
    ]
    cache = PostalCacheRepository(database, "postals")
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        service = LocationService(OneMapClient(http, token="test"), cache)
        result = service.resolve_postal_code(" 018956 ", areas=areas, now=NOW)
        cached = service.resolve_postal_code("018956", areas=[], now=NOW)
    assert result == cached == cache.get("018956")
    assert result.area == "Marina"
    assert result.cached_at == NOW
    assert len(requests) == 1
    assert cache.get("000000") is None


@pytest.mark.parametrize(
    "postal", ["12345", "1234567", "１２３４５６", "abc123", 18956]
)
def test_invalid_postal_does_not_call_network_or_database(postal):
    client, cache = Mock(), Mock()
    with pytest.raises(ValueError):
        LocationService(client, cache).resolve_postal_code(postal, areas=[], now=NOW)
    cache.get.assert_not_called()
    client.lookup.assert_not_called()


def test_empty_catalogue_does_not_geocode(database):
    client = Mock()
    with pytest.raises(DataUnavailableError):
        LocationService(
            client, PostalCacheRepository(database, "postals")
        ).resolve_postal_code(
            "018956",
            areas=[],
            now=NOW,
        )
    client.lookup.assert_not_called()


def test_not_found_is_not_cached(database):
    cache = PostalCacheRepository(database, "postals")
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"found": 0, "results": []})
            )
        ) as http,
        pytest.raises(PostalCodeNotFoundError),
    ):
        LocationService(OneMapClient(http, token="test"), cache).resolve_postal_code(
            "000000",
            areas=[AreaLocation(area="A", latitude=1.3, longitude=103.8)],
            now=NOW,
        )
    assert cache.get("000000") is None


def test_catalogue_reads_all_scan_pages():
    database = Mock()
    table = database.Table.return_value
    table.scan.side_effect = [
        {
            "Items": [
                {"area": "B", "latitude": Decimal("1.3"), "longitude": Decimal("103.8")}
            ],
            "LastEvaluatedKey": {"area": "B"},
        },
        {
            "Items": [
                {"area": "A", "latitude": Decimal("1.4"), "longitude": Decimal("103.9")}
            ]
        },
    ]
    assert [
        area.area for area in AreaSnapshotRepository(database, "table").list_areas()
    ] == ["A", "B"]
    assert table.scan.call_args.kwargs["ExclusiveStartKey"] == {"area": "B"}


def test_postal_repository_validates_complete_document(database):
    cache = PostalCacheRepository(database, "postals")
    value = PostalLocation(
        postal_code="018956",
        latitude=1.28,
        longitude=103.86,
        address="Address",
        area="Marina",
        cached_at=datetime.now(UTC),
    )
    cache.save(value)
    assert cache.get("018956") == value


def test_corrupt_postal_document_is_not_returned(database):
    database.Table("postals").put_item(Item={"postal_code": "018956"})
    with pytest.raises(ValidationError):
        PostalCacheRepository(database, "postals").get("018956")


def test_ingest_handler_wires_real_service_and_closes_http(database, monkeypatch):
    from drysg.data import ingestion
    from handlers import ingest

    clock = Mock()
    clock.now.return_value = NOW
    monkeypatch.setattr(ingestion, "datetime", clock)
    monkeypatch.setenv("TABLE_AREA_SNAPSHOT", "snapshots")
    monkeypatch.setenv("NEA_API_KEY", "test-key")
    monkeypatch.setattr(ingest.boto3, "resource", lambda name, **kw: database)
    http = httpx.Client(transport=httpx.MockTransport(fixture_response))
    monkeypatch.setattr(ingest.httpx, "Client", lambda **kw: http)
    result = ingest.handler({"source": "aws.events"}, None)
    assert result["updated_areas"] == len(
        AreaSnapshotRepository(database, "snapshots").list_areas()
    )
    assert result["updated_areas"] > 0
    assert result["missing_feeds"] == []
    assert http.is_closed


def test_ingest_handler_requires_table_configuration(monkeypatch):
    from handlers import ingest

    monkeypatch.delenv("TABLE_AREA_SNAPSHOT", raising=False)
    with pytest.raises(ValueError, match="TABLE_AREA_SNAPSHOT"):
        ingest.handler({}, None)
