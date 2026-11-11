# Alert evaluation: replaying historical weather

These scripts produce the alert evaluation in the final report (Section 4.2). Real NEA history is
replayed through the production scoring (`compute_score`) and alert rules (`decide_alert`), and
DrySG's rules are compared with ablations and simpler alert strategies. Nothing is emailed and no
AWS account is needed: alerts are only counted.

Run everything from `services/` with the project venv (see `services/README.md`).
Downloaded data, converted pages and results go under `services/.cache/`, which Git ignores.

| File | Purpose |
|---|---|
| `history.py` | Downloads and caches history, rebuilds the API payloads for any 5-minute mark, and scores every area |
| `download_history.py` | Downloads days from the data.gov.sg real-time API (no manual step; ~5 min per day) |
| `convert_nea_csv.py` | Converts the bulk yearly CSVs into per-day pages (fast; used for the full year) |
| `evaluate_alerts_replay.py` | Simulates sessions, applies each alert policy, prints the table, writes JSON |

## Option A: full year from the bulk CSVs (used in the report)

1. Download the 2024 CSVs from data.gov.sg into a folder, e.g. `nea_csv/`:
   *Historical Rainfall across Singapore (2024)*, *Historical Relative Humidity across Singapore
   (2024)*, *Historical Air Temperature across Singapore (2024)*, *Historical Wind Speed across
   Singapore (2024)* and *Historical 2-hour Weather Forecast (2024)* (about 4.3 GB in total).
2. Convert them (about 2 minutes):

   ```bash
   .venv/bin/python -I scripts/evaluation/convert_nea_csv.py --csv-dir path/to/nea_csv
   ```

3. Run the evaluation (about 5 minutes on 8 cores):

   ```bash
   .venv/bin/python -I scripts/evaluation/evaluate_alerts_replay.py --source csv
   ```

## Option B: download from the API in code (no manual step, slower)

```bash
.venv/bin/python -I scripts/evaluation/download_history.py --year 2024   # whole year, ~30 h
.venv/bin/python -I scripts/evaluation/download_history.py               # or 24 sample days, ~2 h
.venv/bin/python -I scripts/evaluation/evaluate_alerts_replay.py --source api
```

The download is rate limited (about one page a second; set `NEA_API_KEY` in `services/.env` for a
higher limit). Every page is cached, so it can be stopped and restarted at any time and picks up
where it left off. `--year` fetches the 9th and 23rd of each month first, so even a partial run
covers every season. The evaluation uses every day downloaded so far, and `--days
2024-06-09,2024-08-09` limits either step to chosen days. A whole-day query of the forecast
archive only returns forecasts from about noon, so the earlier half-hourly forecasts are
fetched one by one.

## What is simulated

- A user hangs laundry at 08:00, 10:00 or 12:00 when it is not raining and leaves it out for
  8 hours. Areas that share the same weather stations replay identically and are counted once.
- Ground truth for rain is the 5-minute rainfall at the area's nearest gauge. A rain onset is
  rain after at least 30 dry minutes.
- Policies: the deployed rules; ablations without the two-run hold, without the 60-minute
  cooldown, and comparing with the previous snapshot instead of the baseline; email on any
  worsening vs. 5 minutes ago; email only when rain is measured.
- Metrics: emails per session (mean, p90), rain onsets already warned, the share warned before
  the rain started and the median lead time, and the 2-hour precision of forecast-triggered
  warnings.

On sample days the CSV and API sources agree within a few percent; small differences come from
how the two archives record station readings and forecasts.
