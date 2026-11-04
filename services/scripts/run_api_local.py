"""Run the DrySG API locally with uvicorn. Nothing is deployed.

DynamoDB is faked in memory (moto), so no AWS account is used. Weather is LIVE: the
server fetches NEA data on start and every few minutes, like the ingest Lambda does,
and postal codes are looked up on the real OneMap API.

    cd services
    .venv/bin/python scripts/run_api_local.py

    curl "http://localhost:8000/recommendation?postalCode=570150"
    open http://localhost:8000/docs        # interactive Swagger UI

Needs ONEMAP_TOKEN (services/.env, see .env.example) to look up postal codes.
Tokens expire after about 3 days: https://www.onemap.gov.sg/apidocs/register

Email verification works without SES: POST /users prints the verification link in
this terminal instead of emailing it. Links point at --app-url (the Vite dev server).
"""

import argparse
import logging
import os
import threading
from pathlib import Path
from typing import Any

import boto3
import uvicorn
from moto import mock_aws

from drysg.alerting.email_template import EmailContent
from drysg.api.app import create_app
from drysg.api.deps import build_deps
from drysg.sessions import normalize_email

SERVICES = Path(__file__).resolve().parents[1]
REGION = "ap-southeast-1"
logger = logging.getLogger("run_api_local")


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding variables already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def create_tables() -> None:
    """Create local weather, user and session storage inside Moto."""
    dynamodb: Any = boto3.resource("dynamodb")
    for name, key in (
        ("AreaSnapshot", "area"),
        ("PostalCache", "postal_code"),
        ("User", "email"),
    ):
        dynamodb.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        )
    dynamodb.create_table(
        TableName="MonitoringSession",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("session_id", "area", "status", "email")
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "area-status-index",
                "KeySchema": [
                    {"AttributeName": "area", "KeyType": "HASH"},
                    {"AttributeName": "status", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "email-index",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
        ],
    )


def refresh_weather() -> None:
    try:
        refresh = build_deps().refresh
        if refresh is not None:
            refresh()
            logger.info("Weather refreshed")
    except Exception as exc:  # keep serving; the next refresh or request retries
        logger.warning("Weather refresh failed: %s", exc)


def refresh_forever(minutes: float, stop: threading.Event) -> None:
    while not stop.wait(minutes * 60):
        refresh_weather()


def print_email(to: str, content: EmailContent) -> None:
    """Stand-in for SES: show the email in the terminal so the link can be clicked."""
    logger.info("Email to %s: %s\n%s", to, content.subject, content.text)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh-minutes", type=float, default=5.0)
    parser.add_argument(
        "--test-email",
        type=normalize_email,
        default="local-test@example.com",
        help="Seed a verified email in the in-memory User table only",
    )
    parser.add_argument(
        "--app-url",
        default="http://127.0.0.1:5173",
        help="Frontend address used in verification links",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    load_env_file(SERVICES / ".env")
    # Fake AWS: set after loading .env so nothing can reach a real account.
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_SESSION_TOKEN="testing",
        AWS_DEFAULT_REGION=REGION,
        AWS_REGION=REGION,
        TABLE_AREA_SNAPSHOT="AreaSnapshot",
        TABLE_POSTAL_CACHE="PostalCache",
        TABLE_USER="User",
        TABLE_MONITORING_SESSION="MonitoringSession",
        SES_SENDER="local@drysg.example",
        APP_BASE_URL=args.app_url,
    )
    os.environ.pop("AWS_PROFILE", None)
    if not os.environ.get("ONEMAP_TOKEN", "").strip():
        logger.warning("ONEMAP_TOKEN is not set: postal lookups will return 503")

    with mock_aws():
        create_tables()
        boto3.resource("dynamodb").Table("User").put_item(
            Item={"email": args.test_email, "verified": True}
        )
        logger.info("Local-only verified test email: %s", args.test_email)
        refresh_weather()
        stop = threading.Event()
        threading.Thread(
            target=refresh_forever, args=(args.refresh_minutes, stop), daemon=True
        ).start()
        deps = build_deps()
        if deps.users is not None:
            deps.users.send_email = print_email
        if deps.experience is not None:
            deps.experience.send_email = print_email
        app = create_app(
            build_deps,
            cors_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        )
        try:
            uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        finally:
            stop.set()


if __name__ == "__main__":
    main()
