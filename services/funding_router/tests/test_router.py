from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from funding_router.analytics import evaluate_pair
from funding_router.config import (
    ConfigError,
    ExchangeSettings,
    ExecutionSettings,
    LiveSettings,
    RiskSettings,
    ServiceSettings,
    Settings,
    load_settings,
)
from funding_router.execution import (
    ExecutionError,
    ExternalPositionError,
    LiveAuthorizationError,
    LiveExecutor,
    authorize_live,
)
from funding_router.gateways import CCXTGateway, GatewayError, parse_interval_hours
from funding_router.models import (
    Candidate,
    FundingQuote,
    MarketSnapshot,
    OrderBook,
    OrderState,
    PositionLeg,
    PositionState,
    PositionStatus,
    Side,
)
from funding_router.paper import PaperTrader
from funding_router.scanner import FundingScanner
from funding_router.store import SQLiteStore


def exchange_settings(exchange_id: str, fee: float = 2.0) -> ExchangeSettings:
    return ExchangeSettings(
        id=exchange_id,
        exchange_class=exchange_id,
        enabled=True,
        markets=("BTC/USDT:USDT",),
        maker_fee_bps=fee,
        taker_fee_bps=5.0,
        api_key_env="",
        secret_env="",
        password_env="",
        uid_env="",
        default_funding_interval_hours=8.0,
        leverage=1.0,
        sandbox=False,
        options={},
        params={},
    )


def make_settings(tmp_path: Path, **risk_changes: object) -> Settings:
    risk = RiskSettings(
        capital_usdt=3_000,
        notional_usdt=1_000,
        max_deployed_fraction=0.8,
        min_free_reserve_fraction=0.2,
        min_current_spread_bps_8h=1.0,
        min_predicted_spread_bps_8h=1.0,
        min_expected_net_bps=-100.0,
        max_basis_bps=100.0,
        max_mark_divergence_bps=100.0,
        min_open_interest_usdt=0.0,
        allow_missing_open_interest=True,
        min_depth_multiple=1.0,
        require_predicted_confirmation=True,
        slippage_buffer_bps=0.0,
        exit_basis_buffer_bps=0.0,
        adverse_selection_buffer_bps=0.0,
        max_unhedged_seconds=0.2,
        max_retries=3,
        min_fill_fraction=0.2,
    )
    if risk_changes:
        risk = replace(risk, **risk_changes)
    settings = Settings(
        service=ServiceSettings(
            poll_seconds=0.01,
            database_path=tmp_path / "router.sqlite3",
            order_book_limit=10,
            funding_history_limit=3,
            history_cache_seconds=1,
        ),
        risk=risk,
        execution=ExecutionSettings(
            maker_timeout_seconds=0.05,
            order_poll_seconds=0.001,
            maker_offset_bps=0.0,
            close_retry_delay_seconds=0.001,
            paper_start_balance_usdt=3_000,
        ),
        live=LiveSettings(
            enabled=True,
            confirmation_phrase="YES",
            confirmation_env="TEST_LIVE_CONFIRM",
            allow_external_positions=False,
            close_on_shutdown=True,
            require_balance_check=True,
            margin_safety_multiplier=1.0,
        ),
        exchanges=(exchange_settings("long"), exchange_settings("short")),
        source_path=tmp_path / "config.toml",
    )
    settings.validate()
    return settings


def snapshot(
    exchange: str,
    *,
    rate: float,
    predicted: float,
    interval: float = 8.0,
    mark: float = 100.0,
    bid: float = 99.9,
    ask: float = 100.1,
    oi: float | None = 10_000_000,
    funding_ts: int | None = None,
    prediction_source: str = "nextFundingRate",
) -> MarketSnapshot:
    quote = FundingQuote(
        exchange_id=exchange,
        symbol="BTC/USDT:USDT",
        asset="BTC",
        funding_rate=rate,
        predicted_funding_rate=predicted,
        interval_hours=interval,
        funding_timestamp_ms=funding_ts,
        mark_price=mark,
        index_price=mark,
        open_interest_usdt=oi,
        observed_at_ms=1_000,
        prediction_source=prediction_source,
    )
    book = OrderBook.from_iterables(
        bids=[[bid, 100.0], [bid - 0.1, 100.0]],
        asks=[[ask, 100.0], [ask + 0.1, 100.0]],
        timestamp_ms=1_000,
    )
    return MarketSnapshot(quote=quote, order_book=book)


def candidate() -> Candidate:
    return Candidate(
        asset="BTC",
        long_exchange="long",
        long_symbol="BTC/USDT:USDT",
        short_exchange="short",
        short_symbol="BTC/USDT:USDT",
        base_amount=1.0,
        matched_notional_usdt=100.0,
        long_entry_price=100.0,
        short_entry_price=100.0,
        current_spread_bps_8h=20.0,
        predicted_spread_bps_8h=15.0,
        gross_funding_bps=45.0,
        entry_basis_bps=0.0,
        fee_bps=10.0,
        slippage_bps=2.0,
        safety_bps=3.0,
        expected_net_bps=30.0,
        evaluation_hold_hours=24.0,
        long_open_interest_usdt=10_000_000,
        short_open_interest_usdt=10_000_000,
        long_depth_multiple=10.0,
        short_depth_multiple=10.0,
        long_funding_timestamp_ms=2_000,
        short_funding_timestamp_ms=2_000,
        long_interval_hours=8.0,
        short_interval_hours=8.0,
        observed_at_ms=1_000,
        maker_exchange="long",
        maker_side=Side.BUY,
        maker_reference_price=99.9,
    )


class FakeGateway:
    def __init__(
        self,
        exchange_id: str,
        snap: MarketSnapshot | None = None,
        *,
        maker_states: list[OrderState] | None = None,
        balance: float | None = 100_000.0,
        report_market_fills: bool = True,
    ):
        self.id = exchange_id
        self.markets = ("BTC/USDT:USDT",)
        self.snap = snap
        self.balance = balance
        self.positions = {"BTC/USDT:USDT": 0.0}
        self.market_orders: list[tuple[Side, float, bool]] = []
        self.prepared: list[str] = []
        self.closed = False
        self._maker_states = list(maker_states or [])
        self._maker_index = 0
        self._maker_side = Side.BUY
        self._maker_applied = 0.0
        self.fail_public = False
        self.fail_non_reduce_market = False
        self.reduce_fill_ratios: list[float] = []
        self.report_market_fills = report_market_fills
        self._market_states: dict[str, OrderState] = {}

    async def initialize(self) -> None:
        return None

    async def prepare_market(self, symbol: str) -> None:
        self.prepared.append(symbol)

    async def fetch_free_collateral_usdt(self) -> float | None:
        return self.balance

    async def fetch_snapshot(self, symbol: str) -> MarketSnapshot:
        if self.fail_public:
            raise GatewayError("public endpoint down")
        assert self.snap is not None
        return self.snap

    def _apply_maker_state(self, state: OrderState) -> None:
        delta = max(0.0, state.filled_base - self._maker_applied)
        if self._maker_side == Side.BUY:
            self.positions[state.symbol] += delta
        else:
            self.positions[state.symbol] -= delta
        self._maker_applied = state.filled_base

    async def place_post_only(
        self, symbol: str, side: Side, base_amount: float, price: float
    ) -> OrderState:
        self._maker_side = side
        self._maker_applied = 0.0
        if not self._maker_states:
            state = OrderState("maker", symbol, side, "closed", base_amount, base_amount, 0.0, price)
            self._maker_states = [state]
        state = self._maker_states[0]
        self._maker_index = 0
        self._apply_maker_state(state)
        return state

    async def fetch_order_state(self, order_id: str, symbol: str) -> OrderState:
        if order_id in self._market_states:
            return self._market_states[order_id]
        if self._maker_index + 1 < len(self._maker_states):
            self._maker_index += 1
        state = self._maker_states[self._maker_index]
        self._apply_maker_state(state)
        return state

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        return None

    async def place_market(
        self, symbol: str, side: Side, base_amount: float, *, reduce_only: bool = False
    ) -> OrderState:
        if self.fail_non_reduce_market and not reduce_only:
            raise GatewayError("simulated hedge failure")
        ratio = self.reduce_fill_ratios.pop(0) if reduce_only and self.reduce_fill_ratios else 1.0
        filled = base_amount * ratio
        current = self.positions[symbol]
        if reduce_only:
            if current > 0 and side == Side.SELL:
                filled = min(filled, current)
            elif current < 0 and side == Side.BUY:
                filled = min(filled, abs(current))
            else:
                filled = 0.0
        self.positions[symbol] += filled if side == Side.BUY else -filled
        if reduce_only and abs(self.positions[symbol]) < 1e-12:
            self.positions[symbol] = 0.0
        self.market_orders.append((side, base_amount, reduce_only))
        order_id = f"market-{len(self.market_orders)}"
        reported_filled = filled if self.report_market_fills else 0.0
        state = OrderState(
            order_id=order_id,
            symbol=symbol,
            side=side,
            status="closed",
            requested_base=base_amount,
            filled_base=reported_filled,
            remaining_base=max(0.0, base_amount - reported_filled),
            average_price=100.0,
        )
        self._market_states[order_id] = state
        return state

    async def fetch_position_base(self, symbol: str) -> float:
        return self.positions[symbol]

    async def close(self) -> None:
        self.closed = True


def test_load_settings_resolves_relative_database(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[service]
database_path = "state/router.db"
[risk]
capital_usdt = 1000
notional_usdt = 500
max_deployed_fraction = 0.8
min_free_reserve_fraction = 0.2
[live]
enabled = false
[[exchanges]]
id = "a"
exchange_class = "a"
markets = ["BTC/USDT:USDT"]
[[exchanges]]
id = "b"
exchange_class = "b"
markets = ["BTC/USDT:USDT"]
""",
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.service.database_path == (tmp_path / "state/router.db").resolve()
    assert settings.enabled_exchanges[0].leverage == 1.0


def test_config_rejects_overdeployment(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    bad = replace(settings, risk=replace(settings.risk, notional_usdt=2_900))
    with pytest.raises(ConfigError):
        bad.validate()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("8h", 8.0), ("30m", 0.5), ("1d", 24.0), (28_800_000, 8.0), (4, 4.0)],
)
def test_parse_interval_hours(raw: object, expected: float) -> None:
    assert parse_interval_hours(raw, 7.0) == pytest.approx(expected)


def test_order_book_exact_vwap_and_depth() -> None:
    book = OrderBook.from_iterables(
        bids=[[99, 1], [98, 2]], asks=[[101, 1], [102, 2]]
    )
    vwap, quote, slippage = book.vwap(Side.BUY, 2)
    assert vwap == pytest.approx(101.5)
    assert quote == pytest.approx(203)
    assert slippage > 0
    with pytest.raises(ValueError):
        book.vwap(Side.SELL, 4)


def test_evaluate_pair_normalizes_different_intervals(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    long = snapshot("long", rate=-0.0008, predicted=-0.0006, interval=8)
    short = snapshot("short", rate=0.0001, predicted=0.00008, interval=1)
    result = evaluate_pair(long, short, settings.risk, settings.exchange_map())
    assert result.candidate is not None
    assert result.candidate.current_spread_bps_8h == pytest.approx(16.0)
    assert result.candidate.base_amount > 0


def test_evaluate_pair_uses_actual_funding_schedule(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, hold_hours=3.0)
    # The 8h long leg has no payment before close; the 1h short leg pays
    # at 0.5h, 1.5h and 2.5h. A payment exactly at close is excluded.
    long = snapshot(
        "long",
        rate=0.0008,
        predicted=0.0007,
        interval=8,
        funding_ts=1_000 + 7 * 3_600_000,
    )
    short = snapshot(
        "short",
        rate=0.0002,
        predicted=0.00015,
        interval=1,
        funding_ts=1_000 + 30 * 60_000,
    )
    result = evaluate_pair(long, short, settings.risk, settings.exchange_map())
    assert result.candidate is not None
    assert result.candidate.metadata["long_funding_events"] == 0
    assert result.candidate.metadata["short_funding_events"] == 3
    assert result.candidate.metadata["current_gross_funding_bps"] == pytest.approx(6.0)
    assert result.candidate.metadata["predicted_gross_funding_bps"] == pytest.approx(4.5)
    assert result.candidate.gross_funding_bps == pytest.approx(4.5)


def test_evaluate_pair_rejects_predicted_reversal(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    long = snapshot("long", rate=-0.001, predicted=0.001)
    short = snapshot("short", rate=0.001, predicted=-0.001)
    result = evaluate_pair(long, short, settings.risk, settings.exchange_map())
    assert result.candidate is None
    assert result.rejection is not None
    assert result.rejection.reason == "predicted_spread_below_threshold"


def test_evaluate_pair_rejects_wide_basis(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_basis_bps=10.0)
    long = snapshot("long", rate=-0.001, predicted=-0.001, bid=101, ask=102)
    short = snapshot("short", rate=0.001, predicted=0.001, bid=99, ask=100)
    result = evaluate_pair(long, short, settings.risk, settings.exchange_map())
    assert result.rejection is not None
    assert result.rejection.reason == "entry_basis_too_wide"


def test_scanner_keeps_other_venues_when_one_public_call_fails(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    long_gw = FakeGateway("long", snapshot("long", rate=-0.001, predicted=-0.001))
    short_gw = FakeGateway("short", snapshot("short", rate=0.001, predicted=0.001))
    result = asyncio.run(FundingScanner(settings, {"long": long_gw, "short": short_gw}).scan_once())
    assert len(result.candidates) == 1
    short_gw.fail_public = True
    result = asyncio.run(FundingScanner(settings, {"long": long_gw, "short": short_gw}).scan_once())
    assert not result.candidates
    assert result.errors


def test_live_authorization_is_triple_guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.delenv("TEST_LIVE_CONFIRM", raising=False)
    with pytest.raises(LiveAuthorizationError):
        authorize_live(settings, True)
    monkeypatch.setenv("TEST_LIVE_CONFIRM", "YES")
    with pytest.raises(LiveAuthorizationError):
        authorize_live(settings, False)
    authorize_live(settings, True)
    disabled = replace(settings, live=replace(settings.live, enabled=False))
    with pytest.raises(LiveAuthorizationError):
        authorize_live(disabled, True)


def test_store_recovers_position_and_events(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    position = PositionState(
        position_id="p1",
        candidate_id="c1",
        asset="BTC",
        status=PositionStatus.OPEN,
        long_leg=PositionLeg("long", "BTC/USDT:USDT", Side.BUY, 1, 100),
        short_leg=PositionLeg("short", "BTC/USDT:USDT", Side.SELL, 1, 100),
        opened_at_ms=1,
        updated_at_ms=2,
        expected_net_bps_at_open=10,
    )
    with SQLiteStore(path) as store:
        store.save_position(position)
        store.append_event("opened", {"x": 1}, "p1", 3)
    with SQLiteStore(path) as store:
        recovered = store.load_active_positions()
        assert recovered[0].position_id == "p1"
        assert store.events()[0]["payload"] == {"x": 1}


def test_paper_trader_accrues_discrete_funding_and_recovers(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = SQLiteStore(settings.service.database_path)
    trader = PaperTrader(settings, store)
    position = trader.open(candidate(), 1_000)
    long = snapshot("long", rate=-0.001, predicted=-0.001, funding_ts=2_000)
    short = snapshot("short", rate=0.002, predicted=0.002, funding_ts=2_000)
    delta = trader.accrue(
        {("long", "BTC/USDT:USDT"): long, ("short", "BTC/USDT:USDT"): short},
        2_001,
    )
    assert delta == pytest.approx(0.3)
    assert position.funding_events == 2
    recovered = PaperTrader(settings, store)
    assert recovered.position is not None
    assert recovered.position.funding_pnl_usdt == pytest.approx(0.3)
    store.close()


def test_partial_maker_fills_are_hedged_incrementally(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    states = [
        OrderState("maker", "BTC/USDT:USDT", Side.BUY, "open", 1, 0.2, 0.8, 99.9),
        OrderState("maker", "BTC/USDT:USDT", Side.BUY, "open", 1, 0.6, 0.4, 99.9),
        OrderState("maker", "BTC/USDT:USDT", Side.BUY, "canceled", 1, 0.6, 0.4, 99.9),
    ]
    long_gw = FakeGateway("long", maker_states=states)
    short_gw = FakeGateway("short")
    with SQLiteStore(settings.service.database_path) as store:
        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)
        position = asyncio.run(executor.open_candidate(candidate()))
        assert position.long_leg.base_amount == pytest.approx(0.6)
        assert position.short_leg.base_amount == pytest.approx(0.6)
        hedge_sizes = [size for _, size, reduce_only in short_gw.market_orders if not reduce_only]
        assert hedge_sizes == pytest.approx([0.2, 0.4])
        assert long_gw.positions["BTC/USDT:USDT"] == pytest.approx(0.6)
        assert short_gw.positions["BTC/USDT:USDT"] == pytest.approx(-0.6)


def test_market_fill_is_confirmed_from_position_when_order_response_is_sparse(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    states = [
        OrderState("maker", "BTC/USDT:USDT", Side.BUY, "closed", 1, 1, 0, 99.9)
    ]
    long_gw = FakeGateway("long", maker_states=states)
    short_gw = FakeGateway("short", report_market_fills=False)
    with SQLiteStore(settings.service.database_path) as store:
        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)
        position = asyncio.run(executor.open_candidate(candidate()))
        assert position.long_leg.base_amount == pytest.approx(1.0)
        assert position.short_leg.base_amount == pytest.approx(1.0)
        hedge_event = next(
            event for event in store.events(20) if event["event_type"] == "hedge_order"
        )
        assert hedge_event["payload"]["reported_filled_base"] == pytest.approx(0.0)
        assert hedge_event["payload"]["confirmed_filled_base"] == pytest.approx(1.0)


def test_hedge_failure_triggers_emergency_flatten(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    states = [OrderState("maker", "BTC/USDT:USDT", Side.BUY, "closed", 1, 1, 0, 99.9)]
    long_gw = FakeGateway("long", maker_states=states)
    short_gw = FakeGateway("short")
    short_gw.fail_non_reduce_market = True
    with SQLiteStore(settings.service.database_path) as store:
        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)
        with pytest.raises(GatewayError):
            asyncio.run(executor.open_candidate(candidate()))
        assert long_gw.positions["BTC/USDT:USDT"] == pytest.approx(0.0)
        assert short_gw.positions["BTC/USDT:USDT"] == pytest.approx(0.0)
        assert not store.load_active_positions()


def test_external_position_blocks_entry(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    long_gw = FakeGateway("long")
    short_gw = FakeGateway("short")
    long_gw.positions["BTC/USDT:USDT"] = 0.5
    with SQLiteStore(settings.service.database_path) as store:
        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)
        with pytest.raises(ExternalPositionError):
            asyncio.run(executor.open_candidate(candidate()))


def test_balance_check_blocks_entry(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    long_gw = FakeGateway("long", balance=1.0)
    short_gw = FakeGateway("short", balance=1.0)
    with SQLiteStore(settings.service.database_path) as store:
        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)
        with pytest.raises(ExecutionError, match="insufficient verified free collateral"):
            asyncio.run(executor.open_candidate(candidate()))


def test_close_retries_partial_reduce_only_fills(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    long_gw = FakeGateway("long")
    short_gw = FakeGateway("short")
    long_gw.positions["BTC/USDT:USDT"] = 1.0
    short_gw.positions["BTC/USDT:USDT"] = -1.0
    long_gw.reduce_fill_ratios = [0.5, 1.0]
    short_gw.reduce_fill_ratios = [0.5, 1.0]
    pos = PositionState(
        position_id="p",
        candidate_id="c",
        asset="BTC",
        status=PositionStatus.OPEN,
        long_leg=PositionLeg("long", "BTC/USDT:USDT", Side.BUY, 1, 100),
        short_leg=PositionLeg("short", "BTC/USDT:USDT", Side.SELL, 1, 100),
        opened_at_ms=1,
        updated_at_ms=1,
        expected_net_bps_at_open=10,
    )
    with SQLiteStore(settings.service.database_path) as store:
        store.save_position(pos)
        executor = LiveExecutor(settings, {"long": long_gw, "short": short_gw}, store)
        closed = asyncio.run(executor.close_position(pos, "test"))
        assert closed.status == PositionStatus.CLOSED
        assert long_gw.positions["BTC/USDT:USDT"] == 0
        assert short_gw.positions["BTC/USDT:USDT"] == 0


def test_ccxt_contract_conversion_uses_contract_size() -> None:
    class FakeExchange:
        markets = {
            "BTC/USDT:USDT": {
                "symbol": "BTC/USDT:USDT",
                "contract": True,
                "contractSize": 0.001,
            }
        }

        def market(self, symbol: str) -> dict:
            return self.markets[symbol]

        def amount_to_precision(self, symbol: str, amount: float) -> str:
            return f"{amount:.0f}"

    gateway = object.__new__(CCXTGateway)
    gateway.id = "fake"
    gateway._initialized = True
    gateway._exchange = FakeExchange()
    assert gateway._base_to_order_amount("BTC/USDT:USDT", 0.01) == pytest.approx(10)
    state = gateway._order_state(
        "BTC/USDT:USDT",
        {
            "id": "1",
            "side": "buy",
            "status": "closed",
            "amount": 10,
            "filled": 5,
            "remaining": 5,
            "average": 100_000,
        },
    )
    assert state.requested_base == pytest.approx(0.01)
    assert state.filled_base == pytest.approx(0.005)


def test_service_scan_once_runs_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from funding_router.service import run_router

    settings = make_settings(tmp_path)
    long_gw = FakeGateway("long", snapshot("long", rate=-0.001, predicted=-0.001))
    short_gw = FakeGateway("short", snapshot("short", rate=0.001, predicted=0.001))
    code = asyncio.run(
        run_router(
            settings,
            "scan",
            once=True,
            gateways={"long": long_gw, "short": short_gw},
        )
    )
    output = capsys.readouterr().out
    assert code == 0
    assert '"candidates"' in output
    assert long_gw.closed and short_gw.closed


def test_service_paper_once_persists_position(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from funding_router.service import run_router

    settings = make_settings(tmp_path)
    long_gw = FakeGateway("long", snapshot("long", rate=-0.001, predicted=-0.001))
    short_gw = FakeGateway("short", snapshot("short", rate=0.001, predicted=0.001))
    code = asyncio.run(
        run_router(
            settings,
            "paper",
            once=True,
            gateways={"long": long_gw, "short": short_gw},
        )
    )
    assert code == 0
    assert '"paper"' in capsys.readouterr().out
    with SQLiteStore(settings.service.database_path) as store:
        trader = PaperTrader(settings, store)
        assert trader.position is not None


def test_ccxt_snapshot_parses_history_interval_and_open_interest(tmp_path: Path) -> None:
    class FakeExchange:
        has = {
            "fetchFundingRate": True,
            "fetchOpenInterest": True,
            "fetchFundingRateHistory": True,
        }
        markets = {
            "BTC/USDT:USDT": {
                "symbol": "BTC/USDT:USDT",
                "base": "BTC",
                "contract": True,
                "inverse": False,
                "contractSize": 0.001,
            }
        }

        async def fetch_funding_rate(self, symbol: str) -> dict:
            return {
                "fundingRate": 0.0002,
                "markPrice": 100.0,
                "indexPrice": 99.9,
                "interval": "4h",
                "fundingTimestamp": 10_000,
                "info": {},
            }

        async def fetch_order_book(self, symbol: str, limit: int) -> dict:
            return {"bids": [[99.9, 10]], "asks": [[100.1, 10]], "timestamp": 1}

        async def fetch_open_interest(self, symbol: str) -> dict:
            return {"openInterestAmount": 2000}

        async def fetch_funding_rate_history(self, symbol: str, since: object, limit: int) -> list[dict]:
            return [
                {"fundingRate": -0.001},
                {"fundingRate": 0.003},
                {"fundingRate": 0.001},
            ]

        def market(self, symbol: str) -> dict:
            return self.markets[symbol]

    gateway = object.__new__(CCXTGateway)
    gateway.config = exchange_settings("fake")
    gateway.service = ServiceSettings(
        database_path=tmp_path / "x.db",
        funding_history_limit=3,
        history_cache_seconds=300,
    )
    gateway.id = "fake"
    gateway.markets = ("BTC/USDT:USDT",)
    gateway._history_cache = {}
    gateway._initialized = True
    gateway._exchange = FakeExchange()
    snap = asyncio.run(gateway.fetch_snapshot("BTC/USDT:USDT"))
    assert snap.quote.interval_hours == pytest.approx(4)
    assert snap.quote.predicted_funding_rate == pytest.approx(0.001)
    assert snap.quote.prediction_source == "history_median"
    assert snap.quote.open_interest_usdt == pytest.approx(200_000)


def test_ccxt_balance_and_leverage_prepare() -> None:
    class FakeExchange:
        has = {"fetchBalance": True, "setLeverage": True}

        def __init__(self) -> None:
            self.leverage_calls: list[tuple[float, str, dict]] = []

        async def fetch_balance(self, params: dict) -> dict:
            return {"free": {"USDT": 100, "USDC": 50}}

        async def set_leverage(self, leverage: float, symbol: str, params: dict) -> None:
            self.leverage_calls.append((leverage, symbol, params))

    gateway = object.__new__(CCXTGateway)
    gateway.config = exchange_settings("fake")
    gateway.service = ServiceSettings()
    gateway.id = "fake"
    gateway.markets = ("BTC/USDT:USDT",)
    gateway._history_cache = {}
    gateway._initialized = True
    gateway._exchange = FakeExchange()
    assert asyncio.run(gateway.fetch_free_collateral_usdt()) == pytest.approx(150)
    asyncio.run(gateway.prepare_market("BTC/USDT:USDT"))
    assert gateway._exchange.leverage_calls[0][0] == 1
