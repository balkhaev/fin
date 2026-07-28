from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from finruntime.canonical import ContractError, require_decimal_string
from finruntime.models import StrategySnapshot

TargetBook = Mapping[str, Mapping[str, str]]
_ZERO = Decimal("0")
_SIGN_EPSILON = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class V136Policy:
    l1_no_trade_band: Decimal
    maximum_target_age_days: int
    step_fraction: Decimal
    risk_reduction_buffer: Decimal

    def validate(self) -> None:
        if self.l1_no_trade_band < 0:
            raise ContractError("V136 band must be non-negative")
        if self.maximum_target_age_days < 1:
            raise ContractError("V136 maximum target age must be positive")
        if not (_ZERO < self.step_fraction <= Decimal("1")):
            raise ContractError("V136 step fraction must be in (0, 1]")
        if self.risk_reduction_buffer < 0:
            raise ContractError("V136 risk-reduction buffer must be non-negative")


FROZEN_V136_POLICY = V136Policy(
    l1_no_trade_band=Decimal("0.08"),
    maximum_target_age_days=28,
    step_fraction=Decimal("1.00"),
    risk_reduction_buffer=Decimal("0.02"),
)


@dataclass(frozen=True, slots=True)
class V136Decision:
    targets: dict[str, dict[str, str]]
    target_age_days: int
    target_changed: bool
    force_reduce: bool
    reasons: tuple[str, ...]
    l1_change: str
    desired_gross: str
    held_gross: str
    perp_sign_change_instruments: tuple[str, ...]


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text if text != "-0" else "0"


def _parse_side(
    book: TargetBook,
    side: str,
    *,
    permit_negative: bool,
) -> dict[str, Decimal]:
    raw = book.get(side, {})
    if not isinstance(raw, Mapping):
        raise ContractError(f"target book {side!r} must be a mapping")
    output: dict[str, Decimal] = {}
    for instrument, text in raw.items():
        if not instrument:
            raise ContractError("target instrument cannot be empty")
        value = require_decimal_string(text, field=f"targets.{side}.{instrument}")
        if not permit_negative and value < 0:
            raise ContractError("spot target weights cannot be negative")
        if value != 0:
            output[str(instrument)] = value
    return output


def _parse_book(book: TargetBook) -> dict[str, dict[str, Decimal]]:
    unsupported = set(book) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported target-book sections: {sorted(unsupported)}")
    return {
        "spot": _parse_side(book, "spot", permit_negative=False),
        "perp": _parse_side(book, "perp", permit_negative=True),
    }


def _canonical_book(
    book: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, dict[str, str]]:
    return {
        side: {
            instrument: _decimal_text(value)
            for instrument, value in sorted(values.items())
            if value != 0
        }
        for side, values in (
            ("spot", book.get("spot", {})),
            ("perp", book.get("perp", {})),
        )
    }


def _sign(value: Decimal) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _gross(book: Mapping[str, Mapping[str, Decimal]]) -> Decimal:
    # Exact V136 research semantics: spot weights are long-only and summed;
    # perpetual weights contribute absolute gross.
    return sum(book["spot"].values(), _ZERO) + sum(
        (abs(value) for value in book["perp"].values()), _ZERO
    )


def _l1_change(
    desired: Mapping[str, Mapping[str, Decimal]],
    held: Mapping[str, Mapping[str, Decimal]],
) -> Decimal:
    total = _ZERO
    for side in ("spot", "perp"):
        instruments = set(desired[side]) | set(held[side])
        total += sum(
            (
                abs(
                    desired[side].get(instrument, _ZERO)
                    - held[side].get(instrument, _ZERO)
                )
                for instrument in instruments
            ),
            _ZERO,
        )
    return total


def _step_book(
    desired: Mapping[str, Mapping[str, Decimal]],
    held: Mapping[str, Mapping[str, Decimal]],
    fraction: Decimal,
) -> dict[str, dict[str, Decimal]]:
    output: dict[str, dict[str, Decimal]] = {"spot": {}, "perp": {}}
    for side in ("spot", "perp"):
        for instrument in sorted(set(desired[side]) | set(held[side])):
            old = held[side].get(instrument, _ZERO)
            new = desired[side].get(instrument, _ZERO)
            stepped = old + fraction * (new - old)
            if stepped != 0:
                output[side][instrument] = stepped
    return output


def apply_v136_policy(
    *,
    desired_targets: TargetBook,
    held_targets: TargetBook,
    target_age_days: int,
    state_initialized: bool,
    policy: V136Policy = FROZEN_V136_POLICY,
) -> V136Decision:
    """Apply the exact frozen V136 no-trade state machine.

    The function is pure: it does not mutate the V75 primary target book or runtime
    state. Availability, gross-cap and margin feasibility belong to later risk and
    planner layers, matching the separation in the frozen research implementation.
    """

    policy.validate()
    if target_age_days < 0:
        raise ContractError("target_age_days must be non-negative")
    desired = _parse_book(desired_targets)
    held = _parse_book(held_targets)
    change = _l1_change(desired, held)
    desired_gross = _gross(desired)
    held_gross = _gross(held)

    perp_instruments = tuple(sorted(set(desired["perp"]) | set(held["perp"])))
    sign_changes = tuple(
        instrument
        for instrument in perp_instruments
        if _sign(desired["perp"].get(instrument, _ZERO))
        != _sign(held["perp"].get(instrument, _ZERO))
        and abs(
            desired["perp"].get(instrument, _ZERO)
            - held["perp"].get(instrument, _ZERO)
        )
        > _SIGN_EPSILON
    )

    gross_reduction = desired_gross < held_gross - policy.risk_reduction_buffer
    global_zero_exit = desired_gross == 0 and held_gross > 0
    force_reduce = gross_reduction or global_zero_exit or bool(sign_changes)

    reasons: list[str] = []
    if not state_initialized:
        reasons.append("initialization")
    if gross_reduction:
        reasons.append("gross_risk_reduction")
    if global_zero_exit:
        reasons.append("global_zero_exit")
    if sign_changes:
        reasons.append("perp_sign_change")
    if target_age_days >= policy.maximum_target_age_days:
        reasons.append("maximum_target_age")
    if change >= policy.l1_no_trade_band:
        reasons.append("l1_band_exceeded")

    should_update = bool(
        not state_initialized
        or force_reduce
        or target_age_days >= policy.maximum_target_age_days
        or change >= policy.l1_no_trade_band
    )
    if should_update:
        selected = (
            desired
            if force_reduce or policy.step_fraction >= Decimal("0.999")
            else _step_book(desired, held, policy.step_fraction)
        )
        next_age = 0
    else:
        selected = {"spot": dict(held["spot"]), "perp": dict(held["perp"])}
        next_age = target_age_days + 1
        reasons.append("inside_no_trade_region")

    return V136Decision(
        targets=_canonical_book(selected),
        target_age_days=next_age,
        target_changed=should_update,
        force_reduce=force_reduce,
        reasons=tuple(reasons),
        l1_change=_decimal_text(change),
        desired_gross=_decimal_text(desired_gross),
        held_gross=_decimal_text(held_gross),
        perp_sign_change_instruments=sign_changes,
    )


def build_v136_shadow_snapshot(
    *,
    primary_snapshot: StrategySnapshot,
    held_targets: TargetBook,
    target_age_days: int,
    state_initialized: bool,
    cash_target: str,
    policy: V136Policy = FROZEN_V136_POLICY,
) -> tuple[StrategySnapshot, V136Decision]:
    """Build a separate shadow snapshot without modifying V75 primary output."""

    primary_snapshot.validate()
    if primary_snapshot.strategy_id != "v75_atlas_nx":
        raise ContractError("V136 shadow requires a v75_atlas_nx primary snapshot")
    require_decimal_string(cash_target, field="cash_target", minimum=Decimal("0"))
    decision = apply_v136_policy(
        desired_targets=primary_snapshot.targets,
        held_targets=held_targets,
        target_age_days=target_age_days,
        state_initialized=state_initialized,
        policy=policy,
    )
    shadow = StrategySnapshot.create(
        strategy_id="v136_execution_shadow",
        strategy_version="runtime-v1",
        decision_time_utc=primary_snapshot.decision_time_utc,
        market_snapshot_id=primary_snapshot.market_snapshot_id,
        state_sequence=primary_snapshot.state_sequence,
        targets=decision.targets,
        gross_target=(
            decision.desired_gross if decision.target_changed else decision.held_gross
        ),
        cash_target=cash_target,
        risk={
            "source_primary_target_hash": primary_snapshot.target_hash,
            "l1_no_trade_band": _decimal_text(policy.l1_no_trade_band),
            "maximum_target_age_days": policy.maximum_target_age_days,
            "step_fraction": _decimal_text(policy.step_fraction),
            "risk_reduction_buffer": _decimal_text(policy.risk_reduction_buffer),
            "target_age_days": decision.target_age_days,
            "force_reduce": decision.force_reduce,
        },
        reasons=decision.reasons,
        quality_flags=primary_snapshot.quality_flags,
    )
    return shadow, decision
