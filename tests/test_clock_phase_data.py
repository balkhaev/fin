"""Synthetic source tests, not financial evidence."""
import io
import zipfile
import numpy as np
import pandas as pd
import pytest
from research.clock_phase.data import parse,hourly,COLS


def minute_data(n=120):
    idx=pd.date_range('2024-01-01',periods=n,freq='min',tz='UTC')
    x=np.arange(n,dtype=float)+100
    return pd.DataFrame(dict(open=x,high=x+2,low=x-2,close=x+.5,volume=np.ones(n),
        quote_volume=x,trades=np.ones(n),buy_volume=np.full(n,.6),buy_quote=x*.6),index=idx)


def raw_zip(rows):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('data.csv','\n'.join(','.join(map(str,r)) for r in rows))
    return b.getvalue()


def raw_row(t=1704067200000):return [t,100,110,90,105,20,t+59999,2100,10,12,1260,0]


def test_minute_parser_normalizes_both_official_units():
    r=raw_row();a=parse(raw_zip([r]));r[0]*=1000;r[6]=r[0]+59999999
    b=parse(raw_zip([r]));pd.testing.assert_frame_equal(a,b)


@pytest.mark.parametrize('column,value',[(2,99),(3,106),(5,-1),(8,1.1),(9,21),(10,2200),(4,'nan'),(6,1704067259000)])
def test_bad_price_or_volume_refused(column,value):
    row=raw_row();row[column]=value
    with pytest.raises(ValueError):parse(raw_zip([row]))


def test_duplicate_or_off_minute_time_not_fixed():
    with pytest.raises(ValueError):parse(raw_zip([raw_row(),raw_row()]))
    with pytest.raises(ValueError):parse(raw_zip([raw_row(1704067200001)]))


def test_mixed_units_rejected():
    r=raw_row();s=raw_row(1704067260000);s[0]*=1000;s[6]=s[0]+59999999
    with pytest.raises(ValueError):parse(raw_zip([r,s]))


def test_boundary_and_shifted_windows_have_exact_four_minutes():
    d=minute_data();r=hourly(d)
    assert r.boundary_quote.iloc[0]==sum(100+x for x in (0,15,30,45))
    assert r.placebo_quote.iloc[0]==sum(100+x for x in (7,22,37,52))
    assert r.price2.iloc[0]==102 and r.price17.iloc[0]==117
    assert r.volume2.iloc[0]==1


def test_partial_hour_has_no_feature_values_but_keeps_observed_quotes():
    d=minute_data();d=d.drop(d.index[30]);r=hourly(d)
    assert not r.bar_ok.iloc[0] and r.bar_ok.iloc[1]
    assert pd.isna(r.close.iloc[0]) and pd.isna(r.boundary_quote.iloc[0])
    assert r.price2.iloc[0]==102


def test_hourly_aggregation_does_not_depend_on_future_hour():
    d=minute_data();a=hourly(d);b=hourly(d.iloc[:60])
    pd.testing.assert_frame_equal(a.iloc[:1],b)


def test_missing_execution_quote_never_interpolated():
    d=minute_data();d=d.drop(d.index[2]);r=hourly(d)
    assert pd.isna(r.price2.iloc[0]) and r.price17.iloc[0]==117


def test_execution_capacity_is_preceding_minute_not_future_hour_volume():
    d=minute_data();d.loc[d.index[1],'volume']=7;d.loc[d.index[2],'volume']=9000
    r=hourly(d);assert r.volume2.iloc[0]==7
