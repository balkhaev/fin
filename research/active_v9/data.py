from __future__ import annotations
import hashlib, io, time, urllib.error, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
import pandas as pd
from config import Config

COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']
NUM=['open','high','low','close','volume','quote_volume','trades','taker_buy_base','taker_buy_quote']
@dataclass(frozen=True)
class Record:
    kind:str;symbol:str;month:str;url:str;bytes:int;sha256:str;checksum_available:bool;checksum_passed:bool|None;rows:int;missing:bool

def utc(x):
    t=pd.Timestamp(x);return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')
def months(a,b):
    x=utc(a).to_period('M').to_timestamp().tz_localize('UTC');e=utc(b)
    while x<e:yield x;x+=pd.offsets.MonthBegin(1)
def request(url,retries=5,timeout=90):
    last=None
    for n in range(retries):
        try:
            q=urllib.request.Request(url,headers={'User-Agent':'fin-active-v9/1.0'})
            with urllib.request.urlopen(q,timeout=timeout) as r:return r.read()
        except urllib.error.HTTPError as e:
            if e.code==404:return None
            last=e
        except (urllib.error.URLError,TimeoutError) as e:last=e
        time.sleep(min(20,2**n))
    raise RuntimeError(f'download failed {url}: {last}')
def checksum(payload):
    if payload is None:return None
    try:v=payload.decode().strip().split()[0].lower()
    except Exception:return None
    return v if len(v)==64 else None
def unit(s):
    v=pd.to_numeric(s,errors='coerce').dropna();m=float(v.abs().median()) if len(v) else 1e12
    return 'ns' if m>=1e17 else 'us' if m>=1e14 else 'ms' if m>=1e11 else 's'
def unzip(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv')]
        if not names:raise ValueError('no csv')
        return z.read(sorted(names)[0])
def parse_kline(payload,start,end):
    raw=unzip(payload);f=pd.read_csv(io.BytesIO(raw),header=None,names=COLS,low_memory=False)
    f.open_time=pd.to_datetime(pd.to_numeric(f.open_time,errors='coerce'),unit=unit(f.open_time),utc=True,errors='coerce')
    for c in NUM:f[c]=pd.to_numeric(f[c],errors='coerce')
    f=f.dropna(subset=['open_time',*NUM]);f=f[(f.open_time>=start)&(f.open_time<end)].sort_values('open_time').drop_duplicates('open_time',keep='last')
    return f.set_index('open_time')[NUM].astype(float)
def parse_funding(payload,start,end):
    raw=unzip(payload);first=raw.splitlines()[0].decode(errors='ignore').lower() if raw else '';header=0 if any(x in first for x in ('calc_time','funding','rate')) else None
    f=pd.read_csv(io.BytesIO(raw),header=header,low_memory=False);lower={str(c).strip().lower():c for c in f.columns};tc=next((lower[x] for x in ('calc_time','funding_time','time') if x in lower),None);rc=next((lower[x] for x in ('last_funding_rate','funding_rate','rate') if x in lower),None);num=f.apply(pd.to_numeric,errors='coerce')
    if tc is None:tc=next(c for c in f.columns if len(num[c].dropna()) and float(num[c].dropna().abs().median())>=1e11)
    if rc is None:rc=[c for c in f.columns if c!=tc and len(num[c].dropna()) and float(num[c].dropna().abs().quantile(.95))<1][-1]
    t=pd.to_datetime(pd.to_numeric(f[tc],errors='coerce'),unit=unit(f[tc]),utc=True,errors='coerce');r=pd.to_numeric(f[rc],errors='coerce');s=pd.Series(r.to_numpy(float),index=t).dropna();return s[(s.index>=start)&(s.index<end)].groupby(level=0).sum().sort_index()
def url(kind,symbol,interval,ym):
    if kind=='klines':return f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip'
    return f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip'
def one(kind,symbol,month,cfg,cache,refresh):
    ym=month.strftime('%Y-%m');u=url(kind,symbol,cfg.interval,ym);path=cache/kind/symbol/u.rsplit('/',1)[-1];path.parent.mkdir(parents=True,exist_ok=True)
    if refresh or not path.exists():
        p=request(u)
        if p is None:return kind,symbol,None,asdict(Record(kind,symbol,ym,u,0,'',False,None,0,True))
        path.write_bytes(p)
    payload=path.read_bytes();dig=hashlib.sha256(payload).hexdigest();ca=False;cp=None
    exp=request(u+'.CHECKSUM',retries=2,timeout=30)
    if exp is not None:
        x=checksum(exp)
        if x:ca=True;cp=x==dig
        if cp is False:raise ValueError('checksum mismatch '+u)
    start=max(month,utc(cfg.start));end=min(month+pd.offsets.MonthBegin(1),utc(cfg.end_exclusive));parsed=parse_kline(payload,start,end) if kind=='klines' else parse_funding(payload,start,end)
    return kind,symbol,parsed,asdict(Record(kind,symbol,ym,u,len(payload),dig,ca,cp,len(parsed),False))
def load(cfg:Config,cache:Path,refresh=False):
    tasks=[(k,s,m) for m in months(cfg.start,cfg.end_exclusive) for s in cfg.symbols for k in ('klines','fundingRate')];group={};records=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        fut=[ex.submit(one,k,s,m,cfg,cache,refresh) for k,s,m in tasks]
        for n,x in enumerate(as_completed(fut),1):
            k,s,p,r=x.result();records.append(r)
            if p is not None:group.setdefault((k,s),[]).append(p)
            if n%200==0 or n==len(fut):print(f'archives {n}/{len(fut)}')
    kl={};fund={};quality=[]
    for s in cfg.symbols:
        parts=group.get(('klines',s),[])
        if not parts:continue
        f=pd.concat(parts).sort_index();f=f[~f.index.duplicated(keep='last')];kl[s]=f
        fs=group.get(('fundingRate',s),[]);fund[s]=pd.concat(fs).sort_index().groupby(level=0).sum() if fs else pd.Series(dtype=float)
        invalid=(f.high<f[['open','close','low']].max(axis=1))|(f.low>f[['open','close','high']].min(axis=1))|(f[['open','high','low','close']]<=0).any(axis=1)
        if invalid.any():raise ValueError(f'{s} invalid rows')
        quality.append({'symbol':s,'kline_rows':len(f),'start':f.index.min().isoformat(),'end':f.index.max().isoformat(),'duplicates':int(f.index.duplicated().sum()),'funding_rows':len(fund[s])})
    return kl,fund,records,quality
