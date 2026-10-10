"""Alert trigger: called by the ingest Lambda after it writes the new snapshots.

For each area it looks up the active monitoring sessions, applies the pure rules in
drysg.core.alerts, and queues one SQS message per session that needs an email.
"""

import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Attr, Key

from drysg.core.alerts import decide_alert
from drysg.core.models import AlertMessage, AreaSnapshot, MonitoringSession, SessionStatus
from drysg.db.items import session_from_item, to_epoch

logger = logging.getLogger(__name__)

AREA_STATUS_INDEX = "area-status-index"
SQS_BATCH_LIMIT = 10


@dataclass
class TriggerStats:
    areas_checked: int = 0
    sessions_checked: int = 0
    alerts_queued: int = 0
    failed_message_ids: list[str] = field(default_factory=list)


def active_sessions(table, area: str, now: datetime) -> Iterator[MonitoringSession]:
    """Active, unexpired sessions for an area (TTL deletion can lag, so filter expiry too)."""
    kwargs = {
        "IndexName": AREA_STATUS_INDEX,
        "KeyConditionExpression": Key("area").eq(area) & Key("status").eq(SessionStatus.ACTIVE),
        "FilterExpression": Attr("expires_at").gt(to_epoch(now)),
    }
    while True:
        page = table.query(**kwargs)
        for item in page["Items"]:
            yield session_from_item(item)
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def send_alerts(sqs, queue_url: str, messages: list[AlertMessage]) -> list[str]:
    """Send in batches of 10, retrying failed entries once. Returns alert_ids that failed."""
    failed: list[str] = []
    for start in range(0, len(messages), SQS_BATCH_LIMIT):
        batch = {str(i): m for i, m in enumerate(messages[start : start + SQS_BATCH_LIMIT])}
        for attempt in range(2):
            resp = sqs.send_message_batch(
                QueueUrl=queue_url,
                Entries=[{"Id": i, "MessageBody": m.model_dump_json()} for i, m in batch.items()],
            )
            batch = {e["Id"]: batch[e["Id"]] for e in resp.get("Failed", [])}
            if not batch:
                break
            if attempt == 0:
                logger.warning("Retrying %d failed SQS entries", len(batch))
        failed.extend(m.alert_id for m in batch.values())
    return failed


def run_alert_trigger(
    snapshots: Iterable[AreaSnapshot],
    *,
    sessions_table,
    sqs,
    queue_url: str,
    now: datetime,
) -> TriggerStats:
    stats = TriggerStats()
    messages: list[AlertMessage] = []
    for snapshot in snapshots:
        stats.areas_checked += 1
        for session in active_sessions(sessions_table, snapshot.area, now):
            stats.sessions_checked += 1
            alert = decide_alert(session, snapshot, now)
            if alert is not None:
                messages.append(alert)

    if messages:
        stats.failed_message_ids = send_alerts(sqs, queue_url, messages)
        stats.alerts_queued = len(messages) - len(stats.failed_message_ids)
    if stats.failed_message_ids:
        # Don't fail the ingest run; the next run re-evaluates against the same baselines.
        logger.error("Failed to queue %d alerts: %s", len(stats.failed_message_ids),
                     stats.failed_message_ids)
    logger.info("Alert trigger: %s", stats)
    return stats


def run_alert_trigger_from_env(snapshots: Iterable[AreaSnapshot], now: datetime) -> TriggerStats:
    """Entry point for the ingest handler, configured from env vars (handlers.md)."""
    table = boto3.resource("dynamodb").Table(os.environ["TABLE_MONITORING_SESSION"])
    return run_alert_trigger(
        snapshots,
        sessions_table=table,
        sqs=boto3.client("sqs"),
        queue_url=os.environ["ALERT_QUEUE_URL"],
        now=now,
    )
