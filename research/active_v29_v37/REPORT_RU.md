# Active V29–V37: независимые funding/basis исследования

## Решение

Ни один из девяти кандидатов не прошёл заранее зафиксированные prefinal, cost и tail-risk gates. Параметры не ослаблялись после просмотра результатов. **Active V28 остаётся frozen growth benchmark.**

## Сводка

| Версия | Идея | Prefinal CAGR | Stress full CAGR | Max DD | 2026 H1 | Решение |
|---|---|---:|---:|---:|---:|---|
| V29 | BTC/ETH negative-funding squeeze | — | 2.15% | -7.85% | 2.74% | rejected |
| V30 | funding-aware exit hysteresis | — | 26.58% | -23.19% | 12.10% | rejected |
| V31 | spot/perp instrument router | — | 26.55% | -23.19% | 12.12% | rejected |
| V32 | dynamic BTC/ETH carry | — | 26.90% | -23.22% | 9.77% | rejected |
| V33 | 13-asset spot/perp carry | -0.22% | -0.20% | -1.12% | 0.00% | rejected |
| V34 | negative-vs-positive funding contrarian | 1.43% | 1.30% | -3.14% | 0.00% | rejected |
| V35 | cross-sectional funding dispersion | 0.90% | 0.73% | -8.63% | -0.49% | rejected |
| V36 | basis z-score convergence | -0.16% | -0.14% | -0.77% | 0.00% | rejected |
| V37 | regime-conditioned directional funding | 1.76% | 1.65% | -2.34% | 0.27% | rejected |

## Главные выводы

- Положительный funding сам по себе не является edge: basis, relative-price risk и двусторонние расходы часто больше начисления.
- Удаление spot-ноги уменьшает расходы, но превращает carry в cross-sectional или directional risk; V34/V35 это не компенсировали.
- V37 показал слабый низкорисковый bull-squeeze эффект, но он не был положительным во всех prefinal-сегментах и исчезал при extreme costs.
- V30/V31 почти не изменили V26: усложнение исполнения без материальной экономической прибавки отклонено.
- V32 выглядел лучше в stress-сценарии, но не выдержал самые тяжёлые расходы и потому не заменяет exact-8h V28.

## Методология

- final 2026 H1 не участвовал в выборе параметров;
- completed information / next-open execution;
- реальные funding timestamps для exact-8h V33–V37;
- фиксированный January-2021 universe с делистнутыми и слабыми активами;
- stress/severe/extreme/super/catastrophic costs;
- отрицательные результаты и source hashes сохранены.

## Provenance

Публичные compute artifacts V33–V37 перечислены в `provenance.json`. Source bundle восстанавливается и компилируется `verify_archive.py`.
