from __future__ import annotations

from finruntime.canonical import require_sha256
from finruntime.portfolio.accounting import PaperAccountState


def activate_paper_plan(
    state: PaperAccountState,
    *,
    plan_id: str,
    as_of_utc: str,
) -> PaperAccountState:
    """Open a new immutable paper-plan context before applying its fill events."""

    state.validate()
    plan_id = require_sha256(plan_id, field="plan_id")
    if state.last_plan_id == plan_id:
        return state
    return PaperAccountState.create(
        strategy_id=state.strategy_id,
        sequence=state.sequence + 1,
        as_of_utc=as_of_utc,
        cash=state.cash,
        spot_positions=state.spot_positions,
        perp_positions=state.perp_positions,
        perp_entry_prices=state.perp_entry_prices,
        fees_paid=state.fees_paid,
        realized_pnl=state.realized_pnl,
        funding_pnl=state.funding_pnl,
        equity=state.equity,
        high_water=state.high_water,
        last_plan_id=plan_id,
        applied_event_ids=state.applied_event_ids,
    )
