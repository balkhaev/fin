#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROGRAM = "V437_V444_STATE_CONDITIONED_STRATEGY_ANATOMY"
EXPECTED_STATE_MODEL_SHA256 = "187164615c057292cd1e7e8b47bfaa930e6fe97da4b42e253dff60ba15fd2690"
EXPECTED_ROWS = 2007
INITIAL_EQUITY = 10_000.0
PERIODS = {
    "development_2021_2023": ("2021-01-01", "2024-01-01"),
    "validation_2024": ("2024-01-01", "2025-01-01"),
    "holdout_2025": ("2025-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
}
FAILURE_PERIODS = {
    "V285_LOW_SKEW_HOURLY_CONTROLLER": "holdout_2025",
    "V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE": "validation_2024",
}


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


def period_name(timestamp: pd.Timestamp) -> str:
    for name, (start, end) in PERIODS.items():
        if pd.Timestamp(start, tz="UTC") <= timestamp < pd.Timestamp(end, tz="UTC"):
            return name
    return "outside_frozen_period"


def duration_bucket(days: float) -> str:
    value = int(days)
    if value <= 2:
        return "1-2"
    if value <= 5:
        return "3-5"
    if value <= 10:
        return "6-10"
    if value <= 20:
        return "11-20"
    if value <= 40:
        return "21-40"
    return "41+"


def load_state(state_path: Path, model_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    if model.get("model_sha256") != EXPECTED_STATE_MODEL_SHA256:
        raise RuntimeError(
            f"state model hash mismatch: {model.get('model_sha256')} != {EXPECTED_STATE_MODEL_SHA256}"
        )
    state = pd.read_csv(state_path)
    if "open_time" not in state:
        raise ValueError("market state file lacks open_time")
    state["open_time"] = pd.to_datetime(state["open_time"], utc=True)
    state = state.set_index("open_time").sort_index()
    if len(state) != EXPECTED_ROWS:
        raise RuntimeError(f"unexpected state rows: {len(state)}")
    required = {
        "state_label", "state_id", "assignment_confidence", "novelty_ratio",
        "novelty_flag", "transition_surprise", "state_duration_days",
        "trend", "breadth", "stress", "rotation", "liquidity", "leverage",
    }
    missing = sorted(required - set(state.columns))
    if missing:
        raise ValueError(f"missing market-state fields: {missing}")
    numeric_required = sorted(required - {"state_label", "novelty_flag"})
    if state[numeric_required].isna().to_numpy().all():
        raise ValueError("market-state numeric fields are empty")
    return state, model


def load_v285(path: Path) -> pd.DataFrame:
    hourly = pd.read_csv(path)
    hourly["open_time"] = pd.to_datetime(hourly["open_time"], utc=True)
    hourly = hourly.set_index("open_time").sort_index()
    if hourly.index.duplicated().any():
        raise ValueError("duplicate V285 hourly timestamps")
    groups = hourly.groupby(hourly.index.floor("D"))
    daily = pd.DataFrame(
        {
            "equity": groups["equity"].last(),
            "gross_mean": groups["gross"].mean(),
            "gross_max": groups["gross"].max(),
            "stress_gross_max": groups["stress_gross"].max(),
            "turnover": groups["turnover"].sum(),
            "costs": groups["costs"].sum(),
            "funding_pnl": groups["funding_pnl"].sum(),
            "price_pnl": groups["price_pnl"].sum(),
            "rebalance_events": groups["daily_rebalance"].sum(),
            "risk_reductions": groups["risk_reduction"].sum(),
            "forced_exits": groups["forced_exits"].sum(),
        }
    )
    daily.index.name = "open_time"
    daily["strategy_id"] = "V285_LOW_SKEW_HOURLY_CONTROLLER"
    return finalize_account(daily)


def load_v365(path: Path) -> pd.DataFrame:
    daily = pd.read_csv(path)
    daily["open_time"] = pd.to_datetime(daily["open_time"], utc=True)
    daily = daily.set_index("open_time").sort_index()
    daily["gross_mean"] = daily["gross"]
    daily["gross_max"] = daily["gross"]
    daily["stress_gross_max"] = np.nan
    daily["risk_reductions"] = 0
    daily["strategy_id"] = "V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE"
    keep = [
        "equity", "gross_mean", "gross_max", "stress_gross_max", "turnover",
        "costs", "funding_pnl", "price_pnl", "rebalance_events",
        "risk_reductions", "forced_exits", "strategy_id",
    ]
    return finalize_account(daily[keep])


def finalize_account(
    account: pd.DataFrame, expected_rows: int = EXPECTED_ROWS
) -> pd.DataFrame:
    if len(account) != expected_rows:
        raise RuntimeError(
            f"unexpected {account.strategy_id.iloc[0]} daily rows: {len(account)}"
        )
    if account.index.duplicated().any():
        raise ValueError("duplicate daily account timestamps")
    numeric = [column for column in account.columns if column != "strategy_id"]
    account[numeric] = account[numeric].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(account["equity"].to_numpy(float)).all():
        raise ValueError("non-finite strategy equity")
    if (account["equity"] <= 0.0).any():
        raise ValueError("non-positive strategy equity")
    previous = account["equity"].shift(1).fillna(INITIAL_EQUITY)
    account["return"] = account["equity"] / previous - 1.0
    account["log_return"] = np.log(account["equity"] / previous)
    account["dollar_pnl"] = account["equity"] - previous
    account["drawdown"] = account["equity"] / account["equity"].cummax() - 1.0
    return account


def join_state(account: pd.DataFrame, state: pd.DataFrame) -> pd.DataFrame:
    joined = state.join(account, how="left")
    if joined["strategy_id"].isna().any():
        missing = joined.index[joined["strategy_id"].isna()]
        raise RuntimeError(f"missing account rows for state dates: {list(missing[:5])}")
    joined["period"] = [period_name(timestamp) for timestamp in joined.index]
    joined["previous_state"] = joined["state_label"].shift(1).fillna("START")
    joined["transition"] = joined["previous_state"] + "->" + joined["state_label"]
    joined["state_changed"] = joined["state_label"].ne(joined["state_label"].shift(1))
    joined["duration_bucket"] = joined["state_duration_days"].map(duration_bucket)
    development_surprise = pd.to_numeric(
        joined.loc[joined.period == "development_2021_2023", "transition_surprise"],
        errors="coerce",
    ).dropna()
    q50 = float(development_surprise.quantile(0.50))
    q90 = float(development_surprise.quantile(0.90))

    def surprise_bucket(value: float) -> str:
        if not np.isfinite(value):
            return "unknown"
        if value <= q50:
            return "low"
        if value <= q90:
            return "medium"
        return "high"

    joined["surprise_bucket"] = pd.to_numeric(
        joined["transition_surprise"], errors="coerce"
    ).map(surprise_bucket)
    joined["novelty_class"] = np.where(joined["novelty_flag"].astype(bool), "novel", "familiar")
    return joined


def metric_row(frame: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(frame["return"], errors="coerce").fillna(0.0)
    log_returns = pd.to_numeric(frame["log_return"], errors="coerce").fillna(0.0)
    volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / volatility * np.sqrt(365.0)) if volatility > 0 else 0.0
    stress_values = pd.to_numeric(frame["stress_gross_max"], errors="coerce")
    turnover = float(pd.to_numeric(frame["turnover"], errors="coerce").fillna(0.0).sum())
    return {
        "days": len(frame),
        "compound_return": float(np.expm1(log_returns.sum())),
        "log_return_contribution": float(log_returns.sum()),
        "dollar_pnl": float(pd.to_numeric(frame["dollar_pnl"], errors="coerce").fillna(0.0).sum()),
        "mean_daily_return": float(returns.mean()),
        "annualized_sharpe": sharpe,
        "positive_day_rate": float((returns > 0.0).mean()),
        "negative_day_rate": float((returns < 0.0).mean()),
        "worst_day": float(returns.min()),
        "return_q05": float(returns.quantile(0.05)),
        "turnover": turnover,
        "average_daily_turnover": turnover / max(len(frame), 1),
        "log_return_per_turnover": float(log_returns.sum() / turnover) if turnover > 0 else None,
        "costs": float(pd.to_numeric(frame["costs"], errors="coerce").fillna(0.0).sum()),
        "funding_pnl": float(pd.to_numeric(frame["funding_pnl"], errors="coerce").fillna(0.0).sum()),
        "price_pnl": float(pd.to_numeric(frame["price_pnl"], errors="coerce").fillna(0.0).sum()),
        "average_gross": float(pd.to_numeric(frame["gross_mean"], errors="coerce").mean()),
        "max_gross": float(pd.to_numeric(frame["gross_max"], errors="coerce").max()),
        "max_stress_gross": float(stress_values.max()) if stress_values.notna().any() else None,
        "rebalance_events": int(pd.to_numeric(frame["rebalance_events"], errors="coerce").fillna(0.0).sum()),
        "risk_reductions": int(pd.to_numeric(frame["risk_reductions"], errors="coerce").fillna(0.0).sum()),
        "forced_exits": int(pd.to_numeric(frame["forced_exits"], errors="coerce").fillna(0.0).sum()),
        "novelty_days": int(frame["novelty_flag"].astype(bool).sum()),
        "novelty_share": float(frame["novelty_flag"].astype(bool).mean()),
        "state_entry_days": int(frame["state_changed"].astype(bool).sum()),
    }


def grouped_metrics(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    grouper: str | list[str] = keys[0] if len(keys) == 1 else keys
    for values, group in frame.groupby(grouper, sort=True, observed=True, dropna=False):
        if not isinstance(values, tuple):
            values = (values,)
        rows.append({**dict(zip(keys, values, strict=True)), **metric_row(group)})
    return pd.DataFrame(rows)


def worst_drawdown_episode(frame: pd.DataFrame, period: str) -> dict[str, Any]:
    selected = frame.loc[frame.period == period].copy()
    if selected.empty:
        return {"period": period, "available": False}
    normalized = selected["equity"] / float(selected["equity"].iloc[0])
    running_peak = normalized.cummax()
    drawdown = normalized / running_peak - 1.0
    trough_date = drawdown.idxmin()
    peak_date = normalized.loc[:trough_date].idxmax()
    peak_value = float(normalized.loc[peak_date])
    after = normalized.loc[trough_date:]
    recovered = after[after >= peak_value]
    end_date = recovered.index[0] if len(recovered) else selected.index[-1]
    episode = selected.loc[peak_date:end_date]
    by_state = grouped_metrics(episode, ["state_label"]).sort_values("dollar_pnl")
    by_transition = grouped_metrics(episode, ["transition"]).sort_values("dollar_pnl")
    return {
        "period": period,
        "available": True,
        "peak_date": peak_date,
        "trough_date": trough_date,
        "episode_end": end_date,
        "max_drawdown": float(drawdown.loc[trough_date]),
        "calendar_days_peak_to_trough": int((trough_date - peak_date).days),
        "calendar_days_episode": int((end_date - peak_date).days + 1),
        "episode_metrics": metric_row(episode),
        "state_occupancy": episode["state_label"].value_counts(normalize=True).to_dict(),
        "negative_state_contributors": by_state.head(4).to_dict(orient="records"),
        "negative_transition_contributors": by_transition.head(5).to_dict(orient="records"),
    }


def negative_loss_share(frame: pd.DataFrame, mask: pd.Series) -> float:
    pnl = pd.to_numeric(frame["dollar_pnl"], errors="coerce").fillna(0.0)
    total = float(-pnl.clip(upper=0.0).sum())
    selected = float(-pnl.where(mask, 0.0).clip(upper=0.0).sum())
    return selected / total if total > 0 else 0.0


def failure_map(joined: dict[str, pd.DataFrame]) -> dict[str, Any]:
    per_strategy: dict[str, Any] = {}
    negative_state_sets: list[set[str]] = []
    negative_transition_sets: list[set[str]] = []
    for strategy_id, frame in joined.items():
        period = FAILURE_PERIODS[strategy_id]
        selected = frame.loc[frame.period == period].copy()
        state_metrics = grouped_metrics(selected, ["state_label"]).sort_values("dollar_pnl")
        transition_metrics = grouped_metrics(selected, ["transition"]).sort_values("dollar_pnl")
        duration_metrics = grouped_metrics(selected, ["duration_bucket"]).sort_values("dollar_pnl")
        negative_states = set(
            state_metrics.loc[state_metrics.compound_return < 0.0, "state_label"].astype(str)
        )
        negative_transitions = set(
            transition_metrics.loc[transition_metrics.compound_return < 0.0, "transition"].astype(str)
        )
        negative_state_sets.append(negative_states)
        negative_transition_sets.append(negative_transitions)
        per_strategy[strategy_id] = {
            "failure_period": period,
            "period_metrics": metric_row(selected),
            "negative_states": sorted(negative_states),
            "negative_transitions": sorted(negative_transitions),
            "largest_negative_states": state_metrics.head(4).to_dict(orient="records"),
            "largest_negative_transitions": transition_metrics.head(5).to_dict(orient="records"),
            "largest_negative_duration_buckets": duration_metrics.head(4).to_dict(orient="records"),
            "novel_day_share_of_negative_dollar_pnl": negative_loss_share(
                selected, selected["novelty_flag"].astype(bool)
            ),
            "state_entry_share_of_negative_dollar_pnl": negative_loss_share(
                selected, selected["state_changed"].astype(bool)
            ),
            "persistent_state_gt_5d_share_of_negative_dollar_pnl": negative_loss_share(
                selected, selected["state_duration_days"] > 5
            ),
            "high_surprise_share_of_negative_dollar_pnl": negative_loss_share(
                selected, selected["surprise_bucket"] == "high"
            ),
            "worst_drawdown_episode": worst_drawdown_episode(frame, period),
        }
    common_states = sorted(set.intersection(*negative_state_sets)) if negative_state_sets else []
    common_transitions = (
        sorted(set.intersection(*negative_transition_sets)) if negative_transition_sets else []
    )
    return {
        "per_strategy": per_strategy,
        "common_negative_states": common_states,
        "common_negative_transitions": common_transitions,
        "common_failure_structure_present": bool(common_states or common_transitions),
        "interpretation_rule": (
            "Common states/transitions are descriptive failure contexts only. They cannot be converted "
            "into historical exposure gates after OOS."
        ),
    }


def champion_year_context(state: pd.DataFrame, annual_path: Path) -> pd.DataFrame:
    annual = pd.read_csv(annual_path)
    annual["year"] = pd.to_numeric(annual["year"], errors="raise").astype(int)
    if "V75_original" not in annual or "V136" not in annual:
        raise ValueError("V138 annual context lacks V75_original or V136")
    annual["V136_minus_V75"] = annual["V136"] - annual["V75_original"]
    occupancy = pd.crosstab(state.index.year, state["state_label"], normalize="index")
    occupancy.columns = [f"occupancy_{column}" for column in occupancy.columns]
    context = annual.merge(occupancy, left_on="year", right_index=True, how="left")
    context["novelty_rate"] = context["year"].map(
        state["novelty_flag"].astype(bool).groupby(state.index.year).mean()
    )
    changes = state["state_label"].ne(state["state_label"].shift(1))
    context["state_change_rate"] = context["year"].map(changes.groupby(state.index.year).mean())
    for axis in ("trend", "breadth", "stress", "rotation", "liquidity", "leverage"):
        context[f"mean_{axis}"] = context["year"].map(state[axis].groupby(state.index.year).mean())
    return context


def synthetic_self_test() -> None:
    index = pd.date_range("2021-01-01", periods=12, freq="1D", tz="UTC")
    state = pd.DataFrame(
        {
            "state_label": ["transition"] * 6 + ["deleveraging"] * 6,
            "state_id": [1] * 6 + [0] * 6,
            "assignment_confidence": 0.4,
            "novelty_ratio": 0.5,
            "novelty_flag": [False] * 10 + [True] * 2,
            "transition_surprise": [np.nan] + [0.1] * 11,
            "state_duration_days": list(range(1, 7)) + list(range(1, 7)),
            "trend": 0.0,
            "breadth": 0.0,
            "stress": 0.0,
            "rotation": 0.0,
            "liquidity": 0.0,
            "leverage": 0.0,
        },
        index=index,
    )
    equity = pd.Series([10000, 10100, 10050, 10200, 10100, 10300, 10200, 10000, 9800, 9900, 9700, 9750], index=index)
    account = pd.DataFrame(
        {
            "equity": equity,
            "gross_mean": 0.4,
            "gross_max": 0.5,
            "stress_gross_max": np.nan,
            "turnover": 0.1,
            "costs": 1.0,
            "funding_pnl": 0.0,
            "price_pnl": equity.diff().fillna(0.0),
            "rebalance_events": 1,
            "risk_reductions": 0,
            "forced_exits": 0,
            "strategy_id": "SYNTHETIC",
        },
        index=index,
    )
    account = finalize_account(account, expected_rows=len(account))
    joined = join_state(account, state)
    table = grouped_metrics(joined, ["state_label"])
    assert len(table) == 2
    episode = worst_drawdown_episode(joined.assign(period="holdout_2025"), "holdout_2025")
    assert episode["available"] is True
    assert episode["max_drawdown"] < 0.0
    print("V437-V444 strategy-anatomy self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path)
    parser.add_argument("--state-model", type=Path)
    parser.add_argument("--v285", type=Path)
    parser.add_argument("--v365", type=Path)
    parser.add_argument("--annual", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        synthetic_self_test()
        return 0
    required_args = [args.state, args.state_model, args.v285, args.v365, args.annual, args.output, args.design]
    if any(value is None for value in required_args):
        raise SystemExit("all input arguments are required unless --self-test is used")

    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    state, state_model = load_state(args.state, args.state_model)
    accounts = {
        "V285_LOW_SKEW_HOURLY_CONTROLLER": load_v285(args.v285),
        "V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE": load_v365(args.v365),
    }
    joined = {strategy: join_state(account, state) for strategy, account in accounts.items()}
    combined = pd.concat(joined.values()).sort_index()

    for strategy, frame in joined.items():
        frame.to_csv(output / f"joined_{strategy}.csv")

    state_metrics = grouped_metrics(combined, ["strategy_id", "state_label"])
    period_state_metrics = grouped_metrics(combined, ["strategy_id", "period", "state_label"])
    transition_metrics = grouped_metrics(combined, ["strategy_id", "period", "transition"])
    novelty_metrics = grouped_metrics(combined, ["strategy_id", "period", "novelty_class"])
    duration_metrics = grouped_metrics(
        combined, ["strategy_id", "period", "state_label", "duration_bucket"]
    )
    surprise_metrics = grouped_metrics(
        combined, ["strategy_id", "period", "surprise_bucket"]
    )
    state_metrics.to_csv(output / "strategy_metrics_by_state.csv", index=False)
    period_state_metrics.to_csv(output / "strategy_metrics_by_period_state.csv", index=False)
    transition_metrics.to_csv(output / "strategy_metrics_by_transition.csv", index=False)
    novelty_metrics.to_csv(output / "strategy_metrics_by_novelty.csv", index=False)
    duration_metrics.to_csv(output / "strategy_metrics_by_duration.csv", index=False)
    surprise_metrics.to_csv(output / "strategy_metrics_by_transition_surprise.csv", index=False)

    episodes = [
        {"strategy_id": strategy, **worst_drawdown_episode(frame, period)}
        for strategy, frame in joined.items()
        for period in PERIODS
    ]
    pd.DataFrame(
        [
            {
                key: json.dumps(clean(value), ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list)) else clean(value)
                for key, value in episode.items()
            }
            for episode in episodes
        ]
    ).to_csv(output / "worst_drawdown_episodes.csv", index=False)

    failure = failure_map(joined)
    write_json(output / "FAILURE_MAP.json", failure)
    annual_context = champion_year_context(state, args.annual)
    annual_context.to_csv(output / "champion_year_state_context.csv", index=False)

    technical = {
        "state_rows": len(state),
        "strategy_rows": {strategy: len(frame) for strategy, frame in joined.items()},
        "state_model_sha256": state_model["model_sha256"],
        "state_model_hash_match": state_model["model_sha256"] == EXPECTED_STATE_MODEL_SHA256,
        "state_file_sha256": sha256_file(args.state),
        "v285_input_sha256": sha256_file(args.v285),
        "v365_input_sha256": sha256_file(args.v365),
        "annual_context_sha256": sha256_file(args.annual),
        "design_sha256": sha256_file(args.design),
        "missing_state_rows": int(combined["state_label"].isna().sum()),
        "nonfinite_equity_rows": int((~np.isfinite(combined["equity"].to_numpy(float))).sum()),
    }
    technical["passed"] = bool(
        technical["state_rows"] == EXPECTED_ROWS
        and all(value == EXPECTED_ROWS for value in technical["strategy_rows"].values())
        and technical["state_model_hash_match"]
        and technical["missing_state_rows"] == 0
        and technical["nonfinite_equity_rows"] == 0
    )
    write_json(output / "TECHNICAL_QUALITY.json", technical)
    if not technical["passed"]:
        raise RuntimeError("strategy-anatomy technical quality gate failed")

    v285_failure = failure["per_strategy"]["V285_LOW_SKEW_HOURLY_CONTROLLER"]
    v365_failure = failure["per_strategy"]["V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE"]
    common_states = failure["common_negative_states"]
    common_transitions = failure["common_negative_transitions"]

    decision = {
        "program": PROGRAM,
        "status": "state_conditioned_failure_map_ready",
        "historical_parameter_search_closed": True,
        "historical_state_gating_permitted": False,
        "strategy_parameter_changes_permitted": False,
        "allocation_changes_permitted": False,
        "v285_revisit_status": "archive_as_rejected_oos_anti_control",
        "v365_revisit_status": "archive_as_rejected_oos_anti_control",
        "primary_refinement_priority": "V75_V136_forward_state_conditioned_execution_telemetry",
        "new_candidate_search_priority": "low_until_genuinely_independent_data_or_payoff_is_available",
        "common_negative_states": common_states,
        "common_negative_transitions": common_transitions,
        "forward_alert_only_hypothesis": {
            "inputs": [
                "state_label", "state_duration_days", "novelty_flag",
                "transition_surprise", "liquidity", "leverage", "stress"
            ],
            "action": "telemetry_alert_only",
            "trade_or_allocation_action": False,
            "earliest_start": "2026-07-28"
        },
        "new_sleeve_allocation": 0.0,
        "v136_capital_allocation": 0.0,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    write_json(output / "FROZEN_DECISION.json", decision)

    def top_state_text(item: dict[str, Any]) -> str:
        rows = item["largest_negative_states"]
        return ", ".join(
            f"{row['state_label']} ({float(row['compound_return']):+.2%})" for row in rows[:3]
        )

    v285_metrics = v285_failure["period_metrics"]
    v365_metrics = v365_failure["period_metrics"]
    report = f"""# V437–V444 — state-conditioned strategy failure anatomy

## Решение

Широкий поиск новых факторов не возобновляется. Два сильнейших rejected-after-OOS процесса разобраны в frozen V413 market-state coordinates. Attribution является описанием и не разрешает post-hoc state gating.

```text
status                              state_conditioned_failure_map_ready
historical_state_gating             prohibited
V285                                rejected OOS anti-control
V365                                rejected OOS anti-control
primary refinement                  V75/V136 forward execution telemetry
new sleeve allocation               0%
live_ready                          false
real_leverage_authorized            false
```

## V285 — failure window 2025

```text
return                              {float(v285_metrics['compound_return']):+.2%}
dollar P&L                          ${float(v285_metrics['dollar_pnl']):+,.2f}
turnover                            {float(v285_metrics['turnover']):.3f}x
novel loss share                    {float(v285_failure['novel_day_share_of_negative_dollar_pnl']):.1%}
state-entry loss share              {float(v285_failure['state_entry_share_of_negative_dollar_pnl']):.1%}
persistent state >5d loss share     {float(v285_failure['persistent_state_gt_5d_share_of_negative_dollar_pnl']):.1%}
```

Largest negative states: {top_state_text(v285_failure)}.

## V365 — failure window 2024

```text
return                              {float(v365_metrics['compound_return']):+.2%}
dollar P&L                          ${float(v365_metrics['dollar_pnl']):+,.2f}
turnover                            {float(v365_metrics['turnover']):.3f}x
novel loss share                    {float(v365_failure['novel_day_share_of_negative_dollar_pnl']):.1%}
state-entry loss share              {float(v365_failure['state_entry_share_of_negative_dollar_pnl']):.1%}
persistent state >5d loss share     {float(v365_failure['persistent_state_gt_5d_share_of_negative_dollar_pnl']):.1%}
```

Largest negative states: {top_state_text(v365_failure)}.

## Общая структура

Common negative states: `{', '.join(common_states) if common_states else 'none'}`.

Common negative transitions: `{', '.join(common_transitions[:8]) if common_transitions else 'none'}`.

Совпадение не превращается в торговое правило: оба failure windows уже просмотрены. Оно используется только для forward attribution V75/V136/V28 и для отказа от повторной настройки V285/V365.

## Champion context

`champion_year_state_context.csv` объединяет опубликованные годовые V75/V136 returns с occupancy, novelty, switching rate и средними market-state axes. Это coarse descriptive context; шесть календарных наблюдений не используются для regression, selection или изменения стратегии.

## Следующий эффективный шаг

Продолжать V429–V436 forward instrumentation. Новая исследовательская гипотеза допускается только при независимом источнике данных/payoff. State labels, duration, novelty и transition surprise пока используются как alert/attribution fields, а не как exposure controls.
"""
    (output / "REPORT_RU.md").write_text(report, encoding="utf-8")

    manifest: dict[str, dict[str, Any]] = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            manifest[str(path.relative_to(output))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    write_json(output / "MANIFEST.json", {"program": PROGRAM, "files": manifest})
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
