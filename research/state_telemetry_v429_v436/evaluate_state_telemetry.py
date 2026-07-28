#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROGRAM = "V429_V436_STATE_CONDITIONED_TELEMETRY"
STRATEGIES = ("V75_ATLAS_NX", "V136_EXECUTION_PLATEAU", "V28_GROWTH_CONTROL")
REQUIRED_FIELDS = (
    "timestamp", "strategy_id", "source_bundle_sha256", "target_hash",
    "realized_position_hash", "gross_target", "gross_realized", "turnover",
    "modelled_slippage_bps", "paper_slippage_bps", "net_return", "equity",
    "drawdown", "reconciliation_ok", "source_hash_match", "data_stale",
    "execution_complete",
)
FORWARD_GATES = {
    "earliest_start": "2026-07-28",
    "minimum_calendar_days": 180,
    "minimum_v136_target_changes": 25,
    "zero_reconciliation_breaks": True,
    "source_hash_match_rate": 1.0,
    "v136_turnover_reduction_min": 0.10,
    "v136_net_return_delta_min": 0.0,
    "v136_max_drawdown_worsening_max": 0.02,
    "paper_slippage_to_model_ratio_max": 1.5,
    "missing_or_stale_data_fail_closed": True,
}


def clean(value: Any) -> Any:
    if isinstance(value, dict): return {str(k): clean(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, np.integer): return int(value)
    if isinstance(value, (np.floating, float)):
        x=float(value); return x if np.isfinite(x) else None
    if isinstance(value, pd.Timestamp): return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_states(path: Path) -> pd.DataFrame:
    state=pd.read_csv(path, index_col=0, parse_dates=True)
    state.index=pd.to_datetime(state.index, utc=True).normalize()
    needed={"state_id","state_label","novelty_flag","novelty_ratio","transition_surprise","state_duration_days"}
    missing=needed-set(state.columns)
    if missing: raise ValueError(f"state file missing {sorted(missing)}")
    state=state[~state.index.duplicated(keep="last")].sort_index()
    state["previous_state_label"]=state["state_label"].shift(1)
    state["state_transition"]=(state["previous_state_label"].astype("string")+" -> "+state["state_label"].astype("string"))
    return state


def read_telemetry(path: Path) -> pd.DataFrame:
    frame=pd.read_csv(path)
    missing=set(REQUIRED_FIELDS)-set(frame.columns)
    if missing: raise ValueError(f"telemetry missing {sorted(missing)}")
    frame=frame[list(REQUIRED_FIELDS)].copy()
    frame["timestamp"]=pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    frame["date"]=frame["timestamp"].dt.normalize()
    if frame.duplicated(["timestamp","strategy_id"]).any(): raise ValueError("duplicate telemetry primary key")
    unknown=sorted(set(frame.strategy_id)-set(STRATEGIES))
    if unknown: raise ValueError(f"unknown strategies {unknown}")
    for column in ("reconciliation_ok","source_hash_match","data_stale","execution_complete"):
        frame[column]=frame[column].astype(str).str.lower().map({"true":True,"false":False,"1":True,"0":False})
        if frame[column].isna().any(): raise ValueError(f"invalid boolean {column}")
    numeric=("gross_target","gross_realized","turnover","modelled_slippage_bps","paper_slippage_bps","net_return","equity","drawdown")
    for column in numeric:
        frame[column]=pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column]).all(): raise ValueError(f"non-finite {column}")
    if (frame.equity<=0).any(): raise ValueError("non-positive equity")
    if (frame[["gross_target","gross_realized","turnover","modelled_slippage_bps","paper_slippage_bps"]]<0).any().any():
        raise ValueError("negative execution metric")
    return frame.sort_values(["strategy_id","timestamp"]).reset_index(drop=True)


def max_drawdown_from_returns(returns: pd.Series) -> float:
    if returns.empty: return 0.0
    equity=(1.0+returns.fillna(0.0)).cumprod()
    return float((equity/equity.cummax()-1.0).min())


def metrics(group: pd.DataFrame) -> dict[str, Any]:
    returns=pd.to_numeric(group.net_return, errors="coerce").dropna()
    sd=float(returns.std(ddof=1)) if len(returns)>1 else 0.0
    model=float(group.modelled_slippage_bps.sum())
    paper=float(group.paper_slippage_bps.sum())
    return {
        "rows": len(group),
        "calendar_days": int((group.date.max()-group.date.min()).days+1) if len(group) else 0,
        "total_return": float((1.0+returns).prod()-1.0) if len(returns) else 0.0,
        "mean_daily_return": float(returns.mean()) if len(returns) else 0.0,
        "annualized_volatility": sd*math.sqrt(365.0),
        "annualized_sharpe": float(returns.mean()/sd*math.sqrt(365.0)) if sd>0 else 0.0,
        "max_drawdown": max_drawdown_from_returns(returns),
        "turnover": float(group.turnover.sum()),
        "mean_gross_target": float(group.gross_target.mean()),
        "mean_gross_realized": float(group.gross_realized.mean()),
        "modelled_slippage_bps_sum": model,
        "paper_slippage_bps_sum": paper,
        "slippage_to_model_ratio": paper/model if model>0 else (0.0 if paper==0 else None),
        "reconciliation_breaks": int((~group.reconciliation_ok).sum()),
        "source_hash_mismatches": int((~group.source_hash_match).sum()),
        "stale_rows": int(group.data_stale.sum()),
        "incomplete_execution_rows": int((~group.execution_complete).sum()),
    }


def target_changes(group: pd.DataFrame) -> int:
    values=group.sort_values("timestamp").target_hash.astype(str)
    return int(values.ne(values.shift(1)).sum()-1) if len(values) else 0


def grouped_metrics(joined: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows=[]
    for values, group in joined.groupby(keys, dropna=False, observed=True):
        values=(values,) if not isinstance(values, tuple) else values
        row={key:value for key,value in zip(keys,values)}
        rows.append({**row, **metrics(group)})
    return pd.DataFrame(rows)


def acceptance(joined: pd.DataFrame) -> dict[str, Any]:
    full={strategy: metrics(group) for strategy,group in joined.groupby("strategy_id")}
    changes={strategy:target_changes(group) for strategy,group in joined.groupby("strategy_id")}
    required=set(STRATEGIES)
    present=set(full)
    start=joined.date.min() if len(joined) else None
    end=joined.date.max() if len(joined) else None
    days=int((end-start).days+1) if start is not None else 0
    v75=full.get("V75_ATLAS_NX",{})
    v136=full.get("V136_EXECUTION_PLATEAU",{})
    turnover_reduction=(1.0-v136.get("turnover",0.0)/v75.get("turnover",1.0)) if v75.get("turnover",0)>0 else None
    return_delta=v136.get("total_return",0.0)-v75.get("total_return",0.0)
    dd_worsening=max(0.0, abs(min(v136.get("max_drawdown",0.0),0.0))-abs(min(v75.get("max_drawdown",0.0),0.0)))
    slippage_ratio=v136.get("slippage_to_model_ratio")
    bad=int((~joined.reconciliation_ok).sum()+(~joined.source_hash_match).sum()+joined.data_stale.sum()+(~joined.execution_complete).sum())
    checks={
        "all_strategies_present": required<=present,
        "earliest_start_respected": start is not None and start>=pd.Timestamp(FORWARD_GATES["earliest_start"],tz="UTC"),
        "minimum_calendar_days": days>=FORWARD_GATES["minimum_calendar_days"],
        "minimum_v136_target_changes": changes.get("V136_EXECUTION_PLATEAU",0)>=FORWARD_GATES["minimum_v136_target_changes"],
        "zero_reconciliation_breaks": int((~joined.reconciliation_ok).sum())==0,
        "source_hash_match_rate": float(joined.source_hash_match.mean())==FORWARD_GATES["source_hash_match_rate"] if len(joined) else False,
        "no_stale_or_incomplete_data": bad==0,
        "v136_turnover_reduction": turnover_reduction is not None and turnover_reduction>=FORWARD_GATES["v136_turnover_reduction_min"],
        "v136_net_return_delta": return_delta>=FORWARD_GATES["v136_net_return_delta_min"],
        "v136_max_drawdown_worsening": dd_worsening<=FORWARD_GATES["v136_max_drawdown_worsening_max"],
        "paper_slippage_to_model_ratio": slippage_ratio is not None and slippage_ratio<=FORWARD_GATES["paper_slippage_to_model_ratio_max"],
    }
    return {
        "period_start": start, "period_end": end, "calendar_days": days,
        "target_changes": changes, "strategy_metrics": full,
        "v136_minus_v75": {"turnover_reduction":turnover_reduction,"net_return_delta":return_delta,"max_drawdown_worsening":dd_worsening,"slippage_to_model_ratio":slippage_ratio},
        "checks": checks, "passed": bool(all(checks.values())),
    }


def evaluate(states_path: Path, telemetry_path: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    states=read_states(states_path)
    telemetry=read_telemetry(telemetry_path)
    joined=telemetry.merge(states, left_on="date", right_index=True, how="left", validate="many_to_one")
    joined.to_csv(output/"joined_state_telemetry.csv", index=False)
    missing_state=int(joined.state_id.isna().sum())
    by_state=grouped_metrics(joined.dropna(subset=["state_id"]), ["strategy_id","state_id","state_label"])
    by_state.to_csv(output/"strategy_metrics_by_state.csv", index=False)
    by_transition=grouped_metrics(joined.dropna(subset=["state_transition"]), ["strategy_id","state_transition"])
    by_transition.to_csv(output/"strategy_metrics_by_transition.csv", index=False)
    by_novelty=grouped_metrics(joined, ["strategy_id","novelty_flag"])
    by_novelty.to_csv(output/"strategy_metrics_by_novelty.csv", index=False)
    result=acceptance(joined)
    result["program"]=PROGRAM
    result["state_rows_missing"]=missing_state
    result["state_coverage_rate"]=float(joined.state_id.notna().mean()) if len(joined) else 0.0
    result["fail_closed_triggered"]=bool(missing_state>0 or not result["checks"]["no_stale_or_incomplete_data"])
    result["capital_change_authorized"]=False
    result["live_ready"]=False
    result["real_leverage_authorized"]=False
    write_json(output/"FORWARD_ACCEPTANCE.json",result)
    return result


def write_template(path: Path) -> None:
    row={field:"" for field in REQUIRED_FIELDS}
    pd.DataFrame([row]).to_csv(path,index=False)


def synthetic_fixture(root: Path) -> tuple[Path,Path]:
    dates=pd.date_range("2026-07-28", periods=220, freq="1D", tz="UTC")
    state=pd.DataFrame(index=dates)
    state["state_id"]=(np.arange(len(dates))//30)%6
    labels=np.array(["deleveraging","transition","rotation","speculative_risk_on","transition_2","calm_risk_on"])
    state["state_label"]=labels[state.state_id]
    state["novelty_flag"]=(np.arange(len(dates))%47==0)
    state["novelty_ratio"]=np.where(state.novelty_flag,1.2,0.5)
    state["transition_surprise"]=0.2
    state["state_duration_days"]=np.arange(len(dates))%30+1
    states=root/"synthetic_states.csv"; state.to_csv(states)
    rows=[]
    for s_no,strategy in enumerate(STRATEGIES):
        eq=10_000.0; high=eq
        for i,date in enumerate(dates):
            ret=0.0004+0.00005*s_no+0.002*math.sin(i/11+s_no)
            eq*=1+ret; high=max(high,eq)
            rows.append({
                "timestamp":date.isoformat(),"strategy_id":strategy,"source_bundle_sha256":"a"*64,
                "target_hash":hashlib.sha256(f"{strategy}-{i//7}".encode()).hexdigest(),
                "realized_position_hash":hashlib.sha256(f"pos-{strategy}-{i//7}".encode()).hexdigest(),
                "gross_target":0.4,"gross_realized":0.4,"turnover":0.02 if i%7==0 else 0.0,
                "modelled_slippage_bps":1.0,"paper_slippage_bps":1.1,"net_return":ret,"equity":eq,
                "drawdown":eq/high-1,"reconciliation_ok":True,"source_hash_match":True,"data_stale":False,"execution_complete":True,
            })
    telemetry=root/"synthetic_telemetry.csv"; pd.DataFrame(rows).to_csv(telemetry,index=False)
    return states,telemetry


def self_test() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); states,telemetry=synthetic_fixture(root)
        result=evaluate(states,telemetry,root/"out")
        assert result["state_coverage_rate"]==1.0
        assert result["checks"]["minimum_calendar_days"] is True
        assert result["checks"]["minimum_v136_target_changes"] is True
        assert result["fail_closed_triggered"] is False
        assert (root/"out/strategy_metrics_by_state.csv").exists()
    print("V429-V436 state-conditioned telemetry self-test passed")


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--states",type=Path)
    parser.add_argument("--telemetry",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--template",type=Path)
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test: self_test(); return 0
    if args.template: write_template(args.template); return 0
    if not all((args.states,args.telemetry,args.output)): raise SystemExit("--states --telemetry --output required")
    result=evaluate(args.states,args.telemetry,args.output)
    print(json.dumps(clean(result),ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
