"""Separate missing trade activity from contemporaneous source observations.

A quote witness is NOT proof of complete trade-channel delivery. This policy
only permits retaining existing protected exposure; it never authorizes entry.
Thresholds are predeclared, not optimized against account returns.
"""
from dataclasses import dataclass
from typing import Mapping

SPOTS = ('binance', 'bybit-spot')
TRADE_FRESH_MS = 5000
QUOTE_WITNESS_MS = 5000
FLOW_SUPPORT_MS = 60000


@dataclass(frozen=True)
class SourceObservation:
    source: str
    trade_received_ms: float
    quote_received_ms: float
    quote_valid: bool


@dataclass(frozen=True)
class HealthDecision:
    retain_protected_position: bool
    reason: str
    quiet_sources: tuple[str, ...] = ()
    permits_new_entry: bool = False


def quiet_hold(now_ms: int, feature_reason: str, perp_trade_ms: float,
               execution_book_ms: int, execution_max_age_ms: int,
               sources: Mapping[str, SourceObservation],
               other_features_ready: bool = False) -> HealthDecision:
    """Fail closed on missing witnesses, future observations, or expired support."""
    import math
    if other_features_ready is not True:
        return HealthDecision(False, 'other_feature_prerequisites_unverified')
    if feature_reason != 'stale_trade':
        return HealthDecision(False, 'not_only_trade_inactivity')
    if not (math.isfinite(perp_trade_ms) and 0 < now_ms-perp_trade_ms <= TRADE_FRESH_MS):
        return HealthDecision(False, 'perpetual_trade_not_fresh')
    if not 0 <= now_ms-execution_book_ms <= execution_max_age_ms:
        return HealthDecision(False, 'execution_book_not_fresh')
    quiet = []
    for name in SPOTS:
        source = sources.get(name)
        if source is None or source.source != name:
            return HealthDecision(False, 'missing_or_mismatched_spot_source')
        age = now_ms-source.trade_received_ms
        if not math.isfinite(age) or not 0 < age < FLOW_SUPPORT_MS:
            return HealthDecision(False, 'spot_flow_support_expired')
        if age > TRADE_FRESH_MS:
            if (not source.quote_valid or not math.isfinite(source.quote_received_ms)
                    or not 0 < now_ms-source.quote_received_ms <= QUOTE_WITNESS_MS):
                return HealthDecision(False, 'quiet_spot_without_prior_quote')
            quiet.append(name)
    if not quiet:
        return HealthDecision(False, 'no_quiet_spot_explanation')
    return HealthDecision(True, 'quote_witnessed_quiet_spot', tuple(quiet))
