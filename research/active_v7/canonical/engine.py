from __future__ import annotations

import numpy as np
import pandas as pd


def simulate(data, spot_signal, perp_open, perp_close, funding, hedge_signal, start, end, spot_cost, hedge_cost, forced_penalty):
    begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    positions = np.flatnonzero((data.index >= begin) & (data.index < finish))
    opens, closes = data.open.to_numpy(float), data.close.to_numpy(float)
    available = data.available.to_numpy(bool)
    po, pc = perp_open.to_numpy(float), perp_close.to_numpy(float)
    fr = funding.to_numpy(float)
    ss, hs = spot_signal.to_numpy(float), hedge_signal.to_numpy(float)
    n_spot, n_perp = opens.shape[1], po.shape[1]
    pending_s = ss[positions[0] - 1].copy() if positions[0] > 0 else np.zeros(n_spot)
    pending_h = hs[positions[0] - 1].copy() if positions[0] > 0 else np.zeros(n_perp)
    spot, hedge = np.zeros(n_spot), np.zeros(n_perp)
    cash, previous = 10_000.0, None
    rows = []
    for position in positions:
        forced_notional = forced_cost = 0.0
        if previous is not None:
            for column in np.flatnonzero(spot > 0):
                if np.isfinite(closes[previous, column]) and np.isfinite(opens[position, column]):
                    spot[column] *= opens[position, column] / closes[previous, column]
                else:
                    notional = float(spot[column])
                    penalty = notional * max(spot_cost, forced_penalty)
                    cash += max(0.0, notional - penalty)
                    spot[column] = 0.0
                    forced_notional += notional
                    forced_cost += penalty
            ratios = np.divide(po[position], pc[previous], out=np.ones(n_perp), where=np.isfinite(po[position]) & np.isfinite(pc[previous]))
            cash += np.sum(hedge * (ratios - 1.0))
            hedge *= ratios
        equity_open = float(cash + spot.sum())
        actual_s = spot / equity_open if equity_open > 0 else np.zeros(n_spot)
        actual_h = hedge / equity_open if equity_open > 0 else np.zeros(n_perp)
        target_s = np.nan_to_num(pending_s.copy())
        target_s[(~available[position]) | (target_s < 0)] = 0.0
        target_h = np.nan_to_num(pending_h.copy())
        gross = float(target_s.sum() + np.abs(target_h).sum())
        if gross > 1.0:
            target_s /= gross
            target_h /= gross
        turn_s = float(np.abs(target_s - actual_s).sum())
        turn_h = float(np.abs(target_h - actual_h).sum())
        transaction_cost = equity_open * (turn_s * spot_cost + turn_h * hedge_cost)
        after_cost = max(0.0, equity_open - transaction_cost)
        spot = target_s * after_cost
        cash = after_cost - spot.sum()
        hedge = target_h * after_cost
        spot_ratio = np.divide(closes[position], opens[position], out=np.ones(n_spot), where=np.isfinite(opens[position]) & np.isfinite(closes[position]))
        spot *= spot_ratio
        hedge_ratio = np.divide(pc[position], po[position], out=np.ones(n_perp), where=np.isfinite(po[position]) & np.isfinite(pc[position]))
        cash += np.sum(hedge * (hedge_ratio - 1.0))
        funding_pnl = float(np.sum(-(hedge * fr[position])))
        cash += funding_pnl
        hedge *= hedge_ratio
        equity = float(cash + spot.sum())
        rows.append({
            "equity": equity,
            "spot_gross": float(spot.sum() / equity),
            "perp_gross": float(np.abs(hedge).sum() / equity),
            "turnover": turn_s + turn_h + forced_notional / equity_open,
            "costs": transaction_cost + forced_cost,
            "funding_pnl": funding_pnl,
        })
        pending_s, pending_h, previous = ss[position].copy(), hs[position].copy(), position
    frame = pd.DataFrame(rows, index=data.index[positions])
    frame["gross"] = frame.spot_gross + frame.perp_gross
    return frame


def metrics(account):
    equity = account.equity
    returns = equity.pct_change().dropna()
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    annualized = float((1.0 + total) ** (365.0 / days) - 1.0)
    drawdown = equity / equity.cummax() - 1.0
    std = float(returns.std(ddof=1))
    return {
        "total_return": total,
        "annualized_return": annualized,
        "max_drawdown": float(drawdown.min()),
        "sharpe": float(np.sqrt(365.0) * returns.mean() / std) if std > 0 else np.nan,
        "annual_turnover": float(account.turnover.sum() / (days / 365.0)),
        "average_gross": float(account.gross.mean()),
        "total_costs": float(account.costs.sum()),
        "funding_pnl": float(account.funding_pnl.sum()),
    }
