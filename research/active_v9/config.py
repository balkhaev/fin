from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations

@dataclass(frozen=True)
class Config:
    symbols: tuple[str,...] = (
        'BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','LTCUSDT',
        'BCHUSDT','EOSUSDT','DOGEUSDT','LINKUSDT','DOTUSDT','TRXUSDT','SOLUSDT',
    )
    start: str = '2020-01-01'
    end_exclusive: str = '2026-07-01'
    interval: str = '1d'
    starting_equity: float = 10_000.0
    max_gross: float = 0.85
    forced_exit_penalty_bps: float = 100.0

PERIODS = {
    'development': ('2021-01-01','2023-01-01'),
    'validation_a': ('2023-01-01','2024-01-01'),
    'validation_b': ('2024-01-01','2025-01-01'),
    'bridge_2025': ('2025-01-01','2026-01-01'),
    'final_2026h1': ('2026-01-01','2026-07-01'),
    'full': ('2021-01-01','2026-07-01'),
}
COSTS = {'low':0.002,'stress':0.004,'severe':0.008}
FAMILIES = ('xs_momentum','residual_momentum','anchor','funding_momentum')

@dataclass(frozen=True)
class Process:
    kind: str
    subset: tuple[str,...]=()
    train_days: int=0
    selection_days: int=0
    top_k: int=0
    score_mode: str='robust'
    @property
    def key(self)->str:
        sub='-'.join(self.subset) if self.subset else 'all'
        return f'{self.kind}_tr{self.train_days}_sel{self.selection_days}_k{self.top_k}_{self.score_mode}_{sub}'

def process_grid()->list[Process]:
    out=[]
    for size in (2,3,4):
        for sub in combinations(FAMILIES,size): out.append(Process('static',subset=sub,top_k=size))
    for tr in (730,1095):
        for sel in (91,182):
            for k in (1,2,3,4):
                for mode in ('robust','worst_year'):out.append(Process('walkforward',train_days=tr,selection_days=sel,top_k=k,score_mode=mode))
    return out
