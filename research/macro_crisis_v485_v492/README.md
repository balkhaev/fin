# V485–V492 — independent macro/crisis replay

This cycle re-runs the existing V95 crisis-alpha engine as a **standalone** return source on newly materialized daily ETF/FX proxy data.

The purpose is to test whether an economically independent macro sleeve can add return. It does not alter V75, V136, V28, V285 or V365.

## Frozen chronology

- parameter selection: 2008–2020 only;
- bridge: 2021–2023;
- holdout: 2024–2025;
- final: January–June 2026;
- no post-2020 observations participate in parameter selection.

## Data and evidence boundary

ETF and FX histories are downloaded and snapshotted by the workflow. They are adjusted-close/reference-price research proxies, not execution-grade futures chains with bid/ask, rolls, swaps, borrow availability or broker margin.

The ATLAS input passed to the legacy runner is deliberately flat and is used only to satisfy its interface. V485 evaluates the standalone V95 output; any legacy ATLAS blend output is ignored.

## Safety

```text
integration_permitted      false
live_ready                 false
real_leverage_authorized   false
profitability_proven       false
capital_change_authorized  false
```
