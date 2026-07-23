from __future__ import annotations
import numpy as np,pandas as pd
class Market:
 def __init__(self,klines,funding):
  index=None
  for f in klines.values():index=f.index if index is None else index.union(f.index)
  self.index=index.sort_values();self.symbols=tuple(sorted(klines));self.open=pd.DataFrame({s:klines[s].open.reindex(index) for s in self.symbols});self.high=pd.DataFrame({s:klines[s].high.reindex(index) for s in self.symbols});self.low=pd.DataFrame({s:klines[s].low.reindex(index) for s in self.symbols});self.close=pd.DataFrame({s:klines[s].close.reindex(index) for s in self.symbols});self.available=self.open.notna()&self.close.notna();self.funding=pd.DataFrame({s:funding.get(s,pd.Series(dtype=float)).resample('1D').sum().reindex(index).fillna(0) for s in self.symbols});self.returns=self.close.pct_change(fill_method=None);self.logret=np.log(self.close).diff();self.vol=self.returns.rolling(60,min_periods=60).std()*np.sqrt(365);self.market=self.logret[['BTCUSDT','ETHUSDT']].mean(axis=1);self._beta={};self._cov={}
 def beta(self,days):
  if days not in self._beta:
   var=self.market.rolling(days,min_periods=days).var();self._beta[days]=pd.DataFrame({s:self.logret[s].rolling(days,min_periods=days).cov(self.market)/var for s in self.symbols})
  return self._beta[days]
