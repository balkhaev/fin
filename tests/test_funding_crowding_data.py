"""Synthetic funding-source regressions, not profitability evidence."""
import io
import zipfile
import numpy as np
import pandas as pd
import pytest
from research.funding_crowding.data import parse,daily_rates,FIELDS


def events():
    idx=pd.date_range('2024-01-01',periods=15,freq='8h',tz='UTC')
    return pd.DataFrame({'rate':np.full(len(idx),.0001),'interval_hours':np.full(len(idx),8.)},index=idx)


def archive(rows):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('funding.csv',','.join(FIELDS)+'\n'+'\n'.join(','.join(map(str,row)) for row in rows))
    return b.getvalue()


def test_three_settlements_are_not_confused_with_daily_rate():
    idx=pd.date_range('2024-01-01',periods=5,tz='UTC')
    result,audit=daily_rates(events(),idx)
    assert pd.isna(result.iloc[0])
    np.testing.assert_allclose(result.iloc[1:],.0003)
    assert audit['valid_days']==4


def test_variable_four_hour_intervals_sum_observed_payments():
    f=events();i=pd.date_range('2024-01-04',periods=12,freq='4h',tz='UTC')
    g=pd.DataFrame({'rate':np.full(12,.00005),'interval_hours':np.full(12,4.)},index=i)
    combined=pd.concat([f.loc[:'2024-01-03'],g])
    idx=pd.date_range('2024-01-01',periods=5,tz='UTC')
    r,_=daily_rates(combined,idx)
    assert pd.isna(r.iloc[3]) # Interval-transition report does not cover24 contiguous hours.
    assert r.iloc[4]==pytest.approx(.0003)


def test_missing_settlement_is_not_zero_or_forward_filled():
    f=events();f=f.drop(f.index[5]);r,_=daily_rates(f,pd.date_range('2024-01-01',periods=5,tz='UTC'))
    assert pd.isna(r.iloc[1]) and pd.isna(r.iloc[2])
    assert r.iloc[3]==pytest.approx(.0003)


def test_midnight_payment_belongs_to_new_day_only():
    f=events();idx=pd.date_range('2024-01-01',periods=5,tz='UTC')
    original,_=daily_rates(f,idx);f.iloc[6,0]=.25
    changed,_=daily_rates(f,idx)
    assert changed.iloc[1]==original.iloc[1]
    assert changed.iloc[2]>original.iloc[2]


def test_prefix_of_daily_information_is_causal():
    f=events();idx=pd.date_range('2024-01-01',periods=5,tz='UTC')
    a,_=daily_rates(f,idx);b,_=daily_rates(f.iloc[:9],idx[:3])
    pd.testing.assert_series_equal(a.iloc[:3],b)


def test_duplicate_settlement_rejected():
    f=events();f=pd.concat([f,f.iloc[-1:]])
    with pytest.raises(ValueError):daily_rates(f,pd.date_range('2024-01-01',periods=5,tz='UTC'))


def test_parser_preserves_timestamp_and_signed_rate():
    t=1704067200000;f=parse(archive([[t,8,-.0002]]))
    assert f.index[0]==pd.Timestamp('2024-01-01',tz='UTC') and f.rate.iloc[0]==-.0002


@pytest.mark.parametrize('row',[[1704067200000,3,.1],[1704067200000,8,'nan'],[1704067200000,8,1.2],[1704067200000.5,8,.1]])
def test_invalid_source_fields_rejected(row):
    with pytest.raises(ValueError):parse(archive([row]))


def test_duplicate_source_rows_not_combined_into_larger_payment():
    with pytest.raises(ValueError):parse(archive([[1704067200000,8,.0001]]*2))


def test_completely_missing_history_remains_nan():
    f=events().iloc[:0];r,_=daily_rates(f,pd.date_range('2024-01-01',periods=5,tz='UTC'))
    assert r.isna().all()
