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
| `ses.tf` | Verified sender email for alerts and verification emails |
| `lambda_api.tf` | API Lambda and its IAM role (including SES, for verification emails) |
| `api_gateway.tf` | HTTP API in front of the API Lambda |
| `web_hosting.tf` | S3 bucket for the website files, served through CloudFront |

Lambda settings (handlers, timeouts, env vars, IAM) follow [`docs/contracts/handlers.md`](../docs/contracts/handlers.md).

**Not yet built:** the website itself (`web/` is still a placeholder), and the API's `/users` and `/users/verify` routes. Until users can register and verify, no real monitoring sessions are created, so no real alerts are sent.

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

Also set `onemap_token` (see [Renewing the OneMap token](#renewing-the-onemap-token)). It has no default on purpose: if it were empty, any `apply` would wipe the token from the API Lambda and postal code lookups would fail.

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

## Deploying the website
`terraform apply` creates an empty bucket. Upload the site by hand after each UI change, from the repo root:
```
cd infra
$api = terraform output -raw api_url                 # macOS/Linux: api=$(terraform output -raw api_url)
$bucket = terraform output -raw web_bucket_name      # macOS/Linux: bucket=$(terraform output -raw web_bucket_name)
$cdn = terraform output -raw web_distribution_id     # macOS/Linux: cdn=$(terraform output -raw web_distribution_id)
cd ../web
npm ci
$env:VITE_API_BASE_URL = $api                        # macOS/Linux: export VITE_API_BASE_URL=$api
npm run build
aws s3 sync dist/ s3://$bucket --delete
aws cloudfront create-invalidation --distribution-id $cdn --paths "/*"
```
The site is then at `terraform output web_url`.

- The API address is baked into the build. If it ever changes, build and upload again.
- Without the invalidation, CloudFront can keep serving the old `index.html` for up to a day.
- A new CloudFront address takes a few minutes to start working.
- The API only accepts calls from the CloudFront address, so a UI on `localhost:5173` can't call it from a browser. Run the API locally (`services/scripts/run_api_local.py`) when working on the UI.
- `--delete` removes anything in the bucket that isn't in `dist/`.

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

### API (recommendation)
Call it with a postal code that isn't cached yet:
```
curl "$(terraform output -raw api_url)/recommendation?postalCode=570150"
```
If it returns `503 DATA_UNAVAILABLE`, the OneMap token is probably missing or expired. Check the log:
```
aws logs tail /aws/lambda/drysg-dev-api --since 10m
```

### API (email verification)
Ask for a verification email, using an address verified in SES while in sandbox:
```
curl -X POST "$(terraform output -raw api_url)/users" -H "Content-Type: application/json" -d '{"email":"<your verified email>"}'
```
It returns `202 {"email": ..., "verified": false}` and emails a link to `<website>/verify?email=...&token=...`. A `503 EMAIL_UNAVAILABLE` means SES refused it: check the API log as above (usually `MessageRejected` for an unverified recipient). Without the website's `/verify` page, confirm by hand with the `email` and `token` from the link:
```
curl -X POST "$(terraform output -raw api_url)/users/verify" -H "Content-Type: application/json" -d '{"email":"<email>","token":"<token>"}'
```

## Renewing the OneMap token
The API needs a OneMap token to look up postal codes that aren't cached yet. It expires after about 3 days, and nothing renews it automatically. Until someone does, new postal codes return `503` and cached ones still work.

1. Get a new token from your OneMap account.
2. Put it in `onemap_token` in your `terraform.tfvars`.
3. Run `terraform plan` and check that only `aws_lambda_function.api` changes.
4. Run `terraform apply`.
5. Call `/recommendation` with a postal code you haven't used before (see [Testing](#api-recommendation)).

- Don't change it with `aws lambda update-function-configuration`. That replaces **all** the Lambda's environment variables, so the table names would be lost too.
- The token is stored in the Terraform state and shows in the Lambda console. Don't share screenshots of either.
- If two people apply with different tokens, the last apply wins. Agree who renews it.

## Email (SES) sandbox
SES starts in **sandbox mode**:
- It only delivers to **verified** addresses, at about 200 emails/day. Sandbox checks **both** the sender and every recipient.
- This applies to alert emails (notifier) and verification emails (`POST /users` on the API).
- If the notifier or API log shows `MessageRejected: Email address is not verified`, the address it names isn't verified. It's usually the recipient.
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
| `onemap_token` | (required, in `terraform.tfvars`) | OneMap token for postal code lookups. Expires after about 3 days |
| `app_base_url` | (the CloudFront address) | Website URL used in email links and as the API's allowed origin. Overrides the CloudFront address if set |
| `ingest_schedule_enabled` | `true` | Turns the 5-minute schedule on or off |
| `ingest_reserved_concurrency` | `1` | Prevents overlapping ingest runs. Use `-1` if the account's concurrency limit is only 10 |

## One-time account bootstrap (already done)
The state bucket already exists. These commands are kept for reference only. Don't run them again.
```
aws s3api create-bucket --bucket drysg-tfstate-637107289229 --region ap-southeast-1 --create-bucket-configuration LocationConstraint=ap-southeast-1
aws s3api put-bucket-versioning --bucket drysg-tfstate-637107289229 --versioning-configuration Status=Enabled
```
