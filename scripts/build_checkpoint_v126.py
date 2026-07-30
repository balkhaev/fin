#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "checkpoints" / "v126"
OUT.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing checkpoint input: {path}")
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(value) -> str:
    return f"{100.0 * float(value):+.2f}%"


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main() -> None:
    previous = read_json(ROOT / "docs" / "checkpoints" / "v118" / "CHECKPOINT_V118.json")
    robust = read_json(ROOT / "research" / "active_v119_v126" / "results" / "summary.json")
    yearly = pd.read_csv(ROOT / "research" / "active_v119_v126" / "results" / "v126_yearly.csv")
    bootstrap = pd.read_csv(ROOT / "research" / "active_v119_v126" / "results" / "paired_bootstrap.csv")
    yearly.to_csv(OUT / "ROBUST_TRIDENT_ANNUAL_RETURNS.csv", index=False)
    bootstrap.to_csv(OUT / "ROBUST_TRIDENT_BOOTSTRAP.csv", index=False)

    checkpoint = {
        "checkpoint": "V126",
        "date": "2026-07-25",
        "repository": "balkhaev/fin",
        "source_commit": git_head(),
        "previous_checkpoint": "V118",
        "live_ready": False,
        "real_leverage_authorized": False,
        "primary_control": "V75_ATLAS_NX",
        "previous": {
            "standalone_sleeves": previous.get("standalone_sleeves"),
            "trident": previous.get("composite"),
        },
        "robust_trident": robust,
        "evidence_limits": {
            "program_level_holdout_pristine": False,
            "broker_level_execution_complete": False,
            "real_margin_model_complete": False,
            "live_authorized": False,
        },
    }
    checkpoint_path = OUT / "CHECKPOINT_V126.json"
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2, default=float) + "\n")

    def row(label: str, payload: dict) -> str:
        return (
            f"| {label} | {pct(payload.get('annualized_return', 0.0))} | "
            f"{pct(payload.get('total_return', 0.0))} | {pct(payload.get('max_drawdown', 0.0))} | "
            f"{float(payload.get('sharpe', 0.0)):.3f} | "
            f"{float(payload.get('max_gross', 0.0)):.3f}× |"
        )

    report = [
        "# Research checkpoint V126",
        "",
        "Checkpoint V126 adds the ROBUST TRIDENT allocation experiment to checkpoint V118.",
        "",
        "## Frozen decision",
        "",
        f"- ROBUST TRIDENT status: `{robust.get('status')}`;",
        f"- selected allocation processes: `{', '.join(robust.get('selected', []))}`;",
        f"- selected allocation families: `{', '.join(robust.get('selected_families', []))}`;",
        f"- selection proof SHA-256: `{robust.get('selection_proof_sha256')}`;",
        "- `live_ready = false`;",
        "- `real_leverage_authorized = false`.",
        "",
        "## Main metrics",
        "",
        "| Candidate / period | CAGR | Total return | Max DD | Sharpe | Max gross |",
        "|---|---:|---:|---:|---:|---:|",
        row("ATLAS selection", robust.get("atlas_selection", {})),
        row("ROBUST selection", robust.get("robust_selection", {})),
        row("ATLAS prefinal", robust.get("atlas_prefinal", {})),
        row("ROBUST prefinal", robust.get("robust_prefinal", {})),
        row("ROBUST holdout 2024–2025", robust.get("robust_holdout", {})),
        row("ROBUST final 2026 H1", robust.get("robust_final_2026h1", {})),
        "",
        "## Promotion checks",
        "",
    ]
    for name, passed in (robust.get("checks") or {}).items():
        report.append(f"- [{'x' if passed else ' '}] `{name}`")
    report += [
        "",
        "## Variant audits",
        "",
        "| Variant | CAGR | Max DD | Sharpe | Max gross |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, payload in (robust.get("variants") or {}).items():
        report.append(
            f"| {name} | {pct(payload['cagr'])} | {pct(payload['dd'])} | "
            f"{float(payload['sharpe']):.3f} | {float(payload['max_gross']):.3f}× |"
        )
    report += [
        "",
        "## Evidence boundaries",
        "",
        "- The 2024–2026 periods were excluded from the V119 selection grid, but are not pristine at the full research-program level.",
        "- Input sleeves are historical proxy account curves, not broker-level fills and cross-margin statements.",
        "- Portfolio leverage remains modeled only and is not authorized for real trading.",
        "- Negative candidates and failed gates remain committed in `research/active_v119_v126/results/`.",
        "",
        "See `ROBUST_TRIDENT_ANNUAL_RETURNS.csv` and `ROBUST_TRIDENT_BOOTSTRAP.csv` for the exact series.",
    ]
    (OUT / "RESEARCH_CHECKPOINT_V126_RU.md").write_text("\n".join(report) + "\n")

    handoff = ROOT / "docs" / "HANDOFF.md"
    content = handoff.read_text() if handoff.exists() else "# Research handoff\n"
    start, end = "<!-- CURRENT-CHECKPOINT-START -->", "<!-- CURRENT-CHECKPOINT-END -->"
    block = (
        f"{start}\n\n## Current checkpoint: V126\n\n"
        "Read `docs/checkpoints/v126/RESEARCH_CHECKPOINT_V126_RU.md` and "
        "`docs/checkpoints/v126/CHECKPOINT_V126.json` before changing any frozen allocation or sleeve parameter. "
        "Direct reusable sources/results are in `research/active_v95_v102`, `research/active_v103_v110`, "
        "`research/active_v111_v118`, and `research/active_v119_v126`.\n\n"
        f"{end}"
    )
    if start in content and end in content:
        content = content.split(start)[0].rstrip() + "\n\n" + block + "\n\n" + content.split(end, 1)[1].lstrip()
    else:
        content = content.rstrip() + "\n\n" + block + "\n"
    handoff.write_text(content)

    manifest = {
        "checkpoint": "V126",
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
