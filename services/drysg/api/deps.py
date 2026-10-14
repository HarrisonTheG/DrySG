"""Build the API's dependencies from environment variables (see .env.example)."""

import os
from functools import cache

import boto3
import httpx
from botocore.config import Config

from drysg.data import IngestionService, LocationService, NeaClient, OneMapClient
from drysg.db import AreaSnapshotRepository, PostalCacheRepository

from .app import ApiDeps, AreaCatalogue, ThrottledRefresh


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


@cache
def build_deps() -> ApiDeps:
    """Created once per Lambda container. The HTTP client lives for the container's life."""
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=os.environ.get("AWS_REGION", "ap-southeast-1"),
        config=Config(
            connect_timeout=3, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 2}
        ),
    )
    snapshots = AreaSnapshotRepository(dynamodb, _required("TABLE_AREA_SNAPSHOT"))
    postals = PostalCacheRepository(dynamodb, _required("TABLE_POSTAL_CACHE"))
    http = httpx.Client()
    nea_key = os.environ.get("NEA_API_KEY") or None
    onemap_token = os.environ.get("ONEMAP_TOKEN") or None

    def refresh() -> None:
        IngestionService(NeaClient(http, api_key=nea_key), snapshots).run()

    return ApiDeps(
        snapshots=snapshots,
        locations=LocationService(OneMapClient(http, token=onemap_token), postals),
        catalogue=AreaCatalogue(snapshots),
        refresh=ThrottledRefresh(refresh),
    )
