"""Alert pipeline against moto: trigger -> SQS -> notifier -> SES, with AlertLog dedupe."""

import json
from datetime import timedelta

import boto3
import pytest

from drysg.alerting.notifier import NotifierDeps, Outcome, handle_alert, process_records
from drysg.alerting.trigger import run_alert_trigger
from drysg.core.models import AlertMessage, Band, SessionStatus
from drysg.db.items import session_from_item, session_to_item
from tests.conftest import SENDER
from tests.factories import NOW, make_alert, make_session, make_snapshot

BASE_URL = "https://drysg.example"


def sent_count(ses) -> int:
    return int(ses.get_send_quota()["SentLast24Hours"])


def receive_all(sqs, queue_url) -> list[AlertMessage]:
    out = []
    while True:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
        msgs = resp.get("Messages", [])
        if not msgs:
            return out
        for m in msgs:
            out.append(AlertMessage.model_validate_json(m["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])


def sqs_record(msg: AlertMessage, message_id: str = "m1") -> dict:
    return {"messageId": message_id, "body": msg.model_dump_json()}


@pytest.fixture
def deps(alert_log_table, sessions_table, ses):
    return NotifierDeps(
        alert_log_table=alert_log_table,
        sessions_table=sessions_table,
        ses=ses,
        sender=SENDER,
        base_url=BASE_URL,
    )


def put_session(table, **overrides):
    session = make_session(**overrides)
    table.put_item(Item=session_to_item(session))
    return session


# --- Trigger ------------------------------------------------------------------


class TestTrigger:
    def test_queues_one_alert_per_eligible_session(self, sessions_table, sqs, queue_url):
        a1 = put_session(sessions_table, session_id="a1", email="a1@x.com")
        a2 = put_session(sessions_table, session_id="a2", email="a2@x.com")
        put_session(sessions_table, session_id="stopped", status=SessionStatus.STOPPED)
        # Expired but not yet removed by DynamoDB TTL.
        put_session(sessions_table, session_id="expired", expires_at=NOW - timedelta(minutes=1))
        put_session(sessions_table, session_id="hougang", area="Hougang")

        stats = run_alert_trigger(
            [make_snapshot(30, area="Bishan"), make_snapshot(90, area="Hougang")],
            sessions_table=sessions_table,
            sqs=sqs,
            queue_url=queue_url,
            now=NOW,
        )

        queued = receive_all(sqs, queue_url)
        assert {m.session_id for m in queued} == {a1.session_id, a2.session_id}
        assert {m.email for m in queued} == {"a1@x.com", "a2@x.com"}
        assert all(m.new_band is Band.DO_NOT_HANG for m in queued)
        assert (stats.areas_checked, stats.sessions_checked, stats.alerts_queued) == (2, 3, 2)

    def test_batches_more_than_ten_alerts(self, sessions_table, sqs, queue_url):
        for i in range(23):
            put_session(sessions_table, session_id=f"s{i:02d}")
        stats = run_alert_trigger(
            [make_snapshot(30)], sessions_table=sessions_table, sqs=sqs, queue_url=queue_url,
            now=NOW,
        )
        assert stats.alerts_queued == 23
        assert len({m.session_id for m in receive_all(sqs, queue_url)}) == 23

    def test_no_alerts_when_conditions_hold(self, sessions_table, sqs, queue_url):
        put_session(sessions_table)
        stats = run_alert_trigger(
            [make_snapshot(85)], sessions_table=sessions_table, sqs=sqs, queue_url=queue_url,
            now=NOW,
        )
        assert stats.alerts_queued == 0
        assert receive_all(sqs, queue_url) == []


# --- Notifier -----------------------------------------------------------------


class TestNotifier:
    def test_sends_email_and_advances_baseline(self, deps, sessions_table, alert_log_table, ses):
        session = put_session(sessions_table)
        alert = make_alert(session_id=session.session_id)

        assert handle_alert(deps, alert, NOW) is Outcome.SENT
        assert sent_count(ses) == 1

        stored = session_from_item(
            sessions_table.get_item(Key={"session_id": session.session_id})["Item"]
        )
        assert (stored.baseline_score, stored.baseline_band, stored.baseline_veto) == (
            30,
            Band.DO_NOT_HANG,
            True,
        )
        assert stored.last_alert_at == NOW
        assert stored.last_alert_band is Band.DO_NOT_HANG

        log = alert_log_table.get_item(
            Key={"session_id": session.session_id, "alert_id": alert.alert_id}
        )["Item"]
        assert log["status"] == "sent"
        assert log["ses_message_id"]

    def test_duplicate_delivery_sends_once(self, deps, sessions_table, ses):
        put_session(sessions_table)
        alert = make_alert()
        failed = process_records(deps, [sqs_record(alert, "m1"), sqs_record(alert, "m2")], NOW)
        assert failed == []
        assert sent_count(ses) == 1

    @pytest.mark.parametrize(
        "overrides",
        [{"status": SessionStatus.STOPPED}, {"expires_at": NOW - timedelta(minutes=1)}],
        ids=["stopped", "expired"],
    )
    def test_no_email_if_monitoring_ended(
        self, deps, sessions_table, alert_log_table, ses, overrides
    ):
        put_session(sessions_table, **overrides)
        alert = make_alert()
        assert handle_alert(deps, alert, NOW) is Outcome.SKIPPED_INACTIVE
        assert sent_count(ses) == 0
        log = alert_log_table.get_item(
            Key={"session_id": alert.session_id, "alert_id": alert.alert_id}
        )["Item"]
        assert log["status"] == "skipped_inactive"

    def test_ses_failure_is_retried_then_succeeds(self, deps, sessions_table, alert_log_table):
        put_session(sessions_table)
        alert = make_alert()
        unverified = NotifierDeps(**{**deps.__dict__, "sender": "nobody@unverified.example"})

        # moto rejects unverified senders like real SES does.
        assert process_records(unverified, [sqs_record(alert, "m1")], NOW) == ["m1"]
        log = alert_log_table.get_item(
            Key={"session_id": alert.session_id, "alert_id": alert.alert_id}
        )["Item"]
        assert log["status"] == "failed"

        # SQS redelivers the same message; a failed claim can be re-claimed.
        assert process_records(deps, [sqs_record(alert, "m1")], NOW) == []
        assert sent_count(deps.ses) == 1

    def test_malformed_message_is_reported_failed(self, deps):
        assert process_records(deps, [{"messageId": "bad", "body": "{not json"}], NOW) == ["bad"]


# --- End to end ---------------------------------------------------------------


def test_pipeline_end_to_end(deps, sessions_table, sqs, queue_url, ses):
    """Rain starts -> one email. The next run, still raining -> no repeat email."""
    put_session(sessions_table)

    def run_once(snapshot):
        run_alert_trigger(
            [snapshot], sessions_table=sessions_table, sqs=sqs, queue_url=queue_url, now=NOW
        )
        records = [sqs_record(m, f"m{i}") for i, m in enumerate(receive_all(sqs, queue_url))]
        assert process_records(deps, records, NOW) == []
        return len(records)

    rain = "Rain detected at nearby station (0.4 mm in last 5 min)"
    assert run_once(make_snapshot(30, veto_reason=rain)) == 1
    assert run_once(make_snapshot(25, veto_reason=rain, at=NOW + timedelta(minutes=5))) == 0
    assert sent_count(ses) == 1


def test_lambda_handler(monkeypatch, sessions_table, alert_log_table, ses):
    monkeypatch.setenv("TABLE_ALERT_LOG", alert_log_table.name)
    monkeypatch.setenv("TABLE_MONITORING_SESSION", sessions_table.name)
    monkeypatch.setenv("SES_SENDER", SENDER)
    monkeypatch.setenv("APP_BASE_URL", BASE_URL)
    from handlers import notifier

    notifier._deps.cache_clear()
    # The handler uses the real clock, so the session must be unexpired relative to now.
    sessions_table.put_item(
        Item=session_to_item(make_session(expires_at=NOW + timedelta(days=3650)))
    )
    event = {"Records": [sqs_record(make_alert(), "ok"), {"messageId": "bad", "body": "x"}]}

    result = notifier.handler(event, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "bad"}]}
    assert sent_count(boto3.client("ses")) == 1
    notifier._deps.cache_clear()
    assert json.dumps(result)  # serialisable for Lambda
