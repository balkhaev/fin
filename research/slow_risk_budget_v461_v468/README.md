# V461–V468 — slow market-risk budget

V453 showed that dynamic switching between V285 and V365 underperformed a stable mix and generated excessive meta-turnover. V461 therefore freezes the strongest development mix at 40% V285 / 60% V365 and changes only the total risky budget.

The overlay uses the six frozen V413 market axes, assignment confidence, novelty and regime duration. It is continuous, smoothed and rebalanced no faster than every 28 or 56 days. It does not map a state label directly to a trade and does not alter either underlying strategy.

Selection uses only 2021–2023 with walk-forward 2022 and 2023 folds. 2024, 2025 and 2026 H1 remain closed until the selection proof is written. The program is exploratory because the component OOS history was already known before V461.
