"""Notifier Lambda: SQS drysg-alerts -> SES. Requires ReportBatchItemFailures on the trigger."""

import logging
import os
from datetime import UTC, datetime
from functools import cache

import boto3

from drysg.alerting.notifier import NotifierDeps, process_records

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@cache
def _deps() -> NotifierDeps:
    # Built once per container, on first invocation (keeps imports cheap for tests).
    dynamodb = boto3.resource("dynamodb")
    return NotifierDeps(
        alert_log_table=dynamodb.Table(os.environ["TABLE_ALERT_LOG"]),
        sessions_table=dynamodb.Table(os.environ["TABLE_MONITORING_SESSION"]),
        ses=boto3.client("ses"),
        sender=os.environ["SES_SENDER"],
        base_url=os.environ["APP_BASE_URL"],
    )


def handler(event, context):
    failed = process_records(_deps(), event.get("Records", []), datetime.now(UTC))
    return {"batchItemFailures": [{"itemIdentifier": mid} for mid in failed]}
