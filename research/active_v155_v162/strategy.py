from __future__ import annotations

import numpy as np
import pandas as pd

from config import Policy


def _valid_contract(value, columns: pd.Index) -> bool:
    return pd.notna(value) and value in columns


def target_weights(policy: Policy, market) -> pd.DataFrame:
    """Build causal close-t targets; the shared engine executes at open t+1."""

    result = pd.DataFrame(0.0, index=market.index, columns=market.expiries)
    state = "flat"
    state_age = 0
    calm_streak = 0

    for day in market.index:
        row = market.features.loc[day]
        front = market.front.at[day]
        second = market.second.at[day]
        has_front = _valid_contract(front, result.columns)
        has_second = _valid_contract(second, result.columns)
        if not has_front:
            state = "flat"
            state_age = calm_streak = 0
            continue

        finite = all(
            np.isfinite(value)
            for value in (
                row.curve,
                row.spot,
                row.spot_ema10,
                row.spot_ema20,
                row.spot_ret5,
                row.front_mom5,
            )
        )
        if not finite:
            state = "flat"
            state_age = calm_streak = 0
            continue

        crisis = bool(
            row.curve >= policy.crisis_threshold
            or row.spot_ret5 >= policy.spike_threshold
            or (
                row.spot > row.spot_ema10
                and row.front_mom5 > 0.05
            )
        )
        calm = bool(
            has_second
            and row.curve <= -policy.contango_threshold
            and row.spot < row.spot_ema20
            and row.spot_ret5 < policy.spike_threshold / 2.0
            and row.front_mom5 <= 0.02
        )
        calm_streak = calm_streak + 1 if calm else 0

        if crisis:
            if policy.family == "carry_only":
                state = "flat"
                state_age = 0
            else:
                state = "convex"
                state_age = 1
        elif state == "convex":
            state_age += 1
            safe_exit = bool(row.curve < 0 and row.spot_ret5 < 0 and row.front_mom5 < 0)
            if state_age >= policy.hold_days and safe_exit:
                state = "flat"
                state_age = 0
        elif state == "carry":
            state_age += 1
            if not calm and state_age >= policy.hold_days:
                state = "flat"
                state_age = 0
        elif calm_streak >= policy.confirm_days:
            state = "carry"
            state_age = 1

        if state == "carry" and has_second and policy.carry_budget > 0:
            result.at[day, front] = -policy.carry_budget / 2.0
            result.at[day, second] = policy.carry_budget / 2.0
        elif state == "convex" and policy.convex_budget > 0:
            result.at[day, front] = policy.convex_budget

    return result


def synthetic_overnight_audit(target: pd.DataFrame, market) -> dict[str, float]:
    """Apply two deliberately severe one-session shocks before any exit.

    Up shock: front +100%, second +50%.
    Down shock: front -50%, second -35%.
    The target is a fraction of equity, so weighted shock P&L is also a fraction
    of equity. This is not a VaR estimate; it is an admission gate.
    """

    up_losses = []
    down_losses = []
    for day in target.index:
        front = market.front.at[day]
        second = market.second.at[day]
        weights = target.loc[day]
        up = 0.0
        down = 0.0
        if _valid_contract(front, target.columns):
            weight = float(weights.get(front, 0.0))
            up += weight * 1.00
            down += weight * -0.50
        if _valid_contract(second, target.columns):
            weight = float(weights.get(second, 0.0))
            up += weight * 0.50
            down += weight * -0.35
        up_losses.append(up)
        down_losses.append(down)
    combined = np.asarray([*up_losses, *down_losses], dtype=float)
    return {
        "worst_up_shock_return": float(min(up_losses)) if up_losses else 0.0,
        "worst_down_shock_return": float(min(down_losses)) if down_losses else 0.0,
        "worst_synthetic_overnight_return": float(combined.min()) if len(combined) else 0.0,
        "max_synthetic_gain": float(combined.max()) if len(combined) else 0.0,
    }


def self_test(market_factory) -> None:
    market = market_factory()
    policy = Policy("test", "carry_convex_switch", 0.08, 0.08, 0.02, 0.0, 0.10, 2, 3)
    first = target_weights(policy, market)
    assert len(first) == len(market.index)
    assert float(first.abs().sum(axis=1).max()) <= 0.080000001
    changed = market_factory()
    changed.features.iloc[-1, changed.features.columns.get_loc("spot_ret5")] = 10.0
    second = target_weights(policy, changed)
    pd.testing.assert_frame_equal(first.iloc[:-1], second.iloc[:-1])
    audit = synthetic_overnight_audit(first, market)
    assert audit["worst_synthetic_overnight_return"] >= -0.08
