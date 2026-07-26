from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import (
    AUDITS,
    PERIODS,
    POLICIES,
    POST_SELECTION_GATES,
    SELECTION_END,
    STANDALONE_GATES,
    Policy,
)
from engine import annual_returns, period, simulate
from features import MarketData, policy_target


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def checks_before_2021(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "prefinal_cagr": row["prefinal_cagr"] >= STANDALONE_GATES["prefinal_cagr_min"],
        "prefinal_sharpe": row["prefinal_sharpe"] >= STANDALONE_GATES["prefinal_sharpe_min"],
        "prefinal_drawdown": row["prefinal_max_drawdown"] >= STANDALONE_GATES["prefinal_max_drawdown_min"],
        "turnover": row["annual_turnover"] <= STANDALONE_GATES["annual_turnover_max"],
        "validation_2011_2014": row["validation_2011_2014_return"] >= STANDALONE_GATES["validation_2011_2014_min"],
        "validation_2015_2018": row["validation_2015_2018_return"] >= STANDALONE_GATES["validation_2015_2018_min"],
        "bridge_2019_2020": row["bridge_2019_2020_return"] >= STANDALONE_GATES["bridge_2019_2020_min"],
        "no_liquidations": row["liquidations"] == 0,
        "positive_margin": row["min_margin_buffer"] > 0,
    }


def stress_checks(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "stress_prefinal_cagr": row["stress_prefinal_cagr"] >= STANDALONE_GATES["stress_prefinal_cagr_min"],
        "stress_prefinal_drawdown": row["stress_prefinal_max_drawdown"] >= STANDALONE_GATES["stress_prefinal_max_drawdown_min"],
        "stress_no_liquidations": row["stress_liquidations"] == 0,
        "stress_positive_margin": row["stress_min_margin_buffer"] > 0,
    }


def score(row: dict[str, Any]) -> float:
    return float(
        2.0 * row["prefinal_cagr"]
        + row["prefinal_sharpe"]
        + 0.5 * row["prefinal_max_drawdown"]
        - 0.01 * row["annual_turnover"]
    )


def evaluate_prefinal(market: MarketData, output: Path) -> tuple[list[Policy], pd.DataFrame]:
    """Evaluate policies without retaining the large target matrices.

    Each target is discarded after simulation. Only the three selected targets
    are rebuilt later, keeping the grid bounded in memory.
    """

    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    stress_audit = next(audit for audit in AUDITS if audit.name == "stress")
    rows: list[dict[str, Any]] = []

    for number, policy in enumerate(POLICIES, start=1):
        target = policy_target(policy, market)
        account = simulate(market, target, base_audit)
        pre = period(account, "2006-01-01", SELECTION_END)
        val1 = period(account, *PERIODS["validation_2011_2014"])
        val2 = period(account, *PERIODS["validation_2015_2018"])
        bridge = period(account, *PERIODS["bridge_2019_2020"])
        row: dict[str, Any] = {
            **asdict(policy),
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
        initial = checks_before_2021(row)
        row.update({f"gate_{key}": value for key, value in initial.items()})
        row["eligible_before_stress"] = all(initial.values())
        if row["eligible_before_stress"]:
            stress_account = simulate(market, target, stress_audit)
            stress_pre = period(stress_account, "2006-01-01", SELECTION_END)
            row.update(
                {
                    "stress_prefinal_cagr": stress_pre["annualized_return"],
                    "stress_prefinal_max_drawdown": stress_pre["max_drawdown"],
                    "stress_liquidations": stress_pre["liquidations"],
                    "stress_min_margin_buffer": stress_pre["min_margin_buffer"],
                }
            )
            stress_gate = stress_checks(row)
        else:
            row.update(
                {
                    "stress_prefinal_cagr": np.nan,
                    "stress_prefinal_max_drawdown": np.nan,
                    "stress_liquidations": np.nan,
                    "stress_min_margin_buffer": np.nan,
                }
            )
            stress_gate = {
                "stress_prefinal_cagr": False,
                "stress_prefinal_drawdown": False,
                "stress_no_liquidations": False,
                "stress_positive_margin": False,
            }
        row.update({f"gate_{key}": value for key, value in stress_gate.items()})
        row["eligible_before_2021"] = bool(row["eligible_before_stress"] and all(stress_gate.values()))
        row["score"] = score(row)
        rows.append(row)
        if number % 18 == 0:
            print(f"evaluated {number}/{len(POLICIES)} VIX policies", flush=True)
        del target, account

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
        "candidate": "V149_V154_DATED_VIX_FUTURES",
        "selection_cutoff": "2020-12-31",
        "selection_uses_2021_or_later": False,
        "policy_count": len(POLICIES),
        "eligible_policy_count": int(ranking["eligible_before_2021"].sum()),
        "selected": [asdict(policy) for policy in selected],
        "standalone_gates": STANDALONE_GATES,
        "ranking_sha256": sha256_bytes(ranking.to_csv(index=False).encode()),
    }
    proof_path = output / "selection_proof_before_2021.json"
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")
    proof["selection_proof_sha256"] = sha256_bytes(proof_path.read_bytes())
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")
    return selected, ranking


def ensemble_target(selected: list[Policy], market: MarketData) -> pd.DataFrame:
    targets = [policy_target(policy, market) for policy in selected]
    result = sum(targets) / len(targets)
    return result


def best_year_share(account: pd.DataFrame) -> float:
    yearly = annual_returns(account, "return")
    positive = np.log1p(yearly.loc[yearly["return"] > 0, "return"])
    if positive.empty or positive.sum() <= 0:
        return 1.0
    return float(positive.max() / positive.sum())


def post_selection_checks(account: pd.DataFrame) -> dict[str, bool]:
    hold1 = period(account, *PERIODS["holdout_2021_2023"])
    hold2 = period(account, *PERIODS["holdout_2024_2025"])
    final = period(account, *PERIODS["final_2026h1"])
    return {
        "holdout_2021_2023": hold1["total_return"] >= POST_SELECTION_GATES["holdout_2021_2023_min"],
        "holdout_2024_2025": hold2["total_return"] >= POST_SELECTION_GATES["holdout_2024_2025_min"],
        "final_2026h1": final["total_return"] >= POST_SELECTION_GATES["final_2026h1_min"],
        "best_year_concentration": best_year_share(account) <= POST_SELECTION_GATES["best_year_positive_log_share_max"],
    }
