# Infrastructure

AWS infrastructure for DrySG, managed with Terraform. Everything runs in **ap-southeast-1 (Singapore)**. Resource names are prefixed `drysg-<env>-` (default env: `dev`).

## What's deployed

```
EventBridge rule (every 5 min)
      │
      ▼
Ingest Lambda ──writes──▶ AreaSnapshot table (latest weather + score, ~47 areas)
      │
      │ active sessions whose area got worse
      ▼
SQS drysg-dev-alerts ──(3 failed tries)──▶ drysg-dev-alerts-dlq
      │
      ▼
Notifier Lambda ──▶ SES email to the user, logged in AlertLog (dedupe)
```

| File | What it creates |
|---|---|
| `dynamodb.tf` | 5 tables from [`docs/contracts/dynamodb.md`](../docs/contracts/dynamodb.md) |
| `sqs.tf` | Alerts queue + dead-letter queue |
| `lambda_ingest.tf` | Ingest Lambda, its IAM role, 5-minute EventBridge schedule |
| `lambda_notifier.tf` | Notifier Lambda, its IAM role, SQS trigger |
| `ses.tf` | Verified sender email for alerts |
| `lambda_api.tf` | API Lambda and its IAM role |
| `api_gateway.tf` | HTTP API in front of the API Lambda |

Lambda settings (handlers, timeouts, env vars, IAM) follow [`docs/contracts/handlers.md`](../docs/contracts/handlers.md).

**Not yet deployed:** the API Lambda + API Gateway (waiting on `services/handlers/api.py`) and web hosting. Until the API exists, no real monitoring sessions are created, so no real alerts are sent.

## Prerequisites
- [AWS CLI v2](https://aws.amazon.com/cli/) and [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.11
- Python 3.12+ (to build the Lambda package)
- Access to the DrySG AWS account through SSO (ask the infra owner)

## One-time setup

### 1. AWS login profile
```
aws configure sso
```
Enter the SSO start URL and SSO region from the AWS access portal ("Access keys"), and name the profile `drysg`.

SSO sessions expire after a few hours. When commands fail with an expired-token error, log in again:
```
aws sso login --profile drysg
```

### 2. Terraform settings file
```
cd infra
copy terraform.tfvars.example terraform.tfvars      # macOS/Linux: cp
```
Set `ses_sender` to the **same address the team already uses** (ask the infra owner). `terraform.tfvars` is gitignored. Using a different address would replace the shared SES identity for everyone.

### 3. Initialise Terraform
```
$env:AWS_PROFILE="drysg"        # macOS/Linux: export AWS_PROFILE=drysg
aws sts get-caller-identity     # confirms you're logged in to the right account
terraform init
```
Terraform state is stored in the S3 bucket `drysg-tfstate-637107289229`, so everyone shares the same state.

## Deploying

Run from the repo root, whenever `services/` or `infra/` changes:
```
python scripts/build_lambda.py     # builds build/lambda.zip (Linux packages for Lambda)
cd infra
terraform plan                     # review: what will be created / changed / destroyed
terraform apply
```
- Always rebuild the zip before applying. If you forget, Terraform deploys the old code without any error.
- Read the `plan` output before `apply`, especially anything marked **destroy**.

## Testing

### Ingest (weather refresh)
Run it once by hand:
```
aws lambda invoke --function-name drysg-dev-ingest out.json
cat out.json
```
Expect `"updated_areas": 47` and `"missing_feeds": []`. To check the table:
```
aws dynamodb scan --table-name drysg-dev-AreaSnapshot --query "Items[].[area.S, updated_at.S]" --output table
```
Run the scan again after 5 minutes. The `updated_at` timestamps should have moved forward. You can also browse the tables in the AWS Console: **DynamoDB → Explore items** (region: Singapore).

To see the scheduled runs:
```
aws logs tail /aws/lambda/drysg-dev-ingest --since 15m
```

### Alerts (queue → notifier → email)
`scripts/smoke_test_alert.py` creates a temporary monitoring session, queues a fake alert, waits for the notifier, and then deletes the test data:
```
services\.venv\Scripts\python.exe scripts\smoke_test_alert.py --email <your verified email>
```
(Set up `services/.venv` as described in [`services/README.md`](../services/README.md). On macOS/Linux use `services/.venv/bin/python`.)

`--email` is who **receives** the test alert. It's not the sender (`ses_sender`). In sandbox, **both** must be verified in SES, so the easiest choice is the `ses_sender` address itself. To receive at a different address, verify that address first (see [Email (SES) sandbox](#email-ses-sandbox)).

A `PASS` means SES accepted the email. Check the inbox **and junk folder**. For a `FAIL`:
```
aws logs tail /aws/lambda/drysg-dev-notifier --since 10m
```

Failed messages end up in the dead-letter queue. It should normally be empty:
```
aws sqs get-queue-attributes --queue-url (terraform output -raw alert_dlq_url) --attribute-names ApproximateNumberOfMessages
```

## Email (SES) sandbox
SES starts in **sandbox mode**:
- It only delivers to **verified** addresses, at about 200 emails/day. Sandbox checks **both** the sender and every recipient.
- If the notifier log shows `MessageRejected: Email address is not verified`, the address it names isn't verified. It's usually the recipient.
- To receive test alerts, verify your address: AWS Console → **SES → Identities → Create identity → Email address**, then click the link AWS emails you (check junk).
- Before real users sign up, request production access in the SES console.

Mail sent "from" a personal address (Hotmail, Gmail, university) through SES may land in junk. A custom domain verified in SES fixes this.

## Cost and pausing
At about $1/month this setup costs very little. Ingest runs 288 times/day, and the tables are on-demand, so they cost nothing when idle. To pause the 5-minute schedule without changing code:
```
terraform apply -var ingest_schedule_enabled=false
```
Run a normal `terraform apply` to turn it back on.

Avoid adding anything that bills while idle (NAT Gateway, RDS, EC2). The Lambdas are intentionally **not** in a VPC for this reason.

## Variables
| Variable | Default | Purpose |
|---|---|---|
| `env` | `dev` | Name prefix (`drysg-<env>-...`) |
| `ses_sender` | (required, in `terraform.tfvars`) | From address for alert emails |
| `app_base_url` | `http://localhost:5173` | Website URL used in email links. Change it once the web app is hosted |
| `ingest_schedule_enabled` | `true` | Turns the 5-minute schedule on or off |
| `ingest_reserved_concurrency` | `1` | Prevents overlapping ingest runs. Use `-1` if the account's concurrency limit is only 10 |

## One-time account bootstrap (already done)
The state bucket already exists. These commands are kept for reference only. Don't run them again.
```
aws s3api create-bucket --bucket drysg-tfstate-637107289229 --region ap-southeast-1 --create-bucket-configuration LocationConstraint=ap-southeast-1
aws s3api put-bucket-versioning --bucket drysg-tfstate-637107289229 --versioning-configuration Status=Enabled
```
