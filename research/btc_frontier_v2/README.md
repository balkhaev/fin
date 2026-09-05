# BTC Frontier V2 — проверка цели +500%

**Цель +500% в год и 20+ сделок в день не достигнута.** Только research/paper:
нет ключей, реальных ордеров или изменения production scheduler. Работа находится
в отдельной ветке; main не изменяется без отдельного подтверждения.

## Эксперимент

128 конфигураций восьми семейств: breakout, trend pullback, impulse follow,
impulse fade, range reversion, squeeze, trend channel и funding reversion.
128 — число конфигураций, не независимых статистических гипотез; часть параметров
даёт одинаковые сигналы. Сравнение 2022–2023, по одному финалисту на семейство,
validation 2024. Ни один из восьми финалистов не прошёл полный порог допуска.

Протокол опубликован до расчётов: `2b4be34a82049f804d796c26eb5df68128ce83dc`.
Выбор до открытия цен августа: `c6d0704d1d61586b32e3acd65ce0639c8a7f2c1c`.
Диагностический победитель: `trend_channel_t240_w48_fast`. Он не допущен к
торговле: в 2022–2023 только 71 сделка против требуемых 100. Пороги не снижались.

2025 — июль 2026 уже просмотрены в предыдущем исследовании: это REUSED, не
pristine OOS. Август не участвовал в выборе параметров, но теперь уже открыт.
Его нельзя повторно назвать новым holdout. Это 31 исторический день, не live
forward и не основание переводить доходность месяца в годовую.

## Данные, расходы, риск

Binance USD-M BTCUSDT, 2022-01-01 — 2026-09-01 UTC (конец исключён):
2 453 760 trade minutes, 5 112 funding events. 110 старых +63 новых архива,
SHA-256 проверены. Новая часть: 44 640 trade candles, 44 640 mark candles,
93 funding events. Marks сохранены, но funding по-прежнему использует цену
начала минуты как приближение, не точную settlement mark. Hyperliquid не
бэктестировался; перенос чужих цен с его комиссией не выдаётся за проверку.

Начальный капитал $10 000; риск 0.25% на сделку; максимум 2x экспозиции.
Дневной выключатель -2%, выключатель по просадке -9.5%, порог приёмки DD<=10%.
Гэп может перескочить триггер. Нет усреднения, мартингейла или автоматического
перезапуска остановленного счёта. Годовые сбросы капитала подписаны отдельно.

Фиксированный fee scenario: 0.05% комиссии и 0.01% slippage на каждую сторону,
реальный funding. Это не историческая реконструкция VIP-тарифа. Lot 0.001 BTC
и minimum notional $100 — ограничения симуляции, не проверка exchangeInfo по
каждой дате. Закрытые данные -> следующий minute open, stop-first, adverse gaps,
trailing только после закрытия. Нет сертифицированного tick/L2/liquidation replay.

## Результаты выбранной конфигурации

4h close выше предыдущего 48-барного high -> long, ниже low -> short.
Стоп 1.5 ATR14, trailing на закрытых минутах с последним доступным 4h ATR,
дополнительный выход по противоположному 24-барному каналу. Фиксированного тейка
нет, max hold 288 четырёхчасовых баров. Это не частый скальпинг.

| Период | Net | Сделки |
|---|---:|---:|
| 2022 | +2.6137% | 34 |
| 2023 | +4.3468% | 37 |
| 2024 | +2.0719% | 46 |
| 2025 | -0.0269% | 48 |
| Январь–июль 2026 | -0.6453% | 26 |

Годы выше — независимые счета по $10 000. Непрерывный reused-счёт за 577 дней:
**-0.6722%, DD -1.4782%, 74 сделки**. Отдельный август: **+0.5930%, 2 сделки**.
Две сделки не устанавливают положительное ожидание. Риск 0.5%: reused -1.2944%,
август +1.1860% также всего на двух сделках. Все цифры net после расходов.

## Monthly router

Выбирает top-1 или CASH по предыдущим 180 дням с двухдневным embargo. Требует
net>0, PF>=1.1, >=20 сделок, DD<=10%. Выбранные сигналы заново исполняются на
едином счёте; независимые плечи не суммируются. Reused: **-7.2259%, 175 сделок,
DD -9.5% и halt**. Август: CASH, ноль сделок. На август параметры не менялись.

## Воспроизведение

Python 3.13, зависимости из существующего `research/btc_flow/requirements.txt`.

```bash
python -m pip install -r research/btc_flow/requirements.txt
python -m pytest -q tests/test_btc_flow.py tests/test_btc_frontier_v2.py
python -m research.btc_flow.download --out btc-evidence
python -m research.btc_frontier_v2.download_fresh --out fresh-evidence
python -m research.btc_frontier_v2.study --phase discover --data btc-evidence --out v2-discovery
python -m research.btc_frontier_v2.study --phase evaluate --data btc-evidence --selection v2-discovery/selection.json --fresh fresh-evidence --out v2-report
```

`summary.json` — компактные результаты, `selection_lock.json` — SHA-256 исходного
selection.json до открытия августа. CI повторно скачивает архивы, проверяет
выбранную конфигурацию, пересчитывает результаты и сравнивает значения с допуском
1e-6. Artifact содержит полные discovery/finalist JSON, trade ledgers, daily
equity, CSV и manifests. Успех CI означает воспроизводимость, не прибыльность.

Ограниченный поиск не доказывает невозможность иной прибыльной стратегии.
`target_achieved=false`, `live_ready=false`. Налоги и фиксированные расходы
инфраструктуры не включены. Дальнейшее использование августа — reused history.

Источники:
- https://github.com/binance/binance-public-data
- https://www.binance.com/en/fee/futureFee
- https://www.binance.com/en/support/faq/detail/360033544231
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
