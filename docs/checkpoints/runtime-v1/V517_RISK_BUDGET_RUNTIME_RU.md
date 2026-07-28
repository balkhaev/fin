# V517 tri-state guard — runtime shadow profile

Этот слой переносит замороженную механику V517/V524 из research в обычный Python API `finruntime.profiles.v517_guard`.

## Назначение

V517 принимает **уже запечатанный** `v75_atlas_nx` `StrategySnapshot`, вычисляет high/base/low risk state только по завершённой истории equity V75 и создаёт отдельный `v517_tristate_guard_shadow` snapshot. Исходный V75 snapshot не изменяется.

```text
completed V75 equity history
        ↓
causal 20/60-day trend state
        ↓
2.075x / 0.97x / 0.60x requested budget
        ↓
completed-profile-equity drawdown guard
        ↓
explicit outer runtime cap
        ↓
scaled shadow StrategySnapshot
        ↓
existing fail-closed gross/collateral planner
```

## Безопасный default

CLI `scripts/build_v517_shadow_snapshot.py` использует `--maximum-runtime-leverage 1.10` по умолчанию. Historical research budget 2.075x можно материализовать только явным параметром, но профиль всё равно зарегистрирован как `shadow` и не содержит exchange submission.

## Пример

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

CSV contract:

```text
as_of_utc,equity,source_sha256
2026-07-27T00:00:00Z,10000,sha256:...
```

Для следующего цикла `--output-state` передаётся через `--state`. История должна быть строго дневной, непрерывной, возрастающей по времени и полностью доступной до decision time primary snapshot.

## Что ещё блокирует live

1. Runtime всё ещё нуждается во внешнем exact V75 target producer; подмена похожим алгоритмом запрещена.
2. Historical 50.55% CAGR не имеет pristine program-level holdout.
3. Плечо 2.075x не прошло position-level margin/liquidation replay по отдельным spot/perpetual ногам.
4. Нет frozen forward периода с реальными fills, slippage и reconciliation.
5. В package отсутствует `submit_order`; registry отклоняет `mode=live`.

Поэтому этот профиль готов для deterministic paper/shadow integration, но не является разрешением реального капитала.
