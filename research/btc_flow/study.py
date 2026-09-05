"""Reproducible, explicitly approximate BTC minute-bar research (not a live bot).

python -m research.btc_flow.study --data /path/to/btc-evidence --out /path/to/report
See protocol.json for the precommitted selection procedure. No parameter fitting
on the final period, no synthetic market history and no assumed maker fills.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import platform
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

COLS = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_volume', 'count', 'taker_buy_volume', 'taker_buy_quote_volume', 'ignore']
TRADE_COLS = ['entry_ms', 'exit_ms', 'side', 'entry', 'exit', 'qty', 'gross',
              'fees', 'funding_cost', 'net', 'reason']

@dataclass(frozen=True)
class Config:
    vwap: int = 20
    hold: int = 8
    rr: float = 2.0
    flow: bool = True

    @property
    def id(self) -> str:
        return f'v{self.vwap}_h{self.hold}_rr{self.rr:g}_flow{int(self.flow)}'

@dataclass(frozen=True)
class Costs:
    entry: float = 0.0005
    exit: float = 0.0005
    slip: float = 0.0001

@dataclass(frozen=True)
class Risk:
    fraction: float = 0.001
    exposure: float = 1.0
    daily: float = 1.0  # 1 disables the research halt, NOT the protective stop
    drawdown: float = 1.0


def load_data(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Reject missing months/checksums/duplicates; never fill gaps or funding."""
    manifest = json.loads((root / 'manifest.json').read_text())
    expected_records = {(kind, f'{year}-{month:02d}')
                        for year in range(2022,2027) for month in range(1,13)
                        if year<2026 or month<=7 for kind in ('klines','fundingRate')}
    actual_records = [(x['kind'],x['period']) for x in manifest]
    if len(actual_records)!=110 or set(actual_records)!=expected_records:
        raise ValueError('Manifest does not cover all 55 months of bars and funding')
    frames, funding = [], []
    for record in manifest:
        if record['status'] != 'verified':
            raise ValueError(f'Missing source: {record}')
        path = root / record['filename']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError(f'Checksum mismatch: {path}')
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) != 1 or not names[0].endswith('.csv'):
                raise ValueError('Expected one CSV per archive')
            with z.open(names[0]) as f:
                if record['kind'] == 'klines':
                    part = pd.read_csv(f, header=None, names=COLS, dtype=str)
                    # Binance USD-M monthly archives can have a header.
                    part = part[pd.to_numeric(part.timestamp, errors='coerce').notna()]
                    part = part.apply(pd.to_numeric, errors='raise')
                    frames.append(part)
                else:
                    funding.append(pd.read_csv(f))
    d = pd.concat(frames, ignore_index=True).sort_values('timestamp').reset_index(drop=True)
    d.timestamp = d.timestamp.astype('int64')
    if d.timestamp.iloc[0]!=1640995200000 or d.timestamp.iloc[-1]+60000!=1785542400000:
        raise ValueError('Evaluation endpoints do not match frozen protocol')
    if d.timestamp.duplicated().any() or (d.timestamp.diff().dropna() != 60000).any():
        raise ValueError('Duplicate or missing minute; source repair required')
    if not np.isfinite(d[COLS].to_numpy(dtype=float)).all():
        raise ValueError('Nonfinite bar value')
    if (d[['open', 'high', 'low', 'close']] <= 0).any().any():
        raise ValueError('Nonpositive price')
    if ((d.high < d[['open', 'close', 'low']].max(axis=1)) |
        (d.low > d[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('Invalid OHLC')
    if ((d.volume < 0) | (d.taker_buy_volume < 0) |
        (d.taker_buy_volume > d.volume + 1e-7)).any():
        raise ValueError('Invalid volume')
    f = pd.concat(funding, ignore_index=True).sort_values('calc_time').reset_index(drop=True)
    if f.calc_time.duplicated().any() or not np.isfinite(f.to_numpy(dtype=float)).all():
        raise ValueError('Invalid funding')
    # The downloaded BTC series is expected to have its published 8h schedule.
    # A changed interval is not silently treated as zero; validate every row.
    if not f.funding_interval_hours.eq(8).all():
        raise ValueError('Funding schedule changed; loader review required')
    expected = np.arange(int(d.timestamp.iloc[0]), int(d.timestamp.iloc[-1]) + 60000, 28800000)
    if ((f.calc_time % 60000) > 5000).any():
        raise ValueError('Funding timestamp jitter exceeds 5s; review required')
    f['minute_time'] = (f.calc_time // 60000) * 60000
    if not np.array_equal(expected, f.minute_time.to_numpy()):
        raise ValueError('Funding coverage incomplete')
    d.index = pd.to_datetime(d.timestamp, unit='ms', utc=True) + pd.Timedelta(minutes=1)
    audit = dict(rows=len(d), start=str(d.index[0] - pd.Timedelta(minutes=1)),
                 end_exclusive=str(d.index[-1]), funding_rows=len(f),
                 source_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
                 missing_minutes=0, checksum_verified_files=len(manifest))
    return d, f, audit


def features(d: pd.DataFrame, funding: pd.DataFrame) -> dict:
    """Every row contains information available at that minute's CLOSE only."""
    close = d.close
    prev = close.shift()
    tr = pd.concat([d.high-d.low, (d.high-prev).abs(), (d.low-prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    # Minute bars are indexed by end time; a 5m close exists only after 5 bars.
    five = close.resample('5min', closed='right', label='right').agg(['last', 'count'])
    five.loc[five['count'] != 5, 'last'] = np.nan
    e20 = five['last'].ewm(span=20, adjust=False, min_periods=50).mean()
    e50 = five['last'].ewm(span=50, adjust=False, min_periods=50).mean()
    trend5 = ((e20 > e50) & (e20 > e20.shift(3))).astype('int8')
    trend5 -= ((e20 < e50) & (e20 < e20.shift(3))).astype('int8')
    trend = trend5.reindex(d.index, method='ffill').fillna(0).to_numpy(dtype=np.int8)
    ratio = (d.taker_buy_volume / d.volume.replace(0, np.nan))
    flow = np.zeros(len(d), np.int8)
    flow[(ratio > 0.60) & (ratio.shift() < 0.50)] = 1
    flow[(ratio < 0.40) & (ratio.shift() > 0.50)] = -1
    stall_long = (d.low >= d.low.shift()).to_numpy()
    stall_short = (d.high <= d.high.shift()).to_numpy()
    vwaps = {w: (d.quote_volume.rolling(w).sum()/d.volume.rolling(w).sum()).to_numpy()
             for w in (20, 60, 120)}
    rates = pd.Series(funding.last_funding_rate.to_numpy(), index=funding.minute_time)
    fr = rates.reindex(d.timestamp).fillna(0).to_numpy()
    # Zero is legitimate only here, after the complete event schedule was validated.
    return dict(trend=trend, flow=flow, stall_long=stall_long, stall_short=stall_short,
                atr=atr.to_numpy(), vwaps=vwaps, funding=fr)


def make_signals(d: pd.DataFrame, feat: dict, cfg: Config) -> np.ndarray:
    close = d.close.to_numpy()
    vwap = feat['vwaps'][cfg.vwap]
    atr = feat['atr']
    direction = np.where(close < vwap - atr, 1, np.where(close > vwap + atr, -1, 0))
    eligible = ((direction == feat['trend']) & (direction != 0) &
                np.where(direction > 0, feat['stall_long'], feat['stall_short']))
    if cfg.flow:
        eligible &= direction == feat['flow']
    # A failed cost/stop check must not consume the excursion. Freeze the
    # decision cost model across execution stresses (do not hide higher fees
    # by quietly taking fewer trades in the stress case).
    stop = np.maximum(.001, atr/close)
    planned = close*(1+direction*.0001)
    gain = direction*(vwap/planned-1)
    eligible &= np.isfinite(gain) & (stop<=.002) & (gain>=cfg.rr*stop) & (gain>=3*.0012)
    # Rearm only after price comes back to its VWAP; no repeated same-pullback entries.
    return arm_signals(direction.astype(np.int8), eligible, close, vwap)


@njit(cache=True)
def arm_signals(direction, eligible, close, vwap):
    out = np.zeros(len(close), np.int8)
    consumed_long, consumed_short = False, False
    for i in range(len(close)):
        if close[i] >= vwap[i]:
            consumed_long = False
        if close[i] <= vwap[i]:
            consumed_short = False
        if eligible[i] and direction[i] == 1 and not consumed_long:
            out[i] = 1
            consumed_long = True
        elif eligible[i] and direction[i] == -1 and not consumed_short:
            out[i] = -1
            consumed_short = True
    return out


@njit(cache=True)
def simulate(ts, o, h, l, c, volumes, atr, vwap, signals, funding, hold, rr,
             fee_in, fee_out, slip, risk, exposure, daily_limit, dd_limit,
             gate_cost=.0012, gate_slip=.0001):
    """Single linear-contract position; funding before new entries at same timestamp.

    Stop-first on ambiguous bars, gap-aware adverse execution, no maker fiction.
    Daily/global risk barriers use the PREVIOUS mark peak (no intrabar hindsight).
    Protection is modeled, not a guarantee of an exchange fill at the barrier.
    """
    n = len(ts)
    eq = np.empty(n, np.float64)
    trades = np.empty((np.count_nonzero(signals) + 1, 11), np.float64)
    cash, peak, day_start = 10000., 10000., 10000.
    side, qty, entry, stop, target = 0, 0., 0., 0., 0.
    entered, last_exit, count = 0, -100, 0
    entry_fee, paid_funding = 0., 0.
    day = -1
    halted, day_halted = False, False
    halt_ms = 0
    for i in range(n):
        utc_day = ts[i] // 86400000
        opening_equity = cash + side * qty * (o[i] - entry)
        if utc_day != day:
            day = utc_day
            day_start = opening_equity
            day_halted = False
        if side != 0 and funding[i] != 0:
            payment = side * qty * o[i] * funding[i]
            cash -= payment
            paid_funding += payment
        # Signals use row i-1; row i's close/high/low are never inspected for entry.
        # Do not enter the last 10 minutes before a fixed 8h funding timestamp.
        to_funding = 480 - ((ts[i] // 60000) % 480)
        if (side == 0 and not halted and not day_halted and i > 0
                and i > last_exit + 1 and signals[i-1] != 0 and 10 < to_funding < 480):
            s = int(signals[i-1])
            p = o[i] * (1 + s * slip)
            sf = max(.001, atr[i-1] / c[i-1])
            planned = o[i] * (1+s*gate_slip)
            gain = s * (vwap[i-1] / planned - 1)
            costs = fee_in + fee_out + 2 * slip
            if (sf <= .002 and math.isfinite(gain) and gain >= rr * sf
                    and gain >= 3 * gate_cost and cash > 0):
                # Exposure capped and volume participation bounded by PREVIOUS bar.
                q = min(cash * risk / (sf + costs) / p,
                        cash * exposure / p, volumes[i-1] * .001)
                if q > 0:
                    side, qty, entry = s, q, p
                    stop = p * (1 - s * sf)
                    target = vwap[i-1]
                    entered = i
                    entry_fee = p * q * fee_in
                    paid_funding = 0.
                    cash -= entry_fee
        reason, exit_price = 0, 0.
        if side != 0:
            # Intrabar risk stops use cash AFTER all known entry/funding charges.
            threshold = max(day_start * (1 - daily_limit), peak * (1 - dd_limit))
            risk_stop = entry + side * (threshold - cash) / qty
            effective = max(stop, risk_stop) if side == 1 else min(stop, risk_stop)
            hit_stop = l[i] <= effective if side == 1 else h[i] >= effective
            hit_target = h[i] >= target if side == 1 else l[i] <= target
            if hit_stop:
                raw_exit = min(o[i], effective) if side == 1 else max(o[i], effective)
                exit_price = raw_exit * (1 - side * slip)
                reason = 4 if effective != stop else 1
            elif hit_target:
                # No beneficial gap slippage credited; use target even if open is better.
                exit_price = target * (1 - side * slip)
                reason = 2
            elif i - entered + 1 >= hold or i == n - 1:
                exit_price = c[i] * (1 - side * slip)
                reason = 3 if i != n-1 else 5
            if reason:
                gross = side * qty * (exit_price - entry)
                exit_fee = qty * exit_price * fee_out
                cash += gross - exit_fee
                fees = entry_fee + exit_fee
                trades[count] = np.array([ts[entered], ts[i]+60000, side, entry,
                                          exit_price, qty, gross, fees, paid_funding,
                                          gross-fees-paid_funding, reason])
                count += 1
                side, qty, last_exit = 0, 0., i
        equity = cash + side * qty * (c[i] - entry)
        eq[i] = equity
        peak = max(peak, equity)
        if equity <= day_start * (1 - daily_limit):
            day_halted = True
        if equity <= peak * (1 - dd_limit) and not halted:
            halted = True
            halt_ms = ts[i] + 60000
    return eq, trades[:count], halt_ms


def replay(d, feat, cfg, costs=Costs(), risk=Risk(), start=None, end=None):
    sig = make_signals(d, feat, cfg)
    mask = np.ones(len(d), bool)
    if start:
        mask &= d.timestamp.to_numpy() >= int(pd.Timestamp(start, tz='UTC').timestamp()*1000)
    if end:
        mask &= d.timestamp.to_numpy() < int(pd.Timestamp(end, tz='UTC').timestamp()*1000)
    indices = np.flatnonzero(mask)
    if not len(indices):
        raise ValueError('Empty evaluation interval')
    a, b = indices[0], indices[-1] + 1
    kw = dict(ts=d.timestamp.to_numpy(np.int64)[a:b],
              o=d.open.to_numpy(float)[a:b], h=d.high.to_numpy(float)[a:b],
              l=d.low.to_numpy(float)[a:b], c=d.close.to_numpy(float)[a:b],
              volumes=d.volume.to_numpy(float)[a:b], atr=feat['atr'][a:b],
              vwap=feat['vwaps'][cfg.vwap][a:b], signals=sig[a:b],
              funding=feat['funding'][a:b], hold=cfg.hold, rr=cfg.rr,
              fee_in=costs.entry, fee_out=costs.exit, slip=costs.slip,
              risk=risk.fraction, exposure=risk.exposure,
              daily_limit=risk.daily, dd_limit=risk.drawdown)
    eq, ts, halted = simulate(**kw)
    idx = d.index[a:b]
    trades = pd.DataFrame(ts, columns=TRADE_COLS)
    days = (d.timestamp.iloc[b-1] + 60000 - d.timestamp.iloc[a]) / 86400000
    values = np.concatenate(([10000.], eq))
    dd = values / np.maximum.accumulate(values) - 1
    net = trades.net.to_numpy() if len(trades) else np.array([])
    win = net[net > 0].sum()
    loss = -net[net < 0].sum()
    summary = dict(config=cfg.id, start=str(idx[0] - pd.Timedelta(minutes=1)),
                   end_exclusive=str(idx[-1]), days=float(days), trades=len(trades),
                   trades_per_day=len(trades)/days, return_pct=(eq[-1]/10000-1)*100,
                   max_drawdown_pct=float(dd.min()*100),
                   win_rate_pct=float(np.mean(net > 0)*100) if len(net) else None,
                   profit_factor=float(win/loss) if loss > 0 else None,
                   fees=float(trades.fees.sum()), funding_cost=float(trades.funding_cost.sum()),
                   final_equity=float(eq[-1]),
                   cagr_pct=float(((eq[-1]/10000)**(365.25/days)-1)*100) if days>=365 and eq[-1]>0 else None,
                   halted_at=str(pd.to_datetime(halted,unit='ms',utc=True)) if halted else None,
                   risk=asdict(risk), costs=asdict(costs))
    if not math.isclose(10000 + float(trades.net.sum()), eq[-1], rel_tol=1e-9, abs_tol=1e-6):
        raise AssertionError('Account does not reconcile to trade ledger')
    daily = pd.Series(eq, index=idx).resample('1D', closed='right', label='right').last().dropna()
    return summary, trades, daily


def block_bootstrap(daily: pd.Series, samples: int = 2000) -> dict:
    """Descriptive 30-day circular block bootstrap, not a selection-corrected proof."""
    values = np.r_[10000., daily.to_numpy()]
    returns = np.diff(np.log(values))
    rng = np.random.default_rng(20260905)
    n = len(returns)
    means = np.empty(samples)
    for k in range(samples):
        starts = rng.integers(0, n, size=math.ceil(n/30))
        sample = np.concatenate([returns[(np.arange(30)+s) % n] for s in starts])[:n]
        means[k] = sample.mean()
    return dict(block_days=30, samples=samples, seed=20260905,
                mean_daily_log_return_ci95=[float(x) for x in np.quantile(means,[.025,.975])],
                caveat='Descriptive, not corrected for parameter selection or model misspecification')


def run(root: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    protocol_path = Path(__file__).with_name('protocol.json')
    protocol = json.loads(protocol_path.read_text())
    d, f, audit = load_data(root)
    feat = features(d, f)
    grid = [Config(w, h, rr, fl) for w,h,rr,fl in itertools.product(
        protocol['candidate_grid']['vwap_minutes'], protocol['candidate_grid']['holding_minutes'],
        protocol['candidate_grid']['reward_risk'], protocol['candidate_grid']['minute_flow_proxy'])]
    baseline = Config()
    training = []
    # Do not even evaluate holdout candidates during selection.
    for cfg in grid:
        annual = [replay(d, feat, cfg, start=f'{y}-01-01', end=f'{y+1}-01-01')[0]
                  for y in (2022,2023)]
        score = min(x['return_pct'] for x in annual)
        training.append(dict(config=asdict(cfg), id=cfg.id, score=score, years=annual))
    ranked = sorted(training, key=lambda r: (-r['score'], r['id']))
    best = Config(**ranked[0]['config'])
    validation = replay(d, feat, best, start='2024-01-01', end='2025-01-01')[0]
    admitted = (ranked[0]['score'] > 0 and validation['return_pct'] > 0 and
                sum(x['trades'] for x in ranked[0]['years']) >= 500 and
                all((x['profit_factor'] or 0) >= 1.1 for x in ranked[0]['years']+[validation]))
    selection = dict(config=asdict(best), id=best.id, admitted=bool(admitted),
                     reason='Training/validation gates passed' if admitted else 'Rejected by frozen training/validation gates',
                     validation=validation, final_used_for_selection=False)
    (out/'selection.json').write_text(json.dumps(selection,indent=2,allow_nan=False))
    annual_rows = []
    # Annual accounts are RESET each January. OOS below is one continuous account.
    for name, cfg, costs, risk in [
        ('original_proxy_diagnostic',baseline,Costs(),Risk()),
        ('winner_proxy_diagnostic',best,Costs(),Risk()),
        ('original_proxy_protected',baseline,Costs(),Risk(daily=.01,drawdown=.04)),
        ('winner_proxy_protected',best,Costs(),Risk(daily=.01,drawdown=.04)),
        ('winner_proxy_zero_fees_ablation',best,Costs(0,0,0),Risk()),
        ('winner_proxy_double_slippage',best,Costs(slip=.0002),Risk()),
        ('winner_proxy_mexc_api_cost_stress',best,Costs(.0008,.0008,.0001),Risk()),
        ('winner_proxy_aggressive_sensitivity',best,Costs(),Risk(.005,3.,.02,.25))]:
        for y in range(2022,2027):
            end = f'{y+1}-01-01' if y<2026 else '2026-08-01'
            summary, trades, daily = replay(d,feat,cfg,costs,risk,f'{y}-01-01',end)
            summary.update(scenario=name, year=y, full_calendar_year=y<2026, capital_reset=True)
            annual_rows.append(summary)
            if y>=2025 and name in ('original_proxy_protected','winner_proxy_diagnostic','winner_proxy_aggressive_sensitivity'):
                trades.to_csv(out/f'{name}_{y}_trades.csv.gz',index=False,compression='gzip')
    oos = []
    for name, cfg, risk in [('original_proxy_protected',baseline,Risk(daily=.01,drawdown=.04)),
                           ('winner_proxy_diagnostic',best,Risk()),
                           ('winner_proxy_protected',best,Risk(daily=.01,drawdown=.04)),
                           ('winner_proxy_aggressive_sensitivity',best,Risk(.005,3.,.02,.25))]:
        summary, trades, daily = replay(d,feat,cfg,Costs(),risk,'2025-01-01','2026-08-01')
        summary.update(scenario=name,capital_reset=False)
        summary['bootstrap'] = block_bootstrap(daily)
        oos.append(summary)
        daily.to_csv(out/f'{name}_oos_daily_equity.csv',header=['equity'])
        trades.to_csv(out/f'{name}_oos_trades.csv.gz',index=False,compression='gzip')
    result = dict(protocol=protocol,protocol_sha256=hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
                  data=audit, environment=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__),
                  candidates_evaluated=len(grid),training_ranking=ranked,selection=selection,
                  annual=annual_rows,oos=oos,exact_tick_backtest_complete=False,
                  same_venue_execution_validated=False,target_achieved=False,live_ready=False,
                  limitations=['Minute flow/stall is NOT 10s/5s flow.', 'Taker proxy is NOT post-only execution.',
                               'Binance prices with hypothetical OKX/MEXC cost scenarios is NOT a same-venue test.',
                               'Funding timestamps with <=5s publication jitter assigned to their minute; entry blocked in the settlement minute.',
                               'Funding charged at minute-open trade price, not historical mark price.',
                               'No queue depth, latency replay or liquidation/margin certification.',
                               '2026 is YTD, not a full-year return.',
                               'Cost-stress scenarios keep the primary signal/entry cost gate fixed; execution, sizing and path may differ.'])
    (out/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    flat = pd.DataFrame([{k:v for k,v in row.items() if k not in ('risk','costs')} for row in annual_rows])
    flat.to_csv(out/'annual_returns.csv',index=False)
    print(json.dumps(dict(data=audit, selection=selection, oos=oos, target_achieved=False),indent=2,allow_nan=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    run(args.data,args.out)
