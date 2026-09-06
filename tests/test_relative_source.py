import io,zipfile
import numpy as np
import pandas as pd
import pytest
from research.relative_futures.data import parse_prices


def zipped(rows):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('data.csv','\n'.join(','.join(map(str,r)) for r in rows))
    return b.getvalue()

def row(t=1704067200000):return [t,100,110,90,105,20,t+3599999,2100,10,12,1260,0]

def test_trade_and_mark_prices_preserve_ohlc_and_time():
    for kind in ('klines','markPriceKlines'):
        d=parse_prices(zipped([row()]),kind)
        assert d.open.iloc[0]==100 and d.index[0]==pd.Timestamp('2024-01-01',tz='UTC')

@pytest.mark.parametrize('position,value',[(2,99),(3,106),(4,0),(4,'nan'),(5,-1),(0,1704067200001),(6,1704070800000)])
def test_bad_bars_not_repaired(position,value):
    r=row();r[position]=value
    with pytest.raises(ValueError):parse_prices(zipped([r]),'klines')

def test_duplicate_hour_rejected():
    with pytest.raises(ValueError):parse_prices(zipped([row(),row()]),'klines')

def test_short_bar_becomes_nan_at_original_timestamp():
    r=row();r[6]=r[0]+10000;d=parse_prices(zipped([r]),'klines')
    assert d.iloc[0].isna().all() and len(d)==1

def test_explicit_microsecond_normalization():
    a=row();b=row();b[0]*=1000;b[6]=b[0]+3599999999
    pd.testing.assert_frame_equal(parse_prices(zipped([a]),'klines'),parse_prices(zipped([b]),'klines'))

def test_header_is_not_a_price_observation():
    from research.relative_futures.data import PRICE_COLS
    d=parse_prices(zipped([PRICE_COLS,row()]),'klines')
    assert len(d)==1
