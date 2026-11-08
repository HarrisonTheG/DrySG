"""Build the Experience scenarios from real historical NEA data (data.gov.sg).

Offline tool: run it by hand, commit the two regenerated JSON files and redeploy. Neither
the Lambdas nor the website download anything at run time.

    cd services
    .venv/bin/python -I scripts/build_experience_scenarios.py --cache-dir ~/experience-cache

What it does:
  1. Downloads whole days of history for the six feeds the live system reads (rainfall,
     humidity, temperature, wind, 2-hour forecast, PSI). Default days: the 9th of every
     month of 2024, plus four days of the 2026 haze.
  2. Replays every 5-minute mark of each day through the live `normalize_weather` and
     `compute_score`, for every forecast area (drysg.experience.replay).
  3. Cuts each area's day into one-hour windows between 07:00 and 18:00, classifies them
     (hang now, wait, rain arrives, ...), and keeps a varied few per category.
  4. Runs the live alert rules over each window to find where an alert would have fired.
  5. Looks up real postal codes for each area from OneMap (needs ONEMAP_TOKEN), keeping only
     codes that the app itself would resolve to that area.

Writes services/drysg/experience/scenarios.json (the API sends alert emails from it) and
web/src/data/experienceScenarios.json (the website plays it back). The files are identical.

The download is about 200 pages a day (temperature is reported every minute) at one page per
second, so the default 16 days take roughly an hour. The cache makes an interrupted run
resumable.
"""

import argparse
import base64
import json
import logging
import os
import re
import time
import zlib
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from drysg.api.recommendation import to_recommendation
from drysg.core.locations import AreaLocation, Coordinates
from drysg.core.scoring import SCORE_METHOD
from drysg.data.normalization import nearest_area
from drysg.experience.replay import (
    CATEGORIES,
    METRIC_FEEDS,
    DayData,
    area_series,
    classify,
    simulate_alerts,
    windows,
)

SERVICES = Path(__file__).resolve().parents[1]
REPO = SERVICES.parent
logger = logging.getLogger("build_experience_scenarios")

API = "https://api-open.data.gov.sg/v2/real-time/api"
FEEDS = (*METRIC_FEEDS, "two-hr-forecast", "psi")
MIN_INTERVAL_S = 1.1
CACHE_VERSION = "v1"
SERVICE_OUTPUT = SERVICES / "drysg" / "experience" / "scenarios.json"
WEB_OUTPUT = REPO / "web" / "src" / "data" / "experienceScenarios.json"
ONEMAP_SEARCH = "https://www.onemap.gov.sg/api/common/elastic/search"
# Laundry is hung in daylight: a window must start at or after and end by these hours.
FIRST_HOUR, LAST_HOUR = 7, 18


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


class DayFetcher:
    """Downloads every page of a feed's day, cached on disk."""

    def __init__(self, http: httpx.Client, cache_dir: Path, api_key: str | None) -> None:
        self._http = http
        self._cache = cache_dir
        self._headers = {"x-api-key": api_key} if api_key else {}
        self._cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def day(self, feed: str, day: date) -> list[dict]:
        """The `data` object of each page, newest readings first."""
        pages, token = [], None
        while True:
            data = self._page(feed, day, token)
            pages.append(data)
            token = data.get("paginationToken")
            if not token:
                return pages

    def _page(self, feed: str, day: date, token: str | None) -> dict:
        tag = base64.urlsafe_b64encode((token or "first").encode()).decode().rstrip("=")
        path = self._cache / f"{CACHE_VERSION}_{feed}_{day.isoformat()}_{tag}.json"
        if path.exists():
            return json.loads(path.read_text())
        data = self._download(feed, day, token)
        path.write_text(json.dumps(data))
        return data

    def _download(self, feed: str, day: date, token: str | None) -> dict:
        params = {"date": day.isoformat()}
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
                logger.warning("%s %s: %s", feed, day, exc)
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            if response.status_code in (400, 404):
                return {}
            response.raise_for_status()
            return response.json().get("data") or {}
        raise RuntimeError(f"Giving up on {feed} {day}")


# The 9th of every month of 2024 for ordinary weather, plus days of the 2026 haze (24-hour PSI
# of 116 to 212 across Singapore), which 2024 did not have.
DEFAULT_DAYS = [date(2024, month, 9) for month in range(1, 13)] + [
    date(2026, 9, 29),
    date(2026, 10, 7),
    date(2026, 10, 15),
    date(2026, 10, 23),
]


def candidates_for_day(day: date, data: DayData) -> list[dict]:
    """Classified one-hour windows for every area on this day."""
    found = []
    for area, snapshots in area_series(data).items():
        for run in windows(snapshots):
            start, end = run[0].updated_at, run[-1].updated_at
            if start.hour < FIRST_HOUR or (end.hour, end.minute) > (LAST_HOUR, 0):
                continue
            alerts = simulate_alerts(run)
            category = classify(run, alerts)
            if category is None:
                continue
            if category == "rain_arrives":
                # Wants dry frames to watch first, and the alert at the first rain.
                first_rain = next(i for i, s in enumerate(run) if s.result.veto)
                if not 3 <= first_rain <= 8 or alerts[0][0] != first_rain:
                    continue
            found.append({"category": category, "area": area, "run": run, "alerts": alerts})
    logger.info("%s: %d candidate windows", day, len(found))
    return found


def pick(candidates: list[dict], count: int) -> list[dict]:
    """A varied few: prefer an unused month and area, then a stable pseudo-random order."""
    chosen: list[dict] = []
    months: Counter = Counter()
    areas: Counter = Counter()
    days: set = set()
    seen: set = set()
    pool = list(candidates)
    while pool and len(chosen) < count:

        def rank(c: dict) -> tuple:
            start = c["run"][0].updated_at
            key = f"{c['area']}{start.isoformat()}".encode()
            return (start.date() in days, months[start.month], areas[c["area"]], zlib.crc32(key))

        best = min(pool, key=rank)
        start = best["run"][0].updated_at
        # Areas that share a weather station replay identical hours; keep one of them.
        signature = (start, tuple(s.result.score for s in best["run"]))
        if signature in seen:
            pool.remove(best)
            continue
        seen.add(signature)
        chosen.append(best)
        months[start.month] += 1
        areas[best["area"]] += 1
        days.add(start.date())
        pool = [
            c
            for c in pool
            if not (
                c["area"] == best["area"]
                and c["run"][0].updated_at.date() == start.date()
            )
        ]
    return chosen


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def postal_codes_for(
    http: httpx.Client, token: str, area: str, catalogue: list[AreaLocation], limit: int = 8
) -> list[str]:
    """Real postal codes that the app maps to `area` (nearest forecast area to the address)."""
    found: list[str] = []
    for query in (area, f"{area} mrt", f"{area} avenue", f"{area} street", f"{area} drive"):
        response = http.get(
            ONEMAP_SEARCH,
            params={"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        time.sleep(0.3)
        if response.status_code != 200:
            continue
        for item in response.json().get("results", []):
            code = item.get("POSTAL", "")
            if not re.fullmatch(r"[0-9]{6}", code) or code in found:
                continue
            here = Coordinates(latitude=float(item["LATITUDE"]), longitude=float(item["LONGITUDE"]))
            if nearest_area(here, catalogue).area == area:
                found.append(code)
        if len(found) >= limit:
            break
    return found[:limit]


def scenario_json(chosen: dict) -> dict:
    run = chosen["run"]
    return {
        "id": f"{chosen['category']}-{run[0].updated_at:%Y%m%d-%H%M}-{slug(chosen['area'])}",
        "category": chosen["category"],
        "area": chosen["area"],
        "startedAt": run[0].updated_at.isoformat(),
        "frames": [to_recommendation(s, "").model_dump(mode="json") for s in run],
        "alerts": [
            {
                "frame": index,
                "reason": m.reason,
                "prevScore": m.prev_score,
                "prevBand": m.prev_band.value,
                "newScore": m.new_score,
                "newBand": m.new_band.value,
                "newVeto": m.new_veto,
                "snapshotTs": m.snapshot_ts.isoformat(),
            }
            for index, m in chosen["alerts"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--days", help="Comma-separated YYYY-MM-DD days instead of the default")
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--cache-dir", type=Path, default=SERVICES / ".cache" / "experience")
    parser.add_argument("--service-output", type=Path, default=SERVICE_OUTPUT)
    parser.add_argument("--web-output", type=Path, default=WEB_OUTPUT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_env(SERVICES / ".env")
    token = os.environ.get("ONEMAP_TOKEN", "").strip()
    if not token:
        raise SystemExit("ONEMAP_TOKEN is required to look up real postal codes")

    days = [date.fromisoformat(d) for d in args.days.split(",")] if args.days else DEFAULT_DAYS
    by_category: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    catalogue: list[AreaLocation] = []
    with httpx.Client() as http:
        fetcher = DayFetcher(http, args.cache_dir, os.environ.get("NEA_API_KEY") or None)
        for n, day in enumerate(days, start=1):
            logger.info("Day %d/%d: %s", n, len(days), day)
            data = DayData.from_pages({feed: fetcher.day(feed, day) for feed in FEEDS})
            if not catalogue:
                catalogue = [
                    AreaLocation(
                        area=a["name"],
                        latitude=a["label_location"]["latitude"],
                        longitude=a["label_location"]["longitude"],
                    )
                    for a in data.area_metadata
                ]
            for candidate in candidates_for_day(day, data):
                by_category[candidate["category"]].append(candidate)

        scenarios = []
        postal: dict[str, list[str]] = {}
        for category in CATEGORIES:
            logger.info("%s: %d candidates", category, len(by_category[category]))
            for chosen in pick(by_category[category], args.per_category * 3):
                area = chosen["area"]
                if area not in postal:
                    postal[area] = postal_codes_for(http, token, area, catalogue)
                    logger.info("%s: %d real postal codes", area, len(postal[area]))
                if len([s for s in scenarios if s["category"] == category]) >= args.per_category:
                    break
                if postal[area]:
                    scenarios.append({**scenario_json(chosen), "postalCodes": postal[area]})

    if not scenarios:
        raise SystemExit("No scenarios built; not writing")
    counts = Counter(s["category"] for s in scenarios)
    logger.info("Scenarios per category: %s", dict(counts))
    document = {"version": 1, "scoreMethod": SCORE_METHOD, "scenarios": scenarios}
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
    for path in (args.service_output, args.web_output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        logger.info("Wrote %s (%.0f KB)", path, len(text) / 1000)


if __name__ == "__main__":
    main()
