"""Finite public-data capture; never reads credentials or submits orders.

Raw messages are stored in LOCAL RECEIVE order, with monotonic sequence numbers.
Failures and reconnects are evidence too. A short recording is not a backtest year.
"""
from __future__ import annotations
import argparse
import asyncio
import gzip
import hashlib
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path

STREAMS = {
    'binance_spot': ('wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker/btcusdt@kline_1m', None),
    'binance_perp': ('wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@forceOrder/btcusdt@markPrice@1s/btcusdt@kline_1m', None),
    'binance_book': ('wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker', None),
    'bybit_spot': ('wss://stream.bybit.com/v5/public/spot', ['publicTrade.BTCUSDT','orderbook.1.BTCUSDT','kline.1.BTCUSDT']),
    'bybit_perp': ('wss://stream.bybit.com/v5/public/linear', ['publicTrade.BTCUSDT','orderbook.50.BTCUSDT','allLiquidation.BTCUSDT','tickers.BTCUSDT','kline.1.BTCUSDT']),
}
REST = {
    'binance_spot_bars': 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=121',
    'binance_perp_bars': 'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=121',
    'bybit_perp_bars': 'https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit=121',
    'bybit_spot_bars': 'https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=1&limit=121',
    'bybit_instrument': 'https://api.bybit.com/v5/market/instruments-info?category=linear&symbol=BTCUSDT',
    'binance_instrument': 'https://fapi.binance.com/fapi/v1/exchangeInfo',
}

async def capture(out: Path, seconds: int = 180) -> dict:
    import websockets
    if not 1 <= seconds <= 86400:
        raise ValueError('Capture duration must be between 1 and 86400 seconds')
    out.mkdir(parents=True, exist_ok=False)
    started = time.time_ns() // 1_000_000
    clock = time.monotonic_ns()
    counts, errors = Counter(), []
    sequence = 0
    target = out / 'raw.jsonl.gz'
    with gzip.open(target, 'wt', encoding='utf-8') as stream:
        def emit(source, kind, payload):
            nonlocal sequence
            sequence += 1
            now = started + (time.monotonic_ns()-clock)//1_000_000
            record = dict(seq=sequence, received_ms=now, source=source, kind=kind, payload=payload)
            stream.write(json.dumps(record, separators=(',', ':'), allow_nan=False)+'\n')
            counts[source+':'+kind] += 1
        async def fetch(name, url):
            def get():
                with urllib.request.urlopen(url, timeout=15) as response:
                    return json.load(response)
            try:
                result = await asyncio.to_thread(get)
                emit(name, 'rest', result)
            except Exception as exc:
                item = dict(source=name, error=str(exc), url=url)
                errors.append(item); emit(name,'error',item)
        await asyncio.gather(*(fetch(name,url) for name,url in REST.items()))
        deadline = time.monotonic()+seconds
        async def socket(name, url, topics):
            while time.monotonic() < deadline:
                try:
                    async with websockets.connect(url, open_timeout=15, close_timeout=2,
                              ping_interval=15, ping_timeout=10, max_queue=4096) as ws:
                        emit(name, 'connected', {'url':url, 'topics':topics})
                        if topics:
                            await ws.send(json.dumps({'op':'subscribe','args':topics}))
                        last_ping = time.monotonic()
                        while time.monotonic()<deadline:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=min(2, max(.01,deadline-time.monotonic())))
                                emit(name,'message',json.loads(raw))
                            except asyncio.TimeoutError:
                                pass
                            if time.monotonic()-last_ping >= 10:
                                if topics:
                                    await ws.send(json.dumps({'op':'ping'}))
                                pong = await ws.ping()
                                await asyncio.wait_for(pong,timeout=5)
                                emit(name,'heartbeat',{})
                                last_ping=time.monotonic()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    item=dict(source=name,error=str(exc)); errors.append(item)
                    emit(name,'disconnect',item)
                    if 'HTTP 451' in str(exc) or 'HTTP 403' in str(exc):
                        break
                    await asyncio.sleep(min(2,max(0,deadline-time.monotonic())))
            emit(name,'capture_end',{})
        tasks=[asyncio.create_task(socket(name,*spec)) for name,spec in STREAMS.items()]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks),timeout=seconds+20)
        except asyncio.TimeoutError:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            emit('recorder','timeout',{})
    ended=started+(time.monotonic_ns()-clock)//1_000_000
    manifest=dict(schema='btc-pressure-raw-v1',start_ms=started,end_ms=ended,
                  requested_stream_seconds=seconds,records=sequence,counts=dict(counts),
                  errors=errors,raw_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                  collection_only=True,annual_backtest=False,live_orders=False,
                  synthetic=False,capture_origin='public_websocket',
                  sources=STREAMS,rest_endpoints=REST)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=180)
    args=parser.parse_args()
    asyncio.run(capture(args.out,args.seconds))
