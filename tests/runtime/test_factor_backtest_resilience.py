from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch
from urllib.error import HTTPError

from finruntime.observability import factor_backtests as subject
from finruntime.observability.errors import DataUnavailableError


class FactorBacktestResilienceTests(unittest.TestCase):
    def test_recent_api_window_covers_previous_month(self) -> None:
        self.assertEqual(
            subject._recent_api_start(
                date(2024, 1, 1), today=date(2026, 8, 1)
            ),
            date(2026, 7, 1),
        )

    def test_missing_archive_is_audited_when_fallback_is_allowed(self) -> None:
        error = HTTPError("https://example.invalid/data.zip", 404, "missing", {}, None)
        with patch.object(subject, "_fetch_bytes", side_effect=error):
            rows, audit = subject._download_archives(
                [subject.ArchiveSpec("test", "https://example.invalid/data.zip")],
                allow_missing=True,
            )
        self.assertEqual(rows, {})
        self.assertEqual(
            audit.missing_urls, ("https://example.invalid/data.zip",)
        )

    def test_bybit_uses_next_official_endpoint_after_403(self) -> None:
        forbidden = HTTPError("https://first", 403, "forbidden", {}, None)
        with (
            patch.object(subject, "BYBIT_API_BASES", ("https://first", "https://second")),
            patch.object(subject, "_fetch_json", side_effect=[forbidden, {"retCode": 0}]) as fetch,
        ):
            result = subject._fetch_bybit_json("/v5/market/test", {"symbol": "BTCUSDT"})
        self.assertEqual(result, {"retCode": 0})
        self.assertEqual(fetch.call_count, 2)

    def test_incomplete_coverage_is_rejected(self) -> None:
        rows = [
            {
                "timestamp_ms": 1_750_032_000_000,
                "close_time_ms": 1_750_032_899_999,
            }
        ]
        with self.assertRaises(DataUnavailableError):
            subject._require_kline_coverage(
                "test", rows, date(2024, 1, 1), date(2026, 7, 31)
            )


if __name__ == "__main__":
    unittest.main()
