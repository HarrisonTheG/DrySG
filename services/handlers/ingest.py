"""Scheduled weather refresh followed by Stream B's session alert trigger."""

import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import boto3
import httpx
from botocore.config import Config

from drysg.alerting.trigger import run_alert_trigger_from_env
from drysg.data import IngestionService, NeaClient
from drysg.db import AreaSnapshotRepository

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Refresh snapshots, then queue eligible session alerts for an invocation.

    Only snapshots saved by this run reach the trigger. Fetch, persistence and
    trigger exceptions propagate. Per-message SQS failures follow Stream B's
    retry policy and are included in the returned alert statistics.
    """
    for name in ("TABLE_AREA_SNAPSHOT", "TABLE_MONITORING_SESSION", "ALERT_QUEUE_URL"):
        if not os.environ.get(name, "").strip():
            raise ValueError(f"{name} must be configured")
    table_name = os.environ.get("TABLE_AREA_SNAPSHOT", "").strip()
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=os.environ.get("AWS_REGION", "ap-southeast-1"),
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"mode": "standard", "total_max_attempts": 2},
        ),
    )
    with httpx.Client() as http:
        result = IngestionService(
            NeaClient(http, api_key=os.environ.get("NEA_API_KEY")),
            AreaSnapshotRepository(dynamodb, table_name),
        ).run()
    alert_stats = run_alert_trigger_from_env(result.snapshots, datetime.now(UTC))
    summary = {
        "updated_areas": len(result.snapshots),
        "skipped_areas": list(result.skipped_areas),
        "missing_feeds": list(result.missing_feeds),
        "alerts": asdict(alert_stats),
    }
    logger.info("Weather ingestion completed: %s", summary)
    return summary
