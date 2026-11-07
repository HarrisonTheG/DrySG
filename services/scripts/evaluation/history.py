"""Historical NEA data for the replay evaluation: download, cache, load and score.

Two sources, both turned into the same per-day `DayData`:
  * api: pages from the data.gov.sg real-time API queried by date (`download_history.py`).
    Slow (~200 pages a day, rate limited) but needs nothing downloaded by hand.
  * csv: the bulk "Historical ... across Singapore (YEAR)" CSVs, converted to one JSON page
    per feed per day by `convert_nea_csv.py`. Fast; used for the full-year evaluation.

`DayData` rebuilds, for any 5-minute mark, the API envelopes the live system would have
received, and `area_series` scores them with the live `normalize_weather` + `compute_score`.
"""

import base64
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as clock
from pathlib import Path
from typing import Any

import httpx

from drysg.core.models import AreaSnapshot
from drysg.core.scoring import InsufficientDataError, compute_score
from drysg.data.normalization import normalize_weather

logger = logging.getLogger("evaluation.history")

SERVICES = Path(__file__).resolve().parents[2]
API_CACHE = SERVICES / ".cache" / "history"
CSV_CACHE = SERVICES / ".cache" / "csv"

API = "https://api-open.data.gov.sg/v2/real-time/api"
METRIC_FEEDS = ("rainfall", "relative-humidity", "air-temperature", "wind-speed")
FEEDS = (*METRIC_FEEDS, "two-hr-forecast", "psi")
STEP = timedelta(minutes=5)
MIN_INTERVAL_S = 1.1
CACHE_VERSION = "v1"

# The 9th and 23rd of every month of 2024: every season, including both monsoons. All the
# 9ths come first, so a partial download still covers the whole year.
DEFAULT_DAYS = [date(2024, m, d) for d in (9, 23) for m in range(1, 13)]

# A whole-day query of the 2-hour forecast archive only returns the last ~25 forecasts of the
# day (from about noon), with no next page. The earlier ones are fetched one by one: NEA
# issues a forecast every half hour, and a query with an exact time returns that forecast.
FORECAST_HOURS = (6, 20)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def parse_days(text: str | None) -> list[date]:
    return [date.fromisoformat(d) for d in text.split(",")] if text else DEFAULT_DAYS


def year_days(year: int) -> list[date]:
    """Every day of `year`, the 9ths and 23rds first so a partial run is spread over the year."""
    first = date(year, 1, 1)
    days = [first + timedelta(n) for n in range((date(year + 1, 1, 1) - first).days)]
    priority = [d for d in days if d.day in (9, 23)]
    return priority + [d for d in days if d.day not in (9, 23)]


def api_days(cache_dir: Path) -> list[date]:
    """Days whose first rainfall page is in the API cache (incomplete days are skipped later)."""
    return sorted(
        date.fromisoformat(p.name.split("_")[2])
        for p in cache_dir.glob(f"{CACHE_VERSION}_rainfall_*_{_page_tag(None)}.json")
    )


def _page_tag(token: str | None) -> str:
    return base64.urlsafe_b64encode((token or "first").encode()).decode().rstrip("=")


# ---------------------------------------------------------------- api source


class MissingFromCache(Exception):
    pass


class DayFetcher:
    """Downloads every page of a feed's day from the real-time API, cached on disk.

    With `http=None` it only reads the cache and raises `MissingFromCache` for the rest.
    """

    def __init__(
        self, http: httpx.Client | None, cache_dir: Path, api_key: str | None = None
    ) -> None:
        self._http = http
        self._cache = cache_dir
        self._headers = {"x-api-key": api_key} if api_key else {}
        self._cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def day(self, feed: str, day: date) -> list[dict]:
        """The `data` object of each page."""
        pages, token = [], None
        while True:
            data = self._cached(
                f"{CACHE_VERSION}_{feed}_{day.isoformat()}_{_page_tag(token)}.json",
                lambda token=token: self._download(feed, day.isoformat(), token),
            )
            pages.append(data)
            token = data.get("paginationToken")
            if not token:
                return pages

    def half_hourly_forecasts(self, day: date) -> list[dict]:
        """Forecast items issued at each half hour of the day (404 = none issued)."""
        items = []
        first, last = FORECAST_HOURS
        for hour in range(first, last):
            for minute in (0, 30):
                moment = datetime.combine(day, clock(hour, minute))
                data = self._cached(
                    f"forecast-at_{moment:%Y-%m-%d_%H%M}.json",
                    lambda m=moment: self._download("two-hr-forecast", m.isoformat(), None),
                )
                items.extend(data.get("items", []))
        return items

    def _cached(self, name: str, fetch) -> dict:
        path = self._cache / name
        if path.exists():
            return json.loads(path.read_text())
        if self._http is None:
            raise MissingFromCache(name)
        data = fetch()
        path.write_text(json.dumps(data))
        return data

    def _download(self, feed: str, when: str, token: str | None) -> dict:
        assert self._http is not None
        params = {"date": when}
        if token:
            params["paginationToken"] = token
        for attempt in range(8):
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._http.get(
                    f"{API}/{feed}", params=params, headers=self._headers, timeout=30
                )
            except httpx.HTTPError as exc:
                logger.warning("%s %s: %s", feed, when, exc)
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            if response.status_code in (400, 404):
                return {}
            response.raise_for_status()
            return response.json().get("data") or {}
        raise RuntimeError(f"Giving up on {feed} {when}")


# ---------------------------------------------------------------- csv source

CSV_FEEDS = (*METRIC_FEEDS, "two-hr-forecast")


def csv_page_path(cache_dir: Path, feed: str, day: str) -> Path:
    return cache_dir / f"csv_{feed}_{day}.json"


def csv_days(cache_dir: Path) -> list[date]:
    return sorted(
        date.fromisoformat(p.stem.rsplit("_", 1)[1]) for p in cache_dir.glob("csv_rainfall_*.json")
    )


# ---------------------------------------------------------------- one day of history


@dataclass
class DayData:
    """One day of NEA history, indexed by timestamp."""

    metric: dict[str, dict[datetime, dict]] = field(default_factory=dict)
    stations: dict[str, list] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    area_metadata: list = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)
    region_metadata: list = field(default_factory=list)
    psi: list[dict] = field(default_factory=list)

    @classmethod
    def from_pages(cls, pages: dict[str, list[dict]]) -> "DayData":
        """`pages[feed]` is the list of API `data` objects (or converted CSV pages)."""
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
        seen = set()
        for data in pages.get("two-hr-forecast", []):
            day.area_metadata = data.get("area_metadata") or day.area_metadata
            for item in data.get("items", []):
                if item["timestamp"] not in seen:
                    seen.add(item["timestamp"])
                    day.forecasts.append(item)
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
                # The archive stamps forecasts with their re-publication date; the moment a
                # forecast was issued is its own `timestamp`.
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


def load_day(day: date, source: str, cache_dir: Path) -> DayData | None:
    """One day from the chosen cache, or None if it is not (fully) there."""
    if source == "csv":
        pages: dict[str, list[dict]] = {"psi": []}  # PSI never changes a score or an alert
        for feed in CSV_FEEDS:
            path = csv_page_path(cache_dir, feed, day.isoformat())
            if not path.exists():
                return None
            pages[feed] = [json.loads(path.read_text())]
        return DayData.from_pages(pages)
    fetcher = DayFetcher(None, cache_dir)
    try:
        pages = {feed: fetcher.day(feed, day) for feed in FEEDS}
        pages["two-hr-forecast"].append({"items": fetcher.half_hourly_forecasts(day)})
    except MissingFromCache:
        return None
    return DayData.from_pages(pages)


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
