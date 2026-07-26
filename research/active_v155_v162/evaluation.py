from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import PERIODS, POLICIES, POST_SELECTION_GATES, SELECTION_END, STANDALONE_GATES, Policy
from loader import V154_ENGINE
from strategy import synthetic_overnight_audit, target_weights

AUDITS = V154_ENGINE.__dict__["Audit"]

# Recreate the already frozen V154 cost/margin scenarios by value rather than
# importing the V154 config module into the local `config` namespace.
BASE = AUDITS("base", 20.0, 0.50, 0.30, 0.10)
STRESS = AUDITS("stress", 40.0, 0.60, 0.35, 0.12)
SEVERE = AUDITS("severe", 80.0, 0.70, 0.40, 0.15, 1, 1.5)
EXTREME = AUDITS("extreme", 150.0, 0.80, 0.50, 0.20, 2, 2.0)
AUDIT_SET = (BASE, STRESS, SEVERE, EXTREME)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def pre_checks(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "prefinal_cagr": row["prefinal_cagr"] >= STANDALONE_GATES["prefinal_cagr_min"],
        "prefinal_sharpe": row["prefinal_sharpe"] >= STANDALONE_GATES["prefinal_sharpe_min"],
        "prefinal_drawdown": row["prefinal_max_drawdown"] >= STANDALONE_GATES["prefinal_max_drawdown_min"],
        "turnover": row["annual_turnover"] <= STANDALONE_GATES["annual_turnover_max"],
        "validation_2011_2014": row["validation_2011_2014_return"] >= STANDALONE_GATES["validation_2011_2014_min"],
        "validation_2015_2018": row["validation_2015_2018_return"] >= STANDALONE_GATES["validation_2015_2018_min"],
        "bridge_2019_2020": row["bridge_2019_2020_return"] >= STANDALONE_GATES["bridge_2019_2020_min"],
        "synthetic_shock": row["worst_synthetic_overnight_return"] >= STANDALONE_GATES["synthetic_overnight_loss_min"],
        "zero_liquidations": row["liquidations"] == 0,
        "positive_margin": row["min_margin_buffer"] > 0,
    }


def stress_checks(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "stress_cagr": row["stress_prefinal_cagr"] >= STANDALONE_GATES["stress_prefinal_cagr_min"],
        "stress_drawdown": row["stress_prefinal_max_drawdown"] >= STANDALONE_GATES["stress_prefinal_max_drawdown_min"],
        "stress_zero_liquidations": row["stress_liquidations"] == 0,
        "stress_positive_margin": row["stress_min_margin_buffer"] > 0,
    }


def score(row: dict[str, Any]) -> float:
    return float(
        row["prefinal_sharpe"]
        + 2.0 * row["prefinal_cagr"]
        + 0.5 * row["prefinal_max_drawdown"]
        - 0.01 * row["annual_turnover"]
        + 0.25 * row["worst_synthetic_overnight_return"]
    )


def evaluate_grid(market, output: Path) -> tuple[list[Policy], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for number, policy in enumerate(POLICIES, start=1):
        target = target_weights(policy, market)
        shock = synthetic_overnight_audit(target, market)
        account = V154_ENGINE.simulate(market, target, BASE)
        pre = V154_ENGINE.period(account, "2006-01-01", SELECTION_END)
        val1 = V154_ENGINE.period(account, *PERIODS["validation_2011_2014"])
        val2 = V154_ENGINE.period(account, *PERIODS["validation_2015_2018"])
        bridge = V154_ENGINE.period(account, *PERIODS["bridge_2019_2020"])
        row: dict[str, Any] = {
            **asdict(policy),
            **shock,
            "prefinal_cagr": pre["annualized_return"],
            "prefinal_total_return": pre["total_return"],
            "prefinal_max_drawdown": pre["max_drawdown"],
            "prefinal_sharpe": pre["sharpe"],
            "annual_turnover": pre["annual_turnover"],
            "liquidations": pre["liquidations"],
            "min_margin_buffer": pre["min_margin_buffer"],
            "validation_2011_2014_return": val1["total_return"],
            "validation_2015_2018_return": val2["total_return"],
            "bridge_2019_2020_return": bridge["total_return"],
        }
        gates = pre_checks(row)
        row.update({f"gate_{key}": value for key, value in gates.items()})
        row["eligible_before_stress"] = all(gates.values())
        if row["eligible_before_stress"]:
            stress = V154_ENGINE.simulate(market, target, STRESS)
            stress_pre = V154_ENGINE.period(stress, "2006-01-01", SELECTION_END)
            row.update(
                {
                    "stress_prefinal_cagr": stress_pre["annualized_return"],
                    "stress_prefinal_max_drawdown": stress_pre["max_drawdown"],
                    "stress_liquidations": stress_pre["liquidations"],
                    "stress_min_margin_buffer": stress_pre["min_margin_buffer"],
                }
            )
            sgates = stress_checks(row)
        else:
            row.update(
                {
                    "stress_prefinal_cagr": np.nan,
                    "stress_prefinal_max_drawdown": np.nan,
                    "stress_liquidations": np.nan,
                    "stress_min_margin_buffer": np.nan,
                }
            )
            sgates = {key: False for key in (
                "stress_cagr",
                "stress_drawdown",
                "stress_zero_liquidations",
                "stress_positive_margin",
            )}
        row.update({f"gate_{key}": value for key, value in sgates.items()})
        row["eligible_before_2021"] = bool(row["eligible_before_stress"] and all(sgates.values()))
        row["score"] = score(row)
        rows.append(row)
        if number % 24 == 0:
            print(f"evaluated {number}/{len(POLICIES)} carry/convex policies", flush=True)

    ranking = pd.DataFrame(rows).sort_values(
        ["eligible_before_2021", "score"], ascending=[False, False]
    )
    ranking.to_csv(output / "selection_ranking_before_2021.csv", index=False)
    eligible = ranking[ranking["eligible_before_2021"]]
    source = eligible if not eligible.empty else ranking
    selected_names: list[str] = []
    selected_families: set[str] = set()
    for _, row in source.iterrows():
        name, family = str(row["name"]), str(row["family"])
        if family not in selected_families or not selected_names:
            selected_names.append(name)
            selected_families.add(family)
        if len(selected_names) == 3:
            break
    for name in source["name"].astype(str):
        if len(selected_names) == 3:
            break
        if name not in selected_names:
            selected_names.append(name)
    by_name = {policy.name: policy for policy in POLICIES}
    selected = [by_name[name] for name in selected_names]

    proof = {
        "candidate": "ACTIVE_V155_V162_VIX_CARRY_CONVEXITY",
        "selection_cutoff": "2020-12-31",
        "selection_uses_2021_or_later": False,
        "hypothesis_inspired_by_post_2020_v154_failure": True,
        "policy_count": len(POLICIES),
        "eligible_policy_count": int(ranking["eligible_before_2021"].sum()),
        "selected": [asdict(policy) for policy in selected],
        "standalone_gates": STANDALONE_GATES,
        "ranking_sha256": sha256_bytes(ranking.to_csv(index=False).encode()),
    }
    path = output / "selection_proof_before_2021.json"
    path.write_text(json.dumps(proof, indent=2) + "\n")
    proof["selection_proof_sha256"] = sha256_bytes(path.read_bytes())
    path.write_text(json.dumps(proof, indent=2) + "\n")
    return selected, ranking


def ensemble_target(selected: list[Policy], market) -> pd.DataFrame:
    return sum(target_weights(policy, market) for policy in selected) / len(selected)


def annual_series(account: pd.DataFrame) -> pd.Series:
    return (1.0 + account["daily_return"].fillna(0.0)).groupby(account.index.year).prod() - 1.0


def post_checks(base: pd.DataFrame, severe: pd.DataFrame) -> dict[str, bool]:
    hold1 = V154_ENGINE.period(base, *PERIODS["holdout_2021_2023"])
    hold2 = V154_ENGINE.period(base, *PERIODS["holdout_2024_2025"])
    final = V154_ENGINE.period(base, *PERIODS["final_2026h1"])
    severe_full = V154_ENGINE.metrics(severe)
    yearly = annual_series(base)
    positive = np.log1p(yearly[yearly > 0])
    best_share = float(positive.max() / positive.sum()) if len(positive) and positive.sum() > 0 else 1.0
    return {
        "holdout_2021_2023": hold1["total_return"] >= POST_SELECTION_GATES["holdout_2021_2023_min"],
        "holdout_2024_2025": hold2["total_return"] >= POST_SELECTION_GATES["holdout_2024_2025_min"],
        "final_2026h1": final["total_return"] >= POST_SELECTION_GATES["final_2026h1_min"],
        "severe_full_cagr": severe_full["annualized_return"] >= POST_SELECTION_GATES["severe_full_cagr_min"],
        "worst_year": float(yearly.min()) >= POST_SELECTION_GATES["worst_year_min"],
        "best_year_concentration": best_share <= POST_SELECTION_GATES["best_year_positive_log_share_max"],
    }
