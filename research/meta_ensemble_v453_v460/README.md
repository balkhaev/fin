# V453–V460 — causal state-aware meta-ensemble

This cycle tests a small frozen set of causal capital allocators across two independently
constructed strategy streams:

- `V285_LOW_SKEW_HOURLY_CONTROLLER`;
- `V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE`;
- cash.

The state-aware policies may use only the frozen V413 market-state row available for the
current decision and strategy returns strictly before the current day. Selection uses
2021–2023 with internal walk-forward folds for 2022 and 2023. 2024, 2025 and 2026 H1
are opened only after the selection proof is written.

This is not a pristine program-level holdout: the component families and their later
outcomes were known before this cycle. Any passing result is exploratory and cannot
authorize capital, live execution or real leverage.
