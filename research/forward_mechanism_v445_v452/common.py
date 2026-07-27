from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROGRAM = "V445_V452_FORWARD_MARKET_MECHANISM_VALIDATOR"
STRATEGIES = ("V75_ATLAS_NX", "V136_EXECUTION_PLATEAU", "V28_GROWTH_CONTROL")
CHAMPION = "V75_ATLAS_NX"
SHADOW = "V136_EXECUTION_PLATEAU"
REQUIRED_TELEMETRY_FIELDS = (
    "timestamp", "strategy_id", "source_bundle_sha256", "target_hash",
    "realized_position_hash", "gross_target", "gross_realized", "turnover",
    "modelled_slippage_bps", "paper_slippage_bps", "net_return", "equity",
    "drawdown", "reconciliation_ok", "source_hash_match", "data_stale",
    "execution_complete",
)
REQUIRED_STATE_FIELDS = (
    "state_id", "state_label", "novelty_flag", "novelty_ratio",
    "transition_surprise", "state_duration_days",
)


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_design(path: Path) -> dict[str, Any]:
    design = json.loads(path.read_text(encoding="utf-8"))
    if design.get("program") != PROGRAM:
        raise ValueError("unexpected design program")
    if not design.get("frozen_before_forward_observations"):
        raise ValueError("design is not frozen before forward observations")
    if design.get("safety", {}).get("historical_parameter_search_closed") is not True:
        raise ValueError("historical search closure missing")
    return design


def parse_bool(series: pd.Series, name: str) -> pd.Series:
    if series.dtype == bool:
        return series.astype(bool)
    parsed = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if parsed.isna().any():
        raise ValueError(f"invalid boolean field {name}")
    return parsed.astype(bool)


def read_states(path: Path, design: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, float]]:
    states = pd.read_csv(path, index_col=0, parse_dates=True)
    states.index = pd.to_datetime(states.index, utc=True).normalize()
    missing = sorted(set(REQUIRED_STATE_FIELDS) - set(states.columns))
    if missing:
        raise ValueError(f"state file missing {missing}")
    if states.index.duplicated().any():
        raise ValueError("duplicate market-state dates")
    states = states.sort_index()
    states["novelty_flag"] = parse_bool(states["novelty_flag"], "novelty_flag")
    for column in ("state_id", "novelty_ratio", "transition_surprise", "state_duration_days"):
        states[column] = pd.to_numeric(states[column], errors="coerce")
    if states["state_id"].isna().any() or states["state_duration_days"].isna().any():
        raise ValueError("missing required state values")
    states["state_changed"] = states["state_label"].ne(states["state_label"].shift(1))
    window = int(design["reference_thresholds"]["switching_window_days"])
    states["switching_rate_20d"] = (
        states["state_changed"].astype(float).rolling(window, min_periods=window).mean()
    )
    reference_start, reference_end = design["reference_thresholds"]["reference_period"]
    reference = states.loc[
        (states.index >= pd.Timestamp(reference_start, tz="UTC"))
        & (states.index < pd.Timestamp(reference_end, tz="UTC"))
    ]
    if len(reference) < 365:
        raise ValueError("insufficient development state reference")
    surprise_q = float(design["reference_thresholds"]["transition_surprise_quantile"])
    switch_q = float(design["reference_thresholds"]["switching_rate_quantile"])
    surprise_values = pd.to_numeric(reference["transition_surprise"], errors="coerce").dropna()
    switching_values = pd.to_numeric(reference["switching_rate_20d"], errors="coerce").dropna()
    if surprise_values.empty or switching_values.empty:
        raise ValueError("development state thresholds cannot be computed")
    thresholds = {
        "high_transition_surprise": float(surprise_values.quantile(surprise_q)),
        "high_switching_rate": float(switching_values.quantile(switch_q)),
    }
    return states, thresholds


def read_telemetry(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = sorted(set(REQUIRED_TELEMETRY_FIELDS) - set(frame.columns))
    if missing:
        raise ValueError(f"telemetry missing {missing}")
    frame = frame[list(REQUIRED_TELEMETRY_FIELDS)].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    frame["date"] = frame["timestamp"].dt.normalize()
    if frame.duplicated(["timestamp", "strategy_id"]).any():
        raise ValueError("duplicate telemetry primary key")
    if frame.duplicated(["date", "strategy_id"]).any():
        raise ValueError("mechanism validator requires one reconciled row per date and strategy")
    unknown = sorted(set(frame["strategy_id"]) - set(STRATEGIES))
    if unknown:
        raise ValueError(f"unknown strategies {unknown}")
    for column in ("reconciliation_ok", "source_hash_match", "data_stale", "execution_complete"):
        frame[column] = parse_bool(frame[column], column)
    numeric = (
        "gross_target", "gross_realized", "turnover", "modelled_slippage_bps",
        "paper_slippage_bps", "net_return", "equity", "drawdown",
    )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column].to_numpy(float)).all():
            raise ValueError(f"non-finite {column}")
    if (frame["equity"] <= 0).any():
        raise ValueError("non-positive equity")
    nonnegative = [
        "gross_target", "gross_realized", "turnover",
        "modelled_slippage_bps", "paper_slippage_bps",
    ]
    if (frame[nonnegative] < 0).any().any():
        raise ValueError("negative execution metric")
    frame = frame.sort_values(["strategy_id", "timestamp"]).reset_index(drop=True)
    frame["target_changed"] = frame.groupby("strategy_id")["target_hash"].transform(
        lambda values: values.astype(str).ne(values.astype(str).shift(1))
    )
    first_rows = frame.groupby("strategy_id").head(1).index
    frame.loc[first_rows, "target_changed"] = False
    return frame


def max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def strategy_metrics(group: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(group.get("net_return", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    modelled = float(pd.to_numeric(group.get("modelled_slippage_bps", 0.0), errors="coerce").sum())
    paper = float(pd.to_numeric(group.get("paper_slippage_bps", 0.0), errors="coerce").sum())
    sd = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    turnover = float(pd.to_numeric(group.get("turnover", 0.0), errors="coerce").sum())
    changes = int(pd.Series(group.get("target_changed", False)).astype(bool).sum())
    reconciliation = pd.Series(group.get("reconciliation_ok", True)).astype(bool)
    hash_match = pd.Series(group.get("source_hash_match", True)).astype(bool)
    stale = pd.Series(group.get("data_stale", False)).astype(bool)
    complete = pd.Series(group.get("execution_complete", True)).astype(bool)
    return {
        "rows": len(group),
        "total_return": float((1.0 + returns).prod() - 1.0),
        "mean_daily_return": float(returns.mean()) if len(returns) else 0.0,
        "annualized_volatility": sd * math.sqrt(365.0),
        "annualized_sharpe": float(returns.mean() / sd * math.sqrt(365.0)) if sd > 0 else 0.0,
        "max_drawdown": max_drawdown(returns),
        "turnover": turnover,
        "target_changes": changes,
        "modelled_slippage_bps_sum": modelled,
        "paper_slippage_bps_sum": paper,
        "slippage_to_model_ratio": paper / modelled if modelled > 0 else (0.0 if paper == 0 else None),
        "reconciliation_breaks": int((~reconciliation).sum()),
        "source_hash_mismatches": int((~hash_match).sum()),
        "stale_rows": int(stale.sum()),
        "incomplete_execution_rows": int((~complete).sum()),
    }
