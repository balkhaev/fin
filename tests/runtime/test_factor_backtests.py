from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from finruntime.observability.factor_backtests import (
    ONE_HOUR_MS,
    _bybit_funding,
    _bybit_mark_klines,
    _funding_candidates,
    _funding_exit_reason,
    _recent_open_interest_points,
    _simulate_consensus,
)


class FactorBacktestTests(unittest.TestCase):
    def test_funding_candidate_uses_only_trailing_rates(self) -> None:
        origin = datetime(2026, 1, 1, tzinfo=UTC)
        timestamps = [
            int((origin + timedelta(hours=8 * index)).timestamp() * 1000)
            for index in range(5)
        ]
        binance = [(timestamp, -0.001) for timestamp in timestamps]
        bybit = [(timestamp, 0.002) for timestamp in timestamps]
        prices = [(timestamp, 100.0) for timestamp in timestamps]

        candidates = _funding_candidates("BTCUSDT", binance, bybit, prices, prices)

        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0]["entry_time_ms"], timestamps[2])
        self.assertEqual(candidates[0]["long_exchange"], "binance")
        self.assertEqual(candidates[0]["short_exchange"], "bybit")
        self.assertGreater(candidates[0]["expected_net_bps"], 10)

        wide_basis_prices = [(timestamp, 99.5) for timestamp in timestamps]
        self.assertEqual(
            _funding_candidates("BTCUSDT", binance, bybit, prices, wide_basis_prices),
            [],
        )

    def test_funding_exit_matches_live_spread_and_max_hold_contract(self) -> None:
        origin = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
        history = [(origin + index * 8 * ONE_HOUR_MS, 0.001) for index in range(12)]
        data = {
            "BTCUSDT": {
                "binance_funding": history,
                "bybit_funding": history,
            }
        }
        position = {
            "symbol": "BTCUSDT",
            "entry_time_ms": origin,
            "long_exchange": "binance",
            "short_exchange": "bybit",
        }

        self.assertEqual(
            _funding_exit_reason(position, origin + 16 * ONE_HOUR_MS, data),
            "current_funding_spread_collapsed",
        )
        data["BTCUSDT"]["bybit_funding"] = [
            (timestamp, rate + 0.002) for timestamp, rate in history
        ]
        self.assertIsNone(
            _funding_exit_reason(position, origin + 64 * ONE_HOUR_MS, data)
        )
        self.assertEqual(
            _funding_exit_reason(position, origin + 72 * ONE_HOUR_MS, data),
            "max_hold_hours",
        )

    def test_recent_open_interest_provenance_excludes_future_tail(self) -> None:
        inside = int(datetime(2026, 7, 29, 23, 55, tzinfo=UTC).timestamp() * 1000)
        future = int(datetime(2026, 7, 30, 0, 0, tzinfo=UTC).timestamp() * 1000)
        payload = [
            {"timestamp": inside, "sumOpenInterest": "100"},
            {"timestamp": future, "sumOpenInterest": "999"},
        ]
        with patch(
            "finruntime.observability.factor_backtests._fetch_json",
            side_effect=[payload, []],
        ) as fetch:
            points, audit = _recent_open_interest_points(date(2026, 7, 29))

        self.assertEqual(points, [(inside, 100.0)])
        self.assertEqual(fetch.call_args_list[0].args[1]["endTime"], str(future - 1))
        self.assertEqual(audit.request_count, 1)
        self.assertEqual(len(audit.payload_sha256), 64)

    def test_bybit_provenance_ignores_transport_server_time(self) -> None:
        observed = int(datetime(2026, 1, 1, 8, tzinfo=UTC).timestamp() * 1000)
        before = int(datetime(2025, 12, 31, 16, tzinfo=UTC).timestamp() * 1000)

        def funding_payload(server_time: int) -> dict[str, object]:
            return {
                "retCode": 0,
                "time": server_time,
                "result": {
                    "list": [
                        {
                            "fundingRateTimestamp": str(observed),
                            "fundingRate": "0.001",
                        },
                        {
                            "fundingRateTimestamp": str(before),
                            "fundingRate": "0.0",
                        },
                    ]
                },
            }

        def mark_payload(server_time: int) -> dict[str, object]:
            return {
                "retCode": 0,
                "time": server_time,
                "result": {
                    "list": [
                        [str(observed), "1", "1", "1", "1"],
                        [str(before), "1", "1", "1", "1"],
                    ]
                },
            }

        with patch(
            "finruntime.observability.factor_backtests._fetch_json",
            return_value=funding_payload(1),
        ):
            funding_first = _bybit_funding(
                "BTCUSDT", date(2026, 1, 1), date(2026, 1, 1)
            )
        with patch(
            "finruntime.observability.factor_backtests._fetch_json",
            return_value=funding_payload(2),
        ):
            funding_second = _bybit_funding(
                "BTCUSDT", date(2026, 1, 1), date(2026, 1, 1)
            )
        with patch(
            "finruntime.observability.factor_backtests._fetch_json",
            return_value=mark_payload(1),
        ):
            mark_first = _bybit_mark_klines(
                "BTCUSDT", date(2026, 1, 1), date(2026, 1, 1)
            )
        with patch(
            "finruntime.observability.factor_backtests._fetch_json",
            return_value=mark_payload(2),
        ):
            mark_second = _bybit_mark_klines(
                "BTCUSDT", date(2026, 1, 1), date(2026, 1, 1)
            )

        self.assertEqual(funding_first, funding_second)
        self.assertEqual(mark_first, mark_second)

    def test_consensus_intrabar_collision_is_stop_first(self) -> None:
        observed = datetime(2026, 1, 1, tzinfo=UTC)
        timestamp = int(observed.timestamp() * 1000)
        wif_rows = [
            {
                "timestamp_ms": timestamp,
                "open": 100.0,
                "high": 106.0,
                "low": 98.0,
                "close": 102.0,
                "quote_volume": 1_000_000.0,
                "taker_buy_quote": 500_000.0,
                "close_time_ms": timestamp + 15 * 60 * 1000 - 1,
            }
        ]
        signals = [
            {
                "module": "wif_oi_flush",
                "symbol": "WIFUSDT",
                "asset": "WIF",
                "signal_time_ms": timestamp - 1,
                "entry_time_ms": timestamp,
                "entry_price": 100.0,
                "atr": 0.8,
                "stop_atr": 1.25,
                "target_r": 5.0,
                "max_hold_minutes": 60,
            }
        ]

        _daily, trades = _simulate_consensus(
            signals, wif_rows, [], date(2026, 1, 1), date(2026, 1, 1)
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "stop_loss")
        self.assertEqual(trades[0]["exit_price"], 99.0)
        self.assertLess(trades[0]["net_pnl_usd"], 0)


if __name__ == "__main__":
    unittest.main()
