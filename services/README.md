# Backend development

AWS Lambda handlers built with FastAPI and Python. Contains the core backend business logic.

| Lambda | Entry point | Status |
|---|---|---|
| API | `handlers/api.py` (FastAPI via Mangum) | Recommendation, health and all three session routes done; user registration/verification not built |
| Ingest | `handlers/ingest.py` | Done. Every 5 minutes: fetch NEA, score, save snapshots, queue alerts |
| Notifier | `handlers/notifier.py` | Done. SQS to SES emails |

Contracts live in [docs/contracts/](../docs/contracts/): [lambda-backend.md](../docs/contracts/lambda-backend.md)
(REST contract and scoring method), [handlers.md](../docs/contracts/handlers.md) (deployment),
[dynamodb.md](../docs/contracts/dynamodb.md) and [data.md](../docs/contracts/data.md).

All commands below run from `services/`.

## 1. Setup

Python 3.12 or newer.

```bash
cd services
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

On Windows use `.venv/Scripts/python.exe` instead of `.venv/bin/python`. The `.venv`
directory is ignored by Git. Always run commands with the venv's Python: the editable
install is what makes `drysg` and `handlers` importable from `scripts/` and tests.

## 2. Environment variables

Copy the template and fill in what you need: `cp .env.example .env` (`.env` is ignored by Git).
Nothing loads `.env` automatically except `scripts/run_api_local.py`. The deployed Lambdas get
their values from infrastructure config. The full per-Lambda list is in
[handlers.md](../docs/contracts/handlers.md#environment-variables).

| What you are doing | Required variables |
|---|---|
| Run the tests | None. Tests use recorded NEA data, mocked HTTP and in-memory DynamoDB |
| Run the API locally (`run_api_local.py`) | `ONEMAP_TOKEN` (postal code lookup). Optional: `NEA_API_KEY` |
| Run the ingest locally (`run_ingest_local.py`) | None. Optional: `NEA_API_KEY` |
| Rebuild the DP percentile table | None. Optional: `NEA_API_KEY` |
| Deploy the API Lambda | `TABLE_AREA_SNAPSHOT`, `TABLE_POSTAL_CACHE`, `ONEMAP_TOKEN`, `APP_BASE_URL`; session routes also need `TABLE_USER`, `TABLE_MONITORING_SESSION`; optional `NEA_API_KEY`, `LOG_LEVEL` |
| Deploy ingest / notifier | See handlers.md |

- **`ONEMAP_TOKEN`**: register at <https://www.onemap.gov.sg/apidocs/register>, then put the
  token in `.env`. Tokens expire after about 3 days; if every postal lookup returns `503`,
  renew it.
- **`NEA_API_KEY`**: optional. Anonymous access works but is rate limited to a few requests
  per second, so a 429 can make a feed come back missing. Set a key for production.
- Local scripts always use fake AWS credentials and in-memory DynamoDB. They never touch a
  real AWS account.

## 3. Run the tests

```bash
.venv/bin/python -m pytest -q                      # everything
.venv/bin/python -m pytest -v                      # one line per test case
.venv/bin/python -m pytest tests/unit/test_scoring.py -v            # drying score rules
.venv/bin/python -m pytest tests/unit/test_api_recommendation.py -v # GET /recommendation
.venv/bin/python -m pytest -k "rain_veto" -v       # tests whose name matches
.venv/bin/python -m ruff check .                   # lint (imports, style)
```

The tests need no AWS account, OneMap token or network: they use recorded NEA responses,
HTTP mock transports and Moto (in-memory DynamoDB, SQS, SES). Score a set of your own
inputs by calling `compute_score()` from `drysg.core.scoring` (see `tests/unit/test_scoring.py`
for examples of building `Conditions`).

## 4. Run the API locally (FastAPI)

```bash
cp .env.example .env          # first time only, then set ONEMAP_TOKEN in .env
.venv/bin/python scripts/run_api_local.py
```

This starts uvicorn on <http://localhost:8000>. Weather is **live**: it fetches NEA data on
start and every 5 minutes. DynamoDB is faked in memory, so nothing is saved between runs.
Options: `--port 8765`, `--host 0.0.0.0`, `--refresh-minutes 5`.

### Monitoring sessions

The runner also creates in-memory User and MonitoringSession tables and seeds
`local-test@example.com` as verified. Override it with `--test-email user@example.com`.
This is a local fixture only; the deployed API never auto-verifies users and returns
`403 EMAIL_NOT_VERIFIED` until their User row has `verified: true`. User registration
and email verification endpoints remain a separate task.

Use Swagger at `/docs`, or send these requests with your HTTP client:

```http
POST /laundry-sessions
Content-Type: application/json

{"postalCode":"570150","email":"local-test@example.com"}
```

POST returns `201` with `id`, `postalCode`, `email`, `areaName`, `startedAt`,
`expiresAt`, and `active`. Responses also include `lastAlertState` once the notifier
has recorded an alert state; otherwise the field is omitted. This records the
notifier's state and does not confirm successful email delivery.
Use its `id` with `GET /laundry-sessions/{id}` and `DELETE /laundry-sessions/{id}`;
both return `200` with the same shape. Timestamps use Singapore time. Sessions last
12 hours; reads check expiry even before DynamoDB TTL deletes the row. DELETE is
idempotent and never creates a missing session. Starting again for the same email
atomically stops the previous session. Failed starts leave the old session active.

Creation stores the current score, band, rain veto and scoring method as the alert
baseline. Stale/missing weather or an obsolete scoring method returns `503`.
Malformed input returns `400`, unknown session IDs return `404`, and exhausted
concurrent-start retries return `409`. Errors retain `{error: {code, message}}`.

Deployment requirements and additional table permissions are listed in
[handlers.md](../docs/contracts/handlers.md#session-api-deployment). The API Gateway
infra branch still needs to wire these settings. This change does not deploy AWS resources.

### Recommendation

Call it with your postal code:

```bash
curl "http://localhost:8000/recommendation?postalCode=570150"
```

Or open <http://localhost:8000/docs> and use the interactive page ("Try it out").

What to check:

| Request | Expected |
|---|---|
| `?postalCode=570150` (any real postal code) | `200` with `score`, `recommendation`, `conditions`, `stationSources` |
| Raining at the nearest station, or rain in the forecast | `rainVeto: true`, `recommendation: "DO_NOT_HANG"` |
| PSI above 100 | `airQualityWarning: true`; the score and recommendation do not change |
| `?postalCode=12345` or no parameter | `400` `INVALID_POSTAL_CODE` |
| `?postalCode=999999` (not a real postal code) | `404` `POSTAL_CODE_NOT_FOUND` |
| Every lookup `503 DATA_UNAVAILABLE` | `ONEMAP_TOKEN` missing or expired, or NEA unreachable |
| `/health` | `{"status": "ok"}` |

The score is the percentile of the current Drying Potential in the historical table
(`drysg/core/dp_percentiles.py`); see section 6. A postal code resolves to the nearest NEA
forecast-area label, so it can land in a neighbouring area. Conditions such as `rainfallMm`,
`forecast` and `psi` are `null` when that feed is unavailable.

### Request and response

**Request**

```
GET /recommendation?postalCode=570150
```

| Parameter | Where | Required | Rule |
|---|---|---|---|
| `postalCode` | query string | yes | exactly six digits (surrounding spaces are trimmed) |

**Response `200`: good drying conditions** (`recommendation` is `HANG_NOW`)

```json
{
  "postalCode": "570150",
  "areaName": "Bishan",
  "score": 99,
  "recommendation": "HANG_NOW",
  "summary": "Good drying conditions. A good time to hang laundry.",
  "percentileDescription": "Drier than 99% of Singapore dry-weather readings in 2024",
  "updatedAt": "2026-10-04T14:00:00+08:00",
  "conditions": {
    "rainfallMm": 0.0,
    "temperatureC": 33.0,
    "humidityPercent": 55.0,
    "windSpeedMs": 4.1,
    "forecast": "Fair (Day)",
    "psi": 48.0,
    "psiBand": "Good"
  },
  "rainVeto": false,
  "airQualityWarning": false,
  "stationSources": {
    "rainfall": "S07",
    "humidity": "S109",
    "temperature": "S24",
    "wind": "S81"
  }
}
```

**Response `200`: rain detected.** The same weather, but 0.6 mm of rain at the nearest
station. Note the score is unchanged (99) while the recommendation is overridden:

```json
{
  "postalCode": "570150",
  "areaName": "Bishan",
  "score": 99,
  "recommendation": "DO_NOT_HANG",
  "summary": "Rain detected at nearby station (0.6 mm in last 5 min). Do not hang.",
  "percentileDescription": null,
  "updatedAt": "2026-10-04T14:00:00+08:00",
  "conditions": { "rainfallMm": 0.6, "temperatureC": 33.0, "humidityPercent": 55.0,
                  "windSpeedMs": 4.1, "forecast": "Fair (Day)", "psi": 48.0, "psiBand": "Good" },
  "rainVeto": true,
  "airQualityWarning": false,
  "stationSources": { "rainfall": "S07", "humidity": "S109", "temperature": "S24", "wind": "S81" }
}
```

**Fields**

| Field | Meaning |
|---|---|
| `score` | 0 to 100: percentile of the current Drying Potential in the 2024 history |
| `recommendation` | `"HANG_NOW"`, `"WAIT"` or `"DO_NOT_HANG"` (see below) |
| `summary` | One sentence for display. Adds an air-quality sentence when PSI is above 100 |
| `percentileDescription` | e.g. "Drier than 99% of ..."; `null` when rain vetoes |
| `updatedAt` | When the snapshot was computed, with the Singapore `+08:00` offset |
| `conditions` | Current values. `windSpeedMs` is converted from NEA's knots. `rainfallMm`, `forecast`, `psi` and `psiBand` are `null` if that feed was unavailable |
| `rainVeto` | `true` if rain (observed or forecast) forced `DO_NOT_HANG` |
| `airQualityWarning` | `true` if PSI is above 100. Display only |
| `stationSources` | The NEA station (or PSI region) behind each reading; can differ per metric |

**Error responses** (always `{"error": {"code", "message"}}`)

| Status | Request | Response body |
|---|---|---|
| `400` | `?postalCode=12345` or missing | `{"error":{"code":"INVALID_POSTAL_CODE","message":"postalCode must be exactly six digits"}}` |
| `404` | `?postalCode=999999` | `{"error":{"code":"POSTAL_CODE_NOT_FOUND","message":"No area found for postal code 999999"}}` |
| `503` | OneMap or NEA down, token missing, or snapshot older than 30 minutes | `{"error":{"code":"DATA_UNAVAILABLE","message":"..."}}` |
| `500` | unexpected failure | `{"error":{"code":"INTERNAL_ERROR","message":"Something went wrong"}}` |

### How the recommendation state is calculated

```ts
export type RecommendationState = "HANG_NOW" | "WAIT" | "DO_NOT_HANG"
```

The state is decided in two steps, in this order (code: `drysg/core/scoring.py`,
`compute_score()`):

```
 temperature, humidity, wind ──► Drying Potential (DP) ──► score 0-100 ──┐
                                                                         │
 rainfall (nearest station) ─┐                                           ▼
 2-hour forecast text ───────┴► rain veto? ── yes ─────────────► DO_NOT_HANG
                                    │ no
                                    ▼
                          score >= 70 ──► HANG_NOW
                          score 50-69 ──► WAIT
                          score  < 50 ──► DO_NOT_HANG

 PSI > 100 ──► airQualityWarning = true   (display only, never changes the state)
```

**Step 1: rain veto (checked first, overrides the score).** `rainVeto` is true if either:
- the nearest rainfall station reports **more than 0 mm** in the last 5 minutes, or
- the 2-hour forecast text contains `rain`, `shower`, `thunder` or `drizzle` (case-insensitive, so
  "Thundery Showers" and "Light Rain (Night)" both count).

```python
def rain_veto_reason(rainfall_mm, forecast_text):
    if rainfall_mm is not None and rainfall_mm > RAIN_VETO_MM:   # RAIN_VETO_MM = 0.0
        return f"Rain detected at nearby station ({rainfall_mm:g} mm in last 5 min)"
    if forecast_text is not None and is_rain_forecast(forecast_text):
        return f"{_strip_suffix(forecast_text)} forecast for the next 2 hours"
    return None
```

If a veto reason exists, the state is `DO_NOT_HANG` and `rainVeto` is `true`. The score is
**still computed and returned** (the veto never lowers it); the UI should show the veto instead.

**Step 2: otherwise the score decides.**

```python
HANG_NOW_MIN = 70
WAIT_MIN = 50

def band_for(score):
    if score >= HANG_NOW_MIN: return Band.HANG_NOW
    if score >= WAIT_MIN:     return Band.WAIT
    return Band.DO_NOT_HANG
```

| State | Condition | Meaning with the current (2024) table |
|---|---|---|
| `DO_NOT_HANG` | rain veto, **or** score < 50 | rain, or DP below the historical median (1.70) |
| `WAIT` | no veto and score 50 to 69 | DP between 1.70 and 2.57 |
| `HANG_NOW` | no veto and score 70 to 100 | DP at or above 2.57 |

The DP boundaries come from the percentile table, so they change when the table is rebuilt.

**How the score is calculated** (`compute_score()`):

1. Take temperature (°C), humidity (%) and wind speed from the nearest stations. Convert wind
   from knots to m/s: `u = knots × 0.514444`.
2. Compute Drying Potential (the aerodynamic term of Penman's evaporation equation):

   ```
   Es(T) = 0.6108 · exp(17.27·T / (T + 237.3))     saturation vapour pressure, kPa
   VPD   = Es(T) · (1 − RH/100)                     vapour pressure deficit, kPa
   DP    = (1 + 0.536·u) · VPD
   ```

3. Convert DP to a score: `score = highest k such that DP_PERCENTILES[k] <= DP` (0 if below the
   minimum, 100 at or above the maximum). `DP_PERCENTILES[k]` is the k-th percentile of DP over
   rain-free single-station 5-minute samples in 2024.

**Worked example** (the live reading for postal code 570150 from the manual test):

| Step | Value |
|---|---|
| Inputs | T = 28.5 °C, RH = 86.1%, wind = 6.71 kt = 3.452 m/s, rainfall 0 mm, forecast "Partly Cloudy (Night)", PSI 146 |
| Es(28.5) | 3.891 kPa |
| VPD | 3.891 × (1 − 0.861) = 0.541 kPa |
| DP | (1 + 0.536 × 3.452) × 0.541 = 2.850 × 0.541 = **1.542** |
| Score | `DP_PERCENTILES[45] = 1.526 <= 1.542 < DP_PERCENTILES[46] = 1.560`, so **score 45** |
| Rain veto | no (0 mm, forecast has no rain words) |
| State | 45 < 50, so **`DO_NOT_HANG`** (poor drying conditions, not a rain veto) |
| Air quality | PSI 146 is above 100, so `airQualityWarning: true`, shown in the summary only |

A drier example: 31 °C, 70% RH, 6 kt gives DP 3.578, score 84, so `HANG_NOW`.

**Inputs that do not decide the state**
- **PSI (air quality)** only sets `airQualityWarning` and adds a sentence to `summary`.
- **Forecast text** matters only through the rain keywords. "Cloudy" and "Fair" score the same.
- **Time of day** is not an input, but DP is naturally low at night, so night scores are low.

**Missing data.** A missing temperature, humidity or wind reading is replaced by its 2024
median (`DP_INPUT_MEDIANS`) and the response sets `degraded: true`, shows that value as null and
says the score is an estimate. An area is only skipped when all three are missing, or when both
rainfall and the forecast are missing (the veto cannot be decided); the API then returns `503`
once its last snapshot is older than 30 minutes.

## 5. Run the ingest locally (no server)

```bash
.venv/bin/python scripts/run_ingest_local.py                      # score every area from live data
.venv/bin/python scripts/run_ingest_local.py --watch-area Bishan  # also seed a session and show the alert
.venv/bin/python scripts/run_ingest_local.py --pace 2             # wait 2 s between NEA calls if you see 429s
```

It runs the real ingest handler with in-memory AWS and prints the area scores and any alert
message that would be queued.

## 6. Rebuild the drying-score percentile table (e.g. for 2025)

`drysg/core/dp_percentiles.py` is generated from a year of NEA history by
`scripts/build_dp_percentiles.py`. It is a constant baked into the deployment package. The
Lambdas never run the script. The current table is built from **2024**.

```bash
# 1. Build 2025 into a separate file first, so the 2024 table is not overwritten.
.venv/bin/python -I scripts/build_dp_percentiles.py --year 2025 \
    --cache-dir ~/dp-cache-2025 \
    --output /tmp/dp_percentiles_2025.py \
    --samples-csv /tmp/dp_samples_2025.csv
```

- It downloads about 4,400 pages from data.gov.sg at roughly one per second, so expect
  **1.5 to 2.5 hours**. Run it in a terminal that stays open (or with `nohup`). Downloads are
  cached in `--cache-dir`, so an interrupted run resumes where it stopped. Use a folder that
  persists; the default is `services/.cache/dp_percentiles` (ignored by Git).
- `--windows-per-day` (default 3) sets how many 25-minute windows are sampled per day. More
  windows means more samples and a longer run.
- The script refuses to write a table built from fewer than 1,000 usable samples.

```bash
# 2. Compare against the current table (the year, sample count and all 101 values):
diff <(sed -n '/^DP_PERCENTILES/,$p' drysg/core/dp_percentiles.py) \
     <(sed -n '/^DP_PERCENTILES/,$p' /tmp/dp_percentiles_2025.py)

# 3. If you are happy with it, replace the module and re-verify:
cp /tmp/dp_percentiles_2025.py drysg/core/dp_percentiles.py
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_ingest_local.py
```

Then re-check the 70/50 band thresholds (`HANG_NOW_MIN`, `WAIT_MIN` in
`drysg/core/scoring.py`) and `SCORE_DROP_THRESHOLD` in `drysg/core/alerts.py`, commit the
regenerated file and the CSV (optional audit trail, kept in `docs/` for 2024), and redeploy
the API and ingest Lambdas. They share the table, so they must be deployed together. Scores
shift slightly on the next ingest run.

To rebuild 2024 or inspect how the table is derived, use `--year 2024 --samples-csv ...`; the
CSV has one row per station sample with its readings, DP and resulting score.

## Other notes

Stream A provides weather ingestion, location lookup and weather/cache repositories. See
[the data handoff](../docs/contracts/data.md) for usage, failure behavior and remaining
integration work. The scheduled ingest handler calls Stream B's alert trigger after saving
snapshots. It requires `TABLE_AREA_SNAPSHOT`, `TABLE_MONITORING_SESSION` and
`ALERT_QUEUE_URL`; the notifier consumes SQS separately and sends via SES. Terraform owns
table provisioning; the repositories never create real tables.
