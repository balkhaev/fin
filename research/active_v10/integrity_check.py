#!/usr/bin/env python3
"""Deterministic integrity checks for the frozen Active V10 allocation.

This test does not re-optimize any market parameter. It verifies the frozen
selection contract, the separate-account accounting identity, and a simple
no-look-ahead invariant on synthetic daily returns.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "selection_manifest.json").read_text(encoding="utf-8"))


def separate_accounts(
    v8_returns: np.ndarray,
    v4_returns: np.ndarray,
    v8_weight: float,
    v4_weight: float,
) -> np.ndarray:
    """Return total equity when sleeves compound independently.

    No capital is transferred between the sleeves after inception. This is the
    operational V10 definition; it is intentionally not a daily rebalanced mix.
    """
    if v8_returns.shape != v4_returns.shape:
        raise ValueError("sleeve return arrays must have equal shapes")
    if min(v8_weight, v4_weight) < 0 or not np.isclose(v8_weight + v4_weight, 1.0):
        raise ValueError("invalid frozen capital weights")
    v8_equity = v8_weight * np.cumprod(1.0 + v8_returns)
    v4_equity = v4_weight * np.cumprod(1.0 + v4_returns)
    return v8_equity + v4_equity


def main() -> int:
    construction = MANIFEST["construction"]
    selection = MANIFEST["selection"]
    checks = MANIFEST["headline_checks"]

    assert MANIFEST["candidate_id"] == "V10_SEPARATE_ACCOUNTS_V8_80_V4_20"
    assert MANIFEST["status"] == "frozen_paper_forward_candidate"
    assert construction["accounts_are_separate"] is True
    assert construction["capital_is_not_rebalanced_between_sleeves"] is True
    assert construction["leverage"] is False
    assert np.isclose(
        construction["v8_growth_weight"] + construction["v4_defensive_weight"],
        1.0,
    )
    assert np.isclose(construction["v8_growth_weight"], 0.8)
    assert np.isclose(construction["v4_defensive_weight"], 0.2)

    # Selection chronology is immutable: 2026 H1 is evaluation only.
    assert selection["selection_end_exclusive"] <= selection["final_start"]
    assert selection["final_used_for_selection"] is False
    assert selection["candidate_v4_weights"] == [0.2, 0.3, 0.4, 0.5]
    assert selection["costs_bps_per_side"] == [40, 80]

    # Headline acceptance checks recorded before paper-forward deployment.
    assert checks["stress_v10_max_drawdown"] > checks["stress_v8_max_drawdown"]
    assert checks["severe_v10_max_drawdown"] > checks["severe_v8_max_drawdown"]
    assert checks["stress_v10_max_drawdown"] - checks["stress_v8_max_drawdown"] >= 0.02
    assert checks["severe_v10_max_drawdown"] - checks["severe_v8_max_drawdown"] >= 0.02
    assert abs(checks["stress_v10_sharpe"] - checks["stress_v8_sharpe"]) <= 0.01
    assert checks["paired_bootstrap_probability_v10_lower_drawdown_min"] >= 0.90

    rng = np.random.default_rng(20260724)
    v8 = rng.normal(0.0006, 0.022, 800)
    v4 = rng.normal(0.00025, 0.008, 800)
    equity = separate_accounts(v8, v4, 0.8, 0.2)
    assert len(equity) == 800
    assert np.isfinite(equity).all()
    assert (equity > 0).all()

    # No-look-ahead check: changing the final observation may only change the
    # final equity value, never the already-computed history.
    changed = v8.copy()
    changed[-1] += 0.50
    second = separate_accounts(changed, v4, 0.8, 0.2)
    np.testing.assert_allclose(equity[:-1], second[:-1], rtol=0.0, atol=0.0)
    assert not np.isclose(equity[-1], second[-1])

    # Separate-account identity against independently compounded sleeves.
    expected = 0.8 * np.cumprod(1.0 + v8) + 0.2 * np.cumprod(1.0 + v4)
    np.testing.assert_allclose(equity, expected, rtol=1e-13, atol=1e-13)

    print("Active V10 frozen-allocation integrity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
