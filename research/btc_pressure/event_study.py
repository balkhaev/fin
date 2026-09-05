"""Frozen sparse-day event audit. Markouts are NOT an account backtest.

Features use closed receive-second buckets and delayed exchange-minute bars.
This boundary-clock archive profile differs from V1 raw-message scheduling.
Neither three sampled days nor overlapping markouts establish annual returns.
"""
from __future__ import annotations
import argparse, hashlib, json
from collections import Counter, deque
from pathlib import Path
from statistics import median
import numpy as np
import pandas as pd
from .strategy import Frame, Mechanisms, quantile

DATES=('2025-09-01','2026-03-01','2026-09-01')
TYPES={'bybit':('trades','book_snapshot_5','liquidations','derivative_ticker'),
       'bybit-spot':('trades',),'binance':('trades',)}
FEE=.00055
SLIP=.0001
LATENCY_US=250000


def verify(root):
    m=json.loads((root/'manifest.json').read_text());rows=m['files']
    expected={(d,v,k) for d in DATES for v,ks in TYPES.items() for k in ks}
    if len(rows)!=len(expected) or {(r['date'],r['venue'],r['kind']) for r in rows}!=expected:
        raise ValueError('Missing/duplicate input identity')
    for r in rows:
        if r['status']!='downloaded':raise ValueError('Unavailable mandatory archive')
        p=root/r['filename']
        if p.name!=r['filename']:raise ValueError('Unsafe archive filename')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('Archive checksum mismatch')
    return m


def read(root,date,venue,kind):
    f=pd.read_csv(root/f'{date}_{venue}_{kind}.csv.gz')
    if not f.exchange.eq(venue).all() or not f.symbol.eq('BTCUSDT').all():
        raise ValueError('Cross-instrument/venue contamination')
    for c in ('timestamp','local_timestamp'):
        if f[c].isna().any():raise ValueError('Missing timestamp')
        f[c]=f[c].astype('int64')
    if (f.local_timestamp.diff().dropna()<0).any():raise ValueError('Reversed receive clock')
    duplicates=int(f.id.duplicated().sum()) if kind=='trades' else 0
    if duplicates:raise ValueError('Duplicate trade identifiers; explicit repair required')
    if kind in ('trades','liquidations'):
        if not f.side.isin(['buy','sell']).all():raise ValueError('Unknown normalized order side')
        if not np.isfinite(f[['price','amount']].to_numpy(float)).all() or (f[['price','amount']]<=0).any().any():
            raise ValueError('Invalid price/quantity')
    return f,dict(rows=len(f),duplicates=duplicates,first_receive_us=int(f.local_timestamp.iloc[0]) if len(f) else None,
                  last_receive_us=int(f.local_timestamp.iloc[-1]) if len(f) else None)


def rolling(values,window,kind='sum'):
    return getattr(pd.Series(values).rolling(window,min_periods=1),kind)().to_numpy()


def shifted(x,n,fill=0.):
    out=np.full(len(x),fill,dtype=float)
    if n==0:return x.copy()
    if n<len(x):out[n:]=x[:-n]
    return out


def buckets(f,start_us,n=86400):
    """Index k summarizes completed receive second [k,k+1), known at k+1."""
    local=f.local_timestamp.to_numpy();exchange=f.timestamp.to_numpy()
    valid=(local>=start_us)&(local<start_us+n*1000000)&(local-exchange<=5000000)&(exchange-local<=500000)
    v=f.loc[valid];i=((v.local_timestamp.to_numpy()-start_us)//1000000).astype(int)
    buy=v.side.eq('buy').to_numpy();notional=v.price.to_numpy()*v.amount.to_numpy()
    buys=np.bincount(i,weights=notional*buy,minlength=n)
    sells=np.bincount(i,weights=notional*(~buy),minlength=n)
    low=np.full(n,np.inf);high=np.full(n,-np.inf)
    np.minimum.at(low,i,v.price.to_numpy());np.maximum.at(high,i,v.price.to_numpy())
    low[~np.isfinite(low)]=np.nan;high[~np.isfinite(high)]=np.nan
    last=np.full(n,-1.);np.maximum.at(last,i,v.local_timestamp.to_numpy()/1000.)
    last=np.maximum.accumulate(last)
    result=dict(buy=buys,sell=sells,low=low,high=high,last=last,dropped=int((~valid).sum()))
    for w in (10,30,60,300):
        b,s=rolling(buys,w),rolling(sells,w)
        result[f'flow{w}']=np.divide(b-s,b+s,out=np.zeros(n),where=b+s>0)
        result[f'amount{w}']=b+s
    return result


def confirmed_bars(trades,start_us):
    """Bar available at exchange-minute end + 5s; no future backfilling."""
    late=trades.local_timestamp-trades.timestamp
    x=trades.loc[(late<=5000000)&(late>=-500000)].copy()
    x['minute']=((x.timestamp-start_us)//60000000).astype(int)
    x=x[(x.minute>=0)&(x.minute<1440)].sort_values(['timestamp','local_timestamp'],kind='stable')
    bars=x.groupby('minute',sort=True).price.agg(['first','max','min','last']).reindex(range(1440))
    prev=bars['last'].shift()
    tr=np.maximum.reduce([(bars['max']-bars['min']).to_numpy(),(bars['max']-prev).abs().to_numpy(),(bars['min']-prev).abs().to_numpy()])
    bars['atr']=pd.Series(tr,index=bars.index).rolling(14,min_periods=14).mean()
    bars['range_high']=bars['max'].rolling(60,min_periods=60).max()
    bars['range_low']=bars['min'].rolling(60,min_periods=60).min()
    return bars


def frames(trades,spots,book,liq,start_us,observer=None):
    p=buckets(trades,start_us);flows=[buckets(x,start_us) for x in spots]
    l=buckets(liq,start_us);bars=confirmed_bars(trades,start_us)
    n=86400;times=start_us//1000+(np.arange(n)+1)*1000
    idx=np.searchsorted(book.local_timestamp.to_numpy(),times*1000,side='left')-1
    last_book=np.where(idx>=0,book.local_timestamp.to_numpy()[np.maximum(idx,0)]/1000.,-1)
    prices=(book['bids[0].price'].to_numpy()+book['asks[0].price'].to_numpy())/2
    bdepth=sum(book[f'bids[{i}].amount'].to_numpy() for i in range(5))
    adepth=sum(book[f'asks[{i}].amount'].to_numpy() for i in range(5))
    hi10=rolling(p['high'],10,'max');lo10=rolling(p['low'],10,'min')
    phi=shifted(hi10,10,np.nan);plo=shifted(lo10,10,np.nan)
    history=np.column_stack([shifted(l['amount10'],x) for x in range(10,310,10)])
    q95=np.partition(history,27,axis=1)[:,27]
    burst=l['amount10']>np.maximum(50000.,q95*3.)
    lside=np.where(burst & (l['flow10']>.5),1,np.where(burst & (l['flow10']<-.5),-1,0))
    rates=[deque(maxlen=180),deque(maxlen=180)];depths=deque(maxlen=60);last_sample=-1
    counters=Counter();machine=Mechanisms();out=[];valid_frames=0
    barvalues=bars[['atr','range_high','range_low']].to_numpy(float)
    for k,now in enumerate(times):
        reason=None;bi=int(((now-start_us//1000)-5000)//60000)-1
        if k<300:reason='flow_warmup'
        elif any(now-v['last'][k]>5000 for v in [p]+flows):reason='stale_trade'
        elif idx[k]<0 or now-last_book[k]>1500:reason='stale_book'
        elif bi<60 or bi>=len(bars) or not np.isfinite(barvalues[bi]).all():reason='bar_warmup_or_gap'
        elif not np.isfinite([hi10[k],lo10[k],phi[k],plo[k]]).all():reason='thin_trade_history'
        if reason:
            if observer is not None:observer(int(now),None,reason)
            counters[reason]+=1
            if reason not in ('flow_warmup','bar_warmup_or_gap'):machine.reset()
            continue
        atr,rhigh,rlow=barvalues[bi];j=idx[k];price=prices[j]
        ratios=[v['flow300'][k] for v in flows];thresholds=[max(.2,quantile(r,.8)) for r in rates]
        ready=all(len(r)>=30 for r in rates)
        consensus=1 if ready and all(a>b for a,b in zip(ratios,thresholds)) else -1 if ready and all(a<-b for a,b in zip(ratios,thresholds)) else 0
        bbase=median(x[0] for x in depths) if depths else bdepth[j]
        abase=median(x[1] for x in depths) if depths else adepth[j]
        f=Frame(int(now),float(price),float(atr),float(rhigh),float(rlow),consensus,
                float(sum(v['flow60'][k] for v in flows)/2),float(p['flow30'][k]),
                float(bdepth[j]/max(bbase,1e-12)),float(adepth[j]/max(abase,1e-12)),
                float(hi10[k]),float(lo10[k]),float(phi[k]),float(plo[k]),int(lside[k]))
        if now//10000!=last_sample:
            for r,value in zip(rates,ratios):r.append(abs(value))
            last_sample=now//10000
        depths.append((bdepth[j],adepth[j]));valid_frames+=1
        if f.consensus:counters['spot_consensus_seconds']+=1
        if f.liquidation_side:counters['liquidation_burst_seconds']+=1
        if observer is not None:observer(int(now),f,'ready')
        proposal=machine.on_frame(f)
        if proposal:out.append(proposal)
    counters['valid_frames']=valid_frames
    counters['out_of_range_or_delayed_trades']=p['dropped']+sum(v['dropped'] for v in flows)
    return out,dict(counters)


def validate_book(book):
    columns=[c for c in book.columns if c.startswith(('asks[','bids['))]
    a=book[columns].to_numpy(float)
    if not np.isfinite(a).all() or (a<=0).any():raise ValueError('Incomplete/invalid top-five book')
    if not (book['bids[0].price']<book['asks[0].price']).all():raise ValueError('Crossed book')
    for i in range(1,5):
        if not ((book[f'bids[{i}].price']<book[f'bids[{i-1}].price']) & (book[f'asks[{i}].price']>book[f'asks[{i-1}].price'])).all():
            raise ValueError('Book levels not monotonic')


def markouts(proposals,book,ticker):
    times=book.local_timestamp.to_numpy();bid=book['bids[0].price'].to_numpy();ask=book['asks[0].price'].to_numpy()
    funding=np.unique(ticker.funding_timestamp.dropna().to_numpy(dtype=np.int64));out=[]
    for p in proposals:
        before=int(np.searchsorted(times,p.time*1000,side='left'))-1
        eligible=False
        if before>=0 and p.time*1000-times[before]<=1500000:
            planned=(bid[before] if p.side==1 else ask[before]) if p.passive else (ask[before] if p.side==1 else bid[before])*(1+p.side*.0005)
            distance=p.side*(planned-p.stop);gain=p.side*(p.target-planned)
            fees=(.0002 if p.passive else FEE)+FEE+2*SLIP
            eligible=bool(0<distance/planned<=.05 and gain>=max(1.5*distance,3*planned*fees))
        i=int(np.searchsorted(times,p.time*1000+LATENCY_US,side='left'))
        if i>=len(book):continue
        entry=(ask[i] if p.side==1 else bid[i])*(1+p.side*SLIP)
        for horizon in (30,300,1800):
            j=int(np.searchsorted(times,times[i]+horizon*1000000+LATENCY_US,side='left'))
            row=dict(time_ms=p.time,family=p.family,side=p.side,proposed_passive=p.passive,horizon_seconds=horizon,
                     stop_fraction=p.side*(p.entry-p.stop)/p.entry,target_fraction=p.side*(p.target-p.entry)/p.entry,
                     entry_price=entry,entry_receive_us=int(times[i]),price_gate_pass=eligible,status='priced')
            if j>=len(book):row['status']='right_censored'
            elif times[i]-(p.time*1000+LATENCY_US)>1500000 or times[j]-(times[i]+horizon*1000000+LATENCY_US)>1500000:
                row['status']='stale_execution_endpoint'
            elif np.any((funding>=times[i])&(funding<=times[j])):row['status']='funding_unpriced'
            else:
                exit=(bid[j] if p.side==1 else ask[j])*(1-p.side*SLIP);gross=p.side*(exit/entry-1)
                row.update(exit_receive_us=int(times[j]),exit_price=exit,gross_bps=gross*10000,
                           cost_bps=FEE*(1+exit/entry)*10000,net_bps=(gross-FEE*(1+exit/entry))*10000)
            out.append(row)
    return out


def study(root,out):
    if out.exists():raise FileExistsError('Evidence output is append-only; choose a new directory')
    manifest=verify(root);out.mkdir(parents=True);summary=[];all_markouts=[]
    for date in DATES:
        print('loading',date,flush=True);data={};audits={}
        for v,ks in TYPES.items():
            for k in ks:data[v,k],audits[f'{v}:{k}']=read(root,date,v,k)
        book=data['bybit','book_snapshot_5'];validate_book(book)
        latency=book.local_timestamp-book.timestamp
        valid_latency=(latency<=5000000)&(latency>=-500000)
        audits['bybit:book_snapshot_5']['excluded_latency_rows']=int((~valid_latency).sum())
        book=book.loc[valid_latency].reset_index(drop=True)
        if not len(book):raise ValueError('No timely book data')
        start=int(pd.Timestamp(date,tz='UTC').timestamp()*1000000)
        proposals,counters=frames(data['bybit','trades'],[data['binance','trades'],data['bybit-spot','trades']],book,data['bybit','liquidations'],start)
        rows=markouts(proposals,book,data['bybit','derivative_ticker'])
        for row in rows:row['date']=date
        all_markouts.extend(rows)
        details=dict(date=date,source_audit=audits,features=counters,signals=len(proposals),signal_counts=dict(Counter(p.family for p in proposals)),
            price_gate_pass=sum(1 for r in rows if r['horizon_seconds']==30 and r['price_gate_pass']))
        summary.append(details);print(json.dumps(details),flush=True)
        (out/f'{date}_session.json').write_text(json.dumps(details,indent=2))
        pd.DataFrame(rows).to_csv(out/f'{date}_markouts.csv',index=False)
        pd.DataFrame([vars(p) for p in proposals]).to_csv(out/f'{date}_signals.csv',index=False)
        del data,book
    frame=pd.DataFrame(all_markouts);frame.to_csv(out/'signal_markouts.csv',index=False);groups=[]
    if len(frame):
        for (family,horizon),g in frame.groupby(['family','horizon_seconds']):
            net=g.loc[g.status=='priced','net_bps']
            groups.append(dict(family=family,horizon_seconds=int(horizon),signals=len(g),priced=len(net),
                price_gate_pass=int(g.price_gate_pass.sum()),mean_net_bps=float(net.mean()) if len(net) else None,median_net_bps=float(net.median()) if len(net) else None,
                positive_fraction=float((net>0).mean()) if len(net) else None,unpriced=dict(Counter(g.loc[g.status!='priced','status']))))
    result=dict(schema='btc-pressure-event-audit-v1',dates=list(DATES),sample_days=3,continuous_years=0,
                protocol_sha256=manifest['protocol_sha256'],sessions=summary,markout_groups=groups,
                cost_scenario=dict(taker_fee=FEE,slippage_per_side=SLIP,latency_ms=LATENCY_US//1000),
                account_return_pct=None,cagr_pct=None,target_achieved=False,live_ready=False,
                limitations=['Three preselected dates, not continuous annual coverage.',
                 'Markouts are overlapping signal counterfactuals, not one-account PnL or executed maker orders.',
                 'Normalized Bybit liquidation side is forced ORDER side; never invert it again.',
                 'Five-second delayed finalized minute bars from timely prints, not original kline feed.',
                 'Evaluation uses closed receive seconds, not first message of each second.',
                 'Vendor local clocks may not be synchronized and are not the user network latency.',
                 'No complete disconnect log in CSVs; staleness gates are not completeness proof.',
                 'Markouts crossing funding are unpriced, not zero-funded.',
                 'Depth-five snapshots do not prove passive queue placement or fill probability.'])
    (out/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps(result['markout_groups'],indent=2),flush=True);return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.data,a.out)
