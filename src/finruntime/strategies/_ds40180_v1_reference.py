"""Read-only wrapper around the byte-pinned DS-40/180 v1 engine.

The implementation in ``_ds40180_v1_frozen_engine`` is the exact blob from
commit cb942798acdd0f27867b923476dc9b50eb67984f. This wrapper changes neither
that source nor the active v2 module: it evaluates the frozen function with the
legacy 1.25x gross and 30% per-asset paper limits in an isolated globals map.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any

from . import _ds40180_v1_frozen_engine as _frozen
from ._ds40180_signals import _apply_target_safety

V1_REFERENCE_STRATEGY_ID = "ds40180_t50c3_okx_paper_v1_reference"
V1_REFERENCE_VERSION = "okx-paper-v1"
V1_REFERENCE_PROFILE = "DS-40/180 T50-C3 v1 frozen reference"
V1_REFERENCE_SOURCE_COMMIT = "cb942798acdd0f27867b923476dc9b50eb67984f"
V1_REFERENCE_SOURCE_BLOB = "dd573280ddec0e2ae50e33941d4f0154525d4809"
V1_PAPER_GROSS_CAP = 1.25
V1_PAPER_ASSET_CAP = 0.30


def _legacy_target_safety(row: list[float]) -> tuple[list[float], bool]:
    return _apply_target_safety(
        row,
        gross_cap=V1_PAPER_GROSS_CAP,
        asset_cap=V1_PAPER_ASSET_CAP,
    )


def build_v1_reference_engine(
    histories: list[dict[str, Any]], failed_assets: list[dict[str, str]]
) -> dict[str, Any]:
    """Evaluate the pinned v1 function without mutating module globals."""

    isolated_globals = dict(_frozen.build_engine.__globals__)
    isolated_globals["_apply_target_safety"] = _legacy_target_safety
    build = FunctionType(
        _frozen.build_engine.__code__,
        isolated_globals,
        name="build_v1_reference_engine",
        argdefs=_frozen.build_engine.__defaults__,
        closure=_frozen.build_engine.__closure__,
    )
    result = build(histories, failed_assets)
    result["reference"] = {
        "strategyId": V1_REFERENCE_STRATEGY_ID,
        "strategyVersion": V1_REFERENCE_VERSION,
        "profile": V1_REFERENCE_PROFILE,
        "sourceCommit": V1_REFERENCE_SOURCE_COMMIT,
        "sourceBlob": V1_REFERENCE_SOURCE_BLOB,
        "paperGrossCap": V1_PAPER_GROSS_CAP,
        "paperAssetCap": V1_PAPER_ASSET_CAP,
        "readOnly": True,
    }
    return result
