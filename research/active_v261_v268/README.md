# Active V261–V268 — fixed-universe liquidity quality factor

This cycle tests whether cross-sectional **liquidity quality** earns an independent Binance USD-M perpetual return after realistic funding, costs and delisting treatment. It is separate from V253 low-risk: no volatility, downside-beta or tail-risk score is used for ranking.

- **V261** — reuse the checksum-aware fixed January-2021 V9 universe and immutable availability rules.
- **V262** — causal quote-liquidity, Amihud-impact and liquidity-stability features.
- **V263** — frozen 144-policy development ranking.
- **V264** — funding, costs, gross, forced-delisting and one-day latency audits.
- **V265** — guarded 2024 validation.
- **V266** — guarded 2025 holdout.
- **V267** — guarded 2026 H1 final and standalone decision.
- **V268** — checkpoint and paper-forward protocol.

The universe is fixed to BTC, ETH, BNB, XRP, ADA, LTC, BCH, EOS, DOGE, LINK, DOT, TRX and SOL. Delisted and weak assets remain; no survivor replacement is permitted.

A historical pass remains paper-forward research only. Live trading and real leverage are disabled.
