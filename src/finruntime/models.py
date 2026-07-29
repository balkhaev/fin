from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .canonical import (
    ContractError,
    format_utc,
    parse_utc,
    require_decimal_string,
    require_sha256,
    sha256_id,
)

SOURCE_QUALITIES = {"ok", "stale", "missing", "future", "invalid"}
PLAN_MODES = {"paper", "shadow"}
SIDES = {"buy", "sell"}
MARKET_TYPES = {"spot", "perpetual"}
FILL_STATUSES = {"partial", "filled", "rejected", "expired"}
RECONCILIATION_STATUSES = {"ok", "warn", "halt"}


def _tuple_strings(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(value) for value in (values or ()))


def _mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _hash_payload(instance: Any, excluded: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in asdict(instance).items()
        if key not in excluded
    }


def _validate_target_book(book: Mapping[str, Mapping[str, str]]) -> None:
    for market_type, positions in book.items():
        if market_type not in {"spot", "perp"}:
            raise ContractError(f"unsupported target market type: {market_type}")
        if not isinstance(positions, Mapping):
            raise ContractError(f"target book {market_type} must be a mapping")
        for instrument, quantity in positions.items():
            if not instrument:
                raise ContractError("instrument name cannot be empty")
            require_decimal_string(quantity, field=f"targets.{market_type}.{instrument}")


@dataclass(frozen=True, slots=True)
class SourceObservation:
    source: str
    source_timestamp_utc: str
    available_at_utc: str
    payload_sha256: str
    quality: str = "ok"

    def validate(self, *, decision_time_utc: str | None = None) -> None:
        if not self.source:
            raise ContractError("source cannot be empty")
        source_time = parse_utc(self.source_timestamp_utc)
        available = parse_utc(self.available_at_utc)
        require_sha256(self.payload_sha256, field="payload_sha256")
        if self.quality not in SOURCE_QUALITIES:
            raise ContractError(f"unsupported source quality: {self.quality}")
        if source_time > available:
            raise ContractError(
                f"source {self.source!r} timestamp is later than availability"
            )
        if decision_time_utc is None:
            return
        decision = parse_utc(decision_time_utc)
        if source_time > decision:
            raise ContractError(f"source {self.source!r} is dated after decision time")
        if available > decision:
            raise ContractError(
                f"source {self.source!r} was not available at decision time"
            )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    schema_version: str
    snapshot_id: str
    as_of_utc: str
    decision_time_utc: str
    sources: Mapping[str, SourceObservation]
    spot: Mapping[str, Any] = field(default_factory=dict)
    perp: Mapping[str, Any] = field(default_factory=dict)
    funding_events: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    onchain: Mapping[str, Any] = field(default_factory=dict)
    cash_rate: Mapping[str, Any] = field(default_factory=dict)
    quality_flags: Sequence[str] = field(default_factory=tuple)

    @classmethod
    def create(
        cls,
        *,
        as_of_utc: str,
        decision_time_utc: str,
        sources: Mapping[str, SourceObservation],
        spot: Mapping[str, Any] | None = None,
        perp: Mapping[str, Any] | None = None,
        funding_events: Sequence[Mapping[str, Any]] | None = None,
        onchain: Mapping[str, Any] | None = None,
        cash_rate: Mapping[str, Any] | None = None,
        quality_flags: Sequence[str] | None = None,
    ) -> "MarketSnapshot":
        provisional = cls(
            schema_version="1.0",
            snapshot_id="sha256:" + "0" * 64,
            as_of_utc=format_utc(as_of_utc),
            decision_time_utc=format_utc(decision_time_utc),
            sources=dict(sources),
            spot=_mapping(spot),
            perp=_mapping(perp),
            funding_events=tuple(funding_events or ()),
            onchain=_mapping(onchain),
            cash_rate=_mapping(cash_rate),
            quality_flags=_tuple_strings(quality_flags),
        )
        result = replace(
            provisional,
            snapshot_id=sha256_id(_hash_payload(provisional, {"snapshot_id"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported MarketSnapshot schema version")
        require_sha256(self.snapshot_id, field="snapshot_id")
        as_of = parse_utc(self.as_of_utc)
        decision = parse_utc(self.decision_time_utc)
        if as_of > decision:
            raise ContractError("as_of_utc cannot be later than decision_time_utc")
        if not self.sources:
            raise ContractError("MarketSnapshot requires at least one source")
        for name, observation in self.sources.items():
            if name != observation.source:
                raise ContractError(
                    f"source key {name!r} does not match observation source"
                )
            observation.validate(decision_time_utc=self.decision_time_utc)
        expected = sha256_id(_hash_payload(self, {"snapshot_id"}))
        if self.snapshot_id != expected:
            raise ContractError("MarketSnapshot hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrategySnapshot:
    schema_version: str
    strategy_id: str
    strategy_version: str
    decision_time_utc: str
    market_snapshot_id: str
    state_sequence: int
    targets: Mapping[str, Mapping[str, str]]
    gross_target: str
    cash_target: str
    risk: Mapping[str, Any]
    reasons: Sequence[str]
    quality_flags: Sequence[str]
    target_hash: str

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        strategy_version: str,
        decision_time_utc: str,
        market_snapshot_id: str,
        state_sequence: int,
        targets: Mapping[str, Mapping[str, str]],
        gross_target: str,
        cash_target: str,
        risk: Mapping[str, Any] | None = None,
        reasons: Sequence[str] | None = None,
        quality_flags: Sequence[str] | None = None,
    ) -> "StrategySnapshot":
        provisional = cls(
            schema_version="1.0",
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            decision_time_utc=format_utc(decision_time_utc),
            market_snapshot_id=require_sha256(
                market_snapshot_id, field="market_snapshot_id"
            ),
            state_sequence=int(state_sequence),
            targets={key: dict(value) for key, value in targets.items()},
            gross_target=gross_target,
            cash_target=cash_target,
            risk=_mapping(risk),
            reasons=_tuple_strings(reasons),
            quality_flags=_tuple_strings(quality_flags),
            target_hash="sha256:" + "0" * 64,
        )
        result = replace(
            provisional,
            target_hash=sha256_id(_hash_payload(provisional, {"target_hash"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported StrategySnapshot schema version")
        if not self.strategy_id or not self.strategy_version:
            raise ContractError("strategy id and version are required")
        parse_utc(self.decision_time_utc)
        require_sha256(self.market_snapshot_id, field="market_snapshot_id")
        require_sha256(self.target_hash, field="target_hash")
        if self.state_sequence < 0:
            raise ContractError("state_sequence must be non-negative")
        _validate_target_book(self.targets)
        require_decimal_string(
            self.gross_target, field="gross_target", minimum=Decimal("0")
        )
        require_decimal_string(
            self.cash_target, field="cash_target", minimum=Decimal("0")
        )
        expected = sha256_id(_hash_payload(self, {"target_hash"}))
        if self.target_hash != expected:
            raise ContractError("StrategySnapshot hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PortfolioState:
    schema_version: str
    strategy_id: str
    sequence: int
    as_of_utc: str
    cash: str
    equity: str
    high_water: str
    positions: Mapping[str, Mapping[str, str]]
    held_targets: Mapping[str, Mapping[str, str]]
    target_age_days: int
    pending_plan_id: str | None
    last_market_snapshot_id: str | None
    last_target_hash: str | None
    last_plan_hash: str | None
    state_hash: str

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        sequence: int,
        as_of_utc: str,
        cash: str,
        equity: str,
        high_water: str,
        positions: Mapping[str, Mapping[str, str]] | None = None,
        held_targets: Mapping[str, Mapping[str, str]] | None = None,
        target_age_days: int = 0,
        pending_plan_id: str | None = None,
        last_market_snapshot_id: str | None = None,
        last_target_hash: str | None = None,
        last_plan_hash: str | None = None,
    ) -> "PortfolioState":
        provisional = cls(
            schema_version="1.0",
            strategy_id=strategy_id,
            sequence=int(sequence),
            as_of_utc=format_utc(as_of_utc),
            cash=cash,
            equity=equity,
            high_water=high_water,
            positions={key: dict(value) for key, value in (positions or {}).items()},
            held_targets={
                key: dict(value) for key, value in (held_targets or {}).items()
            },
            target_age_days=int(target_age_days),
            pending_plan_id=pending_plan_id,
            last_market_snapshot_id=last_market_snapshot_id,
            last_target_hash=last_target_hash,
            last_plan_hash=last_plan_hash,
            state_hash="sha256:" + "0" * 64,
        )
        result = replace(
            provisional,
            state_hash=sha256_id(_hash_payload(provisional, {"state_hash"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported PortfolioState schema version")
        if not self.strategy_id:
            raise ContractError("strategy_id is required")
        parse_utc(self.as_of_utc)
        if self.sequence < 0 or self.target_age_days < 0:
            raise ContractError("sequence and target_age_days must be non-negative")
        require_decimal_string(self.cash, field="cash", minimum=Decimal("0"))
        equity = require_decimal_string(
            self.equity, field="equity", minimum=Decimal("0.00000001")
        )
        high_water = require_decimal_string(
            self.high_water, field="high_water", minimum=Decimal("0.00000001")
        )
        if high_water < equity:
            raise ContractError("high_water must be >= equity")
        _validate_target_book(self.positions)
        _validate_target_book(self.held_targets)
        for field_name in (
            "pending_plan_id",
            "last_market_snapshot_id",
            "last_target_hash",
            "last_plan_hash",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_sha256(value, field=field_name)
        require_sha256(self.state_hash, field="state_hash")
        expected = sha256_id(_hash_payload(self, {"state_hash"}))
        if self.state_hash != expected:
            raise ContractError("PortfolioState hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    intent_id: str
    instrument: str
    venue: str
    market_type: str
    side: str
    reduce_only: bool
    quantity: str
    quantity_unit: str
    reference_price: str
    max_slippage_bps: str
    reason: str
    parent_intent_id: str | None
    not_before_utc: str
    expires_at_utc: str

    @classmethod
    def create(
        cls,
        *,
        instrument: str,
        market_type: str,
        side: str,
        reduce_only: bool,
        quantity: str,
        quantity_unit: str,
        reference_price: str,
        max_slippage_bps: str,
        reason: str,
        not_before_utc: str,
        expires_at_utc: str,
        parent_intent_id: str | None = None,
    ) -> "ExecutionIntent":
        provisional = cls(
            intent_id="sha256:" + "0" * 64,
            instrument=instrument,
            venue="paper",
            market_type=market_type,
            side=side,
            reduce_only=bool(reduce_only),
            quantity=quantity,
            quantity_unit=quantity_unit,
            reference_price=reference_price,
            max_slippage_bps=max_slippage_bps,
            reason=reason,
            parent_intent_id=parent_intent_id,
            not_before_utc=format_utc(not_before_utc),
            expires_at_utc=format_utc(expires_at_utc),
        )
        result = replace(
            provisional,
            intent_id=sha256_id(_hash_payload(provisional, {"intent_id"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        require_sha256(self.intent_id, field="intent_id")
        if self.venue != "paper":
            raise ContractError("ExecutionIntent venue must be paper")
        if self.market_type not in MARKET_TYPES:
            raise ContractError("unsupported market_type")
        if self.side not in SIDES:
            raise ContractError("side must be buy or sell")
        if not self.instrument or not self.quantity_unit or not self.reason:
            raise ContractError("instrument, quantity_unit and reason are required")
        require_decimal_string(
            self.quantity, field="quantity", minimum=Decimal("0.000000000001")
        )
        require_decimal_string(
            self.reference_price,
            field="reference_price",
            minimum=Decimal("0.000000000001"),
        )
        require_decimal_string(
            self.max_slippage_bps,
            field="max_slippage_bps",
            minimum=Decimal("0"),
        )
        if self.parent_intent_id is not None:
            require_sha256(self.parent_intent_id, field="parent_intent_id")
        not_before = parse_utc(self.not_before_utc)
        expires = parse_utc(self.expires_at_utc)
        if expires <= not_before:
            raise ContractError("intent must expire after not_before_utc")
        expected = sha256_id(_hash_payload(self, {"intent_id"}))
        if self.intent_id != expected:
            raise ContractError("ExecutionIntent hash mismatch")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    schema_version: str
    plan_id: str
    strategy_id: str
    mode: str
    created_at_utc: str
    market_snapshot_id: str
    state_sequence: int
    target_hash: str
    intents: Sequence[ExecutionIntent]
    risk_summary: Mapping[str, Any]
    plan_hash: str

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        mode: str,
        created_at_utc: str,
        market_snapshot_id: str,
        state_sequence: int,
        target_hash: str,
        intents: Sequence[ExecutionIntent],
        risk_summary: Mapping[str, Any] | None = None,
    ) -> "ExecutionPlan":
        provisional = cls(
            schema_version="1.0",
            plan_id="sha256:" + "0" * 64,
            strategy_id=strategy_id,
            mode=mode,
            created_at_utc=format_utc(created_at_utc),
            market_snapshot_id=require_sha256(
                market_snapshot_id, field="market_snapshot_id"
            ),
            state_sequence=int(state_sequence),
            target_hash=require_sha256(target_hash, field="target_hash"),
            intents=tuple(intents),
            risk_summary=_mapping(risk_summary),
            plan_hash="sha256:" + "0" * 64,
        )
        digest = sha256_id(_hash_payload(provisional, {"plan_id", "plan_hash"}))
        result = replace(provisional, plan_id=digest, plan_hash=digest)
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported ExecutionPlan schema version")
        if self.mode not in PLAN_MODES:
            raise ContractError("ExecutionPlan mode must be paper or shadow")
        if not self.strategy_id or self.state_sequence < 0:
            raise ContractError("invalid strategy or state sequence")
        parse_utc(self.created_at_utc)
        require_sha256(self.market_snapshot_id, field="market_snapshot_id")
        require_sha256(self.target_hash, field="target_hash")
        require_sha256(self.plan_id, field="plan_id")
        require_sha256(self.plan_hash, field="plan_hash")
        saw_risk_increase = False
        for intent in self.intents:
            if isinstance(intent, Mapping):
                intent = ExecutionIntent(**intent)
            intent.validate()
            if not intent.reduce_only:
                saw_risk_increase = True
            elif saw_risk_increase:
                raise ContractError(
                    "risk-reducing intents must precede risk-increasing intents"
                )
        expected = sha256_id(_hash_payload(self, {"plan_id", "plan_hash"}))
        if self.plan_id != expected or self.plan_hash != expected:
            raise ContractError("ExecutionPlan hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FillEvent:
    schema_version: str
    event_id: str
    plan_id: str
    intent_id: str
    filled_at_utc: str
    status: str
    filled_quantity: str
    price: str
    fee: str
    fee_currency: str
    slippage_bps: str
    source_observation_hash: str

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        intent_id: str,
        filled_at_utc: str,
        status: str,
        filled_quantity: str,
        price: str,
        fee: str,
        fee_currency: str,
        slippage_bps: str,
        source_observation_hash: str,
    ) -> "FillEvent":
        provisional = cls(
            schema_version="1.0",
            event_id="sha256:" + "0" * 64,
            plan_id=require_sha256(plan_id, field="plan_id"),
            intent_id=require_sha256(intent_id, field="intent_id"),
            filled_at_utc=format_utc(filled_at_utc),
            status=status,
            filled_quantity=filled_quantity,
            price=price,
            fee=fee,
            fee_currency=fee_currency,
            slippage_bps=slippage_bps,
            source_observation_hash=require_sha256(
                source_observation_hash, field="source_observation_hash"
            ),
        )
        result = replace(
            provisional,
            event_id=sha256_id(_hash_payload(provisional, {"event_id"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported FillEvent schema version")
        if self.status not in FILL_STATUSES:
            raise ContractError("unsupported fill status")
        parse_utc(self.filled_at_utc)
        for name in ("event_id", "plan_id", "intent_id", "source_observation_hash"):
            require_sha256(getattr(self, name), field=name)
        minimum = (
            Decimal("0")
            if self.status in {"rejected", "expired"}
            else Decimal("0.000000000001")
        )
        require_decimal_string(
            self.filled_quantity, field="filled_quantity", minimum=minimum
        )
        require_decimal_string(self.price, field="price", minimum=Decimal("0"))
        require_decimal_string(self.fee, field="fee", minimum=Decimal("0"))
        require_decimal_string(
            self.slippage_bps, field="slippage_bps", minimum=Decimal("0")
        )
        if not self.fee_currency:
            raise ContractError("fee_currency is required")
        expected = sha256_id(_hash_payload(self, {"event_id"}))
        if self.event_id != expected:
            raise ContractError("FillEvent hash mismatch")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    schema_version: str
    strategy_id: str
    as_of_utc: str
    model_targets: Mapping[str, Any]
    planned_positions: Mapping[str, Any]
    paper_positions: Mapping[str, Any]
    tracking_error_fraction: str
    modelled_cost: str
    realized_paper_cost: str
    funding_pnl: str
    margin_buffer: str
    alerts: Sequence[str]
    status: str
    report_hash: str

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        as_of_utc: str,
        model_targets: Mapping[str, Any],
        planned_positions: Mapping[str, Any],
        paper_positions: Mapping[str, Any],
        tracking_error_fraction: str,
        modelled_cost: str,
        realized_paper_cost: str,
        funding_pnl: str,
        margin_buffer: str,
        alerts: Sequence[str] | None,
        status: str,
    ) -> "ReconciliationReport":
        provisional = cls(
            schema_version="1.0",
            strategy_id=strategy_id,
            as_of_utc=format_utc(as_of_utc),
            model_targets=_mapping(model_targets),
            planned_positions=_mapping(planned_positions),
            paper_positions=_mapping(paper_positions),
            tracking_error_fraction=tracking_error_fraction,
            modelled_cost=modelled_cost,
            realized_paper_cost=realized_paper_cost,
            funding_pnl=funding_pnl,
            margin_buffer=margin_buffer,
            alerts=_tuple_strings(alerts),
            status=status,
            report_hash="sha256:" + "0" * 64,
        )
        result = replace(
            provisional,
            report_hash=sha256_id(_hash_payload(provisional, {"report_hash"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported reconciliation schema")
        if not self.strategy_id:
            raise ContractError("strategy_id is required")
        parse_utc(self.as_of_utc)
        if self.status not in RECONCILIATION_STATUSES:
            raise ContractError("unsupported reconciliation status")
        require_decimal_string(
            self.tracking_error_fraction,
            field="tracking_error_fraction",
            minimum=Decimal("0"),
        )
        require_decimal_string(
            self.modelled_cost, field="modelled_cost", minimum=Decimal("0")
        )
        require_decimal_string(
            self.realized_paper_cost,
            field="realized_paper_cost",
            minimum=Decimal("0"),
        )
        require_decimal_string(self.funding_pnl, field="funding_pnl")
        require_decimal_string(self.margin_buffer, field="margin_buffer")
        require_sha256(self.report_hash, field="report_hash")
        expected = sha256_id(_hash_payload(self, {"report_hash"}))
        if self.report_hash != expected:
            raise ContractError("ReconciliationReport hash mismatch")
