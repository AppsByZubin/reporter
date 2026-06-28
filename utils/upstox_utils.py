from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from utils.mail_utils import clean_optional, config_int, first_config_value


DEFAULT_UPSTOX_API_BASE_URL = "https://api.upstox.com"
DEFAULT_UPSTOX_ORDER_DETAILS_PATH = "/v2/order/details"


class UpstoxApiError(RuntimeError):
    """Raised when Upstox rejects or fails an order-details request."""


@dataclass(frozen=True)
class UpstoxSettings:
    access_token: str
    api_base_url: str
    order_details_path: str
    timeout: int

    @property
    def order_details_url(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/{self.order_details_path.lstrip('/')}"


def build_upstox_settings(config: Mapping[str, str]) -> UpstoxSettings:
    access_token = first_config_value(
        config,
        ("UPSTOX_API_ACCESS_TOKEN", "upstox_api_access_token"),
    )
    api_base_url = (
        first_config_value(config, ("UPSTOX_API_BASE_URL", "upstox_api_base_url"))
        or DEFAULT_UPSTOX_API_BASE_URL
    )
    order_details_path = (
        first_config_value(
            config,
            ("UPSTOX_ORDER_DETAILS_PATH", "upstox_order_details_path"),
        )
        or DEFAULT_UPSTOX_ORDER_DETAILS_PATH
    )

    errors: list[str] = []
    if not access_token:
        errors.append("UPSTOX_API_ACCESS_TOKEN is required for production reports.")
    try:
        timeout = config_int(config, "UPSTOX_API_TIMEOUT_SECONDS", 30)
    except ValueError as exc:
        errors.append(str(exc))
        timeout = 30
    if timeout <= 0:
        errors.append("UPSTOX_API_TIMEOUT_SECONDS must be greater than 0.")

    if errors:
        raise ValueError(" ".join(errors))

    return UpstoxSettings(
        access_token=access_token,
        api_base_url=api_base_url,
        order_details_path=order_details_path,
        timeout=timeout,
    )


class UpstoxOrderClient:
    def __init__(self, settings: UpstoxSettings) -> None:
        self.settings = settings

    def get_order_details(self, order_id: str) -> dict[str, Any]:
        clean_order_id = clean_optional(order_id)
        if not clean_order_id:
            raise ValueError("order_id is required.")

        query = urlencode({"order_id": clean_order_id})
        url = f"{self.settings.order_details_url}?{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.access_token}",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise UpstoxApiError(
                f"Upstox order details failed for {clean_order_id} "
                f"with HTTP {exc.code}: {shorten(read_error_body(exc))}"
            ) from exc
        except URLError as exc:
            raise UpstoxApiError(
                f"Upstox order details request failed for {clean_order_id}: {exc.reason}"
            ) from exc

        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise UpstoxApiError(
                f"Upstox order details returned invalid JSON for {clean_order_id}: "
                f"{shorten(body)}"
            ) from exc

        if not isinstance(decoded, dict):
            raise UpstoxApiError(
                f"Upstox order details returned an unexpected response for {clean_order_id}."
            )
        if decoded.get("status") not in (None, "success"):
            raise UpstoxApiError(
                f"Upstox order details returned status {decoded.get('status')} "
                f"for {clean_order_id}: {shorten(json.dumps(decoded, sort_keys=True))}"
            )
        data = decoded.get("data")
        if not isinstance(data, dict):
            raise UpstoxApiError(
                f"Upstox order details response missing data for {clean_order_id}."
            )
        return data


def read_error_body(error: HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def shorten(value: str, max_length: int = 300) -> str:
    clean = value.strip()
    if len(clean) <= max_length:
        return clean
    return f"{clean[:max_length]}..."
