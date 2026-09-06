"""Source-contract fixtures, never evidence of market profit."""
import copy
import json
import hashlib
import pytest
from research.rotation_venue_transfer.data import parse_page,collect_rows,DAY,START,END


def candle(t=START):
    return [str(t),'100','110','90','105','20','2100','2105','1']


def payload(rows=None):return {'code':'0','msg':'','data':rows if rows is not None else [candle()]}


def test_spot_capacity_is_base_volume_and_ranking_uses_quote_volume():
    r=parse_page(payload(),END)[0]
    assert r['volume']==20 and r['quote_volume']==2105
    assert r['quote_volume']!=2100


def test_timestamp_preserved_not_rounded():
    r=parse_page(payload(),END)[0]
    assert r['time']==START
    with pytest.raises(ValueError):parse_page(payload([candle(START+3600000)]),END)


@pytest.mark.parametrize('field,value',[(8,'0'),(0,str(END)),(0,str(START*1000)),(1,'nan'),(4,'inf'),(5,'-1'),(7,'-1'),(1,'0'),(2,'99'),(3,'106')])
def test_invalid_or_unconfirmed_row_fails_closed(field,value):
    row=candle();row[field]=value
    with pytest.raises(ValueError):parse_page(payload([row]),END)


@pytest.mark.parametrize('response',[None,[],{'code':'51001','data':[]},{'code':'0','data':{}},{'code':'0','data':[['1']]}])
def test_error_response_cannot_be_treated_as_healthy_empty_market(response):
    with pytest.raises(ValueError):parse_page(response,END)


def test_empty_page_is_an_explicit_empty_list():assert parse_page(payload([]),END)==[]


def test_page_is_reverse_time_and_strictly_before_cursor():
    r=parse_page(payload([candle(START+DAY),candle(START)]),END)
    assert r[0]['time']>r[1]['time']
    with pytest.raises(ValueError):parse_page(payload([candle(),candle(START+DAY)]),END)
    with pytest.raises(ValueError):parse_page(payload([candle()]),START)


def test_duplicate_inside_page_is_not_silently_deduplicated():
    with pytest.raises(ValueError):parse_page(payload([candle(),candle()]),END)


def test_duplicate_between_pages_is_rejected():
    r=parse_page(payload(),END)
    with pytest.raises(ValueError):collect_rows([r,r])


def test_collect_does_not_synthesize_missing_days():
    r=parse_page(payload([candle(START+2*DAY),candle(START)]),END)
    assert [x['time'] for x in collect_rows([r])]==[START,START+2*DAY]


def test_prefetch_before_fixed_warmup_is_not_added_to_evaluation():
    r=parse_page(payload([candle(START),candle(START-DAY)]),END)
    assert len(collect_rows([r]))==1


def test_high_and_low_consistency():
    row=candle();row[2]='101'
    with pytest.raises(ValueError):parse_page(payload([row]),END)


def test_confirmed_does_not_override_future_bar_rejection():
    with pytest.raises(ValueError):parse_page(payload([candle(END)]),END)
