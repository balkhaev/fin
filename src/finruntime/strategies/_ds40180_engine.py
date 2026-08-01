"""Causal DS-40/180 regime and T50-C3 target builder."""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any

from ._ds40180_common import (
    ASSETS,
    ASSET_VOLATILITY_LOOKBACK,
    HISTORY_LIMIT,
    META_EXECUTION_COST,
    MINIMUM_ASSETS,
    MINIMUM_COMMON_DAYS,
    MINIMUM_HISTORY,
    MINIMUM_MEDIAN_QUOTE_VOLUME_USD,
    RISK_SCALE_CAP,
    RISK_SCALE_EWM_SPAN,
    RISK_SCALE_FLOOR,
    RISK_SCALE_LOOKBACK,
    SLEEVE_ANNUAL_DAYS,
    TARGET_VOLATILITY,
    _annualized_volatility,
    _clamp,
    _ema,
    _ewm_adjust_false,
    _finite,
    _hysteresis,
    _population_standard_deviation,
    _weekly_hold,
    _zero_row,
)
from ._ds40180_signals import (
    _apply_target_safety,
    _donchian_score,
    _ema_score,
    _momentum_score,
    _run_sleeve,
)


def build_engine(
    histories: list[dict[str, Any]], failed_assets: list[dict[str, str]]
) -> dict[str, Any]:
    if not histories:
        raise ValueError("DS-40/180 received no usable OKX histories")
    history_by_asset = {history["asset"]: history for history in histories}
    latest_market_date = max(
        date.fromisoformat(max(history["bars"])) for history in histories
    )
    inactive_assets = [
        asset
        for asset, history in history_by_asset.items()
        if len(history["bars"]) < MINIMUM_COMMON_DAYS
        or (latest_market_date - date.fromisoformat(max(history["bars"]))).days > 2
    ]
    assets = [
        asset
        for asset in ASSETS
        if asset in history_by_asset and asset not in inactive_assets
    ]
    if "BTC" not in assets:
        raise ValueError("BTC-USDT-SWAP history is required")
    if len(assets) < MINIMUM_ASSETS:
        raise ValueError(
            f"Only {len(assets)} OKX swaps have sufficient history; "
            f"at least {MINIMUM_ASSETS} are required"
        )
    common_dates: set[str] | None = None
    for asset in assets:
        asset_dates = set(history_by_asset[asset]["bars"])
        common_dates = asset_dates if common_dates is None else common_dates & asset_dates
    market_dates = sorted(common_dates or ())[-HISTORY_LIMIT:]
    if len(market_dates) < MINIMUM_COMMON_DAYS:
        raise ValueError(
            f"Only {len(market_dates)} common closed daily candles are available; "
            f"at least {MINIMUM_COMMON_DAYS} are required"
        )

    execution_date = (
        date.fromisoformat(market_dates[-1]) + timedelta(days=1)
    ).isoformat()
    dates = [*market_dates, execution_date]
    market_closes = [
        [float(history_by_asset[asset]["bars"][date_text]["close"]) for asset in assets]
        for date_text in market_dates
    ]
    market_quote_volumes = [
        [
            float(history_by_asset[asset]["bars"][date_text]["quoteVolume"])
            for asset in assets
        ]
        for date_text in market_dates
    ]
    # The terminal row is not a market observation. It is a zero-return execution
    # row that turns the latest closed-bar information into the next session's
    # target while preserving every lag used by the research implementation.
    closes = [*market_closes, list(market_closes[-1])]
    quote_volumes = [*market_quote_volumes, list(market_quote_volumes[-1])]
    returns = [_zero_row(len(assets))]
    for index in range(1, len(dates)):
        returns.append(
            [
                closes[index][asset] / closes[index - 1][asset] - 1.0
                for asset in range(len(assets))
            ]
        )

    raw_eligibility: list[list[bool]] = []
    for index in range(len(dates)):
        row: list[bool] = []
        for asset in range(len(assets)):
            volume_sample = [
                quote_volumes[item][asset]
                for item in range(max(0, index - 29), index + 1)
            ]
            liquid = (
                len(volume_sample) >= 20
                and statistics.median(volume_sample)
                >= MINIMUM_MEDIAN_QUOTE_VOLUME_USD
            )
            row.append(index + 1 >= MINIMUM_HISTORY and liquid)
        raw_eligibility.append(row)
    eligible = [
        list(raw_eligibility[index - 1])
        if index > 0
        else [False] * len(assets)
        for index in range(len(dates))
    ]

    raw_inverse_volatility: list[list[float]] = []
    returns_by_asset = [
        [row[asset] for row in returns] for asset in range(len(assets))
    ]
    for index in range(len(dates)):
        row: list[float] = []
        for asset in range(len(assets)):
            sample = _finite(
                returns_by_asset[asset][
                    max(1, index - ASSET_VOLATILITY_LOOKBACK + 1) : index + 1
                ]
            )
            deviation = (
                _population_standard_deviation(sample) if len(sample) >= 40 else None
            )
            volatility = deviation * math.sqrt(SLEEVE_ANNUAL_DAYS) if deviation else None
            row.append(1.0 / volatility if volatility and volatility > 0 else 0.0)
        raw_inverse_volatility.append(row)
    weekly_raw = _zero_row(len(assets))
    weekly_rows: list[list[float]] = []
    for date_text, row in zip(dates, raw_inverse_volatility, strict=True):
        if date.fromisoformat(date_text).weekday() == 0:
            weekly_raw = list(row)
        weekly_rows.append(list(weekly_raw))
    inverse_volatility = [
        list(weekly_rows[index - 1]) if index > 0 else _zero_row(len(assets))
        for index in range(len(dates))
    ]

    close_by_asset = [[row[asset] for row in closes] for asset in range(len(assets))]
    don_fast_by_asset = [
        _donchian_score(values, [(10, 5), (30, 10), (90, 30)])
        for values in close_by_asset
    ]
    don_base_by_asset = [
        _donchian_score(values, [(20, 10), (55, 20), (120, 55)])
        for values in close_by_asset
    ]
    mom_base_by_asset = [
        _momentum_score(values, [21, 63, 126, 252]) for values in close_by_asset
    ]
    mom_slow_by_asset = [
        _momentum_score(values, [63, 126, 252, 365]) for values in close_by_asset
    ]
    ema_base_by_asset = [
        _ema_score(values, [20, 50, 100, 200]) for values in close_by_asset
    ]
    ema200_by_asset = [_ema(values, 200) for values in close_by_asset]

    try:
        btc_index = assets.index("BTC")
    except ValueError as error:
        raise ValueError("BTC-USDT-SWAP is required") from error
    bear: list[bool] = []
    for index in range(len(dates)):
        bear.append(
            index >= 120
            and closes[index][btc_index] / closes[index - 120][btc_index] - 1.0 < 0.0
        )

    long_only_long: list[list[bool]] = []
    light_long: list[list[bool]] = []
    light_short: list[list[bool]] = []
    slow_long: list[list[bool]] = []
    slow_short: list[list[bool]] = []
    for index in range(len(dates)):
        long_only_row: list[bool] = []
        light_long_row: list[bool] = []
        light_short_row: list[bool] = []
        slow_long_row: list[bool] = []
        slow_short_row: list[bool] = []
        for asset in range(len(assets)):
            don_fast = don_fast_by_asset[asset][index]
            don_base = don_base_by_asset[asset][index]
            mom_base = mom_base_by_asset[asset][index]
            mom_slow = mom_slow_by_asset[asset][index]
            ema_base = ema_base_by_asset[asset][index]
            ema200 = ema200_by_asset[asset][index]
            hybrid = (
                (float(don_base) + float(mom_base)) / 2.0
                if don_base is not None and mom_base is not None
                else None
            )
            long_only_row.append(don_fast is not None and don_fast > 0.5)
            light_long_row.append(hybrid is not None and hybrid > 0.0)
            light_short_row.append(
                bear[index] and ema_base is not None and ema_base < -0.5
            )
            slow_long_row.append(hybrid is not None and hybrid > 0.0)
            slow_short_row.append(
                bear[index]
                and mom_slow is not None
                and mom_slow < 0.0
                and ema200 is not None
                and closes[index][asset] < ema200
            )
        long_only_long.append(long_only_row)
        light_long.append(light_long_row)
        light_short.append(light_short_row)
        slow_long.append(slow_long_row)
        slow_short.append(slow_short_row)

    empty_short = [[False] * len(assets) for _ in dates]
    never_bear = [False] * len(dates)
    long_only = _run_sleeve(
        dates=dates,
        returns=returns,
        eligible=eligible,
        inverse_volatility=inverse_volatility,
        long_entries=long_only_long,
        short_entries=empty_short,
        bear=never_bear,
        bear_long_budget=1.0,
        bear_short_budget=0.0,
    )
    light = _run_sleeve(
        dates=dates,
        returns=returns,
        eligible=eligible,
        inverse_volatility=inverse_volatility,
        long_entries=light_long,
        short_entries=light_short,
        bear=bear,
        bear_long_budget=0.25,
        bear_short_budget=0.10,
    )
    slow = _run_sleeve(
        dates=dates,
        returns=returns,
        eligible=eligible,
        inverse_volatility=inverse_volatility,
        long_entries=slow_long,
        short_entries=slow_short,
        bear=bear,
        bear_long_budget=0.0,
        bear_short_budget=0.50,
    )
    risk_parity = _run_sleeve(
        dates=dates,
        returns=returns,
        eligible=eligible,
        inverse_volatility=inverse_volatility,
        long_entries=[[True] * len(assets) for _ in dates],
        short_entries=empty_short,
        bear=never_bear,
        bear_long_budget=1.0,
        bear_short_budget=0.0,
    )

    benchmark_equity: list[float] = []
    equity = 1.0
    for value in risk_parity["returns"]:
        equity *= 1.0 + value
        benchmark_equity.append(equity)
    mom180: list[float | None] = []
    mom40: list[float | None] = []
    for index in range(len(dates)):
        mom180.append(
            benchmark_equity[index - 1] / benchmark_equity[index - 181] - 1.0
            if index >= 181
            else None
        )
        mom40.append(
            benchmark_equity[index - 1] / benchmark_equity[index - 41] - 1.0
            if index >= 41
            else None
        )
    re180_bear = _hysteresis(mom180, 0.0, 0.06)
    early40_bear = _hysteresis(mom40, -0.15, 0.0)
    combined_bear = [
        max(left, right)
        for left, right in zip(re180_bear, early40_bear, strict=True)
    ]
    sleeve_allocations: list[list[float]] = []
    base_returns: list[float] = []
    previous_allocation = [0.25, 0.75, 0.0]
    for index, state in enumerate(combined_bear):
        slow_weight = 0.60 * state
        allocation = [
            0.25 * (1.0 - slow_weight),
            0.75 * (1.0 - slow_weight),
            slow_weight,
        ]
        sleeve_allocations.append(allocation)
        meta_turnover = sum(
            abs(value - previous_allocation[item])
            for item, value in enumerate(allocation)
        )
        base_returns.append(
            allocation[0] * long_only["returns"][index]
            + allocation[1] * light["returns"][index]
            + allocation[2] * slow["returns"][index]
            - META_EXECUTION_COST * meta_turnover
        )
        previous_allocation = allocation

    lagged_realized_volatility: list[float | None] = []
    raw_scale: list[float] = []
    for index in range(len(dates)):
        volatility = _annualized_volatility(
            base_returns,
            index,
            RISK_SCALE_LOOKBACK,
            max(10, RISK_SCALE_LOOKBACK // 2),
        )
        lagged_realized_volatility.append(volatility)
        if volatility is None:
            unbounded_scale = 1.0
        elif volatility <= 0.0:
            unbounded_scale = RISK_SCALE_CAP
        else:
            unbounded_scale = TARGET_VOLATILITY / volatility
        raw_scale.append(
            _clamp(unbounded_scale, RISK_SCALE_FLOOR, RISK_SCALE_CAP)
        )
    smooth_scale = _ewm_adjust_false(raw_scale, RISK_SCALE_EWM_SPAN)
    risk_scale = _weekly_hold(smooth_scale, dates, initial=1.0)

    final_target: list[list[float]] = []
    gross_cap_applied: list[bool] = []
    for index in range(len(dates)):
        combined = [
            risk_scale[index]
            * (
                sleeve_allocations[index][0] * long_only["weights"][index][asset]
                + sleeve_allocations[index][1] * light["weights"][index][asset]
                + sleeve_allocations[index][2] * slow["weights"][index][asset]
            )
            for asset in range(len(assets))
        ]
        safe_target, applied = _apply_target_safety(combined)
        final_target.append(safe_target)
        gross_cap_applied.append(applied)

    latest_market_index = len(market_dates) - 1
    execution_index = len(dates) - 1
    return {
        "assets": assets,
        "dates": dates,
        "marketDates": market_dates,
        "executionDate": execution_date,
        "latestMarketIndex": latest_market_index,
        "executionIndex": execution_index,
        "closes": closes,
        "returns": returns,
        "eligible": eligible,
        "target": final_target,
        "riskScale": risk_scale,
        "rawRiskScale": raw_scale,
        "laggedRealizedVolatility": lagged_realized_volatility,
        "mom180": mom180,
        "mom40": mom40,
        "re180Bear": re180_bear,
        "early40Bear": early40_bear,
        "combinedBear": combined_bear,
        "sleeveAllocations": sleeve_allocations,
        "grossCapApplied": gross_cap_applied,
        "benchmarkReturns": risk_parity["returns"],
        "baseReturns": base_returns,
        "sleeves": {
            "Long-only": long_only,
            "Light short hedge": light,
            "Slow-bear specialist": slow,
        },
        "failedAssets": failed_assets,
        "inactiveAssets": inactive_assets,
    }
