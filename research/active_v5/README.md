# Active V5: robust spot/perpetual trend search

V5 follows the rejection of AIMR V1, high-turnover V2, and long-only V3. It does not add leverage to compensate for weak expectancy. Instead it searches four predeclared, low-complexity trend families and accounts for execution costs and perpetual funding.

## Families

1. **Spot absolute momentum** — BTC/ETH/cash, independent multi-horizon trend signals, EMA regime filter, covariance-aware volatility sizing.
2. **Spot Donchian breakout** — independent long/flat breakouts with slower exit channels.
3. **Perpetual time-series momentum** — signed BTC/ETH USD-M perpetual exposure, 1.0x maximum gross, multi-horizon consensus, actual archived funding rates.
4. **Perpetual Donchian breakout** — long/short channel trend following with 1.0x maximum gross.

The script also evaluates predeclared account-level blends of the family ensembles.

## Execution assumptions

- Signals use only completed bars.
- Orders are applied at the following bar open.
- Spot uses daily candles; perpetuals use 8-hour candles aligned with ordinary funding timestamps.
- Existing positions receive the close-to-next-open price gap before rebalancing.
- Perpetual funding is charged to the position held at the funding timestamp before the next rebalance.
- Costs are charged on both entry and exit turnover.
- Gross exposure is capped at 1.0x. No martingale and no averaging down are used.

## Research chronology

- development: 2020-01-01 to 2022-01-01;
- validation A: 2022-01-01 to 2024-01-01;
- validation B: 2024-01-01 to 2025-01-01;
- bridge validation: 2025-01-01 to 2026-01-01;
- final evaluation: 2026-01-01 to 2026-07-01.

Family parameters are selected only on development and validation A/B. Candidate selection may use the 2025 bridge. The 2026 H1 result is evaluated only after the candidate name and components are written to `selection_proof_before_final.json`.

The final period is not perfectly untouched in the broad project history, because earlier research viewed aggregate 2025–2026 behavior. It is nevertheless excluded mechanically from V5 ranking.

## Costs

- low: 5 bps per side;
- base: 10 bps per side;
- stress: 20 bps per side;
- actual Binance USD-M funding is added separately.

## Run

```bash
python -m pip install -r research/active_v5/requirements.txt
python research/active_v5/run_research.py --self-test
python research/active_v5/run_research.py \
  --output artifacts/active_v5 \
  --cache .cache/binance_active_v5
```

## Promotion rule

A result can receive `promising_paper_candidate` only when the fixed candidate is positive in the 2025 bridge and 2026 H1 under stress costs, full-history stress drawdown is below 25%, annual turnover is below 30x, at least four stress calendar years are positive, and no leverage above 1.0x is used.

That status permits only an immutable paper-forward process. It does not approve live trading.
