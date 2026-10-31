"""Email verification shared by the API: register an email, then confirm it with a token.

The raw token only exists in the emailed link. The User table stores its SHA-256 hash.
"""

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from drysg.alerting.email_template import EmailContent, render_verification
from drysg.db import TokenIssueRejectedError, TokenMismatchError, UserRepository
from drysg.sessions import normalize_email

logger = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(hours=24)
# At most one verification email per address per minute, so the endpoint cannot be
# used to flood an inbox or use up the SES sending quota.
RESEND_INTERVAL = timedelta(seconds=60)


class InvalidTokenError(ValueError):
    """The verification token is wrong, already used or expired."""


class ResendTooSoonError(RuntimeError):
    """A verification email was sent to this address less than a minute ago."""


class EmailUnavailableError(RuntimeError):
    """The verification email could not be sent."""


@dataclass(frozen=True)
class UserStatus:
    email: str
    verified: bool


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class UserService:
    """Register and verify emails. `send_email(to, content)` delivers the link."""

    def __init__(
        self,
        repository: UserRepository,
        send_email: Callable[[str, EmailContent], None],
        *,
        app_base_url: str,
    ) -> None:
        self.repository = repository
        self.send_email = send_email
        self.app_base_url = app_base_url

    def register(self, email: str, *, now: datetime) -> UserStatus:
        """Send a new verification link, unless the email is already verified.

        Raises:
            InvalidEmailError: The email is not a usable address.
            ResendTooSoonError: The last link was sent less than a minute ago.
            EmailUnavailableError: Sending failed; the user can retry straight away.
        """
        email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        try:
            self.repository.issue_token(
                email,
                token_hash,
                now=now,
                expires_at=now + TOKEN_LIFETIME,
                resend_after=now - RESEND_INTERVAL,
            )
        except TokenIssueRejectedError:
            user = self.repository.get(email) or {}
            if user.get("verified") is True:
                return UserStatus(email=email, verified=True)
            raise ResendTooSoonError(
                "A verification email was just sent. Wait a minute before asking again"
            ) from None

        hours = int(TOKEN_LIFETIME.total_seconds() // 3600)
        content = render_verification(email, token, self.app_base_url, hours)
        try:
            self.send_email(email, content)
        except Exception as exc:
            logger.warning("Verification email to %s failed: %s", email, exc)
            self.repository.allow_resend(email, token_hash)
            raise EmailUnavailableError(
                "The verification email could not be sent. Try again later"
            ) from exc
        return UserStatus(email=email, verified=False)

    def verify(self, email: str, token: str, *, now: datetime) -> UserStatus:
        """Confirm the email. Repeating it after success returns verified again.

        Raises:
            InvalidEmailError: The email is not a usable address.
            InvalidTokenError: Wrong, replaced, used or expired token.
        """
        email = normalize_email(email)
        user = self.repository.get(email)
        if user is not None and user.get("verified") is True:
            return UserStatus(email=email, verified=True)
        token_hash = hash_token(token or "")
        stored = (user or {}).get("token_hash")
        expires = (user or {}).get("token_expires_at")
        invalid = InvalidTokenError("This verification link is invalid or has expired")
        if not stored or not hmac.compare_digest(stored, token_hash):
            raise invalid
        if not expires or datetime.fromisoformat(expires) <= now:
            raise invalid
        try:
            self.repository.mark_verified(email, token_hash, now=now)
        except TokenMismatchError:
            # A double click can verify in another request between our read and update.
            if (self.repository.get(email) or {}).get("verified") is True:
                return UserStatus(email=email, verified=True)
            raise invalid from None
        return UserStatus(email=email, verified=True)
