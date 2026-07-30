# Active V197–V204 — actual depth imbalance and replenishment

Этот цикл ищет независимый intraday P&L на **фактических Binance COIN-M order-book depth snapshots**, а не на свечном объёме, taker-volume proxy или open-interest regime.

V75/V28/V136 не изменяются. Новый sleeve не интегрируется до самостоятельного прохождения всех gates.

## Fixed universe

```text
venue: Binance COIN-M
symbols: BTCUSD_PERP, ETHUSD_PERP
sources: bookDepth, bookTicker, markPriceKlines 1m, metrics
```

## Версии

- **V197** — checksum-aware schema/frequency/coverage probe;
- **V198** — normalized causal depth panel and provenance;
- **V199** — imbalance continuation family;
- **V200** — depth exhaustion/replenishment reversal family;
- **V201** — immutable development selection;
- **V202** — fees, latency, missing-book and concentration audits;
- **V203** — holdout/final and guarded integration;
- **V204** — checkpoint and frozen paper-forward protocol.

## Frozen chronology

```text
development: 2023-01-01 through 2024-06-30
validation:  2024-07-01 through 2024-12-31
holdout:     2025-01-01 through 2025-12-31
final:       2026-01-01 through 2026-06-30
```

Program-level holdout is explicitly non-pristine.

## Mandatory data gate

Before any P&L, the archive must permit deterministic reconstruction of:

- event timestamp and observation frequency;
- bid-side and ask-side depth at matched distance buckets;
- mid/reference price or synchronized executable BBO;
- no crossed/negative book;
- continuous coverage in development, holdout and final;
- checksum and raw SHA-256 provenance.

An unknown 404 is not a zero-depth observation. Future book updates are forbidden in the signal.

## Predeclared signal families

Exact numerical grid is materialized only after V197 reveals native field units, but the economic families are frozen now:

1. **imbalance continuation** — trade with persistent same-sign depth imbalance;
2. **replenishment reversal** — fade price displacement when depleted side refills;
3. **liquidity vacuum continuation** — trade only when near-side depth collapses and spread remains executable;
4. **false-pressure control** — identical rules with side labels reversed.

Selection cannot add a new family after viewing 2025–2026.

## Risk and execution

```text
target gross:                    0.20x
maximum concurrent positions:    1
entry:                            next available executable BBO
holding horizons to freeze:      native 1 / 3 / 12 observations
base round-trip cost floor:      10 bps
severe round-trip cost floor:    20 bps
extreme round-trip cost floor:   35 bps
latency audit:                    +1 native observation
missing/crossed book:             immediate no-new-risk / forced close
```

## Standalone gates

```text
development CAGR                 >= 5%
development Sharpe               >= 1.00
development Max DD               >= -10%
development closed trades        >= 250
BTC and ETH return               > 0
validation return                > 0
holdout return                   > 0
final return                     > 0
severe full CAGR                 > 0
latency full CAGR                > 0
worst calendar quarter           >= -8%
top-month positive P&L share     <= 30%
zero unexplained book events
```

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
