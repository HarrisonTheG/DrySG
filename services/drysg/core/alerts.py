"""Alert rules: decide whether a monitoring session should be alerted.

Pure functions only. Each session is compared with its own baseline (the state
the user was last told about) rather than the previous 5-minute snapshot, so
slow declines are caught and scores flapping around a threshold don't spam.

The baseline moves down when an alert is sent (the notifier) and back up once the
band has been better for two runs in a row (the trigger, via `should_raise_baseline`),
so a later decline, or rain after a dry spell, alerts again.
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
# new band is still Hang Now (still fine to dry, so not worth an email). The score
# is a percentile, and mid-range percentiles are close together in drying potential,
# so single-station noise can swing it by this much for one 5-minute run. The drop
# must therefore also hold in the previous run (the snapshot's prev_score) to alert.
SCORE_DROP_THRESHOLD = 15
# Minimum gap between alerts for one session. Bypassed when conditions
# escalate to Do Not Hang or a new rain veto.
ALERT_COOLDOWN = timedelta(minutes=60)
# Rain coming back after a single dry run alerts a session already told about rain,
# but at most this often. Two dry runs raise the baseline instead (new rain then).
RAIN_REALERT_GAP = timedelta(minutes=30)

_BAND_RANK = {Band.HANG_NOW: 0, Band.WAIT: 1, Band.DO_NOT_HANG: 2}


def band_worsened(old: Band, new: Band) -> bool:
    return _BAND_RANK[new] > _BAND_RANK[old]


def _better(band: Band, than: Band) -> bool:
    return _BAND_RANK[band] < _BAND_RANK[than]


def rain_started(snapshot: AreaSnapshot) -> bool:
    """Rain in this run after a run without it (rain always makes the band Do Not Hang)."""
    return (
        snapshot.result.veto
        and snapshot.prev_band is not None
        and snapshot.prev_band is not Band.DO_NOT_HANG
    )


def _is_new_rain(session: MonitoringSession, new: ScoreResult, rain_restarted: bool) -> bool:
    return new.veto and (not session.baseline_veto or rain_restarted)


def deterioration_reason(
    session: MonitoringSession,
    new: ScoreResult,
    prev_score: int | None = None,
    *,
    rain_restarted: bool = False,
) -> str | None:
    """Why conditions are worse than the session's baseline, or None if they aren't.

    `prev_score` is the area's score in the previous ingest run (same method), used so a
    score drop only alerts once it has lasted two runs. Band drops and rain alert at once.
    `rain_restarted` (see `rain_started`) counts rain as new even if the baseline is rain.
    """
    if _is_new_rain(session, new, rain_restarted):
        return new.veto_reason
    if session.baseline_method != new.method:
        # The baseline score came from another scoring method (e.g. before a deploy that
        # rebuilt the percentile table), so a score or band drop would be an artefact.
        # Only the rain veto, which means the same under every method, is compared.
        return None
    if band_worsened(session.baseline_band, new.band):
        return f"Drying conditions dropped from {session.baseline_band} to {new.band}"
    drop = session.baseline_score - new.score
    held = prev_score is not None and session.baseline_score - prev_score >= SCORE_DROP_THRESHOLD
    if drop >= SCORE_DROP_THRESHOLD and held and new.band is not Band.HANG_NOW:
        return f"Drying score fell from {session.baseline_score} to {new.score}"
    return None


def _is_escalation(session: MonitoringSession, new: ScoreResult, rain_restarted: bool) -> bool:
    """Escalations skip the cooldown: rain starting, or a first drop to Do Not Hang."""
    new_veto = _is_new_rain(session, new, rain_restarted)
    new_do_not_hang = (
        new.band is Band.DO_NOT_HANG and session.last_alert_band is not Band.DO_NOT_HANG
    )
    return new_veto or new_do_not_hang


def in_cooldown(session: MonitoringSession, now: datetime) -> bool:
    return session.last_alert_at is not None and now - session.last_alert_at < ALERT_COOLDOWN


def _eligible(session: MonitoringSession, snapshot: AreaSnapshot, now: datetime) -> bool:
    return (
        session.status is SessionStatus.ACTIVE
        and session.expires_at > now
        and snapshot.area == session.area
    )


def should_raise_baseline(
    session: MonitoringSession, snapshot: AreaSnapshot, now: datetime
) -> bool:
    """True when the band has been better than the baseline for this run and the last.

    The trigger then moves the baseline up to the new state. Requiring two runs keeps
    a one-off good reading (e.g. a dry 5 minutes between showers) from re-arming alerts.
    """
    return (
        _eligible(session, snapshot, now)
        and snapshot.prev_band is not None
        and _better(snapshot.result.band, session.baseline_band)
        and _better(snapshot.prev_band, session.baseline_band)
    )


def decide_alert(
    session: MonitoringSession, snapshot: AreaSnapshot, now: datetime
) -> AlertMessage | None:
    """Return the alert to send for this session, or None."""
    if not _eligible(session, snapshot, now):
        return None
    if should_raise_baseline(session, snapshot, now):
        # Better than the baseline (e.g. rain stopped), so nothing got worse. A score
        # drop from a high score during rain would otherwise read as a decline.
        return None

    new = snapshot.result
    # Only matters when the baseline is already rain: otherwise any rain is new.
    rain_restarted = session.baseline_veto and rain_started(snapshot)
    reason = deterioration_reason(
        session, new, snapshot.prev_score, rain_restarted=rain_restarted
    )
    if reason is None:
        return None
    if rain_restarted and session.last_alert_at is not None and (
        now - session.last_alert_at < RAIN_REALERT_GAP
    ):
        return None
    if in_cooldown(session, now) and not _is_escalation(session, new, rain_restarted):
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
        new_method=new.method,
        reason=reason,
        snapshot_ts=snapshot.updated_at,
    )
