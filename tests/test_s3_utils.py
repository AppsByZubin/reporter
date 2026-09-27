from datetime import date
from unittest import TestCase
from urllib.parse import parse_qs, urlparse

from common.models import ReportDate
from utils.s3_utils import build_s3_client, resolve_artifact_prefix, resolve_artifact_prefixes


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

    def test_resolve_artifact_prefixes_returns_mock_and_production(self) -> None:
        client = FakeS3Client(
            {
                "holder/trades/titanbot/250626/production/",
                "holder/trades/titanbot/20260625/mock/",
            }
        )

        resolved = resolve_artifact_prefixes(
            client,
            "bucket",
            "holder",
            "titanbot",
            ReportDate(date(2026, 6, 25)),
        )

        self.assertEqual(set(resolved), {"mock", "production"})
        self.assertEqual(
            resolved["production"].base_prefix,
            "holder/trades/titanbot/250626/",
        )
        self.assertEqual(
            resolved["mock"].base_prefix,
            "holder/trades/titanbot/20260625/",
        )


class CloudPeClientTests(TestCase):
    def setUp(self) -> None:
        self.settings = {
            "CLOUDPE_S3_REGION": "in-west2",
            "CLOUDPE_S3_ENDPOINT_URL": "https://s3.in-west2.purestore.io",
            "CLOUDPE_S3_ACCESS_KEY_ID": "cloudpe-test-key",
            "CLOUDPE_S3_SECRET_ACCESS_KEY": "cloudpe-test-secret",
            "CLOUDPE_S3_BUCKET_NAME": "test-bucket",
        }

    def test_uploads_and_downloads_use_cloudpe_path_style_and_signature_v4(self) -> None:
        settings = {
            **{key.replace("CLOUDPE_", "DO_"): "legacy-value" for key in self.settings},
            **self.settings,
        }
        client, bucket = build_s3_client(settings)
        self.addCleanup(client.close)
        self.assertEqual(bucket, "test-bucket")
        for operation in ("get_object", "put_object"):
            with self.subTest(operation=operation):
                url = urlparse(client.generate_presigned_url(
                    operation,
                    Params={"Bucket": bucket, "Key": "holder/trades/report.json"},
                ))
                self.assertEqual(url.scheme, "https")
                self.assertEqual(url.netloc, "s3.in-west2.purestore.io")
                self.assertEqual(url.path, "/test-bucket/holder/trades/report.json")
                query = parse_qs(url.query)
                self.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
                credential = query["X-Amz-Credential"][0]
                self.assertTrue(credential.startswith("cloudpe-test-key/"))
                self.assertIn("/in-west2/s3/aws4_request", credential)

    def test_missing_cloudpe_settings_fail_without_legacy_fallback(self) -> None:
        legacy = {key.replace("CLOUDPE_", "DO_"): "legacy-value" for key in self.settings}
        for missing in self.settings:
            with self.subTest(missing=missing):
                settings = {**legacy, **self.settings}
                del settings[missing]
                with self.assertRaisesRegex(ValueError, f"Missing CloudPe S3 settings: {missing}"):
                    build_s3_client(settings)

    def test_legacy_only_settings_are_rejected(self) -> None:
        legacy = {key.replace("CLOUDPE_", "DO_"): "legacy-value" for key in self.settings}
        with self.assertRaisesRegex(ValueError, "Missing CloudPe S3 settings"):
            build_s3_client(legacy)
