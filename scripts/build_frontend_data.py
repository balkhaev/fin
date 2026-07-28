#!/usr/bin/env python3
"""Build the static FIN control-room dataset from committed research evidence.

The builder is dependency-free and deliberately fail-closed: missing live evidence is
rendered as a blocker, never as an implicit pass. It can run in CI, during a Pages
build, or before serving ``frontend/`` locally.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "frontend" / "data" / "dashboard.json"

V517_FALLBACK: dict[str, Any] = {
    "status": "historical_target_met_non_pristine_no_capital_authority",
    "historical_50pct_target_met": True,
    "modeled_robustness_gates_passed": True,
    "live_ready": False,
    "real_leverage_authorized": False,
    "parameters_informed_by_known_history": True,
    "program_level_holdout_pristine": False,
    "base_full": {
        "total_return": 8.483407210943097,
        "cagr": 0.5054770638530515,
        "sharpe": 1.4597904254441398,
        "max_drawdown": -0.23679792568887836,
        "worst_rolling_365": -0.20245657086746538,
        "average_target_leverage": 1.244735924265072,
        "maximum_target_leverage": 2.075,
        "maximum_close_gross": 2.1529717115544655,
        "annual_meta_turnover": 5.119466224899777,
    },
    "severe_full": {"cagr": 0.4024272799015174, "sharpe": 1.2389616427323742, "max_drawdown": -0.2915826292615754},
    "extreme_full": {"cagr": 0.2964196219347963, "sharpe": 0.984563118725719, "max_drawdown": -0.33647008092978925},
    "delay_full": {"cagr": 0.5012808687988206, "sharpe": 1.44714545854028, "max_drawdown": -0.2482846633718191},
    "annual_returns": [
        {"year": 2021, "return": 1.5037196837604418},
        {"year": 2022, "return": 0.0913891403082916},
        {"year": 2023, "return": 0.31409935582976867},
        {"year": 2024, "return": 0.9988513206232994},
        {"year": 2025, "return": 0.2817749914344949},
        {"year": 2026, "return": 0.030809897301597555},
    ],
    "v75_equivalence": {
        "metrics": {"total_return": 3.355413143881263, "cagr": 0.30682098978536976, "sharpe": 1.3294516915576933, "max_drawdown": -0.21591803526892406},
        "annual_returns": {"2021": 1.044015828086435, "2022": 0.01081155462862804, "2023": 0.1485191407183737, "2024": 0.4195522030251728, "2025": 0.23816495433729012, "2026": 0.04425555083890087},
    },
    "evidence_boundary": {
        "account_level_only": True,
        "position_level_margin_replay_complete": False,
        "forward_period_complete": False,
        "parameters_informed_by_known_history": True,
    },
}

V509_FALLBACK: dict[str, Any] = {
    "status": "rejected_after_frozen_oos",
    "base_full": {"cagr": 0.4522, "sharpe": 1.402, "max_drawdown": -0.2648, "average_leverage": 0.959, "max_leverage": 1.95},
    "base_validation_2024": {"total_return": 0.8652},
    "base_holdout_2025": {"total_return": 0.1756},
    "base_final_2026h1": {"total_return": -0.0210},
}

MARKET_FALLBACK: dict[str, Any] = {
    "open_time": "2026-06-30 00:00:00+00:00",
    "trend": -0.8797666900730305,
    "breadth": -0.5714285714285715,
    "stress": -0.1098542085145394,
    "rotation": -0.5336667326224068,
    "liquidity": -1.2194456623966021,
    "leverage": -0.6679295370641978,
    "state_id": 1,
    "state_label": "transition",
    "assignment_confidence": 0.5206229962360047,
    "novelty_ratio": 0.4839489732533799,
    "novelty_flag": False,
    "transition_surprise": 0.17676212621380524,
    "state_duration_days": 12,
}


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return json.loads(json.dumps(fallback))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def iso_date(value: str) -> str:
    return value[:10] if value else ""


def latest_market_state(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    context_path = root / "research/market_state_v421_v428/results/CURRENT_MARKET_CONTEXT.json"
    if context_path.is_file():
        raw = load_json(context_path, MARKET_FALLBACK)
        latest = raw.get("latest") if isinstance(raw.get("latest"), dict) else raw
        return dict(latest), [], str(context_path.relative_to(root))

    daily_path = root / "research/market_state_v413_v420/results/market_state_daily.csv"
    rows: list[dict[str, Any]] = []
    if daily_path.is_file():
        with daily_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(row)
        if rows:
            recent = rows[-90:]
            latest = recent[-1]
            parsed = {
                "open_time": latest.get("open_time", ""),
                "trend": number(latest.get("trend")),
                "breadth": number(latest.get("breadth")),
                "stress": number(latest.get("stress")),
                "rotation": number(latest.get("rotation")),
                "liquidity": number(latest.get("liquidity")),
                "leverage": number(latest.get("leverage")),
                "state_id": int(number(latest.get("state_id"))),
                "state_label": latest.get("state_label", "unknown"),
                "assignment_confidence": number(latest.get("assignment_confidence")),
                "novelty_ratio": number(latest.get("novelty_ratio")),
                "novelty_flag": boolean(latest.get("novelty_flag")),
                "transition_surprise": number(latest.get("transition_surprise")),
                "state_duration_days": int(number(latest.get("state_duration_days"))),
            }
            history = [
                {
                    "date": iso_date(row.get("open_time", "")),
                    "state": row.get("state_label", "unknown"),
                    "confidence": number(row.get("assignment_confidence")),
                    "novelty": number(row.get("novelty_ratio")),
                }
                for row in recent
            ]
            return parsed, history, str(daily_path.relative_to(root))

    return dict(MARKET_FALLBACK), [], "embedded archived fallback"


def synthetic_equity_from_annual(annual: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    equity = 10_000.0
    points = [{"date": "2021-01-01", "equity": equity, "drawdown": 0.0, "leverage": 0.97, "close_gross": 0.97, "state": "base", "guard": False}]
    high = equity
    for item in annual:
        equity *= 1.0 + number(item.get("return"))
        high = max(high, equity)
        points.append(
            {
                "date": f"{int(item.get('year', 0))}-12-31",
                "equity": equity,
                "drawdown": equity / high - 1.0,
                "leverage": 0.97,
                "close_gross": 0.97,
                "state": "base",
                "guard": False,
            }
        )
    return points


def equity_curve(root: Path, annual: list[dict[str, Any]], limit: int = 320) -> tuple[list[dict[str, Any]], str]:
    path = root / "research/v75_tristate_guard_v517_v524/results/equity_primary_base.csv"
    if not path.is_file():
        return synthetic_equity_from_annual(annual), "annual-return fallback"

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    "date": iso_date(raw.get("open_time", "")),
                    "equity": number(raw.get("equity")),
                    "drawdown": number(raw.get("drawdown_open")),
                    "leverage": number(raw.get("desired_leverage")),
                    "close_gross": number(raw.get("close_gross")),
                    "state": raw.get("market_state", "base"),
                    "guard": boolean(raw.get("guard_active")),
                    "risk_reduction": boolean(raw.get("risk_reduction")),
                }
            )
    if len(rows) <= limit:
        return rows, str(path.relative_to(root))

    keep = {0, len(rows) - 1}
    step = (len(rows) - 1) / (limit - 1)
    keep.update(round(i * step) for i in range(limit))
    for index, row in enumerate(rows):
        previous = rows[index - 1] if index else row
        if row["guard"] != previous["guard"] or row["state"] != previous["state"] or row.get("risk_reduction"):
            keep.add(index)
    sampled = [rows[index] for index in sorted(keep)]
    return sampled, str(path.relative_to(root))


def metric_record(metrics: dict[str, Any]) -> dict[str, float]:
    return {
        "cagr": number(metrics.get("cagr")),
        "sharpe": number(metrics.get("sharpe")),
        "max_drawdown": number(metrics.get("max_drawdown")),
        "total_return": number(metrics.get("total_return")),
        "average_leverage": number(metrics.get("average_target_leverage", metrics.get("average_leverage"))),
        "maximum_leverage": number(metrics.get("maximum_target_leverage", metrics.get("max_leverage"))),
        "maximum_close_gross": number(metrics.get("maximum_close_gross", metrics.get("max_close_gross"))),
        "turnover": number(metrics.get("annual_meta_turnover", metrics.get("annual_turnover"))),
        "worst_rolling_365": number(metrics.get("worst_rolling_365")),
    }


def build_dashboard(root: Path = ROOT) -> dict[str, Any]:
    v517_path = root / "research/v75_tristate_guard_v517_v524/results/summary.json"
    v509_path = root / "research/v75_trend_hysteresis_v509_v516/results/summary.json"
    checkpoint_path = root / "docs/checkpoints/v138/CHECKPOINT_V138.json"
    forward_path = root / "research/state_telemetry_v429_v436/FORWARD_INSTRUMENTATION_DECISION.json"

    v517 = load_json(v517_path, V517_FALLBACK)
    v509 = load_json(v509_path, V509_FALLBACK)
    checkpoint = load_json(checkpoint_path, {})
    forward = load_json(
        forward_path,
        {
            "status": "forward_instrumentation_ready_no_live_observations",
            "earliest_observation_start": "2026-07-28",
            "paper_observation_count": 0,
            "live_ready": False,
        },
    )

    v75_metrics = v517.get("v75_equivalence", {}).get("metrics", {})
    v136_metrics = checkpoint.get("candidates", {}).get("V136_execution_plateau", {}).get("candidate_full", {})
    if not v136_metrics:
        v136_metrics = {
            "total_return": 3.3717845751233613,
            "cagr": 0.30771296359748646,
            "max_drawdown": -0.21823965731285888,
            "sharpe": 1.3353794862480461,
            "annual_turnover": 9.946932617894685,
            "max_leverage": 1.0431405191032543,
        }
    v509_metrics = v509.get("base_full", {})
    v517_metrics = v517.get("base_full", {})

    annual = list(v517.get("annual_returns", V517_FALLBACK["annual_returns"]))
    curve, curve_source = equity_curve(root, annual)
    market, market_history, market_source = latest_market_state(root)

    strategy_rows = [
        {
            "id": "v75",
            "name": "V75 ATLAS-NX",
            "role": "Primary benchmark",
            "status": "paper / shadow",
            "tone": "neutral",
            "metrics": metric_record(v75_metrics),
            "evidence": "Frozen historical benchmark",
        },
        {
            "id": "v136",
            "name": "V136 Execution Plateau",
            "role": "Execution shadow",
            "status": "shadow only",
            "tone": "info",
            "metrics": metric_record(v136_metrics),
            "evidence": "Lower modeled turnover; historical promotion gate missed",
        },
        {
            "id": "v509",
            "name": "V509 Trend Hysteresis",
            "role": "OOS near-miss",
            "status": "rejected after OOS",
            "tone": "warning",
            "metrics": metric_record(v509_metrics),
            "evidence": "Strong development; frozen full-period gates failed",
        },
        {
            "id": "v517",
            "name": "V517 Tri-state Guard",
            "role": "Modeled return target",
            "status": "research shadow",
            "tone": "accent",
            "metrics": metric_record(v517_metrics),
            "evidence": "50% historical engineering target; non-pristine",
        },
    ]

    evidence = v517.get("evidence_boundary", {})
    shadow_files = (
        root / "src/finruntime/profiles/v517_guard.py",
        root / "config/strategies/v517_tristate_guard_shadow.json",
        root / "scripts/build_v517_shadow_snapshot.py",
        root / "scripts/live_preflight.py",
    )
    shadow_ready = all(path.is_file() for path in shadow_files)
    paper_count = int(number(forward.get("paper_observation_count")))
    readiness = [
        {"id": "shadow", "label": "Shadow runtime", "status": "pass" if shadow_ready else "block", "detail": "V75 snapshot input, V136 execution filter, V517 adapter and journal"},
        {"id": "producer", "label": "Exact V75 target producer", "status": "block", "detail": "Frozen engine must be supplied with the expected SHA-256"},
        {"id": "margin", "label": "Position-level margin replay", "status": "pass" if evidence.get("position_level_margin_replay_complete") else "block", "detail": "Required through 2.075x with zero liquidations and margin buffer"},
        {"id": "forward", "label": "Frozen forward acceptance", "status": "pass" if evidence.get("forward_period_complete") else "block", "detail": f"{paper_count} observations; minimum evidence window is 180 calendar days"},
        {"id": "adapter", "label": "Exchange adapter", "status": "block", "detail": "Testnet, idempotent orders, reduce-only, kill switch and fail-closed reconciliation required"},
    ]

    axes = [
        {"name": "Trend", "value": number(market.get("trend"))},
        {"name": "Breadth", "value": number(market.get("breadth"))},
        {"name": "Stress", "value": number(market.get("stress"))},
        {"name": "Rotation", "value": number(market.get("rotation"))},
        {"name": "Liquidity", "value": number(market.get("liquidity"))},
        {"name": "Leverage", "value": number(market.get("leverage"))},
    ]

    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    commit = os.environ.get("GITHUB_SHA", "working-tree")
    live_ready = all(item["status"] == "pass" for item in readiness)

    return {
        "schema_version": 1,
        "generated_at_utc": generated,
        "source_commit": commit,
        "environment": {
            "mode": "shadow",
            "shadow_ready": shadow_ready,
            "live_ready": live_ready,
            "exchange_submission_available": False,
            "real_leverage_authorized": False,
            "paper_observation_count": paper_count,
            "forward_status": forward.get("status", "unknown"),
        },
        "hero": {
            "strategy": "V517 Tri-state Guard",
            "cagr": number(v517_metrics.get("cagr")),
            "sharpe": number(v517_metrics.get("sharpe")),
            "max_drawdown": number(v517_metrics.get("max_drawdown")),
            "average_leverage": number(v517_metrics.get("average_target_leverage")),
            "maximum_leverage": number(v517_metrics.get("maximum_target_leverage")),
            "status": v517.get("status", "research"),
            "non_pristine": bool(v517.get("parameters_informed_by_known_history", True)),
        },
        "strategies": strategy_rows,
        "stress_scenarios": [
            {"id": "base", "name": "Base", **metric_record(v517.get("base_full", {}))},
            {"id": "severe", "name": "Severe", **metric_record(v517.get("severe_full", {}))},
            {"id": "extreme", "name": "Extreme", **metric_record(v517.get("extreme_full", {}))},
            {"id": "delay", "name": "+1 day delay", **metric_record(v517.get("delay_full", {}))},
        ],
        "annual_returns": annual,
        "equity_curve": curve,
        "market": {
            "as_of": iso_date(str(market.get("open_time", ""))),
            "state": market.get("state_label", "unknown"),
            "duration_days": int(number(market.get("state_duration_days"))),
            "confidence": number(market.get("assignment_confidence")),
            "novelty_ratio": number(market.get("novelty_ratio")),
            "novelty": boolean(market.get("novelty_flag")),
            "transition_surprise": number(market.get("transition_surprise")),
            "axes": axes,
            "history": market_history,
            "archived": True,
        },
        "readiness": readiness,
        "policy": {
            "high_leverage": number(v517.get("primary_policy_spec", {}).get("high_leverage", 2.075)),
            "base_leverage": number(v517.get("primary_policy_spec", {}).get("base_leverage", 0.97)),
            "low_leverage": number(v517.get("primary_policy_spec", {}).get("low_leverage", 0.60)),
            "rebalance_days": int(number(v517.get("primary_policy_spec", {}).get("rebalance_days", 10))),
            "guard_enter_drawdown": number(v517.get("primary_policy_spec", {}).get("guard_enter_drawdown", -0.245)),
            "guard_exit_drawdown": number(v517.get("primary_policy_spec", {}).get("guard_exit_drawdown", -0.18)),
            "guard_cap": number(v517.get("primary_policy_spec", {}).get("guard_cap", 1.0)),
        },
        "governance": {
            "historical_target_met": bool(v517.get("historical_50pct_target_met", False)),
            "modeled_gates_passed": bool(v517.get("modeled_robustness_gates_passed", False)),
            "parameters_informed_by_history": bool(v517.get("parameters_informed_by_known_history", True)),
            "pristine_holdout": bool(v517.get("program_level_holdout_pristine", False)),
            "promotion_permitted": bool(v517.get("promotion_permitted", False)),
            "capital_change_authorized": bool(v517.get("capital_change_authorized", False)),
        },
        "sources": [
            str(v517_path.relative_to(root)) if v517_path.is_file() else "embedded V517 fallback",
            str(v509_path.relative_to(root)) if v509_path.is_file() else "embedded V509 fallback",
            str(checkpoint_path.relative_to(root)) if checkpoint_path.is_file() else "V138 metrics via V517 equivalence",
            market_source,
            curve_source,
            str(forward_path.relative_to(root)) if forward_path.is_file() else "embedded forward decision fallback",
        ],
    }


def validate_dashboard(value: dict[str, Any]) -> None:
    required = {"schema_version", "environment", "hero", "strategies", "stress_scenarios", "equity_curve", "market", "readiness", "governance"}
    missing = required - value.keys()
    if missing:
        raise ValueError(f"dashboard missing keys: {sorted(missing)}")
    if value["environment"].get("live_ready"):
        raise ValueError("dashboard may not claim live readiness before every preflight gate passes")
    if value["environment"].get("exchange_submission_available"):
        raise ValueError("dashboard may not claim exchange submission support")
    if not value["equity_curve"]:
        raise ValueError("equity curve is empty")
    if not any(item.get("status") == "block" for item in value["readiness"]):
        raise ValueError("live readiness must remain fail-closed while evidence is missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Validate an existing dashboard JSON without rewriting it")
    args = parser.parse_args()

    if args.check:
        value = json.loads(args.output.read_text(encoding="utf-8"))
        validate_dashboard(value)
        print(f"validated {args.output}")
        return 0

    value = build_dashboard(args.root.resolve())
    validate_dashboard(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(value['equity_curve'])} equity points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
