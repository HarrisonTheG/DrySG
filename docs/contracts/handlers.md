# Lambda Handlers (contract for IaC + backend)

Runtime: **Python 3.12**. Code root: `services/`. One deployment package (or layer) contains `drysg/` and `handlers/`. Region: `ap-southeast-1`.

| Lambda | Handler | Trigger | Suggested timeout / memory |
|---|---|---|---|
| API | `handlers.api.handler` | API Gateway HTTP API (proxy, all routes) | 10 s / 512 MB |
| Ingest | `handlers.ingest.handler` | EventBridge schedule `rate(5 minutes)` | 60 s / 512 MB |
| Notifier | `handlers.notifier.handler` | SQS `drysg-alerts` (batch size 10, `ReportBatchItemFailures` enabled) | 30 s / 256 MB |

The ingest Lambda runs the fixed NEA fetch, writes snapshots and then calls `drysg.alerting.trigger.run_alert_trigger_from_env(snapshots, now)` in the same invocation, so the number of NEA calls stays constant no matter how many subscribers there are. Set ingest **reserved concurrency = 1** so overlapping runs can't double-alert.

## Environment variables
| Name | Used by | Example |
|---|---|---|
| `TABLE_AREA_SNAPSHOT` | API, Ingest | `drysg-dev-AreaSnapshot` |
| `TABLE_POSTAL_CACHE` | API | `drysg-dev-PostalCache` |
| `TABLE_USER` | API | `drysg-dev-User` |
| `TABLE_MONITORING_SESSION` | API, Ingest, Notifier | `drysg-dev-MonitoringSession` |
| `TABLE_ALERT_LOG` | Notifier | `drysg-dev-AlertLog` |
| `ALERT_QUEUE_URL` | Ingest | SQS queue URL |
| `SES_SENDER` | API (verification mail), Notifier | `alerts@drysg.example` (must be an SES verified identity) |
| `APP_BASE_URL` | API, Notifier | CloudFront URL, used in verify and stop links |
| `NEA_API_KEY` | Ingest, API (fallback) | Optional. NEA v2 currently works without a key; set one if rate limited |
| `ONEMAP_TOKEN` | API | OneMap API token. Search works without one today but returns an "Authentication token missing" warning |
| `LOG_LEVEL` | all | `INFO` |

## SQS
- **`drysg-alerts`**: standard queue. Visibility timeout ≥ 6× the notifier timeout (180 s). Redrive to `drysg-alerts-dlq` after `maxReceiveCount = 3`.
- **Message body**: JSON of `drysg.core.models.AlertMessage`:

```json
{
  "alert_id": "6f1c...-uuid#2026-10-04T14:05:00+08:00",
  "session_id": "6f1c2b8e-3a4d-4e5f-9a7b-1c2d3e4f5a6b",
  "email": "user@example.com",
  "area": "Hougang",
  "prev_score": 78,
  "prev_band": "Hang Now",
  "new_score": 32,
  "new_band": "Do Not Hang",
  "new_veto": true,
  "reason": "Rain detected at nearby station",
  "snapshot_ts": "2026-10-04T14:05:00+08:00"
}
```

Delivery is at-least-once. The notifier dedupes on `alert_id` with a conditional put to `AlertLog`. If SES fails, the notifier reports the message in `batchItemFailures`, so only that message is retried.

## Alert email links
The "stop monitoring" link in alert emails is `{APP_BASE_URL}/stop?session={session_id}`. **The frontend needs a `/stop` route** that calls `DELETE /monitoring/{session_id}`.

## IAM (least privilege per function)
| Lambda | Permissions |
|---|---|
| API | `dynamodb:GetItem/PutItem/UpdateItem/Query` on User, MonitoringSession (+ `email-index`), PostalCache; `GetItem/PutItem` on AreaSnapshot (fallback write); `ses:SendEmail` from `SES_SENDER` |
| Ingest | `dynamodb:BatchGetItem/BatchWriteItem/GetItem/PutItem` on AreaSnapshot; `dynamodb:Query` on MonitoringSession `area-status-index`; `sqs:SendMessage` on `drysg-alerts` |
| Notifier | `sqs:ReceiveMessage/DeleteMessage/GetQueueAttributes` on `drysg-alerts`; `dynamodb:PutItem/UpdateItem` on AlertLog; `dynamodb:UpdateItem` on MonitoringSession; `ses:SendEmail` from `SES_SENDER` |

All three also need CloudWatch Logs (`AWSLambdaBasicExecutionRole`). Outbound internet for NEA and OneMap means the Lambdas should **not** be placed in a VPC (or need a NAT if they are).

## External endpoints called
| Caller | URL |
|---|---|
| Ingest | `https://api-open.data.gov.sg/v2/real-time/api/{rainfall,two-hr-forecast,relative-humidity,air-temperature,wind-speed,psi}` |
| API | `https://www.onemap.gov.sg/api/common/elastic/search?searchVal={postal}&returnGeom=Y&getAddrDetails=Y&pageNum=1` |
