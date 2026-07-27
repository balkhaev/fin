# Active V293–V300 — fixed-universe crypto residual reversal

This cycle tests a new cross-sectional overreaction/reversal family in the fixed January-2021 Binance USD-M universe. It is distinct from V2 long-only shock reversal and V41 exact-8h liquidity exhaustion: positions are market-neutral cross-sectional baskets selected from completed daily residual returns.

## Frozen universe

BTC, ETH, BNB, XRP, ADA, LTC, BCH, EOS, DOGE, LINK, DOT, TRX and SOL. Delisted and weak assets remain in the panel. Survivor replacement is forbidden.

## Frozen families

1. close-to-close residual reversal;
2. overnight-gap residual reversal;
3. intraday residual reversal;
4. residual continuation control.

The first three families are promotable. Continuation is a predeclared non-promotable control.

## Grid

- lookback: 3 / 5 / 10 completed days;
- long and short baskets: 2 or 3 assets each;
- scheduled rebalance: 1 / 3 / 7 days;
- dollar-neutral or beta-neutral;
- 144 policies total, 108 promotable.

## Chronology and execution

- warmup: 2020;
- development: 2021–2023;
- validation: 2024;
- holdout: 2025;
- final: 2026 H1;
- completed daily close → next UTC open;
- actual archived funding;
- target gross 0.40x;
- maximum realized close gross gate 0.70x;
- costs 30 / 60 / 100 bps per side;
- +1 completed-day latency;
- forced-delisting penalty 100 bps.

2024–2026 remain unopened unless a policy passes the frozen development gates.

## Safety

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
