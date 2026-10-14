"""Notifier: consumes AlertMessages from SQS and emails users via SES.

Per message, in this order:
  1. Claim alert_id in AlertLog (conditional put). Already claimed -> duplicate, skip.
     A previously *failed* claim can be re-claimed, so SQS retries work.
  2. Move the session's baseline to the new state, conditional on the session still
     being active and unexpired. Fails -> user stopped monitoring, skip (no email).
  3. Send via SES. Fails -> mark the claim failed and report the message as a batch
     item failure, so SQS retries it (then DLQ after maxReceiveCount).
  4. Mark the claim sent.

Known gap: a crash between 1 and 3 leaves a 'claimed' entry, so that one alert is
not retried. AlertLog makes this visible for the missed/duplicate-alert evaluation.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from botocore.exceptions import ClientError
from pydantic import ValidationError

from drysg.alerting.email_template import render_alert
from drysg.core.models import AlertMessage, SessionStatus
from drysg.db.items import to_epoch

logger = logging.getLogger(__name__)

ALERT_LOG_RETENTION = timedelta(days=30)


class AlertStatus(StrEnum):
    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_INACTIVE = "skipped_inactive"


class Outcome(StrEnum):
    SENT = "sent"
    DUPLICATE = "duplicate"
    SKIPPED_INACTIVE = "skipped_inactive"


@dataclass
class NotifierDeps:
    alert_log_table: object
    sessions_table: object
    ses: object
    sender: str
    base_url: str


def _is_conditional_failure(err: ClientError) -> bool:
    return err.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _claim(deps: NotifierDeps, msg: AlertMessage, now: datetime) -> bool:
    try:
        deps.alert_log_table.put_item(
            Item={
                "session_id": msg.session_id,
                "alert_id": msg.alert_id,
                "status": AlertStatus.CLAIMED,
                "area": msg.area,
                "prev_band": msg.prev_band,
                "new_band": msg.new_band,
                "prev_score": msg.prev_score,
                "new_score": msg.new_score,
                "reason": msg.reason,
                "snapshot_ts": msg.snapshot_ts.isoformat(),
                "claimed_at": now.isoformat(),
                "ttl": to_epoch(now + ALERT_LOG_RETENTION),
            },
            ConditionExpression="attribute_not_exists(alert_id) OR #s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":failed": AlertStatus.FAILED},
        )
        return True
    except ClientError as err:
        if _is_conditional_failure(err):
            return False
        raise


def _set_log_status(deps: NotifierDeps, msg: AlertMessage, status: AlertStatus, **extra) -> None:
    names = {"#s": "status"}
    values = {":s": status}
    sets = ["#s = :s"]
    for i, (k, v) in enumerate(extra.items()):
        names[f"#k{i}"] = k
        values[f":v{i}"] = v
        sets.append(f"#k{i} = :v{i}")
    deps.alert_log_table.update_item(
        Key={"session_id": msg.session_id, "alert_id": msg.alert_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _advance_baseline(deps: NotifierDeps, msg: AlertMessage, now: datetime) -> bool:
    """Move the baseline to the alerted state. False if the session is no longer active."""
    try:
        deps.sessions_table.update_item(
            Key={"session_id": msg.session_id},
            UpdateExpression=(
                "SET baseline_score = :score, baseline_band = :band, baseline_veto = :veto, "
                "baseline_method = :method, last_alert_at = :now, last_alert_band = :band"
            ),
            ConditionExpression="#s = :active AND expires_at > :now_epoch",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":score": msg.new_score,
                ":band": msg.new_band,
                ":veto": msg.new_veto,
                ":method": msg.new_method,
                ":now": now.isoformat(),
                ":active": SessionStatus.ACTIVE,
                ":now_epoch": to_epoch(now),
            },
        )
        return True
    except ClientError as err:
        if _is_conditional_failure(err):
            return False
        raise


def handle_alert(deps: NotifierDeps, msg: AlertMessage, now: datetime) -> Outcome:
    """Process one alert. Raises if it should be retried."""
    if not _claim(deps, msg, now):
        logger.info("Duplicate alert %s, skipping", msg.alert_id)
        return Outcome.DUPLICATE

    if not _advance_baseline(deps, msg, now):
        logger.info("Session %s no longer active, skipping alert", msg.session_id)
        _set_log_status(deps, msg, AlertStatus.SKIPPED_INACTIVE)
        return Outcome.SKIPPED_INACTIVE

    content = render_alert(msg, deps.base_url)
    try:
        resp = deps.ses.send_email(
            Source=deps.sender,
            Destination={"ToAddresses": [msg.email]},
            Message={
                "Subject": {"Data": content.subject},
                "Body": {"Text": {"Data": content.text}, "Html": {"Data": content.html}},
            },
        )
    except ClientError:
        _set_log_status(deps, msg, AlertStatus.FAILED)
        raise

    _set_log_status(
        deps, msg, AlertStatus.SENT, sent_at=now.isoformat(), ses_message_id=resp["MessageId"]
    )
    return Outcome.SENT


def process_records(deps: NotifierDeps, records: list[dict], now: datetime) -> list[str]:
    """Process an SQS batch. Returns the messageIds that failed (for batchItemFailures)."""
    failed: list[str] = []
    for record in records:
        message_id = record["messageId"]
        try:
            msg = AlertMessage.model_validate_json(record["body"])
            outcome = handle_alert(deps, msg, now)
            logger.info("Alert %s: %s", msg.alert_id, outcome)
        except ValidationError:
            # Retrying won't fix a malformed body; fail it so it lands in the DLQ for inspection.
            logger.exception("Malformed alert message %s", message_id)
            failed.append(message_id)
        except Exception:
            logger.exception("Failed to process alert message %s", message_id)
            failed.append(message_id)
    return failed
