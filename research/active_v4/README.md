# Active V4 — walk-forward family ensemble

A research-only BTC/ETH/cash process designed after V3 failed its 2025–2026 research holdout.

## Candidate construction

Four independent, predeclared families are averaged internally:

1. multi-horizon trend breadth;
2. dual absolute/relative momentum;
3. Donchian breakout;
4. moving-average stack.

The outer process compares static family combinations with quarterly or semiannual walk-forward family selection. The walk-forward ranking uses only a trailing two- or three-year window. No individual rule parameter is selected.

## Execution and costs

- Binance Spot daily archives;
- long/flat BTCUSDT and ETHUSDT, no leverage;
- close signal, next-day open execution;
- overnight and open-to-close returns both included;
- 5, 10, 20 and 30 bps per side scenarios;
- volatility sizing, cash residual and 35% hard research stop.

## Periods

- data warm-up: 2018–2019;
- development: 2020–2022;
- validation: 2023–2024;
- research holdout: 2025 through June 2026.

The holdout was not used by V4 ranking, but it has been seen in previous research iterations and is not a pristine out-of-sample set.

## Run

```bash
python -m pip install -r requirements.txt
python run_research.py --self-test
python run_research.py --output artifacts/active_v4 --cache .cache/binance_vision_1d
```

A positive historical result is only a candidate for a frozen paper-forward process, not permission for live trading.
