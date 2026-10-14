# Lambda Backend Contract (REST API + scoring)

Status: `GET /recommendation` and the three session routes are **implemented**; user
registration/verification routes are still specified but not built (see section 9).
This is the frontend/backend contract for the API Lambda
(`handlers.api.handler`, API Gateway HTTP API proxy). [openapi.yaml](openapi.yaml) and the
examples in [mocks/](mocks/) follow it, and `tests/unit/test_contracts.py` checks that the
code's real responses and generated schema match them. Section 6 lists what changed from the
previous contract. Deployment details (env vars, IAM, SQS) stay in [handlers.md](handlers.md).

All JSON is `camelCase`. All timestamps are ISO 8601 with the Singapore offset (`+08:00`).

## 1. Endpoints

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/recommendation?postalCode=120123` | Latest cached recommendation for a postal code | `200` `DryingRecommendation` |
| POST | `/laundry-sessions` | Start monitoring a postal code for an email | `201` `LaundrySession` |
| GET | `/laundry-sessions/{id}` | Read monitoring state | `200` `LaundrySession` |
| DELETE | `/laundry-sessions/{id}` | Stop monitoring | `200` `LaundrySession` |

### GET /recommendation
- `postalCode` is required and must match `^\d{6}$`.
- Resolves the postal code to its nearest NEA forecast area (OneMap, cached in `PostalCache`) and
  returns the area's latest snapshot from `AreaSnapshot`. It never calls NEA on the request path
  unless no snapshot exists for the area (cold-start fallback).
- A snapshot older than **30 minutes** (6 missed ingest runs) is treated as unavailable (`503`).
  This threshold is a proposal (open question 3).

### POST /laundry-sessions
```json
{ "postalCode": "120123", "email": "user@example.com" }
```
- Requires `User.verified = true`; missing/unverified users return `403 EMAIL_NOT_VERIFIED`.
  `POST /users` and `POST /users/verify` remain unimplemented. SES identity verification
  is separate from application verification. The local runner seeds a mock verified user.
- One active session per email: starting a new one atomically stops the previous one,
  using a conditional pointer on User (see [dynamodb.md](dynamodb.md)).
- Sets the session's `baseline_score`, `baseline_band`, `baseline_veto` and `baseline_method`
  from the current snapshot's `result` (`score`, `band`, `veto`, `method`). The alert trigger
  compares against these, and only compares scores when the methods match.
- Sessions expire **12 hours** after `startedAt`, with whole-second timestamps.
- An unavailable/stale snapshot or an obsolete scoring method returns `503`, preserving
  any existing session. Email and postal-code whitespace is trimmed; emails are lowercase.

### GET /laundry-sessions/{id}
Returns the `LaundrySession`. `active` is `false` once stopped or past `expiresAt`. The current
score is not included: the frontend fetches it separately with `GET /recommendation` (the team's
agreed design).

### DELETE /laundry-sessions/{id}
Idempotent. Stopping an already stopped or expired session returns `200` with `active: false`.
The alert email's stop link is `{APP_BASE_URL}/stop?session={id}`; the frontend `/stop` route calls
this endpoint. The session `id` (UUID4) is the bearer secret, so the `email` is never echoed back
in any response.

### Errors
Every non-2xx response uses:
```ts
interface ApiError {
  error: { code: ErrorCode; message: string }
}
```

| Status | `code` | When |
|---|---|---|
| 400 | `INVALID_POSTAL_CODE` | Missing or not six digits |
| 400 | `INVALID_EMAIL` | Malformed email |
| 400 | `INVALID_REQUEST` | Missing or malformed JSON request body |
| 400 | `INVALID_SESSION_ID` | `id` is not a UUID |
| 403 | `EMAIL_NOT_VERIFIED` | POST session for an unverified email |
| 404 | `POSTAL_CODE_NOT_FOUND` | OneMap has no exact match |
| 404 | `SESSION_NOT_FOUND` | Unknown `id` |
| 409 | `SESSION_CONFLICT` | Concurrent starts exhausted retries; try again |
| 503 | `DATA_UNAVAILABLE` | OneMap/NEA unavailable, cold start with no data, stale snapshot |
| 500 | `INTERNAL_ERROR` | Anything else (logged, details not leaked) |

DynamoDB failures and corrupt records map to `503`/`500`, never to `POSTAL_CODE_NOT_FOUND`.

CORS: allow the CloudFront origin (`APP_BASE_URL`), methods `GET, POST, DELETE, OPTIONS`.

## 2. Shared types (frontend and backend)

```ts
export type RecommendationState = "HANG_NOW" | "WAIT" | "DO_NOT_HANG"

export interface WeatherConditions {
  rainfallMm: number        // 5-minute total at the nearest rainfall station
  temperatureC: number
  humidityPercent: number
  windSpeedMs: number       // metres per second, 1 decimal (NEA reports knots; converted by the backend)
  forecast: string          // NEA 2-hour forecast text, e.g. "Partly Cloudy (Day)"
  psi: number               // 24-hour PSI of the nearest PSI region
  psiBand: "Good" | "Moderate" | "Unhealthy" | "Very Unhealthy" | "Hazardous"
}

/** Which NEA station supplied each local observation. They can differ per metric. */
export interface StationSources {
  rainfall: string
  humidity: string
  temperature: string
  wind: string
}

export interface DryingRecommendation {
  postalCode: string
  areaName: string
  score: number                       // integer 0-100, see section 3
  recommendation: RecommendationState
  summary: string                     // one human sentence, e.g. "Rain detected nearby. Do not hang."
  percentileDescription?: string      // e.g. "Drier than 82% of Singapore weather in 2024"
  updatedAt: string                   // when the snapshot was computed
  conditions: WeatherConditions
  rainVeto: boolean
  airQualityWarning: boolean
  stationSources?: StationSources
}

export interface LaundrySession {
  id: string                          // UUID4
  postalCode: string
  startedAt: string
  expiresAt: string
  active: boolean
}
```

Field rules:
- `psiBand` follows NEA descriptors by PSI value: 0-50 Good, 51-100 Moderate, 101-200 Unhealthy,
  201-300 Very Unhealthy, above 300 Hazardous.
- `stationSources` is omitted if the snapshot predates station tracking (hence optional).
- `percentileDescription` is omitted when `rainVeto` is true.

## 3. Recommendation logic

Rain and air quality are **separate parameters**; they are not weighted into the score.

1. **Rain is a hard veto.** `rainVeto = true` if `rainfallMm > 0` at the nearest station, or the
   2-hour forecast contains `rain`, `shower`, `thunder` or `drizzle` (existing behaviour in
   `scoring.py`, kept). If `rainVeto`, `recommendation = "DO_NOT_HANG"`.
2. **Otherwise** `recommendation` comes from the Drying Score:
   `score >= 70` → `HANG_NOW`, `score >= 50` → `WAIT`, otherwise `DO_NOT_HANG`
   (thresholds unchanged from `HANG_NOW_MIN` / `WAIT_MIN`).
3. **Air quality** never changes `recommendation`. `airQualityWarning = true` when
   `psiBand` is `Unhealthy`, `Very Unhealthy` or `Hazardous` (PSI > 100); the UI shows a warning.
4. When `rainVeto` is true the `score` is still returned (computed from DP, never capped) so the
   field is always a number. The UI should hide it and show the veto instead.

### Drying Score = percentile of Drying Potential

Temperature, humidity and wind are combined with one physics-based quantity rather than arbitrary
weights. The 0-100 value is only a normalisation against historical Singapore weather.

References:
1. Bayliss et al. (2011), "Do you want to hang out?" applies the Penman evaporation model to
   estimate outdoor clothes-drying time. Evaporation depends on temperature, relative humidity and wind.
2. Huang & Chen (2010), *Textile Research Journal*: 4 fabrics × 120 combinations of temperature,
   relative humidity and wind speed. Higher temperature, lower humidity and higher wind increase
   water-vapour transmission.
3. Penman (1948): the evaporation model used as the physical basis.

Formulas (T in °C, RH in %, u in m/s):

```
Es(T) = 0.6108 * exp(17.27 * T / (T + 237.3))      # saturation vapour pressure, kPa
VPD   = Es(T) * (1 - RH / 100)                      # vapour pressure deficit, kPa
DP    = (1 + 0.536 * u) * VPD                       # drying potential (Penman aerodynamic term)
```

Unit handling: NEA wind speed is in **knots**; `u = knots * 0.514444`. Humidity is clamped to
0-100 before use. `windSpeedMs` in the response is the same `u` that went into DP.

### DP to score

The percentile table is computed **once, offline**, and stored in the Lambda code (no runtime
database or data.gov.sg call):

```python
# services/drysg/core/dp_percentiles.py
DP_PERCENTILES: tuple[float, ...] = (
    0.12,  # score 0
    0.14,  # score 1
    # ... 101 values in total, non-decreasing ...
    5.20,  # score 100
)
```

`DP_PERCENTILES[k]` is the k-th percentile (k = 0..100) of historical DP. Scoring:

```python
score = bisect.bisect_right(DP_PERCENTILES, dp) - 1   # then clamp to 0..100
```

so a DP higher than 82% of history scores 82, a DP below the minimum scores 0 and a DP at or above
the maximum scores 100. Worked example: current DP 3.0 exceeds 82% of historical values, so score = 82.

The table must have exactly 101 entries and be non-decreasing. A unit test asserts both.

### Building the table (offline script, `services/scripts/build_dp_percentiles.py`)
1. Download all of 2024 (a full year covers monsoon seasons) of 5-minute air temperature, relative
   humidity, wind speed and rainfall from data.gov.sg (NEA real-time APIs, historical).
2. For each 5-minute timestamp, **every station that reports temperature, humidity and wind gives
   one sample** from its own readings. Live scoring uses the nearest single station, so the history
   is built from single stations too. An island-wide median would have a narrower spread and push
   live scores towards 0 and 100.
3. **Drop a sample when that station had rain** (its own gauge, or the nearest gauge if it has
   none), because rain is handled by the veto and the score only describes rain-free conditions.
   Rain elsewhere on the island does not drop it, which matches the live veto and avoids biasing
   the history towards island-wide dry days. Also drop samples missing any of the three metrics.
4. Convert wind to m/s, compute DP per sample, sort ascending.
5. Emit the 0th-100th percentile values (rounded to 3 decimal places) as `DP_PERCENTILES`, the
   median of each input as `DP_INPUT_MEDIANS` (the fallback for a missing live feed), and record
   the source year and sample count next to the table.

`percentileDescription` text: `"Drier than {score}% of Singapore weather in {year}"`.
Because the table has no rainy timestamps, the percentile reads as "of rain-free conditions";
word it accordingly in the UI ("of dry-weather readings").

### Scoring input requirements
- Temperature, humidity and wind feed DP. If one or two are missing (e.g. an NEA feed is down),
  each missing one is replaced by its 2024 median (`DP_INPUT_MEDIANS`, generated with the table),
  the result is flagged `degraded`, and the response shows the missing value as null with a note
  in `summary`. One failed feed therefore never takes every area offline.
- Required to publish a snapshot: at least one of temperature, humidity or wind, and at least one
  of rainfall or forecast (needed for the veto decision). Otherwise the area is skipped and the
  API returns `503` once its last snapshot is stale.
- PSI is required in the response type. If the PSI feed is missing, the ingest keeps the area's
  last known PSI while it is under 2 hours old (existing freshness rule); beyond that the area is
  skipped (open question 2).

## 4. Storage mapping

No new tables. Reuse the tables in [dynamodb.md](dynamodb.md):

| Contract | Storage |
|---|---|
| `DryingRecommendation` | Built from `AreaSnapshot` + `PostalCache` (postal code → area). Snapshot stores DP, `score`, `rainVeto`, per-metric station IDs. |
| `LaundrySession` | `MonitoringSession` item (`session_to_item()` / `session_from_item()`); `active` = `status == "active"` and now < `expiresAt`. |

The snapshot item gains `dp` (number), `station_sources` (map) and `air_quality_warning` (bool) so the
API does no scoring work on the request path. Ingest and API must share one `compute_score` so the
on-demand fallback and scheduled path cannot drift.

## 5. Examples

`GET /recommendation?postalCode=530123` (dry day):
```json
{
  "postalCode": "530123",
  "areaName": "Hougang",
  "score": 82,
  "recommendation": "HANG_NOW",
  "summary": "Warm, breezy and fairly dry. Good time to hang laundry.",
  "percentileDescription": "Drier than 82% of Singapore dry-weather readings in 2024",
  "updatedAt": "2026-10-04T12:35:00+08:00",
  "conditions": {
    "rainfallMm": 0,
    "temperatureC": 31.0,
    "humidityPercent": 62.5,
    "windSpeedMs": 2.2,
    "forecast": "Fair (Day)",
    "psi": 48,
    "psiBand": "Good"
  },
  "rainVeto": false,
  "airQualityWarning": false,
  "stationSources": {
    "rainfall": "S77",
    "humidity": "S77",
    "temperature": "S24",
    "wind": "S81"
  }
}
```

Raining: same shape with `"rainVeto": true`, `"recommendation": "DO_NOT_HANG"`, `"rainfallMm": 0.6`,
no `percentileDescription`, and `summary` = `"Rain detected nearby. Do not hang."`.

`POST /laundry-sessions` → `201`:
```json
{
  "id": "6f1c2b8e-3a4d-4e5f-9a7b-1c2d3e4f5a6b",
  "postalCode": "530123",
  "startedAt": "2026-10-04T12:40:00+08:00",
  "expiresAt": "2026-10-05T00:40:00+08:00",
  "active": true
}
```

Error:
```json
{ "error": { "code": "POSTAL_CODE_NOT_FOUND", "message": "No area found for postal code 999999" } }
```

## 6. Changes from the previous contract

| Area | Current | This contract |
|---|---|---|
| Query param | `GET /recommendation?postal=` | `postalCode` |
| Session routes | `POST /monitoring`, `GET/DELETE /monitoring/{session_id}` | `/laundry-sessions`, `/laundry-sessions/{id}` |
| Naming | `snake_case` | `camelCase` |
| Recommendation | `band` ("Hang Now", "Wait", "Do Not Hang"), `veto`, `sub_scores`, `psi{}`, `degraded` | `recommendation` enum, `rainVeto`, `airQualityWarning`, flat `WeatherConditions`; no sub-scores or `degraded` |
| Wind | knots | m/s (`windSpeedMs`) |
| Score | Weighted rain 30 / humidity 40 / wind 10 / temp 20 | DP percentile; rain and PSI outside the score |
| PSI | Informational | Drives `airQualityWarning` |
| Session body | `postal_code`, `email` | `postalCode`, `email` |
| Session response | Includes `email`, `area`, `status`, `current` | `LaundrySession` (no email); `GET` adds `recommendation` |
| Errors | adds `INVALID_TOKEN` for `/users/verify` | unchanged there, plus `INVALID_SESSION_ID` |

Done: `services/drysg/core/dp_percentiles.py`, the rewritten `compute_score` and `ScoreResult`,
`handlers/api.py`, `openapi.yaml`, the mocks in `mocks/` and `test_contracts.py`. The domain models
still use the internal `Band` values ("Hang Now", "Wait", "Do Not Hang"); only the API layer
(`drysg/api/recommendation.py`) translates to the `HANG_NOW` / `WAIT` / `DO_NOT_HANG` enum, so
`AlertMessage`, the alert emails and the notifier are unchanged. The `/users` endpoints are
unaffected.

## 7. Open questions

1. **Score when vetoed:** returned as a number (this doc) or hidden/`0`? The brief says "otherwise show Drying Score".
2. **Missing PSI:** keep last known PSI for 2 hours then skip the area (this doc), or make `psi`/`psiBand` optional?
3. **Staleness cutoff:** 30 minutes proposed for `503`.
4. **Band thresholds:** 70/50 carried over from the existing scorer; they should be re-checked
   against the percentile distribution (a score of 70 now means "drier than 70% of dry-weather history").
5. **Historical year:** 2024 per the brief; refresh annually, or use a rolling 12 months?
6. **Session length:** 12 hours assumed from the existing mock; confirm.

## 8. Backlog (future work)

- **Rebuild the DP percentile table with 2025 data.** The current table is built from 2024
  (4,352 rain-free marks, `DP_PERCENTILES_YEAR = 2024`). The data.gov.sg API still serves
  2025 and 2026, so the table can be refreshed. Prefer a full year: a partial 2026 would miss
  the wetter Nov-Dec weather and bias every score.
  - Run: `cd services && .venv/bin/python -I scripts/build_dp_percentiles.py --year 2025 --cache-dir <dir> --samples-csv <csv>`
    (about 1.5-2.5 hours because of the NEA rate limit; the 2024 cache is not reused).
  - Compare against the 2024 table before switching, and consider merging 2024 + 2025
    (needs a small script change) to smooth out a single unusual year.
  - After switching, re-check the 70/50 band thresholds (open question 4) and
    `SCORE_DROP_THRESHOLD` in `core/alerts.py`, then commit the regenerated module and redeploy.
- **Daytime-only table.** Median DP varies from about 1.0 at 06:00 to about 4.1 at 13:00, so the
  all-hours percentile makes nearly every night score low. Evaluate a 08:00-18:00 table
  (a quick rebuild from the cache) so the score reads as "good for daytime hanging".

## 9. Implementation status

Implemented: `GET /recommendation`, `GET /health`, and `POST/GET/DELETE /laundry-sessions`.
- Code: `services/drysg/api/` (`app.py` FastAPI app, `recommendation.py` snapshot-to-response
  mapping, `models.py` response models, `deps.py` environment wiring) and the Lambda entry
  point `services/handlers/api.py` (Mangum). Tests: `services/tests/unit/test_api_recommendation.py`.
- Run locally with live NEA and OneMap data and in-memory DynamoDB (needs `ONEMAP_TOKEN` in
  `services/.env`): `cd services && .venv/bin/python scripts/run_api_local.py`, then
  `curl "http://localhost:8000/recommendation?postalCode=570150"` or open `/docs`.
- Session code: `drysg/sessions.py` (lifecycle), `drysg/db/sessions.py` (atomic persistence),
  `drysg/api/sessions.py` (response mapping), plus routes in `drysg/api/app.py`.
  Tests cover request validation, expiry, replacement races and the alert pipeline.
- Not yet implemented: `/users`, `/users/verify`.

Deviations from section 2 (decided while implementing):
- `conditions.rainfallMm`, `forecast`, `psi`, `psiBand` and each `stationSources` entry are
  **nullable**. Scoring only needs temperature, humidity, wind and either rainfall or a forecast,
  so those feeds can be missing from a valid snapshot. The frontend must handle `null`.
- `stationSources` is always present on this endpoint (entries may be null).
- Cold start: if the area has no snapshot, or the catalogue is empty, the API runs one NEA
  refresh itself. Its role therefore also needs `dynamodb:BatchWriteItem` and `dynamodb:Scan`
  on AreaSnapshot (beyond the `GetItem/PutItem` listed in handlers.md).
- Snapshots older than 30 minutes return `503 DATA_UNAVAILABLE` (`SNAPSHOT_MAX_AGE`).
- The nearest forecast-area label is used, so a postal code can resolve to a neighbouring area
  (for example 570150 resolves to Toa Payoh).
