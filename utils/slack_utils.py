from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from utils.mail_utils import clean_optional, config_bool, config_int


SLACK_API_BASE_URL = "https://slack.com/api"
THREAD_TS_PATTERN = re.compile(r"^\d{10,}\.\d{6}$")
THREAD_TS_PLACEHOLDERS = {"parent-message-ts", "parent_message_ts"}


class SlackApiError(Exception):
    """Raised when Slack rejects or cannot complete a report upload."""


@dataclass(frozen=True)
class SlackSettings:
    token: str
    channel_id: str
    title: str
    initial_comment: str
    thread_ts: str | None
    upload_strict: bool
    timeout: int


def build_slack_settings(
    config: Mapping[str, str],
    execution_date: str,
) -> SlackSettings:
    token = clean_optional(config.get("SLACK_BOT_TOKEN"))
    channel_id = clean_optional(config.get("SLACK_CHANNEL_ID"))
    title = clean_optional(config.get("SLACK_REPORT_TITLE"))
    initial_comment = clean_optional(config.get("SLACK_REPORT_INITIAL_COMMENT"))
    thread_ts = normalize_thread_ts(config.get("SLACK_REPORT_THREAD_TS"))

    errors: list[str] = []
    if not token:
        errors.append("SLACK_BOT_TOKEN is required.")
    if not channel_id:
        errors.append("SLACK_CHANNEL_ID is required.")
    if thread_ts and not THREAD_TS_PATTERN.fullmatch(thread_ts):
        errors.append("SLACK_REPORT_THREAD_TS must be a parent message timestamp.")

    try:
        upload_strict = config_bool(config, "SLACK_REPORT_UPLOAD_STRICT", True)
    except ValueError as exc:
        errors.append(str(exc))
        upload_strict = True

    try:
        timeout = config_int(config, "SLACK_REPORT_TIMEOUT_SECONDS", 30)
    except ValueError as exc:
        errors.append(str(exc))
        timeout = 30
    if timeout <= 0:
        errors.append("SLACK_REPORT_TIMEOUT_SECONDS must be greater than 0.")

    if errors:
        raise ValueError(" ".join(errors))

    default_title = f"{execution_date} trade report"
    return SlackSettings(
        token=token,
        channel_id=channel_id,
        title=title or default_title,
        initial_comment=initial_comment or default_title,
        thread_ts=thread_ts,
        upload_strict=upload_strict,
        timeout=timeout,
    )


def normalize_thread_ts(value: str | None) -> str | None:
    thread_ts = clean_optional(value)
    if not thread_ts:
        return None
    if thread_ts.lower() in THREAD_TS_PLACEHOLDERS:
        return None
    return thread_ts


def send_file_via_slack(
    output_path: Path,
    slack_settings: SlackSettings,
) -> dict[str, Any]:
    if not output_path.exists():
        raise FileNotFoundError(f"Report file not found: {output_path}")

    file_bytes = output_path.read_bytes()
    upload_ticket = slack_api_request(
        "files.getUploadURLExternal",
        slack_settings.token,
        {
            "filename": output_path.name,
            "length": len(file_bytes),
        },
        slack_settings.timeout,
    )
    upload_url = clean_optional(upload_ticket.get("upload_url"))
    file_id = clean_optional(upload_ticket.get("file_id"))
    if not upload_url or not file_id:
        raise SlackApiError("Slack upload ticket did not include upload_url and file_id.")

    upload_file_bytes(
        upload_url,
        output_path,
        file_bytes,
        slack_settings.timeout,
    )

    complete_payload: dict[str, Any] = {
        "files": [{"id": file_id, "title": slack_settings.title}],
        "channel_id": slack_settings.channel_id,
    }
    if slack_settings.initial_comment:
        complete_payload["initial_comment"] = slack_settings.initial_comment
    if slack_settings.thread_ts:
        complete_payload["thread_ts"] = slack_settings.thread_ts

    return slack_api_request(
        "files.completeUploadExternal",
        slack_settings.token,
        complete_payload,
        slack_settings.timeout,
    )


def slack_api_request(
    method: str,
    token: str,
    payload: Mapping[str, Any],
    timeout: int,
) -> dict[str, Any]:
    request = Request(
        f"{SLACK_API_BASE_URL}/{method}",
        data=encode_form_payload(payload),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = read_error_body(exc)
        raise SlackApiError(
            f"Slack API {method} failed with HTTP {exc.code}: {shorten(body)}"
        ) from exc
    except URLError as exc:
        raise SlackApiError(f"Slack API {method} request failed: {exc.reason}") from exc

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SlackApiError(
            f"Slack API {method} returned invalid JSON: {shorten(body)}"
        ) from exc

    if not isinstance(decoded, dict):
        raise SlackApiError(f"Slack API {method} returned an unexpected response.")
    if not decoded.get("ok"):
        raise SlackApiError(
            f"Slack API {method} returned error: {slack_error_details(decoded)}"
        )
    return decoded


def encode_form_payload(payload: Mapping[str, Any]) -> bytes:
    encoded: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            encoded[key] = json.dumps(value, separators=(",", ":"))
        else:
            encoded[key] = str(value)
    return urlencode(encoded).encode("utf-8")


def slack_error_details(response: Mapping[str, Any]) -> str:
    error = str(response.get("error") or "unknown_error")
    details: list[str] = []
    response_metadata = response.get("response_metadata")
    if isinstance(response_metadata, dict):
        messages = response_metadata.get("messages")
        if isinstance(messages, list):
            details.extend(str(message) for message in messages if message)
    for key in ("needed", "provided"):
        value = response.get(key)
        if value:
            details.append(f"{key}={value}")
    if not details:
        return error
    return f"{error} ({'; '.join(details)})"


def upload_file_bytes(
    upload_url: str,
    output_path: Path,
    file_bytes: bytes,
    timeout: int,
) -> None:
    content_type, _ = mimetypes.guess_type(output_path.name)
    if content_type is None:
        content_type = "application/octet-stream"
    request = Request(
        upload_url,
        data=file_bytes,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(file_bytes)),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read()
    except HTTPError as exc:
        body = read_error_body(exc)
        raise SlackApiError(
            f"Slack file upload failed with HTTP {exc.code}: {shorten(body)}"
        ) from exc
    except URLError as exc:
        raise SlackApiError(f"Slack file upload request failed: {exc.reason}") from exc
    if status != 200:
        raise SlackApiError(
            f"Slack file upload failed with HTTP {status}: {shorten(body)}"
        )


def read_error_body(error: HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def shorten(value: bytes | str, max_length: int = 300) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    clean = value.strip()
    if len(clean) <= max_length:
        return clean
    return f"{clean[:max_length]}..."
