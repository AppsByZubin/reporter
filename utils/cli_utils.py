from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import Callable, TypeVar

from common.models import BotArtifacts, ReportDate
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
from utils.slack_utils import build_slack_settings, send_file_via_slack
from utils.s3_utils import (
    build_local_bot_artifacts,
    build_s3_client,
    candidate_artifact_prefixes,
    download_bot_artifacts,
    resolve_artifact_prefix,
    validate_s3_credentials,
)
from utils.upstox_utils import UpstoxOrderClient, build_upstox_settings

DEFAULT_BOT_TIMEOUT_SECONDS = 120
T = TypeVar("T")


class BotProcessingTimeout(TimeoutError):
    """Raised when one bot takes too long to download/build."""


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
        "--slack",
        "--sendslack",
        action="store_true",
        dest="slack",
        help=(
            "Upload the generated report to Slack using SLACK_BOT_TOKEN "
            "and SLACK_CHANNEL_ID."
        ),
    )
    parser.add_argument(
        "--sendmail",
        action="store_true",
        help=(
            "Optionally email the generated report using EMAIL_TO, "
            "EMAIL_FROM, and GMAIL_APP_PASSWORD."
        ),
    )
    parser.add_argument("--holder-prefix", default=DEFAULT_HOLDER_PREFIX)
    return parser.parse_args(argv)


def build_bot_timeout_seconds(config: dict[str, str]) -> int:
    raw_value = config.get(
        "REPORTER_BOT_TIMEOUT_SECONDS",
        str(DEFAULT_BOT_TIMEOUT_SECONDS),
    )
    try:
        timeout_seconds = int(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError("REPORTER_BOT_TIMEOUT_SECONDS must be an integer.") from exc
    if timeout_seconds <= 0:
        raise ValueError("REPORTER_BOT_TIMEOUT_SECONDS must be greater than 0.")
    return timeout_seconds


def run_with_timeout(timeout_seconds: int, callback: Callable[[], T]) -> T:
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return callback()

    def timeout_handler(_signum, _frame) -> None:
        raise BotProcessingTimeout(
            f"processing exceeded {timeout_seconds} seconds"
        )

    try:
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    except (AttributeError, ValueError):
        return callback()

    try:
        return callback()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(
                signal.ITIMER_REAL,
                previous_timer[0],
                previous_timer[1],
            )


def process_bot_report(
    artifacts: BotArtifacts,
    report_date: ReportDate,
    order_detail_fetcher: Callable[[str], dict[str, object]] | None,
) -> tuple[BotArtifacts, list[dict[str, object]], str]:
    rows, observation = build_report_data(
        artifacts,
        report_date.value,
        order_detail_fetcher,
    )
    return artifacts, rows, observation


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logger = create_logger("reporter")
    report_date = parse_execution_date(args.execution_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    credentials = dict(os.environ)
    try:
        bot_timeout_seconds = build_bot_timeout_seconds(credentials)
    except ValueError as exc:
        logger.error("Reporter configuration validation failed: %s", exc)
        return 1

    slack_settings = None
    if args.slack:
        try:
            slack_settings = build_slack_settings(credentials, report_date.output)
        except ValueError as exc:
            logger.error("Slack configuration validation failed: %s", exc)
            return 1
        logger.info(
            "Slack configuration validated for channel %s.",
            slack_settings.channel_id,
        )

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
    if not slack_settings and not mail_settings:
        logger.info("Delivery disabled; report will be written to %s.", args.output_dir)

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

    artifact_prefixes: dict[str, tuple[str, str]] = {}
    missing_artifacts: dict[str, list[str]] = {}
    for bot in bots:
        resolved = resolve_artifact_prefix(
            client,
            bucket,
            holder_prefix,
            bot,
            report_date,
        )
        if resolved is None:
            missing_artifacts[bot] = [
                f"s3://{bucket}/{prefix}"
                for prefix in candidate_artifact_prefixes(holder_prefix, bot, report_date)
            ]
            continue
        artifact_prefixes[bot] = (resolved.base_prefix, resolved.artifact_kind)

    if missing_artifacts:
        for bot, prefixes in missing_artifacts.items():
            logger.warning(
                "Skipping %s because mock/production artifacts were not found; checked %s",
                bot,
                ", ".join(prefixes),
            )
    if not artifact_prefixes:
        logger.error("No report updated because no mock or production artifacts were found.")
        return 1

    artifact_kinds = {
        artifact_kind for _base_prefix, artifact_kind in artifact_prefixes.values()
    }
    if len(artifact_kinds) > 1:
        logger.error(
            "No report updated because mixed artifact modes were found: %s",
            ", ".join(sorted(artifact_kinds)),
        )
        return 1
    artifact_kind = next(iter(artifact_kinds))
    order_detail_fetcher = None
    if artifact_kind == "production":
        try:
            upstox_settings = build_upstox_settings(credentials)
        except ValueError as exc:
            logger.error("Upstox configuration validation failed: %s", exc)
            return 1
        order_detail_fetcher = UpstoxOrderClient(upstox_settings).get_order_details
        logger.info("Upstox order-details lookup enabled for production report.")
    else:
        logger.info("Mock artifacts detected; Upstox lookup disabled.")

    report_data: dict[str, tuple[list[dict[str, object]], str]] = {}
    for bot in bots:
        if bot not in artifact_prefixes:
            continue
        base_prefix, bot_artifact_kind = artifact_prefixes[bot]
        logger.info("Downloading %s artifacts for %s.", bot, report_date.output)
        try:
            artifacts = run_with_timeout(
                bot_timeout_seconds,
                lambda: download_bot_artifacts(
                    client,
                    bucket,
                    holder_prefix,
                    bot,
                    report_date,
                    args.download_dir,
                    base_prefix,
                    bot_artifact_kind,
                ),
            )
        except BotProcessingTimeout:
            artifacts = build_local_bot_artifacts(
                bot,
                base_prefix,
                report_date,
                args.download_dir,
                bot_artifact_kind,
            )
            if artifacts.downloaded_artifact_files == 0:
                logger.warning(
                    "Skipping %s because download exceeded %d seconds and no local "
                    "%s files were available.",
                    bot,
                    bot_timeout_seconds,
                    bot_artifact_kind,
                )
                continue
            logger.warning(
                "%s download exceeded %d seconds; using %d local %s file(s) from %s.",
                bot,
                bot_timeout_seconds,
                artifacts.downloaded_artifact_files,
                bot_artifact_kind,
                artifacts.local_dir,
            )
        artifacts, rows, observation = process_bot_report(
            artifacts,
            report_date,
            order_detail_fetcher,
        )
        report_data[bot] = (rows, observation)
        logger.info(
            "%s: %d report rows, %d %s files.",
            bot,
            len(rows),
            artifacts.downloaded_artifact_files,
            artifacts.artifact_kind,
        )
        for warning in artifacts.warnings or []:
            logger.warning("%s: %s", bot, warning)

    if not report_data:
        logger.error("No report updated because every available bot was skipped.")
        return 1

    output_name = (
        f"{report_date.output}_mock_report.xlsx"
        if artifact_kind == "mock"
        else f"{report_date.output}_report.xlsx"
    )
    output_path = args.output_dir / output_name
    write_report(args.template, output_path, bots, report_data)
    logger.info("Wrote %s.", output_path)

    delivered = False
    if slack_settings:
        try:
            send_file_via_slack(
                output_path,
                slack_settings,
            )
        except Exception as exc:
            if slack_settings.upload_strict:
                logger.error("Slack delivery failed for %s: %s", output_path, exc)
                return 1
            logger.warning("Slack delivery failed for %s: %s", output_path, exc)
        else:
            delivered = True
            logger.info(
                "Uploaded %s to Slack channel %s.",
                output_path,
                slack_settings.channel_id,
            )

    if mail_settings:
        try:
            send_file_via_email(
                output_path,
                mail_settings,
                credentials,
            )
        except Exception as exc:
            logger.error("Email delivery failed for %s: %s", output_path, exc)
            return 1
        logger.info(
            "Emailed %s to %s.",
            output_path,
            ", ".join(mail_settings.recipients),
        )
        delivered = True

    if not delivered:
        logger.info("Report available at %s.", output_path)

    return 0
