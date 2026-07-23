from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class ResearchConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    source_interval: str = "1d"
    data_start: str = "2018-01-01"
    evaluation_start: str = "2020-01-01"
    development_end: str = "2023-01-01"
    validation_end: str = "2025-01-01"
    end_exclusive: str = "2026-07-01"
    starting_equity: float = 10_000.0
    hard_drawdown_stop: float = 0.35
    minimum_training_days: int = 540


@dataclass(frozen=True)
class Costs:
    name: str
    bps_per_side: float

    @property
    def rate(self) -> float:
        return self.bps_per_side / 10_000.0


@dataclass(frozen=True)
class ProcessSpec:
    kind: str
    train_days: int = 0
    selection_days: int = 0
    top_k: int = 0
    score_mode: str = "equal"
    overlay: str = "none"
    subset: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        subset = "-".join(self.subset) if self.subset else "all"
        return (
            f"{self.kind}_tr{self.train_days}_sel{self.selection_days}_"
            f"k{self.top_k}_{self.score_mode}_{self.overlay}_{subset}"
        )


COSTS = (
    Costs("low", 5.0),
    Costs("base", 10.0),
    Costs("stress", 20.0),
    Costs("severe", 30.0),
)

FAMILY_NAMES = ("breadth", "dual", "donchian", "ma_stack")


def process_grid() -> list[ProcessSpec]:
    rows: list[ProcessSpec] = []
    for size in (2, 3, 4):
        for subset in combinations(FAMILY_NAMES, size):
            for overlay in ("none", "breadth", "breadth_vol"):
                rows.append(ProcessSpec(kind="static", top_k=size, overlay=overlay, subset=subset))
    for train_days in (730, 1_095):
        for selection_days in (91, 182):
            for top_k in (1, 2, 3, 4):
                for score_mode in ("robust", "worst_year"):
                    for overlay in ("none", "breadth", "breadth_vol"):
                        rows.append(ProcessSpec(
                            kind="walkforward",
                            train_days=train_days,
                            selection_days=selection_days,
                            top_k=top_k,
                            score_mode=score_mode,
                            overlay=overlay,
                        ))
    return rows
