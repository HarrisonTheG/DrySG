"""Download days of NEA history from the data.gov.sg real-time API into a local cache.

No account needed (set NEA_API_KEY in services/.env for a higher rate limit). About 200
pages a day at one page per second, so ~5 minutes per day; the cache makes an interrupted
run resumable, so a whole year can be fetched over several sessions. The bulk CSVs +
`convert_nea_csv.py` give the same data much faster.

    cd services
    .venv/bin/python -I scripts/evaluation/download_history.py                # 24 default days
    .venv/bin/python -I scripts/evaluation/download_history.py --year 2024    # all of 2024
    .venv/bin/python -I scripts/evaluation/download_history.py --days 2024-06-09
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import (  # noqa: E402
    API_CACHE,
    FEEDS,
    SERVICES,
    DayFetcher,
    load_env,
    parse_days,
    year_days,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--days", help="Comma-separated YYYY-MM-DD days instead of the default")
    choice.add_argument(
        "--year", type=int, help="Every day of this year (~30 h for a year; resumable)"
    )
    parser.add_argument("--cache-dir", type=Path, default=API_CACHE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_env(SERVICES / ".env")
    days = year_days(args.year) if args.year else parse_days(args.days)
    with httpx.Client() as http:
        fetcher = DayFetcher(http, args.cache_dir, os.environ.get("NEA_API_KEY") or None)
        for n, day in enumerate(days, start=1):
            for feed in FEEDS:
                pages = fetcher.day(feed, day)
                logging.info("Day %d/%d %s %s: %d pages", n, len(days), day, feed, len(pages))
            items = fetcher.half_hourly_forecasts(day)
            logging.info("Day %d/%d %s half-hourly forecasts: %d", n, len(days), day, len(items))


if __name__ == "__main__":
    main()
