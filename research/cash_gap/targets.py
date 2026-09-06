"""Historical spot-target composition, not an exchange or account executor.

Uses only already published price-based signals. No funding input, derivatives,
borrowed money or implementation of previously blocked research modules.
The regime describes TARGET intent; only the old simulator determines holdings.
"""
import numpy as np
import pandas as pd
from research.annual_rotation.data import SYMBOLS
from research.rotation_stability.policy import build as old_build, PRIMARY as OLD_PRIMARY

PRIMARY = 'idle_trend10'
NAMES = ('core_weekly', 'idle_trend05', PRIMARY, 'idle_trend20', 'idle_vol10',
         'idle_breadth10', 'idle_btc10', 'idle_trend10_daily', 'always_blend10',
         'sleeve_trend10_only', 'sleeve_btc10_only', 'cash', 'pr132_budget25_every3')


def compose(core, sleeve, idle, scale):
    core = np.asarray(core, dtype=float)
    sleeve = np.asarray(sleeve, dtype=float)
    idle = np.asarray(idle, dtype=bool)
    if core.ndim != 2 or sleeve.shape != core.shape or idle.shape != (len(core),):
        raise ValueError('Misaligned target arrays')
    if not 0 < scale <= 1:
        raise ValueError('Invalid sleeve budget')
    for array in (core, sleeve):
        if not np.isfinite(array).all() or (array < 0).any() or (array.sum(axis=1) > 1+1e-10).any():
            raise ValueError('Non-spot target')
    if np.any(core[idle] != 0):
        raise ValueError('Sleeve permission overlaps a nonzero core target')
    result = core.copy()
    result[idle] = scale*sleeve[idle]
    return result


def build(frames):
    if set(frames) != set(SYMBOLS):
        raise ValueError('Fixed original universe required')
    index = frames[SYMBOLS[0]].index
    if str(index.tz) != 'UTC' or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError('Unique sorted UTC dates required')
    if len(index) > 1 and not np.all(np.diff(index.asi8) == 86400000000000):
        raise ValueError('Dropped daily rows are not supported')
    if any(not d.index.equals(index) for d in frames.values()):
        raise ValueError('Unequal asset grids')
    targets, diagnostics = old_build(frames)
    core = targets[OLD_PRIMARY]
    trace = diagnostics[OLD_PRIMARY]
    close = pd.DataFrame({s: frames[s].close for s in SYMBOLS})
    complete = close.notna().all(axis=1).rolling(201, min_periods=201).sum().eq(201).to_numpy()
    active = trace.market_allowed.to_numpy(bool)
    idle = complete & ~active
    ordinary = targets['ensemble_unscaled']
    btc = np.zeros_like(core)
    btc[:, 0] = 1.
    out = {'core_weekly': core.copy()}
    for fraction, name in ((.05, 'idle_trend05'), (.10, PRIMARY), (.20, 'idle_trend20')):
        out[name] = compose(core, ordinary, idle, fraction)
    out['idle_vol10'] = compose(core, targets['ensemble_vol20'], idle, .5)
    breadth = trace.breadth.ge(1/3).rolling(3, min_periods=3).sum().eq(3).to_numpy()
    out['idle_breadth10'] = compose(core, ordinary, idle & breadth, .1)
    out['idle_btc10'] = compose(core, btc, idle, .1)
    out['idle_trend10_daily'] = out[PRIMARY].copy()
    out['always_blend10'] = .9*core + .1*ordinary*complete[:, None]
    out['sleeve_trend10_only'] = compose(np.zeros_like(core), ordinary, idle, .1)
    out['sleeve_btc10_only'] = compose(np.zeros_like(core), btc, idle, .1)
    out['cash'] = np.zeros_like(core)
    # Keep the old target unchanged; the caller passes Costs(allocation=.25).
    out['pr132_budget25_every3'] = targets['ensemble_market_gate'].copy()
    if set(out) != set(NAMES):
        raise AssertionError('Protocol registry mismatch')
    for name, value in out.items():
        if not np.isfinite(value).all() or (value < 0).any() or (value.sum(axis=1) > 1+1e-10).any():
            raise AssertionError('Invalid funded allocation: '+name)
    diag = pd.DataFrame({'signal_date': index.astype(str), 'complete_history': complete,
        'core_market_allowed': active, 'idle_sleeve_allowed': idle,
        'primary_core_target': core.sum(axis=1), 'primary_target': out[PRIMARY].sum(axis=1),
        'sleeve_target': out['sleeve_trend10_only'].sum(axis=1), 'breadth': trace.breadth.to_numpy()})
    return out, diag


def cadence(name):
    if name not in NAMES:
        raise ValueError('Unknown protocol variant')
    return 1 if name == 'idle_trend10_daily' else 3 if name == 'pr132_budget25_every3' else 7
