"""Map persisted monitoring sessions to the frontend's minimal contract."""

from datetime import datetime

from drysg.core.models import MonitoringSession, SessionStatus

from .models import LaundrySession, RecommendationState
from .recommendation import SGT


def to_laundry_session(session: MonitoringSession, now: datetime) -> LaundrySession:
    """Return Singapore timestamps and logical expiry without relying on DynamoDB TTL."""
    return LaundrySession(
        id=session.session_id,
        postalCode=session.postal_code,
        email=session.email,
        areaName=session.area,
        startedAt=session.started_at.astimezone(SGT).isoformat(),
        expiresAt=session.expires_at.astimezone(SGT).isoformat(),
        active=session.status is SessionStatus.ACTIVE and session.expires_at > now,
        lastAlertState=(
            RecommendationState[session.last_alert_band.name]
            if session.last_alert_band is not None
            else None
        ),
    )
