# Stream A: weather ingestion and location lookup

This implementation follows Isaac's `Conditions`, `ScoreResult`, `AreaSnapshot`,
PostalCache schema and three-handler deployment contract. Scoring weights and
the user/session API are unchanged.

## Ownership and integration

- Stream A owns `drysg.data`, `AreaSnapshotRepository`, `PostalCacheRepository`
  and the collection portion of `handlers.ingest.handler`.
- Stream C calls `LocationService.resolve_postal_code()` and
  `AreaSnapshotRepository.get()`. It owns HTTP responses and user/session state.
  Shared location records live in `drysg.core.locations`; weather/scoring records
  continue to live in Isaac's `drysg.core.models`.
- Stream B continues to own scoring and alert decisions. `IngestionService.run()`
  returns the snapshots saved by the run, including their previous scores/bands.
  The scheduled handler passes all of these snapshots to Stream B's
  `run_alert_trigger_from_env()` after successful persistence. Decisions compare
  each session's baseline with the new snapshot, not with `prev_score`/`prev_band`.
  Do not pre-filter snapshots by changes since the last ingest run: an unchanged
  area score can still require an alert for a particular session.
  `IngestionService` itself remains free of alert dispatch, so API callers can
  reuse it for a cold-start refresh without running scheduled alert decisions.

## Decisions for this implementation

- Forecast area metadata supplies the common area names and label coordinates.
  Distance is great-circle distance; ties are broken by identifier for repeatability.
- Each metric independently selects the nearest station with a fresh valid reading.
  Observations older than 15 minutes and future timestamps are excluded.
  Invalid station/reading records are skipped independently so one faulty sensor
  cannot discard another station's valid rainfall observation.
- A forecast is eligible only during its `[start, end)` validity window and when
  its update timestamp is not in the future.
- PSI uses the nearest of the five official region label coordinates in the
  recorded PSI response. This is an MVP approximation, not administrative boundary
  containment. National readings are excluded. PSI readings may be up to 2 hours old;
  their original timestamp is retained. These freshness windows are configurable.
- Unavailable optional feeds become missing inputs. A missing/invalid forecast-area
  catalogue aborts the run without writes. An area with no scoring inputs is skipped;
  its old snapshot is not marked fresh. API callers must still check snapshot age.
- `updated_at` records computation time; each observation retains `observed_at`.
- OneMap lookup requires a configured token for uncached postal codes. Its HTTP-200
  `error` responses are failures, including the warning in the recorded fixture.
  The fixture remains unchanged; successful-path tests remove that warning explicitly.
- The postal service validates six ASCII digits, matches OneMap's exact POSTAL field,
  caches successful lookups only, and uses the supplied area catalogue. The API should
  obtain that catalogue from `AreaSnapshotRepository.list_areas()`; a cold empty
  catalogue raises `DataUnavailableError`. The API owner still needs to wire the
  cold-start fallback promised by `openapi.yaml`, or coordinate a contract change.
  These helpers do not automatically refresh NEA on each user request.
- HTTP requests use bounded timeouts; NEA makes one request per endpoint per run.
  Retries are left to the next scheduled run. Database errors propagate so failed
  persistence is not reported as a successful refresh.
- Isaac's scorer treats a valid forecast as rain information even when the rainfall
  feed is missing. The existing degraded flag and score cap are preserved: the cap
  for missing rain applies when both rainfall and forecast are missing.

## API handoff

The caller supplies a boto3 DynamoDB resource and owns the HTTP client's lifetime:

```python
import os

import boto3
import httpx

from drysg.data import LocationService, OneMapClient
from drysg.db import AreaSnapshotRepository, PostalCacheRepository

dynamodb = boto3.resource("dynamodb", region_name="ap-southeast-1")
snapshots = AreaSnapshotRepository(dynamodb, os.environ["TABLE_AREA_SNAPSHOT"])
postals = PostalCacheRepository(dynamodb, os.environ["TABLE_POSTAL_CACHE"])

with httpx.Client() as http:
    locations = LocationService(OneMapClient(http, token=os.getenv("ONEMAP_TOKEN")),
                                postals)
    location = locations.resolve_postal_code("018956", areas=snapshots.list_areas())
    snapshot = snapshots.get(location.area)
```

`snapshot` may be `None`, or old after failed refreshes. The API must check its age,
build the existing `Recommendation` response, and format timestamps with the
Singapore offset (`+08:00`) required by OpenAPI. Repository records retain the
actual timezone-aware timestamps; default computation/cache clocks use UTC.
`list_areas()` scans only label fields with pagination; an API instance may reuse
the resulting catalogue rather than scanning on every request. Cached postal
lookups also work with an empty supplied catalogue.

Suggested exception mapping in the API (using the existing `Error` schema):

| Data-layer result | API responsibility |
|---|---|
| `ValueError` for an invalid postal code | `400` |
| `PostalCodeNotFoundError` | `404` |
| `UpstreamError` or `DataUnavailableError` | `503`, subject to the cold-start fallback |
| DynamoDB failure or corrupt stored record | Log internally and handle as unavailable; do not report postal not found |

The OneMap token is configuration owned by the deployment. There is no automatic
token renewal in this service. See the [official registration guide](https://www.onemap.gov.sg/apidocs/register).

When creating a monitoring session, the API must set `baseline_score`,
`baseline_band` and `baseline_veto` from the chosen current snapshot. Use
`drysg.db.items.session_to_item()` / `session_from_item()` for the session's
database shape, including epoch `expires_at`. The notifier owns subsequent
baseline updates. Session CRUD and the frontend `/stop` route remain integration
work for their respective owners.

## Scheduled handler and infrastructure

- Deploy `handlers.ingest.handler` from `services/` with `TABLE_AREA_SNAPSHOT`,
  `TABLE_MONITORING_SESSION`, `ALERT_QUEUE_URL`, optional `NEA_API_KEY`, and the
  Lambda region. Required configuration is checked before fetching or writing.
  Tables, the `area-status-index` and queue must already exist. The ingest role
  needs `dynamodb:Query` on that index and `sqs:SendMessage` on the queue, in
  addition to snapshot permissions.
- Ingestion reads each prior snapshot with a consistent `GetItem` and writes using
  DynamoDB `BatchWriteItem`. This version does not use `BatchGetItem`.
- API catalogue lookup additionally requires `dynamodb:Scan` on AreaSnapshot.
  Postal lookup needs `GetItem` and `PutItem` on PostalCache.
- Keep reserved concurrency at 1 for scheduled ingestion. Batch writes are not
  atomic across areas: failed invocations can leave a partially refreshed table.
  A retry is another refresh; this service does not provide exactly-once alert
  transitions. A failed refresh does not call the trigger. A raised trigger error
  fails the invocation even though snapshots have already been saved. The next
  invocation evaluates session baselines again, including when area scores hold.
- The handler returns counts and missing-feed/skipped-area diagnostics plus
  `alerts`: `areas_checked`, `sessions_checked`, `alerts_queued` and
  `failed_message_ids`. Per-entry SQS failures follow Isaac's existing policy:
  retry once, then report failures without failing the ingestion invocation;
  the next scheduled run re-evaluates unchanged session baselines.
  `missing_feeds` describes failed fetches, not every stale or invalid observation.
- The trigger receives only fresh snapshots returned by this run, never old
  snapshots retained for skipped areas. Its current clock is sampled after writes,
  so sessions that expire during collection are excluded.
- The notifier retains its documented crash window after claiming an alert;
  this wiring does not change deduplication or baseline-update behavior.
- Infrastructure provisioning, API routes and session creation are separate work.
  Tests use Moto for DynamoDB/SQS/SES and mock HTTP; no real resources or emails
  are created or sent by verification.

## Local verification

See [services/README.md](../../services/README.md) for the isolated environment and
test commands. Tests cover fixture parsing, partial failures, invalid/future/stale
data, cache hits, exact postal matching, DynamoDB serialization and pagination,
previous scores, failed refresh retention, and the complete scheduled ingestion
to SQS to notifier/SES path. They also check that an unchanged area score can
alert against a session baseline, that skipped areas do not alert from old data,
and that failed writes/configuration do not queue messages.

## Review focus

- Clock comparisons must use timezone-aware timestamps; stale/future data cannot
  influence scores.
- Source readings must be matched by station ID, not by array position.
- API/cache reads and writes must follow Isaac's DynamoDB item shapes, including
  Decimal conversion, without introducing infrastructure creation into handlers.
- Repeated refreshes must keep prior scores and preserve good snapshots on total
  upstream failure.
- Tests must use fake AWS credentials and HTTP transports, with no real AWS writes
  or email sends.
