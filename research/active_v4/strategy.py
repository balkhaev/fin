from __future__ import annotations
from dataclasses import asdict
import numpy as np
import pandas as pd
from config import ProcessSpec
from market import MarketData, apply_overlay, desired_weights, mean_frames, regime_overlay, scheduled
from metrics import equity_metrics

__all__ = [
    "MarketData", "ProcessFactory", "annual_turnover", "build_family_experts",
    "mean_frames", "evaluate_processes", "select_diverse", "simulate",
]


def build_family_experts(data: MarketData):
    families: dict[str, pd.DataFrame] = {}
    counts: dict[str, int] = {}
    template = pd.DataFrame(0.0, index=data.index, columns=data.symbols)

    variants: list[pd.DataFrame] = []
    for lookbacks in ((21, 63, 126), (42, 126, 252), (63, 126, 252)):
        positive = sum((data.momentum(days) > 0).astype(int) for days in lookbacks)
        score = sum(data.momentum(days).clip(lower=0) for days in lookbacks) / len(lookbacks)
        for required in (2, 3):
            for ema_days in (100, 200):
                eligible = (positive >= required) & (data.close > data.ema(ema_days))
                for target_vol in (0.15, 0.20, 0.25):
                    for mode in ("equal", "invvol", "top1"):
                        for rebalance_days in (7, 14):
                            variants.append(scheduled(
                                desired_weights(data, score, eligible, target_vol, mode, 60),
                                rebalance_days,
                                0.10,
                            ))
    families["breadth"] = mean_frames(variants, template)
    counts["breadth"] = len(variants)

    variants = []
    for fast_days, slow_days in ((21, 126), (42, 180), (63, 252)):
        vol = data.vol(60).replace(0, np.nan)
        score = (
            0.35 * data.momentum(fast_days).div(vol)
            + 0.65 * data.momentum(slow_days).div(vol)
        )
        for ema_days in (100, 200):
            eligible = (
                (data.momentum(fast_days) > 0)
                & (data.momentum(slow_days) > 0)
                & (data.close > data.ema(ema_days))
            )
            for target_vol in (0.15, 0.20, 0.25):
                for mode in ("invvol", "top1"):
                    for rebalance_days in (7, 14):
                        for band in (0.10, 0.20):
                            variants.append(scheduled(
                                desired_weights(data, score, eligible, target_vol, mode, 60),
                                rebalance_days,
                                band,
                            ))
    families["dual"] = mean_frames(variants, template)
    counts["dual"] = len(variants)

    variants = []
    for entry_days, exit_days in ((55, 20), (90, 45), (180, 90), (252, 126)):
        eligible = data.donchian_state(entry_days, exit_days) > 0
        score = data.momentum(entry_days).clip(lower=0)
        for target_vol in (0.15, 0.20, 0.25):
            for mode in ("equal", "invvol", "top1"):
                for rebalance_days in (1, 7):
                    for band in (0.05, 0.10):
                        variants.append(scheduled(
                            desired_weights(data, score, eligible, target_vol, mode, 60),
                            rebalance_days,
                            band,
                        ))
    families["donchian"] = mean_frames(variants, template)
    counts["donchian"] = len(variants)

    variants = []
    for fast_days, medium_days, slow_days in (
        (20, 50, 200), (50, 100, 200), (20, 100, 200), (21, 63, 126),
    ):
        fast = data.ema(fast_days)
        medium = data.ema(medium_days)
        slow = data.ema(slow_days)
        confidence = (
            (data.close > fast).astype(int)
            + (fast > medium).astype(int)
            + (medium > slow).astype(int)
            + (data.momentum(63) > 0).astype(int)
        )
        score = confidence / 4 + data.momentum(126).clip(lower=0)
        for required in (3, 4):
            eligible = confidence >= required
            for target_vol in (0.15, 0.20, 0.25):
                for mode in ("equal", "invvol", "top1"):
                    for rebalance_days in (7, 14):
                        variants.append(scheduled(
                            desired_weights(data, score, eligible, target_vol, mode, 60),
                            rebalance_days,
                            0.10,
                        ))
    families["ma_stack"] = mean_frames(variants, template)
    counts["ma_stack"] = len(variants)

    for name, frame in families.items():
        if not np.isfinite(frame.to_numpy()).all():
            raise ValueError(f"non-finite family weights: {name}")
        if float(frame.sum(axis=1).max()) > 1.000001 or float(frame.min().min()) < 0:
            raise ValueError(f"invalid family weights: {name}")
    return families, counts


def simulate(data, signal_weights, costs, config, start, end):
    begin = pd.Timestamp(start, tz="UTC")
    finish = pd.Timestamp(end, tz="UTC")
    positions = np.flatnonzero((data.index >= begin) & (data.index < finish))
    if len(positions) < 30:
        raise ValueError("insufficient period")

    opens = data.open.to_numpy(float)
    closes = data.close.to_numpy(float)
    intraday = data.intraday_returns.to_numpy(float)
    signals = signal_weights.reindex(data.index).fillna(0.0).to_numpy(float)
    asset_count = len(data.symbols)
    pending = signals[positions[0] - 1].copy() if positions[0] > 0 else np.zeros(asset_count)
    assets = np.zeros(asset_count)
    cash = float(config.starting_equity)
    high_water = cash
    hard_stop = False

    size = len(positions)
    equities = np.empty(size)
    drawdowns = np.empty(size)
    exposures = np.empty(size)
    turnovers = np.empty(size)
    cost_values = np.empty(size)
    actual_rows = np.empty((size, asset_count))
    previous_position = None

    for row_index, position in enumerate(positions):
        if previous_position is not None:
            assets *= opens[position] / closes[previous_position]
        equity_open = float(cash + assets.sum())
        actual = assets / equity_open if equity_open > 0 else np.zeros(asset_count)
        target = np.zeros(asset_count) if hard_stop else pending
        turnover = float(np.abs(target - actual).sum())
        cost_cash = equity_open * turnover * costs.rate
        after_cost = max(0.0, equity_open - cost_cash)
        assets = target * after_cost
        cash = max(0.0, (1.0 - target.sum()) * after_cost)
        assets *= 1.0 + intraday[position]
        equity = float(cash + assets.sum())
        actual_close = assets / equity if equity > 0 else np.zeros(asset_count)
        high_water = max(high_water, equity)
        drawdown = equity / high_water - 1.0 if high_water else -1.0
        if drawdown <= -config.hard_drawdown_stop:
            hard_stop = True

        equities[row_index] = equity
        drawdowns[row_index] = drawdown
        exposures[row_index] = actual_close.sum()
        turnovers[row_index] = turnover
        cost_values[row_index] = cost_cash
        actual_rows[row_index] = actual_close
        pending = signals[position].copy()
        previous_position = position

    result = pd.DataFrame({
        "equity": equities,
        "drawdown": drawdowns,
        "exposure": exposures,
        "turnover": turnovers,
        "costs": cost_values,
    }, index=data.index[positions])
    for column, symbol in enumerate(data.symbols):
        result[f"weight_{symbol}"] = actual_rows[:, column]
    return result


def annual_turnover(equity):
    years = max(
        (equity.index[-1] - equity.index[0]).total_seconds() / (365 * 86_400),
        1 / 365,
    )
    return float(equity.turnover.sum() / years)


def _score_account(account, mode):
    values = equity_metrics(account.equity)
    values["annual_turnover"] = annual_turnover(account)
    required = ("annualized_return", "max_drawdown", "sharpe")
    if any(not np.isfinite(values[key]) for key in required):
        return -1e12
    if values["max_drawdown"] < -0.40:
        return -1e12
    if mode == "robust":
        calmar = values["calmar"] if np.isfinite(values["calmar"]) else -2.0
        return float(
            values["sharpe"]
            + 0.5 * calmar
            + 0.25 * values["annualized_return"]
            - 0.01 * values["annual_turnover"]
        )
    if mode != "worst_year":
        raise ValueError(mode)
    yearly = []
    for _, segment in account.equity.groupby(account.index.year):
        if len(segment) > 1:
            yearly.append(float(segment.iloc[-1] / segment.iloc[0] - 1.0))
    return float(
        (min(yearly) if yearly else -1.0)
        + 0.5 * values["annualized_return"]
        + 0.25 * values["sharpe"]
        - 0.005 * values["annual_turnover"]
    )


class ProcessFactory:
    def __init__(self, data, families, base, stress, config):
        from dataclasses import replace

        self.data = data
        self.families = families
        self.base = base
        self.stress = stress
        self.config = config
        self._frames: dict[str, pd.DataFrame] = {}
        self._selection_cache: dict[tuple[int, int, str], dict[pd.Timestamp, list[str]]] = {}
        self.selection_log: list[dict[str, object]] = []

        ranking_config = replace(config, hard_drawdown_stop=1.0)
        full_start = str(data.index[0].date())
        full_end = str((data.index[-1] + pd.Timedelta(days=1)).date())
        self._ranking_accounts = {
            (name, cost.name): simulate(
                data, frame, cost, ranking_config, full_start, full_end,
            )
            for name, frame in families.items()
            for cost in (base, stress)
        }
        self._family_arrays = {
            name: frame.to_numpy(float) for name, frame in families.items()
        }

    def frame(self, spec: ProcessSpec):
        if spec.key in self._frames:
            return self._frames[spec.key]
        template = next(iter(self.families.values()))
        if spec.kind == "static":
            frame = mean_frames([self.families[name] for name in spec.subset], template)
        elif spec.kind == "walkforward":
            frame = self._walkforward(spec, template)
        else:
            raise ValueError(spec.kind)
        frame = apply_overlay(frame, regime_overlay(self.data, spec.overlay))
        self._frames[spec.key] = frame
        return frame

    def _ranking_schedule(self, train_days: int, selection_days: int, score_mode: str):
        cache_key = (train_days, selection_days, score_mode)
        if cache_key in self._selection_cache:
            return self._selection_cache[cache_key]
        schedule: dict[pd.Timestamp, list[str]] = {}
        last = None
        for timestamp in self.data.index:
            if timestamp < pd.Timestamp(self.config.evaluation_start, tz="UTC"):
                continue
            if last is not None and (timestamp - last).days < selection_days:
                continue
            train_end = timestamp + pd.Timedelta(days=1)
            train_start = max(
                self.data.index[0],
                timestamp - pd.Timedelta(days=train_days),
            )
            if (train_end - train_start).days < self.config.minimum_training_days:
                continue
            scores: dict[str, float] = {}
            for name in self.families:
                per_cost = []
                for cost in (self.base, self.stress):
                    account = self._ranking_accounts[(name, cost.name)]
                    segment = account[
                        (account.index >= train_start) & (account.index < train_end)
                    ]
                    per_cost.append(_score_account(segment, score_mode))
                scores[name] = min(per_cost)
            ranking = [
                name for name, _ in sorted(
                    scores.items(), key=lambda item: (item[1], item[0]), reverse=True,
                )
            ]
            schedule[timestamp] = ranking
            last = timestamp
            self.selection_log.append({
                "schedule": f"tr{train_days}_sel{selection_days}_{score_mode}",
                "decision_time": timestamp.isoformat(),
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "ranking": "+".join(ranking),
                **{f"score_{name}": value for name, value in scores.items()},
            })
        self._selection_cache[cache_key] = schedule
        return schedule

    def _walkforward(self, spec: ProcessSpec, template: pd.DataFrame):
        schedule = self._ranking_schedule(
            spec.train_days, spec.selection_days, spec.score_mode,
        )
        output = np.zeros((len(self.data.index), len(self.data.symbols)))
        selected: list[str] = []
        for index, timestamp in enumerate(self.data.index):
            if timestamp in schedule:
                selected = schedule[timestamp][:spec.top_k]
            if selected:
                output[index] = np.mean(
                    [self._family_arrays[name][index] for name in selected], axis=0,
                )
        return pd.DataFrame(output, index=self.data.index, columns=self.data.symbols)


def _process_score(rows):
    if len(rows) != 4:
        return -1e12
    if any(
        row["total_return"] <= 0
        or row["max_drawdown"] < -0.30
        or row["annual_turnover"] > 30
        for row in rows
    ):
        return -1e12
    calmars = [row["calmar"] for row in rows]
    sharpes = [row["sharpe"] for row in rows]
    annualized = [row["annualized_return"] for row in rows]
    if not all(np.isfinite(calmars + sharpes + annualized)):
        return -1e12
    return float(
        0.40 * min(calmars)
        + 0.25 * min(sharpes)
        + 0.25 * min(annualized)
        - 0.01 * max(row["annual_turnover"] for row in rows)
    )


def evaluate_processes(data, factory, specs, base, stress, config):
    rows: list[dict[str, object]] = []
    frames: dict[str, pd.DataFrame] = {}
    periods = {
        "development": (config.evaluation_start, config.development_end),
        "validation": (config.development_end, config.validation_end),
    }
    for number, spec in enumerate(specs, start=1):
        frame = factory.frame(spec)
        frames[spec.key] = frame
        row: dict[str, object] = {"key": spec.key, **asdict(spec)}
        score_rows = []
        for period, (start, end) in periods.items():
            for cost in (base, stress):
                equity = simulate(data, frame, cost, config, start, end)
                values = equity_metrics(equity.equity)
                values["annual_turnover"] = annual_turnover(equity)
                score_rows.append(values)
                for key, value in values.items():
                    row[f"{period}_{cost.name}_{key}"] = value
        row["robust_score"] = _process_score(score_rows)
        rows.append(row)
        if number % 25 == 0:
            print(f"processes evaluated: {number}")
    return (
        pd.DataFrame(rows).sort_values("robust_score", ascending=False).reset_index(drop=True),
        frames,
        list(factory.selection_log),
    )


def select_diverse(results: pd.DataFrame, limit: int = 3):
    viable = results[results.robust_score > -1e11]
    if viable.empty:
        return results.head(limit).copy()
    selected: list[int] = []
    for index, row in viable.iterrows():
        signature = (
            row.kind, row.overlay, row.top_k, row.score_mode, str(row.subset),
        )
        if all(
            signature != (
                viable.loc[other].kind,
                viable.loc[other].overlay,
                viable.loc[other].top_k,
                viable.loc[other].score_mode,
                str(viable.loc[other].subset),
            )
            for other in selected
        ):
            selected.append(index)
        if len(selected) == limit:
            break
    for index in viable.index:
        if len(selected) == limit:
            break
        if index not in selected:
            selected.append(index)
    return viable.loc[selected].reset_index(drop=True)
