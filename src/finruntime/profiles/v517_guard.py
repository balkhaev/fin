from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    format_utc,
    parse_utc,
    require_decimal_string,
    require_sha256,
    sha256_id,
)
from finruntime.models import StrategySnapshot

_ZERO = Decimal("0")
_ONE = Decimal("1")
_MAX_RESEARCH_LEVERAGE = Decimal("2.10")
_STATE_NAMES = {-1: "low", 0: "base", 1: "high"}


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text if text != "-0" else "0"


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


@dataclass(frozen=True, slots=True)
class CompletedEquityObservation:
    """One completed V75 equity observation available before the decision time."""

    as_of_utc: str
    equity: str
    source_sha256: str

    def validate(self, *, decision_time_utc: str | None = None) -> None:
        as_of = parse_utc(self.as_of_utc)
        value = require_decimal_string(
            self.equity,
            field="completed_equity.equity",
            minimum=Decimal("0.000000000001"),
        )
        if value <= 0:
            raise ContractError("completed equity must be positive")
        require_sha256(self.source_sha256, field="completed_equity.source_sha256")
        if decision_time_utc is not None and as_of >= parse_utc(decision_time_utc):
            raise ContractError("completed equity must predate the decision time")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class V517Policy:
    high_leverage: Decimal
    base_leverage: Decimal
    low_leverage: Decimal
    rebalance_days: int
    no_trade_band: Decimal
    minimum_state_hold_days: int
    high_entry_fast: Decimal
    high_entry_medium_floor: Decimal
    high_exit_fast: Decimal
    high_exit_medium: Decimal
    low_entry_fast: Decimal
    low_entry_medium: Decimal
    low_confirmation_days: int
    low_exit_fast: Decimal
    guard_enter_drawdown: Decimal
    guard_exit_drawdown: Decimal
    guard_cap: Decimal
    guard_minimum_hold_days: int

    def validate(self) -> None:
        for label, value in (
            ("high_leverage", self.high_leverage),
            ("base_leverage", self.base_leverage),
            ("low_leverage", self.low_leverage),
            ("guard_cap", self.guard_cap),
        ):
            if value <= 0 or value > _MAX_RESEARCH_LEVERAGE:
                raise ContractError(f"{label} must be in (0, 2.10]")
        if not (self.high_leverage >= self.base_leverage >= self.low_leverage):
            raise ContractError("V517 leverage states must be ordered high >= base >= low")
        if self.rebalance_days < 1:
            raise ContractError("V517 rebalance_days must be positive")
        if self.no_trade_band < 0:
            raise ContractError("V517 no_trade_band must be non-negative")
        if self.minimum_state_hold_days < 1:
            raise ContractError("V517 state hold must be positive")
        if self.low_confirmation_days < 1:
            raise ContractError("V517 low confirmation must be positive")
        if self.guard_minimum_hold_days < 1:
            raise ContractError("V517 guard hold must be positive")
        if not (self.guard_enter_drawdown < self.guard_exit_drawdown <= 0):
            raise ContractError("V517 guard thresholds are inconsistent")


FROZEN_V517_POLICY = V517Policy(
    high_leverage=Decimal("2.075"),
    base_leverage=Decimal("0.97"),
    low_leverage=Decimal("0.60"),
    rebalance_days=10,
    no_trade_band=Decimal("0.04"),
    minimum_state_hold_days=14,
    high_entry_fast=Decimal("0.05"),
    high_entry_medium_floor=Decimal("-0.04"),
    high_exit_fast=Decimal("-0.01"),
    high_exit_medium=Decimal("0"),
    low_entry_fast=Decimal("-0.05"),
    low_entry_medium=Decimal("-0.10"),
    low_confirmation_days=3,
    low_exit_fast=Decimal("0.01"),
    guard_enter_drawdown=Decimal("-0.245"),
    guard_exit_drawdown=Decimal("-0.18"),
    guard_cap=Decimal("1.00"),
    guard_minimum_hold_days=7,
)


@dataclass(frozen=True, slots=True)
class V517RuntimeState:
    """Persisted state needed between V517 decisions.

    `held_leverage` is the last leverage target actually emitted by this profile.
    The profile does not infer fills or positions; reconciliation belongs to the
    runtime portfolio layer.
    """

    held_leverage: str = "0"
    previous_target_leverage: str = "0"
    initialized: bool = False
    guard_active: bool = False
    guard_age_days: int = 7

    def validate(self) -> None:
        require_decimal_string(
            self.held_leverage,
            field="v517_state.held_leverage",
            minimum=_ZERO,
            maximum=_MAX_RESEARCH_LEVERAGE,
        )
        require_decimal_string(
            self.previous_target_leverage,
            field="v517_state.previous_target_leverage",
            minimum=_ZERO,
            maximum=_MAX_RESEARCH_LEVERAGE,
        )
        if self.guard_age_days < 0:
            raise ContractError("V517 guard age must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class V517MarketState:
    state: int
    state_name: str
    state_age_days: int
    state_switched: bool
    momentum20: str | None
    momentum60: str | None
    completed_observation_count: int
    latest_observation_utc: str | None
    source_bundle_sha256: str

    def validate(self) -> None:
        if self.state not in _STATE_NAMES:
            raise ContractError("invalid V517 market state")
        if self.state_name != _STATE_NAMES[self.state]:
            raise ContractError("V517 state name mismatch")
        if self.state_age_days < 0:
            raise ContractError("V517 state age must be non-negative")
        if self.completed_observation_count < 0:
            raise ContractError("V517 observation count must be non-negative")
        require_sha256(self.source_bundle_sha256, field="source_bundle_sha256")
        if self.latest_observation_utc is not None:
            parse_utc(self.latest_observation_utc)
        for label, value in (("momentum20", self.momentum20), ("momentum60", self.momentum60)):
            if value is not None:
                require_decimal_string(value, field=label)


@dataclass(frozen=True, slots=True)
class V517Decision:
    market: V517MarketState
    requested_leverage: str
    capped_desired_leverage: str
    selected_leverage: str
    runtime_leverage_cap: str
    runtime_cap_applied: bool
    drawdown_open: str
    guard_active: bool
    guard_age_days: int
    target_changed: bool
    risk_reduction: bool
    scheduled_rebalance: bool
    reasons: tuple[str, ...]
    next_state: V517RuntimeState
    decision_hash: str

    @classmethod
    def create(
        cls,
        *,
        market: V517MarketState,
        requested_leverage: Decimal,
        capped_desired_leverage: Decimal,
        selected_leverage: Decimal,
        runtime_leverage_cap: Decimal,
        drawdown_open: Decimal,
        guard_active: bool,
        guard_age_days: int,
        target_changed: bool,
        risk_reduction: bool,
        scheduled_rebalance: bool,
        reasons: Sequence[str],
        next_state: V517RuntimeState,
    ) -> "V517Decision":
        provisional = cls(
            market=market,
            requested_leverage=_decimal_text(requested_leverage),
            capped_desired_leverage=_decimal_text(capped_desired_leverage),
            selected_leverage=_decimal_text(selected_leverage),
            runtime_leverage_cap=_decimal_text(runtime_leverage_cap),
            runtime_cap_applied=capped_desired_leverage < requested_leverage,
            drawdown_open=_decimal_text(drawdown_open),
            guard_active=bool(guard_active),
            guard_age_days=int(guard_age_days),
            target_changed=bool(target_changed),
            risk_reduction=bool(risk_reduction),
            scheduled_rebalance=bool(scheduled_rebalance),
            reasons=tuple(str(item) for item in reasons),
            next_state=next_state,
            decision_hash="sha256:" + "0" * 64,
        )
        result = replace(
            provisional,
            decision_hash=sha256_id(
                {
                    key: value
                    for key, value in asdict(provisional).items()
                    if key != "decision_hash"
                }
            ),
        )
        result.validate()
        return result

    def validate(self) -> None:
        self.market.validate()
        self.next_state.validate()
        require_sha256(self.decision_hash, field="decision_hash")
        cap = require_decimal_string(
            self.runtime_leverage_cap,
            field="runtime_leverage_cap",
            minimum=Decimal("0.01"),
            maximum=_MAX_RESEARCH_LEVERAGE,
        )
        requested = require_decimal_string(
            self.requested_leverage,
            field="requested_leverage",
            minimum=_ZERO,
            maximum=_MAX_RESEARCH_LEVERAGE,
        )
        capped = require_decimal_string(
            self.capped_desired_leverage,
            field="capped_desired_leverage",
            minimum=_ZERO,
            maximum=cap,
        )
        selected = require_decimal_string(
            self.selected_leverage,
            field="selected_leverage",
            minimum=_ZERO,
            maximum=cap,
        )
        require_decimal_string(self.drawdown_open, field="drawdown_open")
        if self.runtime_cap_applied != (capped < requested):
            raise ContractError("V517 runtime cap flag mismatch")
        if selected != require_decimal_string(
            self.next_state.held_leverage,
            field="next_state.held_leverage",
        ):
            raise ContractError("V517 selected leverage/state mismatch")
        expected = sha256_id(
            {
                key: value
                for key, value in asdict(self).items()
                if key != "decision_hash"
            }
        )
        if self.decision_hash != expected:
            raise ContractError("V517 decision hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _validate_observations(
    observations: Sequence[CompletedEquityObservation],
    *,
    decision_time_utc: str,
) -> tuple[tuple[CompletedEquityObservation, ...], tuple[Decimal, ...]]:
    parse_utc(decision_time_utc)
    checked = tuple(observations)
    previous = None
    values: list[Decimal] = []
    for number, observation in enumerate(checked):
        observation.validate(decision_time_utc=decision_time_utc)
        timestamp = parse_utc(observation.as_of_utc)
        if previous is not None:
            if timestamp <= previous:
                raise ContractError("completed equity timestamps must be strictly increasing")
            if (timestamp - previous).total_seconds() != 86400:
                raise ContractError("V517 requires a contiguous daily equity history")
        previous = timestamp
        values.append(
            require_decimal_string(
                observation.equity,
                field=f"completed_equity[{number}].equity",
                minimum=Decimal("0.000000000001"),
            )
        )
    return checked, tuple(values)


def _momentum(values: Sequence[Decimal], decision_index: int, lookback: int) -> Decimal | None:
    if decision_index < lookback + 1:
        return None
    return values[decision_index - 1] / values[decision_index - lookback - 1] - _ONE


def evaluate_v517_market_state(
    observations: Sequence[CompletedEquityObservation],
    *,
    decision_time_utc: str,
    policy: V517Policy = FROZEN_V517_POLICY,
) -> V517MarketState:
    """Evaluate the exact V517 high/base/low state for the next decision.

    Each observation must be a completed daily V75 equity value strictly before
    `decision_time_utc`. The iteration includes the next decision after the last
    observation, matching the research shift(1) convention.
    """

    policy.validate()
    checked, values = _validate_observations(
        observations,
        decision_time_utc=decision_time_utc,
    )
    state = 0
    age = policy.minimum_state_hold_days
    high_count = 0
    low_count = 0
    latest_fast: Decimal | None = None
    latest_medium: Decimal | None = None
    switched = False

    for decision_index in range(len(values) + 1):
        fast = _momentum(values, decision_index, 20)
        medium = _momentum(values, decision_index, 60)
        latest_fast, latest_medium = fast, medium
        high_raw = (
            fast is not None
            and medium is not None
            and fast > policy.high_entry_fast
            and medium > policy.high_entry_medium_floor
        )
        low_raw = (
            fast is not None
            and medium is not None
            and fast < policy.low_entry_fast
            and medium < policy.low_entry_medium
        )
        high_count = high_count + 1 if high_raw else 0
        low_count = low_count + 1 if low_raw else 0
        high_condition = high_count >= 1
        low_condition = low_count >= policy.low_confirmation_days
        switched = False

        if age >= policy.minimum_state_hold_days:
            if state == 1:
                if low_condition:
                    state, age, switched = -1, 0, True
                elif (
                    (fast is not None and fast < policy.high_exit_fast)
                    or (medium is not None and medium < policy.high_exit_medium)
                ):
                    state, age, switched = 0, 0, True
                else:
                    age += 1
            elif state == -1:
                if high_condition:
                    state, age, switched = 1, 0, True
                elif fast is not None and fast > policy.low_exit_fast:
                    state, age, switched = 0, 0, True
                else:
                    age += 1
            else:
                if high_condition:
                    state, age, switched = 1, 0, True
                elif low_condition:
                    state, age, switched = -1, 0, True
                else:
                    age += 1
        else:
            age += 1

    source_bundle_sha256 = sha256_id([item.to_dict() for item in checked])
    result = V517MarketState(
        state=state,
        state_name=_STATE_NAMES[state],
        state_age_days=age,
        state_switched=switched,
        momentum20=_optional_decimal_text(latest_fast),
        momentum60=_optional_decimal_text(latest_medium),
        completed_observation_count=len(checked),
        latest_observation_utc=(
            format_utc(checked[-1].as_of_utc) if checked else None
        ),
        source_bundle_sha256=source_bundle_sha256,
    )
    result.validate()
    return result


def apply_v517_policy(
    *,
    observations: Sequence[CompletedEquityObservation],
    decision_time_utc: str,
    profile_equity: str,
    profile_high_water: str,
    runtime_state: V517RuntimeState,
    maximum_runtime_leverage: str = "2.10",
    policy: V517Policy = FROZEN_V517_POLICY,
) -> V517Decision:
    """Apply the frozen V517 state and guard without creating orders.

    `maximum_runtime_leverage` is an explicit outer cap. Production deployments
    should keep this at or below the independently audited runtime allowance. The
    research ceiling of 2.10 is accepted only because the resulting strategy is
    shadow-only and the downstream planner remains fail-closed.
    """

    policy.validate()
    runtime_state.validate()
    equity = require_decimal_string(
        profile_equity,
        field="profile_equity",
        minimum=Decimal("0.000000000001"),
    )
    high_water = require_decimal_string(
        profile_high_water,
        field="profile_high_water",
        minimum=Decimal("0.000000000001"),
    )
    if equity <= 0 or high_water <= 0 or high_water < equity:
        raise ContractError("profile equity/high-water are inconsistent")
    runtime_cap = require_decimal_string(
        maximum_runtime_leverage,
        field="maximum_runtime_leverage",
        minimum=Decimal("0.01"),
        maximum=_MAX_RESEARCH_LEVERAGE,
    )
    market = evaluate_v517_market_state(
        observations,
        decision_time_utc=decision_time_utc,
        policy=policy,
    )

    guard_active = runtime_state.guard_active
    guard_age = runtime_state.guard_age_days
    drawdown_open = equity / high_water - _ONE
    if guard_age >= policy.guard_minimum_hold_days:
        if not guard_active and drawdown_open <= policy.guard_enter_drawdown:
            guard_active = True
            guard_age = 0
        elif guard_active and drawdown_open >= policy.guard_exit_drawdown:
            guard_active = False
            guard_age = 0
        else:
            guard_age += 1
    else:
        guard_age += 1

    requested = (
        policy.high_leverage
        if market.state == 1
        else policy.low_leverage
        if market.state == -1
        else policy.base_leverage
    )
    if guard_active:
        requested = min(requested, policy.guard_cap)
    desired = min(requested, runtime_cap)

    held = require_decimal_string(
        runtime_state.held_leverage,
        field="v517_state.held_leverage",
        minimum=_ZERO,
        maximum=_MAX_RESEARCH_LEVERAGE,
    )
    previous_target = require_decimal_string(
        runtime_state.previous_target_leverage,
        field="v517_state.previous_target_leverage",
        minimum=_ZERO,
        maximum=_MAX_RESEARCH_LEVERAGE,
    )
    decision_index = len(observations)
    risk_reduction = desired < held - policy.no_trade_band
    scheduled = decision_index == 0 or decision_index % policy.rebalance_days == 0
    target_changed = abs(desired - previous_target) >= policy.no_trade_band
    should_update = not runtime_state.initialized or risk_reduction or (scheduled and target_changed)
    selected = desired if should_update else held

    reasons: list[str] = []
    if not runtime_state.initialized:
        reasons.append("initialization")
    if market.state_switched:
        reasons.append("market_state_switched")
    if guard_active:
        reasons.append("drawdown_guard_active")
    if desired < requested:
        reasons.append("runtime_leverage_cap")
    if risk_reduction:
        reasons.append("urgent_risk_reduction")
    if scheduled:
        reasons.append("scheduled_rebalance_window")
    if scheduled and target_changed:
        reasons.append("target_band_exceeded")
    if not should_update:
        reasons.append("held_inside_schedule_or_band")

    next_state = V517RuntimeState(
        held_leverage=_decimal_text(selected),
        previous_target_leverage=(
            _decimal_text(selected)
            if should_update
            else runtime_state.previous_target_leverage
        ),
        initialized=True,
        guard_active=guard_active,
        guard_age_days=guard_age,
    )
    return V517Decision.create(
        market=market,
        requested_leverage=requested,
        capped_desired_leverage=desired,
        selected_leverage=selected,
        runtime_leverage_cap=runtime_cap,
        drawdown_open=drawdown_open,
        guard_active=guard_active,
        guard_age_days=guard_age,
        target_changed=should_update,
        risk_reduction=risk_reduction,
        scheduled_rebalance=scheduled,
        reasons=reasons,
        next_state=next_state,
    )


def _parse_primary_targets(
    targets: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, Decimal]]:
    unsupported = set(targets) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported target sections: {sorted(unsupported)}")
    output: dict[str, dict[str, Decimal]] = {"spot": {}, "perp": {}}
    for side in ("spot", "perp"):
        raw = targets.get(side, {})
        if not isinstance(raw, Mapping):
            raise ContractError(f"target section {side!r} must be a mapping")
        for instrument, text in raw.items():
            value = require_decimal_string(text, field=f"targets.{side}.{instrument}")
            if side == "spot" and value < 0:
                raise ContractError("V517 cannot scale a negative spot target")
            if value != 0:
                output[side][str(instrument)] = value
    return output


def _scaled_target_book(
    targets: Mapping[str, Mapping[str, str]],
    leverage: Decimal,
) -> tuple[dict[str, dict[str, str]], Decimal]:
    parsed = _parse_primary_targets(targets)
    scaled: dict[str, dict[str, str]] = {"spot": {}, "perp": {}}
    gross = _ZERO
    for side in ("spot", "perp"):
        for instrument, value in sorted(parsed[side].items()):
            selected = value * leverage
            if selected != 0:
                scaled[side][instrument] = _decimal_text(selected)
            gross += selected if side == "spot" else abs(selected)
    return scaled, gross


def build_v517_shadow_snapshot(
    *,
    primary_snapshot: StrategySnapshot,
    observations: Sequence[CompletedEquityObservation],
    profile_equity: str,
    profile_high_water: str,
    runtime_state: V517RuntimeState,
    maximum_runtime_leverage: str = "2.10",
    policy: V517Policy = FROZEN_V517_POLICY,
) -> tuple[StrategySnapshot, V517Decision]:
    """Scale a sealed V75 snapshot into a separate V517 shadow snapshot.

    The source snapshot is never mutated. The result is registered as shadow-only,
    contains no submission side effect and is still subject to the downstream hard
    gross/collateral planner limits.
    """

    primary_snapshot.validate()
    if primary_snapshot.strategy_id != "v75_atlas_nx":
        raise ContractError("V517 requires a v75_atlas_nx primary snapshot")
    decision = apply_v517_policy(
        observations=observations,
        decision_time_utc=primary_snapshot.decision_time_utc,
        profile_equity=profile_equity,
        profile_high_water=profile_high_water,
        runtime_state=runtime_state,
        maximum_runtime_leverage=maximum_runtime_leverage,
        policy=policy,
    )
    leverage = require_decimal_string(
        decision.selected_leverage,
        field="selected_leverage",
        minimum=_ZERO,
        maximum=_MAX_RESEARCH_LEVERAGE,
    )
    targets, gross = _scaled_target_book(primary_snapshot.targets, leverage)
    quality_flags = list(primary_snapshot.quality_flags)
    for flag in (
        "research_non_pristine",
        "position_margin_unverified",
        "forward_validation_missing",
    ):
        if flag not in quality_flags:
            quality_flags.append(flag)
    if decision.runtime_cap_applied and "runtime_leverage_capped" not in quality_flags:
        quality_flags.append("runtime_leverage_capped")

    shadow = StrategySnapshot.create(
        strategy_id="v517_tristate_guard_shadow",
        strategy_version="runtime-v1",
        decision_time_utc=primary_snapshot.decision_time_utc,
        market_snapshot_id=primary_snapshot.market_snapshot_id,
        state_sequence=primary_snapshot.state_sequence,
        targets=targets,
        gross_target=_decimal_text(gross),
        cash_target="0",
        risk={
            "source_primary_target_hash": primary_snapshot.target_hash,
            "v517_decision_hash": decision.decision_hash,
            "source_equity_bundle_sha256": decision.market.source_bundle_sha256,
            "market_state": decision.market.state_name,
            "market_state_age_days": decision.market.state_age_days,
            "momentum20": decision.market.momentum20,
            "momentum60": decision.market.momentum60,
            "requested_research_leverage": decision.requested_leverage,
            "applied_shadow_leverage": decision.selected_leverage,
            "runtime_leverage_cap": decision.runtime_leverage_cap,
            "drawdown_guard_active": decision.guard_active,
            "position_level_margin_replay_complete": False,
            "forward_validation_complete": False,
            "live_execution_permitted": False,
        },
        reasons=decision.reasons,
        quality_flags=quality_flags,
    )
    return shadow, decision
