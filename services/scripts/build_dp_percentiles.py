"""Build services/drysg/core/dp_percentiles.py from historical NEA data (data.gov.sg).

Offline tool: run it by hand (about once a year), commit the regenerated module and
redeploy. The Lambdas never run it; they import the resulting constant.

    cd services
    .venv/bin/python scripts/build_dp_percentiles.py --year 2024 \
        --cache-dir /path/to/cache

Method (see docs/contracts/lambda-backend.md):
  1. For sampled 5-minute marks of the year, read temperature, humidity, wind speed and
     rainfall at every station.
  2. Every station that reports temperature, humidity and wind at a mark gives one
     sample. Live scoring uses the nearest single station, so the history is built from
     single stations too (not an island-wide median, which would have a narrower spread).
  3. Drop a sample when that station had rain: its own rainfall reading, or the nearest
     rain gauge's if it has none. Rain elsewhere on the island does not drop it, which
     matches the live rain veto.
  4. Compute DP with drysg.core.scoring.drying_potential (wind converted to m/s) and
     write the 0th..100th percentiles, plus the median of each input (the fallback used
     when a live feed is missing).

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

from drysg.core.locations import Coordinates
from drysg.core.scoring import KNOTS_TO_MS, drying_potential
from drysg.data.normalization import distance_km

SERVICES = Path(__file__).resolve().parents[1]
logger = logging.getLogger("build_dp_percentiles")

API = "https://api-open.data.gov.sg/v2/real-time/api"
FEEDS = ("air-temperature", "relative-humidity", "wind-speed", "rainfall")
PAGE_SIZE = 25
MIN_INTERVAL_S = 1.1
OUTPUT = SERVICES / "drysg" / "core" / "dp_percentiles.py"
MIN_SAMPLES = 1000  # refuse to publish a table built from too little data
CACHE_VERSION = "v2"  # v2 pages keep station ids and locations

# readings[feed][timestamp] = {station_id: value}
Readings = dict[str, dict[str, dict[str, float | None]]]
# locations[feed][station_id] = (latitude, longitude)
Locations = dict[str, dict[str, tuple[float, float]]]


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


def station_rain(
    station: str,
    rain: dict[str, float | None],
    station_location: tuple[float, float] | None,
    rain_locations: dict[str, tuple[float, float]],
) -> float | None:
    """Rainfall for a weather station: its own gauge, else the nearest gauge with data."""
    own = rain.get(station)
    if own is not None:
        return own
    if station_location is None:
        return None
    here = Coordinates(latitude=station_location[0], longitude=station_location[1])
    gauges = [
        (distance_km(here, Coordinates(latitude=lat, longitude=lon)), gauge)
        for gauge, (lat, lon) in rain_locations.items()
        if rain.get(gauge) is not None
    ]
    return rain[min(gauges)[1]] if gauges else None


def station_samples(
    temp: dict[str, float | None],
    humidity: dict[str, float | None],
    wind_knots: dict[str, float | None],
    rain: dict[str, float | None],
    *,
    station_locations: dict[str, tuple[float, float]],
    rain_locations: dict[str, tuple[float, float]],
) -> list[dict]:
    """One DP sample per rain-free station that reports all three inputs at this mark."""
    samples = []
    for station in sorted(set(temp) & set(humidity) & set(wind_knots)):
        t, h, w = temp[station], humidity[station], wind_knots[station]
        if t is None or h is None or w is None:
            continue
        rainfall = station_rain(station, rain, station_locations.get(station), rain_locations)
        if rainfall is None or rainfall > 0:
            continue
        samples.append(
            {
                "station": station,
                "temp_c": t,
                "humidity_pct": h,
                "wind_knots": w,
                "wind_ms": w * KNOTS_TO_MS,
                "dp": drying_potential(t, h, w * KNOTS_TO_MS),
            }
        )
    return samples


def percentile_table(dp_values: list[float]) -> tuple[float, ...]:
    """The 0th..100th percentiles (101 values) by nearest rank on the sorted sample."""
    if not dp_values:
        raise ValueError("No DP values to summarise")
    ordered = sorted(dp_values)
    last = len(ordered) - 1
    return tuple(round(ordered[round(k / 100 * last)], 3) for k in range(101))


def input_medians(samples: list[dict]) -> dict[str, float]:
    """Median of each scoring input, used when a live feed is missing."""
    if not samples:
        raise ValueError("No samples to summarise")
    return {
        key: round(statistics.median(s[key] for s in samples), 1)
        for key in ("temp_c", "humidity_pct", "wind_knots")
    }


def collect_samples(readings: Readings, locations: Locations) -> list[dict]:
    """Usable station samples at 5-minute marks present in all feeds.

    `readings[feed][timestamp]` maps station id to value; `locations[feed]` maps station
    id to (latitude, longitude).
    """
    station_locations = {
        station: loc
        for feed in ("air-temperature", "relative-humidity", "wind-speed")
        for station, loc in locations.get(feed, {}).items()
    }
    marks = set.intersection(*(set(readings[f]) for f in FEEDS))
    out = []
    for ts in sorted(marks):
        if int(ts[14:16]) % 5:
            continue
        for sample in station_samples(
            readings["air-temperature"][ts],
            readings["relative-humidity"][ts],
            readings["wind-speed"][ts],
            readings["rainfall"][ts],
            station_locations=station_locations,
            rain_locations=locations.get("rainfall", {}),
        ):
            out.append({"timestamp": ts, **sample})
    return out


def write_samples_csv(samples: list[dict], table: tuple[float, ...], path: Path) -> None:
    """One row per usable station sample, with the percentile score that DP maps to."""
    numeric = ("temp_c", "humidity_pct", "wind_knots", "wind_ms", "dp")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(("timestamp", "station", *numeric, "score"))
        for s in samples:
            score = max(0, min(100, bisect_right(table, s["dp"]) - 1))
            writer.writerow(
                [s["timestamp"], s["station"]] + [f"{s[c]:.4f}" for c in numeric] + [score]
            )


def render_module(
    table: tuple[float, ...], *, year: int, samples: int, medians: dict[str, float]
) -> str:
    if len(table) != 101 or any(a > b for a, b in zip(table, table[1:], strict=False)):
        raise ValueError("Table must have 101 non-decreasing values")
    if set(medians) != {"temp_c", "humidity_pct", "wind_knots"}:
        raise ValueError("Medians must cover temp_c, humidity_pct and wind_knots")
    rows = "\n".join(
        "    " + " ".join(f"{v}," for v in table[i : i + 5]) for i in range(0, 101, 5)
    )
    median_rows = "\n".join(f'    "{k}": {medians[k]},' for k in sorted(medians))
    return f'''"""Historical Singapore Drying Potential (DP) percentiles for the drying score.

GENERATED by services/scripts/build_dp_percentiles.py. Do not edit by hand; rerun the script.

`DP_PERCENTILES[k]` is the k-th percentile (k = 0..100) of DP over {samples} rain-free
single-station samples (5-minute marks sampled across {year}). Live scoring also uses the
nearest single station, so both sides have the same spread. Computed once offline and
stored here so the API and ingest Lambdas need no runtime lookup.

`DP_INPUT_MEDIANS` is the median of each input over the same samples. Scoring uses it in
place of a live input whose feed is missing, and flags the result as degraded.
"""

DP_PERCENTILES_YEAR = {year}
DP_PERCENTILES_SOURCE = "data.gov.sg"
DP_PERCENTILES_SAMPLES = {samples}

DP_PERCENTILES: tuple[float, ...] = (
{rows}
)

DP_INPUT_MEDIANS: dict[str, float] = {{
{median_rows}
}}
'''


# --- Fetching -----------------------------------------------------------------


class Fetcher:
    def __init__(self, http: httpx.Client, cache_dir: Path) -> None:
        self._http = http
        self._cache = cache_dir
        self._cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def page(self, feed: str, day: date, offset: int) -> dict:
        """{"stations": {id: [lat, lon]}, "rows": [[timestamp, {id: value}], ...]}."""
        path = self._cache / f"{CACHE_VERSION}_{feed}_{day.isoformat()}_{offset}.json"
        if path.exists():
            return json.loads(path.read_text())
        page = self._download(feed, day, offset)
        path.write_text(json.dumps(page))
        return page

    def _download(self, feed: str, day: date, offset: int) -> dict:
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
                return {"stations": {}, "rows": []}  # past the last page of the day
            if response.status_code != 200:
                raise RuntimeError(f"{feed} {day} offset {offset}: HTTP {response.status_code}")
            data = response.json().get("data") or {}
            stations = {
                s["id"]: [s["location"]["latitude"], s["location"]["longitude"]]
                for s in data.get("stations", [])
                if s.get("id") and s.get("location")
            }
            rows = [
                [r["timestamp"], {d["stationId"]: d.get("value") for d in r["data"]}]
                for r in data.get("readings", [])
            ]
            return {"stations": stations, "rows": rows}
        raise RuntimeError(f"Giving up on {feed} {day} offset {offset}")

    def step_minutes(self, feed: str, day: date) -> int:
        rows = self.page(feed, day, 0)["rows"]
        if len(rows) < 2:
            raise RuntimeError(f"Cannot infer cadence of {feed}")

        def minutes(ts: str) -> int:
            return int(ts[11:13]) * 60 + int(ts[14:16])

        return minutes(rows[0][0]) - minutes(rows[1][0])


def collect_year(
    fetcher: Fetcher, year: int, windows_per_day: int
) -> tuple[Readings, Locations]:
    start = date(year, 1, 1)
    days = (date(year + 1, 1, 1) - start).days
    probe = start + timedelta(days=30)
    steps = {feed: fetcher.step_minutes(feed, probe) for feed in FEEDS}
    logger.info("Cadence (minutes): %s", steps)
    readings: Readings = {feed: {} for feed in FEEDS}
    locations: Locations = {feed: {} for feed in FEEDS}
    for n in range(days):
        day = start + timedelta(days=n)
        for minute in window_minutes(n, windows_per_day):
            for feed in FEEDS:
                page = fetcher.page(feed, day, page_offset(minute, steps[feed]))
                for station, (lat, lon) in page["stations"].items():
                    locations[feed][station] = (lat, lon)
                for ts, values in page["rows"]:
                    readings[feed][ts] = values
        if n % 10 == 0:
            logger.info("Day %d/%d", n + 1, days)
    return readings, locations


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--windows-per-day", type=int, default=3, choices=[1, 2, 3, 4, 6, 8, 12])
    parser.add_argument("--cache-dir", type=Path, default=SERVICES / ".cache" / "dp_percentiles")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--samples-csv", type=Path, help="Also write every usable station sample to this CSV"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with httpx.Client() as http:
        readings, locations = collect_year(
            Fetcher(http, args.cache_dir), args.year, args.windows_per_day
        )
    samples = collect_samples(readings, locations)
    logger.info("Usable rain-free station samples: %d", len(samples))
    if len(samples) < MIN_SAMPLES:
        raise SystemExit(f"Only {len(samples)} usable samples (< {MIN_SAMPLES}); not writing")
    table = percentile_table([s["dp"] for s in samples])
    medians = input_medians(samples)
    if args.samples_csv:
        write_samples_csv(samples, table, args.samples_csv)
        logger.info("Wrote %s", args.samples_csv)
    args.output.write_text(
        render_module(table, year=args.year, samples=len(samples), medians=medians),
        encoding="utf-8",
    )
    logger.info(
        "Wrote %s (p0=%s p50=%s p100=%s, medians %s)",
        args.output, table[0], table[50], table[100], medians,
    )


if __name__ == "__main__":
    main()
