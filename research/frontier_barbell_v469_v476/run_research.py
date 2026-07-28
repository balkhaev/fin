#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
V461_SOURCE = REPO_ROOT / "research" / "slow_risk_budget_v461_v468" / "run_research.py"
_spec = importlib.util.spec_from_file_location("v461_budget_parent", V461_SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import V461 parent: {V461_SOURCE}")
v461 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = v461
_spec.loader.exec_module(v461)

v453 = v461.parent
PROGRAM = "V469_V476_FRONTIER_BARBELL_ENSEMBLE"
INITIAL_EQUITY = v453.INITIAL_EQUITY
PERIODS = v453.PERIODS
FOLDS = v453.FOLDS
AUDITS = v453.AUDITS


@dataclass(frozen=True, slots=True)
class Recipe:
    name: str
    allocations: dict[str, float]
    promotable: bool = True

    def validate(self) -> None:
        if not self.allocations:
            raise ValueError(f"empty recipe {self.name}")
        if any(weight < 0 for weight in self.allocations.values()):
            raise ValueError(f"negative recipe weight {self.name}")
        if abs(sum(self.allocations.values()) - 1.0) > 1e-12:
            raise ValueError(f"recipe weights do not sum to one: {self.name}")


def clean(value: Any) -> Any:
    return v453.clean(value)


def write_json(path: Path, value: Any) -> None:
    v453.write_json(path, value)


def sha256_file(path: Path) -> str:
    return v453.sha256_file(path)


def canonical_hash(value: Any) -> str:
    return v453.canonical_hash(value)


def weighted_blend(accounts: dict[str, pd.DataFrame], allocations: dict[str, float]) -> pd.DataFrame:
    recipe = Recipe("blend", allocations)
    recipe.validate()
    names = list(allocations)
    index = accounts[names[0]].index
    for name in names[1:]:
        if not accounts[name].index.equals(index):
            raise ValueError("account index mismatch")
    result = pd.DataFrame(index=index)
    additive = (
        "net_return", "gross_return", "w_v285", "w_v365", "w_cash", "risky_budget", "gross",
        "underlying_turnover", "meta_turnover", "meta_cost", "underlying_stress_cost", "financing",
    )
    for column in additive:
        result[column] = sum(float(allocations[name]) * accounts[name][column] for name in names)
    result["equity"] = INITIAL_EQUITY * (1.0 + result["net_return"]).cumprod()
    result["drawdown"] = result["equity"] / result["equity"].cummax() - 1.0
    result["state_label"] = accounts[names[0]]["state_label"]
    result["state_duration_days"] = accounts[names[0]]["state_duration_days"]
    result["novelty_flag"] = accounts[names[0]]["novelty_flag"]
    return result


def weights_blend(weights: dict[str, pd.DataFrame], allocations: dict[str, float]) -> pd.DataFrame:
    names = list(allocations)
    output = sum(float(allocations[name]) * weights[name] for name in names)
    return output


def development_score(account: pd.DataFrame, folds: dict[str, dict[str, Any]]) -> float:
    dev = v453.metrics(v453.cut(account, PERIODS["development_2021_2023"]))
    return (
        0.80 * dev["cagr"]
        + 0.75 * dev["sharpe"]
        + 0.20 * min(item["total_return"] for item in folds.values())
        + 0.20 * min(item["sharpe"] for item in folds.values())
        - 0.50 * abs(dev["max_drawdown"])
        - 0.025 * dev["annual_meta_turnover"]
    )


def component_policies() -> dict[str, v461.OverlayPolicy]:
    return {
        "static": v461.OverlayPolicy(
            name="static", budget_floor=1.0, budget_ceiling=1.0,
            rebalance_days=28, smoothing_halflife_days=14, promotable=False,
        ),
        "persistent": v461.OverlayPolicy(
            name="persistent", budget_floor=0.75, budget_ceiling=1.15,
            rebalance_days=28, smoothing_halflife_days=14,
            persistence_activation=True, max_leverage=1.15,
        ),
        "vol22": v461.OverlayPolicy(
            name="vol22", budget_floor=0.75, budget_ceiling=1.25,
            rebalance_days=28, smoothing_halflife_days=14,
            persistence_activation=True, target_vol=0.22, max_leverage=1.25,
        ),
        "inverted": v461.OverlayPolicy(
            name="inverted", budget_floor=0.65, budget_ceiling=1.10,
            rebalance_days=28, smoothing_halflife_days=14,
            persistence_activation=True, max_leverage=1.10,
            inverted_budget_control=True, promotable=False,
        ),
    }


def recipes_from_design(design: dict[str, Any]) -> list[Recipe]:
    recipes = [Recipe(name=row["name"], allocations={str(k): float(v) for k, v in row["allocations"].items()}, promotable=bool(row.get("promotable", True))) for row in design["recipes"]]
    for recipe in recipes:
        recipe.validate()
    return recipes


def self_test() -> None:
    index = pd.date_range("2021-01-01", periods=900, freq="1D", tz="UTC")
    rng = np.random.default_rng(469)
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
        panel[column] = rng.normal(0, 1, len(index))
    panel["duration_class"] = panel["state_duration_days"].map(v453.duration_class)
    panel["state_changed"] = panel["state_label"].ne(panel["state_label"].shift(1))

    policies = component_policies()
    weights = {name: v461.generate_overlay_weights(panel, policy) for name, policy in policies.items()}
    accounts = {name: v453.simulate(panel, table, AUDITS[0]) for name, table in weights.items()}
    allocations = {"persistent": 0.5, "vol22": 0.5}
    first = weighted_blend(accounts, allocations)
    changed = panel.copy()
    changed.iloc[-1, changed.columns.get_loc("ret_v285")] += 2.0
    changed_weights = {name: v461.generate_overlay_weights(changed, policy) for name, policy in policies.items()}
    for name in policies:
        pd.testing.assert_frame_equal(weights[name].iloc[:-1], changed_weights[name].iloc[:-1], check_exact=False, rtol=1e-12, atol=1e-12)
    assert np.isfinite(first.equity).all()
    assert first.risky_budget.max() <= 1.25 + 1e-12
    assert abs(weights_blend(weights, allocations).iloc[0][["w_v285", "w_v365", "w_cash"]].sum() - 1.0) < 1e-12
    print("V469-V476 frontier barbell self-test passed")


def render_report(summary: dict[str, Any]) -> str:
    if summary["status"] == "rejected_before_oos":
        top = summary["selection"]["ranking_top"][0]
        return f"""# V469–V476 — frontier barbell ensemble\n\nStatus: `rejected_before_oos`.\n\nBest recipe: `{top['recipe']}`; development CAGR {top['development_cagr']:.2%}, Sharpe {top['development_sharpe']:.3f}.\n"""
    full = summary["candidate_full"]
    periods = summary["candidate_periods"]
    return f"""# V469–V476 — frontier barbell ensemble\n\nStatus: `{summary['status']}`.\n\nSelected recipe: `{summary['selected_recipe']}`.\n\n```text\nFull CAGR       {full['cagr']:.2%}\nFull Sharpe     {full['sharpe']:.3f}\nFull Max DD     {full['max_drawdown']:.2%}\n2024            {periods['validation_2024']['total_return']:.2%}\n2025            {periods['holdout_2025']['total_return']:.2%}\n2026 H1         {periods['final_2026h1']['total_return']:.2%}\n```\n"""


def run(args: argparse.Namespace) -> int:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    design = json.loads(args.design.read_text(encoding="utf-8"))
    if design["program"] != PROGRAM:
        raise RuntimeError("design/program mismatch")
    panel = v453.load_panel(args.v285, args.v365)
    policies = component_policies()
    recipes = recipes_from_design(design)

    component_weights = {name: v461.generate_overlay_weights(panel, policy) for name, policy in policies.items()}
    base_component_accounts = {name: v453.simulate(panel, table, AUDITS[0]) for name, table in component_weights.items()}
    recipe_accounts: dict[str, pd.DataFrame] = {}
    recipe_weights: dict[str, pd.DataFrame] = {}
    rows = []
    for recipe in recipes:
        account = weighted_blend(base_component_accounts, recipe.allocations)
        recipe_accounts[recipe.name] = account
        recipe_weights[recipe.name] = weights_blend(component_weights, recipe.allocations)
        dev = v453.metrics(v453.cut(account, PERIODS["development_2021_2023"]))
        folds = {name: v453.metrics(v453.cut(account, bounds)) for name, bounds in FOLDS.items()}
        rows.append({
            "recipe": recipe.name,
            "promotable": recipe.promotable,
            **{f"development_{k}": v for k, v in dev.items()},
            **{f"{fold}_{k}": v for fold, item in folds.items() for k, v in item.items()},
            "score": development_score(account, folds),
        })
    ranking = pd.DataFrame(rows).sort_values(["promotable", "score"], ascending=[False, False])
    ranking.to_csv(output / "recipe_development_ranking.csv", index=False)
    control_name = design["selection"]["control_recipe"]
    control = ranking[ranking.recipe == control_name].iloc[0]
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
        eligibility_rows.append({"recipe": row.recipe, **gates, "eligible": all(gates.values()), "score": float(row.score)})
    eligibility = pd.DataFrame(eligibility_rows).sort_values(["eligible", "score"], ascending=[False, False])
    eligibility.to_csv(output / "recipe_eligibility.csv", index=False)
    eligible = list(eligibility[eligibility.eligible].head(1).recipe)
    selection = {
        "program": PROGRAM,
        "design_sha256": sha256_file(args.design),
        "parent_v461_source_sha256": sha256_file(V461_SOURCE),
        "input_sha256": {"v285": sha256_file(args.v285), "v365": sha256_file(args.v365)},
        "selection_period": PERIODS["development_2021_2023"],
        "folds": FOLDS,
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "component_history_already_known_before_cycle": True,
        "eligible_recipes": eligible,
        "eligibility": eligibility.to_dict(orient="records"),
        "ranking_top": ranking.head(20).to_dict(orient="records"),
    }
    selection["selection_proof_sha256"] = canonical_hash(selection)
    write_json(output / "selection_proof_before_oos.json", selection)

    if not eligible:
        decision = {
            "program": PROGRAM,
            "status": "rejected_before_oos",
            "eligible_recipe_count": 0,
            "selected_recipe": None,
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

    selected_name = eligible[0]
    selected_recipe = next(recipe for recipe in recipes if recipe.name == selected_name)
    audit_rows = []
    audit_accounts = {}
    for audit in AUDITS:
        component_accounts = {name: v453.simulate(panel, component_weights[name], audit) for name in selected_recipe.allocations}
        candidate = weighted_blend(component_accounts, selected_recipe.allocations)
        audit_accounts[audit.name] = candidate
        for period, bounds in PERIODS.items():
            audit_rows.append({"audit": audit.name, "period": period, **v453.metrics(v453.cut(candidate, bounds))})
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "audit_metrics.csv", index=False)
    audit_accounts["base"].to_csv(output / "candidate_equity.csv")
    recipe_weights[selected_name].to_csv(output / "candidate_weights.csv")

    def audit_metric(audit: str, period: str) -> dict[str, Any]:
        row = audit_table[(audit_table.audit == audit) & (audit_table.period == period)].iloc[0]
        return {key: clean(value) for key, value in row.items() if key not in {"audit", "period"}}

    candidate_full = audit_metric("base", "full")
    candidate_periods = {name: audit_metric("base", name) for name in ("development_2021_2023", "validation_2024", "holdout_2025", "final_2026h1")}
    control_full = v453.metrics(recipe_accounts[control_name])
    yearly = v453.yearly_returns(audit_accounts["base"], "candidate")
    yearly = yearly.merge(v453.yearly_returns(recipe_accounts[control_name], "control_static_40_60"), on="year", how="outer")
    yearly.to_csv(output / "yearly_returns.csv", index=False)

    gates = {
        "validation_positive": candidate_periods["validation_2024"]["total_return"] > 0,
        "holdout_positive": candidate_periods["holdout_2025"]["total_return"] > 0,
        "final_positive": candidate_periods["final_2026h1"]["total_return"] > 0,
        "full_cagr_uplift": candidate_full["cagr"] >= control_full["cagr"] + design["post_oos"]["full_cagr_uplift_min"],
        "full_sharpe_floor": candidate_full["sharpe"] >= control_full["sharpe"] - design["post_oos"]["full_sharpe_loss_max"],
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
        "selected_recipe": selected_name,
        "selected_allocations": selected_recipe.allocations,
        "oos_opened": True,
        "standalone_selection_passed": passed,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "historical_parameter_search_pristine": False,
        "reason_not_pristine": "component outcomes were known before this cycle",
    }
    summary = {
        **decision,
        "selection": selection,
        "candidate_full": candidate_full,
        "candidate_periods": candidate_periods,
        "control_full": control_full,
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
