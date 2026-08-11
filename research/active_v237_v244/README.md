# Active V237–V244 — funding-settlement premium compression

This cycle tests whether an extreme, completed USD-M premium contains causal price information specifically around the scheduled funding boundary. It is not an unconditional 8h session rule and does not alter any previously rejected funding grid.

- V237: archive/schema coverage gate.
- V238: normalized premium/mark/index/funding panel.
- V239: frozen 144-policy development ranking.
- V240: cost and +5m latency audits.
- V241: guarded 2024 validation.
- V242: guarded 2025 holdout.
- V243: guarded 2026 H1 final.
- V244: frozen decision and forward protocol.

No P&L is permitted until premium, mark, index, trade-price and funding archives pass the immutable data gate.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
