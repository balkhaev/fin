#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
V469_SOURCE = REPO_ROOT / "research" / "frontier_barbell_v469_v476" / "run_research.py"
_spec = importlib.util.spec_from_file_location("v469_frontier_parent", V469_SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import V469 parent: {V469_SOURCE}")
v469 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = v469
_spec.loader.exec_module(v469)

v461 = v469.v461
v453 = v469.v453
PROGRAM = "V477_V484_FRONTIER_OOS_AUDIT"
INITIAL_EQUITY = v453.INITIAL_EQUITY
PERIODS = v453.PERIODS
AUDITS = v453.AUDITS

MODEL_RECIPES: dict[str, dict[str, float]] = {
    "static_40_60": {"static": 1.0},
    "persistent_budget": {"persistent": 1.0},
    "vol22_budget": {"vol22": 1.0},
    "barbell_equal": {"persistent": 0.5, "vol22": 0.5},
    "barbell_static_vol_equal": {"static": 0.5, "vol22": 0.5},
    "triple_equal": {"static": 1 / 3, "persistent": 1 / 3, "vol22": 1 / 3},
}


def clean(value: Any) -> Any:
    return v453.clean(value)


def write_json(path: Path, value: Any) -> None:
    v453.write_json(path, value)


def sha256_file(path: Path) -> str:
    return v453.sha256_file(path)


def constant_weights(index: pd.DatetimeIndex, v285: float, v365: float) -> pd.DataFrame:
    table = pd.DataFrame(index=index)
    table["w_v285"] = float(v285)
    table["w_v365"] = float(v365)
    table["w_cash"] = float(1.0 - v285 - v365)
    table["risky_budget"] = float(v285 + v365)
    table["predicted_vol"] = 0.0
    return table


def all_period_metrics(account: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        name: v453.metrics(v453.cut(account, bounds))
        for name, bounds in PERIODS.items()
    }


def state_attribution(account: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state, part in account.groupby("state_label", sort=True):
        returns = pd.to_numeric(part["net_return"], errors="coerce").fillna(0.0)
        rows.append(
            {
                "state_label": str(state),
                "days": int(len(part)),
                "total_return": float(np.prod(1.0 + returns.to_numpy(float)) - 1.0),
                "mean_daily_return": float(returns.mean()),
                "negative_days": int((returns < 0).sum()),
                "negative_pnl_proxy": float(returns[returns < 0].sum()),
                "average_risky_budget": float(part["risky_budget"].mean()),
                "average_gross": float(part["gross"].mean()),
            }
        )
    return rows


def correlation_diagnostics(panel: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    scopes = {
        "development": PERIODS["development_2021_2023"],
        "oos_2024_2026h1": ("2024-01-01", "2026-07-01"),
        "full": PERIODS["full"],
    }
    for name, bounds in scopes.items():
        part = v453.cut(panel, bounds)
        returns = part[["ret_v285", "ret_v365"]].astype(float)
        out[name] = {
            "days": int(len(part)),
            "correlation": float(returns.corr().iloc[0, 1]),
            "v285_mean": float(returns.ret_v285.mean()),
            "v365_mean": float(returns.ret_v365.mean()),
            "opposite_sign_share": float((np.sign(returns.ret_v285) != np.sign(returns.ret_v365)).mean()),
        }
    return out


def self_test() -> None:
    index = pd.date_range("2021-01-01", periods=900, freq="1D", tz="UTC")
    rng = np.random.default_rng(477)
    panel = pd.DataFrame(index=index)
    panel["ret_v285"] = rng.normal(0.00035, 0.007, len(index))
    panel["ret_v365"] = rng.normal(0.00030, 0.006, len(index))
    panel["turnover_v285"] = 0.01
    panel["turnover_v365"] = 0.02
    panel["gross_v285"] = 0.50
    panel["gross_v365"] = 0.35
    labels = np.array(
        ["deleveraging", "transition", "rotation", "speculative_risk_on", "transition_2", "calm_risk_on"]
    )
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

    component_policies = v469.component_policies()
    component_weights = {
        name: v461.generate_overlay_weights(panel, policy)
        for name, policy in component_policies.items()
    }
    accounts = {
        name: v453.simulate(panel, table, AUDITS[0])
        for name, table in component_weights.items()
    }
    model = v469.weighted_blend(accounts, MODEL_RECIPES["triple_equal"])
    assert np.isfinite(model.equity).all()
    assert len(state_attribution(model)) == 6
    corr = correlation_diagnostics(panel)
    assert -1.0 <= corr["full"]["correlation"] <= 1.0
    raw = v453.simulate(panel, constant_weights(index, 1.0, 0.0), AUDITS[0])
    assert np.isfinite(raw.equity).all()
    print("V477-V484 frontier OOS audit self-test passed")


def render_report(summary: dict[str, Any]) -> str:
    leader = summary["leaders"]
    lines = [
        "# V477–V484 — exact frontier OOS audit",
        "",
        f"Status: `{summary['status']}`.",
        "",
        "No policy is selected and no capital decision is authorized.",
        "",
        "```text",
        f"Full CAGR leader      {leader['full_cagr']['model']}  {leader['full_cagr']['value']:.2%}",
        f"Full Sharpe leader    {leader['full_sharpe']['model']}  {leader['full_sharpe']['value']:.3f}",
        f"Best Max DD           {leader['max_drawdown']['model']}  {leader['max_drawdown']['value']:.2%}",
        f"All OOS periods +     {', '.join(summary['all_oos_periods_positive']) or 'none'}",
        "```",
    ]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    panel = v453.load_panel(args.v285, args.v365)
    component_policies = v469.component_policies()
    component_weights = {
        name: v461.generate_overlay_weights(panel, policy)
        for name, policy in component_policies.items()
    }
    component_weights["raw_v285"] = constant_weights(panel.index, 1.0, 0.0)
    component_weights["raw_v365"] = constant_weights(panel.index, 0.0, 1.0)

    base_components = {
        name: v453.simulate(panel, table, AUDITS[0])
        for name, table in component_weights.items()
    }

    model_accounts: dict[str, pd.DataFrame] = {
        "V285_raw": base_components["raw_v285"],
        "V365_raw": base_components["raw_v365"],
    }
    model_weights: dict[str, pd.DataFrame] = {
        "V285_raw": component_weights["raw_v285"],
        "V365_raw": component_weights["raw_v365"],
    }
    for model, allocations in MODEL_RECIPES.items():
        model_accounts[model] = v469.weighted_blend(base_components, allocations)
        model_weights[model] = v469.weights_blend(component_weights, allocations)

    period_rows: list[dict[str, Any]] = []
    yearly_frames: list[pd.DataFrame] = []
    state_rows: list[dict[str, Any]] = []
    for model, account in model_accounts.items():
        period_metrics = all_period_metrics(account)
        for period, item in period_metrics.items():
            period_rows.append({"model": model, "period": period, **item})
        yearly_frames.append(v453.yearly_returns(account, model))
        for row in state_attribution(account):
            state_rows.append({"model": model, **row})
        account.to_csv(output / f"equity_{model}.csv")
        model_weights[model].to_csv(output / f"weights_{model}.csv")

    period_table = pd.DataFrame(period_rows)
    period_table.to_csv(output / "model_period_metrics.csv", index=False)
    yearly = yearly_frames[0]
    for frame in yearly_frames[1:]:
        yearly = yearly.merge(frame, on="year", how="outer")
    yearly.to_csv(output / "yearly_returns.csv", index=False)
    pd.DataFrame(state_rows).to_csv(output / "state_attribution.csv", index=False)

    audit_rows: list[dict[str, Any]] = []
    for audit in AUDITS:
        audit_components = {
            name: v453.simulate(panel, component_weights[name], audit)
            for name in component_weights
        }
        audit_models: dict[str, pd.DataFrame] = {
            "V285_raw": audit_components["raw_v285"],
            "V365_raw": audit_components["raw_v365"],
        }
        for model, allocations in MODEL_RECIPES.items():
            audit_models[model] = v469.weighted_blend(audit_components, allocations)
        for model, account in audit_models.items():
            audit_rows.append({"audit": audit.name, "model": model, **v453.metrics(account)})
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "audit_full_metrics.csv", index=False)

    full = period_table[period_table.period == "full"].copy()
    def leader(column: str, ascending: bool = False) -> dict[str, Any]:
        row = full.sort_values(column, ascending=ascending).iloc[0]
        return {"model": str(row.model), "value": float(row[column])}

    oos_periods = ("validation_2024", "holdout_2025", "final_2026h1")
    all_positive: list[str] = []
    for model in model_accounts:
        subset = period_table[
            (period_table.model == model) & period_table.period.isin(oos_periods)
        ]
        if len(subset) == len(oos_periods) and (subset.total_return > 0).all():
            all_positive.append(model)

    robust_rows: list[dict[str, Any]] = []
    for model in model_accounts:
        base_full = audit_table[(audit_table.audit == "base") & (audit_table.model == model)].iloc[0]
        severe = audit_table[(audit_table.audit == "severe") & (audit_table.model == model)].iloc[0]
        extreme = audit_table[(audit_table.audit == "extreme") & (audit_table.model == model)].iloc[0]
        delay = audit_table[(audit_table.audit == "delay_1d") & (audit_table.model == model)].iloc[0]
        oos = period_table[(period_table.model == model) & period_table.period.isin(oos_periods)]
        robust_rows.append(
            {
                "model": model,
                "all_oos_periods_positive": bool(len(oos) == 3 and (oos.total_return > 0).all()),
                "full_cagr": float(base_full.cagr),
                "full_sharpe": float(base_full.sharpe),
                "full_max_drawdown": float(base_full.max_drawdown),
                "severe_full_cagr_positive": bool(float(severe.cagr) > 0),
                "extreme_full_cagr_positive": bool(float(extreme.cagr) > 0),
                "delay_full_cagr_positive": bool(float(delay.cagr) > 0),
                "worst_calendar_year": float(yearly[model].min()),
                "max_risky_budget": float(base_full.max_risky_budget),
                "diagnostic_robustness_pass": bool(
                    len(oos) == 3
                    and (oos.total_return > 0).all()
                    and float(base_full.max_drawdown) >= -0.10
                    and float(severe.cagr) > 0
                    and float(extreme.cagr) > 0
                    and float(delay.cagr) > 0
                    and float(yearly[model].min()) >= -0.08
                    and float(base_full.max_risky_budget) <= 1.25 + 1e-12
                ),
            }
        )
    robustness = pd.DataFrame(robust_rows)
    robustness.to_csv(output / "diagnostic_robustness.csv", index=False)

    summary = {
        "program": PROGRAM,
        "status": "frontier_oos_audit_complete_no_capital_authority",
        "oos_opened_for_diagnostic_only": True,
        "selection_performed": False,
        "component_history_already_known_before_cycle": True,
        "input_sha256": {
            "v285": sha256_file(args.v285),
            "v365": sha256_file(args.v365),
            "v469_source": sha256_file(V469_SOURCE),
        },
        "leaders": {
            "full_cagr": leader("cagr"),
            "full_sharpe": leader("sharpe"),
            "max_drawdown": leader("max_drawdown"),
        },
        "all_oos_periods_positive": all_positive,
        "diagnostic_robustness_passes": list(
            robustness.loc[robustness.diagnostic_robustness_pass, "model"]
        ),
        "period_metrics": period_table.to_dict(orient="records"),
        "audit_full_metrics": audit_table.to_dict(orient="records"),
        "yearly_returns": yearly.to_dict(orient="records"),
        "diversification": correlation_diagnostics(panel),
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "capital_change_authorized": False,
    }
    write_json(output / "summary.json", summary)
    (output / "REPORT_RU.md").write_text(render_report(summary), encoding="utf-8")
    manifest = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            manifest[str(path.relative_to(output))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    write_json(output / "MANIFEST.json", {"program": PROGRAM, "files": manifest})
    print(json.dumps(clean(summary), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v285", type=Path)
    parser.add_argument("--v365", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if None in (args.v285, args.v365, args.output):
        raise SystemExit("--v285, --v365 and --output are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
