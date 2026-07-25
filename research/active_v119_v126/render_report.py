from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def pct(value) -> str:
    return f"{100.0 * float(value):+.2f}%"


def main() -> None:
    summary = json.loads((RESULTS / "summary.json").read_text())
    yearly = pd.read_csv(RESULTS / "v126_yearly.csv")
    bootstrap = pd.read_csv(RESULTS / "paired_bootstrap.csv")

    def row(label: str, payload: dict) -> str:
        return (
            f"| {label} | {pct(payload.get('annualized_return', 0.0))} | "
            f"{pct(payload.get('total_return', 0.0))} | {pct(payload.get('max_drawdown', 0.0))} | "
            f"{float(payload.get('sharpe', 0.0)):.3f} | "
            f"{float(payload.get('annual_turnover', 0.0)):.2f}× | "
            f"{float(payload.get('max_gross', 0.0)):.3f}× |"
        )

    lines = [
        "# Active V119–V126 ROBUST TRIDENT",
        "",
        f"**Frozen status:** `{summary['status']}`",
        "",
        "Underlying V75, V95 and V103 signals were unchanged. Only the portfolio-allocation layer was tested.",
        "",
        f"- selected processes: `{', '.join(summary['selected'])}`;",
        f"- selected families: `{', '.join(summary['selected_families'])}`;",
        f"- selection proof SHA-256: `{summary['selection_proof_sha256']}`;",
        f"- `live_ready = {str(summary['live_ready']).lower()}`;",
        f"- `real_leverage_authorized = {str(summary['real_leverage_authorized']).lower()}`.",
        "",
        "## Frozen metrics",
        "",
        "| Candidate / period | CAGR | Total return | Max DD | Sharpe | Turnover | Max gross |",
        "|---|---:|---:|---:|---:|---:|---:|",
        row("ATLAS selection", summary["atlas_selection"]),
        row("ROBUST selection", summary["robust_selection"]),
        row("ATLAS prefinal", summary["atlas_prefinal"]),
        row("ROBUST prefinal", summary["robust_prefinal"]),
        row("ROBUST holdout 2024–2025", summary["robust_holdout"]),
        row("ROBUST final 2026 H1", summary["robust_final_2026h1"]),
        "",
        "## Promotion checks",
        "",
    ]
    for name, passed in summary["checks"].items():
        lines.append(f"- [{'x' if passed else ' '}] `{name}`")

    lines += [
        "",
        "## Latency, transfer-cost and no-leverage variants",
        "",
        "| Variant | CAGR | Max DD | Sharpe | Max gross |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, payload in summary["variants"].items():
        lines.append(
            f"| {name} | {pct(payload['cagr'])} | {pct(payload['dd'])} | "
            f"{float(payload['sharpe']):.3f} | {float(payload['max_gross']):.3f}× |"
        )

    diagnostics = summary["diagnostics"]
    lines += [
        "",
        "## Rolling and concentration diagnostics",
        "",
        f"- best positive-year log-growth share: {pct(diagnostics['best_positive_year_log_share'])};",
        f"- worst rolling 252-day result: {pct(diagnostics['worst_rolling_252'])};",
        f"- worst rolling 504-day result: {pct(diagnostics['worst_rolling_504'])};",
        f"- positive rolling-252 fraction: {pct(diagnostics['positive_rolling_252_fraction'])};",
        f"- positive rolling-504 fraction: {pct(diagnostics['positive_rolling_504_fraction'])}.",
        "",
        "## Paired block bootstrap against ATLAS",
        "",
        "| Block | Horizon | P(beat ATLAS) | P(lower DD) | P(positive) | Median | P05 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in bootstrap.to_dict(orient="records"):
        lines.append(
            f"| {int(item['block'])} | {int(item['horizon'])} | "
            f"{pct(item['p_candidate_beats_atlas'])} | {pct(item['p_candidate_lower_drawdown'])} | "
            f"{pct(item['p_candidate_positive'])} | {pct(item['median_candidate_return'])} | "
            f"{pct(item['p05_candidate_return'])} |"
        )

    lines += [
        "",
        "## Annual returns",
        "",
        "| Year | Return | Max DD |",
        "|---:|---:|---:|",
    ]
    for item in yearly.to_dict(orient="records"):
        lines.append(
            f"| {int(item['year'])} | {pct(item['return'])} | {pct(item['max_drawdown'])} |"
        )

    lines += [
        "",
        "## Evidence limits",
        "",
        "- The 2024–2026 periods were excluded from candidate selection, but are not pristine at the overall research-program level.",
        "- The portfolio layer uses historical proxy account curves rather than broker-level fills, margin calls and cross-collateral rules.",
        "- Portfolio leverage remains modeled only and is not authorized for real trading.",
    ]
    (ROOT / "REPORT_RU.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
