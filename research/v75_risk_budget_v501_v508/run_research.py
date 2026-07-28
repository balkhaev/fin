#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROGRAM = "V501_V508_V75_ACCOUNT_LEVEL_RISK_BUDGET"
INITIAL_EQUITY = 10_000.0
START = pd.Timestamp("2021-01-01", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
PERIODS = {
    "development": (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation_2024": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "holdout_2025": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
    "final_2026h1": (pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC")),
    "full": (START, END),
}
EXPECTED_V75_SHA256 = "f9d543ba8ec15c90efa757e64ed772b1a5934e458463124b7df48ddcac96ef01"
EXPECTED_V75 = {
    "rows": 2007,
    "start": "2021-01-01 00:00:00+00:00",
    "end": "2026-06-30 00:00:00+00:00",
    "total_return": 3.3554131438812718,
    "cagr": 0.3068209897853704,
    "max_drawdown": -0.21591803526892284,
    "sharpe": 1.3294516915576948,
    "annual_returns": {
        2021: 1.0440158280864336,
        2022: 0.0108115546286298,
        2023: 0.148519140718377,
        2024: 0.4195522030251695,
        2025: 0.2381649543372901,
        2026: 0.0442555508389017,
    },
}
V75_ANNUAL_TURNOVER = 10.643693754982161
STABILIZER_ANNUAL_TURNOVER = 10.696772154709933


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    transfer_cost_bps: float
    financing_rate: float
    extra_underlying_cost_bps: float
    signal_delay_days: int = 0

    @property
    def transfer_cost_rate(self) -> float:
        return self.transfer_cost_bps / 10_000.0

    @property
    def extra_underlying_cost_rate(self) -> float:
        return self.extra_underlying_cost_bps / 10_000.0


AUDITS = (
    Audit("base", 10.0, 0.08, 0.0, 0),
    Audit("severe", 25.0, 0.14, 40.0, 1),
    Audit("extreme", 50.0, 0.22, 80.0, 2),
    Audit("delay_1d", 10.0, 0.08, 0.0, 1),
)


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    family: str
    core_weight: float
    base_leverage: float
    target_vol: float
    state_strength: float
    min_leverage: float
    max_leverage: float
    rebalance_days: int
    no_trade_band: float
    smooth_days: int
    drawdown_guard: bool
    promotable: bool = True


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if pd.isna(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0 / 365.0
    return max(((index[-1] - index[0]).days + 1) / 365.0, 1.0 / 365.0)


def returns_metrics(returns: pd.Series, extra: pd.DataFrame | None = None) -> dict[str, Any]:
    returns = pd.to_numeric(returns, errors="coerce").fillna(0.0).astype(float)
    if returns.empty:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "worst_rolling_365": 0.0,
            "final_equity": INITIAL_EQUITY,
        }
    equity = INITIAL_EQUITY * (1.0 + returns).cumprod()
    years = elapsed_years(returns.index)
    total = float(equity.iloc[-1] / INITIAL_EQUITY - 1.0)
    cagr = float((equity.iloc[-1] / INITIAL_EQUITY) ** (1.0 / years) - 1.0)
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    downside = returns.where(returns < 0.0, 0.0)
    downside_std = float(downside.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(365.0)) if std > 0 else 0.0
    sortino = float(returns.mean() / downside_std * np.sqrt(365.0)) if downside_std > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    max_dd = float(drawdown.min())
    rolling = (1.0 + returns).rolling(365, min_periods=180).apply(np.prod, raw=True) - 1.0
    worst_365 = float(rolling.min()) if rolling.notna().any() else total
    metrics: dict[str, Any] = {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": cagr / abs(max_dd) if max_dd < 0 else 0.0,
        "worst_rolling_365": worst_365,
        "final_equity": float(equity.iloc[-1]),
    }
    if extra is not None and len(extra):
        years_extra = elapsed_years(extra.index)
        metrics.update(
            {
                "annual_meta_turnover": float(extra["meta_turnover"].sum() / years_extra),
                "average_leverage": float(extra["leverage"].mean()),
                "max_leverage": float(extra["leverage"].max()),
                "average_close_gross": float(extra["close_gross"].mean()),
                "max_close_gross": float(extra["close_gross"].max()),
                "transfer_cost": float(extra["transfer_cost"].sum()),
                "financing_cost": float(extra["financing_cost"].sum()),
                "extra_underlying_cost": float(extra["extra_underlying_cost"].sum()),
                "risk_reductions": int(extra["risk_reduction"].sum()),
                "scheduled_rebalances": int(extra["scheduled_rebalance"].sum()),
                "scheduled_rebalances": int(extra["scheduled_rebalance"].sum()),
            }
        )
    return metrics


def yearly_returns(returns: pd.Series, name: str = "return") -> pd.DataFrame:
    rows = [
        {"year": int(year), name: float((1.0 + group).prod() - 1.0)}
        for year, group in returns.groupby(returns.index.year)
    ]
    return pd.DataFrame(rows)


def load_v75(path: Path) -> tuple[pd.Series, dict[str, Any]]:
    actual_sha = sha256_file(path)
    if actual_sha != EXPECTED_V75_SHA256:
        raise RuntimeError(f"unexpected V75 compact stream SHA-256: {actual_sha}")
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    if frame.index.duplicated().any() or "equity" not in frame:
        raise ValueError("invalid V75 equity stream")
    equity = pd.to_numeric(frame["equity"], errors="coerce")
    if not np.isfinite(equity).all() or (equity <= 0.0).any():
        raise ValueError("non-finite or non-positive V75 equity")
    returns = equity.pct_change(fill_method=None)
    returns.iloc[0] = equity.iloc[0] / INITIAL_EQUITY - 1.0
    metrics = returns_metrics(returns)
    annual = yearly_returns(returns).set_index("year")["return"].to_dict()
    checks = {
        "sha256": actual_sha == EXPECTED_V75_SHA256,
        "rows": len(frame) == EXPECTED_V75["rows"],
        "start": str(frame.index.min()) == EXPECTED_V75["start"],
        "end": str(frame.index.max()) == EXPECTED_V75["end"],
        "total_return": abs(metrics["total_return"] - EXPECTED_V75["total_return"]) <= 1e-10,
        "cagr": abs(metrics["cagr"] - EXPECTED_V75["cagr"]) <= 5e-4,
        "max_drawdown": abs(metrics["max_drawdown"] - EXPECTED_V75["max_drawdown"]) <= 1e-10,
        "sharpe": abs(metrics["sharpe"] - EXPECTED_V75["sharpe"]) <= 5e-4,
        "annual_returns": all(
            abs(float(annual.get(year, np.nan)) - expected) <= 1e-10
            for year, expected in EXPECTED_V75["annual_returns"].items()
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"V75 economic equivalence failed: {checks}, metrics={metrics}, annual={annual}")
    evidence = {
        "program": PROGRAM,
        "file_sha256": actual_sha,
        "checks": checks,
        "metrics": metrics,
        "annual_returns": annual,
        "economic_equivalence_passed": True,
    }
    return returns.rename("v75_return"), evidence


def load_stabilizer(path: Path) -> pd.Series:
    frame = pd.read_csv(path)
    if "open_time" not in frame or "net_return" not in frame:
        raise ValueError("invalid persistent stabilizer stream")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.set_index("open_time").sort_index()
    if frame.index.duplicated().any():
        raise ValueError("duplicate stabilizer dates")
    values = pd.to_numeric(frame["net_return"], errors="coerce")
    if not np.isfinite(values).all():
        raise ValueError("non-finite stabilizer returns")
    return values.rename("stabilizer_return")


def load_state(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "open_time" not in frame:
        raise ValueError("market-state file lacks open_time")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.set_index("open_time").sort_index()
    required = {
        "trend",
        "breadth",
        "stress",
        "rotation",
        "liquidity",
        "leverage",
        "assignment_confidence",
        "novelty_ratio",
        "novelty_flag",
        "state_duration_days",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing state fields: {missing}")
    return frame


def aligned_inputs(v75: pd.Series, stabilizer: pd.Series, state: pd.DataFrame) -> pd.DataFrame:
    index = v75.index.intersection(stabilizer.index).intersection(state.index).sort_values()
    index = index[(index >= START) & (index < END)]
    if len(index) != EXPECTED_V75["rows"]:
        raise RuntimeError(f"unexpected aligned rows: {len(index)}")
    output = pd.concat([v75.reindex(index), stabilizer.reindex(index), state.reindex(index)], axis=1)
    if output[["v75_return", "stabilizer_return"]].isna().any().any():
        raise RuntimeError("missing strategy returns after alignment")
    return output


def continuous_state_score(frame: pd.DataFrame) -> pd.Series:
    raw = (
        0.24 * pd.to_numeric(frame["trend"], errors="coerce")
        + 0.18 * pd.to_numeric(frame["breadth"], errors="coerce")
        - 0.20 * pd.to_numeric(frame["stress"], errors="coerce")
        - 0.08 * pd.to_numeric(frame["rotation"], errors="coerce")
        + 0.18 * pd.to_numeric(frame["liquidity"], errors="coerce")
        + 0.18 * pd.to_numeric(frame["leverage"], errors="coerce")
    ).fillna(0.0)
    confidence = pd.to_numeric(frame["assignment_confidence"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    novelty_ratio = pd.to_numeric(frame["novelty_ratio"], errors="coerce").fillna(1.0).clip(lower=0.0)
    novelty_penalty = np.where(frame["novelty_flag"].astype(bool), 0.35, 0.0) + 0.15 * (novelty_ratio - 1.0).clip(lower=0.0)
    score = raw * (0.65 + 0.35 * confidence) - novelty_penalty
    return pd.Series(score, index=frame.index, name="state_score").clip(-4.0, 4.0)


def policy_book() -> tuple[Policy, ...]:
    items = [
        Policy("constant_core100_l145", "constant", 1.00, 1.45, 0.0, 0.0, 1.45, 1.45, 14, 0.08, 1, False, False),
        Policy("constant_core100_l160", "constant", 1.00, 1.60, 0.0, 0.0, 1.60, 1.60, 14, 0.08, 1, False, False),
        Policy("constant_core100_l175", "constant", 1.00, 1.75, 0.0, 0.0, 1.75, 1.75, 14, 0.08, 1, False, False),
        Policy("dd_core100_l160", "constant", 1.00, 1.60, 0.0, 0.0, 0.45, 1.60, 7, 0.06, 3, True),
        Policy("dd_core100_l175", "constant", 1.00, 1.75, 0.0, 0.0, 0.45, 1.75, 7, 0.06, 3, True),
        Policy("state_core100_l155_s25", "state", 1.00, 1.55, 0.0, 0.25, 0.55, 1.85, 7, 0.06, 7, True),
        Policy("state_core100_l165_s40", "state", 1.00, 1.65, 0.0, 0.40, 0.50, 1.95, 7, 0.06, 7, True),
    ]
    for core in (1.00, 0.90, 0.85):
        core_name = int(round(core * 100))
        for target_vol, cap, strength in (
            (0.34, 1.75, 0.00),
            (0.36, 1.85, 0.25),
            (0.38, 1.90, 0.25),
            (0.40, 1.95, 0.40),
            (0.42, 1.95, 0.40),
        ):
            items.append(
                Policy(
                    f"statevol_core{core_name}_tv{int(target_vol*100)}_s{int(strength*100)}",
                    "state_vol" if strength else "vol",
                    core,
                    1.0,
                    target_vol,
                    strength,
                    0.45,
                    cap,
                    7,
                    0.06,
                    7,
                    True,
                )
            )
    items.extend(
        [
            Policy("statevol_core90_tv38_s25_r14", "state_vol", 0.90, 1.0, 0.38, 0.25, 0.45, 1.90, 14, 0.08, 14, True),
            Policy("statevol_core90_tv40_s40_r14", "state_vol", 0.90, 1.0, 0.40, 0.40, 0.45, 1.95, 14, 0.08, 14, True),
            Policy("statevol_core85_tv40_s40_r14", "state_vol", 0.85, 1.0, 0.40, 0.40, 0.45, 1.95, 14, 0.08, 14, True),
        ]
    )
    names = [item.name for item in items]
    if len(names) != len(set(names)):
        raise AssertionError("duplicate policy names")
    return tuple(items)


def drawdown_factor(drawdown: float) -> float:
    if drawdown > -0.06:
        return 1.0
    if drawdown > -0.10:
        return 0.82
    if drawdown > -0.15:
        return 0.58
    return 0.35


def base_leverage_series(frame: pd.DataFrame, policy: Policy, audit: Audit) -> pd.Series:
    core = policy.core_weight
    base_return = core * frame["v75_return"] + (1.0 - core) * frame["stabilizer_return"]
    realized_vol = base_return.rolling(63, min_periods=32).std(ddof=1).mul(np.sqrt(365.0)).shift(1)
    score = continuous_state_score(frame)
    if policy.family == "constant":
        desired = pd.Series(policy.base_leverage, index=frame.index, dtype=float)
    elif policy.family == "vol":
        desired = policy.target_vol / realized_vol.replace(0.0, np.nan)
    elif policy.family == "state":
        state_factor = 1.0 + policy.state_strength * np.tanh(score / 1.5)
        desired = policy.base_leverage * state_factor
    elif policy.family == "state_vol":
        state_factor = 1.0 + policy.state_strength * np.tanh(score / 1.5)
        desired = (policy.target_vol / realized_vol.replace(0.0, np.nan)) * state_factor
    else:
        raise ValueError(policy.family)
    desired = desired.replace([np.inf, -np.inf], np.nan).fillna(policy.base_leverage)
    if audit.signal_delay_days > 0 and policy.family != "constant":
        desired = desired.shift(audit.signal_delay_days).fillna(policy.base_leverage)
    desired = desired.clip(policy.min_leverage, policy.max_leverage)
    if policy.smooth_days > 1:
        desired = desired.ewm(span=policy.smooth_days, adjust=False, min_periods=1).mean()
    return desired.clip(policy.min_leverage, policy.max_leverage)


def simulate(frame: pd.DataFrame, policy: Policy, audit: Audit) -> pd.DataFrame:
    desired_series = base_leverage_series(frame, policy, audit)
    state_score_series = continuous_state_score(frame)
    core_target = policy.core_weight
    holdings = np.zeros(2, dtype=float)
    cash = INITIAL_EQUITY
    equity = INITIAL_EQUITY
    high_water = INITIAL_EQUITY
    previous_target = np.zeros(2, dtype=float)
    records: list[dict[str, Any]] = []

    for day_number, (timestamp, row) in enumerate(frame.iterrows()):
        previous_equity = equity
        current_weights = holdings / max(equity, 1e-12)
        current_gross = float(np.abs(current_weights).sum())
        previous_drawdown = equity / max(high_water, 1e-12) - 1.0
        desired_leverage = float(desired_series.loc[timestamp])
        if policy.drawdown_guard:
            desired_leverage *= drawdown_factor(previous_drawdown)
        desired_leverage = float(np.clip(desired_leverage, policy.min_leverage, policy.max_leverage))
        target = np.array([core_target, 1.0 - core_target], dtype=float) * desired_leverage
        l1_change = float(np.abs(target - current_weights).sum())
        risk_reduction = desired_leverage < current_gross - 0.04
        scheduled = day_number == 0 or day_number % policy.rebalance_days == 0
        target_changed = float(np.abs(target - previous_target).sum()) >= policy.no_trade_band
        should_rebalance = day_number == 0 or risk_reduction or (scheduled and target_changed)

        meta_turnover = 0.0
        transfer_cost = 0.0
        scheduled_event = 0
        risk_event = 0
        if should_rebalance:
            meta_turnover = l1_change
            transfer_cost = equity * meta_turnover * audit.transfer_cost_rate
            equity_after_cost = max(equity - transfer_cost, 1e-12)
            holdings = target * equity_after_cost
            cash = equity_after_cost - float(holdings.sum())
            previous_target = target.copy()
            scheduled_event = int(scheduled and not risk_reduction)
            risk_event = int(risk_reduction)
        else:
            cash = equity - float(holdings.sum())

        financing_cost = max(-cash, 0.0) * audit.financing_rate / 365.0
        cash -= financing_cost
        close_open_leverage = float(np.abs(holdings).sum() / max(equity, 1e-12))
        component_turnover = (
            close_open_leverage
            * (
                core_target * V75_ANNUAL_TURNOVER
                + (1.0 - core_target) * STABILIZER_ANNUAL_TURNOVER
            )
        )
        extra_underlying_cost = (
            equity
            * component_turnover
            * audit.extra_underlying_cost_rate
            / 365.0
        )
        cash -= extra_underlying_cost

        component_returns = np.array(
            [float(row["v75_return"]), float(row["stabilizer_return"])], dtype=float
        )
        holdings *= 1.0 + component_returns
        equity = float(cash + holdings.sum())
        if not np.isfinite(equity) or equity <= 0.0:
            raise RuntimeError(f"non-positive account equity at {timestamp}: {equity}")
        high_water = max(high_water, equity)
        net_return = equity / previous_equity - 1.0
        close_weights = holdings / equity
        close_gross = float(np.abs(close_weights).sum())
        records.append(
            {
                "net_return": net_return,
                "equity": equity,
                "leverage": desired_leverage,
                "close_gross": close_gross,
                "core_weight_close": float(close_weights[0]),
                "stabilizer_weight_close": float(close_weights[1]),
                "cash_weight_close": float(cash / equity),
                "meta_turnover": meta_turnover,
                "transfer_cost": transfer_cost,
                "financing_cost": financing_cost,
                "extra_underlying_cost": extra_underlying_cost,
                "risk_reduction": risk_event,
                "scheduled_rebalance": scheduled_event,
                "previous_drawdown": previous_drawdown,
                "state_score": float(state_score_series.loc[timestamp]),
                "state_label": str(row.get("state_label", "unknown")),
                "state_duration_days": int(row.get("state_duration_days", 0)),
                "novelty_flag": bool(row.get("novelty_flag", False)),
            }
        )
    return pd.DataFrame(records, index=frame.index)


def cut(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    start, end = PERIODS[period]
    return frame.loc[(frame.index >= start) & (frame.index < end)]


def metrics_for_account(account: pd.DataFrame, period: str) -> dict[str, Any]:
    part = cut(account, period)
    return returns_metrics(part["net_return"], part)


def development_checks(
    base_metrics: dict[str, Any],
    severe_metrics: dict[str, Any],
    annual: pd.DataFrame,
    policy: Policy,
) -> dict[str, bool]:
    annual_map = annual.set_index("year")["return"].to_dict()
    return {
        "promotable": policy.promotable,
        "cagr_ge_45pct": float(base_metrics["cagr"]) >= 0.45,
        "sharpe_ge_1_35": float(base_metrics["sharpe"]) >= 1.35,
        "max_dd_ge_minus25pct": float(base_metrics["max_drawdown"]) >= -0.25,
        "worst_rolling_365_ge_minus15pct": float(base_metrics["worst_rolling_365"]) >= -0.15,
        "2021_positive": float(annual_map.get(2021, -1.0)) > 0.0,
        "2022_ge_minus3pct": float(annual_map.get(2022, -1.0)) >= -0.03,
        "2023_positive": float(annual_map.get(2023, -1.0)) > 0.0,
        "meta_turnover_le_5x": float(base_metrics["annual_meta_turnover"]) <= 5.0,
        "average_leverage_le_1_65": float(base_metrics["average_leverage"]) <= 1.65,
        "max_leverage_le_1_95": float(base_metrics["max_leverage"]) <= 1.9500001,
        "severe_cagr_ge_30pct": float(severe_metrics["cagr"]) >= 0.30,
        "severe_dd_ge_minus32pct": float(severe_metrics["max_drawdown"]) >= -0.32,
    }


def score_candidate(metrics: dict[str, Any], annual: pd.DataFrame) -> float:
    annual_min = float(annual["return"].min()) if len(annual) else -1.0
    return float(
        metrics["cagr"]
        + 0.10 * metrics["sharpe"]
        - 0.40 * max(0.0, abs(metrics["max_drawdown"]) - 0.22)
        + 0.10 * annual_min
        - 0.004 * metrics["annual_meta_turnover"]
        - 0.03 * max(0.0, metrics["average_leverage"] - 1.50)
    )


def post_oos_checks(audits: dict[str, pd.DataFrame]) -> dict[str, bool]:
    base = audits["base"]
    severe = audits["severe"]
    extreme = audits["extreme"]
    delay = audits["delay_1d"]
    base_full = metrics_for_account(base, "full")
    annual = yearly_returns(base["net_return"])
    annual_map = annual.set_index("year")["return"].to_dict()
    return {
        "validation_2024_positive": metrics_for_account(base, "validation_2024")["total_return"] > 0.0,
        "holdout_2025_positive": metrics_for_account(base, "holdout_2025")["total_return"] > 0.0,
        "final_2026h1_positive": metrics_for_account(base, "final_2026h1")["total_return"] > 0.0,
        "full_cagr_ge_45pct": base_full["cagr"] >= 0.45,
        "full_cagr_ge_50pct_target": base_full["cagr"] >= 0.50,
        "full_sharpe_ge_1_35": base_full["sharpe"] >= 1.35,
        "full_dd_ge_minus27pct": base_full["max_drawdown"] >= -0.27,
        "severe_full_cagr_ge_30pct": metrics_for_account(severe, "full")["cagr"] >= 0.30,
        "extreme_full_cagr_ge_15pct": metrics_for_account(extreme, "full")["cagr"] >= 0.15,
        "delay_full_cagr_ge_40pct": metrics_for_account(delay, "full")["cagr"] >= 0.40,
        "worst_calendar_year_ge_minus10pct": min(annual_map.values()) >= -0.10,
        "max_leverage_le_1_95": base_full["max_leverage"] <= 1.9500001,
    }


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json" or "__pycache__" in path.parts:
            continue
        files[str(path.relative_to(root))] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    write_json(root / "MANIFEST.json", {"program": PROGRAM, "files": files})


def self_test() -> None:
    index = pd.date_range("2021-01-01", periods=500, freq="1D", tz="UTC")
    rng = np.random.default_rng(501)
    v75 = pd.Series(rng.normal(0.0007, 0.018, len(index)), index=index)
    stabilizer = pd.Series(rng.normal(0.0003, 0.007, len(index)), index=index)
    state = pd.DataFrame(
        {
            "v75_return": v75,
            "stabilizer_return": stabilizer,
            "trend": rng.normal(size=len(index)),
            "breadth": rng.normal(size=len(index)),
            "stress": rng.normal(size=len(index)),
            "rotation": rng.normal(size=len(index)),
            "liquidity": rng.normal(size=len(index)),
            "leverage": rng.normal(size=len(index)),
            "assignment_confidence": rng.uniform(0.2, 0.9, len(index)),
            "novelty_ratio": rng.uniform(0.5, 1.5, len(index)),
            "novelty_flag": rng.random(len(index)) < 0.1,
            "state_duration_days": rng.integers(1, 30, len(index)),
            "state_label": "synthetic",
        },
        index=index,
    )
    policy = next(item for item in policy_book() if item.name == "statevol_core90_tv38_s25")
    account = simulate(state, policy, AUDITS[0])
    assert len(account) == len(index)
    assert np.isfinite(account.to_numpy(dtype=object)[:, :13].astype(float)).all()
    assert float(account["leverage"].max()) <= policy.max_leverage + 1e-12
    changed = state.copy()
    changed.iloc[-1, changed.columns.get_loc("trend")] *= 100.0
    changed_account = simulate(changed, policy, AUDITS[0])
    pd.testing.assert_series_equal(
        account["equity"].iloc[:-1], changed_account["equity"].iloc[:-1], check_names=False
    )
    delayed = simulate(state, policy, AUDITS[-1])
    assert not delayed.equals(account)
    print("V501-V508 risk-budget self-test passed")


def run(v75_path: Path, stabilizer_path: Path, state_path: Path, output: Path, design: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    v75, equivalence = load_v75(v75_path)
    stabilizer = load_stabilizer(stabilizer_path)
    state = load_state(state_path)
    frame = aligned_inputs(v75, stabilizer, state)
    write_json(output / "V75_ECONOMIC_EQUIVALENCE.json", equivalence)

    rows: list[dict[str, Any]] = []
    accounts: dict[tuple[str, str], pd.DataFrame] = {}
    for number, policy in enumerate(policy_book(), 1):
        base = simulate(frame, policy, AUDITS[0])
        severe = simulate(frame, policy, AUDITS[1])
        accounts[(policy.name, "base")] = base
        accounts[(policy.name, "severe")] = severe
        base_dev = metrics_for_account(base, "development")
        severe_dev = metrics_for_account(severe, "development")
        annual = yearly_returns(cut(base, "development")["net_return"])
        checks = development_checks(base_dev, severe_dev, annual, policy)
        eligible = bool(all(checks.values()))
        row = {
            "policy": policy.name,
            **asdict(policy),
            "eligible": eligible,
            "score": score_candidate(base_dev, annual),
            **{f"development_{key}": value for key, value in base_dev.items()},
            **{f"severe_development_{key}": value for key, value in severe_dev.items()},
            **{f"gate_{key}": value for key, value in checks.items()},
        }
        rows.append(row)
        print(
            f"{number}/{len(policy_book())} {policy.name} "
            f"CAGR={base_dev['cagr']:.4f} DD={base_dev['max_drawdown']:.4f} "
            f"Sharpe={base_dev['sharpe']:.3f} eligible={eligible}",
            flush=True,
        )

    ranking = pd.DataFrame(rows).sort_values(["eligible", "score"], ascending=[False, False])
    ranking.to_csv(output / "development_ranking.csv", index=False)
    eligible = ranking[ranking["eligible"]]
    selected_name = str(eligible.iloc[0]["policy"]) if len(eligible) else None
    proof = {
        "program": PROGRAM,
        "design_sha256": sha256_file(design),
        "v75_economic_equivalence": equivalence,
        "selection_period": [str(PERIODS["development"][0]), str(PERIODS["development"][1])],
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(policy_book()),
        "eligible_policies": list(eligible["policy"].astype(str)),
        "selected_policy": selected_name,
        "ranking_top": ranking.head(20).to_dict(orient="records"),
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(output / "selection_proof_before_oos.json", proof)

    if selected_name is None:
        decision = {
            "program": PROGRAM,
            "status": "rejected_before_oos",
            "eligible_policy_count": 0,
            "selected_policy": None,
            "oos_opened": False,
            "historical_50pct_target_met": False,
            "integration_permitted": False,
            "capital_change_authorized": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        summary = {**decision, "selection": proof}
        write_json(output / "FROZEN_DECISION.json", decision)
        write_json(output / "summary.json", summary)
        (output / "REPORT_RU.md").write_text(
            "# V501–V508 V75 risk budget\n\n"
            "Status: `rejected_before_oos`. Ни одна политика не прошла development gates; "
            "2024–2026 H1 не открывались.\n",
            encoding="utf-8",
        )
        write_manifest(output)
        return 0

    selected_policy = next(item for item in policy_book() if item.name == selected_name)
    audits: dict[str, pd.DataFrame] = {}
    audit_rows: list[dict[str, Any]] = []
    for audit in AUDITS:
        account = simulate(frame, selected_policy, audit)
        audits[audit.name] = account
        account.to_csv(output / f"equity_{audit.name}.csv", index_label="open_time")
        for period in PERIODS:
            audit_rows.append(
                {
                    "audit": audit.name,
                    "period": period,
                    **asdict(audit),
                    **metrics_for_account(account, period),
                }
            )
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "audit_metrics.csv", index=False)
    annual = yearly_returns(audits["base"]["net_return"])
    annual.to_csv(output / "ANNUAL_RETURNS.csv", index=False)
    checks = post_oos_checks(audits)
    target_met = bool(checks["full_cagr_ge_50pct_target"])
    robustness_passed = bool(all(value for key, value in checks.items() if key != "full_cagr_ge_50pct_target"))
    status = (
        "exploratory_50pct_target_met_no_capital_authority"
        if target_met and robustness_passed
        else "rejected_after_frozen_oos"
    )
    decision = {
        "program": PROGRAM,
        "status": status,
        "eligible_policy_count": int(len(eligible)),
        "selected_policy": selected_name,
        "oos_opened": True,
        "post_oos_checks": checks,
        "historical_50pct_target_met": target_met,
        "robustness_passed": robustness_passed,
        "integration_permitted": False,
        "capital_change_authorized": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selected_policy_spec": asdict(selected_policy),
        "selection": proof,
        "base_full": metrics_for_account(audits["base"], "full"),
        "base_validation_2024": metrics_for_account(audits["base"], "validation_2024"),
        "base_holdout_2025": metrics_for_account(audits["base"], "holdout_2025"),
        "base_final_2026h1": metrics_for_account(audits["base"], "final_2026h1"),
        "severe_full": metrics_for_account(audits["severe"], "full"),
        "extreme_full": metrics_for_account(audits["extreme"], "full"),
        "delay_full": metrics_for_account(audits["delay_1d"], "full"),
        "annual_returns": annual.to_dict(orient="records"),
    }
    write_json(output / "FROZEN_DECISION.json", decision)
    write_json(output / "summary.json", summary)

    report = [
        "# V501–V508 — V75 account-level risk budget",
        "",
        f"Selected policy: `{selected_name}`.",
        "",
        "## Full base result",
        "",
        f"- CAGR: {100*summary['base_full']['cagr']:.2f}%",
        f"- Sharpe: {summary['base_full']['sharpe']:.3f}",
        f"- Max DD: {100*summary['base_full']['max_drawdown']:.2f}%",
        f"- Average leverage: {summary['base_full']['average_leverage']:.3f}x",
        f"- Max leverage: {summary['base_full']['max_leverage']:.3f}x",
        "",
        "## OOS",
        "",
        f"- 2024: {100*summary['base_validation_2024']['total_return']:.2f}%",
        f"- 2025: {100*summary['base_holdout_2025']['total_return']:.2f}%",
        f"- 2026 H1: {100*summary['base_final_2026h1']['total_return']:.2f}%",
        "",
        "## Stress",
        "",
        f"- Severe full CAGR: {100*summary['severe_full']['cagr']:.2f}%",
        f"- Extreme full CAGR: {100*summary['extreme_full']['cagr']:.2f}%",
        f"- 1-day delay full CAGR: {100*summary['delay_full']['cagr']:.2f}%",
        "",
        f"Status: `{status}`.",
        "",
        "This is non-pristine account-level research. It does not authorize leverage or capital changes.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    write_manifest(output)
    print(json.dumps(clean(summary), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v75", type=Path)
    parser.add_argument("--stabilizer", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if None in (args.v75, args.stabilizer, args.state, args.output, args.design):
        raise SystemExit("--v75, --stabilizer, --state, --output and --design are required")
    return run(args.v75, args.stabilizer, args.state, args.output, args.design)


if __name__ == "__main__":
    raise SystemExit(main())
