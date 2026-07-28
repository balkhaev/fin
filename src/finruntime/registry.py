from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from .canonical import ContractError


@dataclass(frozen=True, slots=True)
class FrozenStrategy:
    strategy_id: str
    role: str
    allowed_modes: tuple[str, ...]
    live_ready: bool
    real_leverage_authorized: bool
    parameters: Mapping[str, object]


STRATEGIES: dict[str, FrozenStrategy] = {
    "v75_atlas_nx": FrozenStrategy(
        strategy_id="v75_atlas_nx",
        role="primary",
        allowed_modes=("paper", "shadow"),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "profile": "frozen V75 ATLAS-NX",
            "source_checkpoint": "V138",
        },
    ),
    "v28_growth_control": FrozenStrategy(
        strategy_id="v28_growth_control",
        role="control",
        allowed_modes=("paper", "shadow"),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "profile": "ACTIVE_V28_EXACT8H_BREAKOUT_CARRY_CASH",
            "target_gross_cap": 0.85,
        },
    ),
    "v136_execution_shadow": FrozenStrategy(
        strategy_id="v136_execution_shadow",
        role="shadow",
        allowed_modes=("shadow",),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "l1_no_trade_band": 0.08,
            "maximum_target_age_days": 28,
            "step_fraction": 1.0,
            "risk_reduction_buffer": 0.02,
        },
    ),
    "v517_tristate_guard_shadow": FrozenStrategy(
        strategy_id="v517_tristate_guard_shadow",
        role="shadow",
        allowed_modes=("shadow",),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "high_leverage": 2.075,
            "base_leverage": 0.97,
            "low_leverage": 0.60,
            "rebalance_days": 10,
            "no_trade_band": 0.04,
            "minimum_state_hold_days": 14,
            "guard_enter_drawdown": -0.245,
            "guard_exit_drawdown": -0.18,
            "guard_cap": 1.0,
            "source_checkpoint": "V524",
            "historical_target_non_pristine": True,
        },
    ),
}


def registry_payload() -> dict[str, object]:
    return {
        "registry_version": "runtime-v1",
        "live_execution_available": False,
        "strategies": {
            strategy_id: asdict(profile)
            for strategy_id, profile in STRATEGIES.items()
        },
    }


def get_strategy(strategy_id: str) -> FrozenStrategy:
    try:
        return STRATEGIES[strategy_id]
    except KeyError as exc:
        raise ContractError(f"unknown frozen strategy: {strategy_id}") from exc


def assert_mode(strategy_id: str, mode: str) -> None:
    if mode == "live":
        raise ContractError("live mode is not available")
    profile = get_strategy(strategy_id)
    if mode not in profile.allowed_modes:
        raise ContractError(
            f"mode {mode!r} is not allowed for strategy {strategy_id!r}"
        )
