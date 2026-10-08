"""Scheduled weather collection entry point; alert dispatch is Stream B's hook."""

import logging
import os
from typing import Any

import boto3
import httpx
from botocore.config import Config

from drysg.data import IngestionService, NeaClient
from drysg.db import AreaSnapshotRepository

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Refresh shared snapshots for an EventBridge invocation.

    Failures propagate so Lambda reports a failed invocation. No alert or email
    is sent here yet; Stream B can consume result.snapshots after run().
    """
    table_name = os.environ.get("TABLE_AREA_SNAPSHOT", "").strip()
    if not table_name:
        raise ValueError("TABLE_AREA_SNAPSHOT must be configured")
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
    summary = {
        "updated_areas": len(result.snapshots),
        "skipped_areas": list(result.skipped_areas),
        "missing_feeds": list(result.missing_feeds),
    }
    logger.info("Weather ingestion completed: %s", summary)
    return summary
