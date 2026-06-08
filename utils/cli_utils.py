from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from common.constants import (
    DEFAULT_BOT_LIST,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_HOLDER_PREFIX,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TEMPLATE,
)
from utils.config_utils import read_bot_list
from utils.date_utils import parse_execution_date
from utils.excel_utils import write_report
from utils.logger import create_logger
from utils.mail_utils import build_mail_settings, send_file_via_email
from utils.report_utils import build_report_data
from utils.s3_utils import (
    build_s3_client,
    candidate_base_prefixes,
    download_bot_artifacts,
    resolve_production_base_prefix,
    validate_s3_credentials,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download bot trade artifacts from DigitalOcean Spaces and fill the report template."
    )
    parser.add_argument(
        "execution_date",
        nargs="?",
        help="Execution date as YYYYMMDD. Defaults to today's date.",
    )
    parser.add_argument("--bot-list", type=Path, default=DEFAULT_BOT_LIST)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument(
        "--validate-credentials",
        action="store_true",
        help="Validate DigitalOcean Spaces credentials and exit.",
    )
    parser.add_argument(
        "--sendmail",
        action="store_true",
        help=(
            "Email the generated report using EMAIL_TO, EMAIL_FROM, "
            "and GMAIL_APP_PASSWORD."
        ),
    )
    parser.add_argument("--holder-prefix", default=DEFAULT_HOLDER_PREFIX)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logger = create_logger("reporter")
    report_date = parse_execution_date(args.execution_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    credentials = dict(os.environ)

    mail_settings = None
    if args.sendmail:
        try:
            mail_settings = build_mail_settings(credentials, report_date.output)
        except ValueError as exc:
            logger.error("Mail configuration validation failed: %s", exc)
            return 1
        logger.info(
            "Mail configuration validated for %d recipient(s).",
            len(mail_settings.recipients),
        )
    else:
        logger.info("Mail disabled; report will be written to %s.", args.output_dir)

    try:
        client, bucket = build_s3_client(credentials)
    except Exception as exc:
        if args.validate_credentials:
            logger.error("DigitalOcean Spaces credential validation failed: %s", exc)
            return 1
        raise

    if args.validate_credentials:
        try:
            validate_s3_credentials(client, bucket)
        except Exception as exc:
            logger.error(
                "DigitalOcean Spaces credential validation failed for bucket %s: %s",
                bucket,
                exc,
            )
            return 1
        logger.info("DigitalOcean Spaces credentials validated for bucket %s.", bucket)
        return 0

    bots = read_bot_list(args.bot_list)
    holder_prefix = args.holder_prefix.strip("/")

    base_prefixes: dict[str, str] = {}
    missing_production: dict[str, list[str]] = {}
    for bot in bots:
        base_prefix = resolve_production_base_prefix(
            client,
            bucket,
            holder_prefix,
            bot,
            report_date,
        )
        if base_prefix is None:
            missing_production[bot] = [
                f"s3://{bucket}/{prefix}production/"
                for prefix in candidate_base_prefixes(holder_prefix, bot, report_date)
            ]
            continue
        base_prefixes[bot] = base_prefix

    if missing_production:
        for bot, prefixes in missing_production.items():
            logger.error(
                "Production not found for %s; checked %s",
                bot,
                ", ".join(prefixes),
            )
        logger.error("No report updated because production artifacts are missing.")
        return 1

    report_data: dict[str, tuple[list[dict[str, object]], str]] = {}
    for bot in bots:
        logger.info("Downloading %s artifacts for %s.", bot, report_date.output)
        artifacts = download_bot_artifacts(
            client,
            bucket,
            holder_prefix,
            bot,
            report_date,
            args.download_dir,
            base_prefixes[bot],
        )
        rows, observation = build_report_data(artifacts, report_date.value)
        report_data[bot] = (rows, observation)
        logger.info(
            "%s: %d report rows, %d production files.",
            bot,
            len(rows),
            artifacts.downloaded_production_files,
        )
        for warning in artifacts.warnings or []:
            logger.warning("%s: %s", bot, warning)

    output_path = args.output_dir / f"{report_date.output}_report.xlsx"
    write_report(args.template, output_path, bots, report_data)
    logger.info("Wrote %s.", output_path)

    if mail_settings:
        send_file_via_email(
            output_path,
            mail_settings,
            credentials,
        )
        logger.info(
            "Emailed %s to %s.",
            output_path,
            ", ".join(mail_settings.recipients),
        )
    else:
        logger.info("Report available at %s.", output_path)

    return 0
