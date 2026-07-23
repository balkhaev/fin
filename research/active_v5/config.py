from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterator


@dataclass(frozen=True)
class ResearchConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    start: str = "2020-01-01"
    end_exclusive: str = "2026-07-01"
    development_end: str = "2022-01-01"
    validation_a_end: str = "2024-01-01"
    validation_b_end: str = "2025-01-01"
    bridge_end: str = "2026-01-01"
    spot_interval: str = "1d"
    perp_interval: str = "8h"
    starting_equity: float = 10_000.0
    max_gross_exposure: float = 1.0

    @property
    def selection_periods(self) -> dict[str, tuple[str, str]]:
        return {
            "development": (self.start, self.development_end),
            "validation_a": (self.development_end, self.validation_a_end),
            "validation_b": (self.validation_a_end, self.validation_b_end),
        }

    @property
    def evaluation_periods(self) -> dict[str, tuple[str, str]]:
        return {
            **self.selection_periods,
            "bridge_2025": (self.validation_b_end, self.bridge_end),
            "final_2026h1": (self.bridge_end, self.end_exclusive),
        }


@dataclass(frozen=True)
class Costs:
    name: str
    bps_per_side: float

    @property
    def rate(self) -> float:
        return self.bps_per_side / 10_000.0


COSTS: tuple[Costs, ...] = (
    Costs("low", 5.0),
    Costs("base", 10.0),
    Costs("stress", 20.0),
)


@dataclass(frozen=True)
class SpotMomentumParams:
    lookbacks: tuple[int, int, int]
    required_positive: int
    ema_days: int
    vol_days: int
    target_vol: float
    rebalance_days: int
    weight_band: float

    @property
    def family(self) -> str:
        return "spot_momentum"

    @property
    def key(self) -> str:
        a, b, c = self.lookbacks
        return (
            f"spotmom_l{a}-{b}-{c}_p{self.required_positive}_e{self.ema_days}_"
            f"v{self.vol_days}_tv{int(self.target_vol * 100)}_"
            f"r{self.rebalance_days}_wb{int(self.weight_band * 100)}"
        )


@dataclass(frozen=True)
class SpotBreakoutParams:
    entry_days: int
    exit_days: int
    vol_days: int
    target_vol: float
    rebalance_days: int
    weight_band: float

    @property
    def family(self) -> str:
        return "spot_breakout"

    @property
    def key(self) -> str:
        return (
            f"spotbo_en{self.entry_days}_ex{self.exit_days}_v{self.vol_days}_"
            f"tv{int(self.target_vol * 100)}_r{self.rebalance_days}_"
            f"wb{int(self.weight_band * 100)}"
        )


@dataclass(frozen=True)
class PerpMomentumParams:
    lookback_days: tuple[int, int, int]
    consensus: int
    vol_days: int
    target_vol: float
    rebalance_bars: int
    weight_band: float
    signal_smoothing_bars: int

    @property
    def family(self) -> str:
        return "perp_tsmom"

    @property
    def key(self) -> str:
        a, b, c = self.lookback_days
        return (
            f"perpmom_l{a}-{b}-{c}_c{self.consensus}_v{self.vol_days}_"
            f"tv{int(self.target_vol * 100)}_r{self.rebalance_bars}_"
            f"wb{int(self.weight_band * 100)}_sm{self.signal_smoothing_bars}"
        )


@dataclass(frozen=True)
class PerpBreakoutParams:
    entry_days: int
    exit_days: int
    vol_days: int
    target_vol: float
    rebalance_bars: int
    weight_band: float

    @property
    def family(self) -> str:
        return "perp_breakout"

    @property
    def key(self) -> str:
        return (
            f"perpbo_en{self.entry_days}_ex{self.exit_days}_v{self.vol_days}_"
            f"tv{int(self.target_vol * 100)}_r{self.rebalance_bars}_"
            f"wb{int(self.weight_band * 100)}"
        )


Params = SpotMomentumParams | SpotBreakoutParams | PerpMomentumParams | PerpBreakoutParams


def spot_momentum_grid() -> Iterator[SpotMomentumParams]:
    yield from (
        SpotMomentumParams(*values)
        for values in product(
            ((21, 63, 126), (42, 126, 252), (63, 126, 252)),
            (2, 3),
            (100, 200),
            (30, 60, 90),
            (0.20, 0.30),
            (7, 14),
            (0.10, 0.20),
        )
    )


def spot_breakout_grid() -> Iterator[SpotBreakoutParams]:
    yield from (
        SpotBreakoutParams(*values)
        for values in product(
            ((55, 20), (90, 45), (180, 90), (252, 126)),
            (30, 60),
            (0.20, 0.30),
            (1, 7),
            (0.10, 0.20),
        )
        for values in [(*values[0], *values[1:])]
    )


def perp_momentum_grid() -> Iterator[PerpMomentumParams]:
    yield from (
        PerpMomentumParams(*values)
        for values in product(
            ((7, 21, 63), (14, 42, 126), (21, 63, 126), (42, 126, 252)),
            (2, 3),
            (30, 60),
            (0.20, 0.30),
            (3, 9, 21),
            (0.10, 0.20),
            (1, 3),
        )
    )


def perp_breakout_grid() -> Iterator[PerpBreakoutParams]:
    yield from (
        PerpBreakoutParams(*values)
        for values in product(
            ((20, 10), (55, 20), (90, 45), (180, 90)),
            (30, 60),
            (0.20, 0.30),
            (3, 9),
            (0.10, 0.20),
        )
        for values in [(*values[0], *values[1:])]
    )


def all_parameter_grids() -> dict[str, list[Params]]:
    return {
        "spot_momentum": list(spot_momentum_grid()),
        "spot_breakout": list(spot_breakout_grid()),
        "perp_tsmom": list(perp_momentum_grid()),
        "perp_breakout": list(perp_breakout_grid()),
    }
