"""Monitoring lifecycle shared by the API and other backend callers."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from email_validator import EmailNotValidError, validate_email

from drysg.core.models import AreaSnapshot, MonitoringSession, SessionStatus
from drysg.core.scoring import SCORE_METHOD
from drysg.data import DataUnavailableError
from drysg.data.clients import validate_postal_code
from drysg.data.normalization import require_aware
from drysg.db import ConcurrentSessionUpdateError, SessionRepository

SESSION_LIFETIME = timedelta(hours=12)


class InvalidEmailError(ValueError):
    """The supplied email is not a usable mailbox address."""


class InvalidSessionIdError(ValueError):
    """Session identifiers must be UUID4 bearer tokens."""


class EmailNotVerifiedError(ValueError):
    """The email has not been verified in the product's User table."""


class SessionNotFoundError(LookupError):
    """No stored session has the requested identifier."""


def normalize_email(value: str) -> str:
    """Validate without DNS lookups and normalize to the User table's lowercase key."""
    if not isinstance(value, str):
        raise InvalidEmailError("email must be a valid email address")
    try:
        return validate_email(
            value.strip(), check_deliverability=False, allow_smtputf8=False
        ).normalized.lower()
    except EmailNotValidError as exc:
        raise InvalidEmailError("email must be a valid email address") from exc


def normalize_session_id(value: str) -> str:
    """Validate and canonicalize a UUID4 session identifier."""
    try:
        identifier = UUID(value)
        if identifier.version != 4:
            raise ValueError("Not UUID4")
        return str(identifier)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidSessionIdError("id must be a UUID4 session identifier") from exc


class SessionService:
    """Validate session operations and preserve a single active session per user."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    def start(
        self,
        email: str,
        postal_code: str,
        *,
        snapshot_for_postal: Callable[[str], AreaSnapshot],
        now: datetime,
    ) -> MonitoringSession:
        """Start a verified user's session from the current weather baseline.

        Args:
            snapshot_for_postal: Resolve the normalized postal code to a fresh snapshot.
            now: Time used for session expiry and replacement.

        Returns:
            The persisted active session, including its alert baseline.

        Raises:
            EmailNotVerifiedError: Email ownership has not been verified.
            DataUnavailableError: The snapshot uses an obsolete scoring method.
            ConcurrentSessionUpdateError: Concurrent starts exhausted retries.
        """
        email = normalize_email(email)
        postal_code = validate_postal_code(postal_code)
        require_aware(now)
        # DynamoDB TTL stores whole seconds; keep POST and later GET timestamps equal.
        now = now.replace(microsecond=0)
        session = None
        for _ in range(3):
            user = self._repository.get_user(email)
            if user is None or user.get("verified") is not True:
                raise EmailNotVerifiedError(
                    "Verify your email before starting monitoring"
                )
            if session is None:
                snapshot = snapshot_for_postal(postal_code)
                if snapshot.result.method != SCORE_METHOD:
                    raise DataUnavailableError("Weather scoring is being refreshed")
                session = MonitoringSession(
                    session_id=str(uuid4()),
                    email=email,
                    postal_code=postal_code,
                    area=snapshot.area,
                    status=SessionStatus.ACTIVE,
                    started_at=now,
                    expires_at=now + SESSION_LIFETIME,
                    baseline_score=snapshot.result.score,
                    baseline_band=snapshot.result.band,
                    baseline_veto=snapshot.result.veto,
                    baseline_method=snapshot.result.method,
                )
            previous_id = user.get("active_session_id")
            previous = self._repository.get(previous_id) if previous_id else None
            try:
                self._repository.replace(
                    session,
                    previous_id=previous_id,
                    stop_previous=(
                        previous is not None
                        and previous.status is SessionStatus.ACTIVE
                        and previous.expires_at > now
                    ),
                )
                return session
            except ConcurrentSessionUpdateError:
                continue
        raise ConcurrentSessionUpdateError("Session changed concurrently; try again")

    def get(self, session_id: str) -> MonitoringSession:
        """Return a stored session or raise SessionNotFoundError."""
        session = self._repository.get(normalize_session_id(session_id))
        if session is None:
            raise SessionNotFoundError("Monitoring session not found")
        return session

    def stop(self, session_id: str, *, now: datetime) -> MonitoringSession:
        """Stop monitoring idempotently; raise SessionNotFoundError for missing IDs."""
        session_id = normalize_session_id(session_id)
        require_aware(now)
        session = self._repository.stop(session_id, now)
        if session is None:
            raise SessionNotFoundError("Monitoring session not found")
        return session
