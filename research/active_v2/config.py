from __future__ import annotations

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class ResearchConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    interval: str = "15m"
    start: str = "2020-01-01"
    end_exclusive: str = "2026-07-01"
    starting_equity: float = 10_000.0
    development_end: str = "2023-01-01"
    validation_end: str = "2025-01-01"
    risk_per_trade: float = 0.005
    hard_drawdown_stop: float = 0.30


@dataclass(frozen=True)
class Costs:
    name: str
    bps_per_side: float

    @property
    def rate(self) -> float:
        return self.bps_per_side / 10_000.0


@dataclass(frozen=True)
class RotationParams:
    fast_days: int
    slow_days: int
    ema_days: int
    vol_days: int
    target_vol: float
    rebalance_hours: int
    hysteresis: float

    @property
    def key(self) -> str:
        return (
            f"rot_f{self.fast_days}_s{self.slow_days}_e{self.ema_days}_"
            f"v{self.vol_days}_tv{int(self.target_vol * 100)}_"
            f"r{self.rebalance_hours}_h{self.hysteresis:.2f}"
        )


@dataclass(frozen=True)
class ShockParams:
    shock_bars: int
    z_threshold: float
    trend_ema_days: int
    bar_location: float
    taker_ratio: float
    volume_ratio: float
    stop_atr: float
    target_r: float
    max_hold_bars: int

    @property
    def key(self) -> str:
        return (
            f"shock_b{self.shock_bars}_z{abs(self.z_threshold):.1f}_"
            f"e{self.trend_ema_days}_loc{self.bar_location:.2f}_"
            f"tk{self.taker_ratio:.2f}_vol{self.volume_ratio:.2f}_"
            f"sl{self.stop_atr:.1f}_tp{self.target_r:.1f}_h{self.max_hold_bars}"
        )


COSTS = (
    Costs("low", 5.0),
    Costs("base", 10.0),
    Costs("stress", 20.0),
)


def rotation_grid() -> list[RotationParams]:
    values = product(
        (3, 7, 14),
        (21, 42, 63),
        (10, 20, 40),
        (14, 30),
        (0.20, 0.30, 0.40),
        (4, 12, 24),
        (0.00, 0.20),
    )
    return [RotationParams(*row) for row in values if row[0] < row[1]]


def shock_grid() -> list[ShockParams]:
    values = product(
        (2, 4),
        (-2.5, -3.0),
        (20, 40),
        (0.55, 0.70),
        (0.50, 0.55),
        (1.25, 1.60),
        (1.0, 1.4),
        (1.0, 1.5),
        (8, 16, 32),
    )
    return [ShockParams(*row) for row in values]
