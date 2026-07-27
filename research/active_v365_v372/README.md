# Active V365–V372 — exact downside-volatility compression ensemble

This is a single preregistered follow-up to V341, not a new parameter grid.

Frozen component policies:

1. `low_downside_vol_ratio_l14_k3_r7_dollar`
2. `low_downside_vol_ratio_l60_k3_r7_beta`

The portfolio is the static 50/50 average of their target-weight books. No neighbouring policies, ensemble weights, gross scales, rebalance intervals or gates are tested.

Development 2021–2023 is replayed first. Validation 2024, holdout 2025 and final 2026 H1 are opened once only if every original development gate passes. Program-level OOS is explicitly non-pristine; a full historical pass could authorize only paper-forward monitoring after 27 July 2026.
