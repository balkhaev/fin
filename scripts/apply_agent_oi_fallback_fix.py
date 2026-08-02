#!/usr/bin/env python3
"""Make recent OI REST augmentation optional and archive-backed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one marker, found {count}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    end_time = window_end_ms
    for _batch in range(8):
        payload = _fetch_json(
            f"{BINANCE_FUTURES_API}/futures/data/openInterestHist",
            {
                "symbol": "WIFUSDT",
                "period": "5m",
                "limit": "500",
                "endTime": str(end_time),
            },
        )
        if not isinstance(payload, list) or not payload:
            break
''',
    '''    end_time = window_end_ms
    for _batch in range(8):
        try:
            payload = _fetch_json(
                f"{BINANCE_FUTURES_API}/futures/data/openInterestHist",
                {
                    "symbol": "WIFUSDT",
                    "period": "5m",
                    "limit": "500",
                    "endTime": str(end_time),
                },
            )
        except (HTTPError, OSError, RuntimeError, TypeError, ValueError):
            # The daily metrics archive is canonical. REST only augments the
            # newest tail and may be geo-blocked on hosted CI runners.
            break
        if not isinstance(payload, list) or not payload:
            break
''',
)

replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    oi_by_timestamp.update(dict(recent_oi_points))
    oi_points = [(key, oi_by_timestamp[key]) for key in sorted(oi_by_timestamp)]
    wif_signals: list[dict[str, Any]] = []
''',
    '''    oi_by_timestamp.update(dict(recent_oi_points))
    oi_points = [(key, oi_by_timestamp[key]) for key in sorted(oi_by_timestamp)]
    if preliminary and not oi_points:
        raise DataUnavailableError(
            "WIF open-interest history is unavailable from both archives and REST"
        )
    wif_signals: list[dict[str, Any]] = []
''',
)

replace_once(
    "tests/runtime/test_factor_backtest_resilience.py",
    '''    def test_incomplete_coverage_is_rejected(self) -> None:
''',
    '''    def test_recent_open_interest_rest_geo_block_is_optional(self) -> None:
        error = HTTPError("https://fapi.binance.com", 451, "blocked", {}, None)
        with patch.object(subject, "_fetch_json", side_effect=error):
            points, audit = subject._recent_open_interest_points(date(2026, 7, 31))
        self.assertEqual(points, [])
        self.assertEqual(audit.request_count, 0)
        self.assertEqual(len(audit.payload_sha256), 64)

    def test_incomplete_coverage_is_rejected(self) -> None:
''',
)

print("OI fallback fix applied")
