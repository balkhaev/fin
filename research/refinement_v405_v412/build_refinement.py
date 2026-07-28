#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

CHECKPOINT = ROOT / "docs" / "checkpoints" / "v138" / "CHECKPOINT_V138.json"
V136_SUMMARY = (
    ROOT
    / "research"
    / "active_v131_v138"
    / "results"
    / "v136"
    / "summary.json"
)
RESEARCH_SOURCE = (
    ROOT / "research" / "active_v131_v138" / "source" / "run_research.py"
)
DATA_REQUIREMENTS = ROOT / "research" / "active_v131_v138" / "DATA_REQUIREMENTS.json"
DESIGN = HERE / "V405_V412_DESIGN.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(value: float) -> str:
    return f"{value:+.2%}"


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    checkpoint = read_json(CHECKPOINT)
    v136_summary = read_json(V136_SUMMARY)
    design = read_json(DESIGN)

    assert checkpoint["primary_control"] == "V75_ATLAS_NX"
    assert checkpoint["live_ready"] is False
    assert checkpoint["real_leverage_authorized"] is False
    assert checkpoint["decision"]["promoted_candidates"] == []
    assert v136_summary["live_ready"] is False
    assert v136_summary["real_leverage_authorized"] is False

    v75 = checkpoint["original_v75"]
    v136 = checkpoint["candidates"]["V136_execution_plateau"]["candidate_full"]
    summary_v136 = v136_summary["candidate_full"]
    for key in (
        "total_return",
        "cagr",
        "max_drawdown",
        "sharpe",
        "annual_turnover",
        "costs",
    ):
        assert abs(float(v136[key]) - float(summary_v136[key])) < 1e-12

    deltas = {
        "total_return_percentage_points": 100.0
        * (float(v136["total_return"]) - float(v75["total_return"])),
        "cagr_percentage_points": 100.0
        * (float(v136["cagr"]) - float(v75["cagr"])),
        "sharpe": float(v136["sharpe"]) - float(v75["sharpe"]),
        "max_drawdown_percentage_points": 100.0
        * (float(v136["max_drawdown"]) - float(v75["max_drawdown"])),
        "turnover_reduction_fraction": 1.0
        - float(v136["annual_turnover"]) / float(v75["annual_turnover"]),
        "cost_reduction_fraction": 1.0
        - float(v136["costs"]) / float(v75["costs"]),
        "absolute_cost_saving": float(v75["costs"]) - float(v136["costs"]),
        "funding_pnl_delta": float(v136["funding_pnl"]) - float(v75["funding_pnl"]),
    }

    historical_checks = checkpoint["candidates"]["V136_execution_plateau"]["checks"]
    historical_promotion_passed = bool(all(historical_checks.values()))
    assert historical_promotion_passed is False

    registry = {
        "program": design["program"],
        "generated_from_committed_evidence": True,
        "files": {
            str(CHECKPOINT.relative_to(ROOT)): sha256_file(CHECKPOINT),
            str(V136_SUMMARY.relative_to(ROOT)): sha256_file(V136_SUMMARY),
            str(RESEARCH_SOURCE.relative_to(ROOT)): sha256_file(RESEARCH_SOURCE),
            str(DATA_REQUIREMENTS.relative_to(ROOT)): sha256_file(DATA_REQUIREMENTS),
            str(DESIGN.relative_to(ROOT)): sha256_file(DESIGN),
        },
        "canonical_v75_source_expected_sha256": design["source_registry"][
            "canonical_v75_source_sha256"
        ],
    }
    write_json(RESULTS / "SOURCE_REGISTRY.json", registry)

    scorecard = {
        "champion": "V75_ATLAS_NX",
        "execution_shadow": "V136_EXECUTION_PLATEAU",
        "v75_full": v75,
        "v136_full": v136,
        "v136_minus_v75": deltas,
        "historical_v136_checks": historical_checks,
        "historical_v136_promotion_passed": historical_promotion_passed,
        "interpretation": {
            "v136_full_cagr_uplift_is_material": False,
            "v136_turnover_reduction_reaches_frozen_10pct_gate": bool(
                deltas["turnover_reduction_fraction"] >= 0.10
            ),
            "v136_role": "shadow_only",
            "v75_role": "primary_paper_shadow_champion",
        },
    }
    write_json(RESULTS / "CHAMPION_SCORECARD.json", scorecard)

    decision = {
        "status": "champion_refinement_started",
        "historical_parameter_search_closed": True,
        "primary_champion": "V75_ATLAS_NX",
        "execution_shadow": "V136_EXECUTION_PLATEAU",
        "historical_v136_promotion_passed": False,
        "paper_forward_earliest_start": design["paper_forward"]["earliest_start"],
        "paper_forward_gates": design["paper_forward"],
        "new_sleeve_allocation": 0.0,
        "v136_capital_allocation": 0.0,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "integration_permitted": False,
    }
    write_json(RESULTS / "REFINEMENT_DECISION.json", decision)

    report = f"""# V405–V412 — champion refinement

## Frozen roles

- V75 remains the primary paper/shadow champion.
- V136 remains an exact execution-shadow; it is not promoted historically.
- V28 remains the mandatory control.
- V285 and V365 remain rejected-after-OOS anti-controls.

## Historical V75 vs V136

| Metric | V75 | V136 | Delta |
|---|---:|---:|---:|
| Full CAGR | {v75['cagr']:.2%} | {v136['cagr']:.2%} | {deltas['cagr_percentage_points']:+.3f} pp |
| Total return | {v75['total_return']:.2%} | {v136['total_return']:.2%} | {deltas['total_return_percentage_points']:+.3f} pp |
| Sharpe | {v75['sharpe']:.3f} | {v136['sharpe']:.3f} | {deltas['sharpe']:+.3f} |
| Max DD | {v75['max_drawdown']:.2%} | {v136['max_drawdown']:.2%} | {deltas['max_drawdown_percentage_points']:+.3f} pp |
| Turnover | {v75['annual_turnover']:.2f}x | {v136['annual_turnover']:.2f}x | {deltas['turnover_reduction_fraction']:.2%} reduction |
| Modelled costs | ${v75['costs']:,.2f} | ${v136['costs']:,.2f} | ${deltas['absolute_cost_saving']:,.2f} saving |

V136 improved full CAGR by only {deltas['cagr_percentage_points']:.3f} percentage points and reduced turnover by {deltas['turnover_reduction_fraction']:.2%}. It therefore missed the frozen 0.5 pp uplift and 10% turnover-reduction gates. This is too small to justify replacing V75 from historical evidence.

## Refinement target

The next evidence is forward execution quality, not another backtest grid:

1. minimum {design['paper_forward']['minimum_calendar_days']} calendar days;
2. at least {design['paper_forward']['minimum_v136_target_changes']} V136 target changes;
3. zero reconciliation breaks and 100% source-hash matches;
4. V136 turnover reduction at least {design['paper_forward']['v136_turnover_reduction_min']:.0%};
5. V136 net paper return not below V75;
6. V136 drawdown no more than {design['paper_forward']['v136_max_drawdown_worsening_max']:.0%} worse;
7. paper slippage no more than {design['paper_forward']['paper_slippage_to_model_ratio_max']:.1f}x the frozen model.

```text
historical_parameter_search = closed
V75 role                    = primary paper/shadow
V136 role                   = execution shadow only
new sleeve allocation       = 0%
live_ready                  = false
real_leverage_authorized    = false
```
"""
    (RESULTS / "REFINEMENT_PLAN_RU.md").write_text(report)

    print(json.dumps({"scorecard": scorecard, "decision": decision}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
