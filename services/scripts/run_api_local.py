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

from drysg.api.app import create_app
from drysg.api.deps import build_deps

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
    dynamodb: Any = boto3.resource("dynamodb")
    for name, key in (("AreaSnapshot", "area"), ("PostalCache", "postal_code")):
        dynamodb.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
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


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh-minutes", type=float, default=5.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

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
    )
    os.environ.pop("AWS_PROFILE", None)
    if not os.environ.get("ONEMAP_TOKEN", "").strip():
        logger.warning("ONEMAP_TOKEN is not set: postal lookups will return 503")

    with mock_aws():
        create_tables()
        refresh_weather()
        stop = threading.Event()
        threading.Thread(
            target=refresh_forever, args=(args.refresh_minutes, stop), daemon=True
        ).start()
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
