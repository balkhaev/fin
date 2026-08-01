"""Shared constants and pure helpers for DS-40/180 T50-C3 paper trading."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Iterable

STRATEGY_ID = "ds40180_t50c3_okx_paper"
STRATEGY_VERSION = "okx-paper-v2"
PROFILE_NAME = "DS-40/180 T50-C3 v2"
SNAPSHOT_DATE = "2026-07-31"
OKX_API_BASE = "https://www.okx.com"
OKX_BAR = "1Dutc"
OKX_INTRADAY_BAR = "4H"
HISTORY_LIMIT = 760
INTRADAY_HISTORY_LIMIT = 160
MAX_CANDLE_PAGES = 12
MAX_FUNDING_PAGES = 8
MINIMUM_COMMON_DAYS = 560
MINIMUM_ASSETS = 8
MINIMUM_HISTORY = 260
MINIMUM_MEDIAN_QUOTE_VOLUME_USD = 5_000_000.0
ASSET_VOLATILITY_LOOKBACK = 60
SLEEVE_TARGET_VOLATILITY = 0.30
SLEEVE_ASSET_CAP = 0.10
SLEEVE_EXECUTION_COST = 0.001
SLEEVE_ADVERSE_SHORT_CARRY_ANNUAL = 0.05
META_EXECUTION_COST = 0.0005
TARGET_VOLATILITY = 0.50
RISK_SCALE_FLOOR = 1.00
RISK_SCALE_CAP = 3.00
RISK_SCALE_LOOKBACK = 30
RISK_SCALE_EWM_SPAN = 10

# Absolute paper-only limits. The active cap is selected dynamically and never
# exceeds PAPER_GROSS_CAP.
PAPER_GROSS_CAP = 1.50
PAPER_GROSS_CAP_STRESS = 0.75
PAPER_GROSS_CAP_BASE = 1.25
PAPER_GROSS_CAP_CALM = 1.50
PAPER_ASSET_CAP = 0.25
PAPER_EXECUTION_COST = 0.001
PAPER_IMPACT_BPS = 2.0
MISSING_FUNDING_FALLBACK_ANNUAL = 0.05
MATERIAL_DELTA = 0.0025
NO_TRADE_MIN_BAND = 0.005
NO_TRADE_COST_MULTIPLIER = 4.0

FUNDING_SOFT_ANNUAL = 0.05
FUNDING_MEDIUM_ANNUAL = 0.12
FUNDING_HARD_ANNUAL = 0.20
FUNDING_MULTIPLIER_FLOOR = 0.25

COVARIANCE_LOOKBACK = 60
COVARIANCE_SHRINK = 0.50
STRESS_CORRELATION = 0.80
STRESS_VOL_THRESHOLD = 0.65
CALM_VOL_THRESHOLD = 0.30
STRESS_CORRELATION_THRESHOLD = 0.75
CALM_CORRELATION_THRESHOLD = 0.45

CRISIS_GROSS_CAP = 0.15
CRISIS_BREADTH_THRESHOLD = 0.70
CRISIS_BREAKOUT_BARS = 20
CRISIS_FAST_EMA = 50
CRISIS_SLOW_EMA = 100
CRISIS_VOLATILITY_BARS = 30

SLEEVE_ANNUAL_DAYS = 365.0
ANNUAL_DAYS = 365.25
EPSILON = 1e-10
ASSETS = (
    "ADA",
    "BCH",
    "BNB",
    "BTC",
    "DOGE",
    "DOT",
    "ETH",
    "LINK",
    "LTC",
    "TRX",
    "UNI",
    "XLM",
    "XRP",
)
INSTRUMENTS = {asset: f"{asset}-USDT-SWAP" for asset in ASSETS}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _zero_row(length: int) -> list[float]:
    return [0.0] * length


def _gross(row: Iterable[float]) -> float:
    return sum(abs(value) for value in row)


def _population_standard_deviation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _finite(values: Iterable[float | None]) -> list[float]:
    return [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]


def _annualized_volatility(
    values: list[float],
    end_exclusive: int,
    window: int,
    minimum: int,
    annual_days: float = ANNUAL_DAYS,
) -> float | None:
    sample = _finite(values[max(0, end_exclusive - window) : end_exclusive])
    deviation = (
        _population_standard_deviation(sample) if len(sample) >= minimum else None
    )
    return deviation * math.sqrt(annual_days) if deviation is not None else None


def _ema(values: list[float], span: int) -> list[float | None]:
    alpha = 2.0 / (span + 1.0)
    previous: float | None = None
    observations = 0
    output: list[float | None] = []
    for value in values:
        observations += 1
        previous = value if previous is None else alpha * value + (1.0 - alpha) * previous
        output.append(previous if observations >= span else None)
    return output


def _sign(value: float) -> float:
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


def _ewm_adjust_false(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    output = [float(values[0])]
    for value in values[1:]:
        output.append(alpha * value + (1.0 - alpha) * output[-1])
    return output


def _hysteresis(
    signal: list[float | None], enter_below: float, exit_above: float
) -> list[int]:
    current = 0
    output: list[int] = []
    for value in signal:
        if value is not None and math.isfinite(value):
            if current == 0 and value < enter_below:
                current = 1
            elif current == 1 and value > exit_above:
                current = 0
        output.append(current)
    return output


def _weekly_hold(values: list[float], dates: list[str], initial: float = 1.0) -> list[float]:
    held = initial
    output: list[float] = []
    for date_text, value in zip(dates, values, strict=True):
        if date.fromisoformat(date_text).weekday() == 0:
            held = value
        output.append(held)
    return output


def _timestamp_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.astimezone(UTC).timestamp() * 1000)


def _candle_close_ms(date_text: str) -> int:
    opened = datetime.combine(date.fromisoformat(date_text), datetime.min.time(), UTC)
    return int((opened + timedelta(days=1)).timestamp() * 1000)
