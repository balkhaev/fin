from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SPOT_SYMBOLS = ('ADAUSDT','BCHUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','EOSUSDT','ETHUSDT','LTCUSDT','XRPUSDT')
PERP_SYMBOLS = ('BTCUSDT','ETHUSDT')
START = pd.Timestamp('2021-01-01', tz='UTC')
PREFINAL_END = pd.Timestamp('2026-01-01', tz='UTC')
END = pd.Timestamp('2026-07-01', tz='UTC')
SEGMENTS = {
    'development_2021_2022': (pd.Timestamp('2021-01-01',tz='UTC'), pd.Timestamp('2023-01-01',tz='UTC')),
    'validation_2023': (pd.Timestamp('2023-01-01',tz='UTC'), pd.Timestamp('2024-01-01',tz='UTC')),
    'validation_2024': (pd.Timestamp('2024-01-01',tz='UTC'), pd.Timestamp('2025-01-01',tz='UTC')),
    'bridge_2025': (pd.Timestamp('2025-01-01',tz='UTC'), pd.Timestamp('2026-01-01',tz='UTC')),
}
FORCED_PENALTY = 0.01

@dataclass(frozen=True)
class Policy:
    name: str
    signal_family: str
    style: str
    allocation: str
    risk_mode: str
    long_budget: float
    short_budget: float
    extra_headroom: float

@dataclass(frozen=True)
class Audit:
    name: str
    cost_bps: float = 40.0
    initial_margin_ratio: float = 0.25
    maintenance_margin_ratio: float = 0.10
    operational_reserve: float = 0.20
    funding_multiplier: float = 1.0
    execution_delay: int = 0
    intrabar_widen: float = 0.0
    financing_rate: float = 0.06
    apply_cash_credit: bool = True

AUDITS = (
    Audit('stress'),
    Audit('severe',80.0,0.30,0.12,0.22,2.0,0,0.0,0.09),
    Audit('extreme',120.0,0.40,0.15,0.25,3.0,0,0.0,0.12),
    Audit('catastrophic',200.0,0.50,0.20,0.28,4.0,2,0.20,0.16),
    Audit('delay_1',40.0,execution_delay=1),
    Audit('delay_2',40.0,execution_delay=2),
    Audit('delay_3',40.0,execution_delay=3),
    Audit('funding_x3',40.0,funding_multiplier=3.0,financing_rate=0.09),
    Audit('margin_harsh',120.0,0.50,0.20,0.25,3.0,2,0.20,0.12),
    Audit('no_cash_credit',40.0,apply_cash_credit=False),
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_daily(directory: Path):
    frames = {}
    for symbol in SPOT_SYMBOLS:
        frame = pd.read_csv(directory / f'processed_{symbol}_1d.csv', index_col=0, parse_dates=True)
        frame.index = pd.to_datetime(frame.index, utc=True)
        frames[symbol] = frame
    index = pd.date_range(START, END - pd.Timedelta(days=1), freq='1D', tz='UTC')
    def panel(column: str):
        return pd.DataFrame({s: frames[s][column].reindex(index) for s in SPOT_SYMBOLS}, index=index)
    return index, panel('open'), panel('high'), panel('low'), panel('close'), panel('quote_volume'), panel('taker_buy_quote')


def load_perp(directory: Path):
    opens, highs, lows, closes, funding = {}, {}, {}, {}, {}
    for symbol in PERP_SYMBOLS:
        frame = pd.read_csv(directory / f'perp_{symbol}_8h.csv', index_col=0, parse_dates=True)
        frame.index = pd.to_datetime(frame.index, utc=True)
        opens[symbol], highs[symbol], lows[symbol], closes[symbol] = frame.open, frame.high, frame.low, frame.close
        fund = pd.read_csv(directory / f'funding_{symbol}.csv', index_col=0, parse_dates=True)
        fund.index = pd.to_datetime(fund.index, utc=True, format='mixed').floor('8h')
        funding[symbol] = fund.funding_rate.groupby(level=0).sum()
    index = pd.date_range(START, END - pd.Timedelta(hours=8), freq='8h', tz='UTC')
    return (
        pd.DataFrame(opens).reindex(index), pd.DataFrame(highs).reindex(index),
        pd.DataFrame(lows).reindex(index), pd.DataFrame(closes).reindex(index),
        pd.DataFrame(funding).reindex(index).fillna(0.0),
    )


def load_base_inputs(atlas_root: Path, v75, index: pd.DatetimeIndex):
    return v75.load_inputs(
        atlas_root/'inputs'/'asset'/'v27_asset_weights.csv',
        atlas_root/'inputs'/'asset'/'v4_frozen_signal.csv',
        atlas_root/'inputs'/'asset'/'v67_leader_equity.csv',
        index,
    )


def rolling_z(x: pd.DataFrame, window: int) -> pd.DataFrame:
    mean = x.rolling(window, min_periods=max(20, window//2)).mean()
    std = x.rolling(window, min_periods=max(20, window//2)).std(ddof=1)
    return (x - mean) / std.replace(0, np.nan)


def build_regime_signals(close: pd.DataFrame, quote: pd.DataFrame, taker: pd.DataFrame):
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema100 = close.ewm(span=100, adjust=False, min_periods=100).mean()
    ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    ret21 = close.pct_change(21, fill_method=None)
    ret63 = close.pct_change(63, fill_method=None)
    ret126 = close.pct_change(126, fill_method=None)
    vol60 = close.pct_change(fill_method=None).rolling(60, min_periods=30).std(ddof=1)*np.sqrt(365.0)
    vol90 = close.pct_change(fill_method=None).rolling(90, min_periods=45).std(ddof=1)*np.sqrt(365.0)
    ratio = (taker / quote.replace(0, np.nan)).clip(0, 1)
    flow_z = rolling_z(ratio, 63)
    q_z = rolling_z(np.log1p(quote), 63)

    breadth100 = (close > ema100).mean(axis=1)
    breadth200 = (close > ema200).mean(axis=1)
    mom63 = (ret63 > 0).mean(axis=1)
    mom126 = (ret126 > 0).mean(axis=1)
    flow_breadth = ((flow_z > 0) & (q_z > -0.25)).mean(axis=1)
    btceth_trend = ((close[list(PERP_SYMBOLS)] > ema200[list(PERP_SYMBOLS)]) & (ret126[list(PERP_SYMBOLS)] > 0)).mean(axis=1)
    btceth_fast = ((close[list(PERP_SYMBOLS)] > ema50[list(PERP_SYMBOLS)]) & (ret21[list(PERP_SYMBOLS)] > 0)).mean(axis=1)

    raw = {
        'breadth': .35*breadth100 + .35*breadth200 + .15*mom63 + .15*mom126,
        'flow': .25*breadth100 + .25*breadth200 + .15*mom63 + .10*mom126 + .25*flow_breadth,
        'composite': .20*breadth100 + .20*breadth200 + .15*mom63 + .15*mom126 + .15*flow_breadth + .10*btceth_trend + .05*btceth_fast,
    }
    score = {k: v.shift(1).fillna(0.5).clip(0,1) for k,v in raw.items()}

    # Causal BTC/ETH allocation units. Signal is formed at close t and applied at open t+1.
    pair_close = close[list(PERP_SYMBOLS)]
    pair_ret = pair_close.pct_change(126, fill_method=None)
    pair_vol = vol90[list(PERP_SYMBOLS)].replace(0, np.nan)
    risk_adj = pair_ret / pair_vol
    invvol = 1.0 / pair_vol.clip(lower=0.15)
    balanced = invvol.div(invvol.sum(axis=1), axis=0).fillna(0.5)
    long_relative = pd.DataFrame(0.0, index=close.index, columns=PERP_SYMBOLS)
    short_relative = pd.DataFrame(0.0, index=close.index, columns=PERP_SYMBOLS)
    best = risk_adj.idxmax(axis=1)
    worst = risk_adj.idxmin(axis=1)
    for sym in PERP_SYMBOLS:
        long_relative[sym] = (best == sym).astype(float)
        short_relative[sym] = (worst == sym).astype(float)
    units = {
        'balanced_long': balanced.shift(1).fillna(0.5),
        'balanced_short': balanced.shift(1).fillna(0.5),
        'relative_long': long_relative.shift(1).fillna(0),
        'relative_short': short_relative.shift(1).fillna(0),
    }
    pair_vol_scalar = pair_close.pct_change(fill_method=None).mean(axis=1).rolling(63,min_periods=32).std(ddof=1)*np.sqrt(365.0)
    return score, units, pair_vol_scalar.shift(1)


def style_budgets(style: str):
    return {
        'defensive': (0.05, 0.15),
        'symmetric': (0.10, 0.10),
        'growth': (0.15, 0.05),
    }[style]


def process_overlay(policy: Policy, score: pd.Series, units: dict[str,pd.DataFrame], pair_vol: pd.Series):
    # Ensemble over adjacent thresholds; avoids a single tuned threshold.
    thresholds = ((0.55,0.35), (0.60,0.30), (0.65,0.25))
    arr = []
    long_unit = units[f'{policy.allocation}_long']
    short_unit = units[f'{policy.allocation}_short']
    for high, low in thresholds:
        long_gate = ((score >= high) & (score.diff() >= -0.10)).astype(float)
        short_gate = ((score <= low) & (score.diff() <= 0.10)).astype(float)
        vol_scale = (0.45 / pair_vol.replace(0,np.nan)).clip(lower=0.35, upper=1.0).fillna(0.0)
        long = long_unit.mul(long_gate * vol_scale, axis=0) * policy.long_budget
        short = short_unit.mul(short_gate * vol_scale, axis=0) * (-policy.short_budget)
        arr.append(long + short)
    return sum(arr) / len(arr)


def account_metrics(account: pd.DataFrame):
    eq = account.equity
    r = eq.pct_change().fillna(eq.iloc[0]/10000.0-1.0)
    years = max(((eq.index[-1]-eq.index[0]).days+1)/365.0, 1/365)
    dd = eq/eq.cummax()-1
    sd = r.std(ddof=1)
    return {
        'total_return': float(eq.iloc[-1]/10000.0-1.0),
        'cagr': float((eq.iloc[-1]/10000.0)**(1/years)-1.0),
        'max_drawdown': float(dd.min()),
        'sharpe': float(r.mean()/sd*np.sqrt(365.0)) if sd>0 else 0.0,
        'annual_turnover': float(account.turnover.sum()/years),
        'average_gross': float(account.gross.mean()),
        'max_gross': float(account.gross.max()),
        'average_perp_gross': float(account.perp_gross.mean()),
        'costs': float(account.costs.sum()),
        'funding_pnl': float(account.funding_pnl.sum()),
        'liquidations': int((account.liquidated_notional>0).sum()),
        'min_margin_buffer': float(account.min_margin_buffer.min()),
        'average_regime_overlay_gross': float(account['regime_overlay_gross'].mean()) if 'regime_overlay_gross' in account else 0.0,
    }


def period_metrics(account, start, end):
    x = account[(account.index>=start)&(account.index<end)]
    if x.empty: return {k:0.0 for k in ('total_return','cagr','max_drawdown','sharpe','annual_turnover')}
    y=x.copy();scale=10000.0/y.equity.iloc[0];y.equity*=scale
    return account_metrics(y)


def yearly(account):
    r=account.equity.pct_change().fillna(account.equity.iloc[0]/10000.0-1.0)
    return pd.DataFrame([{'year':int(y),'return':float((1+g).prod()-1)} for y,g in r.groupby(r.index.year)])


def overlay_risk_multiplier(policy: Policy, drawdown: float, stage: int, vol: float):
    if policy.risk_mode == 'fixed':
        return 1.0
    if policy.risk_mode == 'stage':
        return (1.0,0.75,0.50)[stage]
    if policy.risk_mode == 'adaptive':
        d = 1.0 if drawdown > -0.08 else 0.65 if drawdown > -0.15 else 0.35
        v = 1.0 if not np.isfinite(vol) or vol < 0.35 else 0.70 if vol < 0.55 else 0.45
        return d*v*(1.0,0.80,0.60)[stage]
    raise ValueError(policy.risk_mode)


def simulate(v75, index, so, sh, sl, sc, po, ph, pl, pc, fr, base_inputs, overlay_target, policy: Policy, audit: Audit):
    v27,s27,p27,s4,s67,p67,vg,vs,vp,credit,vol=base_inputs
    arrays=[x.to_numpy(float) if hasattr(x,'to_numpy') else np.asarray(x,float) for x in (s27,p27,s4,s67,p67,vg,vs,vp,credit,vol)]
    s27a,p27a,s4a,s67a,p67a,vga,vsa,vpa,ca,va=arrays
    oa=overlay_target.reindex(index).fillna(0).to_numpy(float)
    if audit.execution_delay:
        def delay(x):
            out=np.zeros_like(x);out[audit.execution_delay:]=x[:-audit.execution_delay];return out
        s27a,p27a,s4a,s67a,p67a=map(delay,(s27a,p27a,s4a,s67a,p67a))
        vga,vsa,vpa,ca,va=map(delay,(vga,vsa,vpa,ca,va));oa=delay(oa)
    so,sh,sl,sc=[x.to_numpy(float) for x in (so,sh,sl,sc)]
    po,ph,pl,pc=[x.to_numpy(float) for x in (po,ph,pl,pc)]
    fr=fr.to_numpy(float)*audit.funding_multiplier
    lookup={t:i for i,t in enumerate(pd.date_range(START,END-pd.Timedelta(hours=8),freq='8h',tz='UTC'))}
    cash=initial=hw=10000.0;spot=np.zeros(len(SPOT_SYMBOLS));perp=np.zeros(2);prev=prev8=None;records=[];rate=audit.cost_bps/10000.0
    for i,day in enumerate(index):
        forced=funding=liquidated=0.0;i0=lookup[day]
        if prev is not None:
            for j in np.flatnonzero(spot>0):
                if np.isfinite(so[i,j]) and np.isfinite(sc[prev,j]) and sc[prev,j]>0: spot[j]*=so[i,j]/sc[prev,j]
                else:
                    n=spot[j];pen=n*max(rate,FORCED_PENALTY);cash+=max(0,n-pen);forced+=pen;spot[j]=0
            ratio=np.divide(po[i0],pc[prev8],out=np.ones(2),where=np.isfinite(po[i0])&np.isfinite(pc[prev8])&(pc[prev8]>0));cash+=float(np.sum(perp*(ratio-1)));perp*=ratio
            z=float(np.sum(-perp*fr[i0]));cash+=z;funding+=z
        equity_open=float(cash+spot.sum());actual_s=spot/max(equity_open,1e-12);actual_p=perp/max(equity_open,1e-12)
        bs,bp,w4,q,base_cap,stages=v75.targets_for_day(i,hw,initial,s27a,p27a,s4a,s67a,p67a,vga,vsa,vpa,va,audit)
        stage=int(round(np.mean(stages)));dd=equity_open/max(hw,1e-12)-1.0
        rm=overlay_risk_multiplier(policy,dd,stage,float(va[max(0,i-1)]))
        extra=oa[i]*rm
        # Overlay is scaled first; frozen V75 base remains unchanged.
        cap=min(1.20, base_cap+policy.extra_headroom)
        base_g=float(bs.sum()+np.abs(bp).sum());add_g=float(np.abs(extra).sum());alpha=1.0
        if add_g>0: alpha=min(alpha,max(0.0,cap-base_g)/add_g)
        base_req=float(bs.sum()+audit.initial_margin_ratio*np.abs(bp).sum()+audit.operational_reserve)
        add_req=float(audit.initial_margin_ratio*np.abs(extra).sum())
        if add_req>0: alpha=min(alpha,max(0.0,1.0-base_req)/add_req)
        alpha=max(0.0,min(1.0,alpha));ds=bs;dp=bp+alpha*extra
        turnover=float(np.abs(ds-actual_s).sum()+np.abs(dp-actual_p).sum());tc=equity_open*turnover*rate;after=max(0.0,equity_open-tc)
        spot=ds*after;perp=dp*after;cash=after-spot.sum()
        if audit.apply_cash_credit: cash+=after*max(0,1-w4)*ca[i]
        gross_open=float(ds.sum()+np.abs(dp).sum());cash-=after*max(0,gross_open-1.0)*audit.financing_rate/365.0
        spot_ratio=np.divide(sc[i],so[i],out=np.ones(len(SPOT_SYMBOLS)),where=np.isfinite(sc[i])&np.isfinite(so[i])&(so[i]>0));spot*=spot_ratio
        minbuf=1e9
        for k in range(3):
            j=i0+k;r=np.divide(pc[j],po[j],out=np.ones(2),where=np.isfinite(pc[j])&np.isfinite(po[j])&(po[j]>0));cash+=float(np.sum(perp*(r-1)));perp*=r
            high=ph[j]*(1+audit.intrabar_widen);low=pl[j]*(1-audit.intrabar_widen);mark=np.where(perp>=0,low,high);mr=np.divide(mark,pc[j],out=np.ones(2),where=np.isfinite(mark)&np.isfinite(pc[j])&(pc[j]>0));adverse=float(np.sum(perp*(mr-1)));maint=audit.maintenance_margin_ratio*float(np.abs(perp*mr).sum());buf=(cash+adverse-maint)/max(cash+spot.sum(),1e-12);minbuf=min(minbuf,buf)
            if buf<0 and np.any(perp):
                n=float(np.abs(perp).sum());pen=n*.01;cash-=pen;forced+=pen;liquidated+=n;perp[:]=0
            if k<2:
                z=float(np.sum(-perp*fr[j+1]));cash+=z;funding+=z
        eq=float(cash+spot.sum());hw=max(hw,eq);gross=float((spot.sum()+np.abs(perp).sum())/max(eq,1e-12));perpg=float(np.abs(perp).sum()/max(eq,1e-12))
        records.append({'equity':eq,'gross':gross,'turnover':turnover,'costs':tc+forced,'funding_pnl':funding,'spot_gross':float(spot.sum()/max(eq,1e-12)),'perp_gross':perpg,'liquidated_notional':liquidated,'min_margin_buffer':minbuf,'regime_overlay_gross':float(alpha*np.abs(extra).sum()),'regime_overlay_net':float(alpha*extra.sum()),'high_water':hw})
        prev=i;prev8=i0+2
    return pd.DataFrame(records,index=index)


def policies():
    out=[]
    for family,style,alloc,risk,headroom in itertools.product(
        ('breadth','flow','composite'),('defensive','symmetric','growth'),('balanced','relative'),('stage','adaptive'),(0.05,0.10)
    ):
        lb,sb=style_budgets(style)
        out.append(Policy(f'{family}_{style}_{alloc}_{risk}_h{int(headroom*100)}',family,style,alloc,risk,lb,sb,headroom))
    return out


def score_row(m, seg, severe, baseline):
    uplift=m['cagr']-baseline['cagr']
    return uplift + .08*(m['sharpe']-baseline['sharpe']) - .15*max(0,abs(m['max_drawdown'])-abs(baseline['max_drawdown'])) + .10*min(seg.values()) - .0015*m['annual_turnover']


def self_test():
    idx=pd.date_range('2020-01-01',periods=500,freq='1D',tz='UTC');rng=np.random.default_rng(88)
    close=pd.DataFrame(100*np.exp(np.cumsum(rng.normal(0,.02,(500,9)),axis=0)),index=idx,columns=SPOT_SYMBOLS);q=pd.DataFrame(rng.lognormal(12,1,(500,9)),index=idx,columns=SPOT_SYMBOLS);t=q*rng.uniform(.3,.7,(500,9))
    scores,units,pv=build_regime_signals(close,q,t);assert set(scores)=={'breadth','flow','composite'}
    for p in policies()[:10]:
        x=process_overlay(p,scores[p.signal_family],units,pv);assert x.shape==(500,2);assert np.isfinite(x.to_numpy()).all();assert float(np.abs(x).sum(axis=1).max())<=max(p.long_budget,p.short_budget)+1e-9
    changed=close.copy();changed.iloc[-1]*=10;s2,u2,p2=build_regime_signals(changed,q,t)
    for k in scores: assert np.allclose(scores[k].iloc[:-1],s2[k].iloc[:-1])
    print('V88 regime-overlay causal self-test passed')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--atlas-root',type=Path);ap.add_argument('--output',type=Path);ap.add_argument('--self-test',action='store_true');args=ap.parse_args()
    if args.self_test:self_test();return
    if args.atlas_root is None or args.output is None:raise SystemExit('--atlas-root and --output required')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    v75=load_module('v75_for_v88',args.atlas_root/'source'/'v75_operational_feedback_engine.py')
    index,so,sh,sl,sc,quote,taker=load_daily(args.atlas_root/'inputs'/'asset'/'v6');po,ph,pl,pc,fr=load_perp(args.atlas_root/'inputs'/'asset'/'v5'/'processed');base_inputs=load_base_inputs(args.atlas_root,v75,index)
    base=v75.simulate(index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,v75.Audit('stress'));base.to_csv(out/'v75_original_stress_equity.csv');base_yearly=yearly(base).rename(columns={'return':'V75_original'});base_yearly.to_csv(out/'v75_original_annual_returns.csv',index=False)
    base_pre=period_metrics(base,START,PREFINAL_END);base_full=account_metrics(base)
    scores,units,pair_vol=build_regime_signals(sc,quote,taker)
    overlay_cache={p.name:process_overlay(p,scores[p.signal_family],units,pair_vol) for p in policies()}
    rows=[]
    for n,p in enumerate(policies(),1):
        acc=simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,overlay_cache[p.name],p,Audit('stress'))
        m=period_metrics(acc,START,PREFINAL_END);seg={k:period_metrics(acc,*w)['total_return'] for k,w in SEGMENTS.items()}
        sev=simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,overlay_cache[p.name],p,Audit('severe'))
        sm=period_metrics(sev,START,PREFINAL_END)
        eligible=bool(m['cagr']>=base_pre['cagr']+.005 and m['max_drawdown']>=-.25 and m['sharpe']>=1.25 and min(seg.values())>-0.03 and sm['cagr']>.18 and sm['max_drawdown']>-0.35 and m['annual_turnover']<15 and m['liquidations']==0)
        rows.append({'policy':p.name,'eligible_before_final':eligible,'score':score_row(m,seg,sm,base_pre),**asdict(p),**{f'prefinal_{k}':v for k,v in m.items()},**{f'segment_{k}':v for k,v in seg.items()},'severe_cagr':sm['cagr'],'severe_dd':sm['max_drawdown']})
        print(f'{n}/{len(policies())} {p.name} CAGR={m["cagr"]:.4f} DD={m["max_drawdown"]:.4f} eligible={eligible}',flush=True)
    rank=pd.DataFrame(rows).sort_values(['eligible_before_final','score'],ascending=[False,False]);rank.to_csv(out/'selection_ranking_before_final.csv',index=False)
    elig=rank[rank.eligible_before_final]
    if elig.empty:
        selected_names=list(rank.head(3).policy)
        eligible=False
    else:
        leader=elig.iloc[0];family=leader.signal_family;style=leader['style'];neighbors=elig[(elig.signal_family==family)&(elig['style']==style)].head(3);selected_names=list(neighbors.policy);eligible=True
    # Ensemble the selected neighboring policy targets; use central conservative policy fields.
    selected_policies=[next(p for p in policies() if p.name==n) for n in selected_names]
    ensemble_overlay=sum(overlay_cache[n] for n in selected_names)/len(selected_names)
    central=selected_policies[0]
    ensemble_policy=Policy('ensemble__'+'__'.join(selected_names),central.signal_family,central.style,'balanced','adaptive',central.long_budget,central.short_budget,min(x.extra_headroom for x in selected_policies))
    proof={'candidate':'ACTIVE_V88_V94_REGIME_ADAPTIVE_ATLAS','selection_uses_2021_2025_only':True,'program_level_final_pristine':False,'policy_count':len(policies()),'selected_components':selected_names,'ensemble_policy':asdict(ensemble_policy),'eligible_before_final':eligible,'baseline_prefinal':base_pre,'gates':{'cagr_uplift_min':.005,'max_drawdown_min':-.25,'sharpe_min':1.25,'worst_segment_min':-.03,'severe_cagr_min':.18,'severe_dd_min':-.35,'turnover_max':15}}
    proof['sha256']=hashlib.sha256(json.dumps(proof,sort_keys=True,default=float).encode()).hexdigest();(out/'selection_proof_before_final.json').write_text(json.dumps(proof,indent=2,default=float))
    audits=[];accounts={}
    for au in AUDITS:
        acc=simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,ensemble_overlay,ensemble_policy,au);accounts[au.name]=acc;acc.to_csv(out/f'{au.name}_equity.csv');audits.append({'audit':au.name,**asdict(au),**account_metrics(acc),'final_2026h1_return':period_metrics(acc,PREFINAL_END,END)['total_return'],'prefinal_cagr':period_metrics(acc,START,PREFINAL_END)['cagr']})
    audit_df=pd.DataFrame(audits);audit_df.to_csv(out/'audit_metrics.csv',index=False)
    selected=accounts['stress'];sel_pre=period_metrics(selected,START,PREFINAL_END);sel_final=period_metrics(selected,PREFINAL_END,END)
    checks={'eligible_before_final':eligible,'final_2026h1_positive':sel_final['total_return']>0,'prefinal_uplift_ge_0_5pp':sel_pre['cagr']>=base_pre['cagr']+.005,'prefinal_dd_gt_minus25':sel_pre['max_drawdown']>=-.25,'prefinal_sharpe_ge1_25':sel_pre['sharpe']>=1.25,'all_audit_liquidations_zero':bool((audit_df.liquidations==0).all()),'all_margin_buffers_positive':float(audit_df.min_margin_buffer.min())>0,'severe_cagr_gt18':float(audit_df[audit_df.audit=='severe'].iloc[0].cagr)>.18,'extreme_cagr_gt10':float(audit_df[audit_df.audit=='extreme'].iloc[0].cagr)>.10,'delay_cagr_floor_gt15':float(audit_df[audit_df.audit.str.startswith('delay_')].cagr.min())>.15}
    status='frozen_historical_candidate_needs_nonzero_forward' if all(checks.values()) else 'rejected_or_needs_iteration'
    summary={'candidate':'ACTIVE_V88_REGIME_ADAPTIVE_ATLAS','audit':'ACTIVE_V89_V94_IMMUTABLE_AUDITS','status':status,'live_ready':False,'real_leverage_authorized':False,'selected_components':selected_names,'ensemble_policy':asdict(ensemble_policy),'checks':checks,'baseline_v75_full':base_full,'baseline_v75_prefinal':base_pre,'candidate_prefinal':sel_pre,'candidate_final_2026h1':sel_final,'audit_metrics':audit_df.to_dict(orient='records'),'selection_proof_sha256':proof['sha256'],'limitations':['No pristine program-level holdout.','The regime features and thresholds were evaluated on a history already known to the research program.','OHLC/funding margin simulation cannot model exchange outage, mark-price gaps, collateral freeze or counterparty failure.','A positive final is diagnostic only because 2026 H1 has been repeatedly observed by prior research.']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2,default=float))
    cy=yearly(selected).rename(columns={'return':'V88_candidate'});annual=base_yearly.merge(cy,on='year',how='outer');annual.to_csv(out/'annual_returns.csv',index=False)
    pd.DataFrame([{'candidate':'V75 original',**base_full},{'candidate':'V88 candidate',**account_metrics(selected)}]).to_csv(out/'candidate_comparison.csv',index=False)
    print(json.dumps(summary,indent=2,default=float))

if __name__=='__main__':main()
