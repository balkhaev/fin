
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROGRAM = "V453_V460_CAUSAL_META_ENSEMBLE"
INITIAL_EQUITY = 10_000.0
PERIODS = {
    "development_2021_2023": ("2021-01-01", "2024-01-01"),
    "validation_2024": ("2024-01-01", "2025-01-01"),
    "holdout_2025": ("2025-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "full": ("2021-01-01", "2026-07-01"),
}
FOLDS = {
    "wf_2022": ("2022-01-01", "2023-01-01"),
    "wf_2023": ("2023-01-01", "2024-01-01"),
}
STATE_COLUMNS = (
    "state_label",
    "state_id",
    "assignment_confidence",
    "novelty_ratio",
    "novelty_flag",
    "transition_surprise",
    "state_duration_days",
    "trend",
    "breadth",
    "stress",
    "rotation",
    "liquidity",
    "leverage",
)

@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    kind: str
    lookback_days: int
    rebalance_days: int
    state_conditioning: bool = False
    duration_conditioning: bool = False
    market_budget: bool = False
    target_vol: float | None = None
    max_leverage: float = 1.0
    sleeve_cap: float = 0.75
    rebalance_on_state_change: bool = False
    static_v285: float | None = None
    static_v365: float | None = None
    promotable: bool = True
    inverted_budget_control: bool = False

@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    meta_cost_bps: float
    extra_underlying_cost_bps: float
    financing_rate: float
    weight_delay_days: int = 0

AUDITS = (
    Audit("base", meta_cost_bps=5.0, extra_underlying_cost_bps=0.0, financing_rate=0.08),
    Audit("severe", meta_cost_bps=20.0, extra_underlying_cost_bps=30.0, financing_rate=0.10),
    Audit("extreme", meta_cost_bps=40.0, extra_underlying_cost_bps=70.0, financing_rate=0.12),
    Audit("delay_1d", meta_cost_bps=5.0, extra_underlying_cost_bps=0.0, financing_rate=0.08, weight_delay_days=1),
)

def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return v if np.isfinite(v) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value

def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical_hash(value: Any) -> str:
    payload = json.dumps(clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()

def duration_class(days: float) -> str:
    days = int(days)
    if days <= 2:
        return "early"
    if days <= 5:
        return "intermediate"
    return "persistent"

def load_joined(path: Path, prefix: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "open_time" not in frame:
        unnamed = [c for c in frame.columns if c.startswith("Unnamed")]
        if unnamed:
            frame = frame.rename(columns={unnamed[0]: "open_time"})
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.set_index("open_time").sort_index()
    if frame.index.duplicated().any():
        raise ValueError(f"duplicate timestamps in {prefix}")
    required = {"return", "turnover", "gross_mean", *STATE_COLUMNS}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{prefix} missing columns: {missing}")
    keep = [*STATE_COLUMNS, "return", "turnover", "gross_mean", "costs", "funding_pnl", "price_pnl"]
    out = frame[keep].copy()
    rename = {
        "return": f"ret_{prefix}",
        "turnover": f"turnover_{prefix}",
        "gross_mean": f"gross_{prefix}",
        "costs": f"costs_{prefix}",
        "funding_pnl": f"funding_{prefix}",
        "price_pnl": f"price_{prefix}",
    }
    return out.rename(columns=rename)

def load_panel(v285_path: Path, v365_path: Path) -> pd.DataFrame:
    a = load_joined(v285_path, "v285")
    b = load_joined(v365_path, "v365")
    for column in STATE_COLUMNS:
        left = a[column]
        right = b[column]
        if column == "state_label":
            if not left.astype(str).equals(right.astype(str)):
                raise ValueError(f"state column mismatch: {column}")
        else:
            lv = pd.to_numeric(left, errors="coerce")
            rv = pd.to_numeric(right, errors="coerce")
            if not np.allclose(lv.to_numpy(float), rv.to_numpy(float), equal_nan=True, rtol=0.0, atol=1e-12):
                raise ValueError(f"state column mismatch: {column}")
    b = b.drop(columns=list(STATE_COLUMNS))
    panel = a.join(b, how="inner")
    if len(panel) < 1900:
        raise RuntimeError(f"insufficient aligned rows: {len(panel)}")
    numeric = [c for c in panel.columns if c != "state_label" and c != "novelty_flag"]
    panel[numeric] = panel[numeric].apply(pd.to_numeric, errors="coerce")
    if panel[["ret_v285", "ret_v365"]].isna().any().any():
        raise ValueError("missing strategy returns")
    panel["novelty_flag"] = panel["novelty_flag"].astype(str).str.lower().isin({"true", "1"})
    panel["duration_class"] = panel["state_duration_days"].map(duration_class)
    panel["state_changed"] = panel["state_label"].ne(panel["state_label"].shift(1))
    return panel

def ewm_last(series: pd.Series, halflife: float, kind: str) -> float:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        return 0.0
    if kind == "mean":
        return float(series.ewm(halflife=max(2.0, halflife), adjust=False).mean().iloc[-1])
    if kind == "std":
        if len(series) < 3:
            return float(series.std(ddof=0))
        value = series.ewm(halflife=max(2.0, halflife), adjust=False).std(bias=False).iloc[-1]
        return float(value) if np.isfinite(value) else float(series.std(ddof=1))
    raise ValueError(kind)

def capped_normalize(raw: np.ndarray, cap: float) -> np.ndarray:
    raw = np.maximum(np.asarray(raw, float), 0.0)
    if raw.sum() <= 0:
        return np.zeros_like(raw)
    weights = raw / raw.sum()
    for _ in range(8):
        excess = np.maximum(weights - cap, 0.0)
        if excess.sum() <= 1e-12:
            break
        weights = np.minimum(weights, cap)
        free = weights < cap - 1e-12
        if not free.any():
            break
        weights[free] += excess.sum() * weights[free] / max(weights[free].sum(), 1e-12)
    return weights / weights.sum() if weights.sum() > 0 else weights

def market_budget(row: pd.Series, inverted: bool = False) -> float:
    values = np.array([
        float(row["trend"]),
        float(row["breadth"]),
        float(row["liquidity"]),
        float(row["leverage"]),
        -float(row["stress"]),
        -abs(float(row["rotation"])),
    ])
    z = float(np.nanmean(values))
    if inverted:
        z = -z
    logistic = 1.0 / (1.0 + math.exp(-max(-8.0, min(8.0, z))))
    budget = 0.25 + 0.75 * logistic
    confidence = float(np.clip(row["assignment_confidence"], 0.0, 1.0))
    budget *= 0.85 + 0.15 * confidence
    if bool(row["novelty_flag"]):
        budget *= 0.85
    return float(np.clip(budget, 0.20, 1.00))

def estimate_sleeve_weights(panel: pd.DataFrame, t: int, policy: Policy) -> np.ndarray:
    history = panel.iloc[:t]
    if policy.kind == "static":
        return np.array([float(policy.static_v285), float(policy.static_v365)], dtype=float)
    if len(history) < max(30, policy.lookback_days // 2):
        return np.array([0.5, 0.5], dtype=float)
    window = history.iloc[-max(policy.lookback_days * 3, 252):]
    returns = window[["ret_v285", "ret_v365"]]
    vols = np.array([
        max(ewm_last(returns[column].iloc[-policy.lookback_days:], policy.lookback_days / 2, "std"), 1e-5)
        for column in returns.columns
    ])
    if policy.kind == "invvol":
        return capped_normalize(1.0 / vols, policy.sleeve_cap)

    current = panel.iloc[t]
    mus = []
    for column in returns.columns:
        global_series = returns[column].iloc[-policy.lookback_days:]
        global_mu = ewm_last(global_series, policy.lookback_days / 2, "mean")
        mu = global_mu
        if policy.state_conditioning:
            state_series = window.loc[window["state_label"] == current["state_label"], column]
            if len(state_series):
                state_mu = ewm_last(state_series, policy.lookback_days / 2, "mean")
                shrink = len(state_series) / (len(state_series) + 40.0)
                mu = global_mu + shrink * (state_mu - global_mu)
        if policy.duration_conditioning:
            duration_series = window.loc[
                (window["state_label"] == current["state_label"])
                & (window["duration_class"] == current["duration_class"]),
                column,
            ]
            if len(duration_series):
                duration_mu = ewm_last(duration_series, policy.lookback_days / 2, "mean")
                shrink = len(duration_series) / (len(duration_series) + 30.0)
                mu = mu + 0.5 * shrink * (duration_mu - mu)
        mus.append(mu)
    mus = np.array(mus)
    raw = np.maximum(mus, 0.0) / np.maximum(vols ** 2, 1e-8)
    if raw.sum() <= 0:
        return np.zeros(2)
    return capped_normalize(raw, policy.sleeve_cap)

def generate_weights(panel: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    columns = ["w_v285", "w_v365", "w_cash", "risky_budget", "predicted_vol"]
    weights = pd.DataFrame(0.0, index=panel.index, columns=columns)
    previous = np.array([0.0, 0.0])
    last_rebalance = -10**9
    for t in range(len(panel)):
        row = panel.iloc[t]
        state_rebalance = bool(policy.rebalance_on_state_change and row["state_changed"])
        scheduled = (t - last_rebalance) >= policy.rebalance_days
        if t == 0 or scheduled or state_rebalance:
            base = estimate_sleeve_weights(panel, t, policy)
            budget = market_budget(row, policy.inverted_budget_control) if policy.market_budget else 1.0
            predicted_vol = 0.0
            scale = 1.0
            if policy.target_vol is not None and t >= max(30, policy.lookback_days // 2) and base.sum() > 0:
                history = panel.iloc[max(0, t-policy.lookback_days):t][["ret_v285", "ret_v365"]]
                covariance = history.cov().to_numpy(float) * 365.0
                predicted_var = float(base @ covariance @ base)
                predicted_vol = math.sqrt(max(predicted_var, 0.0))
                if predicted_vol > 1e-6:
                    scale = float(np.clip(policy.target_vol / predicted_vol, 0.50, policy.max_leverage / max(budget, 1e-9)))
            risky_total = min(policy.max_leverage, budget * scale)
            selected = base * risky_total
            previous = selected
            last_rebalance = t
        else:
            selected = previous
            budget = float(selected.sum())
            predicted_vol = float(weights.iloc[t-1]["predicted_vol"]) if t else 0.0
        weights.iloc[t] = [
            float(selected[0]),
            float(selected[1]),
            float(1.0 - selected.sum()),
            float(selected.sum()),
            float(predicted_vol),
        ]
    return weights

def simulate(panel: pd.DataFrame, weights: pd.DataFrame, audit: Audit) -> pd.DataFrame:
    applied = weights.shift(audit.weight_delay_days).fillna(0.0) if audit.weight_delay_days else weights.copy()
    risky = applied[["w_v285", "w_v365"]].to_numpy(float)
    strategy_returns = panel[["ret_v285", "ret_v365"]].to_numpy(float)
    underlying_turnover = (
        np.abs(risky[:, 0]) * panel["turnover_v285"].to_numpy(float)
        + np.abs(risky[:, 1]) * panel["turnover_v365"].to_numpy(float)
    )
    risky_sum = risky.sum(axis=1)
    all_weights = applied[["w_v285", "w_v365", "w_cash"]].to_numpy(float)
    previous = np.vstack([np.array([[0.0, 0.0, 1.0]]), all_weights[:-1]])
    meta_turnover = np.abs(all_weights - previous).sum(axis=1)
    gross = (
        np.abs(risky[:, 0]) * panel["gross_v285"].to_numpy(float)
        + np.abs(risky[:, 1]) * panel["gross_v365"].to_numpy(float)
    )
    gross_return = (risky * strategy_returns).sum(axis=1)
    meta_cost = meta_turnover * audit.meta_cost_bps / 10_000.0
    underlying_stress_cost = underlying_turnover * audit.extra_underlying_cost_bps / 10_000.0
    financing = np.maximum(risky_sum - 1.0, 0.0) * audit.financing_rate / 365.0
    net_return = gross_return - meta_cost - underlying_stress_cost - financing
    equity = INITIAL_EQUITY * np.cumprod(1.0 + net_return)
    if np.any(equity <= 0) or not np.isfinite(equity).all():
        raise RuntimeError(f"non-positive/non-finite equity in {audit.name}")
    account = pd.DataFrame(
        {
            "equity": equity,
            "net_return": net_return,
            "gross_return": gross_return,
            "w_v285": applied["w_v285"],
            "w_v365": applied["w_v365"],
            "w_cash": applied["w_cash"],
            "risky_budget": risky_sum,
            "gross": gross,
            "underlying_turnover": underlying_turnover,
            "meta_turnover": meta_turnover,
            "meta_cost": meta_cost,
            "underlying_stress_cost": underlying_stress_cost,
            "financing": financing,
            "state_label": panel["state_label"],
            "state_duration_days": panel["state_duration_days"],
            "novelty_flag": panel["novelty_flag"],
        },
        index=panel.index,
    )
    account["drawdown"] = account["equity"] / account["equity"].cummax() - 1.0
    return account

def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0), 1 / 365.2425)

def cut(frame: pd.DataFrame, bounds: tuple[str, str]) -> pd.DataFrame:
    start, end = bounds
    return frame[(frame.index >= pd.Timestamp(start, tz="UTC")) & (frame.index < pd.Timestamp(end, tz="UTC"))]

def metrics(account: pd.DataFrame) -> dict[str, Any]:
    if account.empty:
        return {k: 0.0 for k in (
            "total_return","cagr","sharpe","max_drawdown","annual_turnover","annual_meta_turnover",
            "average_gross","max_gross","average_risky_budget","max_risky_budget","final_equity",
        )}
    years = elapsed_years(account.index)
    start_equity = INITIAL_EQUITY if account.index[0] == pd.Timestamp("2021-01-01", tz="UTC") else float(account.equity.iloc[0] / (1.0 + account.net_return.iloc[0]))
    final = float(account.equity.iloc[-1])
    total = final / start_equity - 1.0
    cagr = (final / start_equity) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    r = account["net_return"]
    std = float(r.std(ddof=1))
    sharpe = float(r.mean() / std * math.sqrt(365.0)) if std > 0 else 0.0
    local_equity = (1.0 + r).cumprod()
    dd = local_equity / local_equity.cummax() - 1.0
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": float(dd.min()),
        "annual_turnover": float((account["underlying_turnover"].sum() + account["meta_turnover"].sum()) / years),
        "annual_meta_turnover": float(account["meta_turnover"].sum() / years),
        "average_gross": float(account["gross"].mean()),
        "max_gross": float(account["gross"].max()),
        "average_risky_budget": float(account["risky_budget"].mean()),
        "max_risky_budget": float(account["risky_budget"].max()),
        "final_equity": final,
        "meta_cost": float(account["meta_cost"].sum() * INITIAL_EQUITY),
        "extra_underlying_cost": float(account["underlying_stress_cost"].sum() * INITIAL_EQUITY),
        "financing_drag": float(account["financing"].sum() * INITIAL_EQUITY),
    }

def yearly_returns(account: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for year, part in account.groupby(account.index.year):
        ret = float((1.0 + part["net_return"]).prod() - 1.0)
        rows.append({"year": int(year), label: ret})
    return pd.DataFrame(rows)

def policies_from_design(design: dict[str, Any]) -> list[Policy]:
    return [Policy(**row) for row in design["policies"]]

def development_score(account: pd.DataFrame, fold_metrics: dict[str, dict[str, Any]]) -> float:
    dev = metrics(cut(account, PERIODS["development_2021_2023"]))
    worst_fold_sharpe = min(item["sharpe"] for item in fold_metrics.values())
    worst_fold_return = min(item["total_return"] for item in fold_metrics.values())
    return (
        0.55 * dev["sharpe"]
        + 0.35 * worst_fold_sharpe
        + 0.75 * dev["cagr"]
        + 0.30 * worst_fold_return
        - 0.45 * abs(dev["max_drawdown"])
        - 0.015 * dev["annual_meta_turnover"]
    )

def blend_selected_accounts(accounts: list[pd.DataFrame]) -> pd.DataFrame:
    if not accounts:
        raise ValueError("no selected accounts")
    index = accounts[0].index
    for account in accounts[1:]:
        if not account.index.equals(index):
            raise ValueError("selected policy account index mismatch")
    return_frame = pd.concat([a["net_return"] for a in accounts], axis=1)
    blended_return = return_frame.mean(axis=1)
    result = pd.DataFrame(index=index)
    result["net_return"] = blended_return
    result["gross_return"] = pd.concat([a["gross_return"] for a in accounts], axis=1).mean(axis=1)
    for column in (
        "w_v285","w_v365","w_cash","risky_budget","gross","underlying_turnover","meta_turnover",
        "meta_cost","underlying_stress_cost","financing",
    ):
        result[column] = pd.concat([a[column] for a in accounts], axis=1).mean(axis=1)
    result["equity"] = INITIAL_EQUITY * (1.0 + result["net_return"]).cumprod()
    result["drawdown"] = result["equity"] / result["equity"].cummax() - 1.0
    result["state_label"] = accounts[0]["state_label"]
    result["state_duration_days"] = accounts[0]["state_duration_days"]
    result["novelty_flag"] = accounts[0]["novelty_flag"]
    return result

def self_test() -> None:
    index = pd.date_range("2021-01-01", periods=900, freq="1D", tz="UTC")
    rng = np.random.default_rng(453)
    state_ids = np.repeat(np.arange(6), 150)
    states = np.array(["deleveraging","transition","rotation","speculative_risk_on","transition_2","calm_risk_on"])[state_ids]
    duration = np.tile(np.arange(1, 151), 6)
    panel = pd.DataFrame(index=index)
    panel["ret_v285"] = rng.normal(0.00035, 0.007, len(index))
    panel["ret_v365"] = rng.normal(0.00025, 0.006, len(index))
    panel["turnover_v285"] = 0.01
    panel["turnover_v365"] = 0.02
    panel["gross_v285"] = 0.5
    panel["gross_v365"] = 0.35
    panel["state_label"] = states
    panel["state_id"] = state_ids
    panel["assignment_confidence"] = 0.5
    panel["novelty_ratio"] = 0.5
    panel["novelty_flag"] = False
    panel["transition_surprise"] = 0.1
    panel["state_duration_days"] = duration
    for c in ("trend","breadth","stress","rotation","liquidity","leverage"):
        panel[c] = rng.normal(0, 1, len(index))
    panel["duration_class"] = panel["state_duration_days"].map(duration_class)
    panel["state_changed"] = panel["state_label"].ne(panel["state_label"].shift(1))
    policy = Policy(
        name="self_test", kind="online", lookback_days=126, rebalance_days=14,
        state_conditioning=True, duration_conditioning=True, market_budget=True,
        target_vol=0.18, max_leverage=1.25, rebalance_on_state_change=True,
    )
    first_weights = generate_weights(panel, policy)
    first = simulate(panel, first_weights, AUDITS[0])
    changed = panel.copy()
    changed.iloc[-1, changed.columns.get_loc("ret_v285")] += 1.0
    second_weights = generate_weights(changed, policy)
    pd.testing.assert_frame_equal(first_weights.iloc[:-1], second_weights.iloc[:-1], check_exact=False, rtol=1e-12, atol=1e-12)
    assert first["risky_budget"].max() <= 1.2500001
    assert np.isfinite(first["equity"]).all()
    permuted = generate_weights(panel[panel.columns[::-1]], policy)
    pd.testing.assert_frame_equal(first_weights, permuted)
    print("V453-V460 causal meta-ensemble self-test passed")

def render_report(summary: dict[str, Any]) -> str:
    selected = ", ".join(summary.get("selected_policies", [])) or "none"
    oos = summary.get("candidate_periods", {})
    lines = [
        "# V453–V460 — causal state-aware meta-ensemble",
        "",
        f"Status: `{summary['status']}`.",
        "",
        "## Selected policies",
        "",
        f"`{selected}`",
        "",
        "## Candidate metrics",
        "",
        "```text",
    ]
    full = summary.get("candidate_full", {})
    for key in ("cagr","sharpe","max_drawdown","annual_turnover","max_risky_budget"):
        if key in full:
            lines.append(f"{key:24s} {full[key]: .6f}")
    lines += ["```", "", "## OOS periods", "", "```text"]
    for name, values in oos.items():
        lines.append(f"{name:18s} return={values.get('total_return',0): .4%}  sharpe={values.get('sharpe',0): .3f}  dd={values.get('max_drawdown',0): .3%}")
    lines += [
        "```",
        "",
        "This is exploratory post-research evidence. The component families and 2024–2026 history were already known before this cycle; no result authorizes live trading, leverage or capital.",
    ]
    return "\n".join(lines) + "\n"

def run(args: argparse.Namespace) -> int:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    design = json.loads(args.design.read_text(encoding="utf-8"))
    panel = load_panel(args.v285, args.v365)
    policies = policies_from_design(design)

    weights_by_policy: dict[str, pd.DataFrame] = {}
    accounts_by_policy: dict[tuple[str, str], pd.DataFrame] = {}
    rows = []
    base_accounts = {}
    for policy in policies:
        weights = generate_weights(panel, policy)
        weights_by_policy[policy.name] = weights
        account = simulate(panel, weights, AUDITS[0])
        base_accounts[policy.name] = account
        dev = metrics(cut(account, PERIODS["development_2021_2023"]))
        folds = {name: metrics(cut(account, bounds)) for name, bounds in FOLDS.items()}
        rows.append({
            "policy": policy.name,
            "promotable": policy.promotable,
            **{f"development_{k}": v for k, v in dev.items()},
            **{f"{fold}_{k}": v for fold, item in folds.items() for k, v in item.items()},
            "score": development_score(account, folds),
        })
    ranking = pd.DataFrame(rows).sort_values(["promotable","score"], ascending=[False,False])
    ranking.to_csv(output / "policy_development_ranking.csv", index=False)

    control = ranking[ranking["policy"] == design["selection"]["control_policy"]].iloc[0]
    promotable_rows = []
    for _, row in ranking[ranking["promotable"]].iterrows():
        gates = {
            "wf_2022_positive": float(row["wf_2022_total_return"]) > 0,
            "wf_2023_positive": float(row["wf_2023_total_return"]) > 0,
            "development_cagr_vs_control": float(row["development_cagr"]) >= float(control["development_cagr"]) + design["selection"]["development_cagr_uplift_min"],
            "development_sharpe_vs_control": float(row["development_sharpe"]) >= float(control["development_sharpe"]) + design["selection"]["development_sharpe_uplift_min"],
            "development_dd": float(row["development_max_drawdown"]) >= design["selection"]["development_max_drawdown_min"],
            "meta_turnover": float(row["development_annual_meta_turnover"]) <= design["selection"]["annual_meta_turnover_max"],
            "max_leverage": float(row["development_max_risky_budget"]) <= design["selection"]["max_leverage"],
        }
        promotable_rows.append({"policy": row["policy"], **gates, "eligible": all(gates.values()), "score": float(row["score"])})
    eligibility = pd.DataFrame(promotable_rows).sort_values(["eligible","score"], ascending=[False,False])
    eligibility.to_csv(output / "policy_eligibility.csv", index=False)
    eligible = list(eligibility[eligibility["eligible"]].head(design["selection"]["selected_policy_count"])["policy"])
    selection_proof = {
        "program": PROGRAM,
        "design_sha256": sha256_file(args.design),
        "input_sha256": {"v285": sha256_file(args.v285), "v365": sha256_file(args.v365)},
        "selection_period": PERIODS["development_2021_2023"],
        "folds": FOLDS,
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "component_history_already_known_before_cycle": True,
        "eligible_policies": eligible,
        "eligibility": eligibility.to_dict(orient="records"),
        "ranking_top": ranking.head(20).to_dict(orient="records"),
    }
    selection_proof["selection_proof_sha256"] = canonical_hash(selection_proof)
    write_json(output / "selection_proof_before_oos.json", selection_proof)

    if len(eligible) < design["selection"]["selected_policy_count"]:
        decision = {
            "program": PROGRAM,
            "status": "rejected_before_oos",
            "eligible_policy_count": len(eligible),
            "selected_policies": [],
            "oos_opened": False,
            "integration_permitted": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        write_json(output / "FROZEN_DECISION.json", decision)
        write_json(output / "summary.json", {**decision, "selection": selection_proof})
        (output / "REPORT_RU.md").write_text(render_report({**decision, "candidate_full": {}, "candidate_periods": {}}), encoding="utf-8")
        return 0

    audit_rows = []
    candidate_accounts = {}
    policy_weight_tables = []
    for audit in AUDITS:
        selected_accounts = []
        for name in eligible:
            account = simulate(panel, weights_by_policy[name], audit)
            accounts_by_policy[(name, audit.name)] = account
            selected_accounts.append(account)
        candidate = blend_selected_accounts(selected_accounts)
        candidate_accounts[audit.name] = candidate
        for period_name, bounds in PERIODS.items():
            audit_rows.append({"audit": audit.name, "period": period_name, **metrics(cut(candidate, bounds))})
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "audit_metrics.csv", index=False)
    candidate_accounts["base"].to_csv(output / "candidate_equity.csv")
    avg_weights = sum(weights_by_policy[name] for name in eligible) / len(eligible)
    avg_weights.to_csv(output / "candidate_weights.csv")
    for name in eligible:
        table = weights_by_policy[name].copy()
        table["policy"] = name
        table["open_time"] = table.index
        policy_weight_tables.append(table.reset_index(drop=True))
    pd.concat(policy_weight_tables, ignore_index=True).to_csv(output / "selected_policy_weights.csv", index=False)

    yearly = yearly_returns(candidate_accounts["base"], "candidate")
    for label in ("v285", "v365"):
        standalone = pd.DataFrame(index=panel.index)
        standalone["net_return"] = panel[f"ret_{label}"]
        standalone["equity"] = INITIAL_EQUITY * (1 + standalone["net_return"]).cumprod()
        yearly = yearly.merge(yearly_returns(standalone, label), on="year", how="outer")
    yearly.to_csv(output / "yearly_returns.csv", index=False)

    def audit_metric(audit: str, period: str) -> dict[str, Any]:
        row = audit_table[(audit_table.audit == audit) & (audit_table.period == period)].iloc[0]
        return {key: clean(value) for key, value in row.items() if key not in {"audit","period"}}

    full = audit_metric("base", "full")
    periods = {name: audit_metric("base", name) for name in ("development_2021_2023","validation_2024","holdout_2025","final_2026h1")}
    component_full = {}
    for label in ("v285","v365"):
        account = pd.DataFrame(index=panel.index)
        account["net_return"] = panel[f"ret_{label}"]
        account["equity"] = INITIAL_EQUITY * (1 + account["net_return"]).cumprod()
        account["underlying_turnover"] = panel[f"turnover_{label}"]
        account["meta_turnover"] = 0.0
        account["gross"] = panel[f"gross_{label}"]
        account["risky_budget"] = 1.0
        account["meta_cost"] = 0.0
        account["underlying_stress_cost"] = 0.0
        account["financing"] = 0.0
        component_full[label] = metrics(account)

    static_control_full = metrics(base_accounts[design["selection"]["control_policy"]])
    gates = {
        "validation_positive": periods["validation_2024"]["total_return"] > 0,
        "holdout_positive": periods["holdout_2025"]["total_return"] > 0,
        "final_positive": periods["final_2026h1"]["total_return"] > 0,
        "full_cagr_uplift": full["cagr"] >= max(component_full["v285"]["cagr"], component_full["v365"]["cagr"], static_control_full["cagr"]) + design["post_oos"]["full_cagr_uplift_min"],
        "full_sharpe_uplift": full["sharpe"] >= max(component_full["v285"]["sharpe"], component_full["v365"]["sharpe"], static_control_full["sharpe"]) + design["post_oos"]["full_sharpe_uplift_min"],
        "full_max_drawdown": full["max_drawdown"] >= design["post_oos"]["full_max_drawdown_min"],
        "severe_full_cagr_positive": audit_metric("severe","full")["cagr"] > 0,
        "extreme_full_cagr_positive": audit_metric("extreme","full")["cagr"] > 0,
        "delay_full_cagr_positive": audit_metric("delay_1d","full")["cagr"] > 0,
        "worst_calendar_year": float(yearly["candidate"].min()) >= design["post_oos"]["worst_calendar_year_min"],
        "max_leverage": full["max_risky_budget"] <= design["post_oos"]["max_leverage"],
    }
    passed = all(gates.values())
    decision = {
        "program": PROGRAM,
        "status": "exploratory_candidate_after_oos" if passed else "rejected_after_oos",
        "selected_policies": eligible,
        "oos_opened": True,
        "standalone_selection_passed": passed,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "historical_parameter_search_pristine": False,
        "reason_not_pristine": "component families and 2024-2026 outcomes were known before this cycle",
    }
    summary = {
        **decision,
        "selection": selection_proof,
        "candidate_full": full,
        "candidate_periods": periods,
        "component_full": component_full,
        "static_control_full": static_control_full,
        "post_oos_gates": gates,
        "audit_full": {audit.name: audit_metric(audit.name, "full") for audit in AUDITS},
        "yearly_returns": yearly.to_dict(orient="records"),
    }
    write_json(output / "FROZEN_DECISION.json", decision)
    write_json(output / "summary.json", summary)
    (output / "REPORT_RU.md").write_text(render_report(summary), encoding="utf-8")

    files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            files[str(path.relative_to(output))] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(output / "MANIFEST.json", {"program": PROGRAM, "files": files})
    print(json.dumps(clean(summary), ensure_ascii=False, indent=2))
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v285", type=Path)
    parser.add_argument("--v365", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if None in (args.v285, args.v365, args.design, args.output):
        raise SystemExit("--v285, --v365, --design and --output are required")
    return run(args)

if __name__ == "__main__":
    raise SystemExit(main())
