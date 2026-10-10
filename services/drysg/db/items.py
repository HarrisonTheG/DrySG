"""Conversion between domain models and DynamoDB items (see docs/contracts/dynamodb.md).

DynamoDB returns numbers as Decimal and TTL attributes must be epoch seconds,
so models are not stored with a plain model_dump().
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from drysg.core.models import MonitoringSession


def to_epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def from_epoch(value: Decimal | int) -> datetime:
    return datetime.fromtimestamp(int(value), tz=UTC)


def session_to_item(session: MonitoringSession) -> dict[str, Any]:
    item = session.model_dump(mode="json", exclude_none=True)
    item["expires_at"] = to_epoch(session.expires_at)
    return item


def session_from_item(item: dict[str, Any]) -> MonitoringSession:
    data = dict(item)
    data["expires_at"] = from_epoch(data["expires_at"])
    data["baseline_score"] = int(data["baseline_score"])
    return MonitoringSession.model_validate(data)
