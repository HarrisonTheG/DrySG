"""The local runner supplies the same tables needed by all four public routes."""

import boto3

from scripts.run_api_local import create_tables


def test_local_tables_include_session_and_user_storage(aws):
    create_tables()
    assert {"AreaSnapshot", "PostalCache", "MonitoringSession", "User"} <= set(
        boto3.client("dynamodb").list_tables()["TableNames"]
    )
