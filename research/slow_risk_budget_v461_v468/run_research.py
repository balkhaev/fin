#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_SOURCE = REPO_ROOT / "research" / "meta_ensemble_v453_v460" / "run_research.py"
_spec = importlib.util.spec_from_file_location("v453_meta_parent", PARENT_SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import parent meta-engine: {PARENT_SOURCE}")
parent = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = parent
_spec.loader.exec_module(parent)

PROGRAM = "V461_V468_SLOW_MARKET_RISK_BUDGET"
INITIAL_EQUITY = parent.INITIAL_EQUITY
PERIODS = parent.PERIODS
FOLDS = parent.FOLDS
AUDITS = parent.AUDITS
BASE_V285_WEIGHT = 0.40
BASE_V365_WEIGHT = 0.60


@dataclass(frozen=True, slots=True)
class OverlayPolicy:
    name: str
    budget_floor: float
    budget_ceiling: float
    rebalance_days: int
    smoothing_halflife_days: float
    persistence_activation: bool = False
    target_vol: float | None = None
    max_leverage: float = 1.0
    inverted_budget_control: bool = False
    promotable: bool = True

    def validate(self) -> None:
        if not (0 <= self.budget_floor <= self.budget_ceiling <= self.max_leverage <= 1.25 + 1e-12):
            raise ValueError(f"invalid budget bounds for {self.name}")
        if self.rebalance_days < 1 or self.smoothing_halflife_days <= 0:
            raise ValueError(f"invalid schedule for {self.name}")
        if self.target_vol is not None and not (0.05 <= self.target_vol <= 0.30):
            raise ValueError(f"invalid target vol for {self.name}")


def clean(value: Any) -> Any:
    return parent.clean(value)


def write_json(path: Path, value: Any) -> None:
    parent.write_json(path, value)


def sha256_file(path: Path) -> str:
    return parent.sha256_file(path)


def canonical_hash(value: Any) -> str:
    return parent.canonical_hash(value)


def policies_from_design(design: dict[str, Any]) -> list[OverlayPolicy]:
    policies = [OverlayPolicy(**row) for row in design["policies"]]
    for policy in policies:
        policy.validate()
    return policies


def normalized_market_signal(row: pd.Series, inverted: bool) -> float:
    # The parent score is causal and maps the six frozen V413 axes into [0.22, 1.00].
    # Normalize it to [0, 1] so every policy changes only the overall risk budget.
    raw_budget = parent.market_budget(row, inverted=inverted)
    return float(np.clip((raw_budget - 0.22) / 0.78, 0.0, 1.0))


def fixed_mix_predicted_vol(panel: pd.DataFrame, t: int, lookback_days: int = 126) -> float:
    if t < 30:
        return 0.0
    history = panel.iloc[max(0, t - lookback_days):t][["ret_v285", "ret_v365"]]
    if len(history) < 30:
        return 0.0
    covariance = history.cov().to_numpy(float) * 365.0
    mix = np.array([BASE_V285_WEIGHT, BASE_V365_WEIGHT], dtype=float)
    variance = float(mix @ covariance @ mix)
    return math.sqrt(max(variance, 0.0))


def generate_overlay_weights(panel: pd.DataFrame, policy: OverlayPolicy) -> pd.DataFrame:
    policy.validate()
    columns = ["w_v285", "w_v365", "w_cash", "risky_budget", "predicted_vol", "raw_market_signal"]
    weights = pd.DataFrame(0.0, index=panel.index, columns=columns)
    alpha = 1.0 - math.exp(math.log(0.5) / policy.smoothing_halflife_days)
    smoothed_target = 1.0
    held_budget = 0.0
    last_rebalance = -10**9

    for t in range(len(panel)):
        row = panel.iloc[t]
        signal = normalized_market_signal(row, policy.inverted_budget_control)
        target = policy.budget_floor + signal * (policy.budget_ceiling - policy.budget_floor)
        if policy.persistence_activation and float(row["state_duration_days"]) <= 5.0:
            target = 1.0
        target = float(np.clip(target, policy.budget_floor, policy.budget_ceiling))
        smoothed_target = alpha * target + (1.0 - alpha) * smoothed_target
        predicted_vol = fixed_mix_predicted_vol(panel, t)

        if t == 0 or (t - last_rebalance) >= policy.rebalance_days:
            budget = smoothed_target
            if policy.target_vol is not None and predicted_vol > 1e-8:
                vol_scale = float(np.clip(policy.target_vol / predicted_vol, 0.70, policy.max_leverage / max(budget, 1e-12)))
                budget *= vol_scale
            held_budget = float(np.clip(budget, 0.0, policy.max_leverage))
            last_rebalance = t

        weights.iloc[t] = [
            held_budget * BASE_V285_WEIGHT,
            held_budget * BASE_V365_WEIGHT,
            1.0 - held_budget,
            held_budget,
            predicted_vol,
            signal,
        ]
    return weights


def development_score(account: pd.DataFrame, folds: dict[str, dict[str, Any]]) -> float:
    dev = parent.metrics(parent.cut(account, PERIODS["development_2021_2023"]))
    worst_fold_return = min(item["total_return"] for item in folds.values())
    worst_fold_sharpe = min(item["sharpe"] for item in folds.values())
    return (
        0.70 * dev["cagr"]
        + 0.60 * dev["sharpe"]
        + 0.25 * worst_fold_return
        + 0.25 * worst_fold_sharpe
        - 0.40 * abs(dev["max_drawdown"])
        - 0.025 * dev["annual_meta_turnover"]
    )


def blend_accounts(accounts: list[pd.DataFrame]) -> pd.DataFrame:
    return parent.blend_selected_accounts(accounts)


def component_account(panel: pd.DataFrame, label: str) -> pd.DataFrame:
    account = pd.DataFrame(index=panel.index)
    account["net_return"] = panel[f"ret_{label}"]
    account["gross_return"] = account["net_return"]
    account["equity"] = INITIAL_EQUITY * (1.0 + account["net_return"]).cumprod()
    account["w_v285"] = 1.0 if label == "v285" else 0.0
    account["w_v365"] = 1.0 if label == "v365" else 0.0
    account["w_cash"] = 0.0
    account["risky_budget"] = 1.0
    account["gross"] = panel[f"gross_{label}"]
    account["underlying_turnover"] = panel[f"turnover_{label}"]
    account["meta_turnover"] = 0.0
    account["meta_cost"] = 0.0
    account["underlying_stress_cost"] = 0.0
    account["financing"] = 0.0
    account["drawdown"] = account["equity"] / account["equity"].cummax() - 1.0
    account["state_label"] = panel["state_label"]
    account["state_duration_days"] = panel["state_duration_days"]
    account["novelty_flag"] = panel["novelty_flag"]
    return account


def self_test() -> None:
    index = pd.date_range("2021-01-01", periods=900, freq="1D", tz="UTC")
    rng = np.random.default_rng(461)
    panel = pd.DataFrame(index=index)
    panel["ret_v285"] = rng.normal(0.0004, 0.007, len(index))
    panel["ret_v365"] = rng.normal(0.0003, 0.006, len(index))
    panel["turnover_v285"] = 0.01
    panel["turnover_v365"] = 0.02
    panel["gross_v285"] = 0.50
    panel["gross_v365"] = 0.35
    labels = np.array(["deleveraging", "transition", "rotation", "speculative_risk_on", "transition_2", "calm_risk_on"])
    panel["state_id"] = np.arange(len(index)) // 150
    panel["state_label"] = labels[panel["state_id"].clip(0, 5)]
    panel["state_duration_days"] = np.tile(np.arange(1, 151), 6)
    panel["assignment_confidence"] = 0.6
    panel["novelty_ratio"] = 0.5
    panel["novelty_flag"] = False
    panel["transition_surprise"] = 0.1
    for column in ("trend", "breadth", "stress", "rotation", "liquidity", "leverage"):
        panel[column] = rng.normal(0.0, 1.0, len(index))
    panel["duration_class"] = panel["state_duration_days"].map(parent.duration_class)
    panel["state_changed"] = panel["state_label"].ne(panel["state_label"].shift(1))

    policy = OverlayPolicy(
        name="test", budget_floor=0.75, budget_ceiling=1.15,
        rebalance_days=28, smoothing_halflife_days=14,
        persistence_activation=True, target_vol=0.18, max_leverage=1.15,
    )
    first = generate_overlay_weights(panel, policy)
    changed = panel.copy()
    changed.iloc[-1, changed.columns.get_loc("ret_v285")] += 2.0
    second = generate_overlay_weights(changed, policy)
    pd.testing.assert_frame_equal(first.iloc[:-1], second.iloc[:-1], check_exact=False, rtol=1e-12, atol=1e-12)
    permuted = generate_overlay_weights(panel[panel.columns[::-1]], policy)
    pd.testing.assert_frame_equal(first, permuted)
    account = parent.simulate(panel, first, AUDITS[0])
    assert first["risky_budget"].max() <= 1.1500001
    assert np.isfinite(account["equity"]).all()
    assert account["meta_turnover"].sum() > 0
    print("V461-V468 slow risk-budget self-test passed")


def render_report(summary: dict[str, Any]) -> str:
    if summary["status"] == "rejected_before_oos":
        top = summary["selection"]["ranking_top"][0]
        return f"""# V461–V468 — slow market-risk budget\n\nStatus: `rejected_before_oos`.\n\n```text\neligible policies      {summary['eligible_policy_count']}\nbest policy            {top['policy']}\ndevelopment CAGR       {top['development_cagr']:.2%}\ndevelopment Sharpe     {top['development_sharpe']:.3f}\ndevelopment Max DD     {top['development_max_drawdown']:.2%}\n```\n\n2024–2026 H1 remained unopened.\n"""
    full = summary["candidate_full"]
    periods = summary["candidate_periods"]
    return f"""# V461–V468 — slow market-risk budget\n\nStatus: `{summary['status']}`.\n\nSelected: `{', '.join(summary['selected_policies'])}`.\n\n```text\nFull CAGR          {full['cagr']:.2%}\nFull Sharpe        {full['sharpe']:.3f}\nFull Max DD        {full['max_drawdown']:.2%}\n2024               {periods['validation_2024']['total_return']:.2%}\n2025               {periods['holdout_2025']['total_return']:.2%}\n2026 H1            {periods['final_2026h1']['total_return']:.2%}\n```\n\nThis remains exploratory because component OOS history was known before this cycle.\n"""


def run(args: argparse.Namespace) -> int:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    design = json.loads(args.design.read_text(encoding="utf-8"))
    if design["program"] != PROGRAM:
        raise RuntimeError("design/program mismatch")
    panel = parent.load_panel(args.v285, args.v365)
    policies = policies_from_design(design)
    weights_by_policy: dict[str, pd.DataFrame] = {}
    base_accounts: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []

    for policy in policies:
        weights = generate_overlay_weights(panel, policy)
        account = parent.simulate(panel, weights, AUDITS[0])
        weights_by_policy[policy.name] = weights
        base_accounts[policy.name] = account
        dev = parent.metrics(parent.cut(account, PERIODS["development_2021_2023"]))
        folds = {name: parent.metrics(parent.cut(account, bounds)) for name, bounds in FOLDS.items()}
        rows.append({
            "policy": policy.name,
            "promotable": policy.promotable,
            **{f"development_{k}": v for k, v in dev.items()},
            **{f"{fold}_{k}": v for fold, item in folds.items() for k, v in item.items()},
            "score": development_score(account, folds),
        })

    ranking = pd.DataFrame(rows).sort_values(["promotable", "score"], ascending=[False, False])
    ranking.to_csv(output / "policy_development_ranking.csv", index=False)
    control_name = design["selection"]["control_policy"]
    control = ranking[ranking.policy == control_name].iloc[0]
    eligibility_rows = []
    for _, row in ranking[ranking.promotable].iterrows():
        gates = {
            "wf_2022_positive": float(row.wf_2022_total_return) > 0,
            "wf_2023_positive": float(row.wf_2023_total_return) > 0,
            "development_cagr_uplift": float(row.development_cagr) >= float(control.development_cagr) + design["selection"]["development_cagr_uplift_min"],
            "development_sharpe_floor": float(row.development_sharpe) >= float(control.development_sharpe) - design["selection"]["development_sharpe_loss_max"],
            "development_dd": float(row.development_max_drawdown) >= design["selection"]["development_max_drawdown_min"],
            "meta_turnover": float(row.development_annual_meta_turnover) <= design["selection"]["annual_meta_turnover_max"],
            "max_leverage": float(row.development_max_risky_budget) <= design["selection"]["max_leverage"],
        }
        eligibility_rows.append({"policy": row.policy, **gates, "eligible": all(gates.values()), "score": float(row.score)})
    eligibility = pd.DataFrame(eligibility_rows).sort_values(["eligible", "score"], ascending=[False, False])
    eligibility.to_csv(output / "policy_eligibility.csv", index=False)
    selected = list(eligibility[eligibility.eligible].head(design["selection"]["selected_policy_count"]).policy)
    selection = {
        "program": PROGRAM,
        "design_sha256": sha256_file(args.design),
        "parent_source_sha256": sha256_file(PARENT_SOURCE),
        "input_sha256": {"v285": sha256_file(args.v285), "v365": sha256_file(args.v365)},
        "selection_period": PERIODS["development_2021_2023"],
        "folds": FOLDS,
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "component_history_already_known_before_cycle": True,
        "base_component_weights": {"v285": BASE_V285_WEIGHT, "v365": BASE_V365_WEIGHT},
        "eligible_policies": selected,
        "eligibility": eligibility.to_dict(orient="records"),
        "ranking_top": ranking.head(20).to_dict(orient="records"),
    }
    selection["selection_proof_sha256"] = canonical_hash(selection)
    write_json(output / "selection_proof_before_oos.json", selection)

    if len(selected) < design["selection"]["selected_policy_count"]:
        decision = {
            "program": PROGRAM,
            "status": "rejected_before_oos",
            "eligible_policy_count": len(selected),
            "selected_policies": [],
            "oos_opened": False,
            "integration_permitted": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        summary = {**decision, "selection": selection}
        write_json(output / "FROZEN_DECISION.json", decision)
        write_json(output / "summary.json", summary)
        (output / "REPORT_RU.md").write_text(render_report(summary), encoding="utf-8")
        return 0

    candidate_accounts: dict[str, pd.DataFrame] = {}
    audit_rows = []
    for audit in AUDITS:
        accounts = [parent.simulate(panel, weights_by_policy[name], audit) for name in selected]
        candidate = blend_accounts(accounts)
        candidate_accounts[audit.name] = candidate
        for period, bounds in PERIODS.items():
            audit_rows.append({"audit": audit.name, "period": period, **parent.metrics(parent.cut(candidate, bounds))})
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "audit_metrics.csv", index=False)
    candidate_accounts["base"].to_csv(output / "candidate_equity.csv")
    average_weights = sum(weights_by_policy[name] for name in selected) / len(selected)
    average_weights.to_csv(output / "candidate_weights.csv")

    def audit_metric(audit: str, period: str) -> dict[str, Any]:
        row = audit_table[(audit_table.audit == audit) & (audit_table.period == period)].iloc[0]
        return {key: clean(value) for key, value in row.items() if key not in {"audit", "period"}}

    candidate_full = audit_metric("base", "full")
    candidate_periods = {name: audit_metric("base", name) for name in ("development_2021_2023", "validation_2024", "holdout_2025", "final_2026h1")}
    control_full = parent.metrics(base_accounts[control_name])
    component_full = {label: parent.metrics(component_account(panel, label)) for label in ("v285", "v365")}

    yearly = parent.yearly_returns(candidate_accounts["base"], "candidate")
    yearly = yearly.merge(parent.yearly_returns(base_accounts[control_name], "control_static_40_60"), on="year", how="outer")
    for label in ("v285", "v365"):
        yearly = yearly.merge(parent.yearly_returns(component_account(panel, label), label), on="year", how="outer")
    yearly.to_csv(output / "yearly_returns.csv", index=False)

    gates = {
        "validation_positive": candidate_periods["validation_2024"]["total_return"] > 0,
        "holdout_positive": candidate_periods["holdout_2025"]["total_return"] > 0,
        "final_positive": candidate_periods["final_2026h1"]["total_return"] > 0,
        "full_cagr_uplift": candidate_full["cagr"] >= control_full["cagr"] + design["post_oos"]["full_cagr_uplift_min"],
        "full_sharpe_uplift": candidate_full["sharpe"] >= control_full["sharpe"] + design["post_oos"]["full_sharpe_uplift_min"],
        "full_max_drawdown": candidate_full["max_drawdown"] >= design["post_oos"]["full_max_drawdown_min"],
        "severe_full_cagr_positive": audit_metric("severe", "full")["cagr"] > 0,
        "extreme_full_cagr_positive": audit_metric("extreme", "full")["cagr"] > 0,
        "delay_full_cagr_positive": audit_metric("delay_1d", "full")["cagr"] > 0,
        "worst_calendar_year": float(yearly.candidate.min()) >= design["post_oos"]["worst_calendar_year_min"],
        "max_leverage": candidate_full["max_risky_budget"] <= design["post_oos"]["max_leverage"],
    }
    passed = all(gates.values())
    decision = {
        "program": PROGRAM,
        "status": "exploratory_candidate_after_oos" if passed else "rejected_after_oos",
        "selected_policies": selected,
        "oos_opened": True,
        "standalone_selection_passed": passed,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "historical_parameter_search_pristine": False,
        "reason_not_pristine": "component families and 2024-2026 outcomes were known before this cycle",
    }
    summary = {
        **decision,
        "selection": selection,
        "candidate_full": candidate_full,
        "candidate_periods": candidate_periods,
        "control_full": control_full,
        "component_full": component_full,
        "post_oos_gates": gates,
        "audit_full": {audit.name: audit_metric(audit.name, "full") for audit in AUDITS},
        "yearly_returns": yearly.to_dict(orient="records"),
    }
    write_json(output / "FROZEN_DECISION.json", decision)
    write_json(output / "summary.json", summary)
    (output / "REPORT_RU.md").write_text(render_report(summary), encoding="utf-8")

    files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            files[str(path.relative_to(output))] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(output / "MANIFEST.json", {"program": PROGRAM, "files": files})
    print(json.dumps(clean(summary), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v285", type=Path)
    parser.add_argument("--v365", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if None in (args.v285, args.v365, args.design, args.output):
        raise SystemExit("--v285, --v365, --design and --output are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
