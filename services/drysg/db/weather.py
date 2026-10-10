"""DynamoDB mapping for Isaac's AreaSnapshot and PostalCache contracts."""

import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from drysg.core.locations import AreaLocation, PostalLocation
from drysg.core.models import AreaSnapshot


def _item(model: BaseModel) -> dict[str, Any]:
    """Serialize dates/enums as JSON values and floats as DynamoDB Decimals."""
    return json.loads(model.model_dump_json(), parse_float=Decimal)


class AreaSnapshotRepository:
    """Read and write area snapshots using an injected boto3 DynamoDB resource."""

    def __init__(self, dynamodb: Any, table_name: str) -> None:
        self._table = dynamodb.Table(table_name)

    def get(self, area: str) -> AreaSnapshot | None:
        """Return the latest stored snapshot, or None for an unknown area."""
        item = self._table.get_item(Key={"area": area}, ConsistentRead=True).get("Item")
        return AreaSnapshot.model_validate(item) if item is not None else None

    def save_all(self, snapshots: Sequence[AreaSnapshot]) -> None:
        """Batch-write snapshots; AWS failures propagate to the scheduled caller.

        Writes are not atomic across areas. The handler must run with concurrency 1.
        """
        items = [_item(snapshot) for snapshot in snapshots]
        with self._table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)

    def list_areas(self) -> list[AreaLocation]:
        """Read area label points from snapshots, following every scan page."""
        kwargs: dict[str, Any] = {
            "ProjectionExpression": "#area, latitude, longitude",
            "ExpressionAttributeNames": {"#area": "area"},
        }
        areas = []
        while True:
            page = self._table.scan(**kwargs)
            areas.extend(AreaLocation.model_validate(item) for item in page["Items"])
            if not page.get("LastEvaluatedKey"):
                return sorted(areas, key=lambda area: area.area)
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


class PostalCacheRepository:
    """Persist successful postal lookups using the agreed PostalCache shape."""

    def __init__(self, dynamodb: Any, table_name: str) -> None:
        self._table = dynamodb.Table(table_name)

    def get(self, postal_code: str) -> PostalLocation | None:
        """Return a validated cached location, or None on a cache miss."""
        item = self._table.get_item(Key={"postal_code": postal_code}).get("Item")
        return PostalLocation.model_validate(item) if item is not None else None

    def save(self, location: PostalLocation) -> None:
        """Store a successfully resolved postal location."""
        self._table.put_item(Item=_item(location))
