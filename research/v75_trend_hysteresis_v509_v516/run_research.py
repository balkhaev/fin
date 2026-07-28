#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROGRAM = "V509_V516_V75_TREND_HYSTERESIS_ACCELERATOR"
INITIAL_EQUITY = 10_000.0
START = pd.Timestamp("2021-01-01", tz="UTC")
DEVELOPMENT_END = pd.Timestamp("2024-01-01", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
PERIODS = {
    "development": (START, DEVELOPMENT_END),
    "validation_2024": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "holdout_2025": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
    "final_2026h1": (pd.Timestamp("2026-01-01", tz="UTC"), END),
    "full": (START, END),
}
EXPECTED_V75_SHA256 = "f9d543ba8ec15c90efa757e64ed772b1a5934e458463124b7df48ddcac96ef01"
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
V75_ANNUAL_TURNOVER = 10.643693754982161


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
    entry_return: float
    exit_return: float
    minimum_hold_days: int
    high_leverage: float
    low_leverage: float
    rebalance_days: int
    no_trade_band: float = 0.04
    inverted: bool = False
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
        json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


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
    total = float(equity.iloc[-1] / INITIAL_EQUITY - 1.0)
    cagr = float((equity.iloc[-1] / INITIAL_EQUITY) ** (1.0 / years) - 1.0)
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    downside = returns.where(returns < 0.0, 0.0)
    downside_std = float(downside.std(ddof=1)) if len(returns) > 1 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    rolling = (1.0 + returns).rolling(365, min_periods=180).apply(np.prod, raw=True) - 1.0
    output: dict[str, Any] = {
        "total_return": total,
        "cagr": cagr,
        "sharpe": float(returns.mean() / std * np.sqrt(365.0)) if std > 0 else 0.0,
        "sortino": float(returns.mean() / downside_std * np.sqrt(365.0)) if downside_std > 0 else 0.0,
        "max_drawdown": float(drawdown.min()),
        "calmar": cagr / abs(float(drawdown.min())) if float(drawdown.min()) < 0 else 0.0,
        "worst_rolling_365": float(rolling.min()) if rolling.notna().any() else total,
        "final_equity": float(equity.iloc[-1]),
    }
    if account is not None:
        output.update(
            {
                "annual_meta_turnover": float(account["meta_turnover"].sum() / years),
                "average_leverage": float(account["desired_leverage"].mean()),
                "max_leverage": float(account["desired_leverage"].max()),
                "average_close_gross": float(account["close_gross"].mean()),
                "max_close_gross": float(account["close_gross"].max()),
                "transfer_cost": float(account["transfer_cost"].sum()),
                "financing_cost": float(account["financing_cost"].sum()),
                "extra_underlying_cost": float(account["extra_underlying_cost"].sum()),
                "risk_reductions": int(account["risk_reduction"].sum()),
                "scheduled_rebalances": int(account["scheduled_rebalance"].sum()),
                "signal_switches": int(account["signal_switched"].sum()),
                "high_state_share": float(account["high_state"].mean()),
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


def policy_book() -> tuple[Policy, ...]:
    policies = (
        Policy("h14_e06_xm02_hi195_lo06_r7", 0.06, -0.02, 14, 1.95, 0.60, 7),
        Policy("h21_e06_xm02_hi195_lo06_r7", 0.06, -0.02, 21, 1.95, 0.60, 7),
        Policy("h14_e06_xm02_hi195_lo08_r7", 0.06, -0.02, 14, 1.95, 0.80, 7),
        Policy("h21_e06_xm02_hi195_lo08_r7", 0.06, -0.02, 21, 1.95, 0.80, 7),
        Policy("h14_e06_xm02_hi185_lo10_r7", 0.06, -0.02, 14, 1.85, 1.00, 7),
        Policy("h21_e06_xm02_hi185_lo10_r7", 0.06, -0.02, 21, 1.85, 1.00, 7),
        Policy("h14_e08_xm02_hi195_lo10_r7", 0.08, -0.02, 14, 1.95, 1.00, 7),
        Policy("h14_e06_xm02_hi195_lo08_r14", 0.06, -0.02, 14, 1.95, 0.80, 14),
        Policy("h21_e06_xm02_hi195_lo08_r14", 0.06, -0.02, 21, 1.95, 0.80, 14),
        Policy("constant_l175_control", 1e9, -1e9, 1, 1.75, 1.75, 14, promotable=False),
        Policy("naive_r20_control", 0.0, 0.0, 0, 1.85, 0.60, 3, promotable=False),
        Policy("inverted_h14_control", 0.06, -0.02, 14, 1.95, 0.60, 7, inverted=True, promotable=False),
    )
    if len({p.name for p in policies}) != len(policies):
        raise AssertionError("duplicate policy names")
    return policies


def momentum20(equity: pd.Series) -> pd.Series:
    # At date t use only the V75 equity completed through t-1.
    return (equity.shift(1) / equity.shift(21) - 1.0).rename("momentum20")


def hysteresis_state(score: pd.Series, policy: Policy) -> pd.DataFrame:
    values = pd.to_numeric(score, errors="coerce").to_numpy(float)
    high = False
    age = policy.minimum_hold_days
    states: list[bool] = []
    switches: list[int] = []
    ages: list[int] = []
    for value in values:
        switched = 0
        if np.isfinite(value):
            if high:
                if age >= policy.minimum_hold_days and value < policy.exit_return:
                    high = False
                    age = 0
                    switched = 1
                else:
                    age += 1
            else:
                if age >= policy.minimum_hold_days and value > policy.entry_return:
                    high = True
                    age = 0
                    switched = 1
                else:
                    age += 1
        else:
            age += 1
        states.append(high)
        switches.append(switched)
        ages.append(age)
    if policy.inverted:
        states = [not item for item in states]
    return pd.DataFrame(
        {"high_state": states, "signal_switched": switches, "state_age_days": ages},
        index=score.index,
    )


def desired_leverage(equity: pd.Series, policy: Policy, delay: int = 0) -> pd.DataFrame:
    score = momentum20(equity)
    if policy.name == "constant_l175_control":
        state = pd.DataFrame(
            {"high_state": True, "signal_switched": 0, "state_age_days": np.arange(len(score))},
            index=score.index,
        )
    else:
        state = hysteresis_state(score, policy)
    desired = pd.Series(
        np.where(state["high_state"], policy.high_leverage, policy.low_leverage),
        index=score.index,
        dtype=float,
        name="desired_leverage",
    )
    if delay:
        desired = desired.shift(delay).fillna(policy.low_leverage)
        state = state.shift(delay).fillna(
            {"high_state": False, "signal_switched": 0, "state_age_days": 0}
        )
    return pd.concat([score, desired, state], axis=1)


def simulate(
    returns: pd.Series,
    equity_source: pd.Series,
    policy: Policy,
    audit: Audit,
    start: pd.Timestamp = START,
    end: pd.Timestamp = END,
) -> pd.DataFrame:
    index = returns.index[(returns.index >= start) & (returns.index < end)]
    signal = desired_leverage(equity_source, policy, audit.signal_delay_days).reindex(index)
    r = returns.reindex(index).to_numpy(float)
    desired = signal["desired_leverage"].to_numpy(float)
    high_state = signal["high_state"].astype(bool).to_numpy()
    switched = signal["signal_switched"].fillna(0).to_numpy(int)
    state_age = signal["state_age_days"].fillna(0).to_numpy(int)
    score = signal["momentum20"].to_numpy(float)

    holdings = 0.0
    cash = INITIAL_EQUITY
    equity = INITIAL_EQUITY
    previous_target = 0.0
    records: list[dict[str, Any]] = []
    for i, timestamp in enumerate(index):
        previous_equity = equity
        current_weight = holdings / max(equity, 1e-12)
        target = float(desired[i])
        l1_change = abs(target - current_weight)
        risk_reduction = target < abs(current_weight) - 0.04
        scheduled = i == 0 or i % policy.rebalance_days == 0
        target_changed = abs(target - previous_target) >= policy.no_trade_band
        rebalance = i == 0 or risk_reduction or (scheduled and target_changed)
        meta_turnover = 0.0
        transfer_cost = 0.0
        if rebalance:
            meta_turnover = l1_change
            transfer_cost = equity * meta_turnover * audit.transfer_cost_rate
            after = max(equity - transfer_cost, 1e-12)
            holdings = target * after
            cash = after - holdings
            previous_target = target
        else:
            cash = equity - holdings

        financing_cost = max(-cash, 0.0) * audit.financing_rate / 365.0
        cash -= financing_cost
        close_open_leverage = abs(holdings) / max(equity, 1e-12)
        extra_underlying_cost = (
            equity
            * close_open_leverage
            * V75_ANNUAL_TURNOVER
            * audit.extra_underlying_cost_rate
            / 365.0
        )
        cash -= extra_underlying_cost
        holdings *= 1.0 + r[i]
        equity = float(cash + holdings)
        if not np.isfinite(equity) or equity <= 0.0:
            raise RuntimeError(f"non-positive equity at {timestamp}: {equity}")
        records.append(
            {
                "net_return": equity / previous_equity - 1.0,
                "equity": equity,
                "desired_leverage": target,
                "close_gross": abs(holdings) / equity,
                "cash_weight_close": cash / equity,
                "meta_turnover": meta_turnover,
                "transfer_cost": transfer_cost,
                "financing_cost": financing_cost,
                "extra_underlying_cost": extra_underlying_cost,
                "risk_reduction": int(risk_reduction and rebalance),
                "scheduled_rebalance": int(scheduled and rebalance and not risk_reduction),
                "momentum20": score[i],
                "high_state": bool(high_state[i]),
                "signal_switched": int(switched[i]),
                "state_age_days": int(state_age[i]),
            }
        )
    return pd.DataFrame(records, index=index)


def development_checks(
    base: dict[str, Any],
    severe: dict[str, Any],
    extreme: dict[str, Any],
    delay: dict[str, Any],
    annual: pd.DataFrame,
    policy: Policy,
) -> dict[str, bool]:
    yearly = annual.set_index("year")["return"].to_dict()
    return {
        "promotable": policy.promotable,
        "cagr_ge_50pct": base["cagr"] >= 0.50,
        "sharpe_ge_1_50": base["sharpe"] >= 1.50,
        "max_dd_ge_minus25pct": base["max_drawdown"] >= -0.25,
        "worst_rolling_365_ge_minus22pct": base["worst_rolling_365"] >= -0.22,
        "2021_positive": yearly.get(2021, -1.0) > 0.0,
        "2022_positive": yearly.get(2022, -1.0) > 0.0,
        "2023_positive": yearly.get(2023, -1.0) > 0.0,
        "meta_turnover_le_6x": base["annual_meta_turnover"] <= 6.0,
        "average_leverage_le_1_35": base["average_leverage"] <= 1.35,
        "max_leverage_le_1_95": base["max_leverage"] <= 1.9500001,
        "severe_cagr_ge_40pct": severe["cagr"] >= 0.40,
        "severe_dd_ge_minus28pct": severe["max_drawdown"] >= -0.28,
        "extreme_cagr_ge_30pct": extreme["cagr"] >= 0.30,
        "extreme_dd_ge_minus35pct": extreme["max_drawdown"] >= -0.35,
        "delay_cagr_ge_50pct": delay["cagr"] >= 0.50,
        "delay_dd_ge_minus25pct": delay["max_drawdown"] >= -0.25,
    }


def score_candidate(base: dict[str, Any], severe: dict[str, Any], annual: pd.DataFrame) -> float:
    minimum_year = float(annual["return"].min())
    return float(
        base["cagr"]
        + 0.12 * base["sharpe"]
        - 0.35 * max(0.0, abs(base["max_drawdown"]) - 0.20)
        + 0.08 * minimum_year
        + 0.05 * severe["cagr"]
        - 0.004 * base["annual_meta_turnover"]
    )


def post_oos_checks(audits: dict[str, pd.DataFrame]) -> dict[str, bool]:
    def m(audit: str, period: str) -> dict[str, Any]:
        frame = audits[audit]
        start, end = PERIODS[period]
        part = frame.loc[(frame.index >= start) & (frame.index < end)]
        return metrics(part["net_return"], part)

    base_full = m("base", "full")
    annual = yearly_returns(audits["base"]["net_return"])
    annual_map = annual.set_index("year")["return"].to_dict()
    return {
        "validation_2024_positive": m("base", "validation_2024")["total_return"] > 0.0,
        "holdout_2025_positive": m("base", "holdout_2025")["total_return"] > 0.0,
        "final_2026h1_positive": m("base", "final_2026h1")["total_return"] > 0.0,
        "full_cagr_ge_50pct": base_full["cagr"] >= 0.50,
        "full_sharpe_ge_1_45": base_full["sharpe"] >= 1.45,
        "full_dd_ge_minus25pct": base_full["max_drawdown"] >= -0.25,
        "full_worst_rolling_365_ge_minus22pct": base_full["worst_rolling_365"] >= -0.22,
        "average_leverage_le_1_40": base_full["average_leverage"] <= 1.40,
        "max_leverage_le_1_95": base_full["max_leverage"] <= 1.9500001,
        "severe_full_cagr_ge_40pct": m("severe", "full")["cagr"] >= 0.40,
        "severe_full_dd_ge_minus30pct": m("severe", "full")["max_drawdown"] >= -0.30,
        "extreme_full_cagr_ge_25pct": m("extreme", "full")["cagr"] >= 0.25,
        "extreme_full_dd_ge_minus40pct": m("extreme", "full")["max_drawdown"] >= -0.40,
        "delay_full_cagr_ge_45pct": m("delay_1d", "full")["cagr"] >= 0.45,
        "worst_calendar_year_ge_minus10pct": min(annual_map.values()) >= -0.10,
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
    index = pd.date_range("2021-01-01", periods=600, freq="1D", tz="UTC")
    rng = np.random.default_rng(509)
    returns = pd.Series(rng.normal(0.0007, 0.018, len(index)), index=index)
    equity = INITIAL_EQUITY * (1.0 + returns).cumprod()
    policy = policy_book()[0]
    state = hysteresis_state(momentum20(equity), policy)
    switch_dates = state.index[state["signal_switched"].astype(bool)]
    if len(switch_dates) > 1:
        gaps = np.diff(switch_dates.asi8) / (24 * 3600 * 1e9)
        assert float(gaps.min()) >= policy.minimum_hold_days
    account = simulate(returns, equity, policy, AUDITS[0])
    assert len(account) == len(index)
    numeric = account.select_dtypes(include=[np.number]).drop(columns=["momentum20"])
    assert np.isfinite(numeric.to_numpy()).all()
    assert account["momentum20"].iloc[:21].isna().all()
    assert np.isfinite(account["momentum20"].iloc[21:].to_numpy()).all()
    assert float(account["desired_leverage"].min()) >= policy.low_leverage
    assert float(account["desired_leverage"].max()) <= policy.high_leverage
    changed = equity.copy()
    changed.iloc[-1] *= 10.0
    changed_account = simulate(returns, changed, policy, AUDITS[0])
    pd.testing.assert_series_equal(
        account["desired_leverage"].iloc[:-1],
        changed_account["desired_leverage"].iloc[:-1],
        check_names=False,
    )
    print("V509-V516 trend-hysteresis self-test passed")


def run(v75_path: Path, design_path: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    returns, source_equity, equivalence = load_v75(v75_path)
    write_json(output / "V75_ECONOMIC_EQUIVALENCE.json", equivalence)

    ranking_rows: list[dict[str, Any]] = []
    for number, policy in enumerate(policy_book(), 1):
        audit_metrics: dict[str, dict[str, Any]] = {}
        audit_accounts: dict[str, pd.DataFrame] = {}
        for audit in AUDITS:
            account = simulate(
                returns,
                source_equity,
                policy,
                audit,
                START,
                DEVELOPMENT_END,
            )
            audit_accounts[audit.name] = account
            audit_metrics[audit.name] = metrics(account["net_return"], account)
        annual = yearly_returns(audit_accounts["base"]["net_return"])
        checks = development_checks(
            audit_metrics["base"],
            audit_metrics["severe"],
            audit_metrics["extreme"],
            audit_metrics["delay_1d"],
            annual,
            policy,
        )
        row = {
            "policy": policy.name,
            **asdict(policy),
            "eligible": bool(all(checks.values())),
            "score": score_candidate(audit_metrics["base"], audit_metrics["severe"], annual),
            **{f"base_{key}": value for key, value in audit_metrics["base"].items()},
            **{f"severe_{key}": value for key, value in audit_metrics["severe"].items()},
            **{f"extreme_{key}": value for key, value in audit_metrics["extreme"].items()},
            **{f"delay_{key}": value for key, value in audit_metrics["delay_1d"].items()},
            **{f"gate_{key}": value for key, value in checks.items()},
        }
        ranking_rows.append(row)
        print(
            f"{number}/{len(policy_book())} {policy.name} "
            f"CAGR={audit_metrics['base']['cagr']:.4f} "
            f"DD={audit_metrics['base']['max_drawdown']:.4f} "
            f"eligible={row['eligible']}",
            flush=True,
        )

    ranking = pd.DataFrame(ranking_rows).sort_values(["eligible", "score"], ascending=[False, False])
    ranking.to_csv(output / "development_ranking.csv", index=False)
    eligible = ranking[ranking["eligible"]]
    selected_name = str(eligible.iloc[0]["policy"]) if len(eligible) else None
    proof = {
        "program": PROGRAM,
        "design_sha256": sha256_file(design_path),
        "selection_period": [str(START), str(DEVELOPMENT_END)],
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "signal": "completed V75 equity 20-day return with hysteresis",
        "policy_count": len(policy_book()),
        "eligible_policies": list(eligible["policy"].astype(str)),
        "selected_policy": selected_name,
        "ranking_top": ranking.head(20).to_dict(orient="records"),
        "v75_equivalence": equivalence,
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
        write_json(output / "FROZEN_DECISION.json", decision)
        write_json(output / "summary.json", {**decision, "selection": proof})
        (output / "REPORT_RU.md").write_text(
            "# V509–V516\n\nStatus: `rejected_before_oos`. OOS remained closed.\n",
            encoding="utf-8",
        )
        write_manifest(output)
        return 0

    selected = next(policy for policy in policy_book() if policy.name == selected_name)
    audits: dict[str, pd.DataFrame] = {}
    audit_rows: list[dict[str, Any]] = []
    for audit in AUDITS:
        account = simulate(returns, source_equity, selected, audit, START, END)
        audits[audit.name] = account
        account.to_csv(output / f"equity_{audit.name}.csv", index_label="open_time")
        for period, (start, end) in PERIODS.items():
            part = account.loc[(account.index >= start) & (account.index < end)]
            audit_rows.append(
                {"audit": audit.name, "period": period, **asdict(audit), **metrics(part["net_return"], part)}
            )
    pd.DataFrame(audit_rows).to_csv(output / "audit_metrics.csv", index=False)
    annual = yearly_returns(audits["base"]["net_return"])
    annual.to_csv(output / "ANNUAL_RETURNS.csv", index=False)
    checks = post_oos_checks(audits)
    target_met = bool(checks["full_cagr_ge_50pct"])
    robustness = bool(all(checks.values()))
    status = (
        "exploratory_50pct_target_met_no_capital_authority"
        if target_met and robustness
        else "rejected_after_frozen_oos"
    )

    def pm(audit: str, period: str) -> dict[str, Any]:
        start, end = PERIODS[period]
        part = audits[audit].loc[(audits[audit].index >= start) & (audits[audit].index < end)]
        return metrics(part["net_return"], part)

    decision = {
        "program": PROGRAM,
        "status": status,
        "eligible_policy_count": int(len(eligible)),
        "selected_policy": selected_name,
        "oos_opened": True,
        "historical_50pct_target_met": target_met,
        "robustness_passed": robustness,
        "post_oos_checks": checks,
        "integration_permitted": False,
        "capital_change_authorized": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selected_policy_spec": asdict(selected),
        "selection": proof,
        "base_full": pm("base", "full"),
        "base_validation_2024": pm("base", "validation_2024"),
        "base_holdout_2025": pm("base", "holdout_2025"),
        "base_final_2026h1": pm("base", "final_2026h1"),
        "severe_full": pm("severe", "full"),
        "extreme_full": pm("extreme", "full"),
        "delay_full": pm("delay_1d", "full"),
        "annual_returns": annual.to_dict(orient="records"),
    }
    write_json(output / "FROZEN_DECISION.json", decision)
    write_json(output / "summary.json", summary)
    report = [
        "# V509–V516 — V75 trend-hysteresis accelerator",
        "",
        f"Selected: `{selected_name}`.",
        "",
        f"- Full CAGR: {100*summary['base_full']['cagr']:.2f}%",
        f"- Full Sharpe: {summary['base_full']['sharpe']:.3f}",
        f"- Full Max DD: {100*summary['base_full']['max_drawdown']:.2f}%",
        f"- Average leverage: {summary['base_full']['average_leverage']:.3f}x",
        f"- Maximum leverage: {summary['base_full']['max_leverage']:.3f}x",
        f"- 2024 return: {100*summary['base_validation_2024']['total_return']:.2f}%",
        f"- 2025 return: {100*summary['base_holdout_2025']['total_return']:.2f}%",
        f"- 2026 H1 return: {100*summary['base_final_2026h1']['total_return']:.2f}%",
        f"- Severe CAGR: {100*summary['severe_full']['cagr']:.2f}%",
        f"- Extreme CAGR: {100*summary['extreme_full']['cagr']:.2f}%",
        f"- Delay CAGR: {100*summary['delay_full']['cagr']:.2f}%",
        "",
        f"Status: `{status}`.",
        "",
        "Non-pristine account-level research. No real leverage or capital change is authorized.",
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
