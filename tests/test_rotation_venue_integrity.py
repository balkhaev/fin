"""Small artificial archives exercise identity checks, never market results."""
import hashlib
import json
import pytest
import research.rotation_venue_transfer.data as data


def make_source(tmp_path,monkeypatch):
    end=data.START+2*data.DAY
    monkeypatch.setattr(data,'END',end)
    sources=[]
    for symbol in data.SYMBOLS:
        rows=[]
        for t in (data.START+data.DAY,data.START):
            rows.append([str(t),'100','110','90','105','20','2100','2100','1'])
        raw=json.dumps({'code':'0','data':rows}).encode()
        name=symbol+'-00.json';(tmp_path/name).write_bytes(raw)
        sources.append(dict(symbol=symbol,instrument=symbol[:-4]+'-USDT',status='complete',
            pages=[dict(filename=name,cursor=end,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))]))
    manifest=dict(start_ms=data.START,end_ms=end,bar='1Dutc',sources=sources)
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_loader_reconstructs_only_confirmed_complete_pages(tmp_path,monkeypatch):
    make_source(tmp_path,monkeypatch)
    frames,audit=data.load(tmp_path)
    assert set(frames)==set(data.SYMBOLS)
    assert all(len(frame)==2 for frame in frames.values())
    assert audit['all_confirmed'] and audit['local_hash_not_exchange_signature']


def test_tampered_market_page_fails_before_prices_used(tmp_path,monkeypatch):
    make_source(tmp_path,monkeypatch)
    path=tmp_path/'BTCUSDT-00.json'
    path.write_bytes(path.read_bytes().replace(b'105',b'106'))
    with pytest.raises(ValueError,match='SHA'):data.load(tmp_path)


def test_manifest_cannot_select_a_different_file_path(tmp_path,monkeypatch):
    manifest=make_source(tmp_path,monkeypatch)
    manifest['sources'][0]['pages'][0]['filename']='../other.json'
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Path'):data.load(tmp_path)


def test_missing_coin_invalidates_whole_fixed_cohort(tmp_path,monkeypatch):
    manifest=make_source(tmp_path,monkeypatch);manifest['sources'].pop()
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='universe'):data.load(tmp_path)


def test_wrong_spot_instrument_cannot_replace_required_asset(tmp_path,monkeypatch):
    manifest=make_source(tmp_path,monkeypatch)
    manifest['sources'][0]['instrument']='BTC-USDT-SWAP'
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='instrument'):data.load(tmp_path)


def test_rehashed_missing_day_still_fails_coverage(tmp_path,monkeypatch):
    manifest=make_source(tmp_path,monkeypatch)
    path=tmp_path/'BTCUSDT-00.json';payload=json.loads(path.read_text());payload['data'].pop()
    raw=json.dumps(payload).encode();path.write_bytes(raw)
    manifest['sources'][0]['pages'][0].update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='normalized'):data.load(tmp_path)
