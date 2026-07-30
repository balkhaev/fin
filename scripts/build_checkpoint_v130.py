#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "checkpoints" / "v130"
OUT.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing checkpoint input: {path}")
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def pct(value) -> str:
    return f"{100.0 * float(value):+.2f}%"


def metric_row(label: str, payload: dict) -> str:
    return (
        f"| {label} | {pct(payload.get('annualized_return', 0.0))} | "
        f"{pct(payload.get('total_return', 0.0))} | "
        f"{pct(payload.get('max_drawdown', 0.0))} | "
        f"{float(payload.get('sharpe', 0.0)):.3f} |"
    )


def main() -> None:
    previous = read_json(ROOT / "docs" / "checkpoints" / "v126" / "CHECKPOINT_V126.json")
    audit = read_json(ROOT / "research" / "active_v127_v130" / "results" / "summary.json")

    checkpoint = {
        "checkpoint": "V130",
        "date": "2026-07-25",
        "repository": "balkhaev/fin",
        "source_commit": git_head(),
        "previous_checkpoint": "V126",
        "live_ready": False,
        "real_leverage_authorized": False,
        "parameters_changed_by_audit": audit.get("parameters_changed"),
        "candidate_selection_performed_by_audit": audit.get("candidate_selection_performed"),
        "any_status_changed": audit.get("any_status_changed"),
        "calendar_profiles": audit.get("calendar_profiles"),
        "raw_atlas_metric_comparison": audit.get("raw_atlas_metric_comparison"),
        "V111_calendar_decision": audit.get("V111"),
        "V119_calendar_decision": audit.get("V119"),
        "previous_evidence": {
            "primary_control": previous.get("primary_control"),
            "standalone_sleeves": (previous.get("previous") or {}).get("standalone_sleeves"),
            "trident_before_calendar_audit": (previous.get("previous") or {}).get("trident"),
            "robust_trident_before_calendar_audit": previous.get("robust_trident"),
        },
        "evidence_limits": {
            "program_level_holdout_pristine": False,
            "broker_level_execution_complete": False,
            "futures_roll_and_swap_model_complete": False,
            "live_authorized": False,
        },
    }
    checkpoint_path = OUT / "CHECKPOINT_V130.json"
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2, default=float) + "\n")

    source_dir = ROOT / "research" / "active_v127_v130" / "results"
    for source_name, destination_name in (
        ("calendar_profiles.csv", "CALENDAR_PROFILES.csv"),
        ("REPORT_RU.md", "CALENDAR_AUDIT_REPORT_RU.md"),
    ):
        source = source_dir / source_name
        if not source.exists():
            raise SystemExit(f"missing audit evidence: {source}")
        shutil.copy2(source, OUT / destination_name)

    report = [
        "# Research checkpoint V130",
        "",
        "Checkpoint V130 adds the calendar/frequency audit V127–V130 to checkpoint V126.",
        "",
        "The audit changed no strategy parameter and performed no candidate selection. It recomputed CAGR from elapsed calendar time, used each curve's observed annual frequency for volatility/Sharpe, aligned ATLAS to the exact candidate dates, and reapplied the original V111/V119 promotion gates unchanged.",
        "",
        "## Safety status",
        "",
        "- `live_ready = false`;",
        "- `real_leverage_authorized = false`;",
        f"- any calendar-driven status change: `{str(bool(audit.get('any_status_changed'))).lower()}`.",
        "",
        "## Calendar profiles",
        "",
        "| Series | Rows | Start | End | Weekend rows | Observations/year |",
        "|---|---:|---|---|---:|---:|",
    ]
    for name, payload in (audit.get("calendar_profiles") or {}).items():
        report.append(
            f"| {name} | {int(payload.get('rows', 0))} | {payload.get('start', '—')} | "
            f"{payload.get('end', '—')} | {int(payload.get('weekend_rows', 0))} | "
            f"{float(payload.get('observed_per_year_median', 0.0)):.1f} |"
        )

    report += [
        "",
        "## Reapplied frozen decisions",
        "",
        "| Candidate | Original status | Calendar-corrected status | Changed |",
        "|---|---|---|---:|",
    ]
    for key in ("V111", "V119"):
        decision = audit.get(key) or {}
        report.append(
            f"| {key} | `{decision.get('original_status')}` | `{decision.get('corrected_status')}` | "
            f"{bool(decision.get('status_changed'))} |"
        )

    report += [
        "",
        "## Calendar-corrected metrics",
        "",
        "| Candidate / period | CAGR | Total return | Max DD | Sharpe |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in ("V111", "V119"):
        decision = audit.get(key) or {}
        report.extend(
            [
                metric_row(f"{key} aligned ATLAS prefinal", decision.get("aligned_atlas_prefinal") or {}),
                metric_row(f"{key} candidate prefinal", decision.get("candidate_prefinal") or {}),
                metric_row(f"{key} holdout 2024–2025", decision.get("candidate_holdout") or {}),
                metric_row(f"{key} final 2026 H1", decision.get("candidate_final_2026h1") or {}),
            ]
        )

    report += ["", "## Reapplied gates", ""]
    for key in ("V111", "V119"):
        report.append(f"### {key}")
        report.append("")
        for name, value in ((audit.get(key) or {}).get("checks") or {}).items():
            report.append(f"- [{'x' if value else ' '}] `{name}`")
        report.append("")

    report += [
        "## Evidence boundaries",
        "",
        "- The audit is additive; old summaries and original decisions remain committed and unchanged.",
        "- Holdout/final periods were excluded from the declared selection grids but are not pristine at the overall research-program level.",
        "- ETF/FX sleeves remain historical proxy simulations, not complete execution-grade futures/spot implementations.",
        "- No live trading or real leverage is authorized.",
        "",
        f"Machine-readable checkpoint SHA-256: `{sha256(checkpoint_path)}`.",
    ]
    (OUT / "RESEARCH_CHECKPOINT_V130_RU.md").write_text("\n".join(report) + "\n")

    handoff = ROOT / "docs" / "HANDOFF.md"
    content = handoff.read_text() if handoff.exists() else "# Research handoff\n"
    start, end = "<!-- CURRENT-CHECKPOINT-START -->", "<!-- CURRENT-CHECKPOINT-END -->"
    block = (
        f"{start}\n\n## Current checkpoint: V130\n\n"
        "Read `docs/checkpoints/v130/RESEARCH_CHECKPOINT_V130_RU.md` and "
        "`docs/checkpoints/v130/CHECKPOINT_V130.json` before citing or changing V111/V119. "
        "The calendar-corrected statuses in V130 supersede unqualified use of their original summaries, "
        "while the original evidence remains immutable in the research directories.\n\n"
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
        "checkpoint": "V130",
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
