"""Regressions found by source/ledger audit, not parameter tuning."""
import numpy as np
import pytest
from research.annual_rotation.model import Costs,Config,simulate
from test_annual_rotation import fake,flat,replay
from research.annual_rotation.data import SYMBOLS


def test_integer_lot_accounting_does_not_strand_a_phantom_terminal_lot():
    f=fake(300);w=np.zeros((300,len(SYMBOLS)))
    for i in range(300):
        w[i,[(i//11+j)%len(SYMBOLS) for j in range(3)]]=1/3
    r,l,_=replay(f,w)
    assert r['accounting_complete'] and r['open_assets']==0
    # Independently sum integer units, not repeated floating additions.
    net={s:0 for s in SYMBOLS}
    for x in l.itertuples():net[x.symbol]+=(1 if x.side=='buy' else -1)*round(x.quantity/1e-8)
    assert set(net.values())=={0}


def test_end_of_test_close_cannot_hide_last_day_low_stress():
    f=flat();f['BTCUSDT'].iloc[-1,f['BTCUSDT'].columns.get_loc('low')]=40.
    r,_,_=replay(f,cost=Costs(fee=0,slip=0))
    assert r['simultaneous_daily_low_stress_pct'] < -59


def test_buy_hold_has_only_one_purchase_and_one_terminal_sale():
    f=fake(300);idx=f['BTCUSDT'].index;w=np.zeros((300,len(SYMBOLS)));w[:,0]=1
    r,l,_=simulate(f,w,Config('raw',21,1,7),str(idx[5].date()),str((idx[-1]+__import__('pandas').Timedelta(days=1)).date()),hold_only=True)
    assert r['order_fills']==2 and list(l.side)==['buy','sell']
    assert r['rebalance_days']==1 and r['accounting_complete']
