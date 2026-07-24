# Active V26 — BTC/ETH quarterly basis cash-and-carry

## Решение

**Отклонён.** 66 из 1 944 конфигураций прошли предфинальный фильтр, но замороженный ансамбль не прошёл финальные экономические gates.

## Конструкция

- long BTC/ETH spot;
- short соответствующий USD-M quarterly future;
- сигнал по completed close, исполнение на следующем UTC open;
- старая позиция получает overnight move до ребалансировки;
- обе ноги включены в gross;
- roll за 2–7 дней до expiry;
- cash yield не добавлялся;
- 2026 H1 не использовался при выборе.

## Выбранный ансамбль

- `basis_e8_x0_min30_max60_roll2_equal_g60`;
- `basis_e8_x0_min21_max60_roll2_equal_g80`;
- `basis_e8_x2_min21_max60_roll2_equal_g60`.

Все три требуют входной annualized basis около 8%; target gross 0.6–0.8x.

## Полный путь 2021 — июнь 2026

| Costs | Return | CAGR | Max DD | Sharpe | Turnover/yr | Modelled costs on 10k |
|---|---:|---:|---:|---:|---:|---:|
| base | 5.01% | 0.89% | -1.57% | 1.14 | 5.83x | 246.14 |
| stress | 2.52% | 0.45% | -1.69% | 0.56 | 5.83x | 486.44 |
| severe | -2.29% | -0.42% | -2.58% | -0.46 | 5.83x | 950.08 |
| extreme | -11.24% | -2.15% | -11.24% | -1.79 | 5.83x | 1813.15 |

Stress 2026 H1: **0.00%**, без открытых позиций.

## Почему отклонён

Прошли:

- все четыре prefinal stress-периода положительны;
- worst severe prefinal return выше −5%;
- stress Max DD -1.69% лучше пола −12%;
- turnover 5.83x ниже 12x;
- final 2026 H1 не хуже −2%.

Не прошли:

- stress CAGR 0.45% ниже обязательных 3%;
- extreme full return -11.24% вместо требуемого положительного результата.

Carry слишком мал относительно roll/implementation costs. Низкая просадка не компенсирует доходность ниже разумной cash-альтернативы. Параметры после просмотра результата не ослаблялись.

## Данные и воспроизводимость

- 44 dated contracts: 22 BTC и 22 ETH;
- 532 archive attempts; 390 существующих архивов;
- все 390 доступных checksum прошли; 0 checksum failures;
- 142 отсутствующих quarterly archives относятся к месяцам до listing или после expiry;
- source SHA-256 `c3d047a80dca8465bce5f57de3cfcbb892358f0b963792222468172156f1dea1`;
- green artifact SHA-256 `d678e916756ff8256929e741c352de90827fc7ea359d04e0ed9f740fe416074e`;
- public compute PR: `balkhaev/trader#14`.

V26 сохраняется как отрицательный контроль и не разрешён для live trading, leverage или объединения margin с V8.
