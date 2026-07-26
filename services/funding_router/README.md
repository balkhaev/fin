# Funding Dislocation Router

Защищённый сервис для поиска и исполнения дельта-нейтральных funding-dislocation сделок между perpetual-площадками.

Сервис ищет один и тот же базовый актив на разных биржах, нормализует funding к часу, открывает:

```text
LONG  на площадке с более низким funding
SHORT на площадке с более высоким funding
```

и оценивает не экранный APR, а консервативный результат после:

- maker/taker комиссий входа и выхода;
- фактического VWAP доступной глубины;
- межбиржевого basis;
- slippage buffer;
- adverse-selection и exit-basis buffer;
- различающихся funding-интервалов;
- текущей и прогнозной funding-ставки.

## Состояние

Код завершён и детерминированно протестирован. Private trading по умолчанию заблокирован.

```json
{
  "implementation_complete": true,
  "live_ready": false,
  "real_leverage_authorized": false,
  "profitability_proven": false
}
```

Причина: ни тесты, ни код не могут доказать будущую доходность или заменить authenticated smoke tests конкретных аккаунтов и площадок. Сначала нужен длительный paper-run. Полный статус находится в [`STATUS.json`](STATUS.json).

## Реализовано

- асинхронный CCXT-адаптер;
- `fetchFundingRate`, funding history fallback, order books и open interest;
- нормализация 1h/4h/8h funding;
- точный VWAP на заданное количество базового актива;
- пересчёт contract count через `contractSize`;
- одинаковый base amount на обеих ногах;
- фильтры predicted reversal, OI, depth и basis;
- scan, persistent paper и guarded live режимы;
- passive-maker первая нога;
- немедленный hedge каждой новой частичной maker-заливки;
- timeout/cancel незаполненного остатка;
- повторные `reduceOnly` закрытия;
- отказ работать поверх посторонних позиций;
- проверка свободного collateral;
- SQLite WAL, журнал событий и восстановление после рестарта;
- аварийное закрытие при повреждённом persisted state;
- закрытие live-позиции при SIGTERM по умолчанию;
- Docker Compose, systemd unit и GitHub Actions.

## Установка

Требуется Python 3.11+.

```bash
cd services/funding_router
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp config.example.toml config.toml
cp .env.example .env
funding-router --config config.toml validate
pytest -q
```

CCXT unified API и асинхронный модуль описаны в официальной документации:

- https://github.com/ccxt/ccxt
- https://github.com/ccxt/ccxt/wiki/manual

## Режим 1: public scan

API-ключи не нужны.

```bash
funding-router --config config.toml scan --once
funding-router --config config.toml scan
```

Результат содержит принятые кандидаты, причины отклонения и ошибки отдельных public endpoints. Ошибка одной площадки не останавливает сбор данных с остальных.

## Режим 2: paper

```bash
funding-router --config config.toml paper
```

Paper state сохраняется в SQLite. Round-trip costs списываются сразу при виртуальном входе, а funding начисляется дискретно по расписанию каждой ноги. Это намеренно консервативнее красивой APR-экстраполяции.

Проверка состояния:

```bash
funding-router --config config.toml status --events 50
```

Минимальный критерий перед live:

```text
30 дней без остановок
30+ полностью закрытых paper-позиций
положительный realized P&L после всех modelled costs
нулевые случаи необъяснимой delta mismatch
нулевые случаи повреждения SQLite/recovery
```

## Режим 3: live

Live требует одновременно три разрешения:

1. `live.enabled = true` в `config.toml`;
2. точное значение environment confirmation phrase;
3. CLI-флаг `--confirm-live`.

```bash
export FUNDING_ROUTER_LIVE_CONFIRM=YES_I_UNDERSTAND
funding-router --config config.toml live --confirm-live
```

Без любого из трёх условий private order call не выполняется.

### API-ключи

Создавайте отдельные ключи:

- только futures/perpetual trading;
- **без права вывода**;
- с IP allowlist Hetzner;
- отдельный sub-account на каждой площадке;
- только ограниченная сумма;
- не используйте ключ от основного портфеля.

Credentials читаются только из переменных окружения, имена которых заданы в `config.toml`.

### Важная особенность

Общая позиция дельта-нейтральна, но каждая биржа видит только одну ногу. При резком движении short может приблизиться к ликвидации, хотя long на другой площадке получает сопоставимую прибыль. Поэтому сервис:

- устанавливает ограниченное leverage из конфигурации;
- проверяет free collateral на обеих площадках;
- не открывает вторую позицию;
- не принимает unmanaged positions;
- закрывает обе ноги при потере funding edge;
- закрывает позицию при потере market data три цикла подряд;
- по умолчанию flatten-ит позицию при штатном завершении процесса.

## Расчёт кандидата

Для каждой пары:

```text
current_spread_per_hour = short_rate / short_interval
                        - long_rate  / long_interval

predicted_spread_per_hour = predicted_short / short_interval
                          - predicted_long  / long_interval

conservative_spread = min(current_spread, predicted_spread)

gross_funding_bps = conservative_spread × hold_hours × 10 000

expected_net_bps = gross_funding_bps
                 - entry_and_exit_fees
                 - measured_entry_slippage
                 - unfavorable_entry_basis
                 - configured_exit_basis_buffer
                 - configured_adverse_selection_buffer
```

Благоприятный entry basis намеренно не засчитывается как гарантированный доход.

## Исполнение

```text
scan
  ↓
check predicted funding / OI / depth / basis
  ↓
verify free collateral and absence of unmanaged positions
  ↓
post-only order on less liquid leg
  ↓
for every incremental maker fill:
    immediate market hedge on second venue
  ↓
cancel unfilled maker remainder on timeout
  ↓
reconcile actual positions
  ↓
persist OPEN state in SQLite
```

При любой ошибке входа сервис считывает реальные позиции и пытается закрыть обе стороны через `reduceOnly`.

## Docker Compose

```bash
cp config.example.toml config.toml
cp .env.example .env
mkdir -p data
docker compose build
docker compose up -d
docker compose logs -f
```

Compose по умолчанию запускает **paper**, не live.

## Systemd на Hetzner

Пример unit находится в [`deploy/funding-router.service`](deploy/funding-router.service). Он также запускает paper. Для установки:

```bash
sudo useradd --system --home /opt/funding-router --shell /usr/sbin/nologin funding-router
sudo mkdir -p /opt/funding-router/data
sudo chown -R funding-router:funding-router /opt/funding-router
sudo cp deploy/funding-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now funding-router
```

Не переключайте unit на live до завершения paper acceptance criteria.

## Тесты

```bash
python -m compileall -q src tests
pytest -q
funding-router --config config.example.toml validate
```

Покрываются:

- TOML validation;
- funding normalization;
- VWAP и insufficient depth;
- predicted reversal;
- excessive basis;
- partial maker fills;
- incremental hedging;
- emergency flatten;
- unmanaged positions;
- free collateral;
- repeated reduce-only closing;
- SQLite recovery;
- discrete paper funding;
- derivative `contractSize` conversion.

## Ограничения

1. CCXT унифицирует API, но exchange-specific параметры всё равно отличаются. `options` и `params` в конфиге необходимо проверить на минимальном размере.
2. Некоторые площадки не публикуют надёжный `nextFundingRate`; сервис использует median недавней funding history, а при полном отсутствии прогноза отклоняет сделку, если включён `require_predicted_confirmation`.
3. Funding может исчезнуть непосредственно после входа.
4. Дельта-нейтральность не устраняет basis, liquidation, exchange solvency, withdrawal freeze, API outage и ADL risk.
5. Целевые 40%+ не гарантируются. Высокая annualized ставка часто живёт несколько часов, а комиссии платятся полностью.
6. Перед использованием убедитесь, что выбранные площадки и derivatives доступны в вашей юрисдикции.
