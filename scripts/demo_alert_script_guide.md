# demo_alert.py guide

`scripts/demo_alert.py` sends a demo "bring your laundry in" email without changing any weather data. Use it in a demo, when you want the weather to "deteriorate" on cue.

## What it does
1. Connects to AWS, using pasted credentials (`AWS_ACCESS_KEY_ID` set) or else the `drysg` CLI profile.
2. Picks the session to alert:
   - `--latest`: reads `active_session_id` from your row in `drysg-dev-User`, i.e. the session the UI started. The email and area come from that session in `drysg-dev-MonitoringSession`.
   - `--session-id <id>`: uses that existing session.
   - Neither: writes a fake `demo-<uuid>` session (active for 12 hours) using `--email`, `--postal-code` and `--area`.
3. Builds a fake alert message (previous state: Hang Now, score 78) with the new state chosen by `--kind`.
4. Invokes the `drysg-dev-notifier` Lambda directly, with an SQS-shaped event. The real notifier code then claims the alert in AlertLog, moves the session's baseline to the new state, and sends the SES email.
5. Prints the session id and the notifier result. `batchItemFailures: []` plus `OK` means SES accepted the email.

It skips the weather APIs, the ingest Lambda, SQS and the alert rules in `services/drysg/core/alerts.py`.

| `--kind` | Email |
|---|---|
| `rain` (default) | Rain nearby, bring your laundry in |
| `band` | Conditions dropped from Hang Now to Do Not Hang, no rain |
| `wait` | Drying conditions worsened (Wait band) |

## Prerequisites
- AWS CLI v2 (to check credentials): `aws --version` should say `aws-cli/2.x`.
- Python 3.10 or newer with `venv` (on Ubuntu: `sudo apt install python3-venv`).
- Access to the DrySG AWS account (`637107289229`, region `ap-southeast-1`) with permission to invoke Lambda and read/write DynamoDB.
- For `--latest`: the email is verified and has started monitoring in the UI, and the session is still active.

## Step by step
Run everything in one terminal window, from the repo root.

1. **Create the virtualenv (one time).**
   ```bash
   cd ~/Desktop/drysg
   python3 -m venv .venv-demo
   .venv-demo/bin/pip install boto3
   ```

2. **Paste the AWS credentials (each new terminal, and whenever they expire).**
   In the AWS access portal, click the account, then "Access keys". Copy the "Option 1: environment variables" block and paste it into the terminal. It sets all three values: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN`. Then:
   ```bash
   export AWS_DEFAULT_REGION=ap-southeast-1
   ```

3. **Check you are in the right account.**
   ```bash
   aws sts get-caller-identity
   ```
   `Account` must be `637107289229`. `ExpiredToken` means copy a fresh set of credentials.

4. **Start monitoring in the UI.** Verify your email and start a session for your postal code.

5. **Fire the alert when you want the weather to "deteriorate".**
   ```bash
   .venv-demo/bin/python scripts/demo_alert.py --email you@example.com --latest --kind rain
   ```
   Other examples:
   ```bash
   .venv-demo/bin/python scripts/demo_alert.py --email you@example.com --latest --kind band
   .venv-demo/bin/python scripts/demo_alert.py --session-id <session-id>
   .venv-demo/bin/python scripts/demo_alert.py --email you@example.com --area Bedok   # fake session, no UI needed
   ```

6. **Check the inbox, including Spam.** Mail from the SES sender usually lands in Spam in Gmail. If it didn't arrive:
   ```bash
   aws logs tail /aws/lambda/drysg-dev-notifier --since 5m
   ```

## Options
| Flag | Default | Meaning |
|---|---|---|
| `--email` | `isaaac67@gmail.com` | Recipient. With `--latest`, the user whose session is alerted |
| `--latest` | off | Alert the email's most recent API-started session |
| `--session-id` | none | Alert this existing session |
| `--kind` | `rain` | `rain`, `band` or `wait` |
| `--postal-code`, `--area` | `570150`, `Ang Mo Kio` | Only used for a fake demo session |
| `--env` | `dev` | Resource prefix: `drysg-<env>-...` |
| `--profile` | `drysg` | AWS CLI profile, ignored when `AWS_ACCESS_KEY_ID` is set |

## Behaviour to know
- **Not idempotent.** Each run sends a new email. The `alert_id` includes the current time, so the notifier's duplicate check never matches, and the 60-minute cooldown lives in the ingest trigger, not here. Rerun it as often as you like.
- **One email per run.** After the alert the baseline moves to the new state, so the 5-minute ingest doesn't repeat it. It only alerts again if real conditions get worse than that baseline, subject to the 60-minute cooldown (rain starting and a first drop to Do Not Hang skip it). If real weather is better for two runs in a row, the baseline is quietly raised.
- **Real rain can still alert.** If it really rains in the session's area, the normal pipeline may send its own alert.
- **Stopped sessions are skipped.** If the session was stopped or expired, the notifier sends no email and logs `skipped_inactive`. Start a new session in the UI.
- **Fake sessions are not deleted.** They expire by TTL after 12 hours. AlertLog rows are kept for 30 days.
- **`has no active_session_id`:** the email isn't verified or never started monitoring, or you used a different address than in the UI (the script lowercases it).
- **`AccessDenied`:** your role can't invoke the notifier or read/write the tables. Ask the infra owner.

## Safety
- Never commit or share the pasted credentials. They give access to the role.
- `.venv-demo/` is untracked. Don't commit it. Delete it or add it to `.gitignore`.
