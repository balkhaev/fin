#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core import (
    AXES,
    AXIS_COMPONENTS,
    DEVELOPMENT_END_EXCLUSIVE,
    END_EXCLUSIVE,
    KMEANS_SEED,
    PROGRAM,
    RAW_FEATURES,
    START,
    STATE_COUNT,
    SYMBOLS,
    V9Config,
    Market,
    apply_robust_scaler,
    build_axes,
    build_features,
    canonical_hash,
    clean,
    data_gate,
    fit_robust_scaler,
    load_v9,
    segment_mask,
    write_json,
    write_manifest,
)
from state_model import (
    assign_states,
    fit_kmeans,
    label_centroids,
    occupancy_table,
    quality_report,
    squared_distances,
    state_market_diagnostics,
    transition_matrix,
)


def synthetic_market() -> tuple[Market, dict[str, pd.DataFrame], dict[str, pd.Series]]:
    index = pd.date_range("2019-01-01", periods=2800, freq="1D", tz="UTC")
    rng = np.random.default_rng(413)
    latent = np.repeat(np.arange(7), 400)[: len(index)]
    common = np.zeros(len(index))
    for number, state in enumerate(latent):
        trend = (state - 3.0) * 0.00012
        volatility = 0.006 + 0.0018 * (state % 3)
        common[number] = rng.normal(trend, volatility)

    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(SYMBOLS):
        residual_scale = 0.006 + number * 0.0002
        residual = rng.normal(0.0, residual_scale, len(index))
        returns = (0.55 + 0.02 * (number % 4)) * common + residual
        close = 100.0 * np.exp(np.cumsum(returns))
        gap = rng.normal(0.0, 0.0015, len(index))
        open_price = np.r_[close[0], close[:-1] * np.exp(gap[1:])]
        width = np.abs(rng.normal(0.012, 0.003, len(index)))
        quote = np.exp(17.0 - number * 0.12 + rng.normal(0.0, 0.18, len(index)))
        taker_share = np.clip(
            0.5 + 0.12 * np.tanh(common * 60.0)
            + rng.normal(0.0, 0.04, len(index)),
            0.05,
            0.95,
        )
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * (1.0 + width),
                "low": np.minimum(open_price, close) / (1.0 + width),
                "close": close,
                "volume": quote / np.maximum(close, 1e-12),
                "quote_volume": quote,
                "trades": np.maximum(1.0, quote / 50_000.0),
                "taker_buy_base": taker_share * quote / np.maximum(close, 1e-12),
                "taker_buy_quote": taker_share * quote,
            },
            index=index,
        )
        funding[symbol] = pd.Series(
            0.00002 * np.tanh(common * 80.0)
            + rng.normal(0.0, 1e-5, len(index)),
            index=index,
        )
    return Market(klines, funding), klines, funding


def self_test() -> None:
    market, klines, funding = synthetic_market()
    features = build_features(market, klines)
    assert tuple(features.columns) == RAW_FEATURES
    assert features.notna().mean().mean() > 0.90

    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    future_date = changed[SYMBOLS[0]].index[-1]
    assert future_date >= pd.Timestamp(END_EXCLUSIVE, tz="UTC")
    changed[SYMBOLS[0]].loc[future_date, "close"] *= 4.0
    changed_market = Market(changed, funding)
    changed_features = build_features(changed_market, changed)
    pd.testing.assert_frame_equal(features, changed_features)

    dev_mask = segment_mask(features.index, START, DEVELOPMENT_END_EXCLUSIVE)
    feature_scaler = fit_robust_scaler(features, dev_mask)
    standardized = apply_robust_scaler(features, feature_scaler)
    raw_axes = build_axes(standardized)
    axis_scaler = fit_robust_scaler(raw_axes, dev_mask)
    state_axes = apply_robust_scaler(raw_axes, axis_scaler)
    fit_values = state_axes.loc[dev_mask].dropna().to_numpy(float)

    labels_1, centroids_1, inertia_1 = fit_kmeans(fit_values, KMEANS_SEED)
    labels_2, centroids_2, inertia_2 = fit_kmeans(fit_values, KMEANS_SEED)
    np.testing.assert_array_equal(labels_1, labels_2)
    np.testing.assert_allclose(centroids_1, centroids_2)
    assert abs(inertia_1 - inertia_2) < 1e-9
    assert len(np.unique(labels_1)) == STATE_COUNT
    print("V413-V420 market-state observatory self-test passed")


def data_failure(root: Path, coverage: dict[str, Any]) -> int:
    results = root / "results"
    decision = {
        "program": PROGRAM,
        "status": "data_access_insufficient",
        "observatory_ready_for_forward_attribution": False,
        "market_state_model_is_trading_signal": False,
        "strategy_parameter_changes_permitted": False,
        "allocation_changes_permitted": False,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "state_quality.json", {"passed": False, "coverage": coverage})
    (results / "REPORT_RU.md").write_text(
        "# V413–V420 — market-state observatory\n\n"
        "Status: `data_access_insufficient`. State model was not fitted.\n",
        encoding="utf-8",
    )
    write_manifest(root)
    return 0


def run(root: Path, cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=10_000.0,
        max_gross=0.0,
        forced_exit_penalty_bps=100.0,
    )
    klines, funding, records, data_quality = load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(data_quality).to_csv(results / "data_quality.csv", index=False)
    coverage = data_gate(klines, records)
    write_json(results / "coverage_gate.json", coverage)
    if not coverage["passed"]:
        return data_failure(root, coverage)

    market = Market(klines, funding)
    features = build_features(market, klines)
    features.to_csv(results / "feature_panel.csv")
    dev_mask = segment_mask(features.index, START, DEVELOPMENT_END_EXCLUSIVE)

    feature_scaler = fit_robust_scaler(features, dev_mask)
    standardized = apply_robust_scaler(features, feature_scaler)
    standardized.to_csv(results / "standardized_feature_panel.csv")

    raw_axes = build_axes(standardized)
    axis_scaler = fit_robust_scaler(raw_axes, dev_mask)
    state_axes = apply_robust_scaler(raw_axes, axis_scaler)
    state_axes.to_csv(results / "market_state_axes.csv")

    development_axes = state_axes.loc[dev_mask].dropna()
    development_labels, centroid_values, inertia = fit_kmeans(
        development_axes.to_numpy(float), KMEANS_SEED
    )
    centroid_axes = pd.DataFrame(
        centroid_values,
        columns=state_axes.columns,
        index=pd.Index(range(STATE_COUNT), name="state_id"),
    )
    state_labels = label_centroids(centroid_axes)
    centroid_output = centroid_axes.copy()
    centroid_output.insert(
        0, "state_label", [state_labels[state] for state in centroid_output.index]
    )
    centroid_output.to_csv(results / "state_centroids.csv")

    development_state = pd.Series(
        development_labels, index=development_axes.index, name="state_id"
    )
    transitions = transition_matrix(development_state)
    transition_frame = pd.DataFrame(
        transitions,
        index=[state_labels[state] for state in range(STATE_COUNT)],
        columns=[state_labels[state] for state in range(STATE_COUNT)],
    )
    transition_frame.to_csv(results / "state_transition_matrix.csv")

    development_distance = np.sqrt(
        squared_distances(development_axes.to_numpy(float), centroid_values).min(axis=1)
    )
    development_distance_q95 = float(np.quantile(development_distance, 0.95))
    state_daily = assign_states(
        state_axes,
        centroid_axes,
        development_distance_q95,
        transitions,
        state_labels,
    )
    state_daily.to_csv(results / "market_state_daily.csv")

    occupancy = occupancy_table(state_daily)
    occupancy.to_csv(results / "state_occupancy_by_period.csv", index=False)
    diagnostics = state_market_diagnostics(state_daily, market)
    diagnostics.to_csv(results / "state_market_diagnostics.csv", index=False)

    quality = quality_report(features, state_daily, centroid_axes, transitions)
    quality["program"] = PROGRAM
    write_json(results / "state_quality.json", quality)

    current = state_daily.dropna(subset=["state_id"]).tail(1)
    current_state = None
    if len(current):
        current_state = {"date": current.index[0], **current.iloc[0].to_dict()}

    model = {
        "program": PROGRAM,
        "fit_period": [START, DEVELOPMENT_END_EXCLUSIVE],
        "state_count": STATE_COUNT,
        "random_seed": KMEANS_SEED,
        "raw_features": list(RAW_FEATURES),
        "axis_components": AXIS_COMPONENTS,
        "feature_scaler": feature_scaler,
        "axis_scaler": axis_scaler,
        "centroids": {
            str(state): {
                "label": state_labels[state],
                **centroid_axes.loc[state].to_dict(),
            }
            for state in range(STATE_COUNT)
        },
        "development_inertia": inertia,
        "development_nearest_distance_q95": development_distance_q95,
        "transition_matrix": transition_frame.to_dict(orient="index"),
        "current_state": current_state,
        "selection_uses_strategy_returns": False,
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "oos_used_only_for_stability_and_novelty_diagnostics": True,
    }
    model["model_sha256"] = canonical_hash(model)
    write_json(results / "STATE_MODEL.json", model)

    decision = {
        "program": PROGRAM,
        "status": (
            "observatory_ready_for_forward_attribution"
            if quality["passed"]
            else "observatory_materialized_with_quality_warnings"
        ),
        "observatory_ready_for_forward_attribution": bool(quality["passed"]),
        "market_state_model_is_trading_signal": False,
        "strategy_parameter_changes_permitted": False,
        "allocation_changes_permitted": False,
        "historical_parameter_search_closed": True,
        "primary_champion": "V75_ATLAS_NX",
        "execution_shadow": "V136_EXECUTION_PLATEAU",
        "mandatory_control": "V28_GROWTH_CONTROL",
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    write_json(results / "FROZEN_DECISION.json", decision)

    report = f"""# V413–V420 — market-state observatory

Status: `{decision['status']}`.

The model is a causal description and attribution layer, not a trading signal.

## Frozen representation

- {len(RAW_FEATURES)} completed-data features;
- {len(AXES)} interpretable axes: {', '.join(AXES)};
- robust scalers fitted only on 2021–2023;
- deterministic {STATE_COUNT}-state codebook fitted only on 2021–2023;
- 2024, 2025 and 2026 H1 used only for state stability and novelty diagnostics;
- no V75/V136 returns, thresholds or allocations used to fit the state model.

## Technical quality

```text
passed                         {quality['passed']}
development assignment days    {quality['development_assignment_days']}
OOS assignment days            {quality['oos_assignment_days']}
min development occupancy      {quality['development_min_state_occupancy']:.2%}
max development occupancy      {quality['development_max_state_occupancy']:.2%}
minimum centroid distance      {quality['minimum_centroid_distance']:.3f}
OOS novelty rate               {quality['oos_novelty_rate']:.2%}
OOS mean confidence            {quality['oos_mean_assignment_confidence']:.3f}
```

## Authorized use

The observatory may join future paper telemetry for V75, V136 and V28 to explain:

- return and drawdown by market state;
- turnover and slippage by market state;
- reconciliation failures and stale-data exposure;
- state transitions preceding execution stress.

It may not change strategy parameters or capital allocation from historical diagnostics.

```text
strategy_parameter_changes_permitted = false
allocation_changes_permitted         = false
live_ready                           = false
real_leverage_authorized             = false
```
"""
    (results / "REPORT_RU.md").write_text(report, encoding="utf-8")
    write_manifest(root)
    print(json.dumps(clean({"decision": decision, "quality": quality}), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument("--cache", type=Path, default=Path(".cache/v9"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
