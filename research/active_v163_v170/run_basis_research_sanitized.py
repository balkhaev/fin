#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import basis_config  # noqa: E402
import run_basis_research as core  # noqa: E402

ORIGINAL_LOAD_ALL = core.load_all


def sanitized_load_all(cache: Path, output: Path):
    markets, provenance = ORIGINAL_LOAD_ALL(cache, output)
    for asset, frame in markets.items():
        if frame.empty:
            provenance["assets"][asset]["spread_sanity"] = {
                "max_abs_spread_bps": basis_config.MAX_ABS_SPREAD_BPS,
                "rows_before": 0,
                "rows_after": 0,
                "rows_removed": 0,
            }
            continue
        close_spread = np.abs(
            np.log(frame["close_okx"].to_numpy(float) / frame["close_binance"].to_numpy(float))
        ) * 10_000.0
        open_spread = np.abs(
            np.log(frame["open_okx"].to_numpy(float) / frame["open_binance"].to_numpy(float))
        ) * 10_000.0
        valid = (
            np.isfinite(close_spread)
            & np.isfinite(open_spread)
            & (close_spread <= basis_config.MAX_ABS_SPREAD_BPS)
            & (open_spread <= basis_config.MAX_ABS_SPREAD_BPS)
        )
        before = len(frame)
        filtered = frame.loc[valid].copy().reset_index(drop=True)
        markets[asset] = filtered
        filtered.to_csv(output / f"{asset}_hourly.csv", index=False)
        provenance["assets"][asset]["aligned_rows_before_sanity"] = before
        provenance["assets"][asset]["aligned_rows"] = len(filtered)
        provenance["assets"][asset]["timestamp_min"] = (
            filtered.timestamp.min().isoformat() if not filtered.empty else None
        )
        provenance["assets"][asset]["timestamp_max"] = (
            filtered.timestamp.max().isoformat() if not filtered.empty else None
        )
        provenance["assets"][asset]["spread_sanity"] = {
            "max_abs_spread_bps": basis_config.MAX_ABS_SPREAD_BPS,
            "rows_before": before,
            "rows_after": len(filtered),
            "rows_removed": before - len(filtered),
            "max_observed_close_spread_bps": float(np.nanmax(close_spread)),
            "max_observed_open_spread_bps": float(np.nanmax(open_spread)),
        }
    (output.parent / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    return markets, provenance


core.load_all = sanitized_load_all


if __name__ == "__main__":
    raise SystemExit(core.main())
