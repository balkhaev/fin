"""Tests the new reporting wrapper, not a new trading signal or simulator."""
import copy
import numpy as np
import pandas as pd
import pytest
from research.guard_execution_audit.study import admission,audit,monthly,POLICIES,CADENCES,PRIMARY,CONTROL
from research.annual_rotation.model import Costs


def reports():
    full=dict(accounting_complete=True,cagr_pct=16.,max_close_drawdown_pct=-10.,return_pct=80.,
        annual=[{'full_year':True,'return_pct':5.}],closed_asset_positions=100)
    later=dict(accounting_complete=True,cagr_pct=8.,max_close_drawdown_pct=-8.,return_pct=10.,closed_asset_positions=50)
    primary={'full':full,'later':later}
    control=copy.deepcopy(primary);control['full']['cagr_pct']=15.;control['full']['max_close_drawdown_pct']=-12.
    control['later']['closed_asset_positions']=30
    return primary,control,[dict(accounting_complete=True,return_pct=4.)]*3


def test_primary_is_the_same_original_target_with_different_execution_cadence():
    assert PRIMARY[0]==CONTROL[0]=='guarded_ensemble20'
    assert PRIMARY[1]==1 and CONTROL[1]==7 and CADENCES==(1,3,7,14)
    assert len(POLICIES)==4


def test_gates_are_joint_conditions_not_best_return_selection():
    p,c,s=reports();assert all(admission(p,c,s).values())
    p['full']['max_close_drawdown_pct']=-30
    assert not all(admission(p,c,s).values())


@pytest.mark.parametrize('field,value',[('accounting_complete',False),('cagr_pct',None),('cagr_pct',14.)])
def test_incomplete_or_weaker_primary_cannot_pass(field,value):
    p,c,s=reports();p['full'][field]=value
    assert not all(admission(p,c,s).values())


def test_one_losing_full_year_is_not_hidden_by_total_profit():
    p,c,s=reports();p['full']['annual'].append({'full_year':True,'return_pct':-1.})
    assert not admission(p,c,s)['nonnegative_full_calendar_years']


def test_incomplete_or_losing_cost_stress_fails():
    p,c,s=reports();s[0]=dict(accounting_complete=False,return_pct=None)
    assert not admission(p,c,s)['all_later_stresses_positive']
    s[0]=dict(accounting_complete=True,return_pct=-.01)
    assert not admission(p,c,s)['all_later_stresses_positive']


def test_monthly_cash_is_zero_not_profit():
    c=pd.DataFrame({'time':['2024-02-01 00:00:00+00:00','2024-03-01 00:00:00+00:00'],'equity':[11000.,11000.]})
    r=monthly(c,10000)
    assert r[0]['return_pct']==pytest.approx(10) and r[1]['return_pct']==0


def test_fill_audit_uses_filled_quantities_prices_and_fees():
    fills=pd.DataFrame([dict(symbol='BTCUSDT',side='buy',quantity=1.,price=100.,fee=.1,cash_after=899.9),
                        dict(symbol='BTCUSDT',side='sell',quantity=1.,price=110.,fee=.11,cash_after=1009.79)])
    r=audit(fills,{'final_equity':1009.79,'accounting_complete':True},{},Costs(initial=1000),'2024-01-01')
    assert r['terminal_quantities']=={} and r['cash_reconciled']


def test_unliquidated_coins_are_not_silently_zeroed():
    fills=pd.DataFrame([dict(symbol='BTCUSDT',side='buy',quantity=1.,price=100.,fee=.1,cash_after=899.9)])
    frames={'BTCUSDT':pd.DataFrame({'close':[110.]},index=pd.to_datetime(['2023-12-31'],utc=True))}
    marked=899.9+110*(1-.0005)*(1-.001)
    r=audit(fills,{'final_equity':marked,'accounting_complete':False},frames,Costs(initial=1000),'2024-01-01')
    assert r['terminal_quantities']=={'BTCUSDT':1.}
    with pytest.raises(AssertionError):audit(fills,{'final_equity':marked,'accounting_complete':True},frames,Costs(initial=1000),'2024-01-01')


def test_wrong_cash_does_not_reconcile():
    fills=pd.DataFrame([dict(symbol='BTCUSDT',side='buy',quantity=1.,price=100.,fee=.1,cash_after=999.9)])
    with pytest.raises(AssertionError):audit(fills,{'final_equity':999.9,'accounting_complete':True},{},Costs(initial=1000),'2024-01-01')
