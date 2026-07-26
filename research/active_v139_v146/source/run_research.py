from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests

# Active V139-V146: roll-aware global futures proxy research.
# Yahoo continuous futures are research proxies, not execution-grade contract chains.

START = pd.Timestamp('2007-01-01', tz='UTC')
END = pd.Timestamp('2026-07-01', tz='UTC')
SELECTION_END = pd.Timestamp('2021-01-01', tz='UTC')
BRIDGE_END = pd.Timestamp('2024-01-01', tz='UTC')
HOLDOUT_END = pd.Timestamp('2026-01-01', tz='UTC')

SEGMENTS = {
    'development_2010_2015': (pd.Timestamp('2010-01-01', tz='UTC'), pd.Timestamp('2016-01-01', tz='UTC')),
    'validation_2016_2018': (pd.Timestamp('2016-01-01', tz='UTC'), pd.Timestamp('2019-01-01', tz='UTC')),
    'validation_2019_2020': (pd.Timestamp('2019-01-01', tz='UTC'), pd.Timestamp('2021-01-01', tz='UTC')),
    'bridge_2021_2023': (pd.Timestamp('2021-01-01', tz='UTC'), pd.Timestamp('2024-01-01', tz='UTC')),
    'holdout_2024_2025': (pd.Timestamp('2024-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC')),
    'final_2026h1': (pd.Timestamp('2026-01-01', tz='UTC'), pd.Timestamp('2026-07-01', tz='UTC')),
    'full_2010_2026h1': (pd.Timestamp('2010-01-01', tz='UTC'), END),
}

UNIVERSE = {
    # Yahoo ticker: group. All are fixed before data inspection.
    'ES=F': 'equity', 'NQ=F': 'equity', 'RTY=F': 'equity',
    'ZN=F': 'rates', 'ZB=F': 'rates',
    'GC=F': 'metals', 'SI=F': 'metals',
    'CL=F': 'energy', 'NG=F': 'energy',
    '6E=F': 'fx', '6J=F': 'fx', '6B=F': 'fx', '6A=F': 'fx', '6C=F': 'fx', '6S=F': 'fx',
    'ZC=F': 'agri', 'ZW=F': 'agri', 'ZS=F': 'agri',
}

MICRO_EXECUTABLE = {
    # Research mapping only. Actual broker availability/margins must be checked live.
    'ES=F': 'MES', 'NQ=F': 'MNQ', 'RTY=F': 'M2K',
    'GC=F': 'MGC', 'CL=F': 'MCL',
    '6E=F': 'M6E', '6J=F': 'MJY', '6B=F': 'M6B', '6A=F': 'M6A', '6C=F': 'MCD', '6S=F': 'MSF',
}

GROUP_CAPS = {'equity': .35, 'rates': .30, 'metals': .20, 'energy': .20, 'fx': .30, 'agri': .20}
ROLL_FLOORS = {'equity': .025, 'rates': .015, 'metals': .035, 'energy': .06, 'fx': .018, 'agri': .06}


@dataclass(frozen=True)
class Policy:
    name: str
    family: str
    target_vol: float
    gross_cap: float
    rebalance_days: int
    no_trade_band: float
    universe: str = 'full'


@dataclass(frozen=True)
class Audit:
    name: str
    cost_bps: float
    roll_bps: float
    financing_rate: float
    initial_margin_ratio: float
    maintenance_margin_ratio: float
    operational_reserve: float
    execution_delay: int = 0


AUDITS = (
    Audit('base', 5.0, 2.0, .05, .12, .08, .20, 0),
    Audit('stress', 10.0, 5.0, .08, .15, .10, .22, 0),
    Audit('severe', 20.0, 10.0, .12, .20, .12, .25, 1),
    Audit('extreme', 40.0, 20.0, .16, .25, .15, .30, 2),
    Audit('delay_5', 10.0, 5.0, .08, .15, .10, .22, 5),
)

POLICIES = tuple(
    Policy(
        name=f'{family}_v{int(target*100)}_g{int(gross*100)}_r{reb}_b{int(band*100)}_{univ}',
        family=family,
        target_vol=target,
        gross_cap=gross,
        rebalance_days=reb,
        no_trade_band=band,
        universe=univ,
    )
    for family in ('slow_trend', 'medium_trend', 'breakout', 'ensemble')
    for target in (.10, .12, .15)
    for gross in (1.0, 1.25)
    for reb in (5, 10)
    for band in (.02, .05)
    for univ in ('full', 'micro')
)


def utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def yahoo_url(symbol: str) -> str:
    p1 = int(START.timestamp())
    p2 = int(END.timestamp())
    encoded = urllib.parse.quote(symbol, safe='')
    return (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{encoded}'
        f'?period1={p1}&period2={p2}&interval=1d&events=history&includeAdjustedClose=true'
    )


def fetch_symbol(symbol: str, cache: Path) -> tuple[pd.DataFrame, dict]:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f'{urllib.parse.quote(symbol, safe="")}.json'
    if path.exists():
        raw = path.read_bytes()
    else:
        response = requests.get(yahoo_url(symbol), headers={'User-Agent': 'fin-research/1.0'}, timeout=45)
        response.raise_for_status()
        raw = response.content
        path.write_bytes(raw)
        time.sleep(.15)
    payload = json.loads(raw)
    chart = payload.get('chart', {})
    if chart.get('error'):
        raise RuntimeError(f'{symbol}: {chart["error"]}')
    result = (chart.get('result') or [None])[0]
    if not result or not result.get('timestamp'):
        raise RuntimeError(f'{symbol}: no timestamps')
    idx = pd.to_datetime(result['timestamp'], unit='s', utc=True).normalize()
    quote = result['indicators']['quote'][0]
    frame = pd.DataFrame(
        {name: quote.get(name, [None] * len(idx)) for name in ('open', 'high', 'low', 'close', 'volume')},
        index=idx,
    )
    frame = frame[~frame.index.duplicated(keep='last')].sort_index()
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame[['open', 'high', 'low', 'close']] = frame[['open', 'high', 'low', 'close']].astype(float)
    frame = frame[(frame[['open', 'high', 'low', 'close']] > 0).all(axis=1)]
    meta = {
        'symbol': symbol,
        'group': UNIVERSE[symbol],
        'raw_file': path.name,
        'raw_bytes': len(raw),
        'raw_sha256': sha256_bytes(raw),
        'rows': int(len(frame)),
        'start': frame.index.min().isoformat() if len(frame) else None,
        'end': frame.index.max().isoformat() if len(frame) else None,
    }
    return frame, meta


def load_market(cache: Path, output: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    quality = []
    for symbol in UNIVERSE:
        try:
            frame, meta = fetch_symbol(symbol, cache)
            frames[symbol] = frame
            meta['status'] = 'ok'
        except Exception as exc:
            meta = {'symbol': symbol, 'group': UNIVERSE[symbol], 'status': 'unavailable', 'error': repr(exc), 'rows': 0}
        quality.append(meta)
    if len(frames) < 8:
        raise RuntimeError(f'insufficient futures proxies: {len(frames)}')
    q = pd.DataFrame(quality).sort_values('symbol')
    q.to_csv(output / 'data_quality.csv', index=False)
    return frames, q


def panels(frames: dict[str, pd.DataFrame]) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    index = pd.DatetimeIndex(sorted(set().union(*(set(f.index) for f in frames.values()))))
    index = index[(index >= START) & (index < END)]
    out = {}
    for field in ('open', 'high', 'low', 'close'):
        out[field] = pd.DataFrame({s: f[field].reindex(index) for s, f in frames.items()}, index=index)
    return index, out


def third_friday(ts: pd.Timestamp) -> pd.Timestamp:
    first = pd.Timestamp(year=ts.year, month=ts.month, day=1, tz='UTC')
    fridays = pd.date_range(first, first + pd.offsets.MonthEnd(0), freq='W-FRI', tz='UTC')
    return fridays[2]


def roll_window(index: pd.DatetimeIndex, group: str) -> np.ndarray:
    flags = np.zeros(len(index), dtype=bool)
    for i, ts in enumerate(index):
        if group in {'equity', 'rates', 'fx'}:
            if ts.month not in (3, 6, 9, 12):
                continue
            expiry = third_friday(ts)
            flags[i] = abs((ts - expiry).days) <= 8
        else:
            flags[i] = ts.day >= 18
    return flags


def detect_rolls(index: pd.DatetimeIndex, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    op, cl = data['open'], data['close']
    gap = np.log(op / cl.shift(1)).abs()
    recent = gap.rolling(63, min_periods=20).median().shift(1)
    result = pd.DataFrame(False, index=index, columns=cl.columns)
    for symbol in cl.columns:
        group = UNIVERSE[symbol]
        threshold = np.maximum(ROLL_FLOORS[group], (recent[symbol] * 5).fillna(ROLL_FLOORS[group]))
        result[symbol] = roll_window(index, group) & gap[symbol].gt(threshold)
    return result


def donchian_state(close: pd.DataFrame, entry: int, exit_: int) -> pd.DataFrame:
    high = close.rolling(entry, min_periods=entry).max().shift(1)
    low = close.rolling(entry, min_periods=entry).min().shift(1)
    exit_high = close.rolling(exit_, min_periods=exit_).max().shift(1)
    exit_low = close.rolling(exit_, min_periods=exit_).min().shift(1)
    state = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for j in range(close.shape[1]):
        current = 0.0
        values = []
        for i in range(len(close)):
            price = close.iat[i, j]
            if not np.isfinite(price):
                current = 0.0
            elif np.isfinite(high.iat[i, j]) and price > high.iat[i, j]:
                current = 1.0
            elif np.isfinite(low.iat[i, j]) and price < low.iat[i, j]:
                current = -1.0
            elif current > 0 and np.isfinite(exit_low.iat[i, j]) and price < exit_low.iat[i, j]:
                current = 0.0
            elif current < 0 and np.isfinite(exit_high.iat[i, j]) and price > exit_high.iat[i, j]:
                current = 0.0
            values.append(current)
        state.iloc[:, j] = values
    return state


def build_signals(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    r63 = close.pct_change(63, fill_method=None)
    r126 = close.pct_change(126, fill_method=None)
    r252 = close.pct_change(252, fill_method=None)
    e100 = close.ewm(span=100, adjust=False, min_periods=100).mean()
    e200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    slow = (np.sign(r126) + np.sign(r252) + np.sign(close / e200 - 1.0)) / 3.0
    medium = (np.sign(r63) + np.sign(r126) + np.sign(close / e100 - 1.0)) / 3.0
    breakout = donchian_state(close, 126, 63)
    ensemble = (slow + medium + breakout) / 3.0
    return {
        'slow_trend': slow.shift(1).fillna(0.0),
        'medium_trend': medium.shift(1).fillna(0.0),
        'breakout': breakout.shift(1).fillna(0.0),
        'ensemble': ensemble.shift(1).fillna(0.0),
    }


def available_symbols(close: pd.DataFrame, policy: Policy) -> list[str]:
    base = list(close.columns)
    if policy.universe == 'micro':
        base = [s for s in base if s in MICRO_EXECUTABLE]
    # Require enough pre-2021 history; rule is fixed before post-2020 evaluation.
    pre = close.loc[(close.index >= pd.Timestamp('2010-01-01', tz='UTC')) & (close.index < SELECTION_END), base]
    return [s for s in base if pre[s].notna().mean() >= .65 and pre[s].notna().sum() >= 750]


def target_weights(
    policy: Policy,
    signal: pd.DataFrame,
    close: pd.DataFrame,
) -> pd.DataFrame:
    symbols = available_symbols(close, policy)
    rets = close[symbols].pct_change(fill_method=None)
    vol = rets.rolling(63, min_periods=32).std(ddof=1).shift(1) * np.sqrt(252.0)
    out = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    current = pd.Series(0.0, index=symbols)
    for i, ts in enumerate(close.index):
        if i % policy.rebalance_days != 0:
            out.loc[ts, symbols] = current
            continue
        s = signal.loc[ts, symbols].where(close.loc[ts, symbols].notna(), 0.0).fillna(0.0)
        v = vol.loc[ts, symbols].replace(0, np.nan)
        raw = (s / v.clip(lower=.06)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if raw.abs().sum() == 0:
            candidate = raw
        else:
            candidate = raw / raw.abs().sum()
            # Apply group caps iteratively.
            for group, cap in GROUP_CAPS.items():
                members = [x for x in symbols if UNIVERSE[x] == group]
                g = candidate[members].abs().sum()
                if g > cap and g > 0:
                    candidate.loc[members] *= cap / g
            # Conservative causal portfolio-vol proxy with a fixed 0.25 correlation floor.
            # This avoids estimating a noisy covariance matrix for every grid point.
            risk = (candidate.abs() * v.fillna(np.inf)).replace([np.inf, -np.inf], 0.0)
            independent = float((risk ** 2).sum())
            common = float(risk.sum() ** 2)
            pvol = math.sqrt(max(.75 * independent + .25 * common, 0.0))
            scale = 1.0
            gross = float(candidate.abs().sum())
            if gross > 0:
                scale = min(scale, policy.gross_cap / gross)
            if np.isfinite(pvol) and pvol > 0:
                scale = min(scale, policy.target_vol / pvol)
            candidate *= max(0.0, scale)
        # No-trade region is evaluated at scheduled decision points.
        if float((candidate - current).abs().sum()) < policy.no_trade_band:
            candidate = current.copy()
        current = candidate
        out.loc[ts, symbols] = current
    return out


def shift_array(arr: np.ndarray, delay: int) -> np.ndarray:
    if delay <= 0:
        return arr.copy()
    out = np.zeros_like(arr)
    out[delay:] = arr[:-delay]
    return out


def simulate(
    policy: Policy,
    audit: Audit,
    index: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    rolls: pd.DataFrame,
    target: pd.DataFrame,
) -> pd.DataFrame:
    symbols = list(data['close'].columns)
    op, hi, lo, cl = [data[x][symbols].to_numpy(float) for x in ('open', 'high', 'low', 'close')]
    roll = rolls[symbols].to_numpy(bool)
    tw = shift_array(target[symbols].to_numpy(float), audit.execution_delay)
    cash = 10000.0
    notional = np.zeros(len(symbols), dtype=float)
    records = []
    prev = None
    cost_rate = audit.cost_bps / 10000.0
    roll_rate = audit.roll_bps / 10000.0
    for i, ts in enumerate(index):
        roll_cost = 0.0
        if prev is not None:
            valid = np.isfinite(op[i]) & np.isfinite(cl[prev]) & (cl[prev] > 0)
            overnight = np.divide(op[i], cl[prev], out=np.ones(len(symbols)), where=valid) - 1.0
            # A suspected continuous-series roll gap is neutralized and replaced by explicit roll cost.
            overnight = np.where(roll[i], 0.0, overnight)
            cash += float(np.sum(notional * overnight))
            notional *= 1.0 + overnight
            roll_cost = float(np.sum(np.abs(notional[roll[i]]))) * roll_rate
            cash -= roll_cost
        equity_open = max(cash, 1e-9)
        actual = notional / equity_open
        desired = tw[i].copy()
        desired[~np.isfinite(op[i])] = 0.0
        gross = float(np.abs(desired).sum())
        if gross > policy.gross_cap and gross > 0:
            desired *= policy.gross_cap / gross
        # Collateral feasibility: operational reserve + initial margin must fit equity.
        required = audit.operational_reserve + audit.initial_margin_ratio * float(np.abs(desired).sum())
        if required > 1.0:
            desired *= max(0.0, (1.0 - audit.operational_reserve) / (audit.initial_margin_ratio * max(float(np.abs(desired).sum()), 1e-12)))
        turnover = float(np.abs(desired - actual).sum())
        trading_cost = equity_open * turnover * cost_rate
        cash -= trading_cost
        equity_after = max(cash, 1e-9)
        notional = desired * equity_after
        gross_open = float(np.abs(desired).sum())
        cash -= equity_after * max(0.0, gross_open - 1.0) * audit.financing_rate / 252.0
        valid = np.isfinite(cl[i]) & np.isfinite(op[i]) & (op[i] > 0)
        intraday = np.divide(cl[i], op[i], out=np.ones(len(symbols)), where=valid) - 1.0
        cash += float(np.sum(notional * intraday))
        notional *= 1.0 + intraday
        # Conservative daily high/low margin check.
        adverse_price = np.where(notional >= 0, lo[i], hi[i])
        adverse_ret = np.divide(adverse_price, cl[i], out=np.ones(len(symbols)), where=np.isfinite(adverse_price) & np.isfinite(cl[i]) & (cl[i] > 0)) - 1.0
        adverse_pnl = float(np.sum(notional * adverse_ret))
        maintenance = audit.maintenance_margin_ratio * float(np.abs(notional * (1.0 + adverse_ret)).sum())
        margin_buffer = (cash + adverse_pnl - maintenance) / max(cash, 1e-9)
        liquidated = 0.0
        liquidation_penalty = 0.0
        if margin_buffer < 0 and np.any(notional):
            liquidated = float(np.abs(notional).sum())
            liquidation_penalty = .01 * liquidated
            cash -= liquidation_penalty
            notional[:] = 0.0
        equity = max(cash, 1e-9)
        records.append({
            'equity': equity,
            'gross': float(np.abs(notional).sum() / equity),
            'net': float(notional.sum() / equity),
            'turnover': turnover,
            'trading_costs': trading_cost,
            'roll_costs': roll_cost,
            'liquidation_penalty': liquidation_penalty,
            'liquidated_notional': liquidated,
            'min_margin_buffer': margin_buffer,
        })
        prev = i
    return pd.DataFrame(records, index=index)


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1 / 365.25
    return max((index[-1] - index[0]).total_seconds() / (365.25 * 86400), 1 / 365.25)


def metrics(account: pd.DataFrame, start=None, end=None) -> dict:
    x = account
    if start is not None:
        x = x[(x.index >= utc(start)) & (x.index < utc(end))]
    if x.empty:
        return {k: 0.0 for k in ('total_return', 'annualized_return', 'max_drawdown', 'sharpe', 'annual_turnover')}
    eq = x['equity'].astype(float)
    scale = 10000.0 / eq.iloc[0]
    eq = eq * scale
    r = eq.pct_change().fillna(eq.iloc[0] / 10000.0 - 1.0)
    years = elapsed_years(eq.index)
    observations_per_year = len(eq) / years
    sd = r.std(ddof=1)
    dd = eq / eq.cummax() - 1.0
    return {
        'total_return': float(eq.iloc[-1] / 10000.0 - 1.0),
        'annualized_return': float((eq.iloc[-1] / 10000.0) ** (1.0 / years) - 1.0),
        'max_drawdown': float(dd.min()),
        'sharpe': float(r.mean() / sd * math.sqrt(observations_per_year)) if sd > 0 else 0.0,
        'annual_turnover': float(x['turnover'].sum() / years),
        'average_gross': float(x['gross'].mean()),
        'max_gross': float(x['gross'].max()),
        'costs': float(sum(float(x[c].sum()) for c in ('trading_costs', 'roll_costs', 'liquidation_penalty') if c in x)),
        'liquidations': int((x['liquidated_notional'] > 0).sum()) if 'liquidated_notional' in x else 0,
        'min_margin_buffer': float(x['min_margin_buffer'].min()) if 'min_margin_buffer' in x else 1.0,
        'observations_per_year': float(observations_per_year),
    }


def yearly(account: pd.DataFrame, label: str) -> pd.DataFrame:
    r = account.equity.pct_change().fillna(account.equity.iloc[0] / 10000.0 - 1.0)
    return pd.DataFrame({'year': sorted(r.index.year.unique()), label: [float((1 + g).prod() - 1) for _, g in r.groupby(r.index.year)]})


def score(row: pd.Series) -> float:
    return float(
        2.0 * row['prefinal_cagr'] + .7 * row['prefinal_sharpe'] + .5 * row['worst_validation_return']
        + .5 * row['prefinal_max_drawdown'] - .015 * row['annual_turnover']
    )


def select_processes(
    policies: Iterable[Policy], signals: dict[str, pd.DataFrame], close: pd.DataFrame,
    index: pd.DatetimeIndex, data: dict[str, pd.DataFrame], rolls: pd.DataFrame, output: Path,
) -> tuple[list[Policy], pd.DataFrame, dict[str, pd.DataFrame]]:
    rows = []
    base_accounts: dict[str, pd.DataFrame] = {}
    base_audit = AUDITS[0]
    stress_audit = AUDITS[1]
    for n, policy in enumerate(policies, 1):
        target = target_weights(policy, signals[policy.family], close)
        base = simulate(policy, base_audit, index, data, rolls, target)
        stress = simulate(policy, stress_audit, index, data, rolls, target)
        base_accounts[policy.name] = base
        pre = metrics(base, '2010-01-01', '2021-01-01')
        val1 = metrics(base, *SEGMENTS['validation_2016_2018'])
        val2 = metrics(base, *SEGMENTS['validation_2019_2020'])
        stress_pre = metrics(stress, '2010-01-01', '2021-01-01')
        eligible = bool(
            pre['annualized_return'] >= .05 and pre['sharpe'] >= .60 and pre['max_drawdown'] >= -.20
            and pre['annual_turnover'] <= 15.0 and val1['total_return'] > 0 and val2['total_return'] > 0
            and stress_pre['annualized_return'] > 0 and stress_pre['max_drawdown'] >= -.25
            and pre['liquidations'] == 0 and pre['min_margin_buffer'] > 0
        )
        row = {
            'policy': policy.name, 'family': policy.family, 'universe': policy.universe,
            'target_vol': policy.target_vol, 'gross_cap': policy.gross_cap,
            'rebalance_days': policy.rebalance_days, 'no_trade_band': policy.no_trade_band,
            'eligible_before_2021': eligible,
            'prefinal_cagr': pre['annualized_return'], 'prefinal_sharpe': pre['sharpe'],
            'prefinal_max_drawdown': pre['max_drawdown'], 'annual_turnover': pre['annual_turnover'],
            'validation_2016_2018_return': val1['total_return'], 'validation_2019_2020_return': val2['total_return'],
            'worst_validation_return': min(val1['total_return'], val2['total_return']),
            'stress_prefinal_cagr': stress_pre['annualized_return'], 'stress_prefinal_dd': stress_pre['max_drawdown'],
        }
        row['score'] = score(pd.Series(row))
        rows.append(row)
        if n % 20 == 0:
            print(f'processed {n}/{len(tuple(policies))} policies', flush=True)
    ranking = pd.DataFrame(rows).sort_values(['eligible_before_2021', 'score'], ascending=False)
    ranking.to_csv(output / 'selection_ranking_before_2021.csv', index=False)
    eligible = ranking[ranking.eligible_before_2021]
    if eligible.empty:
        selected_names = ranking.head(3).policy.tolist()
    else:
        # Neighbor ensemble: top process plus up to two candidates from different families/universes.
        selected_names = []
        for _, row in eligible.iterrows():
            if not selected_names or all(row.family != ranking.set_index('policy').loc[x, 'family'] for x in selected_names):
                selected_names.append(str(row.policy))
            if len(selected_names) == 3:
                break
        for name in eligible.policy:
            if len(selected_names) == 3:
                break
            if name not in selected_names:
                selected_names.append(str(name))
    by_name = {p.name: p for p in policies}
    selected = [by_name[x] for x in selected_names]
    proof = {
        'candidate': 'ACTIVE_V139_V146_GLOBAL_FUTURES_PROXY',
        'selection_cutoff': '2020-12-31',
        'selection_uses_2021_or_later': False,
        'universe_fixed_before_fetch': list(UNIVERSE),
        'policy_count': len(tuple(policies)),
        'gates': {
            'prefinal_cagr_min': .05, 'prefinal_sharpe_min': .60, 'prefinal_dd_min': -.20,
            'turnover_max': 15.0, 'both_validations_positive': True,
            'stress_prefinal_positive': True, 'stress_dd_min': -.25,
            'zero_liquidations': True, 'positive_margin_buffer': True,
        },
        'selected': selected_names,
        'selected_all_eligible': bool(not eligible.empty and all(ranking.set_index('policy').loc[x, 'eligible_before_2021'] for x in selected_names)),
        'ranking_sha256': sha256_bytes(ranking.to_csv(index=False).encode()),
    }
    proof_path = output / 'selection_proof_before_2021.json'
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
    proof['selection_proof_sha256'] = sha256_bytes(proof_path.read_bytes())
    return selected, ranking, base_accounts


def ensemble_target(selected: list[Policy], signals: dict[str, pd.DataFrame], close: pd.DataFrame) -> pd.DataFrame:
    targets = [target_weights(p, signals[p.family], close) for p in selected]
    result = sum(targets) / len(targets)
    cap = min(p.gross_cap for p in selected)
    gross = result.abs().sum(axis=1)
    scale = (cap / gross).clip(upper=1.0).fillna(1.0)
    return result.mul(scale, axis=0)


def combine_separate_accounts(atlas: pd.DataFrame, sleeve: pd.DataFrame, weight: float) -> pd.DataFrame:
    # Each sleeve compounds independently. Missing futures days have zero sleeve return.
    idx = atlas.index.union(sleeve.index).sort_values()
    ar = atlas.equity.pct_change().fillna(atlas.equity.iloc[0] / 10000.0 - 1.0).reindex(idx).fillna(0.0)
    sr = sleeve.equity.pct_change().fillna(sleeve.equity.iloc[0] / 10000.0 - 1.0).reindex(idx).fillna(0.0)
    aeq = (10000.0 * (1 - weight)) * (1 + ar).cumprod()
    seq = (10000.0 * weight) * (1 + sr).cumprod()
    eq = aeq + seq
    out = pd.DataFrame({'equity': eq}, index=idx)
    out['gross'] = 0.0
    out['turnover'] = 0.0
    out['min_margin_buffer'] = 1.0
    return out


def dynamic_combine(atlas: pd.DataFrame, sleeve: pd.DataFrame, base_weight: float = .20, transfer_bps: float = 10.0) -> pd.DataFrame:
    idx = atlas.index.union(sleeve.index).sort_values()
    ar = atlas.equity.pct_change().fillna(atlas.equity.iloc[0] / 10000.0 - 1.0).reindex(idx).fillna(0.0)
    sr = sleeve.equity.pct_change().fillna(sleeve.equity.iloc[0] / 10000.0 - 1.0).reindex(idx).fillna(0.0)
    corr = ar.rolling(126, min_periods=63).corr(sr).shift(1)
    svol = sr.rolling(63, min_periods=32).std(ddof=1).shift(1) * np.sqrt(365.25)
    desired = pd.Series(base_weight, index=idx)
    desired = desired.where(corr > .20, .30).where(corr < .50, .10)
    desired = desired.where(svol < .18, desired.clip(upper=.15)).fillna(base_weight)
    aeq, seq = 8000.0, 2000.0
    records = []
    last_month = None
    for ts in idx:
        aeq *= 1 + ar.loc[ts]
        seq *= 1 + sr.loc[ts]
        total = aeq + seq
        turnover = 0.0
        month = (ts.year, ts.month)
        if month != last_month:
            w = float(desired.loc[ts])
            current = seq / max(total, 1e-12)
            transfer = abs(w - current) * total
            cost = transfer * transfer_bps / 10000.0
            total -= cost
            seq = w * total
            aeq = (1 - w) * total
            turnover = abs(w - current)
            last_month = month
        records.append({'equity': aeq + seq, 'gross': 0.0, 'turnover': turnover, 'min_margin_buffer': 1.0, 'sleeve_weight': seq / max(aeq + seq, 1e-12)})
    return pd.DataFrame(records, index=idx)


def low_corr_leverage(combined: pd.DataFrame, atlas: pd.DataFrame, sleeve: pd.DataFrame, financing_rate: float = .08) -> pd.DataFrame:
    idx = combined.index
    ar = atlas.equity.pct_change().reindex(idx).fillna(0.0)
    sr = sleeve.equity.pct_change().reindex(idx).fillna(0.0)
    corr = ar.rolling(126, min_periods=63).corr(sr).shift(1)
    base_r = combined.equity.pct_change().fillna(combined.equity.iloc[0] / 10000.0 - 1.0)
    scale = pd.Series(1.0, index=idx)
    scale[(corr < .20) & (ar.rolling(126).sum().shift(1) > 0) & (sr.rolling(126).sum().shift(1) > 0)] = 1.10
    adj = base_r * scale - (scale - 1.0) * financing_rate / 365.25
    eq = 10000.0 * (1 + adj).cumprod()
    out = pd.DataFrame({'equity': eq, 'gross': scale, 'turnover': scale.diff().abs().fillna(scale.iloc[0] - 1.0), 'min_margin_buffer': 1.0}, index=idx)
    return out


def bootstrap(account: pd.DataFrame, output: Path, seed: int = 20260726) -> pd.DataFrame:
    r = account.equity.pct_change().dropna().to_numpy(float)
    rng = np.random.default_rng(seed)
    rows = []
    for block in (10, 21, 63):
        for horizon in (252, 504):
            samples = []
            dds = []
            for _ in range(3000):
                seq = []
                while len(seq) < horizon:
                    start = int(rng.integers(0, max(1, len(r) - block)))
                    seq.extend(r[start:start + block])
                x = np.asarray(seq[:horizon])
                eq = np.cumprod(1 + x)
                samples.append(eq[-1] - 1)
                dds.append(float(np.min(eq / np.maximum.accumulate(eq) - 1)))
            a = np.asarray(samples); d = np.asarray(dds)
            rows.append({
                'block_days': block, 'horizon_days': horizon,
                'median_return': float(np.median(a)), 'p05_return': float(np.quantile(a, .05)),
                'prob_positive': float((a > 0).mean()), 'median_max_drawdown': float(np.median(d)),
                'p05_max_drawdown': float(np.quantile(d, .05)),
            })
    frame = pd.DataFrame(rows)
    frame.to_csv(output / 'block_bootstrap.csv', index=False)
    return frame


def self_test() -> None:
    idx = pd.date_range('2020-01-01', periods=500, freq='B', tz='UTC')
    rng = np.random.default_rng(7)
    close = pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(.0001, .01, len(idx)))) for s in list(UNIVERSE)[:8]}, index=idx)
    data = {'close': close, 'open': close.shift(1).fillna(close.iloc[0]), 'high': close * 1.01, 'low': close * .99}
    sig = build_signals(close)
    p = Policy('test', 'ensemble', .12, 1.0, 5, .02, 'full')
    target = target_weights(p, sig[p.family], close)
    assert target.index.equals(idx)
    assert float(target.abs().sum(axis=1).max()) <= 1.0000001
    rolls = detect_rolls(idx, data)
    account = simulate(p, AUDITS[0], idx, data, rolls, target)
    assert len(account) == len(idx) and account.equity.gt(0).all()
    changed = close.copy(); changed.iloc[-1] *= 10
    sig2 = build_signals(changed); target2 = target_weights(p, sig2[p.family], changed)
    pd.testing.assert_frame_equal(target.iloc[:-1], target2.iloc[:-1])
    atlas = pd.DataFrame({'equity': 10000 * np.cumprod(1 + rng.normal(.0002, .01, len(idx)))}, index=idx)
    combo = combine_separate_accounts(atlas, account, .2)
    assert len(combo) == len(idx)
    print('self-test passed')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=Path('.cache/v139_v146'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/active_v139_v146'))
    parser.add_argument('--atlas', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if args.atlas is None or not args.atlas.exists():
        raise SystemExit('--atlas V75 equity CSV is required')
    args.output.mkdir(parents=True, exist_ok=True)
    frames, quality = load_market(args.cache / 'yahoo', args.output)
    index, data = panels(frames)
    close = data['close']
    rolls = detect_rolls(index, data)
    roll_rows = []
    for symbol in rolls:
        for ts in rolls.index[rolls[symbol]]:
            roll_rows.append({'date': ts.isoformat(), 'symbol': symbol, 'group': UNIVERSE[symbol]})
    pd.DataFrame(roll_rows).to_csv(args.output / 'detected_roll_events.csv', index=False)
    signals = build_signals(close)
    selected, ranking, _ = select_processes(POLICIES, signals, close, index, data, rolls, args.output)
    proof = json.loads((args.output / 'selection_proof_before_2021.json').read_text())
    target = ensemble_target(selected, signals, close)
    selected_policy = Policy('selected_ensemble', 'ensemble', min(p.target_vol for p in selected), min(p.gross_cap for p in selected), 5, .02, 'full')
    scenario_accounts = {}
    scenario_rows = []
    for audit in AUDITS:
        account = simulate(selected_policy, audit, index, data, rolls, target)
        scenario_accounts[audit.name] = account
        for period, bounds in SEGMENTS.items():
            scenario_rows.append({'scenario': audit.name, 'period': period, **metrics(account, *bounds)})
    scenarios = pd.DataFrame(scenario_rows)
    scenarios.to_csv(args.output / 'selected_sleeve_metrics.csv', index=False)
    base = scenario_accounts['base']
    base.to_csv(args.output / 'v142_selected_futures_equity.csv')
    futures_yearly = yearly(base, 'V142_futures_proxy')

    atlas = pd.read_csv(args.atlas, index_col=0, parse_dates=True)
    atlas.index = pd.to_datetime(atlas.index, utc=True)
    atlas = atlas[['equity']].sort_index()
    atlas = atlas[(atlas.index >= pd.Timestamp('2021-01-01', tz='UTC')) & (atlas.index < END)]
    # Integration is permitted only when standalone selection gates passed before 2021.
    standalone_pass = bool(proof['selected_all_eligible'])
    integration_rows = []
    combinations = {'V75_original': atlas}
    selected_weight = 0.0
    if standalone_pass:
        weight_rank = []
        for w in (.10, .20, .30, .40):
            c = combine_separate_accounts(atlas, base, w)
            m = metrics(c, '2021-01-01', '2024-01-01')
            weight_rank.append({'weight': w, **m})
        wr = pd.DataFrame(weight_rank).sort_values(['sharpe', 'annualized_return'], ascending=False)
        wr.to_csv(args.output / 'integration_weight_selection_2021_2023.csv', index=False)
        selected_weight = float(wr.iloc[0].weight)
        static = combine_separate_accounts(atlas, base, selected_weight)
        dynamic = dynamic_combine(atlas, base, min(.20, selected_weight or .20))
        leveraged = low_corr_leverage(dynamic, atlas, base)
        combinations.update({'V143_static': static, 'V144_dynamic': dynamic, 'V145_low_corr_leverage': leveraged})
    else:
        pd.DataFrame(columns=['weight']).to_csv(args.output / 'integration_weight_selection_2021_2023.csv', index=False)

    for name, account in combinations.items():
        account.to_csv(args.output / f'{name}_equity.csv')
        for period, bounds in {k: v for k, v in SEGMENTS.items() if k in ('bridge_2021_2023', 'holdout_2024_2025', 'final_2026h1')}.items():
            integration_rows.append({'candidate': name, 'period': period, **metrics(account, *bounds)})
        integration_rows.append({'candidate': name, 'period': 'full_2021_2026h1', **metrics(account)})
    integration = pd.DataFrame(integration_rows)
    integration.to_csv(args.output / 'integration_metrics.csv', index=False)

    annual = yearly(atlas, 'V75_original')
    annual = annual.merge(futures_yearly, on='year', how='outer')
    for name, account in combinations.items():
        if name == 'V75_original':
            continue
        annual = annual.merge(yearly(account, name), on='year', how='outer')
    annual.sort_values('year').to_csv(args.output / 'ANNUAL_RETURNS.csv', index=False)

    promoted = []
    if standalone_pass:
        sleeve_bridge = metrics(base, *SEGMENTS['bridge_2021_2023'])
        sleeve_hold = metrics(base, *SEGMENTS['holdout_2024_2025'])
        sleeve_final = metrics(base, *SEGMENTS['final_2026h1'])
        if sleeve_bridge['total_return'] > 0 and sleeve_hold['total_return'] > 0 and sleeve_final['total_return'] > 0:
            for name in ('V143_static', 'V144_dynamic'):
                z = integration[integration.candidate == name].set_index('period')
                a = integration[integration.candidate == 'V75_original'].set_index('period')
                full = z.loc['full_2021_2026h1']; af = a.loc['full_2021_2026h1']
                hold = z.loc['holdout_2024_2025']; final = z.loc['final_2026h1']
                if (
                    hold.total_return > 0 and final.total_return > 0
                    and full.annualized_return >= af.annualized_return - .02
                    and (full.max_drawdown >= af.max_drawdown + .01 or full.sharpe >= af.sharpe + .05)
                ):
                    promoted.append(name)
    bootstrap_target = combinations[promoted[0]] if promoted else atlas
    boot = bootstrap(bootstrap_target, args.output)
    concentration = yearly(bootstrap_target, 'return')
    positive = np.log1p(concentration['return'].clip(lower=-.999999))
    positive = positive[positive > 0]
    best_share = float(positive.max() / positive.sum()) if len(positive) else 0.0

    summary = {
        'candidate': 'ACTIVE_V139_V146_GLOBAL_FUTURES_PROXY',
        'status': 'historical_candidate' if promoted else 'rejected_or_needs_iteration',
        'promoted_candidates': promoted,
        'selected_futures_processes': [asdict(p) for p in selected],
        'standalone_selection_passed': standalone_pass,
        'selected_integration_weight': selected_weight,
        'selection_proof_sha256': proof.get('selection_proof_sha256'),
        'data_source': 'Yahoo continuous front-month futures proxies',
        'execution_grade': False,
        'roll_model': 'calendar-window large-gap neutralization plus explicit roll cost',
        'live_ready': False,
        'real_leverage_authorized': False,
        'base_sleeve_full': metrics(base, *SEGMENTS['full_2010_2026h1']),
        'base_sleeve_bridge': metrics(base, *SEGMENTS['bridge_2021_2023']),
        'base_sleeve_holdout': metrics(base, *SEGMENTS['holdout_2024_2025']),
        'base_sleeve_final_2026h1': metrics(base, *SEGMENTS['final_2026h1']),
        'original_v75_full': metrics(atlas),
        'best_year_positive_log_share': best_share,
        'evidence_limits': {
            'program_level_holdout_pristine': False,
            'actual_contract_chain_and_rolls': False,
            'broker_bid_ask_and_margin': False,
            'Yahoo_continuous_series_is_research_proxy': True,
        },
    }
    if promoted:
        summary['promoted_full'] = metrics(combinations[promoted[0]])
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (args.output / 'FROZEN_DECISION.json').write_text(json.dumps({
        'checkpoint': 'V146', 'date': '2026-07-26', 'decision': summary['status'],
        'promoted_candidates': promoted, 'live_ready': False, 'real_leverage_authorized': False,
        'next_action': 'Replace continuous proxies with dated contract chains, real roll schedules, bid/ask and broker margin before any live decision.',
    }, indent=2) + '\n')
    provenance = {
        'raw_files': quality.to_dict(orient='records'),
        'universe': UNIVERSE,
        'micro_mapping': MICRO_EXECUTABLE,
        'source_script_sha256': sha256_bytes(Path(__file__).read_bytes()),
        'atlas_input': {'path': str(args.atlas), 'bytes': args.atlas.stat().st_size, 'sha256': sha256_bytes(args.atlas.read_bytes())},
    }
    (args.output / 'DATA_PROVENANCE.json').write_text(json.dumps(provenance, indent=2) + '\n')

    report = [
        '# Active V139–V146: Global Futures Proxy', '',
        'Исследование проверяет самостоятельный global managed-futures sleeve и его guarded-интеграцию с исходной V75 ATLAS-NX.', '',
        '## Решение', '',
        f"- standalone selection passed: `{standalone_pass}`;",
        f"- promoted candidates: `{promoted}`;",
        '- `live_ready = false`;', '- `real_leverage_authorized = false`.', '',
        'Yahoo continuous futures используются только как исторический proxy. До dated-contract chain, реальных rolls, bid/ask и broker margin результат не является execution-grade.', '',
        '## Исходная годовая доходность', '',
        annual.to_markdown(index=False), '',
        '## Дальнейший шаг', '',
        'Заменить continuous proxies на dated futures contracts и повторить exact next-open/roll simulation без изменения уже выбранных signal families.',
    ]
    (args.output / 'REPORT_RU.md').write_text('\n'.join(report) + '\n')
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
