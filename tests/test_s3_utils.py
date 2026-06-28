from datetime import date
from unittest import TestCase

from common.models import ReportDate
from utils.s3_utils import resolve_artifact_prefix


class FakeS3Client:
    def __init__(self, existing_prefixes: set[str]) -> None:
        self.existing_prefixes = existing_prefixes

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        if Prefix in self.existing_prefixes:
            return {"Contents": [{"Key": f"{Prefix}orders/order_log.csv"}]}
        return {}


class S3UtilsTests(TestCase):
    def test_resolve_artifact_prefix_prefers_mock_over_production(self) -> None:
        client = FakeS3Client(
            {
                "holder/trades/firebot/250626/production/",
                "holder/trades/firebot/20260625/mock/",
            }
        )

        resolved = resolve_artifact_prefix(
            client,
            "bucket",
            "holder",
            "firebot",
            ReportDate(date(2026, 6, 25)),
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.artifact_kind, "mock")
        self.assertEqual(resolved.base_prefix, "holder/trades/firebot/20260625/")
