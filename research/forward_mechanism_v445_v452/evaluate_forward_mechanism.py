#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common import (
    CHAMPION, PROGRAM, SHADOW, STRATEGIES, clean, read_design, read_states,
    read_telemetry, sha256_file, write_json,
)
from evidence import context_evidence, context_masks, paired_context


def incident_checks(joined: pd.DataFrame) -> dict[str, int]:
    return {
        "reconciliation_breaks": int((~joined["reconciliation_ok"]).sum()),
        "source_hash_mismatches": int((~joined["source_hash_match"]).sum()),
        "stale_rows": int(joined["data_stale"].sum()),
        "incomplete_execution_rows": int((~joined["execution_complete"]).sum()),
    }


def write_manifest(output: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            files[str(path.relative_to(output))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    write_json(output / "MANIFEST.json", {"program": PROGRAM, "files": files})


def write_report(path: Path, result: dict[str, Any]) -> None:
    if result["status"] == "forward_mechanism_validator_ready_no_observations":
        report = f"""# V445–V452 — forward market-mechanism validator

Status: `{result['status']}`.

Наблюдений после frozen start пока нет. Валидатор не является стратегией и не разрешает state gating.

## Primary forward mechanisms

1. `persistent_regime_non_degradation`: V136 должен уменьшить turnover минимум на 10% относительно V75 в режимах длительностью более пяти дней, не ухудшая net return и drawdown.
2. `switching_regime_execution_benefit`: то же требование применяется к high-switching context, определённому только по development state history.

## Diagnostic contexts

`early_state`, `novel`, `high_transition_surprise` публикуются независимо от результата и не могут быть удалены после наблюдения.

```text
paper_observation_count                0
market_mechanism_claim_supported       false
capital_change_authorized              false
strategy_parameter_change_authorized   false
live_ready                             false
real_leverage_authorized               false
```
"""
    else:
        global_item = result["global_context"]
        report = f"""# V445–V452 — forward market-mechanism validator

Status: `{result['status']}`.

```text
period                {result['period_start']} — {result['period_end']}
calendar days         {result['calendar_days']}
observation rows      {result['observation_rows']}
all context           {global_item['classification']}
mechanism supported   {str(result['market_mechanism_claim_supported']).lower()}
```

Context-level results are stored in `CONTEXT_COMPARISON.csv` and `MECHANISM_EVIDENCE.json`.

No context result authorizes capital or strategy changes.
"""
    path.write_text(report, encoding="utf-8")


def initialize(design_path: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    design = read_design(design_path)
    result = {
        "program": PROGRAM,
        "status": "forward_mechanism_validator_ready_no_observations",
        "earliest_observation_start": design["earliest_observation_start"],
        "paper_observation_count": 0,
        "frozen_contexts": design["frozen_contexts"],
        "primary_mechanism_hypotheses": design["primary_mechanism_hypotheses"],
        "diagnostic_contexts": design["diagnostic_contexts"],
        "uncertainty_protocol": design["uncertainty_protocol"],
        "market_mechanism_claim_supported": False,
        "capital_change_authorized": False,
        "strategy_parameter_change_authorized": False,
        "new_sleeve_allocation": 0.0,
        "v136_capital_allocation": 0.0,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "design_sha256": sha256_file(design_path),
    }
    write_json(output / "FORWARD_MECHANISM_DECISION.json", result)
    write_json(output / "CONTEXT_DEFINITIONS.json", {
        "frozen_contexts": design["frozen_contexts"],
        "reference_thresholds": design["reference_thresholds"],
        "primary_mechanism_hypotheses": design["primary_mechanism_hypotheses"],
        "diagnostic_contexts": design["diagnostic_contexts"],
    })
    pd.DataFrame(columns=[
        "context", "classification", "paired_days", "v136_target_changes",
        "v75_return", "v136_return", "net_return_delta", "v75_turnover",
        "v136_turnover", "turnover_reduction", "max_drawdown_worsening",
        "v136_slippage_to_model_ratio",
    ]).to_csv(output / "CONTEXT_COMPARISON.csv", index=False)
    write_report(output / "REPORT_RU.md", result)
    write_manifest(output)
    return result


def evaluate(states_path: Path, telemetry_path: Path, design_path: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    design = read_design(design_path)
    states, thresholds = read_states(states_path, design)
    telemetry = read_telemetry(telemetry_path)
    if telemetry.empty:
        return initialize(design_path, output)
    earliest = pd.Timestamp(design["earliest_observation_start"], tz="UTC")
    if telemetry["date"].min() < earliest:
        raise ValueError("telemetry predates frozen forward start")
    joined = telemetry.merge(states, left_on="date", right_index=True, how="left", validate="many_to_one")
    missing_state_rows = int(joined["state_id"].isna().sum())
    joined.to_csv(output / "joined_forward_mechanism_telemetry.csv", index=False)
    if missing_state_rows:
        raise ValueError(f"missing state rows: {missing_state_rows}")

    evidence: dict[str, Any] = {}
    comparison_rows: list[dict[str, Any]] = []
    for context, mask in context_masks(joined, thresholds).items():
        item = context_evidence(context, paired_context(joined, mask), design)
        evidence[context] = item
        point = item["point_metrics"]
        comparison_rows.append({
            "context": context,
            "classification": item["classification"],
            "paired_days": point["paired_days"],
            "v136_target_changes": point["v136"].get("target_changes", 0),
            "v75_return": point["v75"].get("total_return"),
            "v136_return": point["v136"].get("total_return"),
            "net_return_delta": point["net_return_delta"],
            "v75_turnover": point["v75"].get("turnover"),
            "v136_turnover": point["v136"].get("turnover"),
            "turnover_reduction": point["turnover_reduction"],
            "max_drawdown_worsening": point["max_drawdown_worsening"],
            "v136_slippage_to_model_ratio": point["v136_slippage_to_model_ratio"],
        })
    pd.DataFrame(comparison_rows).to_csv(output / "CONTEXT_COMPARISON.csv", index=False)
    write_json(output / "MECHANISM_EVIDENCE.json", evidence)

    incidents = incident_checks(joined)
    all_context = evidence["all"]
    primary = {
        name: evidence[hypothesis["context"]]
        for name, hypothesis in design["primary_mechanism_hypotheses"].items()
    }
    incidents_clear = all(value == 0 for value in incidents.values())
    global_supported = all_context["classification"] == "supported_forward_mechanism"
    primary_supported = all(
        item["classification"] == "supported_forward_mechanism"
        for item in primary.values()
    )
    supported = bool(incidents_clear and global_supported and primary_supported)
    result = {
        "program": PROGRAM,
        "status": "supported_forward_mechanism_no_capital_authority" if supported
        else "forward_mechanism_not_yet_supported",
        "period_start": telemetry["date"].min(),
        "period_end": telemetry["date"].max(),
        "calendar_days": int((telemetry["date"].max() - telemetry["date"].min()).days + 1),
        "observation_rows": len(telemetry),
        "paired_dates": int(telemetry.loc[telemetry.strategy_id == CHAMPION, "date"].nunique()),
        "reference_thresholds": thresholds,
        "incidents": incidents,
        "all_incidents_clear": incidents_clear,
        "global_context": all_context,
        "primary_mechanisms": primary,
        "context_classifications": {name: item["classification"] for name, item in evidence.items()},
        "market_mechanism_claim_supported": supported,
        "capital_change_authorized": False,
        "strategy_parameter_change_authorized": False,
        "new_sleeve_allocation": 0.0,
        "v136_capital_allocation": 0.0,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "source_hashes": {
            "states": sha256_file(states_path),
            "telemetry": sha256_file(telemetry_path),
            "design": sha256_file(design_path),
        },
    }
    write_json(output / "FORWARD_MECHANISM_DECISION.json", result)
    write_report(output / "REPORT_RU.md", result)
    write_manifest(output)
    return result


def synthetic_fixture(root: Path, design: dict[str, Any]) -> tuple[Path, Path]:
    history = pd.date_range("2021-01-01", "2027-03-04", freq="1D", tz="UTC")
    forward_start = pd.Timestamp(design["earliest_observation_start"], tz="UTC")
    labels = np.array([
        "deleveraging", "transition", "rotation",
        "speculative_risk_on", "transition_2", "calm_risk_on",
    ])
    state_ids = np.zeros(len(history), dtype=int)
    durations = np.ones(len(history), dtype=int)
    current = 0
    duration = 0
    for i, date in enumerate(history):
        if date < forward_start:
            change = (i % 17 == 0) or (i % 113 == 0)
        else:
            relative = (date - forward_start).days
            change = (relative < 80 and relative % 2 == 0) or (relative >= 80 and relative % 19 == 0)
        if change:
            current = (current + 1) % len(labels)
            duration = 1
        else:
            duration += 1
        state_ids[i] = current
        durations[i] = duration
    state = pd.DataFrame(index=history)
    state["state_id"] = state_ids
    state["state_label"] = labels[state_ids]
    state["novelty_flag"] = np.arange(len(history)) % 37 == 0
    state["novelty_ratio"] = np.where(state["novelty_flag"], 1.2, 0.5)
    state["transition_surprise"] = 0.1 + 0.03 * state["state_id"]
    state.loc[state["state_id"].ne(state["state_id"].shift(1)), "transition_surprise"] = 1.5
    state["state_duration_days"] = durations
    states_path = root / "states.csv"
    state.to_csv(states_path)

    dates = pd.date_range(forward_start, periods=220, freq="1D", tz="UTC")
    rows: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        equity = high = 10_000.0
        for i, date in enumerate(dates):
            base_return = 0.00035 + 0.0015 * math.sin(i / 13.0)
            if strategy == SHADOW:
                daily_return, turnover, paper_slippage = base_return + 0.00002, (0.018 if i % 5 == 0 else 0.0), 0.9
            elif strategy == CHAMPION:
                daily_return, turnover, paper_slippage = base_return, (0.024 if i % 5 == 0 else 0.0), 1.1
            else:
                daily_return, turnover, paper_slippage = base_return - 0.00005, (0.020 if i % 5 == 0 else 0.0), 1.0
            equity *= 1.0 + daily_return
            high = max(high, equity)
            epoch = i // 5
            rows.append({
                "timestamp": date.isoformat(), "strategy_id": strategy,
                "source_bundle_sha256": "a" * 64,
                "target_hash": hashlib.sha256(f"{strategy}-{epoch}".encode()).hexdigest(),
                "realized_position_hash": hashlib.sha256(f"pos-{strategy}-{epoch}".encode()).hexdigest(),
                "gross_target": 0.4, "gross_realized": 0.4, "turnover": turnover,
                "modelled_slippage_bps": 1.0, "paper_slippage_bps": paper_slippage,
                "net_return": daily_return, "equity": equity, "drawdown": equity / high - 1.0,
                "reconciliation_ok": True, "source_hash_match": True,
                "data_stale": False, "execution_complete": True,
            })
    telemetry_path = root / "telemetry.csv"
    pd.DataFrame(rows).to_csv(telemetry_path, index=False)
    return states_path, telemetry_path


def self_test(design_path: Path) -> None:
    design = read_design(design_path)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        states, telemetry = synthetic_fixture(root, design)
        result = evaluate(states, telemetry, design_path, root / "results")
        assert result["calendar_days"] == 220
        assert result["all_incidents_clear"] is True
        assert result["context_classifications"]["all"] in {
            "provisional_support", "supported_forward_mechanism"
        }
        assert result["context_classifications"]["persistent_state"] != "insufficient_evidence"
        assert result["context_classifications"]["high_switching"] != "insufficient_evidence"
        assert (root / "results/MECHANISM_EVIDENCE.json").exists()
    print("V445-V452 forward mechanism validator self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", type=Path)
    parser.add_argument("--telemetry", type=Path)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.design)
        return 0
    if args.initialize:
        result = initialize(args.design, args.output)
    else:
        if args.states is None or args.telemetry is None:
            raise SystemExit("--states and --telemetry are required for evaluation")
        result = evaluate(args.states, args.telemetry, args.design, args.output)
    print(json.dumps(clean(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
