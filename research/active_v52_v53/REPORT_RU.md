# Active V52–V53: диверсифицированный on-chain процесс

## V52

В V52 не менялись V46 execution и V50 risk layer. Проверялись только восемь заранее заданных смесей четырёх существующих on-chain семейств V44.

Победитель:

```text
75% exchange_pressure
25% valuation_cycle
→ V46 confirmation
→ V50 vol95_x_recovery
```

Prefinal 2019–2025 при 40 б.п. на сторону:

- CAGR: **16,64%**;
- total return: **+193,81%**;
- Sharpe: **0,969**;
- Max DD: **−24,61%**;
- turnover: **2,69×/год**;
- average gross: 23,65%;
- post-2020 CAGR: **7,78%**;
- доля лучшего положительного года в log-growth: **54,24%**;
- worst leave-one-year-out CAGR: **7,78%**;
- все stress-сегменты положительны.

Полный CAGR при разных расходах:

- 40 б.п.: **15,69%**;
- 80 б.п.: **14,52%**;
- 120 б.п.: **13,36%**;
- 160 б.п.: **12,21%**;
- 200 б.п.: **11,07%**.

V52 существенно лучше V50: результат меньше зависит от 2020 года, post-2020 CAGR вырос примерно с 4,6% до 7,8%, а историческая просадка осталась около 25%.

## V53

V53 не выбирал параметры. Он проверил exact V52 при дополнительной задержке публикации on-chain данных 0/1/3/5 дней и исполнения 0/1/2 дня.

Все 12 комбинаций сохранили положительный prefinal CAGR:

- publication-lag CAGR floor: **14,83%**;
- execution-delay CAGR floor: **16,20%**;
- exact post-2020 CAGR: **7,75%**;
- best-year log share: **54,24%**;
- worst leave-one-year-out CAGR: **5,96%**.

Проверки концентрации и latency пройдены. Однако:

- reverse 2017–2018: нулевая экспозиция;
- 2026 YTD: нулевая экспозиция;
- последний ненулевой target в полной симуляции V52 приходился на 4 ноября 2025 года.

Поэтому нулевой результат 2026 года не является подтверждением edge.

## Решение

```text
V52 status: historical_risk_candidate_needs_nonzero_forward
V53 status: historically_robust_but_forward_unproven
```

V52 замораживается для первого ненулевого forward-окна. Он не заменяет V28 и не разрешён для live-разгона. Следующая допустимая проверка — только forward execution или независимое сравнение как отдельного малого sleeve без повторного выбора параметров.

## Provenance

V52:

- public PR: `balkhaev/trader#31`;
- workflow: `30124022540`;
- artifact: `8608583166`;
- digest: `sha256:b6c74d40f145ea1d5ebbe427626bf3211985e16cd80d1c4f5d7457d2753d593b`;
- selection proof: `4cb6808336fb7951276b71a13e08dcd8fb4cd4c5c449de5fcbda8413889bcee1`;
- exact source SHA-256: `1aad138e8f4fa59262611555c4bd0be62c55c2c5ef8ee11eba8ed87dd60ead81`.

V53:

- workflow: `30124711240`;
- artifact: `8608815933`;
- digest: `sha256:c11a798e9a939bf3080a18b683cb5b833f4db04a91319de8242983f19d4557c9`;
- exact source SHA-256: `b763d3f0aa92bc29bc660107f8cd45a79c2056dc6d56fdfd06109bd86d6cb83b`.
