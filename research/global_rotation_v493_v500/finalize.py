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
    parser.add_argument("--raw", type=Path, required=True)
    args = parser.parse_args()

    results = args.root / "results"
    results.mkdir(parents=True, exist_ok=True)
    design_path = args.root / "V493_V500_DESIGN.json"
    design = json.loads(design_path.read_text())
    summary_path = args.raw / "summary.json"
    metrics_path = args.raw / "metrics.csv"
    if not summary_path.exists() or not metrics_path.exists():
        raise SystemExit("V103 replay outputs are missing")
    legacy = json.loads(summary_path.read_text())
    metrics = pd.read_csv(metrics_path)

    def row(scenario: str, period: str) -> dict[str, Any]:
        selected = metrics[(metrics.scenario == scenario) & (metrics.period == period)]
        if len(selected) != 1:
            raise RuntimeError(f"expected one row for {scenario}/{period}, got {len(selected)}")
        return clean(selected.iloc[0].to_dict())

    bridge = row("stress", "bridge")
    holdout = row("stress", "holdout")
    final = row("stress", "final_2026h1")
    full = row("stress", "full")
    severe = row("severe", "full")
    extreme = row("extreme", "full")
    legacy_checks = {str(key): bool(value) for key, value in legacy["checks"].items()}
    gates = {
        "legacy_checks_all": all(legacy_checks.values()),
        "holdout_2024_2025_return_positive": float(holdout["total_return"]) > 0.0,
        "final_2026h1_return_positive": float(final["total_return"]) > 0.0,
        "full_stress_cagr_min": float(full["annualized_return"]) >= 0.07,
        "full_stress_sharpe_min": float(full["sharpe"]) >= 0.70,
        "full_stress_max_drawdown_min": float(full["max_drawdown"]) >= -0.23,
        "extreme_full_cagr_positive": float(extreme["annualized_return"]) > 0.0,
    }
    passed = bool(all(gates.values()))

    files: dict[str, dict[str, Any]] = {}
    for source in sorted(args.raw.iterdir()):
        if not source.is_file():
            continue
        destination = results / f"legacy_{source.name}"
        shutil.copy2(source, destination)
        files[destination.name] = {
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }

    decision = {
        "program": design["program"],
        "status": "exploratory_standalone_candidate" if passed else "rejected_after_frozen_oos",
        "selection_end": "2020-12-31",
        "selection_uses_post_2020": False,
        "selected": legacy.get("selected", []),
        "legacy_status": legacy.get("status"),
        "legacy_checks": legacy_checks,
        "frozen_post_selection_gates": gates,
        "standalone_candidate": passed,
        "metrics": {
            "bridge_2021_2023": bridge,
            "holdout_2024_2025": holdout,
            "final_2026h1": final,
            "full_stress": full,
            "full_severe": severe,
            "full_extreme": extreme,
        },
        "data_manifest": legacy.get("data_manifest"),
        "atlas_correlation_is_flat_interface_artifact": True,
        "design_sha256": sha256(design_path),
        "files": files,
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
        "# V493–V500 — fixed-universe global rotation replay",
        "",
        f"Status: `{decision['status']}`.",
        "",
        "| Период / audit | CAGR | Total return | Max DD | Sharpe |",
        "|---|---:|---:|---:|---:|",
        f"| Bridge 2021–2023 | {pct(bridge['annualized_return'])} | {pct(bridge['total_return'])} | {pct(bridge['max_drawdown'])} | {float(bridge['sharpe']):.3f} |",
        f"| Holdout 2024–2025 | {pct(holdout['annualized_return'])} | {pct(holdout['total_return'])} | {pct(holdout['max_drawdown'])} | {float(holdout['sharpe']):.3f} |",
        f"| Final 2026 H1 | {pct(final['annualized_return'])} | {pct(final['total_return'])} | {pct(final['max_drawdown'])} | {float(final['sharpe']):.3f} |",
        f"| Full stress | {pct(full['annualized_return'])} | {pct(full['total_return'])} | {pct(full['max_drawdown'])} | {float(full['sharpe']):.3f} |",
        f"| Full severe | {pct(severe['annualized_return'])} | {pct(severe['total_return'])} | {pct(severe['max_drawdown'])} | {float(severe['sharpe']):.3f} |",
        f"| Full extreme | {pct(extreme['annualized_return'])} | {pct(extreme['total_return'])} | {pct(extreme['max_drawdown'])} | {float(extreme['sharpe']):.3f} |",
        "",
        "## Frozen gates",
        "",
    ]
    report.extend(f"- [{'x' if value else ' '}] `{name}`" for name, value in gates.items())
    report += [
        "",
        "Adjusted ETF closes remain proxy research data. No result authorizes integration, capital or live execution.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(clean(decision), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
