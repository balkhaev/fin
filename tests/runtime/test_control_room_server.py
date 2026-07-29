from __future__ import annotations

import csv
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from finruntime.observability.server import create_server
from finruntime.observability.strategy_hub import (
    StrategyHub,
    UpstreamSnapshotCache,
)
from finruntime.operations.cycle import TELEMETRY_FIELDS


def telemetry_row() -> dict[str, object]:
    return {
        "timestamp": "2026-07-28T12:00:00Z",
        "strategy_id": "v75_atlas_nx",
        "source_bundle_sha256": "sha256:" + "1" * 64,
        "target_hash": "sha256:" + "2" * 64,
        "realized_position_hash": "sha256:" + "3" * 64,
        "gross_target": 0.8,
        "gross_realized": 0.8,
        "turnover": 0.02,
        "modelled_slippage_bps": 4.0,
        "paper_slippage_bps": 4.0,
        "net_return": 0.001,
        "equity": 10010.0,
        "drawdown": -0.01,
        "reconciliation_ok": True,
        "source_hash_match": True,
        "data_stale": False,
        "execution_complete": True,
    }


class ControlRoomServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        frontend = root / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text(
            "<!doctype html><title>FIN</title>", encoding="utf-8"
        )
        (frontend / "live.html").write_text(
            "<!doctype html><title>FIN Paper</title>", encoding="utf-8"
        )
        dashboard = frontend / "dashboard.json"
        dashboard.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "environment": {"live_ready": False},
                    "hero": {},
                    "strategies": [],
                    "stress_scenarios": [],
                    "annual_returns": [],
                    "equity_curve": [],
                    "market": {},
                    "readiness": [],
                    "policy": {},
                    "governance": {},
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )
        runtime = root / "runtime" / "v75_atlas_nx"
        runtime.mkdir(parents=True)
        with (runtime / "forward_telemetry.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDS)
            writer.writeheader()
            writer.writerow(telemetry_row())
        (root / "runtime" / "funding_router_snapshot.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "mode": "paper",
                    "updated_at_ms": int(time.time() * 1000),
                    "paper": {"equity_usdt": 3000.0, "open_position": None},
                    "scan": {"errors": [], "candidates": [], "rejections": []},
                    "markets": [{"asset": "BTC", "exchange_id": "binance"}],
                    "candles": [{"asset": "BTC", "items": [{"close": 100.0}]}],
                    "events": [],
                }
            ),
            encoding="utf-8",
        )
        (root / "runtime" / "consensus_paper_snapshot.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "mode": "paper",
                    "strategy_id": "consensus-wif-dot-v1",
                    "health": "healthy",
                    "market_data_at_ms": int(time.time() * 1000),
                    "paper": {
                        "starting_balance_usdt": 10_000.0,
                        "equity_usdt": 10_000.0,
                        "closed_positions": 0,
                        "positions": [],
                    },
                    "signals": [],
                    "candles": [],
                }
            ),
            encoding="utf-8",
        )
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            frontend_root=frontend,
            dashboard_path=dashboard,
            runtime_root=root / "runtime",
            stale_after_seconds=10**9,
            poll_seconds=0.2,
        )
        self.server.strategy_hub = StrategyHub(
            fin2_url="https://example.test/forward",
            cache=UpstreamSnapshotCache(
                lambda _url, _timeout: {
                    "status": "ready",
                    "paper": {
                        "account": {"initialNavUsd": 100_000.0},
                        "navUsd": 100_000.0,
                    },
                    "positions": [],
                }
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def json_get(self, path: str) -> dict[str, object]:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read())

    def test_dashboard_and_health_endpoints(self) -> None:
        dashboard = self.json_get("/api/v1/dashboard")
        self.assertEqual(dashboard["runtime"]["aggregate"]["observation_count"], 1)
        self.assertFalse(dashboard["environment"]["live_ready"])
        health = self.json_get("/api/v1/health")
        self.assertTrue(health["read_only"])
        self.assertFalse(health["exchange_submission_available"])
        scheduler = self.json_get("/api/v1/scheduler")
        self.assertFalse(scheduler["exchange_submission_available"])
        self.assertIn("scheduler", scheduler)
        paper = self.json_get("/api/v1/paper")
        self.assertEqual(paper["mode"], "paper")
        self.assertEqual(paper["health"], "healthy")
        self.assertTrue(health["paper"]["available"])
        strategies = self.json_get("/api/v1/strategies")
        self.assertEqual(strategies["summary"]["strategy_count"], 4)
        self.assertFalse(strategies["exchange_submission_available"])

    def test_post_is_rejected(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/v1/orders", data=b"{}", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(captured.exception.code, 405)
        body = json.loads(captured.exception.read())
        self.assertFalse(body["exchange_submission_available"])

    def test_sse_once_and_path_traversal(self) -> None:
        with urllib.request.urlopen(
            self.base + "/api/v1/events?once=1", timeout=5
        ) as response:
            text = response.read().decode("utf-8")
        self.assertIn("event: snapshot", text)
        with self.assertRaises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(self.base + "/%2e%2e/secret", timeout=5)
        self.assertEqual(captured.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
