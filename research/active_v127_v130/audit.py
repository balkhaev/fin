from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PERIODS = {
    "selection": ("2021-01-01", "2024-01-01"),
    "holdout": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "prefinal": ("2021-01-01", "2026-01-01"),
    "full": ("2021-01-01", "2026-07-01"),
}


def utc(value):
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def read_account(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    if "equity" not in frame:
        raise ValueError(f"missing equity: {path}")
    return frame


def profile(frame: pd.DataFrame) -> dict:
    index = frame.index
    counts = pd.Series(1, index=index).groupby(index.year).sum()
    complete = counts.iloc[1:-1] if len(counts) > 2 else counts
    observed_per_year = float(complete.median()) if len(complete) else float(counts.median())
    elapsed_years = max((index[-1] - index[0]).total_seconds() / (365.25 * 86400.0), 1e-12)
    return {
        "rows": int(len(frame)),
        "start": str(index.min()),
        "end": str(index.max()),
        "weekend_rows": int((index.dayofweek >= 5).sum()),
        "median_gap_days": float(index.to_series().diff().dt.total_seconds().median() / 86400.0),
        "observed_per_year_median": observed_per_year,
        "observed_per_year_elapsed": float((len(frame) - 1) / elapsed_years),
    }


def metrics(frame: pd.DataFrame, observations_per_year: float | None = None) -> dict[str, float]:
    equity = frame.equity.astype(float)
    if len(equity) < 2:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "annualized_volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "annual_turnover": 0.0,
            "average_gross": 0.0,
            "max_gross": 0.0,
        }
    elapsed_years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400.0), 1e-12)
    growth = float(equity.iloc[-1] / equity.iloc[0])
    annual = growth ** (1.0 / elapsed_years) - 1.0 if growth > 0.0 else -1.0
    returns = equity.pct_change().fillna(0.0)
    if observations_per_year is None:
        observations_per_year = profile(frame)["observed_per_year_median"]
    volatility = float(returns.std(ddof=0) * math.sqrt(observations_per_year))
    drawdown = float((equity / equity.cummax() - 1.0).min())
    turnover_series = frame.get("turnover", pd.Series(0.0, index=frame.index))
    gross = frame.get("gross", pd.Series(0.0, index=frame.index))
    return {
        "total_return": growth - 1.0,
        "annualized_return": float(annual),
        "annualized_volatility": volatility,
        "sharpe": float(annual / volatility) if volatility > 1e-12 else 0.0,
        "max_drawdown": drawdown,
        "annual_turnover": float(turnover_series.sum() / elapsed_years),
        "average_gross": float(gross.mean()),
        "max_gross": float(gross.max()),
    }


def cut(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    start, end = PERIODS[period]
    return frame.loc[(frame.index >= utc(start)) & (frame.index < utc(end))]


def align_atlas(atlas: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    index = candidate.index.intersection(atlas.index)
    if len(index) == len(candidate.index):
        return atlas.reindex(candidate.index)
    return atlas.reindex(candidate.index, method="ffill")


def compare_status(
    name: str,
    atlas: pd.DataFrame,
    candidate: pd.DataFrame,
    delay5: pd.DataFrame,
    severe: pd.DataFrame,
    original: dict,
    dd_improvement: float,
    sharpe_improvement: float,
    gross_cap: float,
    require_selection_eligible: bool,
) -> dict:
    aligned_atlas = align_atlas(atlas, candidate)
    ppy = profile(candidate)["observed_per_year_median"]
    atlas_prefinal = metrics(cut(aligned_atlas, "prefinal"), ppy)
    candidate_prefinal = metrics(cut(candidate, "prefinal"), ppy)
    candidate_holdout = metrics(cut(candidate, "holdout"), ppy)
    candidate_final = metrics(cut(candidate, "final_2026h1"), ppy)
    delay_full = metrics(cut(delay5, "full"), profile(delay5)["observed_per_year_median"])
    severe_full = metrics(cut(severe, "full"), profile(severe)["observed_per_year_median"])
    checks = {
        "holdout_positive": candidate_holdout["total_return"] > 0.0,
        "final_positive": candidate_final["total_return"] > 0.0,
        "cagr_not_destroyed": candidate_prefinal["annualized_return"]
        >= atlas_prefinal["annualized_return"] - 0.01,
        "dd_or_sharpe_improved": (
            candidate_prefinal["max_drawdown"]
            >= atlas_prefinal["max_drawdown"] + dd_improvement
        )
        or (
            candidate_prefinal["sharpe"]
            >= atlas_prefinal["sharpe"] + sharpe_improvement
        ),
        "max_gross": candidate_prefinal["max_gross"] <= gross_cap,
        "delay5_positive": delay_full["annualized_return"] > 0.0,
        "severe_transfer_positive": severe_full["annualized_return"] > 0.0,
    }
    if require_selection_eligible:
        checks["selection_has_eligible"] = bool((original.get("checks") or {}).get("selection_has_eligible", False))
    corrected_status = (
        "frozen_calendar_corrected_candidate" if all(checks.values()) else "rejected_after_calendar_audit"
    )
    return {
        "name": name,
        "original_status": original.get("status"),
        "corrected_status": corrected_status,
        "status_changed": original.get("status") != corrected_status,
        "checks": checks,
        "calendar_profile": profile(candidate),
        "aligned_atlas_profile": profile(aligned_atlas),
        "aligned_atlas_prefinal": atlas_prefinal,
        "candidate_prefinal": candidate_prefinal,
        "candidate_holdout": candidate_holdout,
        "candidate_final_2026h1": candidate_final,
        "delay5_full": delay_full,
        "severe_transfer_full": severe_full,
    }


def pct(value) -> str:
    return f"{100.0 * float(value):+.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    atlas = read_account(root / "research/active_v88_v94/inputs/atlas/v75_stress_equity.csv")
    crisis = read_account(root / "research/active_v95_v102/results/v95_stress_equity.csv")
    rotation = read_account(root / "research/active_v103_v110/results/v103_stress_equity.csv")
    trident = read_account(root / "research/active_v111_v118/results/dynamic_equity.csv")
    robust = read_account(root / "research/active_v119_v126/results/v125_robust_ensemble_equity.csv")
    trident_delay = read_account(root / "research/active_v111_v118/results/delay_5_equity.csv")
    trident_severe = read_account(root / "research/active_v111_v118/results/severe_transfer_cost_equity.csv")
    robust_delay = read_account(root / "research/active_v119_v126/results/delay_5_equity.csv")
    robust_severe = read_account(root / "research/active_v119_v126/results/severe_transfer_equity.csv")
    trident_original = json.loads((root / "research/active_v111_v118/results/summary.json").read_text())
    robust_original = json.loads((root / "research/active_v119_v126/results/summary.json").read_text())

    profiles = {
        "V75_ATLAS": profile(atlas),
        "V95_crisis": profile(crisis),
        "V103_rotation": profile(rotation),
        "V111_TRIDENT": profile(trident),
        "V119_ROBUST": profile(robust),
    }
    common = atlas.index.intersection(crisis.index).intersection(rotation.index)
    profiles["common_sleeve_calendar"] = {
        "rows": int(len(common)),
        "start": str(common.min()),
        "end": str(common.max()),
        "weekend_rows": int((common.dayofweek >= 5).sum()),
    }

    v111 = compare_status(
        "V111_TRIDENT",
        atlas,
        trident,
        trident_delay,
        trident_severe,
        trident_original,
        dd_improvement=0.02,
        sharpe_improvement=0.04,
        gross_cap=1.25,
        require_selection_eligible=False,
    )
    v119 = compare_status(
        "V119_ROBUST_TRIDENT",
        atlas,
        robust,
        robust_delay,
        robust_severe,
        robust_original,
        dd_improvement=0.015,
        sharpe_improvement=0.03,
        gross_cap=1.20,
        require_selection_eligible=True,
    )

    raw_atlas_252 = metrics(atlas, 252.0)
    raw_atlas_elapsed = metrics(atlas, profile(atlas)["observed_per_year_median"])
    summary = {
        "research": "ACTIVE_V127_V130_CALENDAR_AUDIT",
        "parameters_changed": False,
        "candidate_selection_performed": False,
        "calendar_profiles": profiles,
        "raw_atlas_metric_comparison": {
            "forced_252": raw_atlas_252,
            "observed_frequency": raw_atlas_elapsed,
        },
        "V111": v111,
        "V119": v119,
        "any_status_changed": bool(v111["status_changed"] or v119["status_changed"]),
        "live_ready": False,
        "real_leverage_authorized": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")

    profiles_frame = pd.DataFrame(profiles).T.reset_index(names="series")
    profiles_frame.to_csv(output / "calendar_profiles.csv", index=False)

    report = [
        "# Active V127–V130 calendar and annualization audit",
        "",
        "No parameter was changed and no candidate was selected.",
        "",
        "## Calendar profiles",
        "",
        "| Series | Rows | Start | End | Weekend rows | Observations/year |",
        "|---|---:|---|---|---:|---:|",
    ]
    for name, payload in profiles.items():
        report.append(
            f"| {name} | {payload.get('rows', 0)} | {payload.get('start', '—')} | "
            f"{payload.get('end', '—')} | {payload.get('weekend_rows', 0)} | "
            f"{float(payload.get('observed_per_year_median', 0.0)):.1f} |"
        )
    report += [
        "",
        "## Reapplied frozen decisions",
        "",
        "| Candidate | Original status | Corrected status | Status changed |",
        "|---|---|---|---:|",
        f"| V111 | `{v111['original_status']}` | `{v111['corrected_status']}` | {v111['status_changed']} |",
        f"| V119 | `{v119['original_status']}` | `{v119['corrected_status']}` | {v119['status_changed']} |",
        "",
        "### Corrected metrics",
        "",
        "| Candidate / period | CAGR | Total return | Max DD | Sharpe |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, payload in (
        ("V111 aligned ATLAS prefinal", v111["aligned_atlas_prefinal"]),
        ("V111 candidate prefinal", v111["candidate_prefinal"]),
        ("V111 holdout", v111["candidate_holdout"]),
        ("V111 final 2026 H1", v111["candidate_final_2026h1"]),
        ("V119 aligned ATLAS prefinal", v119["aligned_atlas_prefinal"]),
        ("V119 candidate prefinal", v119["candidate_prefinal"]),
        ("V119 holdout", v119["candidate_holdout"]),
        ("V119 final 2026 H1", v119["candidate_final_2026h1"]),
    ):
        report.append(
            f"| {label} | {pct(payload['annualized_return'])} | {pct(payload['total_return'])} | "
            f"{pct(payload['max_drawdown'])} | {payload['sharpe']:.3f} |"
        )
    report += [
        "",
        "## Safety",
        "",
        "- `live_ready = false`;",
        "- `real_leverage_authorized = false`;",
        "- old summaries remain unchanged and this audit is additive.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
