from __future__ import annotations
import numpy as np, pandas as pd

def metrics(a):
 e=a.equity;r=e.pct_change().dropna();total=float(e.iloc[-1]/e.iloc[0]-1);days=max((e.index[-1]-e.index[0]).total_seconds()/86400,1);ann=(1+total)**(365/days)-1 if total>-1 else -1;dd=e/e.cummax()-1;std=float(r.std(ddof=1));sh=float(np.sqrt(365)*r.mean()/std) if std>0 else np.nan;down=r[r<0];ds=float(down.std(ddof=1));yrs=max(days/365,1/365)
 return {'total_return':total,'annualized_return':float(ann),'max_drawdown':float(dd.min()),'sharpe':sh,'sortino':float(np.sqrt(365)*r.mean()/ds) if ds>0 else np.nan,'calmar':float(ann/abs(dd.min())) if dd.min()<0 else np.nan,'worst_day':float(r.min()),'annual_turnover':float(a.turnover.sum()/yrs),'average_gross':float(a.gross.mean()),'max_gross':float(a.gross.max()),'total_costs':float(a.costs.sum()),'funding_pnl':float(a.funding_pnl.sum()),'final_equity':float(e.iloc[-1])}
def rolling(e,window=365):
 x=(e/e.shift(window)-1).dropna();return {'rolling_positive_share':float((x>0).mean()) if len(x) else np.nan,'rolling_worst':float(x.min()) if len(x) else np.nan,'rolling_median':float(x.median()) if len(x) else np.nan}
