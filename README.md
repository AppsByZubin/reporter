# Reporter

Creates the daily Excel report from CloudPe S3 trade artifacts.

## Setup

```bash
conda activate reporter
python -m pip install -r requirements.txt
```

The app reads CloudPe S3 settings from environment variables:

```text
CLOUDPE_S3_REGION
CLOUDPE_S3_ACCESS_KEY_ID
CLOUDPE_S3_SECRET_ACCESS_KEY
CLOUDPE_S3_BUCKET_NAME
CLOUDPE_S3_ENDPOINT_URL
CLOUDPE_S3_PREFIX          # optional; defaults to trades
```

Set these before the reporter runs. Use CloudPe access keys; legacy `DO_S3_*`
settings are no longer read. The client uses Signature V4 and path-style bucket
addressing over the configured endpoint.

The reporter Helm chart uses the same region and bucket as the bots:

```bash
export CLOUDPE_S3_REGION=in-west2
export CLOUDPE_S3_ENDPOINT_URL=https://s3.in-west2.purestore.io
export CLOUDPE_S3_BUCKET_NAME=index-bucket
# Supply CLOUDPE_S3_ACCESS_KEY_ID and CLOUDPE_S3_SECRET_ACCESS_KEY securely.
```

[CloudPe's S3 guide](https://www.cloudpe.com/knowledge-base/accessing-cloudpe-s3-buckets-using-s3cmd/)
shows `in-west3` as an example. Use the endpoint and region where your bucket
actually lives; both are required configuration.

In `infrastructure/helm/reporter`, `values.yaml` supplies the endpoint, region,
and bucket. The existing Kubernetes Secret `reporter-s3-secrets` must contain
`CLOUDPE_S3_ACCESS_KEY_ID` and `CLOUDPE_S3_SECRET_ACCESS_KEY` in the release
namespace before the CronJob runs. The chart requires this Secret by default;
when using another credential injection mechanism, set `s3Secret.enabled=false`
and supply the same environment variables.

This switches artifact storage access to CloudPe; it does not copy historical
objects from DigitalOcean. Existing object keys must be present in CloudPe for
historical reports. Generated workbooks remain local, with optional Slack and
email delivery.

Validate the CloudPe S3 credentials without generating a report:

```bash
python reporter.py --validate-credentials
```

For each bot/date prefix, the reporter looks for both `mock/` and `production/`
folders. A bot can therefore contribute to one or both reports. Mock data is
written to:

```text
output/<YYYYMMDD>_mock_trade_report.xlsx
```

Production data reads order IDs from `production/orders/order_log.json` or
`order_log.csv`, calls Upstox order details for those IDs, and writes:

```text
output/<YYYYMMDD>_production_trade_report.xlsx
```

A bot is included only when that mode contains at least one reportable order for
the requested date. If an entire mode has no orders, no empty workbook is
created. With `--slack`, the reporter posts `No orders found for Mock.` or
`No orders found for Production.` instead.

Production reports require an Upstox access token:

```text
UPSTOX_API_ACCESS_TOKEN    # also accepts upstox_api_access_token
UPSTOX_API_BASE_URL        # optional; defaults to https://api.upstox.com
UPSTOX_ORDER_DETAILS_PATH  # optional; defaults to /v2/order/details
UPSTOX_API_TIMEOUT_SECONDS # optional; defaults to 30
UPSTOX_API_USER_AGENT      # optional; defaults to reporter/1.0
```

Each bot download is allowed 120 seconds before the reporter falls back to files
already present in `downloads/`. If no local files are available, the bot is
skipped and the next configured bot runs. Override the default with:

```text
REPORTER_BOT_TIMEOUT_SECONDS # optional; defaults to 120
```

To upload the generated report to Slack, set these values in the environment:

```text
SLACK_BOT_TOKEN                 # bot token with files:write and chat:write scopes
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

The app reads `files/input/bot.list`, resolves each bot's `mock/` and
`production/` folders in CloudPe S3, downloads artifacts under
`downloads/<YYYYMMDD>/<bot>/`, and can write both:

```text
output/<YYYYMMDD>_production_trade_report.xlsx
output/<YYYYMMDD>_mock_trade_report.xlsx
```

The S3 folder date uses `YYYYMMDD`, matching paths like
`trades/titanbot/20261001/mock/orders/order_log.csv`. The reporter prefers this
format and falls back to older six-digit `DDMMYY` folders within the same prefix.
It does not interpret `YYYYDDMM` folders: those can refer to a different date.
Logs are read from `<mode>/logs/`, with the older bot/date log location as a fallback.

Set `CLOUDPE_S3_PREFIX` to the same prefix used by the bots (`trades` by default),
or override it with `--s3-prefix`. For staging reports and Slack delivery:

```bash
python reporter.py 20261001 --s3-prefix trades-staging --slack
```

This creates `output/20261001_mock_trade_report.xlsx` and/or
`output/20261001_production_trade_report.xlsx` and uploads each generated workbook
to the configured Slack channel. The scheduled reporter already uses `--slack`;
set its `CLOUDPE_S3_PREFIX` environment variable when reading a custom prefix.
For old holder-based archives, use `--holder-prefix index-bucket-holder` explicitly.

If some requested bots have no matching data or no orders for a mode,
they are omitted from that workbook. Mixed mock and production folders are
processed independently in the same run. Production reports extract order IDs
from each bot's production artifacts and use Upstox order details to fill the
final workbook. If one bot download stalls for longer than
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
utils/s3_utils.py       # CloudPe S3 downloads
utils/upstox_utils.py   # Upstox order-details lookup
utils/record_utils.py   # CSV/JSON parsing and report row extraction
utils/log_utils.py      # Log observation extraction
utils/report_utils.py   # Build rows + observation text per bot
utils/excel_utils.py    # Template filling and table resizing
```
