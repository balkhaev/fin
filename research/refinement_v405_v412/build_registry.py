#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> int:
    scorecard = read_json(RESULTS / "CHAMPION_SCORECARD.json")
    decision = read_json(RESULTS / "REFINEMENT_DECISION.json")

    registry = {
        "checkpoint": "V412",
        "date": "2026-07-27",
        "historical_parameter_search_closed": True,
        "decision": {
            "primary_paper_shadow_champion": scorecard["champion"],
            "execution_shadow": scorecard["execution_shadow"],
            "mandatory_control": "V28_GROWTH_CONTROL",
            "new_standalone_candidate": None,
            "new_sleeve_allocation": decision["new_sleeve_allocation"],
            "v136_capital_allocation": decision["v136_capital_allocation"],
            "live_ready": decision["live_ready"],
            "real_leverage_authorized": decision["real_leverage_authorized"],
            "integration_permitted": decision["integration_permitted"],
        },
        "champions": {
            "V75_ATLAS_NX": {
                "role": "primary_paper_shadow_champion",
                "historical_metrics": scorecard["v75_full"],
                "real_capital_authorized": False,
            },
            "V136_EXECUTION_PLATEAU": {
                "role": "execution_shadow_only",
                "historical_metrics": scorecard["v136_full"],
                "delta_vs_v75": scorecard["v136_minus_v75"],
                "historical_promotion_passed": scorecard[
                    "historical_v136_promotion_passed"
                ],
                "real_capital_authorized": False,
            },
        },
        "rejected_oos_anti_controls": {
            "V285_LOW_SKEW_HOURLY_CONTROLLER": {
                "validation_2024_return": 0.0105,
                "holdout_2025_return": -0.0336,
                "final_2026h1_return": 0.0412,
                "status": "rejected_after_oos",
                "allocation": 0.0,
            },
            "V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE": {
                "development_cagr": 0.2375,
                "development_sharpe": 2.079,
                "validation_2024_return": -0.1036,
                "holdout_2025_return": 0.0036,
                "final_2026h1_return": -0.0132,
                "status": "rejected_after_oos",
                "allocation": 0.0,
            },
        },
        "final_batch_rejected": {
            "V381_RESIDUAL_ENTROPY": {
                "eligible_policies": 0,
                "promotable_policies": 108,
                "status": "rejected_before_validation",
            },
            "V389_RESIDUAL_RESILIENCE": {
                "eligible_policies": 0,
                "promotable_policies": 108,
                "status": "rejected_before_validation",
            },
            "V397_DISPERSION_SENSITIVITY": {
                "eligible_policies": 0,
                "promotable_policies": 108,
                "status": "rejected_before_validation",
            },
        },
        "next_evidence": {
            "type": "paper_forward_execution_comparison",
            **decision["paper_forward_gates"],
        },
    }

    path = RESULTS / "FINAL_CANDIDATE_REGISTRY.json"
    path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(registry, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
