#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if hasattr(value, "item"):
        return clean(value.item())
    if pd.isna(value):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--legacy-results", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()

    results = args.root / "results"
    results.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.data / "DATA_MANIFEST.json").read_text())
    design_path = args.root / "V485_V492_DESIGN.json"
    design = json.loads(design_path.read_text())

    if not manifest.get("data_gate_passed", False):
        decision = {
            "program": design["program"],
            "status": "data_gate_blocked",
            "data_gate_passed": False,
            "data_manifest_sha256": sha256(args.data / "DATA_MANIFEST.json"),
            "legacy_replay_run": False,
            "standalone_candidate": False,
            "integration_permitted": False,
            "capital_change_authorized": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        (results / "summary.json").write_text(json.dumps(decision, indent=2) + "\n")
        (results / "REPORT_RU.md").write_text(
            "# V485–V492 independent macro replay\n\n"
            "Status: `data_gate_blocked`. Экономический replay не запускался.\n",
            encoding="utf-8",
        )
        return 0

    legacy_summary_path = args.legacy_results / "summary.json"
    metrics_path = args.legacy_results / "v95_metrics.csv"
    if not legacy_summary_path.exists() or not metrics_path.exists():
        raise SystemExit("legacy V95 replay outputs are missing")
    legacy = json.loads(legacy_summary_path.read_text())
    metrics = pd.read_csv(metrics_path)

    def row(scenario: str, period: str) -> dict[str, Any]:
        selected = metrics[(metrics.scenario == scenario) & (metrics.period == period)]
        if len(selected) != 1:
            raise RuntimeError(f"expected one metric row for {scenario}/{period}, got {len(selected)}")
        return clean(selected.iloc[0].to_dict())

    stress_bridge = row("stress", "bridge")
    stress_holdout = row("stress", "holdout")
    stress_final = row("stress", "final_2026h1")
    stress_full = row("stress", "full")
    severe_full = row("severe", "full")
    extreme_full = row("extreme", "full")
    legacy_checks = {str(key): bool(value) for key, value in legacy["standalone_checks"].items()}
    gates = {
        "legacy_standalone_checks_all": all(legacy_checks.values()),
        "holdout_2024_2025_return_positive": float(stress_holdout["total_return"]) > 0.0,
        "final_2026h1_return_positive": float(stress_final["total_return"]) > 0.0,
        "full_stress_cagr_min": float(stress_full["annualized_return"]) >= 0.05,
        "full_stress_sharpe_min": float(stress_full["sharpe"]) >= 0.55,
        "full_stress_max_drawdown_min": float(stress_full["max_drawdown"]) >= -0.20,
        "extreme_full_cagr_positive": float(extreme_full["annualized_return"]) > 0.0,
    }
    passed = bool(all(gates.values()))

    copied: dict[str, dict[str, Any]] = {}
    for source in sorted(args.legacy_results.iterdir()):
        if not source.is_file():
            continue
        destination = results / f"legacy_{source.name}"
        shutil.copy2(source, destination)
        copied[destination.name] = {
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }
    for source in sorted(args.data.iterdir()):
        if not source.is_file():
            continue
        destination = results / source.name
        shutil.copy2(source, destination)
        copied[destination.name] = {
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }

    decision = {
        "program": design["program"],
        "status": "exploratory_standalone_candidate" if passed else "rejected_after_frozen_oos",
        "data_gate_passed": True,
        "selection_end": "2020-12-31",
        "selection_uses_post_2020": False,
        "selected_processes": legacy.get("selected_processes", []),
        "legacy_standalone_status": legacy.get("standalone_status"),
        "legacy_standalone_checks": legacy_checks,
        "frozen_post_selection_gates": gates,
        "standalone_candidate": passed,
        "metrics": {
            "bridge_2021_2023": stress_bridge,
            "holdout_2024_2025": stress_holdout,
            "final_2026h1": stress_final,
            "full_stress": stress_full,
            "full_severe": severe_full,
            "full_extreme": extreme_full,
        },
        "atlas_daily_correlation_is_flat_interface_artifact": True,
        "data_manifest_sha256": sha256(args.data / "DATA_MANIFEST.json"),
        "design_sha256": sha256(design_path),
        "files": copied,
        "evidence_limits": design["evidence_limits"],
        "integration_permitted": False,
        "capital_change_authorized": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    (results / "summary.json").write_text(
        json.dumps(clean(decision), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    def pct(value: Any) -> str:
        return f"{100 * float(value):+.2f}%"

    report = [
        "# V485–V492 — independent macro/crisis replay",
        "",
        f"Status: `{decision['status']}`.",
        "",
        "| Период / audit | CAGR | Total return | Max DD | Sharpe |",
        "|---|---:|---:|---:|---:|",
        f"| Bridge 2021–2023 | {pct(stress_bridge['annualized_return'])} | {pct(stress_bridge['total_return'])} | {pct(stress_bridge['max_drawdown'])} | {float(stress_bridge['sharpe']):.3f} |",
        f"| Holdout 2024–2025 | {pct(stress_holdout['annualized_return'])} | {pct(stress_holdout['total_return'])} | {pct(stress_holdout['max_drawdown'])} | {float(stress_holdout['sharpe']):.3f} |",
        f"| Final 2026 H1 | {pct(stress_final['annualized_return'])} | {pct(stress_final['total_return'])} | {pct(stress_final['max_drawdown'])} | {float(stress_final['sharpe']):.3f} |",
        f"| Full stress | {pct(stress_full['annualized_return'])} | {pct(stress_full['total_return'])} | {pct(stress_full['max_drawdown'])} | {float(stress_full['sharpe']):.3f} |",
        f"| Full severe | {pct(severe_full['annualized_return'])} | {pct(severe_full['total_return'])} | {pct(severe_full['max_drawdown'])} | {float(severe_full['sharpe']):.3f} |",
        f"| Full extreme | {pct(extreme_full['annualized_return'])} | {pct(extreme_full['total_return'])} | {pct(extreme_full['max_drawdown'])} | {float(extreme_full['sharpe']):.3f} |",
        "",
        "## Frozen gates",
        "",
    ]
    report.extend(
        f"- [{'x' if value else ' '}] `{name}`" for name, value in gates.items()
    )
    report += [
        "",
        "ETF/FX histories remain proxy research data. No result authorizes integration, capital or live execution.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(clean(decision), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
