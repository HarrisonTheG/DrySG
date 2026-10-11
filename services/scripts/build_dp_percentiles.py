"""Build services/drysg/core/dp_percentiles.py from historical NEA data (data.gov.sg).

Offline tool: run it by hand (about once a year), commit the regenerated module and
redeploy. The Lambdas never run it; they import the resulting constant.

    cd services
    .venv/bin/python scripts/build_dp_percentiles.py --year 2024 \
        --cache-dir /path/to/cache

Method (see docs/contracts/lambda-backend.md):
  1. For sampled 5-minute marks of the year, read temperature, humidity, wind speed and
     rainfall at every station.
  2. Take the median across stations for temperature, humidity and wind.
  3. Drop marks where any station reports rain, or where any metric is missing.
  4. Compute DP with drysg.core.scoring.drying_potential (wind converted to m/s) and
     write the 0th..100th percentiles.

Sampling: the NEA historical API serves 25 readings per request and allows roughly one
request per second, so a full year of 5-minute marks (~105k per feed) is not practical.
Each day samples `--windows-per-day` 25-minute windows, with start hours rotating by one
hour per day so every hour of day is covered evenly over the year. The cache makes the
run resumable.
"""

import argparse
import base64
import csv
import json
import logging
import statistics
import time
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

import httpx

from drysg.core.scoring import KNOTS_TO_MS, drying_potential

SERVICES = Path(__file__).resolve().parents[1]
logger = logging.getLogger("build_dp_percentiles")

API = "https://api-open.data.gov.sg/v2/real-time/api"
FEEDS = ("air-temperature", "relative-humidity", "wind-speed", "rainfall")
PAGE_SIZE = 25
MIN_INTERVAL_S = 1.1
OUTPUT = SERVICES / "drysg" / "core" / "dp_percentiles.py"
MIN_MARKS = 1000  # refuse to publish a table built from too little data


# --- Pure logic (unit tested) -------------------------------------------------


def page_offset(minute_of_day: int, step_minutes: int) -> int:
    """Pagination offset of the page holding `minute_of_day`.

    The API lists a day's readings newest first and 25 per page. The newest reading is
    at 24:00 minus one step.
    """
    newest = 24 * 60 - step_minutes
    index = max(0, (newest - minute_of_day) // step_minutes)
    return PAGE_SIZE * (index // PAGE_SIZE)


def window_minutes(day_index: int, windows_per_day: int) -> list[int]:
    """Minute-of-day targets for a day: evenly spread, rotating one hour per day."""
    spacing = 24 // windows_per_day
    return [((i * spacing + day_index) % 24) * 60 + 30 for i in range(windows_per_day)]


def station_median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def mark_inputs(temp: list, humidity: list, wind_knots: list, rain: list) -> dict | None:
    """Station medians and DP for one 5-minute mark, or None if it must be excluded.

    Excluded when any station reports rain, rainfall has no readings, or any of
    temperature, humidity and wind has no readings.
    """
    rain_clean = [v for v in rain if v is not None]
    if not rain_clean or any(v > 0 for v in rain_clean):
        return None
    t, h, w = station_median(temp), station_median(humidity), station_median(wind_knots)
    if t is None or h is None or w is None:
        return None
    return {
        "temp_c": t,
        "humidity_pct": h,
        "wind_knots": w,
        "wind_ms": w * KNOTS_TO_MS,
        "dp": drying_potential(t, h, w * KNOTS_TO_MS),
    }


def mark_dp(temp: list, humidity: list, wind_knots: list, rain: list) -> float | None:
    """DP for one 5-minute mark, or None if the mark must be excluded."""
    mark = mark_inputs(temp, humidity, wind_knots, rain)
    return None if mark is None else mark["dp"]


def percentile_table(dp_values: list[float]) -> tuple[float, ...]:
    """The 0th..100th percentiles (101 values) by nearest rank on the sorted sample."""
    if not dp_values:
        raise ValueError("No DP values to summarise")
    ordered = sorted(dp_values)
    last = len(ordered) - 1
    return tuple(round(ordered[round(k / 100 * last)], 3) for k in range(101))


def collect_marks(readings: dict[str, dict[str, list]]) -> list[dict]:
    """Usable 5-minute marks present in all feeds, with their station medians and DP.

    `readings[feed][timestamp]` is the list of station values at that timestamp.
    """
    marks = set.intersection(*(set(readings[f]) for f in FEEDS))
    out = []
    for ts in sorted(marks):
        if int(ts[14:16]) % 5:
            continue
        mark = mark_inputs(
            readings["air-temperature"][ts],
            readings["relative-humidity"][ts],
            readings["wind-speed"][ts],
            readings["rainfall"][ts],
        )
        if mark is not None:
            out.append({"timestamp": ts, **mark})
    return out


def collect_dp(readings: dict[str, dict[str, list]]) -> list[float]:
    return [m["dp"] for m in collect_marks(readings)]


def write_marks_csv(marks: list[dict], table: tuple[float, ...], path: Path) -> None:
    """One row per usable mark, with the percentile score that DP maps to."""
    columns = ("timestamp", "temp_c", "humidity_pct", "wind_knots", "wind_ms", "dp", "score")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for m in marks:
            score = max(0, min(100, bisect_right(table, m["dp"]) - 1))
            writer.writerow(
                [m["timestamp"]]
                + [f"{m[c]:.4f}" for c in columns[1:-1]]
                + [score]
            )


def render_module(table: tuple[float, ...], *, year: int, marks: int) -> str:
    if len(table) != 101 or any(a > b for a, b in zip(table, table[1:], strict=False)):
        raise ValueError("Table must have 101 non-decreasing values")
    rows = "\n".join(
        "    " + " ".join(f"{v}," for v in table[i : i + 5]) for i in range(0, 101, 5)
    )
    return f'''"""Historical Singapore Drying Potential (DP) percentiles for the drying score.

GENERATED by services/scripts/build_dp_percentiles.py. Do not edit by hand; rerun the script.

`DP_PERCENTILES[k]` is the k-th percentile (k = 0..100) of DP over {marks} rain-free
5-minute marks sampled across {year}. Each mark uses the median temperature, humidity
and wind speed across all NEA stations. Computed once offline and stored here so the
API and ingest Lambdas need no runtime lookup.
"""

DP_PERCENTILES_YEAR = {year}
DP_PERCENTILES_SOURCE = "data.gov.sg"
DP_PERCENTILES_MARKS = {marks}

DP_PERCENTILES: tuple[float, ...] = (
{rows}
)
'''


# --- Fetching -----------------------------------------------------------------


class Fetcher:
    def __init__(self, http: httpx.Client, cache_dir: Path) -> None:
        self._http = http
        self._cache = cache_dir
        self._cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def page(self, feed: str, day: date, offset: int) -> list[tuple[str, list]]:
        path = self._cache / f"{feed}_{day.isoformat()}_{offset}.json"
        if path.exists():
            return [tuple(r) for r in json.loads(path.read_text())]
        rows = self._download(feed, day, offset)
        path.write_text(json.dumps(rows))
        return rows

    def _download(self, feed: str, day: date, offset: int) -> list[tuple[str, list]]:
        params = {"date": day.isoformat()}
        if offset:
            params["paginationToken"] = base64.b64encode(f"offset={offset}".encode()).decode()
        for attempt in range(8):
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._http.get(f"{API}/{feed}", params=params, timeout=30)
            except httpx.HTTPError as exc:
                logger.warning("%s %s +%s: %s", feed, day, offset, exc)
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            if response.status_code in (400, 404):
                return []  # past the last page of the day
            if response.status_code != 200:
                raise RuntimeError(f"{feed} {day} offset {offset}: HTTP {response.status_code}")
            data = response.json().get("data") or {}
            return [
                (r["timestamp"], [d.get("value") for d in r["data"]])
                for r in data.get("readings", [])
            ]
        raise RuntimeError(f"Giving up on {feed} {day} offset {offset}")

    def step_minutes(self, feed: str, day: date) -> int:
        rows = self.page(feed, day, 0)
        if len(rows) < 2:
            raise RuntimeError(f"Cannot infer cadence of {feed}")

        def minutes(ts: str) -> int:
            return int(ts[11:13]) * 60 + int(ts[14:16])

        return minutes(rows[0][0]) - minutes(rows[1][0])


def collect_year(fetcher: Fetcher, year: int, windows_per_day: int) -> dict[str, dict]:
    start = date(year, 1, 1)
    days = (date(year + 1, 1, 1) - start).days
    probe = start + timedelta(days=30)
    steps = {feed: fetcher.step_minutes(feed, probe) for feed in FEEDS}
    logger.info("Cadence (minutes): %s", steps)
    readings: dict[str, dict[str, list]] = {feed: {} for feed in FEEDS}
    for n in range(days):
        day = start + timedelta(days=n)
        for minute in window_minutes(n, windows_per_day):
            for feed in FEEDS:
                offset = page_offset(minute, steps[feed])
                for ts, values in fetcher.page(feed, day, offset):
                    readings[feed][ts] = values
        if n % 10 == 0:
            logger.info("Day %d/%d", n + 1, days)
    return readings


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--windows-per-day", type=int, default=3, choices=[1, 2, 3, 4, 6, 8, 12])
    parser.add_argument("--cache-dir", type=Path, default=SERVICES / ".cache" / "dp_percentiles")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--marks-csv", type=Path, help="Also write every usable 5-minute mark to this CSV"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with httpx.Client() as http:
        readings = collect_year(Fetcher(http, args.cache_dir), args.year, args.windows_per_day)
    marks = collect_marks(readings)
    dp_values = [m["dp"] for m in marks]
    logger.info("Usable rain-free marks: %d", len(dp_values))
    if len(dp_values) < MIN_MARKS:
        raise SystemExit(f"Only {len(dp_values)} usable marks (< {MIN_MARKS}); not writing")
    table = percentile_table(dp_values)
    if args.marks_csv:
        write_marks_csv(marks, table, args.marks_csv)
        logger.info("Wrote %s", args.marks_csv)
    args.output.write_text(
        render_module(table, year=args.year, marks=len(dp_values)), encoding="utf-8"
    )
    logger.info("Wrote %s (p0=%s p50=%s p100=%s)", args.output, table[0], table[50], table[100])


if __name__ == "__main__":
    main()
