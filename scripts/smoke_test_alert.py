"""End-to-end test of the deployed alert pipeline: SQS -> notifier Lambda -> SES email.

Creates a temporary active monitoring session, puts a fake "Hang Now -> Do Not Hang" alert on
the alerts queue, waits for the notifier to record it in AlertLog, then deletes both rows.
A "sent" result means SES accepted the email; check the inbox (and junk) of --email.

Usage, from the repo root (needs boto3, e.g. the services/.venv):
    python scripts/smoke_test_alert.py --email you@example.com

--email is the recipient, not the sender. In SES sandbox both must be verified identities.
"""

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import boto3

TIMEOUT_SECONDS = 60


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="Recipient of the test alert (must be verified in sandbox)")
    parser.add_argument("--env", default="dev", help="Environment prefix (default: dev)")
    parser.add_argument("--profile", default="drysg", help="AWS CLI profile (default: drysg)")
    parser.add_argument("--area", default="Hougang", help="Area named in the test email")
    args = parser.parse_args()

    prefix = f"drysg-{args.env}"
    aws = boto3.Session(profile_name=args.profile, region_name="ap-southeast-1")
    dynamodb = aws.resource("dynamodb")
    sessions = dynamodb.Table(f"{prefix}-MonitoringSession")
    alert_log = dynamodb.Table(f"{prefix}-AlertLog")
    sqs = aws.client("sqs")
    queue_url = sqs.get_queue_url(QueueName=f"{prefix}-alerts")["QueueUrl"]

    now = datetime.now(UTC)
    session_id = f"smoke-test-{uuid.uuid4()}"
    snapshot_ts = now.isoformat()
    alert_id = f"{session_id}#{snapshot_ts}"

    # The notifier only emails sessions that are active and not expired
    sessions.put_item(Item={
        "session_id": session_id,
        "email": args.email,
        "postal_code": "000000",
        "area": args.area,
        "status": "active",
        "started_at": now.isoformat(),
        "baseline_score": 78,
        "baseline_band": "Hang Now",
        "baseline_veto": False,
        "expires_at": int((now + timedelta(hours=1)).timestamp()),
    })
    print(f"Created test session {session_id}")

    try:
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({
            "alert_id": alert_id,
            "session_id": session_id,
            "email": args.email,
            "area": args.area,
            "prev_score": 78,
            "prev_band": "Hang Now",
            "new_score": 32,
            "new_band": "Do Not Hang",
            "new_veto": True,
            "reason": "SMOKE TEST: rain detected at nearby station",
            "snapshot_ts": snapshot_ts,
        }))
        print(f"Queued alert, waiting up to {TIMEOUT_SECONDS}s for the notifier...")

        status = None
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            item = alert_log.get_item(
                Key={"session_id": session_id, "alert_id": alert_id}, ConsistentRead=True
            ).get("Item")
            status = item and item.get("status")
            if status and status != "claimed":
                break
            time.sleep(3)

        print(f"AlertLog status: {status or 'no row (notifier did not run)'}")
        if status == "sent":
            print(f"PASS: SES accepted the email. Check {args.email} (including junk).")
            return 0
        print(f"FAIL: see logs with: aws logs tail /aws/lambda/{prefix}-notifier --since 10m")
        return 1
    finally:
        sessions.delete_item(Key={"session_id": session_id})
        alert_log.delete_item(Key={"session_id": session_id, "alert_id": alert_id})
        print("Cleaned up test rows")


if __name__ == "__main__":
    sys.exit(main())
