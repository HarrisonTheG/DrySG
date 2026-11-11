"""Convert data.gov.sg bulk CSV downloads into per-day pages for the replay evaluation.

The bulk "Historical ... across Singapore (YEAR)" CSVs hold a whole year in one file,
which is much faster than paging through the real-time API by date. This rewrites them
as one JSON file per feed per day, shaped like the API's `data` object, so
`DayData.from_pages` reads them unchanged.

    cd services
    .venv/bin/python -I scripts/evaluation/convert_nea_csv.py --csv-dir path/to/nea_csv

Expected files in --csv-dir (names as downloaded; matched by keyword, case-insensitive):
rainfall, relative humidity, air temperature, wind speed, 2-hour weather forecast.
PSI is not needed: it never changes a score or an alert. Output goes to services/.cache/csv
(ignored by Git) unless --out-dir is given.
"""

import argparse
import csv
import json
import logging
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import CSV_CACHE, csv_page_path  # noqa: E402

logger = logging.getLogger("convert_nea_csv")

FEED_KEYWORDS = {
    "rainfall": "rainfall",
    "relative-humidity": "humidity",
    "air-temperature": "temperature",
    "wind-speed": "wind",
    "two-hr-forecast": "forecast",
}


def find_files(csv_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(csv_dir.glob("*.csv")):
        name = path.name.lower()
        for feed, keyword in FEED_KEYWORDS.items():
            if keyword in name:
                files[feed] = path
    missing = set(FEED_KEYWORDS) - set(files)
    if missing:
        raise SystemExit(f"Missing CSVs for: {', '.join(sorted(missing))}")
    return files


def _write(out_dir: Path, feed: str, day: str, page: dict) -> None:
    path = csv_page_path(out_dir, feed, day)
    if path.exists():  # the CSV was not sorted by date: merge with what was written
        old = json.loads(path.read_text())
        if feed == "two-hr-forecast":
            page["items"] = old["items"] + page["items"]
        else:
            stations = {s["id"]: s for s in old["stations"] + page["stations"]}
            page["stations"] = list(stations.values())
            page["readings"] = old["readings"] + page["readings"]
    path.write_text(json.dumps(page, separators=(",", ":")))


def _metric_page(stations: dict, readings: dict, reading_type: str, unit: str) -> dict:
    return {
        "stations": list(stations.values()),
        "readings": [
            {"timestamp": ts, "data": [{"stationId": k, "value": v} for k, v in values.items()]}
            for ts, values in sorted(readings.items(), reverse=True)
        ],
        "readingType": reading_type,
        "readingUnit": unit,
    }


def convert_metric(feed: str, path: Path, out_dir: Path) -> int:
    days = 0
    current, stations, readings, rtype, unit = None, {}, {}, "", ""
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            day = row["date"]
            if day != current:
                if current is not None:
                    _write(out_dir, feed, current, _metric_page(stations, readings, rtype, unit))
                    days += 1
                current, stations, readings = day, {}, {}
            value = row["reading_value"]
            if value in ("", "NA", "null"):
                continue
            sid = row["station_id"]
            if sid not in stations:
                stations[sid] = {
                    "id": sid,
                    "deviceId": row.get("station_device_id") or sid,
                    "name": row["station_name"],
                    "location": {
                        "latitude": float(row["location_latitude"]),
                        "longitude": float(row["location_longitude"]),
                    },
                }
            readings.setdefault(row["timestamp"], {})[sid] = float(value)
            rtype = row.get("reading_type") or rtype
            unit = row.get("reading_unit") or unit
    if current is not None:
        _write(out_dir, feed, current, _metric_page(stations, readings, rtype, unit))
        days += 1
    return days


def _forecast_page(areas: dict, items: dict) -> dict:
    return {
        "area_metadata": list(areas.values()),
        "items": [
            {
                **item,
                "forecasts": [{"area": a, "forecast": t} for a, t in item["forecasts"].items()],
            }
            for _, item in sorted(items.items())
        ],
    }


def convert_forecast(path: Path, out_dir: Path) -> int:
    feed, days = "two-hr-forecast", 0
    current, areas, items = None, {}, {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            day = row["date"]
            if day != current:
                if current is not None:
                    _write(out_dir, feed, current, _forecast_page(areas, items))
                    days += 1
                current, areas, items = day, {}, {}
            area = row["location_name"]
            areas.setdefault(
                area,
                {
                    "name": area,
                    "label_location": {
                        "latitude": float(row["location_latitude"]),
                        "longitude": float(row["location_longitude"]),
                    },
                },
            )
            item = items.setdefault(
                row["timestamp"],
                {
                    "timestamp": row["timestamp"],
                    "update_timestamp": row["update_timestamp"],
                    "valid_period": {
                        "start": row["valid_period_start"],
                        "end": row["valid_period_end"],
                        "text": row["valid_period_text"],
                    },
                    "forecasts": {},
                },
            )
            item["forecasts"][area] = row["forecast_text"]
    if current is not None:
        _write(out_dir, feed, current, _forecast_page(areas, items))
        days += 1
    return days


def _convert(job: tuple[str, Path, Path]) -> tuple[str, int]:
    feed, path, out_dir = job
    if feed == "two-hr-forecast":
        return feed, convert_forecast(path, out_dir)
    return feed, convert_metric(feed, path, out_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--csv-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=CSV_CACHE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    files = find_files(args.csv_dir)
    for feed in files:  # start clean: unsorted CSVs are merged into existing day files
        for old in args.out_dir.glob(f"csv_{feed}_*.json"):
            old.unlink()
    jobs = [(feed, path, args.out_dir) for feed, path in files.items()]
    with multiprocessing.Pool(len(jobs)) as pool:
        for feed, chunks in pool.imap_unordered(_convert, jobs):
            days = len(list(args.out_dir.glob(f"csv_{feed}_*.json")))
            logger.info("%s: %d days (%d date blocks in the CSV)", feed, days, chunks)


if __name__ == "__main__":
    main()
