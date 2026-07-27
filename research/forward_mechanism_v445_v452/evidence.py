from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from common import CHAMPION, SHADOW, strategy_metrics


def context_masks(joined: pd.DataFrame, thresholds: dict[str, float]) -> dict[str, pd.Series]:
    duration = pd.to_numeric(joined["state_duration_days"], errors="coerce")
    surprise = pd.to_numeric(joined["transition_surprise"], errors="coerce")
    switching = pd.to_numeric(joined["switching_rate_20d"], errors="coerce")
    return {
        "all": pd.Series(True, index=joined.index),
        "early_state": duration <= 2,
        "intermediate_state": (duration >= 3) & (duration <= 5),
        "persistent_state": duration > 5,
        "novel": joined["novelty_flag"].astype(bool),
        "familiar": ~joined["novelty_flag"].astype(bool),
        "high_transition_surprise": surprise >= thresholds["high_transition_surprise"],
        "high_switching": switching >= thresholds["high_switching_rate"],
        "low_switching": switching < thresholds["high_switching_rate"],
    }


def paired_context(joined: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    selected = joined.loc[mask & joined["strategy_id"].isin((CHAMPION, SHADOW))].copy()
    fields = [
        "net_return", "turnover", "modelled_slippage_bps", "paper_slippage_bps",
        "drawdown", "target_changed", "reconciliation_ok", "source_hash_match",
        "data_stale", "execution_complete",
    ]
    wide = selected.pivot(index="date", columns="strategy_id", values=fields)
    required = pd.MultiIndex.from_product([fields, [CHAMPION, SHADOW]])
    if not required.isin(wide.columns).all():
        return pd.DataFrame()
    return wide.loc[:, required].dropna().sort_index()


def paired_point_metrics(wide: pd.DataFrame) -> dict[str, Any]:
    if wide.empty:
        empty = strategy_metrics(pd.DataFrame())
        return {
            "paired_days": 0, "v75": empty, "v136": empty,
            "turnover_reduction": None, "net_return_delta": None,
            "max_drawdown_worsening": None,
            "v136_slippage_to_model_ratio": None,
        }

    def metrics_for(strategy: str) -> dict[str, Any]:
        group = pd.DataFrame(index=wide.index)
        for field in (
            "net_return", "turnover", "modelled_slippage_bps", "paper_slippage_bps",
            "drawdown", "target_changed", "reconciliation_ok", "source_hash_match",
            "data_stale", "execution_complete",
        ):
            group[field] = wide[(field, strategy)].to_numpy()
        return strategy_metrics(group)

    v75 = metrics_for(CHAMPION)
    v136 = metrics_for(SHADOW)
    turnover_reduction = 1.0 - v136["turnover"] / v75["turnover"] if v75["turnover"] > 0 else None
    return_delta = v136["total_return"] - v75["total_return"]
    dd_worsening = max(
        0.0,
        abs(min(v136["max_drawdown"], 0.0)) - abs(min(v75["max_drawdown"], 0.0)),
    )
    return {
        "paired_days": len(wide), "v75": v75, "v136": v136,
        "turnover_reduction": turnover_reduction,
        "net_return_delta": return_delta,
        "max_drawdown_worsening": dd_worsening,
        "v136_slippage_to_model_ratio": v136["slippage_to_model_ratio"],
    }


def circular_block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=blocks)
    offsets = np.arange(block)
    return np.concatenate([(start + offsets) % n for start in starts])[:n]


def block_bootstrap(wide: pd.DataFrame, design: dict[str, Any]) -> dict[str, Any]:
    protocol = design["uncertainty_protocol"]
    n = len(wide)
    block = int(protocol["block_days"])
    if n < max(14, block * 2):
        return {"available": False, "reason": "insufficient paired days for block bootstrap"}
    resamples = int(protocol["resamples"])
    confidence = float(protocol["confidence_level"])
    rng = np.random.default_rng(int(protocol["seed"]))
    r75 = wide[("net_return", CHAMPION)].to_numpy(float)
    r136 = wide[("net_return", SHADOW)].to_numpy(float)
    t75 = wide[("turnover", CHAMPION)].to_numpy(float)
    t136 = wide[("turnover", SHADOW)].to_numpy(float)
    return_deltas = np.empty(resamples)
    turnover_reductions = np.empty(resamples)
    for i in range(resamples):
        idx = circular_block_indices(n, block, rng)
        return_deltas[i] = np.prod(1.0 + r136[idx]) - np.prod(1.0 + r75[idx])
        total75 = float(t75[idx].sum())
        turnover_reductions[i] = 1.0 - float(t136[idx].sum()) / total75 if total75 > 0 else np.nan
    alpha = (1.0 - confidence) / 2.0

    def interval(values: np.ndarray) -> dict[str, float | None]:
        finite = values[np.isfinite(values)]
        if not len(finite):
            return {"lower": None, "median": None, "upper": None}
        return {
            "lower": float(np.quantile(finite, alpha)),
            "median": float(np.quantile(finite, 0.5)),
            "upper": float(np.quantile(finite, 1.0 - alpha)),
        }

    return {
        "available": True, "block_days": block, "resamples": resamples,
        "confidence_level": confidence, "seed": int(protocol["seed"]),
        "net_return_delta": interval(return_deltas),
        "turnover_reduction": interval(turnover_reductions),
    }


def context_minimums(context: str, design: dict[str, Any]) -> dict[str, int] | None:
    for hypothesis in design["primary_mechanism_hypotheses"].values():
        if hypothesis["context"] == context:
            return {
                "minimum_paired_days": int(hypothesis["minimum_paired_days"]),
                "minimum_v136_target_changes": int(hypothesis["minimum_v136_target_changes"]),
            }
    item = design.get("diagnostic_contexts", {}).get(context)
    if item:
        return {
            "minimum_paired_days": int(item["minimum_paired_days"]),
            "minimum_v136_target_changes": int(item["minimum_v136_target_changes"]),
        }
    return None


def context_evidence(context: str, wide: pd.DataFrame, design: dict[str, Any]) -> dict[str, Any]:
    point = paired_point_metrics(wide)
    bootstrap = block_bootstrap(wide, design)
    minimums = context_minimums(context, design)
    gates = design["global_gates_inherited_from_v429"]
    if context == "all":
        minimum_days = int(gates["minimum_calendar_days"])
        minimum_changes = int(gates["minimum_v136_target_changes"])
    elif minimums:
        minimum_days = minimums["minimum_paired_days"]
        minimum_changes = minimums["minimum_v136_target_changes"]
    else:
        minimum_days = minimum_changes = 0
    changes = int(point["v136"].get("target_changes", 0))
    minimum_checks = {
        "paired_days": point["paired_days"] >= minimum_days,
        "v136_target_changes": changes >= minimum_changes,
    }
    point_checks = {
        "turnover_reduction": point["turnover_reduction"] is not None
        and point["turnover_reduction"] >= float(gates["v136_turnover_reduction_min"]),
        "net_return_delta": point["net_return_delta"] is not None
        and point["net_return_delta"] >= float(gates["v136_net_return_delta_min"]),
        "max_drawdown_worsening": point["max_drawdown_worsening"] is not None
        and point["max_drawdown_worsening"] <= float(gates["v136_max_drawdown_worsening_max"]),
        "paper_slippage_to_model_ratio": point["v136_slippage_to_model_ratio"] is not None
        and point["v136_slippage_to_model_ratio"] <= float(gates["paper_slippage_to_model_ratio_max"]),
    }
    support_checks = {
        "turnover_reduction_ci_lower_positive": bool(
            bootstrap.get("available")
            and bootstrap["turnover_reduction"]["lower"] is not None
            and bootstrap["turnover_reduction"]["lower"] > 0.0
        ),
        "net_return_delta_ci_lower_within_tolerance": bool(
            bootstrap.get("available")
            and bootstrap["net_return_delta"]["lower"] is not None
            and bootstrap["net_return_delta"]["lower"] >= -0.005
        ),
    }
    if not all(minimum_checks.values()):
        classification = "insufficient_evidence"
    elif not all(point_checks.values()):
        classification = "contradicted"
    elif not all(support_checks.values()):
        classification = "provisional_support"
    else:
        classification = "supported_forward_mechanism"
    return {
        "context": context,
        "minimum_requirements": {"paired_days": minimum_days, "v136_target_changes": minimum_changes},
        "evidence_minimum_checks": minimum_checks,
        "point_metrics": point, "point_checks": point_checks,
        "bootstrap": bootstrap, "statistical_support_checks": support_checks,
        "classification": classification,
        "capital_or_strategy_change_authorized": False,
    }
