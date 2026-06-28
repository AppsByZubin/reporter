import json
from unittest import TestCase
from unittest.mock import patch

from utils.upstox_utils import build_upstox_settings, UpstoxOrderClient


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class UpstoxUtilsTests(TestCase):
    def test_build_upstox_settings_accepts_uppercase_or_lowercase_token(self) -> None:
        settings = build_upstox_settings(
            {
                "upstox_api_access_token": " token ",
                "UPSTOX_API_BASE_URL": "https://api-hft.upstox.com",
                "UPSTOX_ORDER_DETAILS_PATH": "/v2/order/details",
                "UPSTOX_API_TIMEOUT_SECONDS": "45",
            }
        )

        self.assertEqual(settings.access_token, "token")
        self.assertEqual(settings.order_details_url, "https://api-hft.upstox.com/v2/order/details")
        self.assertEqual(settings.timeout, 45)

    def test_build_upstox_settings_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "UPSTOX_API_ACCESS_TOKEN"):
            build_upstox_settings({})

    @patch("utils.upstox_utils.urlopen")
    def test_get_order_details_calls_upstox_api(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(
            json.dumps(
                {
                    "status": "success",
                    "data": {
                        "order_id": "260625000196384",
                        "exchange_order_id": "1600000055377381",
                    },
                }
            ).encode("utf-8")
        )
        settings = build_upstox_settings({"UPSTOX_API_ACCESS_TOKEN": "token"})

        result = UpstoxOrderClient(settings).get_order_details("260625000196384")

        self.assertEqual(result["exchange_order_id"], "1600000055377381")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.upstox.com/v2/order/details?order_id=260625000196384",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(request.get_header("Accept"), "application/json")
