"""Ten predeclared long/cash policies; signals are timestamped at candle CLOSE."""
import numpy as np
import pandas as pd

NAMES=('ema_1h_12_48','ema_4h_24_120','ema_1d_10_50','sma_1d_50_200',
       'channel_1h_20_10','channel_4h_55_20','channel_1d_55_20',
       'momentum_1d_63_200','dip_1d_rsi2_200','ensemble_1d')
PRIMARY='ensemble_1d'


def aggregate(data,hours):
    b=data.resample(f'{hours}h',closed='left',label='right').agg(
        {'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    count=data.close.resample(f'{hours}h',closed='left',label='right').count()
    b.loc[count!=hours,:]=np.nan
    return b


def policy_segment(b,kind,a=0,z=0):
    c=b.close
    if kind=='ema':
        fast=c.ewm(span=a,adjust=False,min_periods=a).mean()
        slow=c.ewm(span=z,adjust=False,min_periods=z).mean()
        return ((fast>slow)&(c>slow)).astype(float)
    if kind=='sma':
        slow=c.rolling(z,min_periods=z).mean()
        return ((c.rolling(a,min_periods=a).mean()>slow)&(c>slow)).astype(float)
    if kind=='momentum':
        return ((c>c.shift(63))&(c>c.rolling(200,min_periods=200).mean())).astype(float)
    if kind=='channel':
        upper=b.high.rolling(a,min_periods=a).max().shift()
        lower=b.low.rolling(z,min_periods=z).min().shift()
        state=0.;values=[]
        for p,hi,lo in zip(c,upper,lower):
            if not np.isfinite(hi) or not np.isfinite(lo):state=0.
            elif state==0 and p>hi:state=1.
            elif state==1 and p<lo:state=0.
            values.append(state)
        return pd.Series(values,index=b.index)
    if kind=='dip':
        delta=c.diff();g=delta.clip(lower=0).ewm(alpha=.5,adjust=False,min_periods=2).mean()
        loss=(-delta.clip(upper=0)).ewm(alpha=.5,adjust=False,min_periods=2).mean()
        den=g+loss;rsi=100*g/den.replace(0,np.nan)
        regime=c>c.rolling(200,min_periods=200).mean()
        state=0.;age=0;values=[]
        for score,trend in zip(rsi,regime):
            if state:
                age+=1
                if not trend or score>70 or age>=5:state=0.
            elif trend and score<10:state=1.;age=0
            values.append(state)
        return pd.Series(values,index=b.index)
    raise ValueError('Unknown policy')


def policy(b,kind,a=0,z=0):
    # A gap resets state and lookbacks. Prices never forward-fill through a gap.
    out=pd.Series(0.,index=b.index)
    group=(~b.close.notna()).cumsum()
    valid=b.close.notna()
    for _,segment in b.loc[valid].groupby(group[valid]):
        out.loc[segment.index]=policy_segment(segment,kind,a,z)
    return out


def build(data):
    specs=(('ema_1h_12_48',1,'ema',12,48),('ema_4h_24_120',4,'ema',24,120),
      ('ema_1d_10_50',24,'ema',10,50),('sma_1d_50_200',24,'sma',50,200),
      ('channel_1h_20_10',1,'channel',20,10),('channel_4h_55_20',4,'channel',55,20),
      ('channel_1d_55_20',24,'channel',55,20),('momentum_1d_63_200',24,'momentum',0,0),
      ('dip_1d_rsi2_200',24,'dip',0,0))
    bars={h:aggregate(data,h) for h in (1,4,24)};out={}
    for name,h,kind,a,z in specs:
        signal=policy(bars[h],kind,a,z)
        # At hour-open T this holds only a candle that closed AT or BEFORE T.
        # The engine imposes another full hour's latency by default.
        out[name]=signal.reindex(data.index,method='ffill').fillna(0).to_numpy(np.int8)
    votes=out['ema_1d_10_50']+out['channel_1d_55_20']+out['momentum_1d_63_200']
    out[PRIMARY]=(votes>=2).astype(np.int8)
    return out
