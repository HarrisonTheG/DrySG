"""Builders for test data. Defaults describe an active session in Bishan baselined at Hang Now."""

from datetime import datetime, timedelta

from drysg.core.models import (
    AlertMessage,
    AreaSnapshot,
    Band,
    Conditions,
    MonitoringSession,
    ScoreResult,
    SessionStatus,
    SubScores,
)
from drysg.core.scoring import band_for

NOW = datetime.fromisoformat("2026-10-04T14:00:00+08:00")


def make_session(**overrides) -> MonitoringSession:
    fields = {
        "session_id": "11111111-1111-4111-8111-111111111111",
        "email": "user@example.com",
        "postal_code": "570150",
        "area": "Bishan",
        "status": SessionStatus.ACTIVE,
        "started_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(hours=11),
        "baseline_score": 85,
        "baseline_band": Band.HANG_NOW,
    }
    fields.update(overrides)
    return MonitoringSession(**fields)


def make_snapshot(
    score: int, *, area: str = "Bishan", veto_reason: str | None = None, at: datetime = NOW
) -> AreaSnapshot:
    return AreaSnapshot(
        area=area,
        region="central",
        latitude=1.35,
        longitude=103.84,
        conditions=Conditions(),
        result=ScoreResult(
            score=score,
            band=band_for(score),
            sub_scores=SubScores(),
            veto=veto_reason is not None,
            veto_reason=veto_reason,
        ),
        updated_at=at,
    )


def make_alert(**overrides) -> AlertMessage:
    fields = {
        "alert_id": f"11111111-1111-4111-8111-111111111111#{NOW.isoformat()}",
        "session_id": "11111111-1111-4111-8111-111111111111",
        "email": "user@example.com",
        "area": "Bishan",
        "prev_score": 85,
        "prev_band": Band.HANG_NOW,
        "new_score": 30,
        "new_band": Band.DO_NOT_HANG,
        "new_veto": True,
        "reason": "Rain detected at nearby station (0.4 mm in last 5 min)",
        "snapshot_ts": NOW,
    }
    fields.update(overrides)
    return AlertMessage(**fields)
