# Active V155–V162 — VIX calendar carry / convexity switch

Этот цикл использует сохранённые официальные месячные VX-контракты V154, но проверяет новую экономическую гипотезу.

- **Carry state:** малая позиция short front / long second только при спокойном contango.
- **Convex state:** малая long-front позиция при backwardation или подтверждённом скачке VIX.
- **Flat state:** когда ни carry, ни crisis regime не подтверждены.

Это не повторный подбор V154 long-tail threshold. V154 закрыт и не изменяется.

## Метод

- сигнал по завершённому дню;
- исполнение не раньше следующего open;
- явные месячные контракты и roll;
- costs на обе ноги calendar spread;
- margin и liquidation audit;
- synthetic overnight VIX shock до возможности выхода;
- selection только на 2006–2020;
- 2021–2023, 2024–2025 и 2026 H1 не участвуют в выборе;
- интеграция с V75 разрешается только после самостоятельного прохождения sleeve.

Гипотеза создана после просмотра провала V154 после 2020 года, поэтому program-level holdout не считается pristine даже при формальном frozen cutoff.

`live_ready = false`; `real_leverage_authorized = false`.
