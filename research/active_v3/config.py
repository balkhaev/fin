from __future__ import annotations

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class ResearchConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    source_interval: str = "15m"
    start: str = "2020-01-01"
    end_exclusive: str = "2026-07-01"
    development_end: str = "2023-01-01"
    validation_end: str = "2025-01-01"
    starting_equity: float = 10_000.0
    hard_drawdown_stop: float = 0.30


@dataclass(frozen=True)
class Costs:
    name: str
    bps_per_side: float

    @property
    def rate(self) -> float:
        return self.bps_per_side / 10_000.0


@dataclass(frozen=True)
class TrendParams:
    fast_days: int
    slow_days: int
    ema_days: int
    vol_days: int
    target_vol: float
    rebalance_days: int
    min_hold_days: int
    switch_threshold: float
    weight_band: float
    min_slow_momentum: float

    @property
    def key(self) -> str:
        return (
            f"dtrend_f{self.fast_days}_s{self.slow_days}_e{self.ema_days}_"
            f"v{self.vol_days}_tv{int(self.target_vol*100)}_r{self.rebalance_days}_"
            f"mh{self.min_hold_days}_sw{self.switch_threshold:.2f}_"
            f"wb{self.weight_band:.2f}_m{self.min_slow_momentum:.2f}"
        )


COSTS = (
    Costs("low", 5.0),
    Costs("base", 10.0),
    Costs("stress", 20.0),
)


def trend_grid() -> list[TrendParams]:
    rows = product(
        (21, 42),
        (90, 180),
        (100, 200),
        (30, 60),
        (0.15, 0.20, 0.25),
        (7, 14),
        (14, 28),
        (0.25, 0.50),
        (0.10, 0.20),
        (0.00, 0.05),
    )
    return [TrendParams(*row) for row in rows]
