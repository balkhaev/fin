# Полноценный бектест всех paper-стратегий

Дата проверки: 2026-07-30. Начальный капитал каждого независимого прогона:
`$10 000`. Кнопка в интерфейсе вызывает новый серверный расчёт и не читает
заранее сохранённый результат.

## Контракт

- Бектест не меняет paper-ledger и не имеет пути отправки ордеров на биржу.
- Клиент не передаёт период, капитал или параметры стратегии.
- Для текущих рыночных стратегий используется окно последних двух закрытых лет
  UTC: 2024-07-29—2026-07-29.
- Вход возможен только после публикации всех факторов. Внутрисвечное совпадение
  stop/target считается консервативно: stop исполняется первым.
- Каждый ответ содержит новый `run_id`, SHA-256 входов, количество запросов,
  фактические сделки и явные ограничения доказательства.
- Один процесс выполняет не более одного тяжёлого бектеста одновременно.

## Strategy identity и источники

### DYN-IV113

Текущий `dyn-iv113-paper-v1` заново прогоняется через production engine на
закрытых Binance Spot 1d свечах с warm-up. Это replay той же identity, которая
работает в paper.

### Atlas

Порог 50%+ относится к исторической identity
`v517_v524_v75_tristate_guard`, а не к reconstructed paper identity
`atlas_nx_r1`. Сервер при каждом клике заново применяет точный tri-state
leverage/DD-guard алгоритм к закреплённому account-level V75 equity stream и
проверяет normalized SHA-256
`f9d543ba8ec15c90efa757e64ed772b1a5934e458463124b7df48ddcac96ef01`.

Регрессионные значения полного периода 2021-01-01—2026-06-30 зашиты как
fail-closed проверки движка. Таблица показывает эпизоды целевого плеча последних
двух лет потока, потому что position-level ledger исходного V75 не сохранён.
Параметры V517 выбирались на известной истории; program-level holdout не pristine.

### Consensus WIF + DOT

Replay реализует текущий `consensus-wif-dot-v1`: Binance USD-M 15m contract
klines, WIF premium-index, исторический 5m open interest и DOT funding. WIF
входит на открытии следующей свечи после закрытого сигнала; DOT — через 15 минут
после уже опубликованного funding. Применяются production risk accelerator,
лимит gross/positions, 20 bps round-turn cost, stop, target, time exit и sticky
15% hard-stop.

### Funding Neutral

Replay причинно объединяет опубликованные Binance и Bybit funding rates и
закрытые часовые mark prices по BTC, ETH, PENDLE, WIF и DOT. Prediction fallback
совпадает с runtime: trailing median последних трёх известных ставок. Применены
нормализация funding-интервалов, basis/mark-divergence, fees, slippage и safety
buffers, одна позиция по `$1 000`, выход при collapse/reversal и максимум через
72 часа.

Исторические full-depth order books и точный venue OI за весь период публично не
доступны. Поэтому depth/OI live-фильтры не симулируются и явно отмечаются в
ответе. Это replay funding/basis core, а не доказательство полной live-механики.

## Измеренные результаты

| Стратегия | Период метрики | CAGR | Итоговый NAV | Max DD | Сделки/эпизоды |
|---|---:|---:|---:|---:|---:|
| DYN-IV113 | 2024-07-29—2026-07-29 | 142.549% | $58 760.73 | -31.092% | 56 |
| Atlas V517/V524 | 2021-01-01—2026-06-30 | 50.547706% | $94 834.07 | -23.679793% | account-level |
| Atlas V517/V524 | последние 2 года frozen stream | 43.344905% | рассчитывается из frozen stream | — | leverage episodes |
| Consensus WIF + DOT | 2024-07-29—2026-07-29 | 8.125377% | $11 689.88 | -15.650433% | 23 |
| Funding Neutral core | 2024-07-29—2026-07-29 | 0.000000% | $10 000.00 | 0.000000% | 0 |

Результат Consensus получен из 72 864 WIF и 72 864 DOT свечей, 72 768 premium
свечей и 58 143 OI observations. Из 39 предварительных WIF событий прошли 9;
DOT дал 49 сигналов; risk state допустил 23 сделки. Input SHA-256:
`c2595534320a9e50d52b09674a4978d3b6855bf6b6200ff0ac1efa4502d8f145`.

Funding проверил 231 market-data response (`17 667 771` bytes), но ни один
момент не прошёл строгий expected-net порог после всех доступных costs/buffers.
Поэтому ноль сделок и 0% — валидный отрицательный результат, а не заглушка.
Input SHA-256:
`52458996b34e4bd250ca935a2d362efc8fe677a470f446e89d84326e91fa11cd`.

## Проверки и readback

- Точный Atlas replay имеет deterministic assertions по CAGR, final NAV и Max DD.
- Unit-тесты проверяют разделение Atlas historical/paper identities, отсутствие
  OHLC-подмены factor-стратегий, trailing-only funding prediction и stop-first.
- Отдельный тест проверяет Funding exit contract: spread collapse и 72h max hold.
- UI показывает полный Atlas CAGR и отдельный CAGR последних двух лет, а вместо
  вымышленных asset trades выводит account leverage episodes.
- Production readback обязан включать четыре POST, WebSocket snapshot, disabled
  exchange submission и остановленные legacy `fin2`/`trader` приложения.

## Принятые ограничения

- Историческая доходность не гарантирует будущую.
- Atlas 50.55% не переносится на текущий `atlas_nx_r1` paper-account.
- V517 — account-level и non-pristine; это точная воспроизводимость известного
  исследования, а не независимый OOS claim.
- Funding может честно вернуть ноль сделок; порог ради красивого результата не
  ослабляется.
- Публичные market-data endpoints могут временно быть недоступны; запрос тогда
  завершается ошибкой и не публикует частичный CAGR.
