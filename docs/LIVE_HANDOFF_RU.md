# FIN: handoff от research к live

Текущий репозиторий содержит полный research/evidence слой, deterministic paper/shadow runtime, V136 execution filter, V517 tri-state risk-budget adapter, state-conditioned telemetry и forward mechanism validator.

## Что можно запускать сейчас

```text
V75 sealed StrategySnapshot           внешний вход runtime
V136 execution shadow                 готов
V517 risk-budget shadow               готов
pre-trade gross/collateral planner    готов
paper broker/accounting               готов
reconciliation/journal                готов
state telemetry                       готов
forward mechanism validator           готов
exchange order submission             отсутствует
```

Рекомендуемый текущий режим — **paper/shadow**, не real-money live.

## Shadow preflight

```bash
python -m pip install -e .
python scripts/live_preflight.py --mode shadow
```

Команда должна завершиться с кодом `0` и вернуть:

```json
{
  "shadow_ready": true,
  "live_ready": false,
  "exchange_submission_attempted": false
}
```

## Построение V517 shadow snapshot

V517 не генерирует alpha-target V75. Он масштабирует уже запечатанный `v75_atlas_nx` snapshot.

```bash
python scripts/build_v517_shadow_snapshot.py \
  --primary-snapshot runtime/v75_snapshot.json \
  --equity-history runtime/v75_equity_history.csv \
  --initialize-state \
  --profile-equity 10000 \
  --profile-high-water 10000 \
  --maximum-runtime-leverage 1.10 \
  --output-snapshot runtime/v517_snapshot.json \
  --output-decision runtime/v517_decision.json \
  --output-state runtime/v517_state.json
```

Для следующего цикла используйте `--state runtime/v517_state.json`, а не `--initialize-state`.

`1.10x` — безопасный внешний cap по умолчанию. Historical research budget `2.075x` нельзя переносить на реальные позиции до прохождения position-level margin replay.

## Paper cycle

После формирования sealed snapshot используйте существующий operations layer:

```bash
python -m finruntime init-account --help
python -m finruntime paper-cycle --help
python -m finruntime status --help
python -m finruntime verify-journal --help
```

Runtime сохраняет immutable cycle artifacts, hash-chain journal, fills/accounting, reconciliation и forward telemetry.

## State-conditioned evidence

```bash
python research/state_telemetry_v429_v436/evaluate_state_telemetry.py --self-test
python research/forward_mechanism_v445_v452/evaluate_forward_mechanism.py \
  --design research/forward_mechanism_v445_v452/V445_V452_DESIGN.json \
  --output /tmp/v445-self-test \
  --self-test
```

Реальные daily telemetry rows должны накапливаться без retuning V75/V136/V517.

## Live preflight

Real-money readiness требует четыре внешних доказательства:

1. exact V75 target producer с SHA-256
   `3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc`;
2. position-level margin/liquidation replay для фактических spot/perpetual ног до leverage `2.075x`;
3. минимум 180 дней frozen forward telemetry с успешным `FORWARD_ACCEPTANCE.json`;
4. отдельно реализованный и testnet-проверенный exchange adapter с idempotent orders, reduce-only, kill switch, environment secrets и fail-closed reconciliation.

Проверка:

```bash
python scripts/live_preflight.py \
  --mode live \
  --target-producer /secure/path/v75_operational_feedback_engine.py \
  --margin-audit /secure/evidence/position_margin_audit.json \
  --forward-acceptance /secure/evidence/FORWARD_ACCEPTANCE.json \
  --exchange-adapter-manifest /secure/evidence/exchange_adapter_manifest.json
```

До одновременного прохождения всех checks команда завершается ненулевым кодом и не вызывает exchange API.

## Шаблоны evidence

```text
config/live/position_margin_audit.template.json
config/live/forward_acceptance.template.json
config/live/exchange_adapter_manifest.template.json
```

Шаблоны имеют `template_only=true`; preflight намеренно отвергает их как доказательство.

## Исторический результат V517

V517/V524 достиг modeled full CAGR около `50.55%`, Sharpe `1.460` и Max DD `-23.68%`, но:

- параметры были informed уже известной историей;
- pristine program-level holdout отсутствует;
- low-state не наблюдался в доступной истории;
- maximum close gross достигал примерно `2.15x`;
- position-level margin replay и forward fills отсутствуют.

Поэтому эта цифра является engineering target, а не обещанием будущей доходности или разрешением плеча.

## Секреты

API keys не коммитятся и не передаются в CLI arguments. Exchange adapter обязан читать secrets только из environment/secret manager. `live_preflight.py` не читает и не печатает ключи.
