"""Numerical analysis regressions, not claims of profitable trading."""
import numpy as np
import pandas as pd
import pytest
from research.rotation_venue_transfer.study import align_targets,monthly,audit_fills
from research.annual_rotation.data import SYMBOLS


def index(n,start='2023-01-01'):return pd.date_range(start,periods=n,freq='D',tz='UTC')


def test_target_alignment_only_pads_pre_warmup_cash():
    src=index(5);dest=index(7,'2022-12-30');w=np.full((5,9),.01)
    r=align_targets(w,src,dest)
    assert not r[:2].any()
    np.testing.assert_array_equal(r[2:],w)


def test_missing_interior_targets_are_not_forward_filled():
    src=index(5).delete(2)
    with pytest.raises(ValueError):align_targets(np.zeros((4,9)),src,index(5))


def test_target_subsetting_preserves_absolute_dates():
    src=index(5);w=np.arange(45).reshape(5,9)/100
    np.testing.assert_array_equal(align_targets(w,src,src[2:]),w[2:])


def test_duplicate_timestamps_rejected():
    src=index(5)
    with pytest.raises(ValueError):align_targets(np.zeros((5,9)),src,src.append(src[-1:]))


def test_month_boundaries_reconcile_compounding():
    curve=pd.DataFrame(dict(time=['2024-01-31 00:00:00+00:00','2024-02-01 00:00:00+00:00','2024-02-02 00:00:00+00:00'],equity=[11000.,12100.,10890.]))
    r=monthly(curve,10000.)
    assert [(x['year'],x['month']) for x in r]==[(2024,1),(2024,2)]
    assert r[0]['return_pct']==pytest.approx(21)
    assert r[1]['return_pct']==pytest.approx(-10)
    assert np.prod([1+x['return_pct']/100 for x in r])==pytest.approx(1.089)


def test_cash_only_month_is_zero_not_a_win():
    curve=pd.DataFrame(dict(time=index(10).astype(str),equity=np.full(10,10000.)))
    assert all(x['return_pct']==0 for x in monthly(curve,10000))


def test_separate_fill_audit_reconstructs_prices_and_fees():
    f=pd.DataFrame([dict(side='buy',symbol='BTCUSDT',quantity=1.,price=100.,fee=.1,cash_after=899.9),
                    dict(side='sell',symbol='BTCUSDT',quantity=1.,price=110.,fee=.11,cash_after=1009.79)])
    report=dict(initial=1000.,final_equity=1009.79,settings={'fee':.001,'slip':.0005})
    r=audit_fills(f,report,.00000001,{},'2024-01-01')
    assert r['cash_reconciled'] and r['remaining_coins']=={}


def test_corrupted_account_balance_cannot_pass_fill_audit():
    f=pd.DataFrame([dict(side='buy',symbol='BTCUSDT',quantity=1.,price=100.,fee=.1,cash_after=9999.)])
    report=dict(initial=1000.,final_equity=1000.,settings={'fee':.001,'slip':.0005})
    with pytest.raises(AssertionError):audit_fills(f,report,.00000001,{},'2024-01-01')
