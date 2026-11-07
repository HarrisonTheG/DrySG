"""Evaluate the alert rules by replaying real NEA history (report Section 4.2).

Offline and email-free: every 5-minute mark of each cached day is scored with the live
`normalize_weather` + `compute_score`, then simulated monitoring sessions are run through
the live `decide_alert` (and through simpler alert policies for comparison). Alerts are
counted, not sent.

    cd services
    # full year from the bulk CSVs (see README.md in this folder)
    .venv/bin/python -I scripts/evaluation/evaluate_alerts_replay.py --source csv
    # or the 24 days fetched by download_history.py
    .venv/bin/python -I scripts/evaluation/evaluate_alerts_replay.py --source api

Assumptions (stated in the report):
  * A user hangs laundry at 08:00, 10:00 or 12:00 when it is not raining, and leaves it
    out for SESSION_HOURS. Sessions that start under a rain veto are not simulated.
  * Ground truth for rain is the observed 5-minute rainfall at the area's nearest gauge.
    A rain onset is rain after at least DRY_GAP of no rain at that gauge.
  * The user counts as warned about an onset if a rain alert reached them in the
    WARN_WINDOW before it (or at it).
"""

import argparse
import contextlib
import json
import logging
import multiprocessing
import os
import statistics
import sys
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import (  # noqa: E402
    API_CACHE,
    CSV_CACHE,
    SERVICES,
    api_days,
    area_series,
    csv_days,
    load_day,
    parse_days,
)

import drysg.core.alerts as alert_rules  # noqa: E402
from drysg.core.alerts import decide_alert, should_raise_baseline  # noqa: E402
from drysg.core.models import (  # noqa: E402
    AlertMessage,
    AreaSnapshot,
    Band,
    MonitoringSession,
    SessionStatus,
)

logger = logging.getLogger("evaluate_alerts_replay")

SGT_START_HOURS = (8, 10, 12)
SESSION_HOURS = 8
STEP = timedelta(minutes=5)
DRY_GAP = timedelta(minutes=30)
WARN_WINDOW = timedelta(hours=2)
MIN_COVERAGE = 0.9  # share of 5-minute marks a session must have data for

_RANK = {Band.HANG_NOW: 0, Band.WAIT: 1, Band.DO_NOT_HANG: 2}


# ---------------------------------------------------------------- alert policies
#
# A policy gets the session's snapshots (first = when the laundry was hung) and returns
# the alerts as (frame index, is_rain_alert).

Alerts = list[tuple[int, bool]]


def _new_session(first: AreaSnapshot) -> MonitoringSession:
    return MonitoringSession(
        session_id="eval",
        email="eval@example.invalid",
        postal_code="000000",
        area=first.area,
        status=SessionStatus.ACTIVE,
        started_at=first.updated_at,
        expires_at=first.updated_at + timedelta(hours=12),
        baseline_score=first.result.score,
        baseline_band=first.result.band,
        baseline_veto=first.result.veto,
        baseline_method=first.result.method,
    )


def _moved(session: MonitoringSession, snapshot: AreaSnapshot, **extra) -> MonitoringSession:
    return session.model_copy(
        update={
            "baseline_score": snapshot.result.score,
            "baseline_band": snapshot.result.band,
            "baseline_veto": snapshot.result.veto,
            "baseline_method": snapshot.result.method,
            **extra,
        }
    )


def drysg_policy(
    run: list[AreaSnapshot],
    *,
    hold: bool = True,
    baseline: bool = True,
) -> Alerts:
    """The live rules, as the alert trigger and notifier apply them.

    hold=False: a score drop alerts on its first run (no two-run confirmation).
    baseline=False: compare with the previous snapshot instead of what the user was told.
    """
    session = _new_session(run[0])
    alerts: Alerts = []
    for index, snapshot in enumerate(run[1:], start=1):
        now = snapshot.updated_at
        seen = (
            snapshot if hold else snapshot.model_copy(update={"prev_score": snapshot.result.score})
        )
        message: AlertMessage | None = decide_alert(session, seen, now)
        if message is not None:
            alerts.append((index, message.new_veto))
            session = _moved(session, snapshot, last_alert_at=now, last_alert_band=message.new_band)
        elif not baseline or should_raise_baseline(session, snapshot, now):
            session = _moved(session, snapshot)
    return alerts


@contextlib.contextmanager
def no_cooldown() -> Iterator[None]:
    saved = alert_rules.ALERT_COOLDOWN
    alert_rules.ALERT_COOLDOWN = timedelta(0)
    try:
        yield
    finally:
        alert_rules.ALERT_COOLDOWN = saved


def naive_change_policy(run: list[AreaSnapshot]) -> Alerts:
    """Email whenever the band gets worse or rain starts, compared with 5 minutes ago."""
    alerts: Alerts = []
    for index in range(1, len(run)):
        before, now = run[index - 1].result, run[index].result
        rain = now.veto and not before.veto
        if rain or _RANK[now.band] > _RANK[before.band]:
            alerts.append((index, now.veto))
    return alerts


def observed_rain_policy(run: list[AreaSnapshot]) -> Alerts:
    """A reactive rain alarm: email when the nearest gauge starts measuring rain (no forecast)."""
    alerts: Alerts = []
    for index in range(1, len(run)):
        if _raining(run[index]) and not _raining(run[index - 1]):
            alerts.append((index, True))
    return alerts


POLICIES: dict[str, Callable[[list[AreaSnapshot]], Alerts]] = {
    "DrySG (deployed rules)": drysg_policy,
    "DrySG without two-run hold": lambda run: drysg_policy(run, hold=False),
    "DrySG without cooldown": lambda run: _without_cooldown(run),
    "DrySG vs previous snapshot (no baseline)": lambda run: drysg_policy(run, baseline=False),
    "Naive: any worsening vs 5 min ago": naive_change_policy,
    "Reactive: observed rain only": observed_rain_policy,
}


def _without_cooldown(run: list[AreaSnapshot]) -> Alerts:
    with no_cooldown():
        return drysg_policy(run)


# ---------------------------------------------------------------- ground truth


RAIN = 3  # severity of a rain veto, above every band


def _severity(snapshot: AreaSnapshot) -> int:
    return RAIN if snapshot.result.veto else _RANK[snapshot.result.band]


def _raining(snapshot: AreaSnapshot) -> bool:
    rain = snapshot.conditions.rainfall_mm
    return rain is not None and rain.value > 0


def rain_onsets(
    day_series: list[AreaSnapshot], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """(onset, warning window start) for rain in (start, end) after DRY_GAP without any.

    A warning only counts for an onset if it came after the previous rain at that gauge
    ended, so an alert for an earlier shower is not credited to the next one.
    """
    onsets, last_rain = [], None
    for snapshot in day_series:
        if not _raining(snapshot):
            continue
        t = snapshot.updated_at
        if (last_rain is None or t - last_rain >= DRY_GAP) and start < t < end:
            window = (
                t - WARN_WINDOW if last_rain is None else max(t - WARN_WINDOW, last_rain + STEP)
            )
            onsets.append((t, window))
        last_rain = t
    return onsets


def rain_within(day_series: list[AreaSnapshot], t: datetime, horizon: timedelta) -> bool:
    return any(t <= s.updated_at <= t + horizon and _raining(s) for s in day_series)


# ---------------------------------------------------------------- sessions


def sessions_for(series: list[AreaSnapshot], day: date) -> Iterator[list[AreaSnapshot]]:
    expected = SESSION_HOURS * 60 // 5
    for hour in SGT_START_HOURS:
        start = next((s for s in series if s.updated_at.hour >= hour), None)
        if (
            start is None
            or start.updated_at - start.updated_at.replace(hour=hour, minute=0) > 2 * STEP
        ):
            continue
        end = start.updated_at + timedelta(hours=SESSION_HOURS)
        run = [s for s in series if start.updated_at <= s.updated_at < end]
        if len(run) < MIN_COVERAGE * expected or start.result.veto:
            continue  # gaps in the data, or nobody hangs laundry in the rain
        yield run


def distinct_series(series: dict[str, list[AreaSnapshot]]) -> dict[str, list[AreaSnapshot]]:
    """Areas with the same nearest stations replay identically; keep one of each."""
    seen, kept = set(), {}
    for area, snaps in sorted(series.items()):
        key = tuple((s.updated_at, s.result.score, s.result.veto, _raining(s)) for s in snaps)
        if key not in seen:
            seen.add(key)
            kept[area] = snaps
    return kept


# ---------------------------------------------------------------- evaluation


def _empty_stats() -> tuple[dict, dict]:
    per_policy: dict[str, dict] = {
        name: {
            "emails": [],
            "rain_alerts": 0,
            "decline_alerts": 0,
            "covered": 0,
            "lead_min": [],
            "forecast_alerts": 0,
            "forecast_hits": 0,
            "frames": 0,
            "under_informed": 0,
            "redundant_alerts": 0,
            "rain_frames": 0,
            "rain_frames_warned": 0,
        }
        for name in POLICIES
    }
    totals = {
        "days": [],
        "skipped_days": [],
        "areas": 0,
        "sessions": 0,
        "onsets": 0,
        "daytime_marks": 0,
        "daytime_marks_with_forecast": 0,
    }
    return per_policy, totals


def _merge(into: dict, part: dict) -> None:
    for key, value in part.items():
        if isinstance(value, dict):
            _merge(into[key], value)
        elif isinstance(value, list):
            into[key].extend(value)
        else:
            into[key] += value


def evaluate(days: list[date], cache_dir: Path, source: str = "csv", workers: int = 1) -> dict:
    per_policy, totals = _empty_stats()
    jobs = [(day, source, cache_dir) for day in days]
    if workers > 1:
        with multiprocessing.Pool(workers) as pool:
            parts = pool.imap_unordered(_evaluate_day, jobs)
            for policy_part, totals_part in parts:
                _merge(per_policy, policy_part)
                _merge(totals, totals_part)
    else:
        for job in jobs:
            policy_part, totals_part = _evaluate_day(job)
            _merge(per_policy, policy_part)
            _merge(totals, totals_part)
    totals["days"].sort()
    return _summarise(per_policy, totals)


def _evaluate_day(job: tuple[date, str, Path]) -> tuple[dict, dict]:
    day, source, cache_dir = job
    per_policy, totals = _empty_stats()
    data = load_day(day, source, cache_dir)
    if data is None:
        logger.info("%s skipped (not downloaded)", day)
        totals["skipped_days"].append(day.isoformat())
        return per_policy, totals
    series = distinct_series(area_series(data))
    for snaps in series.values():
        for s in snaps:
            if 7 <= s.updated_at.hour < 20:
                totals["daytime_marks"] += 1
                totals["daytime_marks_with_forecast"] += s.conditions.forecast is not None
    totals["days"].append(day.isoformat())
    totals["areas"] += len(series)
    day_sessions = 0
    for snaps in series.values():
        for run in sessions_for(snaps, day):
            day_sessions += 1
            start, end = run[0].updated_at, run[-1].updated_at + STEP
            onsets = rain_onsets(snaps, start, end)
            totals["onsets"] += len(onsets)
            frame_at = {s.updated_at: i for i, s in enumerate(run)}
            episode: list[int | None] = []
            last_veto, count = None, 0
            for s in run:
                if s.result.veto:
                    if last_veto is None or s.updated_at - last_veto > DRY_GAP:
                        count += 1
                    last_veto = s.updated_at
                    episode.append(count)
                else:
                    episode.append(None)
            for name, policy in POLICIES.items():
                stats = per_policy[name]
                alerts = policy(run)
                stats["emails"].append(len(alerts))
                # Each email is classed: the first one in a rain episode (a run of rain
                # vetoes, split by DRY_GAP without one) is a rain warning; any further one in
                # the same episode is a repeat; one without a veto is a drying decline.
                # The user's belief at each frame is the state in their last email (or the
                # state when they hung the laundry); no policy sends all-clears.
                belief, sent, rain_times, warned_episodes = (
                    [],
                    {i for i, _ in alerts},
                    [],
                    set(),
                )
                current = _severity(run[0])
                for i, s in enumerate(run):
                    if i in sent:
                        if episode[i] is None:
                            stats["decline_alerts"] += 1
                        elif episode[i] in warned_episodes:
                            stats["redundant_alerts"] += 1
                        else:
                            warned_episodes.add(episode[i])
                            stats["rain_alerts"] += 1
                            rain_times.append(s.updated_at)
                            if not _raining(s):  # raised by the 2-hour forecast
                                stats["forecast_alerts"] += 1
                                stats["forecast_hits"] += rain_within(
                                    snaps, s.updated_at, WARN_WINDOW
                                )
                        current = _severity(s)
                    belief.append(current)
                for i, s in enumerate(run[1:], start=1):
                    stats["frames"] += 1
                    stats["under_informed"] += _severity(s) > belief[i]
                    if _raining(s):
                        stats["rain_frames"] += 1
                        stats["rain_frames_warned"] += belief[i] == RAIN
                for onset, window_start in onsets:
                    index = frame_at.get(onset)
                    if index is not None and belief[index] == RAIN:
                        stats["covered"] += 1
                    early = [t for t in rain_times if window_start <= t < onset]
                    if early:
                        stats["lead_min"].append((onset - min(early)).total_seconds() / 60)
    totals["sessions"] += day_sessions
    logger.info("%s: %d distinct areas, %d sessions", day, len(series), day_sessions)
    return per_policy, totals


def _summarise(per_policy: dict, totals: dict) -> dict:
    results = {}
    for name, s in per_policy.items():
        emails = s["emails"] or [0]
        n = len(emails)
        results[name] = {
            "emails_per_session_mean": round(statistics.mean(emails), 2),
            "emails_per_session_p90": sorted(emails)[int(0.9 * (n - 1))],
            "emails_per_session_max": max(emails),
            "sessions_with_more_than_3_emails_pct": round(100 * sum(e > 3 for e in emails) / n, 1),
            "rain_alerts": s["rain_alerts"],
            "decline_alerts": s["decline_alerts"],
            "redundant_alerts": s["redundant_alerts"],
            "redundant_pct": round(100 * s["redundant_alerts"] / max(1, sum(s["emails"]))),
            "rain_onsets_warned_pct": round(100 * s["covered"] / totals["onsets"], 1)
            if totals["onsets"]
            else None,
            "warned_before_rain_pct": round(100 * len(s["lead_min"]) / totals["onsets"], 1)
            if totals["onsets"]
            else None,
            "median_lead_min_when_early": statistics.median(s["lead_min"]) if s["lead_min"] else 0,
            "rain_time_warned_pct": round(100 * s["rain_frames_warned"] / s["rain_frames"], 1)
            if s["rain_frames"]
            else None,
            "under_informed_time_pct": round(100 * s["under_informed"] / s["frames"], 1)
            if s["frames"]
            else None,
            "forecast_alert_precision_2h_pct": round(
                100 * s["forecast_hits"] / s["forecast_alerts"], 1
            )
            if s["forecast_alerts"]
            else None,
        }
    return {
        "totals": totals,
        "policies": results,
        "assumptions": {
            "start_hours_sgt": SGT_START_HOURS,
            "session_hours": SESSION_HOURS,
            "dry_gap_min": DRY_GAP.seconds // 60,
            "warn_window_min": WARN_WINDOW.seconds // 60,
        },
    }


def print_table(report: dict) -> None:
    t = report["totals"]
    print(
        f"\nDays: {len(t['days'])} (skipped {len(t['skipped_days'])}), distinct area-days: "
        f"{t['areas']}, sessions: {t['sessions']}, rain onsets during sessions: {t['onsets']}, "
        f"daytime marks with a forecast: {t['daytime_marks_with_forecast']}/{t['daytime_marks']}\n"
    )
    cols = [
        ("emails/session", "emails_per_session_mean"),
        ("p90", "emails_per_session_p90"),
        ("rain", "rain_alerts"),
        ("decline", "decline_alerts"),
        ("repeat in rain", "redundant_alerts"),
        ("onset warned %", "rain_onsets_warned_pct"),
        ("before rain %", "warned_before_rain_pct"),
        ("lead min", "median_lead_min_when_early"),
        ("under-informed %", "under_informed_time_pct"),
        ("fcst precision %", "forecast_alert_precision_2h_pct"),
    ]
    print("| Policy | " + " | ".join(c for c, _ in cols) + " |")
    print("|---" * (len(cols) + 1) + "|")
    for name, r in report["policies"].items():
        print(f"| {name} | " + " | ".join(str(r[k]) for _, k in cols) + " |")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--days", help="Comma-separated YYYY-MM-DD days instead of the default")
    parser.add_argument(
        "--source",
        choices=("api", "csv"),
        default="csv",
        help="csv: days from convert_nea_csv.py; api: pages cached by download_history.py",
    )
    parser.add_argument("--cache-dir", type=Path, help="Defaults to .cache/csv or .cache/history")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument(
        "--output",
        type=Path,
        default=SERVICES / ".cache" / "evaluation" / "replay_evaluation.json",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cache_dir is None:
        args.cache_dir = CSV_CACHE if args.source == "csv" else API_CACHE
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.days:
        days = parse_days(args.days)
    elif args.source == "csv":
        days = csv_days(args.cache_dir)
    else:
        days = api_days(args.cache_dir)  # whatever download_history.py has fetched so far
    report = evaluate(days, args.cache_dir, args.source, args.workers)
    args.output.write_text(json.dumps(report, indent=2))
    print_table(report)
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
