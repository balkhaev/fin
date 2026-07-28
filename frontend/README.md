# FIN Control Room

Статический frontend для research/paper/shadow контура `balkhaev/fin`.

Он показывает:

- V75/V136/V509/V517 и их разные роли;
- equity, drawdown и account-level risk budget V517;
- base/severe/extreme/delay stress surface;
- архивный market-state vector V413;
- fail-closed live-readiness gates;
- локальный импорт runtime telemetry CSV/JSON без отправки данных наружу.

## Локальный запуск

```bash
python scripts/build_frontend_data.py
python -m http.server 8000 --directory frontend
```

Откройте `http://localhost:8000`.

Frontend не требует npm, bundler или внешних JavaScript-библиотек. Все данные собираются из committed research evidence. Импортированный telemetry-файл обрабатывается только в браузере и никуда не загружается.

## GitHub Pages

Workflow `deploy-control-room-pages.yml` запускается вручную. Перед первым запуском включите Pages с источником **GitHub Actions** в настройках репозитория.

## Safety boundary

Панель не создаёт ордера и не хранит API-ключи. Значение `live_ready` вычисляется fail-closed и остаётся `false`, пока не пройдены exact target producer, position-level margin replay, frozen forward acceptance и exchange-adapter gates.
