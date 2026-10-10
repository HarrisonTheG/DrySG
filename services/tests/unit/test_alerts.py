"""Alert decision rules (pure)."""

from datetime import timedelta

import pytest

from drysg.core.alerts import ALERT_COOLDOWN, SCORE_DROP_THRESHOLD, decide_alert
from drysg.core.models import Band, SessionStatus
from tests.factories import NOW, make_session, make_snapshot

RAIN = "Rain detected at nearby station (0.4 mm in last 5 min)"


@pytest.mark.parametrize(
    ("baseline", "new_score", "veto", "alerts"),
    [
        # Band drops
        ((85, Band.HANG_NOW), 60, None, True),
        ((85, Band.HANG_NOW), 40, None, True),
        ((60, Band.WAIT), 40, None, True),
        # Rain veto, even if the score barely moved
        ((85, Band.HANG_NOW), 49, RAIN, True),
        # Score drop within Wait
        ((68, Band.WAIT), 68 - SCORE_DROP_THRESHOLD, None, True),
        ((68, Band.WAIT), 68 - SCORE_DROP_THRESHOLD + 1, None, False),
        # Big drop but still Hang Now: not worth an email
        ((100, Band.HANG_NOW), 72, None, False),
        # Same or better
        ((85, Band.HANG_NOW), 85, None, False),
        ((60, Band.WAIT), 90, None, False),
        ((40, Band.DO_NOT_HANG), 30, None, False),
    ],
)
def test_deterioration_vs_baseline(baseline, new_score, veto, alerts):
    session = make_session(baseline_score=baseline[0], baseline_band=baseline[1])
    alert = decide_alert(session, make_snapshot(new_score, veto_reason=veto), NOW)
    assert (alert is not None) is alerts


def test_rain_alerts_even_when_baseline_already_do_not_hang():
    session = make_session(baseline_score=45, baseline_band=Band.DO_NOT_HANG)
    alert = decide_alert(session, make_snapshot(20, veto_reason=RAIN), NOW)
    assert alert is not None
    assert alert.reason == RAIN


def test_no_repeat_alert_while_rain_continues():
    session = make_session(baseline_score=20, baseline_band=Band.DO_NOT_HANG, baseline_veto=True)
    assert decide_alert(session, make_snapshot(15, veto_reason=RAIN), NOW) is None


def test_slow_decline_is_caught_against_baseline():
    """Small drops each run never trip a per-run rule, but do vs the session baseline."""
    session = make_session(baseline_score=75, baseline_band=Band.HANG_NOW)
    scores = [72, 71, 70, 69]
    alerts = [decide_alert(session, make_snapshot(s), NOW) for s in scores]
    assert alerts[:3] == [None, None, None]
    assert alerts[3] is not None


def test_alert_message_contents():
    session = make_session()
    snapshot = make_snapshot(40, veto_reason=RAIN)
    alert = decide_alert(session, snapshot, NOW)
    assert alert.alert_id == f"{session.session_id}#{snapshot.updated_at.isoformat()}"
    assert (alert.prev_score, alert.prev_band) == (85, Band.HANG_NOW)
    assert (alert.new_score, alert.new_band, alert.new_veto) == (40, Band.DO_NOT_HANG, True)
    assert alert.email == session.email
    assert alert.reason == RAIN


def test_band_drop_reason():
    alert = decide_alert(make_session(), make_snapshot(60), NOW)
    assert alert.reason == "Drying conditions dropped from Hang Now to Wait"


@pytest.mark.parametrize(
    "session",
    [
        make_session(status=SessionStatus.STOPPED),
        make_session(status=SessionStatus.EXPIRED),
        make_session(expires_at=NOW - timedelta(seconds=1)),
        make_session(area="Hougang"),
    ],
    ids=["stopped", "expired-status", "past-expiry", "other-area"],
)
def test_ineligible_sessions_never_alert(session):
    assert decide_alert(session, make_snapshot(10, veto_reason=RAIN), NOW) is None


class TestCooldown:
    def test_suppresses_non_escalating_alert(self):
        session = make_session(
            baseline_score=68,
            baseline_band=Band.WAIT,
            last_alert_at=NOW - ALERT_COOLDOWN + timedelta(minutes=1),
            last_alert_band=Band.WAIT,
        )
        assert decide_alert(session, make_snapshot(50), NOW) is None

    def test_allows_after_cooldown(self):
        session = make_session(
            baseline_score=68,
            baseline_band=Band.WAIT,
            last_alert_at=NOW - ALERT_COOLDOWN,
            last_alert_band=Band.WAIT,
        )
        assert decide_alert(session, make_snapshot(50), NOW) is not None

    def test_drop_to_do_not_hang_bypasses_cooldown(self):
        session = make_session(
            baseline_score=60,
            baseline_band=Band.WAIT,
            last_alert_at=NOW - timedelta(minutes=5),
            last_alert_band=Band.WAIT,
        )
        assert decide_alert(session, make_snapshot(40), NOW) is not None

    def test_new_rain_bypasses_cooldown(self):
        session = make_session(
            baseline_score=45,
            baseline_band=Band.DO_NOT_HANG,
            last_alert_at=NOW - timedelta(minutes=5),
            last_alert_band=Band.DO_NOT_HANG,
        )
        assert decide_alert(session, make_snapshot(30, veto_reason=RAIN), NOW) is not None
