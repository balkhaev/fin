# Runtime-v1 — paper operations runbook

Этот runbook запускает только `paper`/`shadow` цикл. В runtime отсутствует exchange submit adapter.

## 1. Инициализация отдельного счёта

```bash
python -m finruntime init-account \
  --root runtime-state \
  --strategy v75_atlas_nx \
  --as-of-utc 2026-07-28T00:05:00Z \
  --starting-cash 10000
```

Повторная команда не перезаписывает существующий state без `--force`. Для V75, V28 и V136 используются разные strategy roots, journals и telemetry CSV.

## 2. Required sealed inputs

До цикла должны существовать:

```text
market_snapshot.json
strategy_snapshot.json
paper_quotes.json
reference_prices.json
```

### `paper_quotes.json`

JSON array:

```json
[
  {
    "instrument": "BTC/USDT",
    "market_type": "spot",
    "observed_at_utc": "2026-07-28T00:06:00Z",
    "source_observation_hash": "sha256:...",
    "bid": "99950",
    "ask": "100050",
    "mid": "100000",
    "available_quantity": "0.50",
    "quality": "ok"
  }
]
```

### `reference_prices.json`

```json
{
  "spot": {
    "BTC/USDT": {"reference_price": "100000"}
  },
  "perp": {
    "BTC/USDT:USDT": {"reference_price": "100000"}
  }
}
```

Reference prices должны быть deterministic observations, а не ручной estimate.

## 3. Запуск цикла

```bash
python -m finruntime paper-cycle \
  --root runtime-state \
  --market-snapshot market_snapshot.json \
  --strategy-snapshot strategy_snapshot.json \
  --quotes paper_quotes.json \
  --reference-prices reference_prices.json \
  --critical-source spot_daily \
  --critical-source perp_8h \
  --onchain-source onchain_features \
  --modelled-cost 2.50 \
  --modelled-slippage-bps 8
```

Exit codes:

```text
0  committed/reconciled or warn
2  reconciled HALT
other  contract, corruption or operational failure
```

`--no-source-hash-match` и `--data-stale` используются только для явной фиксации обнаруженного incident; они не позволяют продолжить risk increase.

## 4. Материализованные файлы

Для каждой стратегии:

```text
runtime-state/<strategy>/
├── account_state.json
├── events.jsonl
├── forward_telemetry.csv
└── cycles/<cycle-id>/
    ├── request_identity.json
    ├── risk_decision.json
    ├── execution_plan.json
    ├── fill_events.json
    ├── fill_outcomes.json
    ├── account_state.json
    ├── reconciliation.json
    ├── forward_telemetry.json
    └── COMMITTED.json
```

`COMMITTED.json` записывается до global state/telemetry side effects. Поэтому повторный запуск того же cycle id восстанавливает отсутствующий `account_state.json` или telemetry row, не дублируя journal events.

## 5. Проверка состояния

```bash
python -m finruntime status \
  --root runtime-state \
  --strategy v75_atlas_nx

python -m finruntime verify-journal \
  runtime-state/v75_atlas_nx/events.jsonl
```

Corrupt JSON, broken event hash chain, conflicting telemetry primary key или попытка rollback state завершают процесс с ошибкой.

## 6. Daily operating order

```text
1. Seal MarketSnapshot and payload hashes.
2. Produce frozen StrategySnapshot externally.
3. Seal quotes/reference prices.
4. Run paper-cycle once.
5. Read reconciliation status.
6. Verify journal.
7. Publish telemetry only from committed cycle artifact.
```

До materialization exact V75/V28 target engines шаг 2 остаётся внешним provenance-blocked input. Runtime не генерирует substitute targets.

## Safety

```text
submit surface              absent
live execution              false
real leverage authorized    false
capital change authorized   false
```
