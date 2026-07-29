from __future__ import annotations

from typing import Mapping, Sequence

from finruntime.execution.paper_broker import (
    DEFAULT_PAPER_BROKER_POLICY,
    PaperBrokerPolicy,
    PaperExecutionResult,
    PaperQuote,
    execute_paper_plan,
)
from finruntime.models import ExecutionPlan
from finruntime.portfolio.accounting import AccountingHalt, PaperAccountState
from finruntime.portfolio.lifecycle import activate_paper_plan
from finruntime.portfolio.risk import ReferencePriceBook


def execute_paper_cycle(
    *,
    plan: ExecutionPlan,
    account_state: PaperAccountState,
    quotes: Sequence[PaperQuote] | Mapping[tuple[str, str], PaperQuote],
    mark_prices: ReferencePriceBook,
    policy: PaperBrokerPolicy = DEFAULT_PAPER_BROKER_POLICY,
) -> PaperExecutionResult:
    """Activate and execute one immutable paper plan exactly once.

    A partially or fully executed plan must be replanned from the resulting account state.
    Reusing the same plan against later quotes could otherwise overfill its original intents.
    """

    plan.validate()
    account_state.validate()
    if account_state.last_plan_id == plan.plan_id:
        raise AccountingHalt(
            "paper plan was already activated; build a new plan from current account state"
        )

    active = activate_paper_plan(
        account_state,
        plan_id=plan.plan_id,
        as_of_utc=plan.created_at_utc,
    )
    return execute_paper_plan(
        plan=plan,
        account_state=active,
        quotes=quotes,
        mark_prices=mark_prices,
        policy=policy,
    )
