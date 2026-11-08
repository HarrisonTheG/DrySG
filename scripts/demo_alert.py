"""Demo: fire a "rain nearby, bring your laundry in" email without touching weather data.

Skips the API, the ingest schedule and SQS. It writes an active session row, then invokes the
notifier Lambda directly with an SQS-shaped event, so the real notifier code claims the alert,
advances the baseline and sends the SES email. Unlike smoke_test_alert.py the session is kept,
so the email's "I've brought my laundry in" link points at a real session.

Usage, from the repo root (needs boto3, e.g. the services/.venv, and `aws sso login --profile drysg`):
    python scripts/demo_alert.py
    python scripts/demo_alert.py --email other@example.com --area Bedok --kind band
"""

import argparse
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3

DEMO_EMAIL = "isaaac67@gmail.com"
DEMO_POSTAL_CODE = "570150"
DEMO_AREA = "Ang Mo Kio"

KINDS = {
    "rain": dict(new_score=40, new_band="Do Not Hang", new_veto=True,
                 reason="Rain detected near your area"),
    "band": dict(new_score=25, new_band="Do Not Hang", new_veto=False,
                 reason="Drying conditions dropped from Hang Now to Do Not Hang"),
    "wait": dict(new_score=55, new_band="Wait", new_veto=False,
                 reason="Drying score fell from 78 to 55"),
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--email", default=DEMO_EMAIL, help="Recipient of the demo alert")
    p.add_argument("--postal-code", default=DEMO_POSTAL_CODE)
    p.add_argument("--area", default=DEMO_AREA)
    p.add_argument("--session-id", help="Alert an existing session (e.g. one started via the API)")
    p.add_argument("--latest", action="store_true", help="Alert --email's most recent API-started session")
    p.add_argument("--kind", choices=KINDS, default="rain")
    p.add_argument("--env", default="dev")
    p.add_argument("--profile", default="drysg")
    args = p.parse_args()

    prefix = f"drysg-{args.env}"
    profile = None if os.environ.get("AWS_ACCESS_KEY_ID") else args.profile
    aws = boto3.Session(profile_name=profile, region_name="ap-southeast-1")
    sessions = aws.resource("dynamodb").Table(f"{prefix}-MonitoringSession")

    now = datetime.now(timezone.utc)
    snapshot_ts = now.isoformat()
    if args.latest:
        user = aws.resource("dynamodb").Table(f"{prefix}-User").get_item(
            Key={"email": args.email.lower()}, ConsistentRead=True
        ).get("Item")
        args.session_id = user and user.get("active_session_id")
        if not args.session_id:
            raise SystemExit(f"{args.email} has no active_session_id; start monitoring in the UI first")
    if args.session_id:
        session_id = args.session_id
        item = sessions.get_item(Key={"session_id": session_id}, ConsistentRead=True).get("Item")
        if not item:
            raise SystemExit(f"No session {session_id} in {prefix}-MonitoringSession")
        args.email, args.area = item["email"], item["area"]
    else:
        session_id = f"demo-{uuid.uuid4()}"
        sessions.put_item(Item={
            "session_id": session_id,
            "email": args.email.lower(),
            "postal_code": args.postal_code,
            "area": args.area,
            "status": "active",
            "started_at": now.isoformat(),
            "baseline_score": 78,
            "baseline_band": "Hang Now",
            "baseline_veto": False,
            "expires_at": int((now + timedelta(hours=12)).timestamp()),
        })

    body = {
        "alert_id": f"{session_id}#{snapshot_ts}",
        "session_id": session_id,
        "email": args.email.lower(),
        "area": args.area,
        "prev_score": 78,
        "prev_band": "Hang Now",
        "snapshot_ts": snapshot_ts,
        **KINDS[args.kind],
    }
    event = {"Records": [{"messageId": "demo-1", "body": json.dumps(body)}]}
    resp = aws.client("lambda").invoke(
        FunctionName=f"{prefix}-notifier", Payload=json.dumps(event).encode()
    )
    result = json.loads(resp["Payload"].read())
    failed = result.get("batchItemFailures") if isinstance(result, dict) else result
    print(f"session {session_id}\nnotifier result: {result}")
    print("FAIL" if failed else f"OK: email sent to {args.email} (check Spam)")
    print(f"logs: aws logs tail /aws/lambda/{prefix}-notifier --since 5m --profile {args.profile}")


if __name__ == "__main__":
    main()
