from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

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
    "btc_opportunity_paper_v1": FrozenStrategy(
        strategy_id="btc_opportunity_paper_v1",
        role="experimental_opportunity_controller",
        allowed_modes=("paper", "shadow"),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "profile": "BTC shared-account causal opportunity controller v1",
            "spot_only": True,
            "entry_notional_cap": 0.25,
            "per_trade_risk_fraction": 0.0025,
            "qualification_required_by_default": True,
            "historical_metrics_inherited": False,
            "forward_clock_reset": True,
            "exchange_submission_available": False,
        },
    ),
    "ds40180_t50c3_okx_paper": FrozenStrategy(
        strategy_id="ds40180_t50c3_okx_paper",
        role="turbo_paper",
        allowed_modes=("paper", "shadow"),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "strategy_version": "okx-paper-v2",
            "profile": "DS-40/180 T50-C3 v2",
            "venue": "okx",
            "instrument_type": "SWAP",
            "target_volatility": 0.50,
            "risk_scale_floor": 1.0,
            "risk_scale_cap": 3.0,
            "paper_gross_cap": 1.50,
            "paper_gross_cap_stress": 0.75,
            "paper_gross_cap_base": 1.25,
            "paper_gross_cap_calm": 1.50,
            "paper_asset_cap": 0.25,
            "funding_aware_sizing": True,
            "covariance_stress_control": True,
            "crisis_4h_gross_cap": 0.15,
            "no_trade_min_band": 0.005,
            "persistent_append_only_ledger": True,
            "forward_clock_reset": True,
            "historical_metrics_inherited": False,
            "exchange_submission_available": False,
        },
    ),
    "atlas_nx_r1": FrozenStrategy(
        strategy_id="atlas_nx_r1",
        role="primary_reconstruction",
        allowed_modes=("paper", "shadow"),
        live_ready=False,
        real_leverage_authorized=False,
        parameters={
            "profile": "Atlas NX R1 reconstruction",
            "predecessor_strategy_id": "v75_atlas_nx",
            "forward_clock_reset": True,
            "historical_metrics_inherited": False,
        },
    ),
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
            strategy_id: asdict(profile) for strategy_id, profile in STRATEGIES.items()
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
