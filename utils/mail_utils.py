from __future__ import annotations

import json
import mimetypes
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Mapping, Sequence


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}
DEFAULT_SMTP_HOST = "smtp.gmail.com"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class MailSettings:
    recipients: list[str]
    sender: str
    app_password: str
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
    app_password = normalize_gmail_app_password(config.get("GMAIL_APP_PASSWORD", ""))

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
    elif not is_email(sender):
        errors.append("EMAIL_FROM must be a valid email address.")
    if not app_password:
        errors.append("GMAIL_APP_PASSWORD is required.")
    elif len(app_password) != 16:
        errors.append("GMAIL_APP_PASSWORD must be 16 characters.")

    if errors:
        raise ValueError(" ".join(errors))

    return MailSettings(
        recipients=recipients,
        sender=sender,
        app_password=app_password,
        subject=build_email_subject(execution_date),
    )


def build_email_subject(execution_date: str) -> str:
    return f"{execution_date} trade report"


def normalize_gmail_app_password(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def is_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.fullmatch(value))


def send_file_via_email(
    output_path: Path,
    mail_settings: MailSettings,
    config: Mapping[str, str],
) -> None:
    if not output_path.exists():
        raise FileNotFoundError(f"Report file not found: {output_path}")

    smtp_host = first_config_value(config, ("SMTP_HOST",)) or DEFAULT_SMTP_HOST
    use_ssl = config_bool(config, "SMTP_USE_SSL", False)
    use_tls = config_bool(config, "SMTP_USE_TLS", False if use_ssl else True)
    if use_ssl and use_tls:
        raise ValueError("SMTP_USE_SSL and SMTP_USE_TLS cannot both be true.")

    smtp_port = config_int(
        config,
        "SMTP_PORT",
        465 if use_ssl else 587 if use_tls else 25,
    )
    timeout = config_int(config, "SMTP_TIMEOUT_SECONDS", 30)

    message = build_report_email(
        output_path,
        mail_settings.recipients,
        mail_settings.subject,
        mail_settings.sender,
    )
    context = ssl.create_default_context()

    if use_ssl:
        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=timeout,
            context=context,
        ) as smtp:
            smtp.login(mail_settings.sender, mail_settings.app_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(mail_settings.sender, mail_settings.app_password)
        smtp.send_message(message)


def build_report_email(
    output_path: Path,
    recipients: Sequence[str],
    subject: str,
    sender: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(f"Attached is {output_path.name}.")

    mime_type, _ = mimetypes.guess_type(output_path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"
    maintype, subtype = mime_type.split("/", 1)
    message.add_attachment(
        output_path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=output_path.name,
    )
    return message


def config_bool(config: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = config.get(name)
    if raw_value is None or raw_value == "":
        return default
    value = raw_value.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: true, false, yes, no, 1, 0.")


def config_int(config: Mapping[str, str], name: str, default: int) -> int:
    raw_value = config.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def first_config_value(config: Mapping[str, str], names: Sequence[str]) -> str:
    for raw_value in config_values(config, names):
        clean = clean_optional(raw_value)
        if clean:
            return clean
    return ""


def config_values(config: Mapping[str, str], names: Sequence[str]) -> list[str]:
    return [config[name] for name in names if name in config]


def clean_optional(value: str | None) -> str:
    return value.strip() if value else ""
