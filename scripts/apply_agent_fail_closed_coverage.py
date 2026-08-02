#!/usr/bin/env python3
"""Add funding coverage and HTTP outage regression checks."""

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
    '''def run_consensus_backtest(start: date, end: date) -> dict[str, Any]:
''',
    '''def _require_funding_coverage(
    name: str,
    rows: list[dict[str, str]],
    start: date,
    end: date,
) -> None:
    if not rows:
        raise DataUnavailableError(f"{name} returned no usable funding history")
    observed = sorted(
        datetime.fromtimestamp(int(row["calc_time"]) / 1000, UTC).date()
        for row in rows
    )
    if observed[0] > start + timedelta(days=1) or observed[-1] < end:
        raise DataUnavailableError(
            f"{name} coverage is incomplete: "
            f"{observed[0].isoformat()}..{observed[-1].isoformat()}, "
            f"required {start.isoformat()}..{end.isoformat()}"
        )


def run_consensus_backtest(start: date, end: date) -> dict[str, Any]:
''',
)

replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    _require_kline_coverage("WIF klines", wif_rows, warmup_start, end)
    _require_kline_coverage("WIF premium", premium_rows, warmup_start, end)
    _require_kline_coverage("DOT klines", dot_rows, warmup_start, end)
    preliminary = _preliminary_wif_signals(wif_rows, premium_rows)
''',
    '''    _require_kline_coverage("WIF klines", wif_rows, warmup_start, end)
    _require_kline_coverage("WIF premium", premium_rows, warmup_start, end)
    _require_kline_coverage("DOT klines", dot_rows, warmup_start, end)
    dot_funding_rows = archive_rows.get("dot_funding", [])
    _require_funding_coverage("DOT funding", dot_funding_rows, start, end)
    preliminary = _preliminary_wif_signals(wif_rows, premium_rows)
''',
)

replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    dot_signals = _dot_signals(archive_rows.get("dot_funding", []), dot_rows)
''',
    '''    dot_signals = _dot_signals(dot_funding_rows, dot_rows)
''',
)

replace_once(
    "tests/runtime/test_factor_backtest_resilience.py",
    '''    def test_incomplete_coverage_is_rejected(self) -> None:
''',
    '''    def test_missing_funding_history_is_rejected(self) -> None:
        with self.assertRaises(DataUnavailableError):
            subject._require_funding_coverage(
                "DOT funding", [], date(2026, 7, 1), date(2026, 7, 31)
            )

    def test_incomplete_coverage_is_rejected(self) -> None:
''',
)

replace_once(
    "tests/runtime/test_control_room_server.py",
    '''from finruntime.observability.server import create_server
''',
    '''from finruntime.observability.errors import DataUnavailableError
from finruntime.observability.server import create_server
''',
)

replace_once(
    "tests/runtime/test_control_room_server.py",
    '''        self.backtest_calls: list[str] = []

        def run_backtest(strategy_id: str) -> dict[str, object]:
            self.backtest_calls.append(strategy_id)
''',
    '''        self.backtest_calls: list[str] = []
        self.backtest_error: Exception | None = None

        def run_backtest(strategy_id: str) -> dict[str, object]:
            self.backtest_calls.append(strategy_id)
            if self.backtest_error is not None:
                raise self.backtest_error
''',
)

replace_once(
    "tests/runtime/test_control_room_server.py",
    '''    def test_backtest_post_has_a_global_single_flight_guard(self) -> None:
''',
    '''    def test_backtest_data_outage_returns_retryable_503(self) -> None:
        self.backtest_error = DataUnavailableError("public archive unavailable")
        request = urllib.request.Request(
            self.base + "/api/v1/backtests/consensus-wif-dot", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(captured.exception.code, 503)
        body = json.loads(captured.exception.read())
        self.assertEqual(body["error"], "data_unavailable")
        self.assertTrue(body["retryable"])

    def test_backtest_post_has_a_global_single_flight_guard(self) -> None:
''',
)

print("fail-closed coverage fix applied")
