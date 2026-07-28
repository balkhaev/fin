# V501–V508 — controlled-growth crypto engine

Цель цикла — проверить, можно ли приблизиться к 50% CAGR без ухудшения Max DD ниже −25%, ликвидаций и разрушения результата в severe/extreme audits.

Гипотеза отличается от прошлых market-neutral исследований: short baskets систематически теряли деньги, поэтому тестируется заранее объявленный long/cash engine на fixed January-2021 Binance USD-M universe. Он объединяет только причинные cross-sectional признаки: downside beta, idiosyncratic skewness, downside-volatility compression, market correlation, residual resilience и medium-term momentum.

Market State V413 используется только как непрерывный риск-бюджет по осям trend/breadth/stress/rotation/liquidity/leverage. Дискретных правил `state label -> trade` нет. Все сигналы строятся по завершённому дню и исполняются не раньше следующего open.

Selection: 2021–2023. Validation 2024, holdout 2025 и final 2026 H1 открываются только после записи immutable proof. История семейства уже известна, поэтому итог является exploratory/non-pristine evidence и сам по себе не разрешает капитал, live trading или реальное плечо.