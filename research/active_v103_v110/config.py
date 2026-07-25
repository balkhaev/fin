from __future__ import annotations

SECTOR_ETFS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
COUNTRY_ETFS = ("EWA", "EWC", "EWG", "EWH", "EWJ", "EWS", "EWT", "EWU", "EWW", "EWY", "EWZ")
DEFENSIVE_ETFS = ("IEF", "TLT", "SHY", "GLD", "DBC", "UUP")
UNIVERSE = SECTOR_ETFS + COUNTRY_ETFS + DEFENSIVE_ETFS
GROUPS = {
    **{ticker: "sector" for ticker in SECTOR_ETFS},
    **{ticker: "country" for ticker in COUNTRY_ETFS},
    **{ticker: "defensive" for ticker in DEFENSIVE_ETFS},
}
PERIODS = {
    "development": ("2007-01-01", "2014-01-01"),
    "validation_a": ("2014-01-01", "2018-01-01"),
    "validation_b": ("2018-01-01", "2021-01-01"),
    "bridge": ("2021-01-01", "2024-01-01"),
    "holdout": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "selection": ("2007-01-01", "2021-01-01"),
    "prefinal": ("2007-01-01", "2026-01-01"),
    "full": ("2007-01-01", "2026-07-01"),
}
SELECTION_PERIODS = ("development", "validation_a", "validation_b")
COSTS = {"stress": 10.0, "severe": 25.0, "extreme": 50.0}
REBALANCE = (5, 10, 20)
TARGET_VOL = (0.12, 0.15, 0.18)
GROSS_CAP = (1.00, 1.15, 1.25)
SELECTION_GATES = {
    "cagr": 0.06,
    "sharpe": 0.60,
    "dd": -0.25,
    "turnover": 15.0,
    "worst_period": -0.05,
    "severe_worst": -0.12,
}
PROMOTION_GATES = {
    "cagr": 0.07,
    "sharpe": 0.70,
    "dd": -0.23,
    "post2020": 0.05,
    "best_year_share": 0.45,
    "rolling252": -0.18,
}
BLEND_WEIGHTS = (0.10, 0.20, 0.30, 0.40)
