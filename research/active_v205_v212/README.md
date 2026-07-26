# Active V205–V212 — actual taker-flow × depth interaction

V197–V204 показал, что standalone depth imbalance/replenishment не имеет положительного development edge после 10 bps round-trip cost floor. Этот отдельный цикл не ослабляет его пороги. Он проверяет новую экономическую гипотезу: **агрессивный taker flow может иметь смысл только во взаимодействии с доступной ликвидностью и ценовым откликом**.

## Fixed universe

```text
venue: Binance COIN-M
symbols: BTCUSD_PERP, ETHUSD_PERP
sources: bookDepth + regular 1m klines
```

Regular klines используются ради фактического `volume` и `taker_buy_volume`; mark price остаётся proxy execution/reference price. Исторического непрерывного BBO нет, поэтому даже положительный результат будет research-only.

## Versions

- **V205** — checksum/schema/coverage of actual 1m taker flow;
- **V206** — causal flow-depth panel and provenance;
- **V207** — flow/depth agreement continuation;
- **V208** — absorption and flow-exhaustion reversal;
- **V209** — immutable development selection;
- **V210** — costs, latency, concentration and false-pressure controls;
- **V211** — validation/holdout/final and guarded integration;
- **V212** — checkpoint and frozen paper-forward protocol.

## Frozen chronology

```text
development: 2023-01-01 through 2024-06-30
validation:  2024-07-01 through 2024-12-31
holdout:     2025-01-01 through 2025-12-31
final:       2026-01-01 through 2026-06-30
```

Program-level holdout is explicitly non-pristine. Selection ends before validation.

## Causal features

For each completed UTC minute:

```text
taker imbalance = 2 * taker_buy_volume / total_volume - 1
signed flow      = 2 * taker_buy_volume - total_volume
flow z-score     = causal 24h rolling z-score of signed flow
volume z-score   = causal 24h rolling z-score of log volume
depth pressure   = frozen V198 near/wide depth pressure z-score
price impact     = completed 3-minute mark-price move
```

Entry is no earlier than the next minute mark open. The signal never uses future flow, depth or extrema.

## Frozen families

1. **agreement continuation** — flow and depth pressure are extreme with the same sign;
2. **flow-vacuum continuation** — extreme flow agrees with pressure while near depth is depleted;
3. **absorption reversal** — extreme taker flow meets opposite depth pressure and limited price progress;
4. **flow exhaustion reversal** — extreme flow and volume produce unusually small price impact;
5. **reversed agreement control** — side label reversed; never promotion-eligible.

Exact grid is machine-readable and frozen before any P&L.

## Risk and costs

```text
target gross:                  0.20x
maximum concurrent positions:  1
base round-trip:               10 bps
severe round-trip:             20 bps
extreme round-trip:            35 bps
latency audit:                 +1 minute
forced missing-data exit:      +20 bps
```

## Standalone gates

```text
development CAGR               >= 5%
development Sharpe             >= 1.00
development Max DD             >= -10%
development closed trades      >= 250
BTC and ETH development return > 0
validation return              > 0
holdout return                 > 0
final return                   > 0
severe full CAGR               > 0
latency full CAGR              > 0
worst calendar quarter         >= -8%
top-month positive P&L share   <= 30%
zero unexplained events
```

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
