"""Read extra aggressor fields from hash-verified prior public spot archives.

This is aggressor-side executed volume, not net capital inflow or a reconstructed
limit-order book. Original price loader and accounting remain unchanged.
"""
from pathlib import Path
import hashlib
import io
import json
import zipfile
import numpy as np
import pandas as pd
from research.annual_rotation.data import load as old_load, SYMBOLS, COLS, normalize_time

MANIFEST_SHA = 'da9ca6d1e782e8ef6c816390ef3e6ea363eec53a67f58592a8505d754bf5bfe2'


def validate_extra(frame):
    """Reject corrupted volume/counts; do not repair them into tradable signals."""
    columns = ['volume', 'quote_volume', 'trades', 'buy_volume', 'buy_quote']
    a = frame[columns].to_numpy(float)
    if not np.isfinite(a).all() or (a < 0).any():
        raise ValueError('Negative or nonfinite executed volume/count')
    if not np.equal(frame.trades, np.floor(frame.trades)).all():
        raise ValueError('Fractional trade count')
    for numerator, total in [('buy_volume', 'volume'), ('buy_quote', 'quote_volume')]:
        if (frame[numerator] > frame[total] + 1e-9 * np.maximum(1., frame[total])).any():
            raise ValueError('Taker-buy volume exceeds total volume')
    if ((frame.trades == 0) & ((frame.volume > 0) | (frame.quote_volume > 0))).any():
        raise ValueError('Positive executed volume without trades')


def load(root):
    root = Path(root)
    raw = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError('Different precommitted source snapshot')
    frames, old_audit = old_load(root)  # Checks every official archive hash/OHLC.
    manifest = json.loads(raw)
    grouped = {s: [] for s in SYMBOLS}
    for month in manifest['files']:
        parts = month['parts'] if month.get('frequency') == 'daily' else [month]
        for part in parts:
            content = (root / part['filename']).read_bytes()
            if hashlib.sha256(content).hexdigest() != part['sha256']:
                raise ValueError('Raw page changed during read')
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                with z.open(z.namelist()[0]) as f:
                    d = pd.read_csv(f, names=COLS, header=None, dtype=str)
            bad = pd.to_numeric(d.time, errors='coerce').isna()
            if bad.any():
                if bad.sum() != 1 or not bad.iloc[0]:
                    raise ValueError('Malformed timestamp row')
                d = d.iloc[1:]
            d = d.apply(pd.to_numeric, errors='raise')
            d['time'] = normalize_time(d.time)
            if (d.time % 86400000 != 0).any():
                raise ValueError('Off-grid flow observation; no rounding')
            validate_extra(d)
            grouped[month['symbol']].append(d)
    row_count = 0
    for symbol in SYMBOLS:
        d = pd.concat(grouped[symbol], ignore_index=True).sort_values('time')
        if d.time.duplicated().any():
            raise ValueError('Duplicate aggressor row')
        d.index = pd.to_datetime(d.pop('time'), unit='ms', utc=True)
        base = frames[symbol]
        if not d.index.equals(base.index):
            raise ValueError('Flow and price coverage differ')
        for col in ('open', 'high', 'low', 'close', 'volume', 'quote_volume'):
            np.testing.assert_array_equal(d[col].to_numpy(), base[col].to_numpy())
        for col in ('trades', 'buy_volume', 'buy_quote'):
            base[col] = d[col]
        row_count += len(d)
    return frames, dict(old_audit, extra_fields=['trades', 'buy_volume', 'buy_quote'],
        extra_rows=row_count, aggressor_not_net_inflow=True,
        prior_OHLC_exactly_matched=True, volumes_repaired=False)
