# Runtime contracts

Все runtime-объекты сериализуются в canonical JSON:

```text
UTF-8
sorted keys
compact separators
no NaN/Infinity
UTC ISO-8601 with `Z`
decimal quantities as strings where exchange precision matters
```

Hash:

```text
sha256(canonical_json_bytes)
```

## 1. MarketSnapshot

```json
{
  "schema_version": "1.0",
  "snapshot_id": "sha256:...",
  "as_of_utc": "2026-07-26T00:00:00Z",
  "decision_time_utc": "2026-07-26T00:05:00Z",
  "sources": {
    "spot_daily": {
      "source": "...",
      "source_timestamp_utc": "...",
      "available_at_utc": "...",
      "payload_sha256": "...",
      "quality": "ok"
    }
  },
  "spot": {},
  "perp": {},
  "funding_events": [],
  "onchain": {},
  "cash_rate": {},
  "quality_flags": []
}
```

Invariants:

- `available_at_utc <= decision_time_utc` для каждого использованного source;
- одна observation не может иметь два разных payload hashes;
- критический source со статусом `missing`, `future` или `invalid` запрещает увеличение риска;
- stale on-chain старше 48 часов обнуляет accelerator permission.

## 2. StrategySnapshot

```json
{
  "schema_version": "1.0",
  "strategy_id": "v75_atlas_nx",
  "strategy_version": "runtime-v1",
  "decision_time_utc": "...",
  "market_snapshot_id": "sha256:...",
  "state_sequence": 123,
  "targets": {
    "spot": {"BTC/USDT": "0.20"},
    "perp": {"BTC/USDT:USDT": "-0.05"}
  },
  "gross_target": "0.25",
  "cash_target": "0.75",
  "risk": {
    "high_water": "...",
    "ratchet_stages": [0, 1, 1],
    "defensive_weight": "0.10",
    "accelerator_scale": "0.00",
    "gross_cap": "1.05",
    "volatility_multiplier": "0.75"
  },
  "reasons": [],
  "quality_flags": [],
  "target_hash": "sha256:..."
}
```

## 3. PortfolioState

```json
{
  "schema_version": "1.0",
  "strategy_id": "v75_atlas_nx",
  "sequence": 123,
  "as_of_utc": "...",
  "cash": "10000.00",
  "equity": "10500.00",
  "high_water": "11000.00",
  "positions": {
    "spot": {},
    "perp": {}
  },
  "held_targets": {
    "spot": {},
    "perp": {}
  },
  "target_age_days": 4,
  "pending_plan_id": null,
  "last_market_snapshot_id": "sha256:...",
  "last_target_hash": "sha256:...",
  "last_plan_hash": "sha256:...",
  "state_hash": "sha256:..."
}
```

Invariants:

- `high_water >= equity` after cycle close;
- high-water не уменьшается;
- sequence строго возрастает только после нового accepted event set;
- отрицательный cash допускается только если это явно разрешённый collateral accounting state; для runtime-v1 такой режим запрещён;
- state reconstructible из event log.

## 4. ExecutionIntent

```json
{
  "intent_id": "sha256:...",
  "instrument": "BTC/USDT:USDT",
  "venue": "paper",
  "market_type": "perpetual",
  "side": "buy",
  "reduce_only": true,
  "quantity": "0.010",
  "quantity_unit": "base",
  "reference_price": "65000.0",
  "max_slippage_bps": "10",
  "reason": "perpetual_sign_flip_close",
  "parent_intent_id": null,
  "not_before_utc": "...",
  "expires_at_utc": "..."
}
```

## 5. ExecutionPlan

```json
{
  "schema_version": "1.0",
  "plan_id": "sha256:...",
  "strategy_id": "v75_atlas_nx",
  "mode": "paper",
  "created_at_utc": "...",
  "market_snapshot_id": "sha256:...",
  "state_sequence": 123,
  "target_hash": "sha256:...",
  "intents": [],
  "risk_summary": {
    "gross_before": "...",
    "gross_after_target": "...",
    "margin_required": "...",
    "cash_required": "..."
  },
  "plan_hash": "sha256:..."
}
```

Rules:

- `mode` только `paper` или `shadow`;
- `live` invalid schema value;
- risk-reducing intents идут раньше risk-increasing;
- sign flip — минимум два intents;
- один state sequence + snapshot id + profile даёт один plan id.

## 6. FillEvent

```json
{
  "schema_version": "1.0",
  "event_id": "sha256:...",
  "plan_id": "sha256:...",
  "intent_id": "sha256:...",
  "filled_at_utc": "...",
  "status": "partial",
  "filled_quantity": "0.005",
  "price": "65010.0",
  "fee": "0.25",
  "fee_currency": "USDT",
  "slippage_bps": "1.54",
  "source_observation_hash": "sha256:..."
}
```

## 7. ReconciliationReport

```json
{
  "schema_version": "1.0",
  "strategy_id": "v75_atlas_nx",
  "as_of_utc": "...",
  "model_targets": {},
  "planned_positions": {},
  "paper_positions": {},
  "tracking_error_fraction": "0.0012",
  "modelled_cost": "...",
  "realized_paper_cost": "...",
  "funding_pnl": "...",
  "margin_buffer": "...",
  "alerts": [],
  "status": "ok",
  "report_hash": "sha256:..."
}
```

Status values:

```text
ok
warn
halt
```

## 8. Event log

Каждая строка append-only JSONL:

```json
{
  "event_type": "PLAN_CREATED",
  "event_time_utc": "...",
  "strategy_id": "...",
  "sequence": 123,
  "payload": {},
  "payload_hash": "sha256:...",
  "previous_event_hash": "sha256:...",
  "event_hash": "sha256:..."
}
```

Required event types:

```text
SNAPSHOT_ACCEPTED
TARGET_COMPUTED
PLAN_CREATED
FILL_RECORDED
FUNDING_RECORDED
STATE_COMMITTED
RECONCILIATION_COMPLETED
HALT_RAISED
HALT_CLEARED
```

## 9. Strategy registry

```json
{
  "registry_version": "runtime-v1",
  "live_execution_available": false,
  "strategies": {
    "v75_atlas_nx": {
      "role": "primary",
      "allowed_modes": ["paper", "shadow"],
      "live_ready": false,
      "real_leverage_authorized": false
    },
    "v28_growth_control": {
      "role": "control",
      "allowed_modes": ["paper", "shadow"],
      "live_ready": false,
      "real_leverage_authorized": false
    },
    "v136_execution_shadow": {
      "role": "shadow",
      "allowed_modes": ["shadow"],
      "live_ready": false,
      "real_leverage_authorized": false
    }
  }
}
```

## 10. Compatibility policy

- добавление optional field — minor schema version;
- изменение смысла field — major version;
- frozen strategy config change — новый strategy id/version и новый forward clock;
- migration обязана сохранять старый event log и записывать migration event;
- runtime не читает неизвестную major schema version.
