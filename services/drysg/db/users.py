"""User rows for email verification (docs/contracts/dynamodb.md, User table)."""

from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError


class TokenIssueRejectedError(RuntimeError):
    """The user is already verified, or a token was sent too recently."""


class TokenMismatchError(RuntimeError):
    """The stored token changed (or was used) between the read and the update."""


def _utc(value: datetime) -> str:
    # Token timestamps are compared as strings inside DynamoDB conditions, so they are
    # always written in UTC with the same format.
    return value.astimezone(UTC).isoformat()


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] == "ConditionalCheckFailedException"


class UserRepository:
    """Verification state on the User table. Never touches `active_session_id`."""

    def __init__(self, dynamodb: Any, user_table: str) -> None:
        self._users = dynamodb.Table(user_table)

    def get(self, email: str) -> dict[str, Any] | None:
        return self._users.get_item(Key={"email": email}, ConsistentRead=True).get(
            "Item"
        )

    def issue_token(
        self,
        email: str,
        token_hash: str,
        *,
        now: datetime,
        expires_at: datetime,
        resend_after: datetime,
    ) -> None:
        """Create the user if needed and store a new token hash.

        Only succeeds for an unverified user whose last token was sent before
        `resend_after`, so concurrent requests cannot send two emails.

        Raises:
            TokenIssueRejectedError: Already verified, or the last email was too recent.
        """
        try:
            self._users.update_item(
                Key={"email": email},
                UpdateExpression=(
                    "SET verified = if_not_exists(verified, :false), "
                    "created_at = if_not_exists(created_at, :now), "
                    "token_hash = :hash, token_expires_at = :expires, "
                    "token_sent_at = :sent"
                ),
                ConditionExpression=(
                    "(attribute_not_exists(verified) OR verified = :false) AND "
                    "(attribute_not_exists(token_sent_at) OR token_sent_at < :cutoff)"
                ),
                ExpressionAttributeValues={
                    ":false": False,
                    ":now": now.isoformat(),
                    ":hash": token_hash,
                    ":expires": _utc(expires_at),
                    ":sent": _utc(now),
                    ":cutoff": _utc(resend_after),
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise TokenIssueRejectedError() from exc
            raise

    def allow_resend(self, email: str, token_hash: str) -> None:
        """Clear the resend timer after a failed send, so the user can retry at once."""
        try:
            self._users.update_item(
                Key={"email": email},
                UpdateExpression="REMOVE token_sent_at",
                ConditionExpression="token_hash = :hash",
                ExpressionAttributeValues={":hash": token_hash},
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

    def mark_verified(self, email: str, token_hash: str, *, now: datetime) -> None:
        """Verify the user and consume the token, if it is still the stored one.

        Raises:
            TokenMismatchError: The token was replaced, used or expired meanwhile.
        """
        try:
            self._users.update_item(
                Key={"email": email},
                UpdateExpression=(
                    "SET verified = :true, verified_at = :now "
                    "REMOVE token_hash, token_expires_at, token_sent_at"
                ),
                ConditionExpression="token_hash = :hash AND token_expires_at > :utc",
                ExpressionAttributeValues={
                    ":true": True,
                    ":now": now.isoformat(),
                    ":hash": token_hash,
                    ":utc": _utc(now),
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise TokenMismatchError() from exc
            raise

    def claim_experience_send(self, email: str, *, now: datetime, cutoff: datetime) -> str:
        """Claim the right to send one Experience email: "ok", "unverified" or "too_soon".

        One atomic update, so concurrent requests cannot both pass: the user must be
        verified and have had no Experience email since `cutoff`.
        """
        try:
            self._users.update_item(
                Key={"email": email},
                UpdateExpression="SET last_experience_at = :now",
                ConditionExpression=(
                    "verified = :true AND "
                    "(attribute_not_exists(last_experience_at) OR last_experience_at < :cutoff)"
                ),
                ExpressionAttributeValues={
                    ":true": True,
                    ":now": _utc(now),
                    ":cutoff": _utc(cutoff),
                },
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
            user = self.get(email) or {}
            return "too_soon" if user.get("verified") is True else "unverified"
        return "ok"

    def release_experience_send(self, email: str, *, now: datetime) -> None:
        """Undo a claim after a failed send, so the user can retry straight away."""
        try:
            self._users.update_item(
                Key={"email": email},
                UpdateExpression="REMOVE last_experience_at",
                ConditionExpression="last_experience_at = :now",
                ExpressionAttributeValues={":now": _utc(now)},
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
