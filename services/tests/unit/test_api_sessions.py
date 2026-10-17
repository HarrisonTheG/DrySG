"""Session HTTP contract, persistence and replacement against mocked AWS tables."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import boto3
import pytest
from fastapi.testclient import TestClient

from drysg.alerting.notifier import NotifierDeps, Outcome, handle_alert
from drysg.alerting.trigger import run_alert_trigger
from drysg.api.app import create_app
from drysg.api.deps import build_deps
from drysg.core.locations import PostalLocation
from drysg.core.models import AlertMessage
from drysg.core.scoring import SCORE_METHOD
from drysg.db import PostalCacheRepository, SessionRepository
from drysg.sessions import InvalidEmailError, SessionService
from tests.conftest import SENDER
from tests.factories import NOW, make_snapshot, make_weather_snapshot

EMAIL = "user@example.com"
POSTAL = "570150"


@pytest.fixture
def session_api(aws, monkeypatch):
    database = boto3.resource("dynamodb")
    monkeypatch.setenv("ONEMAP_TOKEN", "")
    for name, key, env in (
        ("snapshots", "area", "TABLE_AREA_SNAPSHOT"),
        ("postals", "postal_code", "TABLE_POSTAL_CACHE"),
        ("users", "email", "TABLE_USER"),
        ("sessions", "session_id", "TABLE_MONITORING_SESSION"),
    ):
        extra = {}
        attributes = [{"AttributeName": key, "AttributeType": "S"}]
        if name == "sessions":
            attributes += [
                {"AttributeName": "area", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
            ]
            extra["GlobalSecondaryIndexes"] = [
                {
                    "IndexName": "area-status-index",
                    "KeySchema": [
                        {"AttributeName": "area", "KeyType": "HASH"},
                        {"AttributeName": "status", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ]
        database.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
            AttributeDefinitions=attributes,
            **extra,
        )
        monkeypatch.setenv(env, name)
    database.Table("users").put_item(Item={"email": EMAIL, "verified": True})
    PostalCacheRepository(database, "postals").save(
        PostalLocation(
            postal_code=POSTAL,
            latitude=1.35,
            longitude=103.84,
            address="Example address",
            area="Bishan",
            cached_at=NOW,
        )
    )
    build_deps.cache_clear()
    deps = build_deps()
    clock = {"now": NOW}
    deps.clock = lambda: clock["now"]
    deps.refresh = None  # Missing weather must not cause a real NEA request in tests.
    deps.snapshots.save_all([make_weather_snapshot()])
    with TestClient(create_app(lambda: deps), raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            deps=deps,
            clock=clock,
            table=database.Table("sessions"),
            users=database.Table("users"),
            database=database,
        )
    build_deps.cache_clear()


def start(api, **overrides):
    return api.client.post(
        "/laundry-sessions", json={"email": EMAIL, "postalCode": POSTAL, **overrides}
    )


def test_start_returns_contract_and_persists_alert_baseline(session_api):
    response = start(session_api, email=" USER@EXAMPLE.COM ", postalCode=" 570150 ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "id",
        "postalCode",
        "email",
        "areaName",
        "startedAt",
        "expiresAt",
        "active",
    }
    assert UUID(body["id"]).version == 4
    assert body["postalCode"] == POSTAL
    assert body["email"] == EMAIL
    assert body["areaName"] == "Bishan"
    assert body["active"] is True
    assert body["startedAt"] == "2026-10-04T14:00:00+08:00"
    assert body["expiresAt"] == "2026-10-05T02:00:00+08:00"
    item = session_api.table.get_item(Key={"session_id": body["id"]})["Item"]
    baseline = session_api.deps.snapshots.get("Bishan").result
    assert item["email"] == EMAIL
    assert item["area"] == "Bishan"
    assert item["status"] == "active"
    assert item["expires_at"] == int((NOW + timedelta(hours=12)).timestamp())
    assert item["baseline_score"] == baseline.score
    assert item["baseline_band"] == baseline.band
    assert item["baseline_veto"] == baseline.veto
    assert item["baseline_method"] == SCORE_METHOD


def test_get_and_repeated_delete_keep_the_session_inactive(session_api):
    response = start(session_api)
    assert response.status_code == 201
    original = response.json()
    path = f"/laundry-sessions/{original['id']}"
    assert session_api.client.get(path).json() == original
    stopped = session_api.client.delete(path)
    assert stopped.status_code == 200
    assert stopped.json() == {**original, "active": False}
    row = session_api.table.get_item(Key={"session_id": original["id"]})["Item"]
    session_api.clock["now"] += timedelta(minutes=1)
    assert session_api.client.delete(path).json() == stopped.json()
    assert session_api.client.get(path).json() == stopped.json()
    after = session_api.table.get_item(Key={"session_id": original["id"]})["Item"]
    assert after["status"] == "stopped"
    assert after["stopped_at"] == row["stopped_at"]


@pytest.mark.parametrize(
    ("stored_band", "expected_state"),
    [("Hang Now", "HANG_NOW"), ("Wait", "WAIT"), ("Do Not Hang", "DO_NOT_HANG")],
)
def test_get_and_stop_return_recorded_alert_state(
    session_api, stored_band, expected_state
):
    response = start(session_api)
    assert response.status_code == 201
    original = response.json()
    session_api.table.update_item(
        Key={"session_id": original["id"]},
        UpdateExpression="SET last_alert_band = :band, last_alert_at = :at",
        ExpressionAttributeValues={":band": stored_band, ":at": NOW.isoformat()},
    )
    path = f"/laundry-sessions/{original['id']}"
    for method, active in (
        (session_api.client.get, True),
        (session_api.client.delete, False),
    ):
        result = method(path)
        assert result.status_code == 200
        assert result.json() == {
            **original,
            "active": active,
            "lastAlertState": expected_state,
        }


def test_expired_session_is_inactive_even_before_ttl_deletion(session_api):
    response = start(session_api)
    assert response.status_code == 201
    body = response.json()
    session_api.clock["now"] = datetime.fromisoformat(body["expiresAt"])
    path = f"/laundry-sessions/{body['id']}"
    for method in (session_api.client.get, session_api.client.delete):
        result = method(path)
        assert result.status_code == 200
        assert result.json()["active"] is False


def test_new_session_replaces_previous_for_normalized_email(session_api):
    first = start(session_api)
    assert first.status_code == 201
    second = start(session_api, email="USER@example.com")
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    old = session_api.client.get(f"/laundry-sessions/{first.json()['id']}")
    assert old.json()["active"] is False
    assert second.json()["active"] is True
    assert (
        sum(row["status"] == "active" for row in session_api.table.scan()["Items"]) == 1
    )


@pytest.mark.parametrize("verified", [False, None])
def test_unverified_or_unknown_email_cannot_start(session_api, verified):
    if verified is None:
        session_api.users.delete_item(Key={"email": EMAIL})
    else:
        session_api.users.put_item(Item={"email": EMAIL, "verified": verified})
    response = start(session_api)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert session_api.table.scan()["Items"] == []


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"email": "bad", "postalCode": POSTAL}, "INVALID_EMAIL"),
        ({"email": None, "postalCode": POSTAL}, "INVALID_EMAIL"),
        ({"postalCode": POSTAL}, "INVALID_EMAIL"),
        ({"email": EMAIL, "postalCode": "123"}, "INVALID_POSTAL_CODE"),
        ({"email": EMAIL, "postalCode": 570150}, "INVALID_POSTAL_CODE"),
        ({"email": EMAIL}, "INVALID_POSTAL_CODE"),
        ({"email": EMAIL, "postalCode": "５７０１５０"}, "INVALID_POSTAL_CODE"),
        ([], "INVALID_REQUEST"),
    ],
)
def test_bad_request_uses_shared_error_body(session_api, payload, code):
    response = session_api.client.post("/laundry-sessions", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert session_api.table.scan()["Items"] == []


@pytest.mark.parametrize("method", ["get", "delete"])
@pytest.mark.parametrize("identifier", ["not-a-uuid", "123"])
def test_invalid_session_id_is_400(session_api, method, identifier):
    response = getattr(session_api.client, method)(f"/laundry-sessions/{identifier}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SESSION_ID"


@pytest.mark.parametrize("method", ["get", "delete"])
def test_unknown_session_is_404_and_delete_does_not_create_it(session_api, method):
    response = getattr(session_api.client, method)(f"/laundry-sessions/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert session_api.table.scan()["Items"] == []


@pytest.mark.parametrize("problem", ["stale", "missing", "old_method"])
def test_unavailable_snapshot_does_not_stop_existing_session(session_api, problem):
    first = start(session_api)
    assert first.status_code == 201
    snapshot = make_weather_snapshot()
    if problem == "stale":
        snapshot.updated_at = NOW - timedelta(minutes=31)
    elif problem == "old_method":
        snapshot.result.method = None
    if problem == "missing":
        session_api.deps.snapshots._table.delete_item(Key={"area": "Bishan"})
    else:
        session_api.deps.snapshots.save_all([snapshot])
    response = start(session_api)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    old = session_api.client.get(f"/laundry-sessions/{first.json()['id']}")
    assert old.json()["active"] is True
    assert len(session_api.table.scan()["Items"]) == 1


def test_creation_and_read_agree_when_clock_has_microseconds(session_api):
    session_api.clock["now"] = NOW.replace(microsecond=123456)
    response = start(session_api)
    assert response.status_code == 201
    path = f"/laundry-sessions/{response.json()['id']}"
    assert session_api.client.get(path).json() == response.json()


def test_malformed_json_uses_shared_error_response(session_api):
    response = session_api.client.post(
        "/laundry-sessions", content="{", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_missing_session_configuration_preserves_recommendation(session_api):
    session_api.deps.sessions = None
    response = start(session_api)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    recommendation = session_api.client.get(
        "/recommendation", params={"postalCode": POSTAL}
    )
    assert recommendation.status_code == 200


def test_new_user_cannot_replace_another_users_session(session_api):
    first = start(session_api)
    other = "other@example.com"
    session_api.users.put_item(Item={"email": other, "verified": True})
    second = start(session_api, email=other)
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert (
        session_api.client.get(f"/laundry-sessions/{first.json()['id']}").json()[
            "active"
        ]
        is True
    )


def test_ttl_deleted_predecessor_does_not_block_new_session(session_api):
    first = start(session_api)
    assert first.status_code == 201
    session_api.table.delete_item(Key={"session_id": first.json()["id"]})
    response = start(session_api)
    assert response.status_code == 201
    assert len(session_api.table.scan()["Items"]) == 1


def test_service_validates_email_without_http(session_api):
    with pytest.raises(InvalidEmailError):
        session_api.deps.sessions.start(
            "invalid",
            POSTAL,
            snapshot_for_postal=lambda _: make_weather_snapshot(),
            now=NOW,
        )
    assert session_api.table.scan()["Items"] == []


def test_concurrent_start_retries_without_leaving_two_active(session_api, monkeypatch):
    """A competing start commits between this request's read and transaction."""
    repository = SessionRepository(session_api.database, "sessions", "users")
    service = SessionService(repository)
    original_replace = repository.replace
    competing_id = None

    def interleaved_replace(session, **kwargs):
        nonlocal competing_id
        if competing_id is None:
            competing = start(session_api)
            assert competing.status_code == 201
            competing_id = competing.json()["id"]
        return original_replace(session, **kwargs)

    monkeypatch.setattr(repository, "replace", interleaved_replace)
    result = service.start(
        EMAIL, POSTAL, snapshot_for_postal=lambda _: make_weather_snapshot(), now=NOW
    )
    rows = session_api.table.scan()["Items"]
    assert len(rows) == 2
    assert {row["session_id"] for row in rows if row["status"] == "active"} == {
        result.session_id
    }
    assert (
        session_api.client.get(f"/laundry-sessions/{competing_id}").json()["active"]
        is False
    )


def test_failed_transaction_keeps_old_session_and_no_orphan(session_api, monkeypatch):
    """Verification revoked after validation must abort all replacement writes."""
    first = start(session_api)
    assert first.status_code == 201
    repository = SessionRepository(session_api.database, "sessions", "users")
    original_replace = repository.replace

    def revoke_then_replace(session, **kwargs):
        session_api.users.update_item(
            Key={"email": EMAIL},
            UpdateExpression="SET verified = :no",
            ExpressionAttributeValues={":no": False},
        )
        return original_replace(session, **kwargs)

    monkeypatch.setattr(repository, "replace", revoke_then_replace)
    session_api.deps.sessions = SessionService(repository)
    response = start(session_api)
    assert response.status_code == 403
    rows = session_api.table.scan()["Items"]
    assert len(rows) == 1
    assert rows[0]["session_id"] == first.json()["id"]
    assert rows[0]["status"] == "active"


def test_api_session_reaches_alert_pipeline_and_delete_blocks_delivery(
    session_api, sqs, queue_url, alert_log_table, ses
):
    response = start(session_api)
    assert response.status_code == 201
    session_id = response.json()["id"]
    now = NOW + timedelta(minutes=5)
    rain = make_snapshot(30, at=now, veto_reason="Rain detected")
    stats = run_alert_trigger(
        [rain], sessions_table=session_api.table, sqs=sqs, queue_url=queue_url, now=now
    )
    assert stats.alerts_queued == 1
    message = AlertMessage.model_validate_json(
        sqs.receive_message(QueueUrl=queue_url)["Messages"][0]["Body"]
    )
    assert message.session_id == session_id
    assert message.email == EMAIL
    assert message.new_method == SCORE_METHOD
    session_api.client.delete(f"/laundry-sessions/{session_id}")
    deps = NotifierDeps(
        alert_log_table=alert_log_table,
        sessions_table=session_api.table,
        ses=ses,
        sender=SENDER,
        base_url="https://drysg.example",
    )
    assert handle_alert(deps, message, now) is Outcome.SKIPPED_INACTIVE
    assert ses.get_send_quota()["SentLast24Hours"] == 0
