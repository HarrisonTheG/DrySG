"""handlers.api.handler: API Gateway HTTP API (payload v2) event in, proxy response out."""

import json
from typing import Any

import boto3
import httpx
import pytest
from moto import mock_aws

from drysg.api.app import ApiDeps, AreaCatalogue
from drysg.api.deps import build_deps
from drysg.data import LocationService, OneMapClient
from drysg.db import AreaSnapshotRepository, PostalCacheRepository, SessionRepository
from drysg.sessions import SessionService
from handlers import api
from tests.factories import NOW, make_weather_snapshot

NO_CONTEXT: Any = None  # the handler never reads the Lambda context


def http_api_event(
    path: str,
    query: str = "",
    origin: str | None = None,
    *,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    headers = {"host": "abc.execute-api.ap-southeast-1.amazonaws.com"}
    if origin:
        headers["origin"] = origin
    if body is not None:
        headers["content-type"] = "application/json"
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": headers,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc",
            "domainName": "abc.execute-api.ap-southeast-1.amazonaws.com",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "pytest",
            },
            "requestId": "req-1",
            "stage": "$default",
            "time": "12/Oct/2026:00:00:00 +0000",
            "timeEpoch": 1791763200000,
        },
        "isBase64Encoded": False,
        "body": None if body is None else json.dumps(body),
    }


@pytest.fixture
def deps():
    def onemap(request: httpx.Request) -> httpx.Response:
        postal = request.url.params["searchVal"]
        result = {
            "POSTAL": postal,
            "LATITUDE": "1.3550",
            "LONGITUDE": "103.8450",
            "ADDRESS": f"1 EXAMPLE ROAD SINGAPORE {postal}",
        }
        return httpx.Response(200, json={"found": 1, "results": [result]})

    with mock_aws():
        dynamodb: Any = boto3.resource(
            "dynamodb",
            region_name="ap-southeast-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        for name, key in (
            ("snapshots", "area"),
            ("postals", "postal_code"),
            ("sessions", "session_id"),
            ("users", "email"),
        ):
            dynamodb.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
            )
        snapshots = AreaSnapshotRepository(dynamodb, "snapshots")
        dynamodb.Table("users").put_item(
            Item={"email": "user@example.com", "verified": True}
        )
        snapshots.save_all([make_weather_snapshot("Bishan")])
        http = httpx.Client(transport=httpx.MockTransport(onemap))
        yield ApiDeps(
            snapshots=snapshots,
            locations=LocationService(
                OneMapClient(http, token="token"),
                PostalCacheRepository(dynamodb, "postals"),
            ),
            catalogue=AreaCatalogue(snapshots),
            clock=lambda: NOW,
            sessions=SessionService(SessionRepository(dynamodb, "sessions", "users")),
        )


@pytest.fixture(autouse=True)
def _override(deps):
    api.app.dependency_overrides[build_deps] = lambda: deps
    yield
    api.app.dependency_overrides.clear()


def test_recommendation_through_api_gateway_event():
    response = api.handler(
        http_api_event("/recommendation", "postalCode=570150"), NO_CONTEXT
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert (body["postalCode"], body["areaName"]) == ("570150", "Bishan")
    assert body["recommendation"] in {"HANG_NOW", "WAIT", "DO_NOT_HANG"}


def test_validation_error_through_api_gateway_event():
    response = api.handler(
        http_api_event("/recommendation", "postalCode=12"), NO_CONTEXT
    )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "INVALID_POSTAL_CODE"


def test_deployed_app_does_not_publish_docs():
    for path in ("/docs", "/openapi.json"):
        assert api.handler(http_api_event(path), NO_CONTEXT)["statusCode"] == 404


def test_health_through_api_gateway_event():
    response = api.handler(http_api_event("/health"), NO_CONTEXT)
    assert (response["statusCode"], json.loads(response["body"])) == (
        200,
        {"status": "ok"},
    )


def test_session_lifecycle_through_api_gateway_events():
    response = api.handler(
        http_api_event(
            "/laundry-sessions",
            method="POST",
            body={"email": "user@example.com", "postalCode": "570150"},
        ),
        NO_CONTEXT,
    )
    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    path = f"/laundry-sessions/{body['id']}"
    fetched = api.handler(http_api_event(path), NO_CONTEXT)
    assert fetched["statusCode"] == 200
    assert json.loads(fetched["body"]) == body
    stopped = api.handler(http_api_event(path, method="DELETE"), NO_CONTEXT)
    assert stopped["statusCode"] == 200
    assert json.loads(stopped["body"]) == {**body, "active": False}
