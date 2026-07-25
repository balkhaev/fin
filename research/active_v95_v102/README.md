# Active V95–V102 — Global crisis-alpha and ATLAS integration

This cycle tests a standalone long/short global macro proxy sleeve using the already materialized V88 ETF and FX prices in `balkhaev/fin`.

## Versions

- **V95** — time-series momentum;
- **V96** — moving-average trend;
- **V97** — breakout and cross-sectional relative momentum;
- **V98** — crisis-defensive macro overlay;
- **V99** — frozen neighboring-family ensemble selected only through 2020;
- **V100** — post-2020 standalone validation and cost stress;
- **V101** — separate-account integration with ATLAS, attempted only after standalone gates pass;
- **V102** — final concentration, latency and leverage decision.

Signals use completed daily closes and affect returns no earlier than the next observation. ETF shorts include borrow cost, gross exposure above one includes financing drag, and every selection/promotion gate is written in source before final evaluation.

The research is a historical proxy simulation. It does not authorize live trading or leverage.
