"""Alert decision rules (pure)."""

from datetime import timedelta

import pytest

from drysg.core.alerts import (
    ALERT_COOLDOWN,
    RAIN_REALERT_GAP,
    SCORE_DROP_THRESHOLD,
    decide_alert,
    rain_started,
    should_raise_baseline,
)
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
        # Score drop within Wait (held for two runs: see the persistence test)
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


@pytest.mark.parametrize(
    ("prev_score", "alerts"),
    [
        (None, False),  # first run after a method change: not yet confirmed
        (60, False),  # one-run dip: the previous run was close to the baseline
        (68 - SCORE_DROP_THRESHOLD, True),  # the drop held for two runs
    ],
)
def test_score_drop_must_hold_for_two_runs(prev_score, alerts):
    session = make_session(baseline_score=68, baseline_band=Band.WAIT)
    snapshot = make_snapshot(68 - SCORE_DROP_THRESHOLD, prev_score=prev_score)
    assert (decide_alert(session, snapshot, NOW) is not None) is alerts


def test_band_drop_and_rain_still_alert_immediately():
    session = make_session(baseline_score=85, baseline_band=Band.HANG_NOW)
    assert decide_alert(session, make_snapshot(60, prev_score=None), NOW) is not None
    assert decide_alert(session, make_snapshot(80, veto_reason=RAIN), NOW) is not None


def test_rain_alerts_even_when_baseline_already_do_not_hang():
    session = make_session(baseline_score=45, baseline_band=Band.DO_NOT_HANG)
    alert = decide_alert(session, make_snapshot(20, veto_reason=RAIN), NOW)
    assert alert is not None
    assert alert.reason == RAIN


def test_no_repeat_alert_while_rain_continues():
    session = make_session(baseline_score=20, baseline_band=Band.DO_NOT_HANG, baseline_veto=True)
    assert decide_alert(session, make_snapshot(15, veto_reason=RAIN), NOW) is None


def test_baseline_from_another_method_only_alerts_on_rain():
    # The session was baselined before a deploy changed the scoring method.
    session = make_session(baseline_score=85, baseline_band=Band.HANG_NOW,
                           baseline_method="weighted-v1")
    drop = make_snapshot(30)
    rain = make_snapshot(30, veto_reason=RAIN)
    for snapshot in (drop, rain):
        snapshot.result.method = "dp-percentile-2024"
    assert decide_alert(session, drop, NOW) is None
    alert = decide_alert(session, rain, NOW)
    assert alert is not None and alert.reason == RAIN
    assert alert.new_method == "dp-percentile-2024"  # the notifier re-baselines on it


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
        assert decide_alert(session, make_snapshot(50, prev_score=50), NOW) is None

    def test_allows_after_cooldown(self):
        session = make_session(
            baseline_score=68,
            baseline_band=Band.WAIT,
            last_alert_at=NOW - ALERT_COOLDOWN,
            last_alert_band=Band.WAIT,
        )
        assert decide_alert(session, make_snapshot(50, prev_score=50), NOW) is not None

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


# --- Rain returning and the baseline moving back up ---------------------------

RAINED_ON = {"baseline_score": 20, "baseline_band": Band.DO_NOT_HANG, "baseline_veto": True}


def test_rain_started_needs_a_dry_previous_run():
    assert rain_started(make_snapshot(20, veto_reason=RAIN, prev_band=Band.WAIT))
    assert not rain_started(make_snapshot(20, veto_reason=RAIN, prev_band=Band.DO_NOT_HANG))
    assert not rain_started(make_snapshot(20, veto_reason=RAIN))  # previous run unknown
    assert not rain_started(make_snapshot(60, prev_band=Band.WAIT))


def test_rain_after_one_dry_run_alerts_a_session_already_told_about_rain():
    # Started (or last alerted) in the rain; it stopped for one run and is back.
    session = make_session(**RAINED_ON)
    alert = decide_alert(session, make_snapshot(15, veto_reason=RAIN, prev_band=Band.WAIT), NOW)
    assert alert is not None and alert.reason == RAIN


def test_continuing_rain_still_does_not_repeat():
    session = make_session(**RAINED_ON, last_alert_at=NOW - timedelta(hours=2),
                           last_alert_band=Band.DO_NOT_HANG)
    snapshot = make_snapshot(15, veto_reason=RAIN, prev_band=Band.DO_NOT_HANG)
    assert decide_alert(session, snapshot, NOW) is None


@pytest.mark.parametrize(
    ("since_last_alert", "alerts"),
    [(timedelta(minutes=10), False), (RAIN_REALERT_GAP, True)],
)
def test_returning_rain_alerts_at_most_every_30_minutes(since_last_alert, alerts):
    session = make_session(**RAINED_ON, last_alert_at=NOW - since_last_alert,
                           last_alert_band=Band.DO_NOT_HANG)
    snapshot = make_snapshot(15, veto_reason=RAIN, prev_band=Band.WAIT)
    assert (decide_alert(session, snapshot, NOW) is not None) is alerts


@pytest.mark.parametrize(
    ("baseline", "prev_band", "new_score", "raises"),
    [
        (RAINED_ON, Band.WAIT, 75, True),  # dry for two runs
        (RAINED_ON, Band.DO_NOT_HANG, 75, False),  # only one dry run so far
        (RAINED_ON, None, 75, False),  # previous run unknown
        ({"baseline_score": 55, "baseline_band": Band.WAIT}, Band.HANG_NOW, 80, True),
        ({"baseline_score": 55, "baseline_band": Band.WAIT}, Band.WAIT, 80, False),
        ({"baseline_score": 85, "baseline_band": Band.HANG_NOW}, Band.HANG_NOW, 95, False),
    ],
)
def test_baseline_rises_once_the_band_is_better_for_two_runs(
    baseline, prev_band, new_score, raises
):
    session = make_session(**baseline)
    snapshot = make_snapshot(new_score, prev_band=prev_band)
    assert should_raise_baseline(session, snapshot, NOW) is raises


def test_ineligible_sessions_never_raise_their_baseline():
    snapshot = make_snapshot(75, prev_band=Band.WAIT)
    for session in (
        make_session(**RAINED_ON, status=SessionStatus.STOPPED),
        make_session(**RAINED_ON, expires_at=NOW),
        make_session(**RAINED_ON, area="Hougang"),
    ):
        assert not should_raise_baseline(session, snapshot, NOW)


def test_rain_stopping_is_not_read_as_a_score_drop():
    # Rain kept the band at Do Not Hang while the score stayed high. After the rain the
    # score is 30 lower but the band is better: the baseline rises, no "score fell" email.
    session = make_session(baseline_score=90, baseline_band=Band.DO_NOT_HANG, baseline_veto=True)
    snapshot = make_snapshot(60, prev_score=60, prev_band=Band.WAIT)
    assert decide_alert(session, snapshot, NOW) is None
    assert should_raise_baseline(session, snapshot, NOW)
