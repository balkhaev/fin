# Active V9 — fixed-universe USD-M cross-sectional long/short

Research-only market-neutral candidate using a fixed January-2021 universe, actual Binance funding, next-open execution, 40/80 bps selection costs and a final 2026 H1 opened only after process selection.

## Universe

BTC, ETH, BNB, XRP, ADA, LTC, BCH, EOS, DOGE, LINK, DOT, TRX and SOL USD-M perpetuals. The universe is fixed in advance and includes weak/delisted names to reduce survivorship bias.

## Candidate families

- cross-sectional momentum;
- residual momentum after removing BTC/ETH market beta;
- price-range anchor momentum;
- funding-adjusted momentum.

Each family averages neighbouring rules. Static family subsets and walk-forward family selection are evaluated without selecting a single indicator parameter.

## Execution

- completed daily close signal;
- execution at the next UTC open;
- previous position receives the overnight move before rebalance;
- actual archived funding is included;
- gross target is capped at 0.85x;
- forced delisting exits receive a 100 bps penalty;
- 40 and 80 bps per-side costs are used for selection.

## Periods

- development: 2021–2022;
- validation A: 2023;
- validation B: 2024;
- bridge: 2025;
- final: January–June 2026, opened only after process selection.

A positive historical result permits only a frozen paper-forward experiment, not live deployment.