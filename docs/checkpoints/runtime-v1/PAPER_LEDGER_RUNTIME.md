# Runtime-v1 — paper execution, accounting and reconciliation

Этот checkpoint реализует M7–M8 после deterministic planner. Он не содержит exchange submission.

## Paper quote

Каждый quote содержит:

```text
instrument
market_type
observed_at_utc
source_observation_hash
bid + ask или explicit mid
available_quantity
quality
```

Исполнение использует bid/ask, если они доступны. При отсутствии bid/ask разрешён только заранее заданный spread proxy. Outage, invalid и stale quote по умолчанию не исполняются.

## Fill model

Default paper assumptions:

```text
spot commission          10 bps
perpetual commission      6 bps
proxy half-spread         4 bps
adverse impact            2 bps
participation rate       10%
```

Фактический FillEvent хранит fill price, fee, slippage и source observation hash. Partial, rejected и expired fills не переписывают исходный ExecutionPlan.

Perpetual sign-flip open не исполняется, пока parent reduce-only close не заполнен полностью.

## Paper account

Account state является immutable и hash-addressed. Он хранит:

- cash;
- spot base quantities;
- signed linear-perpetual base quantities;
- perpetual entry prices;
- cumulative fees;
- realized perpetual P&L;
- funding P&L;
- marked equity и high-water;
- last plan id;
- applied event ids.

Повторный FillEvent/FundingEvent idempotent. Corrupt или экономически невозможный переход вызывает `AccountingHalt`.

### Accounting rules

- spot buy уменьшает cash на notional + fee;
- spot sell увеличивает cash на notional − fee;
- perpetual opening меняет quantity/weighted entry, но не перечисляет полный notional;
- perpetual reduction реализует P&L относительно entry mark;
- positive funding rate: long pays, short receives;
- funding применяется по фактическому event timestamp;
- reduce-only fill не может пересечь позицию через zero;
- negative cash и non-positive equity запрещены.

## Reconciliation

Каждый цикл сравнивает:

```text
model targets
full-fill planned positions
actual paper positions
tracking error
modelled cost
paper cost
funding P&L
margin buffer
```

Status:

- `ok` — все обязательные проверки пройдены;
- `warn` — partial/incomplete execution, tracking error или cost overrun;
- `halt` — source hash mismatch, stale data или отрицательный margin buffer.

## Forward telemetry

Формируется строка, совместимая с V429 contract:

```text
timestamp,strategy_id,source_bundle_sha256,target_hash,
realized_position_hash,gross_target,gross_realized,turnover,
modelled_slippage_bps,paper_slippage_bps,net_return,equity,drawdown,
reconciliation_ok,source_hash_match,data_stale,execution_complete
```

Это делает возможным ежедневное накопление V75/V136/V28 observations после подключения frozen target engines.

## Safety

```text
submit surface              absent
live execution              false
real leverage authorized    false
profitability proven        false
capital change authorized   false
```
