"""Alert rules: decide whether a monitoring session should be alerted.

Pure functions only. Each session is compared with its own baseline (the state
the user was last told about) rather than the previous 5-minute snapshot, so
slow declines are caught and scores flapping around a threshold don't spam.
"""

from datetime import datetime, timedelta

from drysg.core.models import (
    AlertMessage,
    AreaSnapshot,
    Band,
    MonitoringSession,
    ScoreResult,
    SessionStatus,
)

# A drop of at least this many points below the baseline alerts, unless the
# new band is still Hang Now (still fine to dry, so not worth an email).
SCORE_DROP_THRESHOLD = 15
# Minimum gap between alerts for one session. Bypassed when conditions
# escalate to Do Not Hang or a new rain veto.
ALERT_COOLDOWN = timedelta(minutes=60)

_BAND_RANK = {Band.HANG_NOW: 0, Band.WAIT: 1, Band.DO_NOT_HANG: 2}


def band_worsened(old: Band, new: Band) -> bool:
    return _BAND_RANK[new] > _BAND_RANK[old]


def deterioration_reason(session: MonitoringSession, new: ScoreResult) -> str | None:
    """Why conditions are worse than the session's baseline, or None if they aren't."""
    if new.veto and not session.baseline_veto:
        return new.veto_reason
    if band_worsened(session.baseline_band, new.band):
        return f"Drying conditions dropped from {session.baseline_band} to {new.band}"
    drop = session.baseline_score - new.score
    if drop >= SCORE_DROP_THRESHOLD and new.band is not Band.HANG_NOW:
        return f"Drying score fell from {session.baseline_score} to {new.score}"
    return None


def _is_escalation(session: MonitoringSession, new: ScoreResult) -> bool:
    """Escalations skip the cooldown: rain starting, or a first drop to Do Not Hang."""
    new_veto = new.veto and not session.baseline_veto
    new_do_not_hang = (
        new.band is Band.DO_NOT_HANG and session.last_alert_band is not Band.DO_NOT_HANG
    )
    return new_veto or new_do_not_hang


def in_cooldown(session: MonitoringSession, now: datetime) -> bool:
    return session.last_alert_at is not None and now - session.last_alert_at < ALERT_COOLDOWN


def decide_alert(
    session: MonitoringSession, snapshot: AreaSnapshot, now: datetime
) -> AlertMessage | None:
    """Return the alert to send for this session, or None."""
    if session.status is not SessionStatus.ACTIVE or session.expires_at <= now:
        return None
    if snapshot.area != session.area:
        return None

    new = snapshot.result
    reason = deterioration_reason(session, new)
    if reason is None:
        return None
    if in_cooldown(session, now) and not _is_escalation(session, new):
        return None

    return AlertMessage(
        alert_id=f"{session.session_id}#{snapshot.updated_at.isoformat()}",
        session_id=session.session_id,
        email=session.email,
        area=session.area,
        prev_score=session.baseline_score,
        prev_band=session.baseline_band,
        new_score=new.score,
        new_band=new.band,
        new_veto=new.veto,
        reason=reason,
        snapshot_ts=snapshot.updated_at,
    )
