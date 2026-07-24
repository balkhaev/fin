# Active V26 — quarterly basis cash-and-carry

Исследовательская проверка независимого BTC/ETH carry-sleeve: long spot и short соответствующего Binance USD-M dated quarterly future.

- completed daily close signal;
- исполнение на следующем UTC open;
- gross включает обе ноги и ограничен 1.0x;
- contract roll и expiry protection;
- отдельные spot/futures costs в четырёх сценариях;
- выбор по 2021–2025, январь–июнь 2026 не участвует в ranking.

**Решение:** `rejected_or_needs_iteration`. Стратегия имеет низкую просадку, но после stress costs даёт лишь около 0.45% CAGR, при extreme costs теряет 11.24%, а в 2026 H1 не открывает позиции. Frozen V8 остаётся growth benchmark; V26 не добавляется к нему.
