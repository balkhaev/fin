#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "checkpoints" / "v118"
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "V95": ROOT / "research" / "active_v95_v102" / "results" / "summary.json",
    "V103": ROOT / "research" / "active_v103_v110" / "results" / "summary.json",
    "V111": ROOT / "research" / "active_v111_v118" / "results" / "summary.json",
}


def read_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing checkpoint input: {path}")
    return json.loads(path.read_text())


def pct(value) -> str:
    return f"{100.0 * float(value):+.2f}%"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def collect_yearly() -> pd.DataFrame:
    files = {
        "V95_crisis_alpha": ROOT / "research" / "active_v95_v102" / "results" / "v95_yearly.csv",
        "V103_global_rotation": ROOT / "research" / "active_v103_v110" / "results" / "yearly.csv",
        "V111_TRIDENT": ROOT / "research" / "active_v111_v118" / "results" / "v117_yearly.csv",
    }
    output = None
    for label, path in files.items():
        frame = pd.read_csv(path)
        if "year" not in frame or "return" not in frame:
            raise SystemExit(f"invalid yearly file: {path}")
        values = frame[["year", "return"]].rename(columns={"return": label})
        output = values if output is None else output.merge(values, on="year", how="outer")
    return output.sort_values("year")


def main() -> None:
    payloads = {name: read_json(path) for name, path in SOURCES.items()}
    v95, v103, trident = payloads["V95"], payloads["V103"], payloads["V111"]
    yearly = collect_yearly()
    yearly.to_csv(OUT / "ANNUAL_RETURNS.csv", index=False)

    checkpoint = {
        "checkpoint": "V118",
        "date": "2026-07-25",
        "repository": "balkhaev/fin",
        "source_commit": git_head(),
        "live_ready": False,
        "real_leverage_authorized": False,
        "primary_control": "V75_ATLAS_NX",
        "standalone_sleeves": {
            "V95_global_crisis_alpha": {
                "status": v95.get("standalone_status"),
                "checks": v95.get("standalone_checks"),
                "stress_prefinal": v95.get("stress_prefinal"),
                "stress_final_2026h1": v95.get("stress_final_2026h1"),
                "selection_proof_sha256": v95.get("selection_proof_sha256"),
            },
            "V103_global_rotation": {
                "status": v103.get("status"),
                "checks": v103.get("checks"),
                "stress_prefinal": v103.get("stress_prefinal"),
                "final_2026h1": v103.get("final_2026h1"),
                "selection_proof_sha256": v103.get("selection_proof_sha256"),
            },
        },
        "composite": {
            "candidate": "V111_TRIDENT",
            "status": trident.get("status"),
            "checks": trident.get("checks"),
            "atlas_prefinal": trident.get("atlas_prefinal"),
            "trident_prefinal": trident.get("trident_prefinal"),
            "trident_holdout": trident.get("trident_holdout"),
            "trident_final_2026h1": trident.get("trident_final_2026h1"),
            "audit": trident.get("audit"),
            "latency_costs": trident.get("latency_costs"),
            "selection_proof_sha256": trident.get("selection_proof_sha256"),
        },
        "evidence_limits": {
            "program_level_holdout_pristine": False,
            "ETF_FX_execution_grade": False,
            "real_bid_ask_and_rolls_complete": False,
            "live_authorized": False,
        },
    }
    checkpoint_path = OUT / "CHECKPOINT_V118.json"
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2, default=float) + "\n")

    def row(label: str, payload: dict) -> str:
        if not payload:
            return f"| {label} | — | — | — | — |"
        return (
            f"| {label} | {pct(payload.get('annualized_return', 0.0))} | "
            f"{pct(payload.get('total_return', 0.0))} | {pct(payload.get('max_drawdown', 0.0))} | "
            f"{float(payload.get('sharpe', 0.0)):.3f} |"
        )

    report = [
        "# Research checkpoint V118",
        "",
        "Checkpoint фиксирует три независимо исследованных источника P&L и их guarded composite:",
        "",
        "1. V75 ATLAS-NX — crypto/on-chain control;",
        "2. V95 — global crisis-alpha на ETF/FX proxies;",
        "3. V103 — fixed-universe sector/country rotation;",
        "4. V111 TRIDENT — separate-account portfolio layer поверх трёх sleeves.",
        "",
        "## Решение",
        "",
        f"- V95 status: `{v95.get('standalone_status')}`;",
        f"- V103 status: `{v103.get('status')}`;",
        f"- TRIDENT status: `{trident.get('status')}`;",
        "- `live_ready = false`;",
        "- `real_leverage_authorized = false`.",
        "",
        "## Основные frozen metrics",
        "",
        "| Candidate / period | CAGR | Total return | Max DD | Sharpe |",
        "|---|---:|---:|---:|---:|",
        row("V95 prefinal", v95.get("stress_prefinal", {})),
        row("V95 final 2026 H1", v95.get("stress_final_2026h1", {})),
        row("V103 prefinal", v103.get("stress_prefinal", {})),
        row("V103 final 2026 H1", v103.get("final_2026h1", {})),
        row("ATLAS prefinal control", trident.get("atlas_prefinal", {})),
        row("TRIDENT prefinal", trident.get("trident_prefinal", {})),
        row("TRIDENT holdout 2024–2025", trident.get("trident_holdout", {})),
        row("TRIDENT final 2026 H1", trident.get("trident_final_2026h1", {})),
        "",
        "## TRIDENT promotion checks",
        "",
    ]
    for name, value in (trident.get("checks") or {}).items():
        report.append(f"- [{'x' if value else ' '}] `{name}`")
    report += [
        "",
        "## Evidence boundaries",
        "",
        "- Selection proofs are frozen before their declared holdout/final windows, but the overall program has already seen much of the history; program-level pristine holdout is absent.",
        "- ETF and ECB FX prices are proxy research data, not full execution-grade futures chains with rolls, intraday bid/ask, swaps and broker margin.",
        "- Portfolio leverage remains modeled only. No real leverage or live execution is authorized.",
        "- Negative checks and rejected variants remain in each research directory.",
        "",
        f"Machine-readable checkpoint: `CHECKPOINT_V118.json` (SHA-256 `{sha256(checkpoint_path)}`).",
        "",
        "Annual returns are stored in `ANNUAL_RETURNS.csv`.",
    ]
    (OUT / "RESEARCH_CHECKPOINT_V118_RU.md").write_text("\n".join(report) + "\n")

    handoff = ROOT / "docs" / "HANDOFF.md"
    content = handoff.read_text() if handoff.exists() else "# Research handoff\n"
    start, end = "<!-- V118-START -->", "<!-- V118-END -->"
    block = (
        f"{start}\n\n## Current checkpoint: V118\n\n"
        "Read `docs/checkpoints/v118/RESEARCH_CHECKPOINT_V118_RU.md` and "
        "`docs/checkpoints/v118/CHECKPOINT_V118.json` before changing frozen parameters. "
        "Continue from direct source/result files in `research/active_v95_v102`, "
        "`research/active_v103_v110`, and `research/active_v111_v118`.\n\n"
        f"{end}"
    )
    if start in content and end in content:
        before = content.split(start)[0].rstrip()
        after = content.split(end, 1)[1].lstrip()
        content = before + "\n\n" + block + ("\n\n" + after if after else "\n")
    else:
        content = content.rstrip() + "\n\n" + block + "\n"
    handoff.write_text(content)

    manifest = {
        "checkpoint": "V118",
        "source_commit": checkpoint["source_commit"],
        "files": {
            str(path.relative_to(ROOT)): {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in sorted(OUT.glob("*"))
            if path.is_file()
        },
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(checkpoint, indent=2, default=float))


if __name__ == "__main__":
    main()
