from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "services" / "funding_router"


def replace_or_assert(path: Path, old: str, new: str, marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return
    if marker in text:
        return
    raise RuntimeError(f"neither old block nor new marker found in {path}")


analytics = ROOT / "src" / "funding_router" / "analytics.py"
if "def _funding_event_count(" not in analytics.read_text(encoding="utf-8"):
    raise RuntimeError("analytics funding schedule patch is missing")

execution = ROOT / "src" / "funding_router" / "execution.py"
replace_or_assert(
    execution,
    '''    async def _await_market_fill(\n        self,\n        gateway: ExchangeGateway,\n        symbol: str,\n        initial: OrderState,\n        requested_base: float,\n    ) -> OrderState:\n        state = initial\n        deadline = asyncio.get_running_loop().time() + self.settings.risk.max_unhedged_seconds\n        while (\n            state.filled_base + self._tolerance(requested_base) < requested_base\n            and not state.done\n            and state.order_id\n            and asyncio.get_running_loop().time() < deadline\n        ):\n            await asyncio.sleep(min(self.settings.execution.order_poll_seconds, 0.25))\n            state = await gateway.fetch_order_state(state.order_id, symbol)\n        return state\n\n    async def _hedge_exact(\n''',
    '''    async def _await_market_fill(\n        self,\n        gateway: ExchangeGateway,\n        symbol: str,\n        initial: OrderState,\n        requested_base: float,\n    ) -> OrderState:\n        state = initial\n        deadline = asyncio.get_running_loop().time() + self.settings.risk.max_unhedged_seconds\n        fetched_once = False\n        while (\n            state.filled_base + self._tolerance(requested_base) < requested_base\n            and state.order_id\n            and asyncio.get_running_loop().time() < deadline\n        ):\n            # Some venues return a sparse createOrder response marked closed.\n            # Fetch at least once before interpreting a zero fill as final.\n            if fetched_once and state.done:\n                break\n            await asyncio.sleep(min(self.settings.execution.order_poll_seconds, 0.25))\n            state = await gateway.fetch_order_state(state.order_id, symbol)\n            fetched_once = True\n        return state\n\n    async def _confirm_position_delta(\n        self,\n        gateway: ExchangeGateway,\n        symbol: str,\n        side: Side,\n        position_before: float,\n        requested_base: float,\n    ) -> float:\n        direction = 1.0 if side == Side.BUY else -1.0\n        tolerance = self._tolerance(requested_base)\n        deadline = asyncio.get_running_loop().time() + self.settings.risk.max_unhedged_seconds\n        confirmed = 0.0\n        while True:\n            position_after = await gateway.fetch_position_base(symbol)\n            confirmed = max(confirmed, (position_after - position_before) * direction)\n            confirmed = min(requested_base, max(0.0, confirmed))\n            if confirmed + tolerance >= requested_base:\n                return confirmed\n            if asyncio.get_running_loop().time() >= deadline:\n                return confirmed\n            await asyncio.sleep(min(self.settings.execution.order_poll_seconds, 0.25))\n\n    async def _hedge_exact(\n''',
    "async def _confirm_position_delta(",
)
replace_or_assert(
    execution,
    '''        for attempt in range(1, self.settings.risk.max_retries + 1):\n            remaining = required_base - accumulator.filled_base\n            if remaining <= tolerance:\n                break\n            order = await gateway.place_market(symbol, side, remaining, reduce_only=False)\n            order = await self._await_market_fill(gateway, symbol, order, remaining)\n            accumulator.add(order)\n            self.store.append_event(\n                "hedge_order",\n                {\n                    "exchange": gateway.id,\n                    "symbol": symbol,\n                    "side": side.value,\n                    "attempt": attempt,\n                    "requested_base": remaining,\n                    "filled_base": order.filled_base,\n                    "status": order.status,\n                    "order_id": order.order_id,\n                },\n                position_id,\n            )\n''',
    '''        for attempt in range(1, self.settings.risk.max_retries + 1):\n            remaining = required_base - accumulator.filled_base\n            if remaining <= tolerance:\n                break\n            position_before = await gateway.fetch_position_base(symbol)\n            order = await gateway.place_market(symbol, side, remaining, reduce_only=False)\n            order = await self._await_market_fill(gateway, symbol, order, remaining)\n            confirmed_base = await self._confirm_position_delta(\n                gateway, symbol, side, position_before, remaining\n            )\n            if confirmed_base <= tolerance:\n                safe_no_fill = order.status.lower() in {\n                    "canceled",\n                    "cancelled",\n                    "rejected",\n                    "expired",\n                }\n                if not safe_no_fill:\n                    raise ExecutionError(\n                        f"ambiguous market fill on {gateway.id}: "\n                        f"order={order.order_id!r}, status={order.status!r}, "\n                        f"reported={order.filled_base}, confirmed={confirmed_base}"\n                    )\n            confirmed_order = OrderState(\n                order_id=order.order_id,\n                symbol=order.symbol,\n                side=order.side,\n                status=order.status,\n                requested_base=remaining,\n                filled_base=confirmed_base,\n                remaining_base=max(0.0, remaining - confirmed_base),\n                average_price=order.average_price,\n                raw=order.raw,\n            )\n            accumulator.add(confirmed_order)\n            self.store.append_event(\n                "hedge_order",\n                {\n                    "exchange": gateway.id,\n                    "symbol": symbol,\n                    "side": side.value,\n                    "attempt": attempt,\n                    "requested_base": remaining,\n                    "reported_filled_base": order.filled_base,\n                    "confirmed_filled_base": confirmed_base,\n                    "status": order.status,\n                    "order_id": order.order_id,\n                },\n                position_id,\n            )\n''',
    '"confirmed_filled_base": confirmed_base',
)

tests = ROOT / "tests" / "test_router.py"
replace_or_assert(
    tests,
    '''        balance: float | None = 100_000.0,\n    ):\n''',
    '''        balance: float | None = 100_000.0,\n        report_market_fills: bool = True,\n    ):\n''',
    "report_market_fills: bool = True",
)
replace_or_assert(
    tests,
    '''        self.reduce_fill_ratios: list[float] = []\n''',
    '''        self.reduce_fill_ratios: list[float] = []\n        self.report_market_fills = report_market_fills\n        self._market_states: dict[str, OrderState] = {}\n''',
    "self._market_states: dict[str, OrderState] = {}",
)
replace_or_assert(
    tests,
    '''    async def fetch_order_state(self, order_id: str, symbol: str) -> OrderState:\n        if self._maker_index + 1 < len(self._maker_states):\n            self._maker_index += 1\n        state = self._maker_states[self._maker_index]\n        self._apply_maker_state(state)\n        return state\n''',
    '''    async def fetch_order_state(self, order_id: str, symbol: str) -> OrderState:\n        if order_id in self._market_states:\n            return self._market_states[order_id]\n        if self._maker_index + 1 < len(self._maker_states):\n            self._maker_index += 1\n        state = self._maker_states[self._maker_index]\n        self._apply_maker_state(state)\n        return state\n''',
    "if order_id in self._market_states:",
)
replace_or_assert(
    tests,
    '''        self.market_orders.append((side, base_amount, reduce_only))\n        return OrderState(\n            order_id=f"market-{len(self.market_orders)}",\n            symbol=symbol,\n            side=side,\n            status="closed",\n            requested_base=base_amount,\n            filled_base=filled,\n            remaining_base=max(0.0, base_amount - filled),\n            average_price=100.0,\n        )\n''',
    '''        self.market_orders.append((side, base_amount, reduce_only))\n        order_id = f"market-{len(self.market_orders)}"\n        reported_filled = filled if self.report_market_fills else 0.0\n        state = OrderState(\n            order_id=order_id,\n            symbol=symbol,\n            side=side,\n            status="closed",\n            requested_base=base_amount,\n            filled_base=reported_filled,\n            remaining_base=max(0.0, base_amount - reported_filled),\n            average_price=100.0,\n        )\n        self._market_states[order_id] = state\n        return state\n''',
    "reported_filled = filled if self.report_market_fills else 0.0",
)
normalization_test = '''def test_evaluate_pair_normalizes_different_intervals(tmp_path: Path) -> None:\n    settings = make_settings(tmp_path)\n    long = snapshot("long", rate=-0.0008, predicted=-0.0006, interval=8)\n    short = snapshot("short", rate=0.0001, predicted=0.00008, interval=1)\n    result = evaluate_pair(long, short, settings.risk, settings.exchange_map())\n    assert result.candidate is not None\n    assert result.candidate.current_spread_bps_8h == pytest.approx(16.0)\n    assert result.candidate.base_amount > 0\n\n\n'''
schedule_test = '''def test_evaluate_pair_uses_actual_funding_schedule(tmp_path: Path) -> None:\n    settings = make_settings(tmp_path, hold_hours=3.0)\n    # The 8h long leg has no payment before close; the 1h short leg pays\n    # at 0.5h, 1.5h and 2.5h. A payment exactly at close is excluded.\n    long = snapshot(\n        "long",\n        rate=0.0008,\n        predicted=0.0007,\n        interval=8,\n        funding_ts=1_000 + 7 * 3_600_000,\n    )\n    short = snapshot(\n        "short",\n        rate=0.0002,\n        predicted=0.00015,\n        interval=1,\n        funding_ts=1_000 + 30 * 60_000,\n    )\n    result = evaluate_pair(long, short, settings.risk, settings.exchange_map())\n    assert result.candidate is not None\n    assert result.candidate.metadata["long_funding_events"] == 0\n    assert result.candidate.metadata["short_funding_events"] == 3\n    assert result.candidate.metadata["current_gross_funding_bps"] == pytest.approx(6.0)\n    assert result.candidate.metadata["predicted_gross_funding_bps"] == pytest.approx(4.5)\n    assert result.candidate.gross_funding_bps == pytest.approx(4.5)\n\n\n'''
text = tests.read_text(encoding="utf-8")
if "def test_evaluate_pair_uses_actual_funding_schedule" not in text:
    if normalization_test not in text:
        raise RuntimeError("normalization test insertion point missing")
    tests.write_text(text.replace(normalization_test, normalization_test + schedule_test, 1), encoding="utf-8")

sparse_test = '''def test_market_fill_is_confirmed_from_position_when_order_response_is_sparse(\n    tmp_path: Path,\n) -> None:\n    settings = make_settings(tmp_path)\n    states = [\n        OrderState("maker", "BTC/USDT:USDT", Side.BUY, "closed", 1, 1, 0, 99.9)\n    ]\n    long_gw = FakeGateway("long", maker_states=states)\n    short_gw = FakeGateway("short", report_market_fills=False)\n    with SQLiteStore(settings.service.database_path) as store:\n        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)\n        position = asyncio.run(executor.open_candidate(candidate()))\n        assert position.long_leg.base_amount == pytest.approx(1.0)\n        assert position.short_leg.base_amount == pytest.approx(1.0)\n        hedge_event = next(\n            event for event in store.events(20) if event["event_type"] == "hedge_order"\n        )\n        assert hedge_event["payload"]["reported_filled_base"] == pytest.approx(0.0)\n        assert hedge_event["payload"]["confirmed_filled_base"] == pytest.approx(1.0)\n\n\n'''
text = tests.read_text(encoding="utf-8")
if "def test_market_fill_is_confirmed_from_position_when_order_response_is_sparse" not in text:
    marker = "def test_hedge_failure_triggers_emergency_flatten(tmp_path: Path) -> None:\n"
    if marker not in text:
        raise RuntimeError("hedge failure test insertion point missing")
    tests.write_text(text.replace(marker, sparse_test + marker, 1), encoding="utf-8")

status_path = ROOT / "STATUS.json"
status = json.loads(status_path.read_text(encoding="utf-8"))
status["deterministic_tests"] = 27
status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")

readme = ROOT / "README.md"
text = readme.read_text(encoding="utf-8")
old_formula = '''conservative_spread = min(current_spread, predicted_spread)\n\ngross_funding_bps = conservative_spread × hold_hours × 10 000\n'''
new_formula = '''long_events  = payments strictly before planned close\nshort_events = payments strictly before planned close\n\ncurrent_gross_bps = short_current_rate × short_events\n                  - long_current_rate  × long_events\n\npredicted_gross_bps = short_predicted_rate × short_events\n                    - long_predicted_rate  × long_events\n\ngross_funding_bps = min(current_gross_bps, predicted_gross_bps) × 10 000\n'''
if old_formula in text:
    text = text.replace(old_formula, new_formula, 1)
elif "long_events  = payments strictly before planned close" not in text:
    raise RuntimeError("README formula block missing")
old_tests = '''- partial maker fills;\n- incremental hedging;\n'''
new_tests = '''- actual funding timestamp/payment counts;\n- partial maker fills;\n- incremental hedging;\n- sparse market-order responses reconciled against real positions;\n'''
if old_tests in text:
    text = text.replace(old_tests, new_tests, 1)
elif "sparse market-order responses reconciled against real positions" not in text:
    raise RuntimeError("README test list block missing")
readme.write_text(text, encoding="utf-8")

manifest_path = ROOT / "MANIFEST.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
files: dict[str, dict[str, object]] = {}
for relative in manifest["files"]:
    data = (ROOT / relative).read_bytes()
    files[relative] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
manifest_path.write_text(
    json.dumps({"files": files, "format": "funding-router-manifest-v1"}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print("funding router safety patch materialized")
