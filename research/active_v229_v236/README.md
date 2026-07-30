# Active V229–V236 — USD-M perpetual/quarterly calendar spread

This cycle tests a same-venue, same-USDT-collateral relative-value family that was not covered by the prior spot/delivery basis rejection.

- **V229** — deterministic USD-M quarterly archive/schema coverage.
- **V230** — front-contract calendar and expiry-safe panel.
- **V231** — frozen basis/funding/curve policy grid.
- **V232** — costs, funding, latency and forced-roll audit.
- **V233** — guarded 2024 validation.
- **V234** — guarded 2025 holdout.
- **V235** — guarded 2026 H1 final and integration gate.
- **V236** — checkpoint and forward protocol.

COIN-M contracts are not an after-the-fact substitute if USD-M quarterly coverage fails.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
