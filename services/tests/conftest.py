"""Shared fixtures: moto-backed AWS resources matching docs/contracts/{dynamodb,handlers}.md."""

import boto3
import pytest
from moto import mock_aws

REGION = "ap-southeast-1"
SENDER = "alerts@drysg.example"


@pytest.fixture(autouse=True)
def _fake_aws_env(monkeypatch):
    # Never let tests reach a real AWS account.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def sessions_table(aws):
    table = boto3.resource("dynamodb").create_table(
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
    return table


@pytest.fixture
def alert_log_table(aws):
    return boto3.resource("dynamodb").create_table(
        TableName="AlertLog",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "session_id", "KeyType": "HASH"},
            {"AttributeName": "alert_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "alert_id", "AttributeType": "S"},
        ],
    )


@pytest.fixture
def sqs(aws):
    return boto3.client("sqs")


@pytest.fixture
def queue_url(sqs):
    return sqs.create_queue(QueueName="drysg-alerts")["QueueUrl"]


@pytest.fixture
def ses(aws):
    client = boto3.client("ses")
    client.verify_email_identity(EmailAddress=SENDER)
    return client
