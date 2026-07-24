# fin

Репозиторий воспроизводимых исследований торговых алгоритмов.

Цель — сохранять код, предположения, происхождение данных, отрицательные результаты и положительные кандидаты, не выдавая переобучение за рабочую стратегию.

## Текущий checkpoint

**Research checkpoint V68 — 2026-07-25.** Parent `main` перед checkpoint: `6f9f18883265a1c95bbdc2caad43de089b1533f5`.

- [Полный checkpoint и текущее решение](docs/checkpoints/v68/RESEARCH_CHECKPOINT_V68_FULL_RU.md)
- [Полный ledger V1–V68](docs/checkpoints/v68/RESEARCH_LEDGER_V1_V68.csv)
- [Годовые результаты](docs/checkpoints/v68/ANNUAL_RETURNS.csv)
- [Machine-readable checkpoint](docs/checkpoints/v68/CHECKPOINT_V68.json)
- [Verification ledger](docs/checkpoints/v68/VERIFICATION_LEDGER.json)
- [Exact V67/V68 local reproof](docs/checkpoints/v68/V67_V68_LOCAL_REPROOF.json)

## Правила

- completed information → next available open;
- overnight move старой позиции учитывается до ребалансировки;
- costs, slippage proxy, funding, delisting и collateral учитываются явно;
- selection proof фиксируется до final;
- пороги не ослабляются после просмотра;
- отрицательные результаты не удаляются;
- ноль при нулевой экспозиции не является forward-подтверждением;
- ни один кандидат V1–V68 не имеет статуса `live_ready`.
