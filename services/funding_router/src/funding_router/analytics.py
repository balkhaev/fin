from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .config import ExchangeSettings, RiskSettings
from .models import Candidate, MarketSnapshot, Rejection, Side


@dataclass(frozen=True, slots=True)
class Evaluation:
    candidate: Candidate | None
    rejection: Rejection | None


def _reject(long: MarketSnapshot, short: MarketSnapshot, reason: str, **details: float | str | bool | None) -> Evaluation:
    return Evaluation(
        candidate=None,
        rejection=Rejection(
            asset=long.asset,
            long_exchange=long.exchange_id,
            short_exchange=short.exchange_id,
            reason=reason,
            details=details,
        ),
    )


def evaluate_pair(
    long: MarketSnapshot,
    short: MarketSnapshot,
    risk: RiskSettings,
    exchanges: Mapping[str, ExchangeSettings],
) -> Evaluation:
    """Evaluate a same-asset long/short perpetual funding pair.

    Positive funding is paid by longs to shorts. Therefore a pair earns funding
    when the short venue's normalized rate exceeds the long venue's rate.
    """
    if long.asset != short.asset:
        return _reject(long, short, "asset_mismatch")
    if long.exchange_id == short.exchange_id:
        return _reject(long, short, "same_exchange")

    current_spread_hourly = short.quote.current_rate_per_hour - long.quote.current_rate_per_hour
    predicted_spread_hourly = short.quote.predicted_rate_per_hour - long.quote.predicted_rate_per_hour
    current_spread_bps_8h = current_spread_hourly * 8.0 * 10_000.0
    predicted_spread_bps_8h = predicted_spread_hourly * 8.0 * 10_000.0

    if current_spread_bps_8h < risk.min_current_spread_bps_8h:
        return _reject(
            long,
            short,
            "current_spread_below_threshold",
            current_spread_bps_8h=current_spread_bps_8h,
        )
    if predicted_spread_bps_8h < risk.min_predicted_spread_bps_8h:
        return _reject(
            long,
            short,
            "predicted_spread_below_threshold",
            predicted_spread_bps_8h=predicted_spread_bps_8h,
        )
    if risk.require_predicted_confirmation and (
        not long.quote.prediction_confirmed or not short.quote.prediction_confirmed
    ):
        return _reject(
            long,
            short,
            "prediction_unconfirmed",
            long_source=long.quote.prediction_source,
            short_source=short.quote.prediction_source,
        )

    mark_divergence_bps = abs(long.quote.mark_price / short.quote.mark_price - 1.0) * 10_000.0
    if mark_divergence_bps > risk.max_mark_divergence_bps:
        return _reject(
            long,
            short,
            "mark_divergence_too_wide",
            mark_divergence_bps=mark_divergence_bps,
        )

    for snapshot, label in ((long, "long"), (short, "short")):
        oi = snapshot.quote.open_interest_usdt
        if oi is None and not risk.allow_missing_open_interest:
            return _reject(long, short, f"{label}_open_interest_missing")
        if oi is not None and oi < risk.min_open_interest_usdt:
            return _reject(
                long,
                short,
                f"{label}_open_interest_below_threshold",
                open_interest_usdt=oi,
            )

    reference_price = (long.order_book.mid + short.order_book.mid) / 2.0
    if reference_price <= 0 or not math.isfinite(reference_price):
        return _reject(long, short, "invalid_reference_price")
    base_amount = risk.notional_usdt / reference_price

    try:
        long_vwap, long_quote_notional, long_slippage = long.order_book.vwap(Side.BUY, base_amount)
        short_vwap, short_quote_notional, short_slippage = short.order_book.vwap(Side.SELL, base_amount)
    except ValueError as exc:
        return _reject(long, short, "insufficient_depth", error=str(exc))

    long_available = long.order_book.available_base(Side.BUY)
    short_available = short.order_book.available_base(Side.SELL)
    long_depth_multiple = long_available / base_amount
    short_depth_multiple = short_available / base_amount
    if long_depth_multiple < risk.min_depth_multiple:
        return _reject(
            long,
            short,
            "long_depth_below_threshold",
            depth_multiple=long_depth_multiple,
        )
    if short_depth_multiple < risk.min_depth_multiple:
        return _reject(
            long,
            short,
            "short_depth_below_threshold",
            depth_multiple=short_depth_multiple,
        )

    entry_basis_bps = (long_vwap / short_vwap - 1.0) * 10_000.0
    if abs(entry_basis_bps) > risk.max_basis_bps:
        return _reject(
            long,
            short,
            "entry_basis_too_wide",
            entry_basis_bps=entry_basis_bps,
        )

    long_cfg = exchanges[long.exchange_id]
    short_cfg = exchanges[short.exchange_id]
    if long_depth_multiple <= short_depth_multiple:
        maker_exchange = long.exchange_id
        maker_side = Side.BUY
        entry_fee_bps = long_cfg.maker_fee_bps + short_cfg.taker_fee_bps
    else:
        maker_exchange = short.exchange_id
        maker_side = Side.SELL
        entry_fee_bps = short_cfg.maker_fee_bps + long_cfg.taker_fee_bps
    exit_fee_bps = long_cfg.taker_fee_bps + short_cfg.taker_fee_bps
    fee_bps = entry_fee_bps + exit_fee_bps

    slippage_bps = long_slippage + short_slippage + risk.slippage_buffer_bps
    conservative_spread_hourly = min(current_spread_hourly, predicted_spread_hourly)
    gross_funding_bps = conservative_spread_hourly * risk.hold_hours * 10_000.0
    safety_bps = risk.exit_basis_buffer_bps + risk.adverse_selection_buffer_bps
    # Do not credit a favorable entry basis. It may disappear before both legs close.
    basis_cost_bps = max(0.0, entry_basis_bps)
    expected_net_bps = gross_funding_bps - fee_bps - slippage_bps - safety_bps - basis_cost_bps
    if expected_net_bps < risk.min_expected_net_bps:
        return _reject(
            long,
            short,
            "expected_net_below_threshold",
            gross_funding_bps=gross_funding_bps,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            safety_bps=safety_bps,
            entry_basis_bps=entry_basis_bps,
            expected_net_bps=expected_net_bps,
            evaluation_hold_hours=risk.hold_hours,
        )

    observed_at_ms = max(long.quote.observed_at_ms, short.quote.observed_at_ms)
    candidate = Candidate(
        asset=long.asset,
        long_exchange=long.exchange_id,
        long_symbol=long.symbol,
        short_exchange=short.exchange_id,
        short_symbol=short.symbol,
        base_amount=base_amount,
        matched_notional_usdt=(long_quote_notional + short_quote_notional) / 2.0,
        long_entry_price=long_vwap,
        short_entry_price=short_vwap,
        current_spread_bps_8h=current_spread_bps_8h,
        predicted_spread_bps_8h=predicted_spread_bps_8h,
        gross_funding_bps=gross_funding_bps,
        entry_basis_bps=entry_basis_bps,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        safety_bps=safety_bps,
        expected_net_bps=expected_net_bps,
        evaluation_hold_hours=risk.hold_hours,
        long_open_interest_usdt=long.quote.open_interest_usdt,
        short_open_interest_usdt=short.quote.open_interest_usdt,
        long_depth_multiple=long_depth_multiple,
        short_depth_multiple=short_depth_multiple,
        long_funding_timestamp_ms=long.quote.funding_timestamp_ms,
        short_funding_timestamp_ms=short.quote.funding_timestamp_ms,
        long_interval_hours=long.quote.interval_hours,
        short_interval_hours=short.quote.interval_hours,
        observed_at_ms=observed_at_ms,
        maker_exchange=maker_exchange,
        maker_side=maker_side,
        maker_reference_price=(long.order_book.best_bid if maker_side == Side.BUY else short.order_book.best_ask),
        metadata={
            "long_prediction_source": long.quote.prediction_source,
            "short_prediction_source": short.quote.prediction_source,
            "mark_divergence_bps": mark_divergence_bps,
        },
    )
    return Evaluation(candidate=candidate, rejection=None)
