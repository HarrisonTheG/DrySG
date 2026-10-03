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
| `token_hash` | S | SHA-256 of the verification token. The raw token is never stored |
| `token_expires_at` | S | ISO timestamp (proposed: 24 h after issue) |
| `created_at`, `verified_at` | S | ISO timestamps |

## MonitoringSession
| Attribute | Type | Notes |
|---|---|---|
| `session_id` (PK) | S | UUID4, which doubles as the bearer secret for stop links |
| `email` | S | |
| `postal_code` | S | |
| `area` | S | Copied from PostalCache at start |
| `status` | S | `active` \| `stopped` \| `expired` |
| `started_at`, `stopped_at` | S | ISO timestamps |
| `last_alert_at` | S | ISO timestamp, used for cooldown |
| `last_alert_band` | S | Band at the last alert, so a veto can bypass the cooldown |
| `expires_at` | **N** | Epoch seconds, **TTL attribute** (proposed: started_at + 12 h) |

**GSI `area-status-index`**: PK `area`, SK `status`, projection ALL. The alert trigger queries `area = :a AND status = 'active'` for each deteriorated area.

**GSI `email-index`**: PK `email`, projection KEYS_ONLY. Used to stop the previous active session when a user starts a new one.

DynamoDB TTL deletion can lag by up to ~48 h, so the alert trigger must also filter `expires_at > now`.

## AlertLog
An idempotency and audit trail for alerts. It is also used in evaluation to count missed and duplicate alerts.

| Attribute | Type | Notes |
|---|---|---|
| `session_id` (PK) | S | |
| `alert_id` (SK) | S | `{session_id}#{snapshot_ts}`. The notifier does a conditional put (`attribute_not_exists`) to dedupe |
| `area` | S | |
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
| Alert trigger | Query active sessions by area | MonitoringSession / `area-status-index` |
| Notifier | Conditional Put alert, Update session `last_alert_at` | AlertLog, MonitoringSession |
| API `/recommendation` | Get postal → Get snapshot | PostalCache, AreaSnapshot |
| API `/users*` | Get/Put/Update user | User |
| API `/monitoring*` | Put/Get/Update session, Query by email | MonitoringSession, `email-index` |
