from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from flow_config import Audit, Policy

PARENT = Path(__file__).resolve().parents[1] / "active_v197_v204"
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))
import engine as parent_engine  # noqa: E402

calendar_returns = parent_engine.calendar_returns
concentration_metrics = parent_engine.concentration_metrics
metrics = parent_engine.metrics
period_metrics = parent_engine.period_metrics
slice_account = parent_engine.slice_account
slice_trades = parent_engine.slice_trades
trade_bootstrap = parent_engine.trade_bootstrap


def _persistent(base: pd.Series, side: pd.Series, observations: int) -> pd.Series:
    valid = base.fillna(False).astype(bool)
    if observations <= 1:
        return valid
    for offset in range(1, observations):
        valid &= base.shift(offset, fill_value=False)
        valid &= side.eq(side.shift(offset))
    return valid.fillna(False)


def policy_signal(panel: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    index = panel.index
    direction = pd.Series(0, index=index, dtype=np.int8)
    score = pd.Series(np.nan, index=index, dtype=float)
    quality = panel.quality.fillna(False).astype(bool)
    flow_z = pd.to_numeric(panel.flow_z, errors="coerce")
    pressure_z = pd.to_numeric(panel.pressure_z, errors="coerce")
    flow_side = np.sign(flow_z).fillna(0).astype(np.int8)

    if policy.family in {"agreement_continuation", "reversed_agreement_control"}:
        base = (
            quality
            & flow_z.abs().ge(policy.flow_threshold)
            & pressure_z.abs().ge(policy.pressure_threshold)
            & flow_side.ne(0)
            & np.sign(flow_z).eq(np.sign(pressure_z))
        )
        valid = _persistent(base, flow_side, policy.persistence)
        direction.loc[valid] = flow_side.loc[valid]
        if policy.family == "reversed_agreement_control":
            direction.loc[valid] *= -1
        score.loc[valid] = flow_z.loc[valid].abs() + pressure_z.loc[valid].abs()

    elif policy.family == "flow_vacuum_continuation":
        depth_z = pd.to_numeric(panel.depth_z, errors="coerce")
        valid = (
            quality
            & flow_z.abs().ge(policy.flow_threshold)
            & pressure_z.abs().ge(policy.pressure_threshold)
            & depth_z.le(policy.depth_threshold)
            & flow_side.ne(0)
            & np.sign(flow_z).eq(np.sign(pressure_z))
        )
        direction.loc[valid] = flow_side.loc[valid]
        score.loc[valid] = (
            flow_z.loc[valid].abs()
            + pressure_z.loc[valid].abs()
            + (-depth_z.loc[valid]).clip(lower=0)
        )

    elif policy.family == "absorption_reversal":
        impact = pd.to_numeric(panel.impact_bps, errors="coerce")
        valid = (
            quality
            & flow_z.abs().ge(policy.flow_threshold)
            & pressure_z.abs().ge(policy.pressure_threshold)
            & impact.le(policy.max_impact_bps)
            & flow_side.ne(0)
            & np.sign(flow_z).eq(-np.sign(pressure_z))
        )
        direction.loc[valid] = -flow_side.loc[valid]
        score.loc[valid] = (
            flow_z.loc[valid].abs()
            + pressure_z.loc[valid].abs()
            + (policy.max_impact_bps - impact.loc[valid]).clip(lower=0) / 3.0
        )

    elif policy.family == "flow_exhaustion_reversal":
        impact = pd.to_numeric(panel.impact_bps, errors="coerce")
        volume_z = pd.to_numeric(panel.volume_z, errors="coerce")
        valid = (
            quality
            & flow_z.abs().ge(policy.flow_threshold)
            & volume_z.ge(policy.volume_threshold)
            & impact.le(policy.max_impact_bps)
            & flow_side.ne(0)
        )
        direction.loc[valid] = -flow_side.loc[valid]
        score.loc[valid] = (
            flow_z.loc[valid].abs()
            + volume_z.loc[valid].clip(lower=0)
            + (policy.max_impact_bps - impact.loc[valid]).clip(lower=0) / 3.0
        )
    else:
        raise ValueError(f"unknown flow-depth family: {policy.family}")

    return pd.DataFrame({"direction": direction, "score": score}, index=index)


def simulate(
    panels: dict[str, pd.DataFrame],
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    previous_signal = parent_engine.policy_signal
    parent_engine.policy_signal = policy_signal
    try:
        return parent_engine.simulate(panels, policy, audit)
    finally:
        parent_engine.policy_signal = previous_signal


def policy_dict(policy: Policy) -> dict[str, object]:
    return asdict(policy) | {"name": policy.name}
