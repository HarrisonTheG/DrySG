"""Session persistence and atomic replacement using the existing User table."""

from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from drysg.core.models import MonitoringSession, SessionStatus

from .items import session_from_item, session_to_item, to_epoch


class ConcurrentSessionUpdateError(RuntimeError):
    """Another request changed the user or session during replacement."""


class SessionRepository:
    """Store sessions and serialize starts using a pointer on the user's row."""

    def __init__(self, dynamodb: Any, session_table: str, user_table: str) -> None:
        self._sessions = dynamodb.Table(session_table)
        self._users = dynamodb.Table(user_table)
        self._client = dynamodb.meta.client

    def get_user(self, email: str) -> dict[str, Any] | None:
        """Read verification state and the last session pointer consistently."""
        return self._users.get_item(Key={"email": email}, ConsistentRead=True).get(
            "Item"
        )

    def get(self, session_id: str) -> MonitoringSession | None:
        """Read a session, including rows waiting for TTL deletion."""
        item = self._sessions.get_item(
            Key={"session_id": session_id}, ConsistentRead=True
        ).get("Item")
        return None if item is None else session_from_item(item)

    def replace(
        self,
        session: MonitoringSession,
        *,
        previous_id: str | None,
        stop_previous: bool,
    ) -> None:
        """Atomically publish the new session and stop its active predecessor.

        Raises:
            ConcurrentSessionUpdateError: The caller must re-read user/session state.
        """
        values: dict[str, Any] = {
            ":new": session.session_id,
            ":verified": True,
        }
        condition = "verified = :verified AND "
        if previous_id is None:
            condition += "attribute_not_exists(active_session_id)"
        else:
            condition += "active_session_id = :previous"
            values[":previous"] = previous_id
        writes: list[dict[str, Any]] = [
            {
                "Update": {
                    "TableName": self._users.name,
                    "Key": {"email": session.email},
                    "UpdateExpression": "SET active_session_id = :new",
                    "ConditionExpression": condition,
                    "ExpressionAttributeValues": values,
                }
            },
            {
                "Put": {
                    "TableName": self._sessions.name,
                    "Item": session_to_item(session),
                    "ConditionExpression": "attribute_not_exists(session_id)",
                }
            },
        ]
        if stop_previous:
            writes.append(
                {
                    "Update": {
                        "TableName": self._sessions.name,
                        "Key": {"session_id": previous_id},
                        "UpdateExpression": "SET #status = :stopped, stopped_at = :now",
                        "ConditionExpression": "#status = :active AND email = :email",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {
                            ":stopped": SessionStatus.STOPPED.value,
                            ":active": SessionStatus.ACTIVE.value,
                            ":email": session.email,
                            ":now": session.started_at.isoformat(),
                        },
                    }
                }
            )
        try:
            self._client.transact_write_items(TransactItems=writes)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "TransactionCanceledException":
                reasons = exc.response.get("CancellationReasons", [])
                if any(
                    r.get("Code") in {"ConditionalCheckFailed", "TransactionConflict"}
                    for r in reasons
                ):
                    raise ConcurrentSessionUpdateError() from exc
            raise

    def stop(self, session_id: str, now: datetime) -> MonitoringSession | None:
        """Stop an active, unexpired session without upserting a missing row."""
        try:
            response = self._sessions.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET #status = :stopped, stopped_at = :now",
                ConditionExpression="#status = :active AND expires_at > :epoch",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":active": SessionStatus.ACTIVE.value,
                    ":stopped": SessionStatus.STOPPED.value,
                    ":now": now.isoformat(),
                    ":epoch": to_epoch(now),
                },
                ReturnValues="ALL_NEW",
            )
            return session_from_item(response["Attributes"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self.get(session_id)
