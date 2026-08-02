#!/usr/bin/env python3
"""Switch recent Consensus history to reproducible daily Binance archives."""

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
    '''    current_month = _month_start(datetime.now(UTC).date())
    while cursor <= end:
        covered_start = max(start, cursor)
        covered_end = min(end, _month_end(cursor))
        if cursor >= current_month and kind != "metrics":
            cursor = _next_month(cursor)
            continue
        use_daily = kind == "metrics"
''',
    '''    current_month = _month_start(datetime.now(UTC).date())
    previous_month = _month_start(current_month - timedelta(days=1))
    while cursor <= end:
        covered_start = max(start, cursor)
        covered_end = min(end, _month_end(cursor))
        # Monthly bundles are published asynchronously. Use immutable daily
        # archives for the current and immediately preceding month so a replay
        # never depends on a not-yet-published monthly ZIP or a geo-sensitive API.
        use_daily = kind == "metrics" or cursor >= previous_month
''',
)

replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    archive_rows, archive_audit = _download_archives(specs, allow_missing=True)
    api_audits: list[DownloadAudit] = []
    current_start = _recent_api_start(warmup_start)
    if current_start <= end:
        for group, path, symbol in (
            ("wif", "/fapi/v1/klines", "WIFUSDT"),
            ("premium", "/fapi/v1/premiumIndexKlines", "WIFUSDT"),
            ("dot", "/fapi/v1/klines", "DOTUSDT"),
        ):
            api_rows, api_audit = _binance_api_klines(path, symbol, current_start, end)
            archive_rows.setdefault(group, []).extend(api_rows)
            api_audits.append(api_audit)
        funding_rows, funding_audit = _binance_api_funding(
            "DOTUSDT", current_start, end
        )
        archive_rows.setdefault("dot_funding", []).extend(funding_rows)
        api_audits.append(funding_audit)
''',
    '''    archive_rows, archive_audit = _download_archives(specs, allow_missing=True)
    # Recent closed history is intentionally archive-only. Daily bundles make
    # the replay deterministic and avoid Binance REST geo-policy differences.
    api_audits: list[DownloadAudit] = []
''',
)

replace_once(
    "tests/runtime/test_factor_backtest_resilience.py",
    '''    def test_recent_api_window_covers_previous_month(self) -> None:
        self.assertEqual(
            subject._recent_api_start(
                date(2024, 1, 1), today=date(2026, 8, 1)
            ),
            date(2026, 7, 1),
        )
''',
    '''    def test_recent_months_use_daily_archives(self) -> None:
        with patch.object(subject, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = subject.datetime(
                2026, 8, 3, tzinfo=subject.UTC
            )
            mocked_datetime.fromtimestamp.side_effect = subject.datetime.fromtimestamp
            specs = subject._archive_specs(
                "wif", "klines", "WIFUSDT", "15m",
                date(2026, 7, 1), date(2026, 7, 2),
            )
        self.assertEqual(len(specs), 2)
        self.assertTrue(all("/daily/klines/" in item.url for item in specs))
''',
)

print("archive-only fix applied")
