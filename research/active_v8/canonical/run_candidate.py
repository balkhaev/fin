#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import (
    FORCED_DELISTING_PENALTY_BPS,
    PERIODS,
    RATCHET,
    SCENARIOS,
    TARGET_GROSS_CAP,
    V7_HEDGE_COMPONENTS,
    V8_COMPONENTS,
    V8_OVERLAY_SCALE,
)
from engine import SimulationSettings, metrics, rolling_diagnostics, simulate
from inputs import load
from signals import build_combined_hedge, build_v7_hedge, build_v8_relative


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frozen Active V8 candidate")
    parser.add_argument("--v6", type=Path, required=False)
    parser.add_argument("--v5", type=Path, required=False)
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def self_test() -> None:
    index = pd.date_range("2020-01-01", periods=500, freq="1D", tz="UTC")
    btc = pd.Series(np.exp(np.linspace(8.0, 9.0, len(index))), index=index)
    eth = pd.Series(np.exp(np.linspace(6.0, 7.3, len(index))), index=index)
    close = pd.DataFrame({"BTCUSDT": btc, "ETHUSDT": eth})
    signal = build_v8_relative(close)
    assert signal.index.equals(index)
    assert list(signal.columns) == ["BTCUSDT", "ETHUSDT"]
    assert np.isfinite(signal.to_numpy()).all()
    assert float(signal.abs().sum(axis=1).max()) <= 1.000001
    perturbed = close.copy()
    perturbed.iloc[-1, 1] *= 10.0
    second = build_v8_relative(perturbed)
    pd.testing.assert_frame_equal(signal.iloc[:-1], second.iloc[:-1])
    print("self-test passed")


def block_bootstrap(
    return_series: pd.Series,
    *,
    block: int,
    horizon: int = 365,
    simulations: int = 10_000,
    seed: int = 20260724,
) -> dict[str, float]:
    values = np.asarray(return_series.dropna(), dtype=float)
    if len(values) < block:
        raise ValueError("return series shorter than bootstrap block")
    rng = np.random.default_rng(seed + block + horizon)
    block_count = int(np.ceil(horizon / block))
    starts = rng.integers(0, len(values) - block + 1, size=(simulations, block_count))
    paths = np.empty((simulations, block_count * block), dtype=float)
    offsets = np.arange(block)
    for number in range(block_count):
        paths[:, number * block : (number + 1) * block] = values[
            starts[:, number, None] + offsets
        ]
    paths = paths[:, :horizon]
    equity = np.cumprod(1.0 + paths, axis=1)
    terminal = equity[:, -1] - 1.0
    drawdown = equity / np.maximum.accumulate(equity, axis=1) - 1.0

    def probability_hit_before(up: float, down: float) -> float:
        up_hit = equity >= 1.0 + up
        down_hit = equity <= 1.0 - down
        up_first = np.where(up_hit.any(axis=1), up_hit.argmax(axis=1), horizon + 1)
        down_first = np.where(down_hit.any(axis=1), down_hit.argmax(axis=1), horizon + 1)
        return float((up_first < down_first).mean())

    return {
        "block_days": float(block),
        "horizon_days": float(horizon),
        "simulations": float(simulations),
        "positive_probability": float((terminal > 0.0).mean()),
        "greater_than_10_probability": float((terminal > 0.10).mean()),
        "greater_than_20_probability": float((terminal > 0.20).mean()),
        "median_return": float(np.median(terminal)),
        "p05_return": float(np.quantile(terminal, 0.05)),
        "p95_return": float(np.quantile(terminal, 0.95)),
        "median_max_drawdown": float(np.median(drawdown.min(axis=1))),
        "p05_max_drawdown": float(np.quantile(drawdown.min(axis=1), 0.05)),
        "p20_before_m10_probability": probability_hit_before(0.20, 0.10),
        "p50_before_m20_probability": probability_hit_before(0.50, 0.20),
    }


def main() -> int:
    parsed = arguments()
    if parsed.self_test:
        self_test()
        return 0
    if parsed.v6 is None or parsed.v5 is None:
        raise SystemExit("--v6 and --v5 are required unless --self-test is used")

    output = parsed.output
    output.mkdir(parents=True, exist_ok=True)

    data, spot_signal, perp_open, perp_close, funding = load(parsed.v6, parsed.v5)
    v7_hedge = build_v7_hedge(perp_close, funding, spot_signal)
    v8_relative = build_v8_relative(perp_close)
    combined_perp = build_combined_hedge(v7_hedge, v8_relative)

    spot_signal.to_csv(output / "spot_signal.csv")
    v7_hedge.to_csv(output / "v7_hedge_signal.csv")
    v8_relative.to_csv(output / "v8_relative_signal.csv")
    combined_perp.to_csv(output / "combined_perp_signal.csv")

    zero_spot = spot_signal * 0.0
    rows: list[dict[str, object]] = []
    saved_accounts: dict[tuple[str, str, str], pd.DataFrame] = {}

    for scenario, costs in SCENARIOS.items():
        spot_cost = float(costs["spot_cost_bps"]) / 10_000.0
        perp_cost = float(costs["perp_cost_bps"]) / 10_000.0
        for period, (start, end) in PERIODS.items():
            specifications = (
                (
                    "v7_baseline",
                    spot_signal,
                    v7_hedge,
                    SimulationSettings(target_gross_cap=1.0),
                ),
                (
                    "v8_relative_standalone",
                    zero_spot,
                    v8_relative,
                    SimulationSettings(target_gross_cap=1.0),
                ),
                (
                    "v8_v7_fixed",
                    spot_signal,
                    combined_perp,
                    SimulationSettings(target_gross_cap=TARGET_GROSS_CAP),
                ),
                (
                    "v8_v7_ratchet",
                    spot_signal,
                    combined_perp,
                    SimulationSettings(
                        target_gross_cap=TARGET_GROSS_CAP,
                        initial_scale=float(RATCHET["initial_scale"]),
                        first_high_water_multiple=float(RATCHET["first_high_water_multiple"]),
                        first_reduced_scale=float(RATCHET["first_reduced_scale"]),
                        second_high_water_multiple=float(RATCHET["second_high_water_multiple"]),
                        second_reduced_scale=float(RATCHET["second_reduced_scale"]),
                        ratchet=True,
                    ),
                ),
            )
            for candidate, candidate_spot, candidate_perp, settings in specifications:
                account = simulate(
                    data,
                    candidate_spot,
                    perp_open,
                    perp_close,
                    funding,
                    candidate_perp,
                    start,
                    end,
                    spot_cost_rate=spot_cost,
                    perp_cost_rate=perp_cost,
                    forced_penalty_rate=FORCED_DELISTING_PENALTY_BPS / 10_000.0,
                    settings=settings,
                )
                values = {**metrics(account), **rolling_diagnostics(account)}
                rows.append(
                    {
                        "candidate": candidate,
                        "scenario": scenario,
                        "period": period,
                        **values,
                    }
                )
                if period in {"full", "final_2026h1"}:
                    saved_accounts[(candidate, scenario, period)] = account
                    account.to_csv(
                        output / f"{candidate}_{scenario}_{period}_equity.csv"
                    )

    metric_frame = pd.DataFrame(rows)
    metric_frame.to_csv(output / "metrics.csv", index=False)

    stress_full = saved_accounts[("v8_v7_ratchet", "stress", "full")]
    bootstrap = pd.DataFrame(
        [
            block_bootstrap(stress_full["equity"].pct_change(), block=block)
            for block in (14, 30, 60)
        ]
    )
    bootstrap.to_csv(output / "block_bootstrap.csv", index=False)

    stress_final = metric_frame[
        (metric_frame["candidate"] == "v8_v7_ratchet")
        & (metric_frame["scenario"] == "stress")
        & (metric_frame["period"] == "final_2026h1")
    ].iloc[0]
    stress_all = metric_frame[
        (metric_frame["candidate"] == "v8_v7_ratchet")
        & (metric_frame["scenario"] == "stress")
        & (metric_frame["period"] == "full")
    ].iloc[0]
    severe_validation_a = metric_frame[
        (metric_frame["candidate"] == "v8_v7_ratchet")
        & (metric_frame["scenario"] == "severe")
        & (metric_frame["period"] == "validation_a")
    ].iloc[0]

    status = "frozen_paper_forward_candidate" if (
        stress_all["total_return"] > 1.0
        and stress_all["max_drawdown"] > -0.30
        and stress_final["total_return"] > 0.0
        and stress_final["max_drawdown"] > -0.15
        and stress_all["max_gross"] < 1.0
    ) else "rejected_or_needs_iteration"

    summary = {
        "status": status,
        "candidate": "V8_V7_RATCHET_SCALE_0_4_CAP_0_85",
        "selection_excludes_2026h1_parameters": True,
        "program_level_2026h1_is_pristine": False,
        "target_gross_cap": TARGET_GROSS_CAP,
        "v8_overlay_scale": V8_OVERLAY_SCALE,
        "stress_full_return": float(stress_all["total_return"]),
        "stress_full_cagr": float(stress_all["annualized_return"]),
        "stress_full_max_drawdown": float(stress_all["max_drawdown"]),
        "stress_full_sharpe": float(stress_all["sharpe"]),
        "stress_full_max_gross": float(stress_all["max_gross"]),
        "stress_final_2026h1_return": float(stress_final["total_return"]),
        "stress_final_2026h1_max_drawdown": float(stress_final["max_drawdown"]),
        "severe_validation_a_return": float(severe_validation_a["total_return"]),
        "important_limit": "Severe-cost 2023 is negative; candidate is paper-forward only.",
        "v7_hedge_components": list(V7_HEDGE_COMPONENTS),
        "v8_components": list(V8_COMPONENTS),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output / "frozen_candidate.json").write_text(
        json.dumps(
            {
                "candidate": summary["candidate"],
                "status": status,
                "spot_signal": "frozen V6 momentum family ensemble",
                "v7_hedge_components": list(V7_HEDGE_COMPONENTS),
                "v8_relative_components": list(V8_COMPONENTS),
                "v8_overlay_scale": V8_OVERLAY_SCALE,
                "target_gross_cap": TARGET_GROSS_CAP,
                "ratchet": RATCHET,
                "cost_scenarios_bps_per_side": SCENARIOS,
                "forced_delisting_penalty_bps": FORCED_DELISTING_PENALTY_BPS,
                "periods": PERIODS,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    provenance = {
        "source": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in sorted(ROOT.glob("*.py"))
        },
        "outputs": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "provenance.json"
        },
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
