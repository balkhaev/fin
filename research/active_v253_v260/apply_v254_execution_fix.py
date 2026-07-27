#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parent / "run_research.py"
source = path.read_text()

old = '''    "annual_turnover_max": 20.0,
    "all_development_years_positive": True,
'''
new = '''    "annual_turnover_max": 20.0,
    "max_realized_gross": MAX_REALIZED_GROSS,
    "all_development_years_positive": True,
'''
if old not in source:
    raise SystemExit("development gate insertion point not found")
source = source.replace(old, new, 1)

old = '''    ever_traded: set[str] = set()
    forced_exit_count = 0

    def allocate(value: float, symbol_index: int, side: float) -> None:
'''
new = '''    ever_traded: set[str] = set()
    forced_exit_count = 0
    last_signal_target: np.ndarray | None = None

    def allocate(value: float, symbol_index: int, side: float) -> None:
'''
if old not in source:
    raise SystemExit("last target insertion point not found")
source = source.replace(old, new, 1)

old = '''        signal_index = i - 1 - audit.execution_delay_days
        target = (
            target_values[signal_index].copy()
            if signal_index >= 0
            else np.zeros(symbol_count, dtype=float)
        )
        target[~available[i]] = 0.0
        gross_target = float(np.abs(target).sum())
        if gross_target > TARGET_GROSS:
            target *= TARGET_GROSS / gross_target
        equity_before_trade = max(equity, 1e-12)
        desired = target * equity_before_trade
        delta = desired - notional
        turnover_notional = float(np.abs(delta).sum())
        turnover = turnover_notional / equity_before_trade
        if turnover_notional > 0:
            cost_by_asset = np.abs(delta) * audit.cost_rate
            for j in np.flatnonzero(cost_by_asset > 0):
                side = float(np.sign(desired[j])) if abs(desired[j]) > 1e-12 else float(np.sign(notional[j]))
                allocate(-float(cost_by_asset[j]), j, side)
            trade_cost = float(cost_by_asset.sum())
            equity -= trade_cost
            day_cost += trade_cost
            notional = target * max(equity, 0.0)
            for j in np.flatnonzero(np.abs(notional) > 1e-12):
                ever_traded.add(market.symbols[j])
        rebalance_event = int(turnover > 1e-4)
'''
new = '''        signal_index = i - 1 - audit.execution_delay_days
        target = (
            target_values[signal_index].copy()
            if signal_index >= 0
            else np.zeros(symbol_count, dtype=float)
        )
        target[~available[i]] = 0.0
        gross_target = float(np.abs(target).sum())
        if gross_target > TARGET_GROSS:
            target *= TARGET_GROSS / gross_target
        equity_before_trade = max(equity, 1e-12)
        target_changed = bool(
            last_signal_target is None
            or not np.allclose(target, last_signal_target, rtol=0.0, atol=1e-12)
        )
        current_gross = float(np.abs(notional).sum() / equity_before_trade)
        risk_rebalance = current_gross > MAX_REALIZED_GROSS
        need_rebalance = bool(target_changed or risk_rebalance or day_forced > 0)
        turnover = 0.0
        rebalance_event = 0
        if need_rebalance:
            desired = target * equity_before_trade
            delta = desired - notional
            turnover_notional = float(np.abs(delta).sum())
            turnover = turnover_notional / equity_before_trade
            if turnover_notional > 0:
                cost_by_asset = np.abs(delta) * audit.cost_rate
                for j in np.flatnonzero(cost_by_asset > 0):
                    side = (
                        float(np.sign(desired[j]))
                        if abs(desired[j]) > 1e-12
                        else float(np.sign(notional[j]))
                    )
                    allocate(-float(cost_by_asset[j]), j, side)
                trade_cost = float(cost_by_asset.sum())
                equity -= trade_cost
                day_cost += trade_cost
                notional = target * max(equity, 0.0)
                for j in np.flatnonzero(np.abs(notional) > 1e-12):
                    ever_traded.add(market.symbols[j])
            rebalance_event = int(turnover > 1e-4)
            last_signal_target = target.copy()
'''
if old not in source:
    raise SystemExit("daily rebalance block not found")
source = source.replace(old, new, 1)

old = '''            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and all_years_positive
'''
new = '''            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and values["max_gross"] <= DEVELOPMENT_GATES["max_realized_gross"]
            and all_years_positive
'''
if old not in source:
    raise SystemExit("gross eligibility insertion point not found")
source = source.replace(old, new, 1)

old = '''    assert float(account.gross.max()) <= MAX_REALIZED_GROSS
    assert diagnostics["symbol_count_traded"] >= 4
'''
new = '''    assert float(account.gross.max()) <= MAX_REALIZED_GROSS
    assert diagnostics["symbol_count_traded"] >= 4
    assert diagnostics["rebalance_events"] < len(account) // 3
'''
if old not in source:
    raise SystemExit("self-test insertion point not found")
source = source.replace(old, new, 1)

path.write_text(source)
print("V254 scheduled execution and gross gate fix applied")
