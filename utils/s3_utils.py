from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from common.models import BotArtifacts, ReportDate


def build_s3_client(credentials: dict[str, str]):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency boto3. Install with: python -m pip install -r requirements.txt"
        ) from exc

    required = [
        "DO_S3_REGION",
        "DO_S3_ACCESS_KEY_ID",
        "DO_S3_SECRET_ACCESS_KEY",
        "DO_S3_BUCKET_NAME",
        "DO_S3_ENDPOINT_URL",
    ]
    missing = [name for name in required if not credentials.get(name)]
    if missing:
        raise ValueError(f"Missing DigitalOcean Spaces settings: {', '.join(missing)}")

    client = boto3.client(
        "s3",
        region_name=credentials["DO_S3_REGION"],
        endpoint_url=credentials["DO_S3_ENDPOINT_URL"],
        aws_access_key_id=credentials["DO_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=credentials["DO_S3_SECRET_ACCESS_KEY"],
    )
    return client, credentials["DO_S3_BUCKET_NAME"]


def validate_s3_credentials(client: Any, bucket: str) -> None:
    client.list_objects_v2(Bucket=bucket, MaxKeys=1)


def prefix_has_objects(client: Any, bucket: str, prefix: str) -> bool:
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return bool(response.get("Contents"))


def candidate_base_prefixes(
    holder_prefix: str,
    bot: str,
    report_date: ReportDate,
) -> list[str]:
    return [
        f"{holder_prefix}/trades/{bot}/{report_date.space_folder}/",
        f"{holder_prefix}/trades/{bot}/{report_date.legacy_space_folder}/",
    ]


def resolve_base_prefix(
    client: Any,
    bucket: str,
    holder_prefix: str,
    bot: str,
    report_date: ReportDate,
) -> str:
    candidates = candidate_base_prefixes(holder_prefix, bot, report_date)
    for prefix in candidates:
        if prefix_has_objects(client, bucket, prefix):
            return prefix
    return candidates[0]


def resolve_production_base_prefix(
    client: Any,
    bucket: str,
    holder_prefix: str,
    bot: str,
    report_date: ReportDate,
) -> str | None:
    for prefix in candidate_base_prefixes(holder_prefix, bot, report_date):
        if prefix_has_objects(client, bucket, f"{prefix}production/"):
            return prefix
    return None


def download_prefix(client: Any, bucket: str, prefix: str, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            relative = key.removeprefix(prefix)
            if not relative:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(target))
            count += 1
    return count


def download_first_existing(
    client: Any,
    bucket: str,
    candidates: Iterable[tuple[str, Path]],
) -> Path | None:
    for key, target in candidates:
        try:
            client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if is_missing_s3_object(exc):
                continue
            raise
        target.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, key, str(target))
        return target
    return None


def is_missing_s3_object(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(error.get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def download_bot_artifacts(
    client: Any,
    bucket: str,
    holder_prefix: str,
    bot: str,
    report_date: ReportDate,
    download_root: Path,
    base_prefix: str | None = None,
) -> BotArtifacts:
    if base_prefix is None:
        base_prefix = resolve_production_base_prefix(
            client, bucket, holder_prefix, bot, report_date
        ) or resolve_base_prefix(client, bucket, holder_prefix, bot, report_date)
    local_dir = download_root / report_date.output / bot
    artifacts = BotArtifacts(bot=bot, base_prefix=base_prefix, local_dir=local_dir)

    production_prefix = f"{base_prefix}production/"
    artifacts.downloaded_production_files = download_prefix(
        client, bucket, production_prefix, local_dir / "production"
    )

    if artifacts.downloaded_production_files == 0:
        artifacts.add_warning(
            f"No production files found at s3://{bucket}/{production_prefix}"
        )

    log_name = f"{report_date.log_prefix}_{bot}.log"
    artifacts.log_file = download_first_existing(
        client,
        bucket,
        [(f"{base_prefix}{log_name}", local_dir / log_name)],
    )
    if artifacts.log_file is None:
        artifacts.add_warning(f"Missing log file: s3://{bucket}/{base_prefix}{log_name}")

    orders_dir = local_dir / "production" / "orders"
    artifacts.order_events_file = first_existing_local(
        [orders_dir / "order_events.json"]
    )
    if artifacts.order_events_file is None:
        artifacts.order_events_file = download_first_existing(
            client,
            bucket,
            [
                (
                    f"{base_prefix}production/orders/order_events.json",
                    orders_dir / "order_events.json",
                )
            ],
        )
    if artifacts.order_events_file is None:
        artifacts.add_warning(
            f"Missing order events file: s3://{bucket}/{base_prefix}production/orders/order_events.json"
        )

    order_log_candidates = [
        orders_dir / "order_log.json",
        orders_dir / "order_log.csv",
    ]
    artifacts.order_log_file = first_existing_local(order_log_candidates)
    if artifacts.order_log_file is None:
        artifacts.order_log_file = download_first_existing(
            client,
            bucket,
            [
                (
                    f"{base_prefix}production/orders/order_log.json",
                    orders_dir / "order_log.json",
                ),
                (
                    f"{base_prefix}production/orders/order_log.csv",
                    orders_dir / "order_log.csv",
                ),
            ],
        )
    if artifacts.order_log_file is None:
        artifacts.add_warning(
            f"Missing order log file: s3://{bucket}/{base_prefix}production/orders/order_log.json "
            f"or order_log.csv"
        )

    return artifacts


def first_existing_local(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists() and path.is_file():
            return path
    return None
