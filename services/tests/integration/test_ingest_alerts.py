"""Scheduled ingestion connects real normalization and scoring to Isaac's alerts."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError

from drysg.alerting.notifier import NotifierDeps, process_records
from drysg.core.models import AlertMessage, SessionStatus
from drysg.core.scoring import compute_score
from drysg.data import DataUnavailableError, ingestion, normalize_weather
from drysg.db import AreaSnapshotRepository
from drysg.db.items import session_from_item, session_to_item
from handlers import ingest
from tests.conftest import SENDER
from tests.factories import make_session, make_snapshot

NOW = datetime.fromisoformat("2026-10-04T00:50:00+08:00")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "nea"


@pytest.fixture
def environment(monkeypatch, sessions_table, queue_url):
    payloads = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in FIXTURES.glob("*.json")
    }
    for batch in payloads["rainfall"]["data"]["readings"]:
        for reading in batch["data"]:
            reading["value"] = 1
    requests = []

    def respond(request):
        requests.append(request)
        payload = payloads.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=payload) if payload else httpx.Response(503)

    table = boto3.resource("dynamodb").create_table(
        TableName="AreaSnapshot",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "area", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "area", "AttributeType": "S"}],
    )
    monkeypatch.setenv("TABLE_AREA_SNAPSHOT", table.name)
    monkeypatch.setenv("TABLE_MONITORING_SESSION", sessions_table.name)
    monkeypatch.setenv("ALERT_QUEUE_URL", queue_url)
    clock = Mock()
    clock.now.return_value = NOW
    monkeypatch.setattr(ingestion, "datetime", clock)
    monkeypatch.setattr(ingest, "datetime", clock, raising=False)
    client_factory = httpx.Client
    clients = []

    def create_client(**kwargs):
        http = client_factory(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(http)
        return http

    monkeypatch.setattr(ingest.httpx, "Client", create_client)
    return {
        "payloads": payloads,
        "table": table,
        "requests": requests,
        "clients": clients,
        "clock": clock,
    }


def put_session(table, **overrides):
    session = make_session(
        started_at=NOW - timedelta(hours=1),
        **{"expires_at": NOW + timedelta(hours=1), **overrides},
    )
    table.put_item(Item=session_to_item(session))
    return session


def test_ingestion_to_email_uses_session_baseline_and_preserves_nea_call_count(
    environment,
    sessions_table,
    queue_url,
    sqs,
    alert_log_table,
    ses,
):
    session = put_session(sessions_table)
    put_session(sessions_table, session_id="stopped", status=SessionStatus.STOPPED)
    put_session(sessions_table, session_id="expired", expires_at=NOW)
    repo = AreaSnapshotRepository(boto3.resource("dynamodb"), "AreaSnapshot")
    weather = next(
        area
        for area in normalize_weather(environment["payloads"], now=NOW)
        if area.area == session.area
    )
    # The area score is unchanged, but this user's baseline still needs an alert.
    previous = make_snapshot(
        compute_score(weather.conditions).score, at=NOW - timedelta(minutes=5)
    )
    repo.save_all([previous])

    result = ingest.handler({"source": "aws.events"}, None)

    assert result["alerts"]["alerts_queued"] == 1
    assert result["alerts"]["sessions_checked"] == 1
    assert result["alerts"]["failed_message_ids"] == []
    assert len(environment["requests"]) == 6
    snapshot = repo.get(session.area)
    assert snapshot.prev_score == snapshot.result.score
    messages = sqs.receive_message(QueueUrl=queue_url).get("Messages", [])
    assert len(messages) == 1
    message = messages[0]
    alert = AlertMessage.model_validate_json(message["Body"])
    assert alert.prev_score == session.baseline_score
    assert alert.new_score == snapshot.result.score
    assert alert.snapshot_ts == snapshot.updated_at
    assert alert.new_veto

    deps = NotifierDeps(
        alert_log_table, sessions_table, ses, SENDER, "https://drysg.example"
    )
    record = {"messageId": message["MessageId"], "body": message["Body"]}
    assert process_records(deps, [record, record], NOW) == []
    assert int(ses.get_send_quota()["SentLast24Hours"]) == 1
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
    stored = session_from_item(
        sessions_table.get_item(Key={"session_id": session.session_id})["Item"]
    )
    assert stored.baseline_score == snapshot.result.score

    environment["clock"].now.return_value = NOW + timedelta(minutes=1)
    again = ingest.handler({}, None)
    assert again["alerts"]["alerts_queued"] == 0
    assert len(environment["requests"]) == 12
    assert all(client.is_closed for client in environment["clients"])


def test_skipped_area_does_not_alert_using_its_old_snapshot(
    environment,
    sessions_table,
    sqs,
    queue_url,
):
    put_session(sessions_table, area="Ang Mo Kio")
    repo = AreaSnapshotRepository(boto3.resource("dynamodb"), "AreaSnapshot")
    previous = make_snapshot(20, area="Ang Mo Kio", at=NOW - timedelta(hours=1))
    repo.save_all([previous])
    payloads = environment["payloads"]
    forecast = payloads["two-hr-forecast"]
    # Without rainfall, only an area that still has a forecast can decide its rain veto.
    del payloads["rainfall"]
    for item in forecast["data"]["items"]:
        item["forecasts"] = [
            entry for entry in item["forecasts"] if entry["area"] == "Bishan"
        ]

    result = ingest.handler({}, None)

    assert result["updated_areas"] == 1
    assert "Ang Mo Kio" in result["skipped_areas"]
    assert result["alerts"]["areas_checked"] == 1
    assert result["alerts"]["alerts_queued"] == 0
    assert repo.get("Ang Mo Kio") == previous
    assert not sqs.receive_message(QueueUrl=queue_url).get("Messages")


def test_unavailable_weather_does_not_queue_alerts(environment, sqs, queue_url):
    environment["payloads"].clear()
    with pytest.raises(DataUnavailableError):
        ingest.handler({}, None)
    assert not sqs.receive_message(QueueUrl=queue_url).get("Messages")
    assert environment["table"].scan()["Items"] == []
    assert all(client.is_closed for client in environment["clients"])


def test_failed_snapshot_write_does_not_queue_alerts(
    environment,
    sessions_table,
    sqs,
    queue_url,
    monkeypatch,
):
    put_session(sessions_table)
    failure = ClientError({"Error": {"Code": "InternalServerError"}}, "BatchWriteItem")
    monkeypatch.setattr(AreaSnapshotRepository, "save_all", Mock(side_effect=failure))
    with pytest.raises(ClientError):
        ingest.handler({}, None)
    assert not sqs.receive_message(QueueUrl=queue_url).get("Messages")


def test_trigger_exception_propagates_after_snapshot_save(environment, monkeypatch):
    failure = ClientError({"Error": {"Code": "AccessDeniedException"}}, "Query")
    trigger = Mock(side_effect=failure)
    monkeypatch.setattr(ingest, "run_alert_trigger_from_env", trigger, raising=False)
    with pytest.raises(ClientError):
        ingest.handler({}, None)
    assert environment["table"].scan()["Items"]


@pytest.mark.parametrize(
    "name",
    [
        "TABLE_AREA_SNAPSHOT",
        "TABLE_MONITORING_SESSION",
        "ALERT_QUEUE_URL",
    ],
)
def test_missing_configuration_fails_before_fetch_or_write(
    environment,
    monkeypatch,
    name,
):
    monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match=name):
        ingest.handler({}, None)
    assert environment["requests"] == []
    assert environment["table"].scan()["Items"] == []
