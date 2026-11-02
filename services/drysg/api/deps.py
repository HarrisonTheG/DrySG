"""Build the API's dependencies from environment variables (see .env.example)."""

import os
from functools import cache
from typing import Any

import boto3
import httpx
from botocore.config import Config

from drysg.alerting.email_template import EmailContent
from drysg.data import IngestionService, LocationService, NeaClient, OneMapClient
from drysg.db import (
    AreaSnapshotRepository,
    PostalCacheRepository,
    SessionRepository,
    UserRepository,
)
from drysg.sessions import SessionService
from drysg.users import UserService

from .app import ApiDeps, AreaCatalogue, ThrottledRefresh


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def ses_sender(ses: Any, sender: str):
    """An email sender for UserService that sends through SES from `sender`."""

    def send(to: str, content: EmailContent) -> None:
        ses.send_email(
            Source=sender,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": content.subject},
                "Body": {
                    "Text": {"Data": content.text},
                    "Html": {"Data": content.html},
                },
            },
        )

    return send


@cache
def build_deps() -> ApiDeps:
    """Created once per Lambda container. The HTTP client lives for the container's life."""
    region = os.environ.get("AWS_REGION", "ap-southeast-1")
    aws_config = Config(
        connect_timeout=3,
        read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    )
    dynamodb = boto3.resource("dynamodb", region_name=region, config=aws_config)
    snapshots = AreaSnapshotRepository(dynamodb, _required("TABLE_AREA_SNAPSHOT"))
    postals = PostalCacheRepository(dynamodb, _required("TABLE_POSTAL_CACHE"))
    http = httpx.Client()
    nea_key = os.environ.get("NEA_API_KEY") or None
    onemap_token = os.environ.get("ONEMAP_TOKEN") or None
    session_table = os.environ.get("TABLE_MONITORING_SESSION", "").strip()
    user_table = os.environ.get("TABLE_USER", "").strip()
    sender = os.environ.get("SES_SENDER", "").strip()
    app_base_url = os.environ.get("APP_BASE_URL", "").strip()

    def refresh() -> None:
        IngestionService(NeaClient(http, api_key=nea_key), snapshots).run()

    return ApiDeps(
        snapshots=snapshots,
        locations=LocationService(OneMapClient(http, token=onemap_token), postals),
        catalogue=AreaCatalogue(snapshots),
        refresh=ThrottledRefresh(refresh),
        sessions=(
            SessionService(SessionRepository(dynamodb, session_table, user_table))
            if session_table and user_table
            else None
        ),
        # Email verification needs the User table, a sender and the site URL for links.
        users=(
            UserService(
                UserRepository(dynamodb, user_table),
                ses_sender(
                    boto3.client("ses", region_name=region, config=aws_config), sender
                ),
                app_base_url=app_base_url,
            )
            if user_table and sender and app_base_url
            else None
        ),
    )
