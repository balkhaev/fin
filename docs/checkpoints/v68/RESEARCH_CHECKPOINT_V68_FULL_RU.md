# Research checkpoint V68 — 2026-07-25

Этот checkpoint фиксирует программу V1–V68, merged GitHub history, локальную повторную проверку архивов и точные байты публичного V67/V68 artifact.

## Решение

1. **V28 остаётся основным growth benchmark.** Его aggregate stress profile: CAGR около 30,17%, Max DD около −25,29%, Sharpe около 1,35. 2026 H1 был положительным, но не является pristine на уровне всей программы.
2. **V10 теперь находится в `main`** как defensive tier: 80% отдельный V8-счёт и 20% отдельный V4-счёт.
3. **V67 — наиболее сильный новый исторический кандидат:** on-chain blend + BTC/ETH spot + небольшой perpetual overlay, target cap 1,15×, observed max gross 1,078×.
4. **V68 подтверждает только modelled intrabar margin path.** Нулевые ликвидации в симуляции не исключают venue, mark-price, gap и custody risk.
5. **Ни один кандидат не `live_ready`.** V65/V67 в 2026 YTD имели нулевую экспозицию, поэтому не прошли ненулевой forward.

## Reproof scope

Перед checkpoint повторно пройдены архивные verifiers V26, V27, V29–V37, V44–V46, V54–V68, V59–V64 и V62–V66. Источники V47–V68 повторно скомпилированы. Для V67/V68 пересчитаны selection-proof hash, metrics, yearly return, concentration и intrabar audit из точного публичного artifact.

Это **не** означает новый полный raw-data rerun каждого V1–V68. Для старых версий уровень доказательства указан в ledger. V11–V17 имеют только legacy summary: канонический artifact в текущем `main` не найден. V61 имеет исходник, но не имеет полного канонического результата.

## Checkpoint parent

```text
repository: balkhaev/fin
parent main: 6f9f18883265a1c95bbdc2caad43de089b1533f5
checkpoint branch: checkpoint/research-v68-2026-07-25
```

---

# Candidate scorecard — checkpoint V68

Все значения — исторические симуляции после указанных costs. Это не прогноз доходности и не разрешение на реальную торговлю.

| Кандидат | Роль | CAGR | Max DD | Sharpe | Turnover | Max gross | Forward status |
|---|---|---:|---:|---:|---:|---:|---|
| V10 | Balanced capital tier | не новый alpha | ~−21,09% stress DD | ~1,208 | separate accounts | без нового leverage | Frozen paper-forward |
| V28 | Primary growth benchmark | 30,17% | −25,29% | 1,35 | 12,76× | 0,939× close, 0,85× target | 2026 H1 positive; program holdout not pristine |
| V52 | Spot on-chain control | 16,64% prefinal | −24,61% | 0,969 | 2,69× | ≤1,0× | 2026 YTD zero exposure |
| V65 | Safe small-perp ensemble | 28,34% prefinal | −24,05% | 1,103 | 5,01× | 1,043× | 2026 YTD zero exposure |
| V67 | Blended on-chain/market candidate | 31,39% prefinal | −25,12% | 1,097 | 5,72× | 1,078× | 2026 YTD zero exposure |
| V68 | Intrabar audit of V67 | 21,61% widened/120bps full | −26,73% | 0,852 | 5,69× | 1,011× harsh | audit only |

## Frozen hierarchy

- Operational benchmark: **V28**.
- Defensive allocation tier: **V10**.
- Small-leverage historical controls: **V65** and **V67**.
- Margin audit: **V68**.
- Real-money leverage remains unauthorised before a nonzero forward window with fill/funding/margin reconciliation.

---

# Методология и доказательный стандарт

## Неподвижные требования

1. Сигнал использует только завершённую информацию и исполняется не раньше следующего доступного open.
2. Старая позиция получает overnight move до ребалансировки.
3. Комиссии, spread/slippage proxy, funding, forced delisting, collateral и cash settlement учитываются явно.
4. Selection proof фиксируется до открытия final.
5. Отрицательные результаты не удаляются; пороги не ослабляются после просмотра.
6. Для derivatives проверяются target/realised gross, initial/maintenance margin, funding multiples и intrabar high/low.
7. Нулевая доходность при нулевой экспозиции не считается forward-подтверждением.

## Уровни доказательства

- **E0 — concept:** только описание.
- **E1 — deterministic:** source hash, compilation и causal self-test.
- **E2 — reproducible historical:** backtest воспроизводится из pinned inputs.
- **E3 — adversarial historical:** costs, latency, leave-one-out, concentration, funding и intrabar audits.
- **E4 — nonzero frozen paper-forward:** после freeze возникла позиция, фактические fills/costs reconciled.
- **E5 — limited live:** малый капитал, kill switches и независимый мониторинг.

На checkpoint V68 V28 и V67 находятся не выше E3. V67 не достиг E4.

## Что означает «передоказано»

- merged Git history и PR coverage сверены;
- доступные archive verifiers повторно выполнены;
- финальные V67/V68 bytes взяты из public Actions artifact с digest `686d2f84d0256b603610030c5fea7b1a75f64aca996291c68cc587ecbdad1683`;
- selection-proof hash V67 пересчитан из JSON без поля hash;
- summary metrics V67 пересчитаны из exact equity curve;
- V68 checks пересчитаны из exact audit metrics;
- modelled liquidations равны нулю, но реальная безопасность не гарантируется.

---

# Verification matrix

| Scope | Проверка | Результат |
|---|---|---|
| GitHub main | merged PR chain V1–V10, V18–V53 | checked; V10 merged during checkpoint |
| V11–V17 | canonical artifact discovery | not found; summary-only evidence |
| V26 | archive verifier + causal self-test | passed |
| V27 | archive verifier | passed |
| V29–V37 | bundle hashes, safe extract, compilation, decisions | passed |
| V44–V46 | bundle hashes, compilation, frozen metrics/provenance | passed |
| V47–V53 | exact artifact source compilation | passed |
| V54–V68 | comprehensive compact archive verifier | passed |
| V59–V64 | archive verifier | passed |
| V62–V66 | package verifier | passed |
| V67 | source self-test | passed |
| V67 | exact proof hash recomputation | passed: `5795fc…` |
| V67 | exact equity → metrics/yearly/concentration recomputation | passed |
| V68 | exact audit metrics → summary checks | passed |
| Public Actions V67–V68 | run `30131328671` | success |
| Forward proof | nonzero 2026 YTD exposure | failed / absent |

## Verification semantics

`passed` means the specific row passed. It does not imply that every historical version was rerun from raw exchange archives in this checkpoint, and it does not imply live safety.

---

# Provenance corrections and unresolved limitations

## V67 selection-proof discrepancy

Текст public compute PR `balkhaev/trader#33` содержал hash `b7f60a…`. Однако опубликованный Actions artifact `8611212699` с подтверждённым digest

```text
686d2f84d0256b603610030c5fea7b1a75f64aca996291c68cc587ecbdad1683
```

содержит другой exact proof file. Его stored и независимо пересчитанный internal hash совпадают:

```text
5795fc62a02e8a8ba423eedc2db4cbbcf6d0028c78e247860bf7fa555b78a6e5
```

SHA-256 самого proof file:

```text
5eeb9a00281f290c2781412d77d530a9dedf35bace5944f86cde50862abb555d
```

Checkpoint использует содержимое exact artifact как source of truth. Старый PR-body hash помечен как stale provenance metadata. Причина расхождения не предполагается и не выдумывается.

## Version-label collisions

В локальной исследовательской истории номера V54, V55, V63 и V64 использовались для более чем одной внутренней линии. Ledger сохраняет их как `54a/54b`, `55a/55b`, `63a/63b`, `64a/64b`, не переписывая исходные названия файлов.

## Incomplete V61

Код V61 сохранён, но полный canonical result artifact не найден. Поэтому V61 имеет статус `implementation incomplete`, а не `rejected`.

## Legacy V11–V17

Сводные решения сохранены, но канонический code/result artifact не был обнаружен в текущем `main` или retained local evidence. Они имеют evidence level `legacy_summary_only`.

---

# Research ledger V1–V17

| V | Гипотеза | Статус | Evidence | Решение |
|---:|---|---|---|---|
| 1 | AIMR 5m momentum/reversion | `rejected` | `canonical_main` | Negative expectancy after realistic costs; momentum excluded. |
| 2 | 4h rotation + shock reversal | `rejected` | `canonical_main` | Rotation signal failed doubled costs; shock reversal rejected. |
| 3 | Daily next-open rotation | `rejected` | `canonical_main` | Corrected overnight execution; holdout did not confirm edge. |
| 4 | Low-turnover trend ensemble | `benchmark` | `canonical_main` | Defensive benchmark with moderate return and low turnover. |
| 5 | Spot/perpetual absolute trend | `rejected` | `canonical_main` | Strong early history but 2025 bridge and 2026 H1 failed. |
| 6 | Cross-sectional spot momentum | `rejected standalone` | `canonical_main` | Retained as a research input, not accepted standalone. |
| 7 | Spot momentum + BTC/ETH hedge | `frozen historical` | `canonical_main` | First directional-plus-hedge candidate; forward unproven. |
| 8 | V7 + BTC/ETH relative trend + ratchet | `frozen benchmark` | `canonical_main` | Growth benchmark lineage; later refined by exact-8h V28. |
| 9 | Cross-sectional perpetuals | `rejected` | `canonical_main` | Severe CAGR negative; drawdown near 56%. |
| 10 | 80% V8 growth / 20% V4 defensive separate accounts | `frozen balanced tier` | `canonical_main` | Merged at checkpoint; defensive allocation tier, not new alpha. |
| 11 | Multi-asset ETF core | `legacy summary only` | `legacy_summary_only` | No independent improvement reported. |
| 12 | ETF sector rotation | `legacy summary only` | `legacy_summary_only` | Reported as failing frozen gates. |
| 13 | Global ETF long/short trend | `legacy summary only` | `legacy_summary_only` | Borrow/cost profile reported insufficient. |
| 14 | ETF relative value | `legacy summary only` | `legacy_summary_only` | Pair families reported as failing. |
| 15 | Frozen-module allocator | `legacy summary only` | `legacy_summary_only` | No robust allocation uplift reported. |
| 16 | Statistical gate | `audit only / legacy summary` | `legacy_summary_only` | HAC/ES/bootstrap gate, not a strategy. |
| 17 | Forward/execution protocol | `protocol / legacy summary` | `legacy_summary_only` | Protocol for data after 2026-06-30. |

---

# Research ledger V18–V37

| V | Гипотеза | Статус | Evidence | Решение |
|---:|---|---|---|---|
| 18 | Beta-hedged alt relative momentum | `rejected` | `canonical_main` | Insufficient uplift versus V8. |
| 19 | Higher initial risk scale | `rejected` | `canonical_main` | Higher CAGR but unacceptable extreme segments. |
| 20 | Regime acceleration | `rejected` | `canonical_main` | 2023 instability and extreme collapse. |
| 21 | Scheduled volatility targeting | `rejected` | `canonical_main` | Worse growth and tails. |
| 22 | Aggressive + defensive sleeve | `rejected near-candidate` | `canonical_main` | Uplift below predeclared threshold. |
| 23 | Drawdown throttle | `rejected` | `canonical_main` | Reduced DD at excessive CAGR cost. |
| 24 | Deep-drawdown circuit | `rejected near-candidate` | `canonical_main` | Failed uplift/tail gates without gate weakening. |
| 25 | V24 with 1.15 gross cap | `rejected` | `canonical_main` | Extra gross did not improve the result. |
| 26 | Execution-aware no-trade state machine | `frozen historical control` | `canonical_main` | Lower turnover and improved historical CAGR. |
| 27 | V26 + conservative cash sleeve | `frozen historical control` | `canonical_main` | Idle cash receives haircut Treasury proxy. |
| 28 | Exact-8h growth stack | `primary frozen benchmark` | `canonical_main` | ~30.17% stress CAGR, ~-25.29% DD; program-level holdout not pristine. |
| 29 | Negative-funding squeeze | `rejected` | `canonical_main` | No standalone edge. |
| 30 | Funding-aware exit hysteresis | `rejected/trivial` | `canonical_main` | Economically indistinguishable from V26. |
| 31 | Spot/perp router | `rejected` | `canonical_main` | No material uplift. |
| 32 | Dynamic BTC/ETH carry | `rejected` | `canonical_main` | Failed heavy costs. |
| 33 | Multi-asset spot/perp carry | `rejected` | `canonical_main` | Basis and costs consumed funding. |
| 34 | Long negative / short positive funding | `rejected` | `canonical_main` | Validation periods negative. |
| 35 | Funding dispersion | `rejected` | `canonical_main` | Low CAGR and negative severe result. |
| 36 | Basis z-score convergence | `rejected` | `canonical_main` | Funding materially below costs. |
| 37 | Regime-conditioned funding | `rejected low-risk effect` | `canonical_main` | Small and cost-sensitive. |

---

# Research ledger V38–V53

| V | Гипотеза | Статус | Evidence | Решение |
|---:|---|---|---|---|
| 38 | 8h session effects | `rejected` | `canonical_main` | High turnover; no durable edge. |
| 39 | 8h breakout | `rejected` | `canonical_main` | Low CAGR; 2024/final negative. |
| 40 | Quote-volume/taker-flow continuation | `rejected` | `canonical_main` | Flow signal did not survive costs. |
| 41 | Liquidity exhaustion/reversal | `rejected` | `canonical_main` | Near-zero result from near-zero exposure. |
| 42 | Open-interest regimes | `rejected` | `canonical_main` | Prefinal and final negative. |
| 43 | Crowding/positioning | `rejected` | `canonical_main` | Practically no trading. |
| 44 | Raw on-chain exchange pressure | `raw effect only` | `canonical_main` | High gross effect but ~37x turnover and ~-49% DD. |
| 45 | Miner/flow regime | `rejected` | `canonical_main` | No standalone edge. |
| 46 | Execution-aware on-chain confirmation | `rejected high-tail` | `canonical_main` | High CAGR but drawdown near 60%. |
| 47 | Risk layer on raw V44 | `rejected` | `canonical_main` | Risk improved but turnover/cost sensitivity remained high. |
| 48 | Risk layer on V46 | `rejected near-miss` | `canonical_main` | Missed immutable -35% DD floor. |
| 49 | Lower-vol V46 budget | `historical candidate` | `canonical_main` | Passed historical gates; final exposure zero. |
| 50 | Vol95 × crash/recovery | `historical candidate downgraded` | `canonical_main` | Good DD but 2020 concentration and zero forward. |
| 51 | Immutable V50 robustness audit | `audit concern` | `canonical_main` | Latency robust; post-2020 weak and best-year concentration high. |
| 52 | 75% exchange pressure + 25% valuation | `historical on-chain candidate` | `canonical_main` | ~16.64% CAGR, ~-24.61% DD; concentration improved; zero forward. |
| 53 | Immutable V52 latency/concentration audit | `audit pass, forward-unproven` | `canonical_main` | All latency variants positive; reverse/final zero exposure. |

---

# Research ledger V54–V68

| V | Гипотеза | Статус | Evidence | Решение |
|---:|---|---|---|---|
| 54a | On-chain BTC/ETH beta rotation | `rejected` | `checkpoint_archive_verified` | Standalone beta rotation rejected. |
| 54b | On-chain overlay on V26 | `historical candidate, zero overlay forward` | `checkpoint_archive_verified` | Historical uplift present but overlay did not activate in final. |
| 55a | Neighbour beta ensemble | `historical candidate` | `checkpoint_archive_verified` | Historical candidate; zero forward. |
| 55b | V54 cash collateral sleeve | `rejected` | `checkpoint_archive_verified` | Failed predeclared selection. |
| 56 | Full neighbour on-chain ensemble | `historical candidate` | `checkpoint_archive_verified` | Passed historical/latency/tail gates; zero forward. |
| 57 | ETH/BTC market-neutral proxy | `rejected` | `checkpoint_archive_verified` | CAGR around 1.6%; insufficient edge. |
| 58 | Consensus convexity | `historical candidate` | `checkpoint_archive_verified` | ~25.1% CAGR, ~-21.1% DD; zero forward. |
| 59 | Consensus boundary audit | `historical candidate` | `checkpoint_archive_verified` | Neighbour/leaveout/bootstrap robust; zero forward. |
| 60 | Irreversible ratchet on V59 | `rejected` | `checkpoint_archive_verified` | Vintage DD gate failed and uplift insufficient. |
| 61 | Cash sleeve on V59 | `implementation incomplete` | `checkpoint_archive_verified` | Source retained; no complete canonical result found. No acceptance/rejection claim. |
| 62 | On-chain spot + small perpetual overlay | `historical leverage candidate` | `checkpoint_archive_verified` | ~29.36% CAGR, ~-25.0% DD; zero forward. |
| 63a | V62 eligible plateau ensemble | `rejected near-candidate` | `checkpoint_archive_verified` | Reduced single-policy risk but failed strict audit. |
| 63b | V26 on-chain futures boost | `rejected` | `checkpoint_archive_verified` | Failed predeclared checks. |
| 64a | Funding-aware small-leverage plateau | `rejected` | `checkpoint_archive_verified` | Funding-aware plateau missed thresholds. |
| 64b | V52 spot/perpetual overlay | `rejected near-candidate` | `checkpoint_archive_verified` | Strong return but missed immutable Sharpe/DD gates. |
| 65 | Safe-cap spot/perpetual ensemble | `frozen historical candidate` | `checkpoint_archive_verified` | ~28.34% CAGR, ~-24.05% DD, max gross ~1.043x; zero forward. |
| 66 | Adversarial intrabar audit of V65 | `audit pass, forward-unproven` | `checkpoint_archive_verified` | No modelled liquidations; positive buffer. |
| 67 | Blended V52/V62 spot + small perpetual | `latest historical candidate` | `checkpoint_archive_verified` | ~31.39% prefinal CAGR, ~-25.12% DD, max gross ~1.078x; zero forward. |
| 68 | Adversarial intrabar audit of V67 | `audit pass, forward-unproven` | `checkpoint_archive_verified` | No modelled liquidations; widened/high-cost scenarios positive. |
