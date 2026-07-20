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
from utils.report_utils import build_report_data, has_production_orders
from utils.slack_utils import (
    build_slack_settings,
    send_file_via_slack,
    send_message_via_slack,
)
from utils.s3_utils import (
    build_local_bot_artifacts,
    build_s3_client,
    candidate_artifact_prefixes,
    download_bot_artifacts,
    resolve_artifact_prefixes,
    validate_s3_credentials,
)
from utils.upstox_utils import UpstoxOrderClient, build_upstox_settings

DEFAULT_BOT_TIMEOUT_SECONDS = 120
REPORT_ARTIFACT_KINDS = ("production", "mock")
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

    artifact_prefixes: dict[str, dict[str, str]] = {
        artifact_kind: {} for artifact_kind in REPORT_ARTIFACT_KINDS
    }
    missing_artifacts: dict[str, list[str]] = {}
    for bot in bots:
        resolved_by_kind = resolve_artifact_prefixes(
            client,
            bucket,
            holder_prefix,
            bot,
            report_date,
        )
        if not resolved_by_kind:
            missing_artifacts[bot] = [
                f"s3://{bucket}/{prefix}"
                for prefix in candidate_artifact_prefixes(holder_prefix, bot, report_date)
            ]
            continue
        for artifact_kind, resolved in resolved_by_kind.items():
            artifact_prefixes[artifact_kind][bot] = resolved.base_prefix

    if missing_artifacts:
        for bot, prefixes in missing_artifacts.items():
            logger.warning(
                "Skipping %s because mock/production artifacts were not found; checked %s",
                bot,
                ", ".join(prefixes),
            )
    order_detail_fetchers: dict[
        str,
        Callable[[str], dict[str, object]] | None,
    ] = {artifact_kind: None for artifact_kind in REPORT_ARTIFACT_KINDS}
    if artifact_prefixes["mock"]:
        logger.info("Mock artifacts detected; Upstox lookup disabled for mock report.")

    report_data_by_kind: dict[
        str,
        dict[str, tuple[list[dict[str, object]], str]],
    ] = {artifact_kind: {} for artifact_kind in REPORT_ARTIFACT_KINDS}
    for artifact_kind in REPORT_ARTIFACT_KINDS:
        for bot in bots:
            if bot not in artifact_prefixes[artifact_kind]:
                continue
            base_prefix = artifact_prefixes[artifact_kind][bot]
            logger.info(
                "Downloading %s %s artifacts for %s.",
                bot,
                artifact_kind,
                report_date.output,
            )
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
                        artifact_kind,
                    ),
                )
            except BotProcessingTimeout:
                artifacts = build_local_bot_artifacts(
                    bot,
                    base_prefix,
                    report_date,
                    args.download_dir,
                    artifact_kind,
                )
                if artifacts.downloaded_artifact_files == 0:
                    logger.warning(
                        "Skipping %s %s because download exceeded %d seconds and no "
                        "local files were available.",
                        bot,
                        artifact_kind,
                        bot_timeout_seconds,
                    )
                    continue
                logger.warning(
                    "%s %s download exceeded %d seconds; using %d local file(s) "
                    "from %s.",
                    bot,
                    artifact_kind,
                    bot_timeout_seconds,
                    artifacts.downloaded_artifact_files,
                    artifacts.local_dir,
                )
            if artifact_kind == "production" and not has_production_orders(
                artifacts,
                report_date.value,
            ):
                rows, observation = [], ""
            else:
                if (
                    artifact_kind == "production"
                    and order_detail_fetchers[artifact_kind] is None
                ):
                    try:
                        upstox_settings = build_upstox_settings(credentials)
                    except ValueError as exc:
                        logger.error(
                            "Upstox configuration validation failed: %s",
                            exc,
                        )
                        return 1
                    order_detail_fetchers[artifact_kind] = UpstoxOrderClient(
                        upstox_settings
                    ).get_order_details
                    logger.info(
                        "Upstox order-details lookup enabled for production report."
                    )
                artifacts, rows, observation = process_bot_report(
                    artifacts,
                    report_date,
                    order_detail_fetchers[artifact_kind],
                )
            logger.info(
                "%s %s: %d report rows, %d downloaded files.",
                bot,
                artifact_kind,
                len(rows),
                artifacts.downloaded_artifact_files,
            )
            for warning in artifacts.warnings or []:
                logger.warning("%s %s: %s", bot, artifact_kind, warning)
            if not rows:
                logger.info(
                    "Omitting %s from the %s report because no orders were found.",
                    bot,
                    artifact_kind,
                )
                continue
            report_data_by_kind[artifact_kind][bot] = (rows, observation)

    output_paths: dict[str, Path] = {}
    for artifact_kind in REPORT_ARTIFACT_KINDS:
        report_data = report_data_by_kind[artifact_kind]
        if not report_data:
            logger.info("No orders found for %s.", artifact_kind.capitalize())
            continue
        output_path = args.output_dir / (
            f"{report_date.output}_{artifact_kind}_trade_report.xlsx"
        )
        report_bots = [bot for bot in bots if bot in report_data]
        write_report(args.template, output_path, report_bots, report_data)
        output_paths[artifact_kind] = output_path
        logger.info("Wrote %s.", output_path)

    delivered = False
    if slack_settings:
        for artifact_kind in REPORT_ARTIFACT_KINDS:
            output_path = output_paths.get(artifact_kind)
            no_orders_message = (
                f"No orders found for {artifact_kind.capitalize()}."
            )
            try:
                if output_path is None:
                    send_message_via_slack(no_orders_message, slack_settings)
                else:
                    send_file_via_slack(output_path, slack_settings)
            except Exception as exc:
                delivery_target = output_path or no_orders_message
                if slack_settings.upload_strict:
                    logger.error(
                        "Slack delivery failed for %s: %s",
                        delivery_target,
                        exc,
                    )
                    return 1
                logger.warning(
                    "Slack delivery failed for %s: %s",
                    delivery_target,
                    exc,
                )
            else:
                delivered = True
                if output_path is None:
                    logger.info(
                        "Sent '%s' to Slack channel %s.",
                        no_orders_message,
                        slack_settings.channel_id,
                    )
                else:
                    logger.info(
                        "Uploaded %s to Slack channel %s.",
                        output_path,
                        slack_settings.channel_id,
                    )

    if mail_settings:
        for output_path in output_paths.values():
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
        for output_path in output_paths.values():
            logger.info("Report available at %s.", output_path)

    return 0
