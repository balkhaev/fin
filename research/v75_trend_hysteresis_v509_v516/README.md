# V509–V516 — V75 trend-hysteresis accelerator

V501 показал trade-off: постоянное плечо 1,75× поднимает development CAGR до 47,26%, но расширяет Max DD до −29,93%; обычный drawdown governor удерживает DD около −24%, но оставляет CAGR около 39%.

Этот цикл проверяет более ранний причинный механизм: общий risk budget меняется только после устойчивого движения уже завершённой equity V75.

## Frozen signal

```text
momentum20(t) = equity(t-1) / equity(t-21) - 1
```

Hysteresis:

- high-risk state включается после momentum20 выше +6% или +8%;
- high-risk state выключается после momentum20 ниже −2%;
- состояние держится минимум 14 или 21 день;
- high leverage: 1,85× или 1,95×;
- low leverage: 0,60×, 0,80× или 1,00×;
- rebalancing: 7 или 14 дней;
- urgent reductions разрешены немедленно.

Ни current-day return, ни будущая V75 equity не участвуют в сегодняшнем leverage decision.

## Chronology

```text
development        2021–2023
validation         2024
holdout            2025
final               2026 H1
```

2024–2026 H1 открываются один раз только после immutable development proof.

## Development requirements

```text
CAGR                         >=50%
Sharpe                       >=1.50
Max DD                       >=-25%
all development years        positive
average leverage             <=1.35x
maximum leverage             <=1.95x
meta turnover                <=6x/year
severe CAGR / DD             >=40% / >=-28%
extreme CAGR / DD            >=30% / >=-35%
1-day-delay CAGR / DD        >=50% / >=-25%
```

## Evidence boundary

Это non-pristine account-level overlay над исторически известным V75. Даже полный исторический pass не разрешает реальное плечо: требуется position-level margin replay и отдельный forward период.

```text
integration_permitted      false
capital_change_authorized  false
live_ready                 false
real_leverage_authorized   false
profitability_proven       false
```
