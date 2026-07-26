#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs" / "implementation" / "IMPLEMENTATION_PLAN.json"

REQUIRED_DOCS = (
    ROOT / "IMPLEMENTATION.md",
    ROOT / "docs" / "implementation" / "MASTER_IMPLEMENTATION_PLAN_RU.md",
    ROOT / "docs" / "implementation" / "AGENT_HANDOFF_RU.md",
    ROOT / "docs" / "implementation" / "RUNTIME_CONTRACTS_RU.md",
    ROOT / "docs" / "implementation" / "ACCEPTANCE_CRITERIA_RU.md",
)


def main() -> int:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    assert plan["plan_id"] == "implementation-runtime-v1"
    assert plan["implementation_scope"] == {
        "primary": "v75_atlas_nx",
        "control": "v28_growth_control",
        "shadow": "v136_execution_shadow",
        "separate_service": "services/funding_router",
    }

    safety = plan["safety"]
    assert safety["live_execution_available"] is False
    assert safety["live_ready"] is False
    assert safety["real_leverage_authorized"] is False
    assert safety["broker_submit_adapter_allowed"] is False

    v136 = plan["frozen_parameters"]["v136"]
    assert v136["l1_band"] == 0.08
    assert v136["max_age_days"] == 28
    assert v136["step_fraction"] == 1.0
    assert v136["risk_reduction_buffer"] == 0.02
    assert v136["immediate_zero_exit"] is True
    assert v136["split_perpetual_sign_flip"] is True

    milestones = plan["milestones"]
    assert [item["id"] for item in milestones] == [f"M{i}" for i in range(11)]
    assert all(item["status"] == "todo" for item in milestones)

    for path in REQUIRED_DOCS:
        assert path.is_file() and path.stat().st_size > 0, path

    canonical = set(plan["canonical_documents"])
    assert "IMPLEMENTATION.md" in canonical
    assert "docs/implementation/MASTER_IMPLEMENTATION_PLAN_RU.md" in canonical

    text = "\n".join(path.read_text(encoding="utf-8") for path in REQUIRED_DOCS)
    for required in (
        "v75_atlas_nx",
        "v28_growth_control",
        "v136_execution_shadow",
        "live_execution_available = false",
        "real_leverage_authorized = false",
    ):
        assert required in text, required

    print(json.dumps({
        "status": "success",
        "plan_id": plan["plan_id"],
        "documents": len(REQUIRED_DOCS),
        "milestones": len(milestones),
        "live_execution_available": safety["live_execution_available"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
