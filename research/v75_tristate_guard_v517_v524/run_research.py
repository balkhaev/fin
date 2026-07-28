#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROGRAM = "V517_V524_V75_TRISTATE_DRAWDOWN_GUARD"
INITIAL_EQUITY = 10_000.0
START = pd.Timestamp("2021-01-01", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
EXPECTED_V75_SHA256 = "f9d543ba8ec15c90efa757e64ed772b1a5934e458463124b7df48ddcac96ef01"
V75_ANNUAL_TURNOVER = 10.643693754982161
EXPECTED_V75 = {
    "rows": 2007,
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
PERIODS = {
    "development_2021_2023": (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation_2024": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "holdout_2025": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
    "final_2026h1": (pd.Timestamp("2026-01-01", tz="UTC"), END),
    "full": (START, END),
}


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
    high_leverage: float = 2.075
    base_leverage: float = 0.97
    low_leverage: float = 0.60
    rebalance_days: int = 10
    no_trade_band: float = 0.04
    guard_enter_drawdown: float = -0.245
    guard_exit_drawdown: float = -0.18
    guard_cap: float = 1.00
    guard_minimum_hold_days: int = 7
    guard_enabled: bool = True
    primary: bool = False


PRIMARY = Policy("tristate_h2075_b097_guard245", primary=True)


def policy_book() -> tuple[Policy, ...]:
    policies = [
        PRIMARY,
        replace(PRIMARY, name="neighbor_h2050", high_leverage=2.05, primary=False),
        replace(PRIMARY, name="neighbor_h2100", high_leverage=2.10, primary=False),
        replace(PRIMARY, name="neighbor_base096", base_leverage=0.96, primary=False),
        replace(PRIMARY, name="neighbor_base098", base_leverage=0.98, primary=False),
        replace(PRIMARY, name="neighbor_rebalance9", rebalance_days=9, primary=False),
        replace(PRIMARY, name="neighbor_rebalance11", rebalance_days=11, primary=False),
        replace(PRIMARY, name="neighbor_guard240", guard_enter_drawdown=-0.24, primary=False),
        replace(PRIMARY, name="neighbor_guard250", guard_enter_drawdown=-0.25, primary=False),
        replace(PRIMARY, name="neighbor_recovery170", guard_exit_drawdown=-0.17, primary=False),
        replace(PRIMARY, name="neighbor_recovery190", guard_exit_drawdown=-0.19, primary=False),
        replace(PRIMARY, name="no_guard_control", guard_enabled=False, primary=False),
        Policy(
            "constant_175_control",
            high_leverage=1.75,
            base_leverage=1.75,
            low_leverage=1.75,
            rebalance_days=14,
            guard_enabled=False,
        ),
    ]
    if len({policy.name for policy in policies}) != len(policies):
        raise AssertionError("duplicate policy names")
    return tuple(policies)


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(item) for item in value]
    if isinstance(value, (np.integer,)):
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
        json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0 / 365.0
    return max(((index[-1] - index[0]).days + 1) / 365.0, 1.0 / 365.0)


def yearly_returns(returns: pd.Series, name: str = "return") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"year": int(year), name: float((1.0 + group).prod() - 1.0)}
            for year, group in returns.groupby(returns.index.year)
        ]
    )


def metrics(returns: pd.Series, account: pd.DataFrame | None = None) -> dict[str, Any]:
    returns = pd.to_numeric(returns, errors="coerce").fillna(0.0).astype(float)
    equity = INITIAL_EQUITY * (1.0 + returns).cumprod()
    years = elapsed_years(returns.index)
    drawdown = equity / equity.cummax() - 1.0
    rolling = (1.0 + returns).rolling(365, min_periods=180).apply(np.prod, raw=True) - 1.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    downside = returns.where(returns < 0.0, 0.0)
    downside_std = float(downside.std(ddof=1)) if len(returns) > 1 else 0.0
    total_return = float(equity.iloc[-1] / INITIAL_EQUITY - 1.0)
    cagr = float((equity.iloc[-1] / INITIAL_EQUITY) ** (1.0 / years) - 1.0)
    output: dict[str, Any] = {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": float(returns.mean() / std * np.sqrt(365.0)) if std > 0 else 0.0,
        "sortino": float(returns.mean() / downside_std * np.sqrt(365.0)) if downside_std > 0 else 0.0,
        "max_drawdown": float(drawdown.min()),
        "calmar": cagr / abs(float(drawdown.min())) if float(drawdown.min()) < 0 else 0.0,
        "worst_rolling_365": float(rolling.min()) if rolling.notna().any() else total_return,
        "final_equity": float(equity.iloc[-1]),
    }
    if account is not None:
        output.update(
            {
                "annual_meta_turnover": float(account["meta_turnover"].sum() / years),
                "average_target_leverage": float(account["desired_leverage"].mean()),
                "maximum_target_leverage": float(account["desired_leverage"].max()),
                "average_close_gross": float(account["close_gross"].mean()),
                "maximum_close_gross": float(account["close_gross"].max()),
                "transfer_cost": float(account["transfer_cost"].sum()),
                "financing_cost": float(account["financing_cost"].sum()),
                "extra_underlying_cost": float(account["extra_underlying_cost"].sum()),
                "risk_reductions": int(account["risk_reduction"].sum()),
                "scheduled_rebalances": int(account["scheduled_rebalance"].sum()),
                "guard_day_share": float(account["guard_active"].mean()),
                "high_state_share": float((account["market_state"] == 1).mean()),
                "base_state_share": float((account["market_state"] == 0).mean()),
                "low_state_share": float((account["market_state"] == -1).mean()),
            }
        )
    return output


def load_v75(path: Path) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    actual_sha = sha256_file(path)
    if actual_sha != EXPECTED_V75_SHA256:
        raise RuntimeError(f"unexpected V75 stream SHA-256: {actual_sha}")
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    equity = pd.to_numeric(frame["equity"], errors="coerce")
    if len(frame) != EXPECTED_V75["rows"] or frame.index.duplicated().any():
        raise RuntimeError("unexpected V75 index")
    if not np.isfinite(equity).all() or (equity <= 0.0).any():
        raise ValueError("invalid V75 equity")
    returns = equity.pct_change(fill_method=None)
    returns.iloc[0] = equity.iloc[0] / INITIAL_EQUITY - 1.0
    observed = metrics(returns)
    annual = yearly_returns(returns).set_index("year")["return"].to_dict()
    checks = {
        "sha256": actual_sha == EXPECTED_V75_SHA256,
        "rows": len(frame) == EXPECTED_V75["rows"],
        "total_return": abs(observed["total_return"] - EXPECTED_V75["total_return"]) <= 1e-10,
        "cagr": abs(observed["cagr"] - EXPECTED_V75["cagr"]) <= 5e-4,
        "max_drawdown": abs(observed["max_drawdown"] - EXPECTED_V75["max_drawdown"]) <= 1e-10,
        "sharpe": abs(observed["sharpe"] - EXPECTED_V75["sharpe"]) <= 5e-4,
        "annual_returns": all(
            abs(float(annual.get(year, np.nan)) - expected) <= 1e-10
            for year, expected in EXPECTED_V75["annual_returns"].items()
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"V75 equivalence failure: {checks}")
    return returns.rename("v75_return"), equity.rename("v75_equity"), {
        "file_sha256": actual_sha,
        "checks": checks,
        "metrics": observed,
        "annual_returns": annual,
        "economic_equivalence_passed": True,
    }


def lagged_momentum(equity: pd.Series, lookback: int) -> pd.Series:
    return (equity.shift(1) / equity.shift(lookback + 1) - 1.0).rename(f"momentum_{lookback}")


def market_state(equity: pd.Series) -> pd.DataFrame:
    fast = lagged_momentum(equity, 20)
    medium = lagged_momentum(equity, 60)
    state = 0
    age = 14
    high_count = 0
    low_count = 0
    rows: list[dict[str, Any]] = []
    for timestamp, fast_value, medium_value in zip(equity.index, fast, medium, strict=True):
        high_raw = (
            np.isfinite(fast_value)
            and np.isfinite(medium_value)
            and fast_value > 0.05
            and medium_value > -0.04
        )
        low_raw = (
            np.isfinite(fast_value)
            and np.isfinite(medium_value)
            and fast_value < -0.05
            and medium_value < -0.10
        )
        high_count = high_count + 1 if high_raw else 0
        low_count = low_count + 1 if low_raw else 0
        high_condition = high_count >= 1
        low_condition = low_count >= 3
        switched = 0
        if age >= 14:
            if state == 1:
                if low_condition:
                    state, age, switched = -1, 0, 1
                elif (
                    (np.isfinite(fast_value) and fast_value < -0.01)
                    or (np.isfinite(medium_value) and medium_value < 0.0)
                ):
                    state, age, switched = 0, 0, 1
                else:
                    age += 1
            elif state == -1:
                if high_condition:
                    state, age, switched = 1, 0, 1
                elif np.isfinite(fast_value) and fast_value > 0.01:
                    state, age, switched = 0, 0, 1
                else:
                    age += 1
            else:
                if high_condition:
                    state, age, switched = 1, 0, 1
                elif low_condition:
                    state, age, switched = -1, 0, 1
                else:
                    age += 1
        else:
            age += 1
        rows.append(
            {
                "open_time": timestamp,
                "momentum20": fast_value,
                "momentum60": medium_value,
                "market_state": state,
                "state_age_days": age,
                "state_switched": switched,
            }
        )
    return pd.DataFrame(rows).set_index("open_time")


def simulate(
    returns: pd.Series,
    source_equity: pd.Series,
    policy: Policy,
    audit: Audit,
) -> pd.DataFrame:
    state_frame = market_state(source_equity)
    if audit.signal_delay_days:
        state_frame = state_frame.shift(audit.signal_delay_days)
        state_frame["market_state"] = state_frame["market_state"].fillna(0)
        state_frame["state_age_days"] = state_frame["state_age_days"].fillna(0)
        state_frame["state_switched"] = state_frame["state_switched"].fillna(0)
    state_frame = state_frame.reindex(returns.index)

    holdings = 0.0
    cash = INITIAL_EQUITY
    equity = INITIAL_EQUITY
    high_water = INITIAL_EQUITY
    previous_target = 0.0
    guard_active = False
    guard_age = policy.guard_minimum_hold_days
    records: list[dict[str, Any]] = []

    for number, timestamp in enumerate(returns.index):
        previous_equity = equity
        drawdown_open = equity / max(high_water, 1e-12) - 1.0
        if policy.guard_enabled:
            if guard_age >= policy.guard_minimum_hold_days:
                if not guard_active and drawdown_open <= policy.guard_enter_drawdown:
                    guard_active = True
                    guard_age = 0
                elif guard_active and drawdown_open >= policy.guard_exit_drawdown:
                    guard_active = False
                    guard_age = 0
                else:
                    guard_age += 1
            else:
                guard_age += 1
        else:
            guard_active = False
            guard_age += 1

        state = int(state_frame.at[timestamp, "market_state"])
        raw_target = (
            policy.high_leverage
            if state == 1
            else policy.low_leverage
            if state == -1
            else policy.base_leverage
        )
        target = min(raw_target, policy.guard_cap) if guard_active else raw_target
        current_weight = holdings / max(equity, 1e-12)
        risk_reduction = target < abs(current_weight) - policy.no_trade_band
        scheduled = number == 0 or number % policy.rebalance_days == 0
        target_changed = abs(target - previous_target) >= policy.no_trade_band
        rebalance = number == 0 or risk_reduction or (scheduled and target_changed)
        meta_turnover = 0.0
        transfer_cost = 0.0
        if rebalance:
            meta_turnover = abs(target - current_weight)
            transfer_cost = equity * meta_turnover * audit.transfer_cost_rate
            after_cost = max(equity - transfer_cost, 1e-12)
            holdings = target * after_cost
            cash = after_cost - holdings
            previous_target = target
        else:
            cash = equity - holdings

        financing_cost = max(-cash, 0.0) * audit.financing_rate / 365.0
        cash -= financing_cost
        open_leverage = abs(holdings) / max(equity, 1e-12)
        extra_underlying_cost = (
            equity
            * open_leverage
            * V75_ANNUAL_TURNOVER
            * audit.extra_underlying_cost_rate
            / 365.0
        )
        cash -= extra_underlying_cost
        holdings *= 1.0 + float(returns.at[timestamp])
        equity = float(cash + holdings)
        if not np.isfinite(equity) or equity <= 0.0:
            raise RuntimeError(f"non-positive equity at {timestamp}: {equity}")
        high_water = max(high_water, equity)
        records.append(
            {
                "net_return": equity / previous_equity - 1.0,
                "equity": equity,
                "desired_leverage": target,
                "raw_target_leverage": raw_target,
                "close_gross": abs(holdings) / equity,
                "meta_turnover": meta_turnover,
                "transfer_cost": transfer_cost,
                "financing_cost": financing_cost,
                "extra_underlying_cost": extra_underlying_cost,
                "risk_reduction": int(risk_reduction and rebalance),
                "scheduled_rebalance": int(scheduled and rebalance and not risk_reduction),
                "guard_active": bool(guard_active),
                "guard_age_days": int(guard_age),
                "drawdown_open": drawdown_open,
                "market_state": state,
                "momentum20": state_frame.at[timestamp, "momentum20"],
                "momentum60": state_frame.at[timestamp, "momentum60"],
            }
        )
    return pd.DataFrame(records, index=returns.index)


def period_metrics(account: pd.DataFrame, period: str) -> dict[str, Any]:
    start, end = PERIODS[period]
    part = account.loc[(account.index >= start) & (account.index < end)]
    return metrics(part["net_return"], part)


def acceptance_checks(accounts: dict[str, pd.DataFrame]) -> dict[str, bool]:
    base = period_metrics(accounts["base"], "full")
    severe = period_metrics(accounts["severe"], "full")
    extreme = period_metrics(accounts["extreme"], "full")
    delay = period_metrics(accounts["delay_1d"], "full")
    annual = yearly_returns(accounts["base"]["net_return"]).set_index("year")["return"].to_dict()
    return {
        "full_cagr_ge_50pct": base["cagr"] >= 0.50,
        "full_sharpe_ge_1_45": base["sharpe"] >= 1.45,
        "full_max_dd_ge_minus25pct": base["max_drawdown"] >= -0.25,
        "full_worst_rolling_365_ge_minus22pct": base["worst_rolling_365"] >= -0.22,
        "all_calendar_years_positive": all(value > 0.0 for value in annual.values()),
        "average_target_leverage_le_1_35": base["average_target_leverage"] <= 1.35,
        "maximum_target_leverage_le_2_10": base["maximum_target_leverage"] <= 2.1000001,
        "annual_meta_turnover_le_6x": base["annual_meta_turnover"] <= 6.0,
        "severe_full_cagr_ge_40pct": severe["cagr"] >= 0.40,
        "severe_full_dd_ge_minus30pct": severe["max_drawdown"] >= -0.30,
        "extreme_full_cagr_ge_25pct": extreme["cagr"] >= 0.25,
        "extreme_full_dd_ge_minus40pct": extreme["max_drawdown"] >= -0.40,
        "delay_full_cagr_ge_45pct": delay["cagr"] >= 0.45,
        "delay_full_dd_ge_minus25pct": delay["max_drawdown"] >= -0.25,
    }


def self_test() -> None:
    index = pd.date_range("2021-01-01", periods=700, freq="1D", tz="UTC")
    rng = np.random.default_rng(517)
    returns = pd.Series(rng.normal(0.0008, 0.018, len(index)), index=index)
    equity = INITIAL_EQUITY * (1.0 + returns).cumprod()
    state = market_state(equity)
    assert state["momentum20"].iloc[:21].isna().all()
    assert state["momentum60"].iloc[:61].isna().all()
    assert np.isfinite(state["momentum20"].iloc[21:]).all()
    assert np.isfinite(state["momentum60"].iloc[61:]).all()
    account = simulate(returns, equity, PRIMARY, AUDITS[0])
    numeric = account.select_dtypes(include=[np.number]).drop(columns=["momentum20", "momentum60"])
    assert np.isfinite(numeric.to_numpy()).all()
    assert float(account["desired_leverage"].max()) <= PRIMARY.high_leverage
    changed = equity.copy()
    changed.iloc[-1] *= 10.0
    changed_state = market_state(changed)
    pd.testing.assert_frame_equal(state.iloc[:-1], changed_state.iloc[:-1])
    changed_account = simulate(returns, changed, PRIMARY, AUDITS[0])
    pd.testing.assert_series_equal(
        account["desired_leverage"].iloc[:-1],
        changed_account["desired_leverage"].iloc[:-1],
        check_names=False,
    )
    stressed = returns.copy()
    stressed.iloc[300:305] = -0.12
    stressed_equity = INITIAL_EQUITY * (1.0 + stressed).cumprod()
    guarded = simulate(stressed, stressed_equity, PRIMARY, AUDITS[0])
    assert guarded["guard_active"].any()
    assert float(guarded.loc[guarded["guard_active"], "desired_leverage"].max()) <= PRIMARY.guard_cap
    print("V517-V524 tri-state guard self-test passed")


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


def run(v75_path: Path, design_path: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    returns, source_equity, equivalence = load_v75(v75_path)
    write_json(output / "V75_ECONOMIC_EQUIVALENCE.json", equivalence)

    policy_rows: list[dict[str, Any]] = []
    accounts_by_policy: dict[str, dict[str, pd.DataFrame]] = {}
    for policy in policy_book():
        audit_accounts: dict[str, pd.DataFrame] = {}
        row: dict[str, Any] = {"policy": policy.name, **asdict(policy)}
        for audit in AUDITS:
            account = simulate(returns, source_equity, policy, audit)
            audit_accounts[audit.name] = account
            full = period_metrics(account, "full")
            row.update({f"{audit.name}_{key}": value for key, value in full.items()})
        checks = acceptance_checks(audit_accounts)
        row.update({f"gate_{key}": value for key, value in checks.items()})
        row["all_gates_passed"] = bool(all(checks.values()))
        accounts_by_policy[policy.name] = audit_accounts
        policy_rows.append(row)
        print(
            f"{policy.name} CAGR={row['base_cagr']:.4f} "
            f"DD={row['base_max_drawdown']:.4f} "
            f"severe={row['severe_cagr']:.4f} pass={row['all_gates_passed']}",
            flush=True,
        )

    table = pd.DataFrame(policy_rows)
    table.to_csv(output / "policy_audit_metrics.csv", index=False)
    primary_accounts = accounts_by_policy[PRIMARY.name]
    for audit_name, account in primary_accounts.items():
        account.to_csv(output / f"equity_primary_{audit_name}.csv", index_label="open_time")

    period_rows: list[dict[str, Any]] = []
    for audit_name, account in primary_accounts.items():
        for period in PERIODS:
            period_rows.append(
                {"audit": audit_name, "period": period, **period_metrics(account, period)}
            )
    pd.DataFrame(period_rows).to_csv(output / "primary_period_metrics.csv", index=False)
    annual = yearly_returns(primary_accounts["base"]["net_return"])
    annual.to_csv(output / "ANNUAL_RETURNS.csv", index=False)

    primary_checks = acceptance_checks(primary_accounts)
    all_passed = bool(all(primary_checks.values()))
    neighborhood = table[~table["primary"] & ~table["policy"].str.endswith("control")]
    neighbor_pass_count = int(neighborhood["all_gates_passed"].sum())
    primary_full = period_metrics(primary_accounts["base"], "full")
    severe_full = period_metrics(primary_accounts["severe"], "full")
    extreme_full = period_metrics(primary_accounts["extreme"], "full")
    delay_full = period_metrics(primary_accounts["delay_1d"], "full")

    status = (
        "historical_target_met_non_pristine_no_capital_authority"
        if all_passed
        else "historical_target_not_met"
    )
    decision = {
        "program": PROGRAM,
        "status": status,
        "selection_performed": False,
        "parameters_informed_by_known_history": True,
        "program_level_holdout_pristine": False,
        "historical_50pct_target_met": bool(primary_checks["full_cagr_ge_50pct"]),
        "modeled_robustness_gates_passed": all_passed,
        "primary_policy": PRIMARY.name,
        "neighbor_policy_count": int(len(neighborhood)),
        "neighbor_all_gate_pass_count": neighbor_pass_count,
        "promotion_permitted": False,
        "integration_permitted": False,
        "capital_change_authorized": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "primary_policy_spec": asdict(PRIMARY),
        "design_sha256": sha256_file(design_path),
        "v75_equivalence": equivalence,
        "acceptance_checks": primary_checks,
        "base_full": primary_full,
        "severe_full": severe_full,
        "extreme_full": extreme_full,
        "delay_full": delay_full,
        "annual_returns": annual.to_dict(orient="records"),
        "evidence_boundary": {
            "account_level_only": True,
            "position_level_margin_replay_complete": False,
            "forward_period_complete": False,
            "parameters_informed_by_known_history": True,
        },
    }
    write_json(output / "FROZEN_DECISION.json", decision)
    write_json(output / "summary.json", summary)

    report = [
        "# V517–V524 — V75 tri-state drawdown guard",
        "",
        f"Status: `{status}`.",
        "",
        "## Primary historical model",
        "",
        f"- Full CAGR: {100 * primary_full['cagr']:.2f}%",
        f"- Full Sharpe: {primary_full['sharpe']:.3f}",
        f"- Full Max DD: {100 * primary_full['max_drawdown']:.2f}%",
        f"- Average target leverage: {primary_full['average_target_leverage']:.3f}x",
        f"- Maximum target leverage: {primary_full['maximum_target_leverage']:.3f}x",
        f"- Severe CAGR / DD: {100 * severe_full['cagr']:.2f}% / {100 * severe_full['max_drawdown']:.2f}%",
        f"- Extreme CAGR / DD: {100 * extreme_full['cagr']:.2f}% / {100 * extreme_full['max_drawdown']:.2f}%",
        f"- 1-day delay CAGR / DD: {100 * delay_full['cagr']:.2f}% / {100 * delay_full['max_drawdown']:.2f}%",
        "",
        "## Calendar returns",
        "",
    ]
    for item in annual.to_dict(orient="records"):
        report.append(f"- {int(item['year'])}: {100 * float(item['return']):+.2f}%")
    report += [
        "",
        "## Evidence boundary",
        "",
        "The 50% historical target is non-pristine: parameters were informed by already known history.",
        "The model is an account-level overlay and does not replay position-level margin or liquidation.",
        "No capital, live execution or real leverage is authorized.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    write_manifest(output)
    print(json.dumps(clean(summary), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v75", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if None in (args.v75, args.design, args.output):
        raise SystemExit("--v75, --design and --output are required")
    return run(args.v75, args.design, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
