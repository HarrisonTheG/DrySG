Utility scripts for database seeding, automated testing, and development helpers.

| Script | Purpose |
|---|---|
| `build_lambda.py` | Builds `build/lambda.zip` (services code + Linux packages) for Terraform to deploy. Run before `terraform apply`. |
| `smoke_test_alert.py` | End-to-end test of the deployed alert pipeline (SQS → notifier → SES). See [infra/README.md](../infra/README.md#testing). |
| `demo_alert.py` | Sends a demo alert email through the notifier Lambda without changing weather data. See [demo_alert_script_guide.md](demo_alert_script_guide.md). |
