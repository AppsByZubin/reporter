from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_RESEND_USER_AGENT = "reporter/1.0"
EMAIL_PATTERN = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
DISPLAY_SENDER_PATTERN = re.compile(
    r"^.+\s<(?P<email>[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+)>$"
)
PUBLIC_RESEND_SENDER_DOMAINS = frozenset(
    {
        "aol.com",
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "rocketmail.com",
        "yahoo.com",
        "ymail.com",
    }
)


@dataclass(frozen=True)
class MailSettings:
    recipients: list[str]
    sender: str
    api_key: str
    subject: str


def parse_email_recipients(raw_values: Sequence[str] | None) -> list[str]:
    recipients: list[str] = []
    for raw_value in raw_values or []:
        if raw_value is None:
            continue
        json_recipients = parse_json_email_recipients(raw_value)
        if json_recipients is not None:
            recipients.extend(parse_email_recipients(json_recipients))
            continue
        for recipient in re.split(r"[,;\n]+", raw_value):
            clean = recipient.strip()
            if clean:
                recipients.append(clean)
    return recipients


def parse_json_email_recipients(raw_value: str) -> list[str] | None:
    text = raw_value.strip()
    if not text.startswith("["):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None
    return parsed


def build_mail_settings(
    config: Mapping[str, str],
    execution_date: str,
) -> MailSettings:
    recipients = parse_email_recipients([config.get("EMAIL_TO", "")])
    sender = clean_optional(config.get("EMAIL_FROM"))
    api_key = clean_optional(config.get("RESEND_API_KEY"))

    errors: list[str] = []
    if not recipients:
        errors.append("EMAIL_TO must include at least one recipient.")
    invalid_recipients = [
        recipient for recipient in recipients if not is_email(recipient)
    ]
    if invalid_recipients:
        errors.append(
            f"EMAIL_TO contains invalid recipients: {', '.join(invalid_recipients)}."
        )
    if not sender:
        errors.append("EMAIL_FROM is required.")
    elif not is_valid_sender(sender):
        errors.append(
            "EMAIL_FROM must be an email address or display sender like "
            "'Reports <reports@yourdomain.com>'."
        )
    else:
        domain = sender_domain(sender)
        if domain in PUBLIC_RESEND_SENDER_DOMAINS:
            errors.append(
                f"EMAIL_FROM uses {domain}, but Resend requires a verified sender "
                "domain. Use an address from a domain verified in Resend, such as "
                "'Reports <reports@yourdomain.com>'."
            )
    if not api_key:
        errors.append("RESEND_API_KEY is required.")

    if errors:
        raise ValueError(" ".join(errors))

    return MailSettings(
        recipients=recipients,
        sender=sender,
        api_key=api_key,
        subject=build_email_subject(execution_date),
    )


def build_email_subject(execution_date: str) -> str:
    return f"{execution_date} trade report"


def is_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.fullmatch(value))


def is_valid_sender(value: str) -> bool:
    return is_email(value) or bool(DISPLAY_SENDER_PATTERN.fullmatch(value))


def sender_domain(value: str) -> str:
    sender_email = value.strip()
    display_match = DISPLAY_SENDER_PATTERN.fullmatch(sender_email)
    if display_match:
        sender_email = display_match.group("email")
    return sender_email.rsplit("@", 1)[-1].lower()


def send_file_via_email(
    output_path: Path,
    mail_settings: MailSettings,
    config: Mapping[str, str],
) -> None:
    if not output_path.exists():
        raise FileNotFoundError(f"Report file not found: {output_path}")

    payload = build_resend_payload(output_path, mail_settings)
    api_url = clean_optional(config.get("RESEND_API_URL")) or DEFAULT_RESEND_API_URL
    user_agent = (
        clean_optional(config.get("RESEND_USER_AGENT")) or DEFAULT_RESEND_USER_AGENT
    )
    timeout = config_int(config, "RESEND_API_TIMEOUT_SECONDS", 30)
    send_resend_email(api_url, mail_settings.api_key, user_agent, payload, timeout)


def build_resend_payload(
    output_path: Path,
    mail_settings: MailSettings,
) -> dict[str, Any]:
    encoded_attachment = base64.b64encode(output_path.read_bytes()).decode("ascii")
    return {
        "from": mail_settings.sender,
        "to": mail_settings.recipients,
        "subject": mail_settings.subject,
        "text": f"Attached is {output_path.name}.",
        "attachments": [
            {
                "filename": output_path.name,
                "content": encoded_attachment,
            }
        ],
    }


def send_resend_email(
    api_url: str,
    api_key: str,
    user_agent: str,
    payload: Mapping[str, Any],
    timeout: int,
) -> None:
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend API returned HTTP {exc.code}: {detail}") from exc


def config_int(config: Mapping[str, str], name: str, default: int) -> int:
    raw_value = config.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def clean_optional(value: str | None) -> str:
    return value.strip() if value else ""
