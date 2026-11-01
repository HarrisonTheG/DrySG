# DynamoDB Tables (contract for IaC + backend)

All tables use **on-demand (PAY_PER_REQUEST)** billing. Table names are passed to Lambdas as env vars (see `handlers.md`) so environments can be prefixed, e.g. `drysg-dev-AreaSnapshot`.

DynamoDB does not accept Python `float`. Repositories must convert numbers to `Decimal` on write and back on read. Timestamps are stored as ISO 8601 strings, except TTL attributes, which must be **epoch seconds (Number)**.

## AreaSnapshot
The latest fused conditions and score per NEA forecast area. The ingest Lambda writes it every 5 minutes, and the API reads it.

| Attribute | Type | Notes |
|---|---|---|
| `area` (PK) | S | NEA forecast area name, e.g. `Bishan` (~47 items total) |
| `region` | S | PSI region |
| `latitude`, `longitude` | N | Area label location |
| `conditions` | M | Serialised `Conditions` model |
| `result` | M | Serialised `ScoreResult` model |
| `prev_score` | N | Score from the previous ingest run |
| `prev_band` | S | Band from the previous ingest run |
| `updated_at` | S | ISO timestamp |

The item shape is `drysg.core.models.AreaSnapshot.model_dump(mode="json")`, with numbers converted to Decimal.

## PostalCache
Caches OneMap lookups so repeat postal codes don't call OneMap.

| Attribute | Type | Notes |
|---|---|---|
| `postal_code` (PK) | S | 6 digits |
| `latitude`, `longitude` | N | From OneMap |
| `address` | S | OneMap `ADDRESS`, for display |
| `area` | S | Nearest forecast area |
| `cached_at` | S | ISO timestamp |

No TTL. Postal codes rarely move, and the nearest area can be recomputed if area metadata changes.

## User
| Attribute | Type | Notes |
|---|---|---|
| `email` (PK) | S | Lower-cased |
| `verified` | BOOL | |
| `token_hash` | S | SHA-256 (hex) of the verification token. The raw token is never stored. Removed once verified |
| `token_expires_at` | S | UTC ISO timestamp, 24 h after issue. Removed once verified |
| `token_sent_at` | S | UTC ISO timestamp of the last verification email; a new one is refused within 60 s. Removed once verified |
| `created_at`, `verified_at` | S | ISO timestamps |
| `active_session_id` | S | Optional pointer to the latest API-created session. Updated transactionally with creating the new session and stopping the previous active one. It may point to a stopped or TTL-deleted session; the next start replaces it |

## MonitoringSession
| Attribute | Type | Notes |
|---|---|---|
| `session_id` (PK) | S | UUID4, which doubles as the bearer secret for stop links |
| `email` | S | |
| `postal_code` | S | |
| `area` | S | Copied from PostalCache at start |
| `status` | S | `active` \| `stopped` \| `expired` |
| `started_at`, `stopped_at` | S | ISO timestamps |
| `baseline_score` | N | Score the user was last told about. **The API sets it from the area snapshot at session start**, the notifier moves it down after each alert, and the alert trigger moves it up once the band has been better for two runs |
| `baseline_band` | S | Band matching `baseline_score` |
| `baseline_veto` | BOOL | Whether the baseline state was a rain veto |
| `baseline_method` | S | `result.method` of the baseline snapshot, e.g. `dp-percentile-2024`. Score and band alerts only fire when it matches the new snapshot's method; a rain veto alerts either way |
| `last_alert_at` | S | ISO timestamp, used for cooldown |
| `last_alert_band` | S | Band at the last alert, so an escalation to Do Not Hang can bypass the cooldown |
| `expires_at` | **N** | Epoch seconds, **TTL attribute** (proposed: started_at + 12 h) |

The item shape is `drysg.db.items.session_to_item(MonitoringSession)` / `session_from_item`.

Alerts compare new conditions with the session's **baseline**, not with the previous 5-minute snapshot. This catches slow declines and prevents repeat alerts when a score hovers around a threshold. Once the band has been better than the baseline for two runs in a row (e.g. rain stopped), the trigger raises the baseline to the new state, so the next decline or rain alerts again. Rain returning after a single dry run also alerts, at most every 30 minutes. The rules are in `drysg/core/alerts.py`.

**GSI `area-status-index`**: PK `area`, SK `status`, projection ALL. The alert trigger queries `area = :a AND status = 'active'` for each deteriorated area.

**GSI `email-index`**: PK `email`, projection KEYS_ONLY. Available for email-based lookup.
The session API uses a consistent read of `User.active_session_id` instead of this
eventually consistent index when replacing a session. A conditional transaction updates
the verified user's pointer, creates the session and stops its active predecessor together.
Concurrent requests re-read and retry up to three times, then return `409 SESSION_CONFLICT`.
Session writers must maintain this pointer to participate in the one-active-session rule;
manually seeded smoke-test rows are not managed by the API replacement flow.

DynamoDB TTL deletion can lag by up to ~48 h, so the alert trigger must also filter `expires_at > now`.

## AlertLog
An idempotency and audit trail for alerts. It is also used in evaluation to count missed and duplicate alerts.

| Attribute | Type | Notes |
|---|---|---|
| `session_id` (PK) | S | |
| `alert_id` (SK) | S | `{session_id}#{snapshot_ts}`. The notifier claims it with a conditional put (`attribute_not_exists(alert_id) OR status = failed`) to dedupe |
| `status` | S | `claimed` → `sent` \| `failed` \| `skipped_inactive` |
| `area` | S | |
| `snapshot_ts`, `claimed_at` | S | ISO timestamps |
| `prev_band`, `new_band` | S | |
| `prev_score`, `new_score` | N | |
| `reason` | S | |
| `sent_at` | S | ISO timestamp |
| `ses_message_id` | S | |
| `ttl` | **N** | Epoch seconds, **TTL attribute** (proposed: 30 days) |

## Access patterns summary
| Who | Operation | Table / index |
|---|---|---|
| Ingest | BatchGet all snapshots (for prev score), BatchWrite all snapshots | AreaSnapshot |
| Alert trigger | Query active sessions by area; conditional Update to raise a session baseline (only if still active and unchanged) | MonitoringSession / `area-status-index` |
| Notifier | Conditional Put + Update alert status; conditional Update of the session baseline (only if still active) | AlertLog, MonitoringSession |
| API `/recommendation` | Get postal → Get snapshot | PostalCache, AreaSnapshot |
| API `/users*` | Get/Put/Update user | User |
| API `/laundry-sessions*` | Consistent Get user/session; transactional Put session + Update user pointer and previous session; conditional Update on stop | User, MonitoringSession |
