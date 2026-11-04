"""Replay real historical NEA data through the production scoring and alert rules.

Pure functions, no network. `scripts/build_experience_scenarios.py` downloads a day of
history and calls these to build the Experience scenarios. They use the same
`normalize_weather`, `compute_score` and `decide_alert` as the live system, so a replayed
moment shows exactly what DrySG would have said and when it would have alerted.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from drysg.core.alerts import decide_alert, should_raise_baseline
from drysg.core.models import (
    AlertMessage,
    AreaSnapshot,
    Band,
    MonitoringSession,
    PsiLevel,
    SessionStatus,
)
from drysg.core.scoring import InsufficientDataError, compute_score
from drysg.data.normalization import normalize_weather

STEP = timedelta(minutes=5)
FRAME_COUNT = 13  # one hour of 5-minute readings
SESSION_LIFETIME = timedelta(hours=12)

METRIC_FEEDS = ("rainfall", "relative-humidity", "air-temperature", "wind-speed")

# Scenario categories, in the order the website shows them.
CATEGORIES = (
    "hang_now",
    "wait",
    "poor_drying",
    "worsening",
    "rain_arrives",
    "raining",
    "rain_clears",
    "haze",
)


@dataclass
class DayData:
    """One day of NEA history, indexed by timestamp. Built by `DayData.from_pages`."""

    metric: dict[str, dict[datetime, dict]] = field(default_factory=dict)
    stations: dict[str, list] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    area_metadata: list = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)
    region_metadata: list = field(default_factory=list)
    psi: list[dict] = field(default_factory=list)

    @classmethod
    def from_pages(cls, pages: dict[str, list[dict]]) -> "DayData":
        """`pages[feed]` is the list of `data` objects from each page of that feed's day."""
        day = cls()
        for feed in METRIC_FEEDS:
            index: dict[datetime, dict] = {}
            stations: dict[str, Any] = {}
            for data in pages.get(feed, []):
                day.units[feed] = data.get("readingUnit", day.units.get(feed, ""))
                for station in data.get("stations", []):
                    stations[station["id"]] = station
                for batch in data.get("readings", []):
                    index[datetime.fromisoformat(batch["timestamp"])] = batch
            day.metric[feed] = index
            day.stations[feed] = list(stations.values())
        for data in pages.get("two-hr-forecast", []):
            day.area_metadata = data.get("area_metadata") or day.area_metadata
            day.forecasts.extend(data.get("items", []))
        for data in pages.get("psi", []):
            day.region_metadata = data.get("regionMetadata") or day.region_metadata
            day.psi.extend(data.get("items", []))
        return day

    def marks(self) -> list[datetime]:
        """5-minute marks that every metric feed reports, oldest first."""
        sets = [set(self.metric.get(feed, {})) for feed in METRIC_FEEDS]
        return sorted(set.intersection(*sets)) if all(sets) else []

    def payloads_at(self, now: datetime) -> dict[str, dict]:
        """NEA envelopes as the live API would have returned them at `now`."""
        payloads: dict[str, dict] = {}
        for feed in METRIC_FEEDS:
            batch = self.metric[feed].get(now)
            if batch is not None:
                payloads[feed] = {
                    "code": 0,
                    "data": {
                        "stations": self.stations[feed],
                        "readings": [batch],
                        "readingUnit": self.units[feed],
                    },
                }
        items = []
        for item in self.forecasts:
            period = item["valid_period"]
            start = datetime.fromisoformat(period["start"])
            if start <= now < datetime.fromisoformat(period["end"]):
                # The archive stamps every forecast with its re-publication date; the
                # moment the forecast was issued is its own `timestamp`.
                items.append({**item, "update_timestamp": item["timestamp"]})
        payloads["two-hr-forecast"] = {
            "code": 0,
            "data": {"area_metadata": self.area_metadata, "items": items},
        }
        recent = [
            item
            for item in self.psi
            if timedelta(0) <= now - datetime.fromisoformat(item["timestamp"]) <= timedelta(hours=2)
        ]
        payloads["psi"] = {
            "code": 0,
            "data": {"regionMetadata": self.region_metadata, "items": recent},
        }
        return payloads


def area_series(day: DayData) -> dict[str, list[AreaSnapshot]]:
    """Per-area snapshots at every mark, scored with the live rules.

    `prev_score`/`prev_band` link each snapshot to the one 5 minutes before, as ingest does.
    Areas that cannot be scored at a mark (a feed missing) have no snapshot for it.
    """
    series: dict[str, list[AreaSnapshot]] = defaultdict(list)
    for now in day.marks():
        try:
            areas = normalize_weather(day.payloads_at(now), now=now)
        except ValueError:
            continue
        for area in areas:
            try:
                result = compute_score(area.conditions)
            except InsufficientDataError:
                continue
            previous = series[area.area][-1] if series[area.area] else None
            consecutive = previous is not None and now - previous.updated_at == STEP
            series[area.area].append(
                AreaSnapshot(
                    **area.model_dump(),
                    result=result,
                    prev_score=previous.result.score if consecutive else None,
                    prev_band=previous.result.band if consecutive else None,
                    updated_at=now,
                )
            )
    return series


def windows(snapshots: list[AreaSnapshot], *, frames: int = FRAME_COUNT):
    """Every run of `frames` consecutive 5-minute snapshots."""
    for start in range(len(snapshots) - frames + 1):
        run = snapshots[start : start + frames]
        if run[-1].updated_at - run[0].updated_at == STEP * (frames - 1):
            yield run


def simulate_alerts(run: list[AreaSnapshot]) -> list[tuple[int, AlertMessage]]:
    """The alerts a session started at the first frame would get, as (frame index, message).

    Follows the live path: the trigger asks `decide_alert`, raises the baseline when
    conditions stay better, and the notifier moves the baseline to the alerted state.
    """
    first = run[0]
    session = MonitoringSession(
        session_id="experience",
        email="experience@example.invalid",
        postal_code="000000",
        area=first.area,
        status=SessionStatus.ACTIVE,
        started_at=first.updated_at,
        expires_at=first.updated_at + SESSION_LIFETIME,
        baseline_score=first.result.score,
        baseline_band=first.result.band,
        baseline_veto=first.result.veto,
        baseline_method=first.result.method,
    )
    alerts = []
    for index, snapshot in enumerate(run[1:], start=1):
        now = snapshot.updated_at
        message = decide_alert(session, snapshot, now)
        if message is not None:
            alerts.append((index, message))
            session = session.model_copy(
                update={
                    "baseline_score": message.new_score,
                    "baseline_band": message.new_band,
                    "baseline_veto": message.new_veto,
                    "baseline_method": message.new_method,
                    "last_alert_at": now,
                    "last_alert_band": message.new_band,
                }
            )
        elif should_raise_baseline(session, snapshot, now):
            session = session.model_copy(
                update={
                    "baseline_score": snapshot.result.score,
                    "baseline_band": snapshot.result.band,
                    "baseline_veto": snapshot.result.veto,
                    "baseline_method": snapshot.result.method,
                }
            )
    return alerts


_HAZE_LEVELS = {PsiLevel.UNHEALTHY, PsiLevel.VERY_UNHEALTHY, PsiLevel.HAZARDOUS}


def classify(run: list[AreaSnapshot], alerts: list[tuple[int, AlertMessage]]) -> str | None:
    """The category a replayed hour belongs to, or None when it is not a clear example.

    Rain wins over drying score, because rain vetoes the recommendation whatever the
    score. A category needs the whole hour to fit it, so playback shows one clear story.
    """
    results = [snapshot.result for snapshot in run]
    veto = [r.veto for r in results]
    bands = [r.band for r in results]
    if any(r.psi_caution in _HAZE_LEVELS for r in results):
        return "haze"
    if all(veto):
        return None if alerts else "raining"
    if veto[0] and not any(veto[-4:]):
        return None if alerts else "rain_clears"
    if (
        not any(veto[:3])
        and bands[0] is not Band.DO_NOT_HANG
        and any(message.new_veto for _, message in alerts)
    ):
        return "rain_arrives"
    if any(veto):
        return None
    if all(band is Band.HANG_NOW for band in bands):
        return "hang_now"
    if all(band is Band.WAIT for band in bands):
        return "wait"
    if all(band is Band.DO_NOT_HANG for band in bands):
        return "poor_drying"
    if bands[0] is not Band.DO_NOT_HANG and alerts and not any(m.new_veto for _, m in alerts):
        return "worsening"
    return None
