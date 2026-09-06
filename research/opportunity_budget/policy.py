"""Conditional entry-risk allocation for OFFLINE research, not a broker or ledger.

Uses the published account unchanged. Its known missing-mark valuation bug remains
unfixed. Targets are requests; quantity/fee/funding accounting belongs to that
reference, and actual gross can drift or overshoot before a delayed exit.
"""
from dataclasses import dataclass
from typing import Mapping
import numpy as np
import pandas as pd
from research.relative_futures.data import SYMBOLS
from research.relative_futures.signals import build as legacy_build

PRIMARY = "multi_risk30_cap15"
MODELS = ("old_pair_1x", "old_pair_15x", "old_pair_2x", "pair_risk30_cap15",
          "btc_slow_risk30_cap15", PRIMARY, "multi_risk30_cap2",
          "multi_risk20_cap15", "multi_risk30_no_refresh")
HOURS_PER_YEAR = 24 * 365.25
PAIRS = ((48, 192), (168, 672), (672, 2688))
BASIS = np.array([[1., 0.], [0., 1.], [-.5, .5]])
RISK_SHARES = np.array([.5, .25, .25])

@dataclass(frozen=True)
class Allocation:
    annual_risk: float = .30
    cap: float = 1.5
    refresh: bool = True

    def __post_init__(self):
        if not np.isfinite([self.annual_risk, self.cap]).all():
            raise ValueError("Finite allocation settings required")
        if not 0 < self.annual_risk <= .5 or not .1 <= self.cap <= 2:
            raise ValueError("Outside declared research limits")


def freeze_and_refresh(desired: np.ndarray, refresh: bool = True):
    """No same-state pyramiding. A risk reduction is a delayed flat/re-entry request.

The zero interval resets the legacy sign-state executor. We do not assume that
four signal-flat hours guarantee four actual flat hours under capacity limits.
    """
    if desired.ndim != 2 or desired.shape[1] != 2 or not np.isfinite(desired).all():
        raise ValueError("Finite Nx2 requested notionals required")
    output = np.zeros_like(desired)
    refreshes = np.zeros(len(desired), bool)
    frozen = np.zeros(2); signature = (0, 0); last_change = -10000; flat_until = -1
    for i, row in enumerate(desired):
        if i < flat_until:
            continue
        new_signature = tuple(np.sign(row).astype(int))
        if new_signature != signature:
            signature = new_signature; frozen = row.copy(); last_change = i
        elif (refresh and any(signature) and i-last_change >= 72
              and np.abs(row).sum() < .75*np.abs(frozen).sum()):
            refreshes[i] = True
            flat_until = i+4; signature = (0, 0); frozen = np.zeros(2)
            continue
        output[i] = frozen
    return output, refreshes


def directional_states(log_close: np.ndarray, score: np.ndarray,
                       agreement: np.ndarray, vol: np.ndarray, support: np.ndarray,
                       clock: np.ndarray):
    n = len(log_close)
    direction = np.zeros(n); confidence = np.zeros(n)
    side = 0; cooldown_until = -1; high_water = 0.; trail = 0.
    for i in range(n):
        if not support[i] or not np.isfinite([log_close[i], score[i], vol[i]]).all():
            side = 0; cooldown_until = max(cooldown_until, i+24)
            continue
        if side:
            high_water = max(high_water, side*log_close[i])
        if clock[i]:
            if side and (side*score[i] <= .1 or high_water-side*log_close[i] >= trail):
                side = 0; cooldown_until = i+24
            elif not side and i >= cooldown_until and abs(score[i]) >= .5 and agreement[i] >= 2:
                side = int(np.sign(score[i])); high_water = side*log_close[i]
                trail = 4*vol[i]*np.sqrt(24)
        direction[i] = side
        if side:
            confidence[i] = np.clip(side*score[i]/1.5, 0., 1.)
    return direction, confidence


def risk_allocate(states, confidence, cov_fast, cov_slow, use, cfg):
    """Combine sleeve RISK shares, then scale NET contracts by full covariance.

0.1% hourly diagonal floor is added as idiosyncratic uncertainty. The greater of
fast/slow portfolio variance is used; correlation is not silently assumed zero.
Confidence is a score, not an estimated probability or expected return.
    """
    risk = RISK_SHARES * np.asarray(use, dtype=float)
    risk = risk / risk.sum()
    sleeve_var_fast = np.einsum("si,nij,sj->ns", BASIS, cov_fast, BASIS)
    sleeve_var_slow = np.einsum("si,nij,sj->ns", BASIS, cov_slow, BASIS)
    sleeve_sd = np.sqrt(np.maximum(sleeve_var_fast, sleeve_var_slow))
    active = (states != 0) & (np.asarray(use) > 0)
    sleeve_loading = (states * risk[None, :] * (.5+.5*confidence)
                      / np.maximum(sleeve_sd, 1e-6))
    direction = sleeve_loading @ BASIS
    gross = np.abs(direction).sum(axis=1)
    unit = np.divide(direction, gross[:, None], out=np.zeros_like(direction), where=gross[:, None] > 1e-12)
    variance = np.maximum(np.einsum("ni,nij,nj->n", unit, cov_fast, unit),
                          np.einsum("ni,nij,nj->n", unit, cov_slow, unit))
    vol = np.sqrt(np.maximum(variance, 1e-12) * HOURS_PER_YEAR)
    count = active.sum(axis=1)
    conf = np.divide((confidence*active).sum(axis=1), count,
                     out=np.zeros(len(count)), where=count > 0)
    # The cap2 comparator may reach two only at strong confidence; the primary
    # remains capped at1.5. The floor budget is not a floor forcing a position.
    ceiling = .5+(cfg.cap-.5)*conf
    scale = np.minimum(ceiling, cfg.annual_risk/np.maximum(vol, 1e-12)) * (.5+.5*conf)
    scale[gross < 1e-12] = 0
    desired = unit*scale[:, None]
    return desired, vol, conf


def build(frames: Mapping[str, pd.DataFrame]):
    legacy, _ = legacy_build(frames)
    index = frames[SYMBOLS[0]].index
    close = pd.DataFrame({s: frames[s].close for s in SYMBOLS})
    log_close = np.log(close)
    returns = log_close.diff()
    support = close.notna().all(axis=1).rolling(2689, min_periods=2689).sum().eq(2689).to_numpy()
    fast_sd = returns.ewm(span=168, adjust=False, min_periods=168).std()
    slow_sd = returns.ewm(span=720, adjust=False, min_periods=720).std()
    sd = np.maximum(fast_sd.to_numpy(), slow_sd.to_numpy())
    sd = np.maximum(sd, .001)
    scores = []
    for fast, slow in PAIRS:
        difference = close.ewm(span=fast, adjust=False, min_periods=slow).mean() - close.ewm(span=slow, adjust=False, min_periods=slow).mean()
        scores.append(difference.to_numpy() / (close.to_numpy()*sd*np.sqrt((slow-fast)/2)))
    scores = np.stack(scores)
    mean_score = scores.mean(axis=0)
    agreement = (np.sign(scores) == np.sign(mean_score)[None, :, :]).sum(axis=0)
    # Signal at hour03 includes its close04; it executes under the original
    # account at hour05 (closed-hour target index i-2), not at that same close.
    clock = (index.hour.to_numpy()+1) % 4 == 0
    states = np.zeros((len(index), 3)); confidence = np.zeros_like(states)
    for k in range(2):
        states[:, k], confidence[:, k] = directional_states(log_close.iloc[:, k].to_numpy(), mean_score[:, k],
            agreement[:, k], sd[:, k], support, clock)
    old_pair = legacy['pair_momentum720']
    states[:, 2] = np.sign(old_pair[:, 1])
    ratio = log_close.iloc[:, 1] - log_close.iloc[:, 0]
    z = (ratio-ratio.shift(720))/(ratio.diff().rolling(168, min_periods=168).std()*np.sqrt(720))
    confidence[:, 2] = np.nan_to_num(np.clip(np.abs(z.to_numpy())/3., 0., 1.))
    states[~support] = 0.; confidence[~support] = 0.
    covariances = []
    for span in (168, 720):
        covariance = returns.ewm(span=span, adjust=False, min_periods=span).cov().to_numpy().reshape(-1, 2, 2)
        # Used only before support/entry is allowed; never fill a traded price.
        covariance = np.nan_to_num(covariance, nan=0.)
        covariance[:, 0, 0] += .001**2; covariance[:, 1, 1] += .001**2
        covariances.append(covariance)
    targets = {'old_pair_1x': old_pair.copy(), 'old_pair_15x': old_pair.copy(), 'old_pair_2x': old_pair.copy()}
    diagnostics = []
    registry = {
        'pair_risk30_cap15': ((0, 0, 1), Allocation()),
        'btc_slow_risk30_cap15': ((1, 0, 0), Allocation()),
        PRIMARY: ((1, 1, 1), Allocation()),
        'multi_risk30_cap2': ((1, 1, 1), Allocation(cap=2.)),
        'multi_risk20_cap15': ((1, 1, 1), Allocation(annual_risk=.20)),
        'multi_risk30_no_refresh': ((1, 1, 1), Allocation(refresh=False)),
    }
    for name, (use, cfg) in registry.items():
        desired, vol, conf = risk_allocate(states, confidence, *covariances, use, cfg)
        desired[~support] = 0.
        requested, refresh = freeze_and_refresh(desired, cfg.refresh)
        # Unchanged engine limits targetL1<=1 and allows Costs.gross<=2.
        targets[name] = requested/2.
        diagnostics.append(pd.DataFrame({'time': index.astype(str), 'model': name,
            'target_gross': np.abs(requested).sum(axis=1), 'desired_gross': np.abs(desired).sum(axis=1),
            'confidence': conf, 'unit_portfolio_annual_volatility': vol,
            'risk_refresh': refresh, 'btc_state': states[:, 0], 'eth_state': states[:, 1],
            'relative_state': states[:, 2]}))
    if set(targets) != set(MODELS):
        raise AssertionError("Protocol registry mismatch")
    for name, value in targets.items():
        if not np.isfinite(value).all() or (np.abs(value).sum(axis=1) > 1+1e-12).any():
            raise AssertionError("Invalid target: "+name)
    return targets, pd.concat(diagnostics, ignore_index=True)


def engine_gross(name):
    if name not in MODELS:
        raise ValueError("Unregistered model")
    return {'old_pair_1x': 1., 'old_pair_15x': 1.5}.get(name, 2.)
