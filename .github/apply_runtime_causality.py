from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match in {path}, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


ACCOUNTING = "src/finruntime/portfolio/accounting.py"
replace_once(
    ACCOUNTING,
    "from dataclasses import asdict, dataclass, replace",
    "from dataclasses import asdict, dataclass, field, replace",
)
replace_once(
    ACCOUNTING,
    '''    return output


def _text_mapping(value: Mapping[str, Decimal]) -> dict[str, str]:
''',
    '''    return output


def _intent_fill_mapping(
    value: Mapping[str, str] | None,
) -> dict[str, Decimal]:
    output: dict[str, Decimal] = {}
    for intent_id, raw in (value or {}).items():
        normalized_id = require_sha256(
            str(intent_id), field="active_plan_intent_id"
        )
        output[normalized_id] = require_decimal_string(
            raw,
            field=f"active_plan_filled_quantities.{normalized_id}",
            minimum=Decimal("0.000000000001"),
        )
    return output


def _text_mapping(value: Mapping[str, Decimal]) -> dict[str, str]:
''',
)
replace_once(
    ACCOUNTING,
    '''    applied_event_ids: Sequence[str]
    account_hash: str
''',
    '''    applied_event_ids: Sequence[str]
    account_hash: str
    active_plan_filled_quantities: Mapping[str, str] = field(default_factory=dict)
    active_plan_fill_event_ids: Sequence[str] = field(default_factory=tuple)
''',
)
replace_once(
    ACCOUNTING,
    '''        last_plan_id: str | None = None,
        applied_event_ids: Sequence[str] = (),
    ) -> "PaperAccountState":
''',
    '''        last_plan_id: str | None = None,
        applied_event_ids: Sequence[str] = (),
        active_plan_filled_quantities: Mapping[str, str] | None = None,
        active_plan_fill_event_ids: Sequence[str] = (),
    ) -> "PaperAccountState":
''',
)
replace_once(
    ACCOUNTING,
    '''        provisional = cls(
            schema_version="1.0",
            strategy_id=strategy_id,
''',
    '''        provisional = cls(
            schema_version="1.1",
            strategy_id=strategy_id,
''',
)
replace_once(
    ACCOUNTING,
    '''            applied_event_ids=tuple(applied_event_ids),
            account_hash="sha256:" + "0" * 64,
        )
''',
    '''            applied_event_ids=tuple(applied_event_ids),
            account_hash="sha256:" + "0" * 64,
            active_plan_filled_quantities=dict(
                active_plan_filled_quantities or {}
            ),
            active_plan_fill_event_ids=tuple(active_plan_fill_event_ids),
        )
''',
)
replace_once(
    ACCOUNTING,
    '''        if self.schema_version != "1.0" or not self.strategy_id:
''',
    '''        if self.schema_version not in {"1.0", "1.1"} or not self.strategy_id:
''',
)
replace_once(
    ACCOUNTING,
    '''        if self.last_plan_id is not None:
            require_sha256(self.last_plan_id, field="last_plan_id")
        if len(set(self.applied_event_ids)) != len(self.applied_event_ids):
''',
    '''        if self.last_plan_id is not None:
            require_sha256(self.last_plan_id, field="last_plan_id")
        plan_fills = _intent_fill_mapping(self.active_plan_filled_quantities)
        if len(set(self.applied_event_ids)) != len(self.applied_event_ids):
''',
)
replace_once(
    ACCOUNTING,
    '''        for event_id in self.applied_event_ids:
            require_sha256(event_id, field="applied_event_id")
        require_sha256(self.account_hash, field="account_hash")
        expected = sha256_id(_hash_payload(self, {"account_hash"}))
''',
    '''        for event_id in self.applied_event_ids:
            require_sha256(event_id, field="applied_event_id")
        if len(set(self.active_plan_fill_event_ids)) != len(
            self.active_plan_fill_event_ids
        ):
            raise ContractError("active plan fill event ids must be unique")
        for event_id in self.active_plan_fill_event_ids:
            require_sha256(event_id, field="active_plan_fill_event_id")
        if not set(self.active_plan_fill_event_ids).issubset(
            set(self.applied_event_ids)
        ):
            raise ContractError("active plan fill events must be applied to the account")
        if self.last_plan_id is None and (
            plan_fills or self.active_plan_fill_event_ids
        ):
            raise ContractError("active plan progress requires last_plan_id")
        if self.schema_version == "1.0" and (
            plan_fills or self.active_plan_fill_event_ids
        ):
            raise ContractError("legacy account state cannot contain plan progress")
        require_sha256(self.account_hash, field="account_hash")
        hash_payload = _hash_payload(self, {"account_hash"})
        if self.schema_version == "1.0":
            hash_payload.pop("active_plan_filled_quantities", None)
            hash_payload.pop("active_plan_fill_event_ids", None)
        expected = sha256_id(hash_payload)
''',
)
replace_once(
    ACCOUNTING,
    '''        # Force validation of normalized mappings even when their return values are unused.
        _ = spot, perp, entries

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
''',
    '''        # Force validation of normalized mappings even when their return values are unused.
        _ = spot, perp, entries, plan_fills

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.schema_version == "1.0":
            payload.pop("active_plan_filled_quantities", None)
            payload.pop("active_plan_fill_event_ids", None)
        return payload
''',
)
replace_once(
    ACCOUNTING,
    '''    last_plan_id: str | None,
    applied_event_ids: Sequence[str],
    sequence_increment: int = 1,
) -> PaperAccountState:
''',
    '''    last_plan_id: str | None,
    applied_event_ids: Sequence[str],
    active_plan_filled_quantities: Mapping[str, Decimal] | None = None,
    active_plan_fill_event_ids: Sequence[str] | None = None,
    sequence_increment: int = 1,
) -> PaperAccountState:
''',
)
replace_once(
    ACCOUNTING,
    '''    if equity <= 0:
        raise AccountingHalt("paper equity cannot become non-positive")
    return PaperAccountState.create(
''',
    '''    if equity <= 0:
        raise AccountingHalt("paper equity cannot become non-positive")
    normalized_as_of = format_utc(as_of_utc)
    if parse_utc(normalized_as_of) < parse_utc(state.as_of_utc):
        raise AccountingHalt("paper account time cannot move backward")
    plan_fills = (
        dict(active_plan_filled_quantities)
        if active_plan_filled_quantities is not None
        else _intent_fill_mapping(state.active_plan_filled_quantities)
    )
    plan_events = (
        tuple(active_plan_fill_event_ids)
        if active_plan_fill_event_ids is not None
        else tuple(state.active_plan_fill_event_ids)
    )
    return PaperAccountState.create(
''',
)
replace_once(
    ACCOUNTING,
    '''        as_of_utc=as_of_utc,
''',
    '''        as_of_utc=normalized_as_of,
''',
)
replace_once(
    ACCOUNTING,
    '''        last_plan_id=last_plan_id,
        applied_event_ids=tuple(applied_event_ids),
    )
''',
    '''        last_plan_id=last_plan_id,
        applied_event_ids=tuple(applied_event_ids),
        active_plan_filled_quantities=_text_mapping(plan_fills),
        active_plan_fill_event_ids=plan_events,
    )
''',
)
replace_once(
    ACCOUNTING,
    '''    cash, spot, perp, entries, fees, realized, funding, equity, high_water = _state_values(state)
    applied = tuple(state.applied_event_ids) + (fill.event_id,)
    if fill.status in {"rejected", "expired"}:
''',
    '''    cash, spot, perp, entries, fees, realized, funding, equity, high_water = _state_values(state)
    applied = tuple(state.applied_event_ids) + (fill.event_id,)
    plan_fills = _intent_fill_mapping(state.active_plan_filled_quantities)
    plan_events = tuple(state.active_plan_fill_event_ids) + (fill.event_id,)
    if fill.status in {"rejected", "expired"}:
''',
)
replace_once(
    ACCOUNTING,
    '''            last_plan_id=fill.plan_id,
            applied_event_ids=applied,
        )

    quantity = require_decimal_string(
''',
    '''            last_plan_id=fill.plan_id,
            applied_event_ids=applied,
            active_plan_filled_quantities=plan_fills,
            active_plan_fill_event_ids=plan_events,
        )

    quantity = require_decimal_string(
''',
)
replace_once(
    ACCOUNTING,
    '''    if quantity > requested:
        raise AccountingHalt("filled quantity exceeds intent quantity")
    price = require_decimal_string(
''',
    '''    cumulative = plan_fills.get(intent.intent_id, _ZERO) + quantity
    if cumulative > requested:
        raise AccountingHalt("cumulative filled quantity exceeds intent quantity")
    if fill.status == "filled" and cumulative != requested:
        raise AccountingHalt("filled status requires cumulative intent completion")
    if fill.status == "partial" and cumulative >= requested:
        raise AccountingHalt("partial status cannot complete an intent")
    plan_fills[intent.intent_id] = cumulative
    price = require_decimal_string(
''',
)
replace_once(
    ACCOUNTING,
    '''        last_plan_id=fill.plan_id,
        applied_event_ids=applied,
    )


def apply_funding_event(
''',
    '''        last_plan_id=fill.plan_id,
        applied_event_ids=applied,
        active_plan_filled_quantities=plan_fills,
        active_plan_fill_event_ids=plan_events,
    )


def apply_funding_event(
''',
)

LIFECYCLE = "src/finruntime/portfolio/lifecycle.py"
replace_once(
    LIFECYCLE,
    '''from finruntime.canonical import require_sha256
from finruntime.portfolio.accounting import PaperAccountState
''',
    '''from finruntime.canonical import parse_utc, require_sha256
from finruntime.portfolio.accounting import AccountingHalt, PaperAccountState
''',
)
replace_once(
    LIFECYCLE,
    '''    state.validate()
    plan_id = require_sha256(plan_id, field="plan_id")
''',
    '''    state.validate()
    if parse_utc(as_of_utc) < parse_utc(state.as_of_utc):
        raise AccountingHalt("paper plan activation cannot move account time backward")
    plan_id = require_sha256(plan_id, field="plan_id")
''',
)

JOURNAL = "src/finruntime/journal/atomic.py"
replace_once(
    JOURNAL,
    '''    format_utc,
    require_sha256,
''',
    '''    format_utc,
    parse_utc,
    require_sha256,
''',
)
replace_once(
    JOURNAL,
    '''_SINGLETON_EVENT_TYPES = {
    "SNAPSHOT_ACCEPTED",
    "TARGET_COMPUTED",
    "PLAN_CREATED",
    "STATE_COMMITTED",
    "RECONCILIATION_COMPLETED",
    "HALT_RAISED",
    "HALT_CLEARED",
}


def _write_bytes_atomic''',
    '''_SINGLETON_EVENT_TYPES = {
    "SNAPSHOT_ACCEPTED",
    "TARGET_COMPUTED",
    "PLAN_CREATED",
    "STATE_COMMITTED",
    "RECONCILIATION_COMPLETED",
    "HALT_RAISED",
    "HALT_CLEARED",
}

_RUNTIME_EVENT_PHASES = {
    "SNAPSHOT_ACCEPTED": 10,
    "TARGET_COMPUTED": 20,
    "PLAN_CREATED": 30,
    "FILL_RECORDED": 40,
    "STATE_COMMITTED": 50,
    "RECONCILIATION_COMPLETED": 60,
    "HALT_RAISED": 70,
}

_RUNTIME_EVENT_PREDECESSORS = {
    "TARGET_COMPUTED": {"SNAPSHOT_ACCEPTED"},
    "PLAN_CREATED": {"TARGET_COMPUTED"},
    "FILL_RECORDED": {"PLAN_CREATED"},
    "STATE_COMMITTED": {"PLAN_CREATED"},
    "RECONCILIATION_COMPLETED": {"STATE_COMMITTED"},
    "HALT_RAISED": {"RECONCILIATION_COMPLETED"},
}


def _write_bytes_atomic''',
)
replace_once(
    JOURNAL,
    '''    @staticmethod
    def _verify_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
''',
    '''    @staticmethod
    def _verify_semantics(events: Sequence[Mapping[str, Any]]) -> None:
        last_sequence: dict[str, int] = {}
        last_time: dict[str, Any] = {}
        phase_by_cycle: dict[tuple[str, int], int] = {}
        seen_by_cycle: dict[tuple[str, int], set[str]] = {}
        halted: set[str] = set()
        for line_number, event in enumerate(events, 1):
            strategy_id = str(event["strategy_id"])
            sequence = int(event["sequence"])
            event_type = str(event["event_type"])
            event_time = parse_utc(str(event["event_time_utc"]))
            previous_sequence = last_sequence.get(strategy_id)
            if previous_sequence is not None and sequence < previous_sequence:
                raise JournalCorruptionError(
                    f"journal sequence moved backward on line {line_number}"
                )
            previous_time = last_time.get(strategy_id)
            if previous_time is not None and event_time < previous_time:
                raise JournalCorruptionError(
                    f"journal event time moved backward on line {line_number}"
                )
            last_sequence[strategy_id] = sequence
            last_time[strategy_id] = event_time

            cycle_key = (strategy_id, sequence)
            seen = seen_by_cycle.setdefault(cycle_key, set())
            phase = _RUNTIME_EVENT_PHASES.get(event_type)
            if phase is not None:
                prior_phase = phase_by_cycle.get(cycle_key, -1)
                if phase < prior_phase:
                    raise JournalCorruptionError(
                        f"runtime event phase moved backward on line {line_number}"
                    )
                required = _RUNTIME_EVENT_PREDECESSORS.get(event_type, set())
                missing = required - seen
                if missing:
                    raise JournalCorruptionError(
                        f"runtime event {event_type} lacks predecessors {sorted(missing)} "
                        f"on line {line_number}"
                    )
                phase_by_cycle[cycle_key] = phase
                seen.add(event_type)

            if event_type == "HALT_RAISED":
                halted.add(strategy_id)
            elif event_type == "HALT_CLEARED":
                if strategy_id not in halted:
                    raise JournalCorruptionError(
                        f"HALT_CLEARED without HALT_RAISED on line {line_number}"
                    )
                halted.remove(strategy_id)

    @staticmethod
    def _verify_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
''',
)
replace_once(
    JOURNAL,
    '''            seen.add(str(event["event_hash"]))
            previous = str(event["event_hash"])
        return normalized
''',
    '''            seen.add(str(event["event_hash"]))
            previous = str(event["event_hash"])
        AppendOnlyJournal._verify_semantics(normalized)
        return normalized
''',
)
replace_once(
    JOURNAL,
    '''                by_identity[identity] = event
                selected.append(event)

            if new_events:
''',
    '''                by_identity[identity] = event
                selected.append(event)

            if new_events:
                self._verify_events([*existing, *new_events])
''',
)
# Sequence is used by semantic validation; import it explicitly.
replace_once(
    JOURNAL,
    "from typing import Any, Iterable, Iterator, Mapping",
    "from typing import Any, Iterable, Iterator, Mapping, Sequence",
)

TEST_INTEGRITY = "tests/runtime/test_runtime_integrity.py"
replace_once(
    TEST_INTEGRITY,
    "from finruntime.data.availability import evaluate_availability, seal_sources",
    "from finruntime.data.availability import seal_sources",
)
old_future = '''    def test_future_source_timestamp_blocks_risk_increase(self) -> None:
        observation = self.observation(
            source_time="2026-07-27T00:06:00Z",
            available_at="2026-07-27T00:04:00Z",
        )
        snapshot = MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": observation},
        )

        decision = evaluate_availability(
            snapshot,
            critical_sources=("spot_daily",),
        )

        self.assertFalse(decision.risk_increase_permitted)
        self.assertIn(
            "source_timestamp_after_available_at:spot_daily",
            decision.blocking_reasons,
        )
        self.assertIn(
            "source_timestamp_after_decision_time:spot_daily",
            decision.blocking_reasons,
        )
'''
new_future = '''    def test_future_source_timestamp_is_rejected_by_snapshot_contract(self) -> None:
        observation = self.observation(
            source_time="2026-07-27T00:06:00Z",
            available_at="2026-07-27T00:04:00Z",
        )
        with self.assertRaises(ContractError):
            MarketSnapshot.create(
                as_of_utc="2026-07-27T00:00:00Z",
                decision_time_utc="2026-07-27T00:05:00Z",
                sources={"spot_daily": observation},
            )
'''
replace_once(TEST_INTEGRITY, old_future, new_future)
path = Path(TEST_INTEGRITY)
text = path.read_text(encoding="utf-8")
start = text.index("    def test_partial_plan_cannot_be_executed_twice")
end = text.index("\n\nif __name__ == \"__main__\":", start)
new_method = '''    def test_partial_plan_resumes_without_overfill(self) -> None:
        source = SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-27T00:00:00Z",
            available_at_utc="2026-07-27T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
        )
        market = MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": source},
            spot={"BTC/USDT": {"reference_price": "100"}},
        )
        strategy = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc=market.decision_time_utc,
            market_snapshot_id=market.snapshot_id,
            state_sequence=1,
            targets={"spot": {"BTC/USDT": "0.2"}, "perp": {}},
            gross_target="0.2",
            cash_target="0.8",
            risk={"gross_cap": "1.05"},
        )
        account = PaperAccountState.empty(
            strategy_id="v75_atlas_nx",
            as_of_utc=market.decision_time_utc,
            starting_cash="10000",
        )
        portfolio = account.to_portfolio_state()
        risk = apply_pretrade_risk(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            reference_prices=self.reference_prices(),
            critical_sources=("spot_daily",),
        )
        plan = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=risk,
            reference_prices=self.reference_prices(),
        )
        policy = PaperBrokerPolicy(
            spot_commission_bps=Decimal("10"),
            perp_commission_bps=Decimal("6"),
            proxy_half_spread_bps=Decimal("4"),
            impact_bps=Decimal("2"),
            participation_rate=Decimal("0.10"),
        )
        first_quote = PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-27T00:06:00Z",
            source_observation_hash="sha256:" + "2" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="50",
        )
        first = execute_paper_cycle(
            plan=plan,
            account_state=account,
            quotes=(first_quote,),
            mark_prices=self.reference_prices(),
            policy=policy,
        )
        self.assertEqual(first.fill_events[0].status, "partial")
        self.assertEqual(first.fill_events[0].filled_quantity, "5")
        intent_id = plan.intents[0].intent_id
        self.assertEqual(
            first.account_state.active_plan_filled_quantities[intent_id], "5"
        )

        old_quote = PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-27T00:05:30Z",
            source_observation_hash="sha256:" + "4" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="1000",
        )
        with self.assertRaises(AccountingHalt):
            execute_paper_cycle(
                plan=plan,
                account_state=first.account_state,
                quotes=(old_quote,),
                mark_prices=self.reference_prices(),
                policy=policy,
            )

        retry_quote = PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-27T00:07:00Z",
            source_observation_hash="sha256:" + "3" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="1000",
        )
        second = execute_paper_cycle(
            plan=plan,
            account_state=first.account_state,
            quotes=(retry_quote,),
            mark_prices=self.reference_prices(),
            policy=policy,
        )
        self.assertEqual(second.fill_events[0].status, "filled")
        self.assertEqual(second.fill_events[0].filled_quantity, "15")
        self.assertEqual(second.account_state.spot_positions["BTC/USDT"], "20")
        self.assertTrue(second.execution_complete)

        repeated = execute_paper_cycle(
            plan=plan,
            account_state=second.account_state,
            quotes=(retry_quote,),
            mark_prices=self.reference_prices(),
            policy=policy,
        )
        self.assertEqual(repeated.fill_events, ())
        self.assertEqual(
            repeated.account_state.account_hash,
            second.account_state.account_hash,
        )
'''
path.write_text(text[:start] + new_method + text[end:], encoding="utf-8")

TEST_LEDGER = "tests/runtime/test_paper_ledger.py"
replace_once(
    TEST_LEDGER,
    '''        self.assertEqual(result.outcomes[1].reason, "parent_intent_not_fully_filled")
        self.assertEqual(result.account_state.perp_positions["BTC/USDT:USDT"], "1")

    def test_funding_long_pays_positive_rate_idempotently(self) -> None:
''',
    '''        self.assertEqual(result.outcomes[1].reason, "parent_intent_not_fully_filled")
        self.assertEqual(result.account_state.perp_positions["BTC/USDT:USDT"], "1")

        resumed = execute_paper_cycle(
            plan=plan,
            account_state=result.account_state,
            quotes=[
                self.quote(
                    market_type="perpetual",
                    available="100",
                    time="2026-07-27T00:07:00Z",
                )
            ],
            mark_prices=self.prices(),
            policy=policy,
        )
        self.assertEqual(
            [fill.status for fill in resumed.fill_events], ["filled", "filled"]
        )
        self.assertEqual(
            resumed.account_state.perp_positions["BTC/USDT:USDT"], "-3"
        )
        self.assertTrue(resumed.execution_complete)

    def test_funding_long_pays_positive_rate_idempotently(self) -> None:
''',
)
