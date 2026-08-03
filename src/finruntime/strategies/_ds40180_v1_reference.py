"""Read-only wrapper around the byte-pinned DS-40/180 v1 source closure.

The modules under ``_ds40180_v1`` are exact Git blobs from commit
cb942798acdd0f27867b923476dc9b50eb67984f. The wrapper only adds identity and
provenance metadata; it does not alter the frozen calculation.
"""

from __future__ import annotations

from typing import Any

from ._ds40180_v1._ds40180_engine import build_engine as _build_frozen_engine

V1_REFERENCE_STRATEGY_ID = "ds40180_t50c3_okx_paper_v1_reference"
V1_REFERENCE_VERSION = "okx-paper-v1"
V1_REFERENCE_PROFILE = "DS-40/180 T50-C3 v1 frozen reference"
V1_REFERENCE_SOURCE_COMMIT = "cb942798acdd0f27867b923476dc9b50eb67984f"
V1_REFERENCE_SOURCE_BLOBS = {
    "common": "3e0c9c43f92de98620a3819e162e086766587f36",
    "signals": "018034baf84e1268c827213b597ba8fda8fb581b",
    "engine": "dd573280ddec0e2ae50e33941d4f0154525d4809",
    "account": "04430d038f11aa4b57efb5f40d694b1cf8987269",
}
V1_PAPER_GROSS_CAP = 1.25
V1_PAPER_ASSET_CAP = 0.30


def build_v1_reference_engine(
    histories: list[dict[str, Any]], failed_assets: list[dict[str, str]]
) -> dict[str, Any]:
    """Evaluate the frozen v1 engine and attach non-trading metadata."""

    result = _build_frozen_engine(histories, failed_assets)
    result["reference"] = {
        "strategyId": V1_REFERENCE_STRATEGY_ID,
        "strategyVersion": V1_REFERENCE_VERSION,
        "profile": V1_REFERENCE_PROFILE,
        "sourceCommit": V1_REFERENCE_SOURCE_COMMIT,
        "sourceBlobs": dict(V1_REFERENCE_SOURCE_BLOBS),
        "paperGrossCap": V1_PAPER_GROSS_CAP,
        "paperAssetCap": V1_PAPER_ASSET_CAP,
        "readOnly": True,
    }
    return result
