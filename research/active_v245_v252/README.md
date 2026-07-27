# Active V245–V252 — USD-M / COIN-M dual perpetual

This cycle tests same-asset market segmentation between Binance linear USD-M and inverse COIN-M perpetuals. It removes the expensive spot leg and expiry roll, while retaining actual funding on both legs.

- V245: checksum-verified dual-perpetual panel.
- V246: causal basis and funding forecasts.
- V247: frozen 144-policy development ranking.
- V248: cost and latency audits.
- V249: guarded 2024 validation.
- V250: guarded 2025 holdout.
- V251: guarded 2026 H1 final.
- V252: frozen decision and collateral-forward protocol.

Equal base notional does not permit cross-wallet collateral netting. Even a historical pass remains blocked from integration until a separate leg-level margin audit.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
