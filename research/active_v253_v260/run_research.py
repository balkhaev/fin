#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
V9_ROOT = REPO_ROOT / "research" / "active_v9"
sys.path.insert(0, str(V9_ROOT))

from config import Config as V9Config  # noqa: E402
from data import load as load_v9  # noqa: E402
from market import Market  # noqa: E402

CANDIDATE = "ACTIVE_V253_CRYPTO_LOW_RISK_FACTOR"
SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "EOSUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "TRXUSDT",
    "SOLUSDT",
)
FAMILIES = (
    "low_residual_volatility",
    "low_downside_beta",
    "low_residual_tail_loss",
    "reversed_high_risk_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_START = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
FINAL_START = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.60
MAX_REALIZED_GROSS = 0.70
FORCED_EXIT_PENALTY_BPS = 100.0


@dataclass(frozen=True, slots=True)
class Policy:
    family: str
    lookback_days: int
    long_short_k: int
    rebalance_days: int
    neutralization: str

    @property
    def name(self) -> str:
        return (
            f"{self.family}_l{self.lookback_days}_k{self.long_short_k}_"
            f"r{self.rebalance_days}_{self.neutralization}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    cost_bps_per_side: float
    execution_delay_days: int = 0

    @property
    def cost_rate(self) -> float:
        return self.cost_bps_per_side / 10_000.0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        FAMILIES,
        (60, 90, 180),
        (2, 3),
        (7, 14, 28),
        ("dollar", "beta"),
    )
)
AUDITS = (
    Audit("base", 30.0),
    Audit("severe", 60.0),
    Audit("extreme", 100.0),
    Audit("delay_1d", 30.0, execution_delay_days=1),
)
DEVELOPMENT_GATES = {
    "cagr_min": 0.04,
    "sharpe_min": 0.80,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 24,
    "annual_turnover_max": 20.0,
    "max_realized_gross": MAX_REALIZED_GROSS,
    "all_development_years_positive": True,
    "net_long_leg_pnl_positive": True,
    "net_short_leg_pnl_positive": True,
    "symbols_traded_min": 8,
    "top_positive_asset_pnl_share_max": 0.40,
}
POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.10,
    "forced_exit_count_max": 4,
}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def month_range(first: str, last: str) -> set[str]:
    return {str(value) for value in pd.period_range(first, last, freq="M")}


def data_gate(
    klines: dict[str, pd.DataFrame],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    all_passed = True
    for symbol in SYMBOLS:
        price_records = [row for row in records if row["symbol"] == symbol and row["kind"] == "klines"]
        funding_records = [
            row for row in records if row["symbol"] == symbol and row["kind"] == "fundingRate"
        ]
        valid_price = sorted(
            row["month"] for row in price_records if not row.get("missing") and int(row.get("rows", 0)) > 0
        )
        valid_funding = {
            row["month"]
            for row in funding_records
            if not row.get("missing") and int(row.get("rows", 0)) > 0
        }
        if valid_price:
            active_months = month_range(valid_price[0], valid_price[-1])
            price_share = len(set(valid_price) & active_months) / len(active_months)
            funding_share = len(valid_funding & active_months) / len(active_months)
        else:
            active_months = set()
            price_share = 0.0
            funding_share = 0.0
        development_months = month_range("2021-01", "2023-12")
        development_price_months = len(set(valid_price) & development_months)
        latest_required = symbol in {"BTCUSDT", "ETHUSDT"}
        latest_present = "2026-06" in set(valid_price)
        symbol_passed = bool(
            symbol in klines
            and price_share >= 0.95
            and funding_share >= 0.90
            and development_price_months >= 34
            and (not latest_required or latest_present)
        )
        frame = klines.get(symbol, pd.DataFrame())
        details[symbol] = {
            "first_price_month": valid_price[0] if valid_price else None,
            "last_price_month": valid_price[-1] if valid_price else None,
            "active_month_count": len(active_months),
            "price_active_month_share": price_share,
            "funding_active_month_share": funding_share,
            "development_price_months": development_price_months,
            "latest_required": latest_required,
            "latest_present": latest_present,
            "rows": len(frame),
            "timestamp_min": frame.index.min().isoformat() if not frame.empty else None,
            "timestamp_max": frame.index.max().isoformat() if not frame.empty else None,
            "passed": symbol_passed,
        }
        all_passed = all_passed and symbol_passed
    return {
        "candidate": "V253_FIXED_UNIVERSE_DATA_COVERAGE",
        "fixed_universe_size": len(SYMBOLS),
        "minimum_price_active_month_share": 0.95,
        "minimum_funding_active_month_share": 0.90,
        "minimum_development_price_months": 34,
        "details": details,
        "passed": bool(all_passed),
        "survivor_replacement_permitted": False,
    }


def score_frame(market: Market, family: str, lookback: int) -> pd.DataFrame:
    beta = market.beta(lookback).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)
    residual_vol = residual.rolling(lookback, min_periods=lookback).std(ddof=1) * np.sqrt(365.0)
    if family == "low_residual_volatility":
        return -residual_vol
    if family == "reversed_high_risk_control":
        return residual_vol
    if family == "low_downside_beta":
        mask = market.market.lt(0.0)
        downside_market = market.market.where(mask)
        min_periods = max(20, lookback // 4)
        denominator = downside_market.rolling(lookback, min_periods=min_periods).var()
        result = pd.DataFrame(index=market.index, columns=market.symbols, dtype=float)
        for symbol in market.symbols:
            downside_asset = market.logret[symbol].where(mask)
            covariance = downside_asset.rolling(lookback, min_periods=min_periods).cov(
                downside_market
            )
            result[symbol] = -(covariance / denominator.replace(0.0, np.nan))
        return result
    if family == "low_residual_tail_loss":
        return residual.rolling(lookback, min_periods=lookback).quantile(0.10)
    raise ValueError(family)


def raw_weights(
    market: Market,
    score: pd.DataFrame,
    k: int,
    neutralization: str,
) -> pd.DataFrame:
    values = score.reindex(market.index).to_numpy(float)
    available = market.available.to_numpy(bool)
    volatility = market.vol.to_numpy(float)
    beta = market.beta(90).shift(1).to_numpy(float)
    output = np.zeros_like(values)
    for i in range(len(values)):
        valid = np.flatnonzero(
            available[i]
            & np.isfinite(values[i])
            & np.isfinite(volatility[i])
            & (volatility[i] > 1e-6)
        )
        if len(valid) < 2 * k:
            continue
        selected_values = values[i, valid]
        if np.nanmax(selected_values) - np.nanmin(selected_values) < 1e-12:
            continue
        order = valid[np.argsort(selected_values)]
        short = order[:k]
        long = order[-k:]
        long_weights = 1.0 / volatility[i, long]
        short_weights = 1.0 / volatility[i, short]
        long_weights /= long_weights.sum()
        short_weights /= short_weights.sum()
        if neutralization == "beta":
            long_beta = float(np.sum(long_weights * np.maximum(beta[i, long], 0.05)))
            short_beta = float(np.sum(short_weights * np.maximum(beta[i, short], 0.05)))
            if not np.isfinite(long_beta) or not np.isfinite(short_beta) or short_beta <= 0:
                continue
            short_weights *= np.clip(long_beta / short_beta, 0.5, 1.5)
            gross = long_weights.sum() + short_weights.sum()
            long_weights *= TARGET_GROSS / gross
            short_weights *= TARGET_GROSS / gross
        elif neutralization == "dollar":
            long_weights *= TARGET_GROSS / 2.0
            short_weights *= TARGET_GROSS / 2.0
        else:
            raise ValueError(neutralization)
        output[i, long] = long_weights
        output[i, short] = -short_weights
    return pd.DataFrame(output, index=market.index, columns=market.symbols)


def schedule_weights(
    raw: pd.DataFrame,
    available: pd.DataFrame,
    every: int,
    band: float = 0.10,
) -> pd.DataFrame:
    values = raw.to_numpy(float)
    availability = available.reindex(raw.index).to_numpy(bool)
    output = np.zeros_like(values)
    current = np.zeros(values.shape[1], dtype=float)
    for i, row in enumerate(values):
        current[~availability[i]] = 0.0
        candidate = np.nan_to_num(row, nan=0.0, posinf=0.0, neginf=0.0)
        candidate[~availability[i]] = 0.0
        if i % every == 0 and np.abs(candidate - current).sum() >= band:
            current = candidate.copy()
        gross = float(np.abs(current).sum())
        if gross > TARGET_GROSS:
            current *= TARGET_GROSS / gross
        output[i] = current
    return pd.DataFrame(output, index=raw.index, columns=raw.columns)


def build_weights(
    market: Market,
    policy: Policy,
    score_cache: dict[tuple[str, int], pd.DataFrame],
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame],
) -> pd.DataFrame:
    score_key = (policy.family, policy.lookback_days)
    if score_key not in score_cache:
        score_cache[score_key] = score_frame(market, *score_key)
    raw_key = (
        policy.family,
        policy.lookback_days,
        policy.long_short_k,
        policy.neutralization,
    )
    if raw_key not in raw_cache:
        raw_cache[raw_key] = raw_weights(
            market,
            score_cache[score_key],
            policy.long_short_k,
            policy.neutralization,
        )
    return schedule_weights(raw_cache[raw_key], market.available, policy.rebalance_days)


def simulate(
    market: Market,
    weights: pd.DataFrame,
    start: str,
    end_exclusive: str,
    audit: Audit,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    index = market.index
    selected = np.flatnonzero(
        (index >= pd.Timestamp(start, tz="UTC"))
        & (index < pd.Timestamp(end_exclusive, tz="UTC"))
    )
    if not len(selected):
        return pd.DataFrame(), {}
    opens = market.open.to_numpy(float)
    closes = market.close.to_numpy(float)
    funding = market.funding.to_numpy(float)
    available = market.available.to_numpy(bool)
    target_values = weights.reindex(index).fillna(0.0).to_numpy(float)
    symbol_count = len(market.symbols)
    notional = np.zeros(symbol_count, dtype=float)
    equity = INITIAL_EQUITY
    previous = -1
    rows: list[dict[str, Any]] = []
    asset_pnl = {symbol: 0.0 for symbol in market.symbols}
    long_pnl = 0.0
    short_pnl = 0.0
    ever_traded: set[str] = set()
    forced_exit_count = 0
    last_signal_target: np.ndarray | None = None

    def allocate(value: float, symbol_index: int, side: float) -> None:
        nonlocal long_pnl, short_pnl
        symbol = market.symbols[symbol_index]
        asset_pnl[symbol] += float(value)
        if side > 0:
            long_pnl += float(value)
        elif side < 0:
            short_pnl += float(value)

    for i in selected:
        day_cost = 0.0
        day_funding = 0.0
        day_price = 0.0
        day_forced = 0
        if previous >= 0:
            for j in np.flatnonzero(np.abs(notional) > 1e-12):
                side = float(np.sign(notional[j]))
                if np.isfinite(opens[i, j]) and np.isfinite(closes[previous, j]) and closes[previous, j] > 0:
                    overnight_return = opens[i, j] / closes[previous, j] - 1.0
                    pnl = float(notional[j] * overnight_return)
                    equity += pnl
                    day_price += pnl
                    allocate(pnl, j, side)
                    notional[j] *= opens[i, j] / closes[previous, j]
                else:
                    penalty_rate = max(audit.cost_rate, FORCED_EXIT_PENALTY_BPS / 10_000.0)
                    penalty = float(abs(notional[j]) * penalty_rate)
                    equity -= penalty
                    day_cost += penalty
                    allocate(-penalty, j, side)
                    notional[j] = 0.0
                    forced_exit_count += 1
                    day_forced += 1

        signal_index = i - 1 - audit.execution_delay_days
        target = (
            target_values[signal_index].copy()
            if signal_index >= 0
            else np.zeros(symbol_count, dtype=float)
        )
        target[~available[i]] = 0.0
        gross_target = float(np.abs(target).sum())
        if gross_target > TARGET_GROSS:
            target *= TARGET_GROSS / gross_target
        equity_before_trade = max(equity, 1e-12)
        target_changed = bool(
            last_signal_target is None
            or not np.allclose(target, last_signal_target, rtol=0.0, atol=1e-12)
        )
        current_gross = float(np.abs(notional).sum() / equity_before_trade)
        risk_rebalance = current_gross > MAX_REALIZED_GROSS
        need_rebalance = bool(target_changed or risk_rebalance or day_forced > 0)
        turnover = 0.0
        rebalance_event = 0
        if need_rebalance:
            desired = target * equity_before_trade
            delta = desired - notional
            turnover_notional = float(np.abs(delta).sum())
            turnover = turnover_notional / equity_before_trade
            if turnover_notional > 0:
                cost_by_asset = np.abs(delta) * audit.cost_rate
                for j in np.flatnonzero(cost_by_asset > 0):
                    side = (
                        float(np.sign(desired[j]))
                        if abs(desired[j]) > 1e-12
                        else float(np.sign(notional[j]))
                    )
                    allocate(-float(cost_by_asset[j]), j, side)
                trade_cost = float(cost_by_asset.sum())
                equity -= trade_cost
                day_cost += trade_cost
                notional = target * max(equity, 0.0)
                for j in np.flatnonzero(np.abs(notional) > 1e-12):
                    ever_traded.add(market.symbols[j])
            rebalance_event = int(turnover > 1e-4)
            last_signal_target = target.copy()

        valid = np.isfinite(opens[i]) & np.isfinite(closes[i]) & (opens[i] > 0)
        ratios = np.divide(closes[i], opens[i], out=np.ones(symbol_count), where=valid)
        for j in np.flatnonzero(np.abs(notional) > 1e-12):
            side = float(np.sign(notional[j]))
            price_pnl = float(notional[j] * (ratios[j] - 1.0))
            funding_pnl = float(-(notional[j] * funding[i, j]))
            equity += price_pnl + funding_pnl
            day_price += price_pnl
            day_funding += funding_pnl
            allocate(price_pnl + funding_pnl, j, side)
            notional[j] *= ratios[j]

        gross = float(np.abs(notional).sum() / max(equity, 1e-12))
        rows.append(
            {
                "equity": max(0.0, equity),
                "gross": gross,
                "turnover": turnover,
                "costs": day_cost,
                "funding_pnl": day_funding,
                "price_pnl": day_price,
                "rebalance_events": rebalance_event,
                "forced_exits": day_forced,
            }
        )
        previous = int(i)

    account = pd.DataFrame(rows, index=index[selected])
    positive = [max(0.0, value) for value in asset_pnl.values()]
    positive_total = float(sum(positive))
    top_positive_share = max(positive) / positive_total if positive_total > 0 else 1.0
    diagnostics = {
        "asset_pnl": asset_pnl,
        "long_leg_pnl": long_pnl,
        "short_leg_pnl": short_pnl,
        "symbols_traded": sorted(ever_traded),
        "symbol_count_traded": len(ever_traded),
        "forced_exit_count": forced_exit_count,
        "rebalance_events": int(account.rebalance_events.sum()) if not account.empty else 0,
        "top_positive_asset_pnl_share": top_positive_share,
    }
    return account, diagnostics


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0), 1 / 365.2425)


def account_metrics(account: pd.DataFrame) -> dict[str, float | int]:
    if account.empty:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "annual_turnover": 0.0,
            "average_gross": 0.0,
            "max_gross": 0.0,
            "costs": 0.0,
            "funding_pnl": 0.0,
            "rebalance_events": 0,
            "forced_exits": 0,
        }
    years = elapsed_years(account.index)
    final = float(account.equity.iloc[-1])
    total_return = final / INITIAL_EQUITY - 1.0
    cagr = (final / INITIAL_EQUITY) ** (1.0 / years) - 1.0 if final > 0 and years > 0 else -1.0
    returns = account.equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    frequency = len(returns) / years if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(frequency)) if std > 0 else 0.0
    drawdown = account.equity / account.equity.cummax() - 1.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "costs": float(account.costs.sum()),
        "funding_pnl": float(account.funding_pnl.sum()),
        "rebalance_events": int(account.rebalance_events.sum()),
        "forced_exits": int(account.forced_exits.sum()),
    }


def slice_account(account: pd.DataFrame, start: str, end_exclusive: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end_exclusive, tz="UTC")
    selected = account[(account.index >= start_ts) & (account.index < end_ts)].copy()
    if selected.empty:
        return selected
    before = account[account.index < start_ts]
    base = float(before.equity.iloc[-1]) if not before.empty else INITIAL_EQUITY
    scale = INITIAL_EQUITY / base
    for column in ("equity", "costs", "funding_pnl", "price_pnl"):
        selected[column] *= scale
    return selected


def yearly_returns(account: pd.DataFrame, name: str) -> pd.DataFrame:
    previous = INITIAL_EQUITY
    rows: list[dict[str, Any]] = []
    for year, part in account.groupby(account.index.year):
        end_value = float(part.equity.iloc[-1])
        rows.append({"year": int(year), name: end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}


def self_test() -> None:
    assert len(POLICIES) == 144
    index = pd.date_range("2020-01-01", periods=1100, freq="1D", tz="UTC")
    rng = np.random.default_rng(253)
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(SYMBOLS[:7]):
        volatility = 0.007 + number * 0.002
        returns = rng.normal(0.0, volatility, len(index))
        close = 100.0 * np.exp(np.cumsum(returns))
        open_price = np.r_[close[0], close[:-1] * np.exp(rng.normal(0.0, 0.001, len(index) - 1))]
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * 1.01,
                "low": np.minimum(open_price, close) * 0.99,
                "close": close,
                "volume": 1.0,
                "quote_volume": 1.0,
                "trades": 1.0,
                "taker_buy_base": 0.5,
                "taker_buy_quote": 0.5,
            },
            index=index,
        )
        funding[symbol] = pd.Series(0.0, index=index)
    market = Market(klines, funding)
    policy = Policy("low_residual_volatility", 60, 2, 7, "beta")
    weights = build_weights(market, policy, {}, {})
    account, diagnostics = simulate(market, weights, "2021-01-01", "2023-01-01", AUDITS[0])
    assert not account.empty
    assert np.isfinite(account.equity).all()
    debug = {
        "max_gross": float(account.gross.max()),
        "rebalance_events": diagnostics["rebalance_events"],
        "rows": len(account),
        "symbols": diagnostics["symbol_count_traded"],
    }
    print("V254 execution diagnostics", debug)
    # Synthetic-path sanity only. The immutable 0.70 gross limit remains an
    # explicit development eligibility gate below; this assertion merely catches
    # explosive accounting failures before any market-data replay.
    assert float(account.gross.max()) < 1.0
    assert diagnostics["symbol_count_traded"] >= 4
    assert diagnostics["rebalance_events"] < len(account) // 3

    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    changed[SYMBOLS[0]].iloc[-1, changed[SYMBOLS[0]].columns.get_loc("close")] *= 4.0
    changed_market = Market(changed, funding)
    changed_weights = build_weights(changed_market, policy, {}, {})
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V253-V260 self-test passed")


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(root))
        if rel in {"MANIFEST.json", "run.log"}:
            continue
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(root / "MANIFEST.json", {"candidate": CANDIDATE, "files": files})


def failure_outputs(
    root: Path,
    gate: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    decision = {
        "candidate": CANDIDATE,
        "status": "data_access_insufficient",
        "standalone_selection_passed": False,
        "integration_permitted": False,
        "promoted_candidates": [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "coverage_gate": gate,
        "selection": None,
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "coverage_gate.json", gate)
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    write_json(
        results / "selection_proof_before_validation.json",
        {"candidate": CANDIDATE, "selection_not_run": True, "reason": "data gate failed"},
    )
    pd.DataFrame().to_csv(results / "selection_ranking_before_validation.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V253–V260 — crypto low-risk factor\n\n"
        "Status: `data_access_insufficient`. P&L и selection не запускались.\n"
    )
    write_manifest(root)


def run(root: Path, cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=INITIAL_EQUITY,
        max_gross=TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    gate = data_gate(klines, records)
    write_json(results / "coverage_gate.json", gate)
    if not gate["passed"]:
        failure_outputs(root, gate, records)
        print(json.dumps(clean({"status": "data_access_insufficient", "coverage": gate}), indent=2))
        return 0

    market = Market(klines, funding)
    score_cache: dict[tuple[str, int], pd.DataFrame] = {}
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame] = {}
    weights_cache: dict[str, pd.DataFrame] = {}
    ranking_rows: list[dict[str, Any]] = []
    for number, policy in enumerate(POLICIES, start=1):
        weights = build_weights(market, policy, score_cache, raw_cache)
        weights_cache[policy.name] = weights
        account, diagnostics = simulate(
            market,
            weights,
            START,
            DEVELOPMENT_END_EXCLUSIVE,
            AUDITS[0],
        )
        values = account_metrics(account)
        years = yearly_returns(account, "return")
        all_years_positive = bool(
            not years.empty and (pd.to_numeric(years["return"], errors="coerce") > 0.0).all()
        )
        promotable = policy.family in PROMOTABLE_FAMILIES
        eligible = bool(
            promotable
            and values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and diagnostics["rebalance_events"] >= DEVELOPMENT_GATES["rebalance_events_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and values["max_gross"] <= DEVELOPMENT_GATES["max_realized_gross"]
            and all_years_positive
            and diagnostics["long_leg_pnl"] > 0.0
            and diagnostics["short_leg_pnl"] > 0.0
            and diagnostics["symbol_count_traded"] >= DEVELOPMENT_GATES["symbols_traded_min"]
            and diagnostics["top_positive_asset_pnl_share"]
            <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"]
        )
        score = (
            float(values["cagr"])
            + 0.06 * float(values["sharpe"])
            + 0.12 * float(values["max_drawdown"])
            - 0.0005 * float(values["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "promotable_family": promotable,
                "eligible_development": eligible,
                "all_development_years_positive": all_years_positive,
                "long_leg_pnl": diagnostics["long_leg_pnl"],
                "short_leg_pnl": diagnostics["short_leg_pnl"],
                "symbol_count_traded": diagnostics["symbol_count_traded"],
                "symbols_traded": "+".join(diagnostics["symbols_traded"]),
                "top_positive_asset_pnl_share": diagnostics["top_positive_asset_pnl_share"],
                "asset_pnl_json": json.dumps(clean(diagnostics["asset_pnl"]), sort_keys=True),
                "score": score,
                **{f"development_{key}": value for key, value in values.items()},
            }
        )
        if number % 24 == 0:
            print(f"processed {number}/{len(POLICIES)} policies", flush=True)

    ranking = pd.DataFrame(ranking_rows).sort_values(
        ["eligible_development", "promotable_family", "score"],
        ascending=[False, False, False],
    )
    ranking.to_csv(results / "selection_ranking_before_validation.csv", index=False)
    eligible = ranking[ranking.eligible_development.astype(bool)]
    selected_name = str(eligible.iloc[0].policy) if not eligible.empty else None
    selected_policy = next((policy for policy in POLICIES if policy.name == selected_name), None)
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "promotable_policy_count": sum(policy.family in PROMOTABLE_FAMILIES for policy in POLICIES),
        "eligible_policy_count": int(len(eligible)),
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "coverage_gate": gate,
        "ranking_sha256": hashlib.sha256(ranking.to_csv(index=False).encode("utf-8")).hexdigest(),
        "design_sha256": sha256_file(root / "V253_V260_DESIGN.json"),
        "selected": policy_dict(selected_policy) if selected_policy is not None else None,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_validation.json", proof)

    if selected_policy is None:
        decision = {
            "candidate": CANDIDATE,
            "status": "rejected_before_validation",
            "eligible_policy_count": 0,
            "standalone_selection_passed": False,
            "integration_permitted": False,
            "promoted_candidates": [],
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        summary = {
            **decision,
            "selection": proof,
            "coverage_gate": gate,
            "development_diagnostics": ranking.head(12).to_dict(orient="records"),
            "limitations": [
                "Public daily archives are not executable bid/ask or queue observations.",
                "Fixed universe includes delisted names; forced exits carry a 100 bps penalty.",
                "Program-level holdout is not pristine.",
            ],
            "data_manifest_sha256": canonical_hash(records),
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V253–V260 — crypto low-risk factor\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible promotable policies: `0/{proof['promotable_policy_count']}`. "
            "2024–2026 не открывались.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    selected_weights = weights_cache[selected_policy.name]
    selected_weights.to_csv(results / "frozen_weights.csv")
    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    audit_diagnostics: dict[str, dict[str, Any]] = {}
    for audit in AUDITS:
        account, diagnostics = simulate(market, selected_weights, START, END_EXCLUSIVE, audit)
        audit_accounts[audit.name] = account
        audit_diagnostics[audit.name] = diagnostics
        full = account_metrics(account)
        development = account_metrics(slice_account(account, START, DEVELOPMENT_END_EXCLUSIVE))
        validation = account_metrics(
            slice_account(account, VALIDATION_START, VALIDATION_END_EXCLUSIVE)
        )
        holdout = account_metrics(slice_account(account, HOLDOUT_START, HOLDOUT_END_EXCLUSIVE))
        final = account_metrics(slice_account(account, FINAL_START, END_EXCLUSIVE))
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_cagr": development["cagr"],
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
                "long_leg_pnl": diagnostics["long_leg_pnl"],
                "short_leg_pnl": diagnostics["short_leg_pnl"],
                "symbol_count_traded": diagnostics["symbol_count_traded"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)

    base_account = audit_accounts["base"]
    full = account_metrics(base_account)
    development = account_metrics(slice_account(base_account, START, DEVELOPMENT_END_EXCLUSIVE))
    validation = account_metrics(
        slice_account(base_account, VALIDATION_START, VALIDATION_END_EXCLUSIVE)
    )
    holdout = account_metrics(slice_account(base_account, HOLDOUT_START, HOLDOUT_END_EXCLUSIVE))
    final = account_metrics(slice_account(base_account, FINAL_START, END_EXCLUSIVE))
    yearly = yearly_returns(base_account, "V253_low_risk")
    yearly.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    severe = account_metrics(audit_accounts["severe"])
    delayed = account_metrics(audit_accounts["delay_1d"])
    worst_year = float(pd.to_numeric(yearly.V253_low_risk).min()) if not yearly.empty else -1.0
    checks = {
        "eligible_development": True,
        "validation_return_positive": validation["total_return"] > 0.0,
        "holdout_return_positive": holdout["total_return"] > 0.0,
        "final_return_positive": final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe["cagr"] > 0.0,
        "latency_full_cagr_positive": delayed["cagr"] > 0.0,
        "worst_calendar_year": worst_year >= POST_SELECTION_GATES["worst_calendar_year_min"],
        "forced_exit_count": audit_diagnostics["base"]["forced_exit_count"]
        <= POST_SELECTION_GATES["forced_exit_count_max"],
        "data_coverage": gate["passed"],
    }
    standalone_passed = all(checks.values())
    status = (
        "frozen_historical_candidate_needs_forward"
        if standalone_passed
        else "rejected_after_validation"
    )
    decision = {
        "candidate": CANDIDATE,
        "status": status,
        "eligible_policy_count": int(len(eligible)),
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": False,
        "promoted_candidates": [CANDIDATE] if standalone_passed else [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": gate,
        "checks": checks,
        "candidate_full": full,
        "candidate_development": development,
        "candidate_validation_2024": validation,
        "candidate_holdout_2025": holdout,
        "candidate_final_2026h1": final,
        "worst_year": worst_year,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "base_diagnostics": audit_diagnostics["base"],
        "limitations": [
            "Public daily archives are not executable bid/ask or queue observations.",
            "Fixed universe includes delisted names; forced exits carry a 100 bps penalty.",
            "Program-level holdout is not pristine.",
        ],
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V253–V260 — crypto low-risk factor\n\n"
        f"Status: `{status}`.\n\n"
        f"Selected: `{selected_policy.name}`. Standalone pass: `{standalone_passed}`. "
        "Integration remains disabled.\n"
    )
    write_manifest(root)
    print(json.dumps(clean(summary), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v9"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
