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

To email the generated report through Gmail SMTP, set these values in the
environment:

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

Generate and email the report:

```bash
python reporter.py 20260604 --sendmail
```

Without `--sendmail`, the app only writes the report to the output folder:

```bash
python reporter.py 20260604
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

The app reads `files/input/bot.list`, verifies every bot has a `production/`
folder in Spaces, downloads each bot's `production/` folder under
`downloads/<YYYYMMDD>/<bot>/`, and writes:

```text
output/<YYYYMMDD>_report.xlsx
```

The Spaces folder date is resolved by looking for `production/` under `DDMMYY`
first, matching paths like `index-bucket-holder/trades/firebot/040626/`, with
`YYYYMMDD` as a fallback. If any requested bot has no `production/` data, the
app exits without writing the report.

## Code Layout

```text
reporter.py             # Command entry point
common/constants.py     # Shared paths, report columns, template section config
common/models.py        # Shared dataclasses
utils/cli_utils.py      # Argument parsing and workflow orchestration
utils/config_utils.py   # bot.list reader
utils/date_utils.py     # Execution-date parsing
utils/logger.py         # Console and file logging
utils/mail_utils.py     # HTTPS report email delivery
utils/s3_utils.py       # DigitalOcean Spaces/S3 downloads
utils/record_utils.py   # CSV/JSON parsing and report row extraction
utils/log_utils.py      # Log observation extraction
utils/report_utils.py   # Build rows + observation text per bot
utils/excel_utils.py    # Template filling and table resizing
```
