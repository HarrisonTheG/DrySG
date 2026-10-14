"""Run the ingest Lambda locally against LIVE NEA data, with AWS faked by moto.

Nothing is deployed and nothing touches a real AWS account: DynamoDB and SQS are
in-memory. Only the NEA feeds are fetched for real (6 read-only GETs).

    cd services
    .venv/bin/python scripts/run_ingest_local.py
    .venv/bin/python scripts/run_ingest_local.py --watch-area Bishan   # also test alerts

--watch-area seeds an active session for that area, baselined as a perfect "Hang Now",
so the alert trigger has something to compare against and you can see the SQS message
the handler would queue.
"""

import argparse
import json
import os
import time
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import boto3
import httpx
from moto import mock_aws

from drysg.core.models import AreaSnapshot, Band, MonitoringSession, Reading, SessionStatus
from drysg.db import AreaSnapshotRepository
from drysg.db.items import session_to_item
from handlers import ingest

REGION = "ap-southeast-1"


def use_fake_aws_credentials() -> None:
    """Make sure nothing can reach a real AWS account."""
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_SESSION_TOKEN="testing",
        AWS_DEFAULT_REGION=REGION,
        AWS_REGION=REGION,
    )
    os.environ.pop("AWS_PROFILE", None)


def create_tables(dynamodb: Any) -> None:
    dynamodb.create_table(
        TableName="AreaSnapshot",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "area", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "area", "AttributeType": "S"}],
    )
    dynamodb.create_table(
        TableName="MonitoringSession",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "area", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "area-status-index",
                "KeySchema": [
                    {"AttributeName": "area", "KeyType": "HASH"},
                    {"AttributeName": "status", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def seed_session(dynamodb: Any, area: str) -> None:
    now = datetime.now(UTC)
    session = MonitoringSession(
        session_id="00000000-0000-4000-8000-000000000000",
        email="local-test@example.com",
        postal_code="000000",
        area=area,
        status=SessionStatus.ACTIVE,
        started_at=now - timedelta(minutes=30),
        expires_at=now + timedelta(hours=12),
        baseline_score=100,
        baseline_band=Band.HANG_NOW,
    )
    dynamodb.Table("MonitoringSession").put_item(Item=session_to_item(session))


def load_snapshots(dynamodb: Any) -> list[AreaSnapshot]:
    """All saved snapshots, lowest score first."""
    repo = AreaSnapshotRepository(dynamodb, "AreaSnapshot")
    items = dynamodb.Table("AreaSnapshot").scan()["Items"]
    found = (repo.get(item["area"]) for item in items)
    return sorted((s for s in found if s is not None), key=lambda s: s.result.score)


def reading_text(reading: Reading | None) -> str:
    return "-" if reading is None else f"{reading.value:g}"


def paced_nea_requests(seconds: float) -> AbstractContextManager[Any]:
    """Sleep before every httpx GET, to stay under the NEA rate limit (HTTP 429)."""
    original_get = httpx.Client.get

    def paced_get(self: httpx.Client, *args: Any, **kwargs: Any) -> httpx.Response:
        time.sleep(seconds)
        return original_get(self, *args, **kwargs)

    return patch.object(httpx.Client, "get", paced_get)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--watch-area", help="Seed an active session for this area")
    parser.add_argument(
        "--pace", type=float, default=0.0, help="Seconds to wait between NEA requests (429s)"
    )
    parser.add_argument("--top", type=int, default=10, help="Rows to print per end of the list")
    args = parser.parse_args()
    use_fake_aws_credentials()

    pacing = paced_nea_requests(args.pace) if args.pace else nullcontext()

    with pacing, mock_aws():
        dynamodb: Any = boto3.resource("dynamodb")
        sqs = boto3.client("sqs")
        create_tables(dynamodb)
        queue_url = sqs.create_queue(QueueName="drysg-alerts")["QueueUrl"]
        os.environ.update(
            TABLE_AREA_SNAPSHOT="AreaSnapshot",
            TABLE_MONITORING_SESSION="MonitoringSession",
            ALERT_QUEUE_URL=queue_url,
        )
        if args.watch_area:
            seed_session(dynamodb, args.watch_area)

        summary = ingest.handler({"source": "local"}, None)
        print("Handler result:")
        print(json.dumps(summary, indent=2))

        snapshots = load_snapshots(dynamodb)
        print(f"\n{len(snapshots)} areas saved. Lowest and highest scores:")
        print(
            f"{'area':<22}{'score':>6}  {'band':<12}{'DP':>6}  {'rain':>5}  "
            f"{'T':>5} {'RH':>5} {'kt':>5}  flags"
        )
        shown = snapshots[: args.top] + ([] if len(snapshots) <= 2 * args.top else [None]) + (
            snapshots[-args.top :] if len(snapshots) > args.top else []
        )
        for s in shown:
            if s is None:
                print("  ...")
                continue
            c, r = s.conditions, s.result

            flags = ("VETO " if r.veto else "") + ("AIR " if r.air_quality_warning else "")
            rain, temp = reading_text(c.rainfall_mm), reading_text(c.temp_c)
            humidity, wind = reading_text(c.humidity_pct), reading_text(c.wind_knots)
            print(
                f"{s.area:<22}{r.score:>6}  {r.band.value:<12}{r.dp or 0:>6.2f}  "
                f"{rain:>5}  {temp:>5} {humidity:>5} {wind:>5}  {flags}"
            )

        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get(
            "Messages", []
        )
        print(f"\nAlerts queued in SQS: {len(messages)}")
        for m in messages:
            print(json.dumps(json.loads(m["Body"]), indent=2))


if __name__ == "__main__":
    main()
