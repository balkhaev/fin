# Active V189–V196 — defined-risk options variance carry

Этот цикл ищет независимый источник P&L в BTC/ETH options и **не использует naked short volatility**. Кандидат допускается только как полностью ограниченный по максимальному убытку spread/iron-condor sleeve с явным bid/ask, settlement и отдельным margin ledger.

V75/V28/V136 не изменяются. Интеграция запрещена до самостоятельного прохождения всех gates.

## Fixed universe

```text
venue: Binance Options
underlyings: BTCUSDT, ETHUSDT
source: daily EOHSummary archives
```

## Версии

- **V189** — checksum-aware EOH schema/coverage/executability probe;
- **V190** — normalized option-chain panel, contract parser and provenance;
- **V191** — causal implied-versus-realized variance signal;
- **V192** — defined-risk put/call spreads and iron-condor ledger;
- **V193** — immutable development selection;
- **V194** — bid/ask, settlement, jump, latency and margin audits;
- **V195** — validation/holdout/final and guarded V75 integration;
- **V196** — checkpoint and frozen paper-forward protocol.

## Frozen chronology

```text
development: 2023-07-01 through 2024-06-30
validation:  2024-07-01 through 2024-12-31
holdout:     2025-01-01 through 2025-12-31
final:       2026-01-01 through 2026-06-30
```

На уровне общей программы эти окна не pristine; это фиксируется в selection proof.

## Non-negotiable data gate

Исторический P&L не рассчитывается, если EOH archive не даёт или не позволяет детерминированно восстановить:

- contract symbol, expiry, strike и call/put;
- executable bid и ask;
- underlying/reference price;
- open interest или объём для liquidity gate;
- settlement/expiry intrinsic value;
- timestamps и contract multiplier.

Mark price без bid/ask недостаточна. Отсутствующая котировка не заменяется theoretical mid после просмотра.

## Frozen candidate family

```text
DTE buckets:             7–14, 14–30
short absolute delta:    0.15, 0.25
long-wing delta:         0.05, 0.10
minimum IV-RV spread:    10, 20 volatility points
holding:                 1 day, 3 days, or exit at 2 DTE
structures:              put spread, call spread, iron condor
```

Все ноги открываются одновременно по консервативной executable combination: short leg at bid, long leg at ask. Если одна нога недоступна, сделки нет.

## Risk and costs

```text
maximum loss per structure: 2% of equity
maximum concurrent expiries: 2
minimum margin/cash reserve: 50%
no naked short option legs
no portfolio-margin credit between expiries
base fees/slippage: actual bid/ask + published fee proxy
severe audit: 1.5x spread and fee cost
latency audit: next EOH snapshot
jump audit: intrinsic settlement with no discretionary roll
```

## Standalone gates

```text
development CAGR                 >= 8%
development Sharpe               >= 1.00
development Max DD               >= -15%
closed structures               >= 60
BTC and ETH standalone return    > 0
validation return                > 0
holdout return                   > 0
final return                     > 0
severe full CAGR                 > 0
next-snapshot latency CAGR       > 0
worst calendar quarter           >= -10%
top-quarter positive P&L share   <= 40%
zero undefined settlements
zero margin/max-loss breaches
```

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
