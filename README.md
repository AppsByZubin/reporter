# Reporter

Creates the daily Excel report from DigitalOcean Spaces trade artifacts.

## Setup

```bash
conda activate reporter
python -m pip install -r requirements.txt
```

The app reads DigitalOcean Spaces settings from environment variables:

```text
DO_S3_REGION
DO_S3_ACCESS_KEY_ID
DO_S3_SECRET_ACCESS_KEY
DO_S3_BUCKET_NAME
DO_S3_ENDPOINT_URL
```

Those are normally exported by `/home/amit/scripts/do_s3_bucket.sh` before the
reporter runs.

Validate the DigitalOcean Spaces credentials without generating a report:

```bash
python reporter.py --validate-credentials
```

For each bot/date prefix, the reporter looks for either a `mock/` folder or a
`production/` folder. Mock data is written directly to:

```text
output/<YYYYMMDD>_mock_report.xlsx
```

Production data reads order IDs from `production/orders/order_events.json`, calls
Upstox order details for those IDs, and writes:

```text
output/<YYYYMMDD>_report.xlsx
```

Production reports require an Upstox access token:

```text
UPSTOX_API_ACCESS_TOKEN    # also accepts upstox_api_access_token
UPSTOX_API_BASE_URL        # optional; defaults to https://api.upstox.com
UPSTOX_ORDER_DETAILS_PATH  # optional; defaults to /v2/order/details
UPSTOX_API_TIMEOUT_SECONDS # optional; defaults to 30
```

Each bot download is allowed 120 seconds before the reporter falls back to files
already present in `downloads/`. If no local files are available, the bot is
skipped and the next configured bot runs. Override the default with:

```text
REPORTER_BOT_TIMEOUT_SECONDS # optional; defaults to 120
```

To upload the generated report to Slack, set these values in the environment:

```text
SLACK_BOT_TOKEN                 # bot token with files:write scope
SLACK_CHANNEL_ID                # channel ID where the report should be shared
SLACK_REPORT_INITIAL_COMMENT    # optional; defaults to "<execution_date> trade report"
SLACK_REPORT_THREAD_TS          # optional; parent message ts for threaded uploads
SLACK_REPORT_UPLOAD_STRICT      # optional; defaults to true
SLACK_REPORT_TIMEOUT_SECONDS    # optional; defaults to 30
```

Those values can be sourced from `/home/amit/scripts/slack_reporter.sh` before
the reporter runs. Invite the Slack app to the target channel before uploading.

Slack upload failures fail the command by default. Set
`SLACK_REPORT_UPLOAD_STRICT=false` when the report should still be considered
generated successfully even if Slack is temporarily unavailable.

Generate and upload the report to Slack:

```bash
python reporter.py 20260604 --slack
```

You can still write the report without sending it anywhere:

```bash
python reporter.py 20260604
```

Email delivery remains available as an optional second delivery target. To email
the generated report through Gmail SMTP, set these values in the environment:

```text
EMAIL_TO            # comma, semicolon, newline, or JSON list of recipients
EMAIL_FROM          # Gmail sender address
GMAIL_APP_PASSWORD  # 16-character Gmail app password
```

Gmail defaults to `smtp.gmail.com` on port `587` with TLS, so `SMTP_HOST` is not
required for the usual Gmail setup. The generic SMTP settings are still
supported if you need to override them:

```text
SMTP_HOST
SMTP_PORT            # optional; defaults to 587 with TLS
SMTP_USE_TLS         # optional; defaults to true
SMTP_USE_SSL         # optional; use true for SMTP-over-SSL on port 465
SMTP_FORCE_IPV4      # optional; set true to force IPv4 SMTP sockets
SMTP_TIMEOUT_SECONDS # optional; defaults to 30
```

The email subject is generated automatically as `<execution_date> trade report`,
for example `20260604 trade report`.

Gmail requires an app password instead of the regular account password. The
machine running the script must also be able to reach the SMTP host and port.

## Run

Use today's date:

```bash
python reporter.py
```

Use a specific execution date:

```bash
python reporter.py 20260604
```

Generate and upload the report to Slack:

```bash
python reporter.py 20260604 --slack
```

Generate, upload to Slack, and email the report:

```bash
python reporter.py 20260604 --slack --sendmail
```

## Docker

Build the reporter image locally:

```bash
IMAGE_REPO=docker.io/bizzkpm/reporter
TAG=sha-$(git rev-parse --short HEAD)
docker build -t ${IMAGE_REPO}:${TAG} .
```

Push the image manually:

```bash
docker login
docker push ${IMAGE_REPO}:${TAG}
```

The GitHub Actions workflow in `.github/workflows/dockerhub.yml` builds and
pushes `docker.io/bizzkpm/reporter:sha-<commit>` on pushes to `main`, then
updates `AppsByZubin/infrastructure/helm/reporter/values.yaml` with that tag so
Argo CD can sync the new image.

Configure these GitHub repository secrets in the `reporter` repo:

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
INFRASTRUCTURE_REPO_TOKEN
```

The app reads `files/input/bot.list`, resolves each bot's `mock/` or
`production/` folder in Spaces, downloads artifacts under
`downloads/<YYYYMMDD>/<bot>/`, and writes either:

```text
output/<YYYYMMDD>_report.xlsx
output/<YYYYMMDD>_mock_report.xlsx
```

The Spaces folder date is resolved by looking under `DDMMYY` first, matching
paths like `index-bucket-holder/trades/firebot/040626/`, with `YYYYMMDD` as a
fallback. If some requested bots have no matching data, they are skipped. If no
configured bot has data, or if mock and production folders are mixed in the same
run, the app exits without writing the report. Production reports extract order
IDs from each bot's `production/orders/order_events.json` and use Upstox order
details to fill the final workbook. If one bot download stalls for longer than
`REPORTER_BOT_TIMEOUT_SECONDS`, local files are used when available; otherwise
that bot is skipped and later bots still run.

## Code Layout

```text
reporter.py             # Command entry point
common/constants.py     # Shared paths, report columns, template section config
common/models.py        # Shared dataclasses
utils/cli_utils.py      # Argument parsing and workflow orchestration
utils/config_utils.py   # bot.list reader
utils/date_utils.py     # Execution-date parsing
utils/logger.py         # Console and file logging
utils/mail_utils.py     # SMTP report email delivery
utils/slack_utils.py    # Slack report upload delivery
utils/s3_utils.py       # DigitalOcean Spaces/S3 downloads
utils/upstox_utils.py   # Upstox order-details lookup
utils/record_utils.py   # CSV/JSON parsing and report row extraction
utils/log_utils.py      # Log observation extraction
utils/report_utils.py   # Build rows + observation text per bot
utils/excel_utils.py    # Template filling and table resizing
```
