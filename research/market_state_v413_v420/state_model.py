from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from core import (
    AXES,
    DEVELOPMENT_END_EXCLUSIVE,
    END_EXCLUSIVE,
    SEGMENTS,
    START,
    STATE_COUNT,
    TECHNICAL_GATES,
    segment_mask,
)


def squared_distances(values: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    delta = values[:, None, :] - centroids[None, :, :]
    return np.einsum("nkd,nkd->nk", delta, delta)


def fit_kmeans(values: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray, float]:
    model = KMeans(
        n_clusters=STATE_COUNT,
        init="k-means++",
        n_init=32,
        max_iter=400,
        random_state=seed,
        algorithm="lloyd",
    )
    raw_labels = model.fit_predict(values)
    raw_centroids = model.cluster_centers_
    order = sorted(
        range(STATE_COUNT),
        key=lambda state: tuple(np.round(raw_centroids[state], 10).tolist()),
    )
    reverse = {old: new for new, old in enumerate(order)}
    labels = np.asarray([reverse[int(label)] for label in raw_labels], dtype=int)
    centroids = raw_centroids[order]
    inertia = float(squared_distances(values, centroids).min(axis=1).sum())
    return labels, centroids, inertia


def state_base_name(centroid: pd.Series) -> str:
    trend = float(centroid["trend"])
    breadth = float(centroid["breadth"])
    stress = float(centroid["stress"])
    rotation = float(centroid["rotation"])
    liquidity = float(centroid["liquidity"])
    leverage = float(centroid["leverage"])
    if stress >= 0.65 and trend <= -0.20:
        return "deleveraging"
    if stress >= 0.65 and liquidity <= -0.20:
        return "liquidity_stress"
    if trend >= 0.45 and breadth >= 0.10 and stress <= 0.30:
        return "calm_risk_on"
    if trend >= 0.20 and leverage >= 0.45 and stress >= 0.20:
        return "speculative_risk_on"
    if rotation >= 0.45 and abs(trend) <= 0.80:
        return "rotation"
    if trend <= -0.45 and stress < 0.65:
        return "quiet_risk_off"
    return "transition"


def label_centroids(centroids: pd.DataFrame) -> dict[int, str]:
    used: dict[str, int] = {}
    labels: dict[int, str] = {}
    for state, row in centroids.iterrows():
        base = state_base_name(row)
        used[base] = used.get(base, 0) + 1
        suffix = "" if used[base] == 1 else f"_{used[base]}"
        labels[int(state)] = f"{base}{suffix}"
    return labels


def transition_matrix(
    states: pd.Series,
    state_count: int = STATE_COUNT,
    smoothing: float = 0.5,
) -> np.ndarray:
    counts = np.full((state_count, state_count), smoothing, dtype=float)
    previous_state: int | None = None
    previous_time: pd.Timestamp | None = None
    for timestamp, raw_state in states.dropna().items():
        state = int(raw_state)
        if (
            previous_state is not None
            and previous_time is not None
            and timestamp - previous_time <= pd.Timedelta(days=2)
        ):
            counts[previous_state, state] += 1.0
        previous_state = state
        previous_time = timestamp
    return counts / counts.sum(axis=1, keepdims=True)


def assign_states(
    axes: pd.DataFrame,
    centroids: pd.DataFrame,
    development_distance_q95: float,
    transitions: np.ndarray,
    state_labels: dict[int, str],
) -> pd.DataFrame:
    valid = axes.notna().all(axis=1)
    output = pd.DataFrame(index=axes.index)
    output["state_id"] = pd.Series(pd.NA, index=axes.index, dtype="Int64")
    output["state_label"] = pd.Series(pd.NA, index=axes.index, dtype="string")
    output["assignment_confidence"] = np.nan
    output["nearest_distance"] = np.nan
    output["novelty_ratio"] = np.nan
    output["novelty_flag"] = False

    if valid.any():
        values = axes.loc[valid, centroids.columns].to_numpy(float)
        distance2 = squared_distances(values, centroids.to_numpy(float))
        order = np.argsort(distance2, axis=1)
        first = order[:, 0]
        first_distance = np.sqrt(distance2[np.arange(len(values)), first])
        second_distance = np.sqrt(distance2[np.arange(len(values)), order[:, 1]])
        confidence = np.clip(
            1.0 - first_distance / np.maximum(second_distance, 1e-12),
            0.0,
            1.0,
        )
        output.loc[valid, "state_id"] = first
        output.loc[valid, "state_label"] = [state_labels[int(value)] for value in first]
        output.loc[valid, "assignment_confidence"] = confidence
        output.loc[valid, "nearest_distance"] = first_distance
        output.loc[valid, "novelty_ratio"] = first_distance / max(
            development_distance_q95, 1e-12
        )
        output.loc[valid, "novelty_flag"] = (
            output.loc[valid, "novelty_ratio"].astype(float) > 1.0
        )

    surprise = pd.Series(np.nan, index=axes.index)
    duration = pd.Series(0, index=axes.index, dtype=int)
    previous_state: int | None = None
    previous_time: pd.Timestamp | None = None
    run_length = 0
    for timestamp, raw_state in output["state_id"].items():
        if pd.isna(raw_state):
            previous_state = None
            previous_time = None
            run_length = 0
            continue
        state = int(raw_state)
        if (
            previous_state is not None
            and previous_time is not None
            and timestamp - previous_time <= pd.Timedelta(days=2)
        ):
            probability = float(transitions[previous_state, state])
            surprise.loc[timestamp] = -math.log(max(probability, 1e-12))
            run_length = run_length + 1 if state == previous_state else 1
        else:
            run_length = 1
        duration.loc[timestamp] = run_length
        previous_state = state
        previous_time = timestamp
    output["transition_surprise"] = surprise
    output["state_duration_days"] = duration
    return pd.concat([axes, output], axis=1)


def occupancy_table(state_daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for segment, (start, end) in SEGMENTS.items():
        subset = state_daily.loc[segment_mask(state_daily.index, start, end)]
        valid = subset.dropna(subset=["state_id"])
        total = len(valid)
        for (state_id, state_label), group in valid.groupby(
            ["state_id", "state_label"], observed=True
        ):
            rows.append(
                {
                    "segment": segment,
                    "state_id": int(state_id),
                    "state_label": str(state_label),
                    "days": len(group),
                    "occupancy": len(group) / max(total, 1),
                    "median_duration_days": float(group["state_duration_days"].median()),
                    "mean_confidence": float(group["assignment_confidence"].mean()),
                    "novelty_rate": float(group["novelty_flag"].mean()),
                }
            )
    return pd.DataFrame(rows)


def future_sum(series: pd.Series, horizon: int) -> pd.Series:
    return series.shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).sum().iloc[::-1]


def future_mean(series: pd.Series, horizon: int) -> pd.Series:
    return series.shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).mean().iloc[::-1]


def future_std(series: pd.Series, horizon: int) -> pd.Series:
    return series.shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).std(ddof=1).iloc[::-1]


def state_market_diagnostics(
    state_daily: pd.DataFrame,
    market: Any,
) -> pd.DataFrame:
    market_return = market.market.reindex(state_daily.index)
    dispersion = market.logret.std(axis=1, ddof=1).reindex(state_daily.index)
    diagnostic = state_daily[["state_id", "state_label"]].copy()
    diagnostic["next_1d_market_log_return"] = market_return.shift(-1)
    diagnostic["next_5d_market_log_return"] = future_sum(market_return, 5)
    diagnostic["next_20d_market_vol"] = future_std(market_return, 20) * math.sqrt(365.0)
    diagnostic["next_5d_dispersion"] = future_mean(dispersion, 5)
    rows: list[dict[str, Any]] = []
    for segment, (start, end) in SEGMENTS.items():
        subset = diagnostic.loc[segment_mask(diagnostic.index, start, end)]
        for (state_id, state_label), group in subset.dropna(
            subset=["state_id"]
        ).groupby(["state_id", "state_label"], observed=True):
            one = pd.to_numeric(group["next_1d_market_log_return"], errors="coerce").dropna()
            five = pd.to_numeric(group["next_5d_market_log_return"], errors="coerce").dropna()
            vol = pd.to_numeric(group["next_20d_market_vol"], errors="coerce").dropna()
            disp = pd.to_numeric(group["next_5d_dispersion"], errors="coerce").dropna()
            rows.append(
                {
                    "segment": segment,
                    "state_id": int(state_id),
                    "state_label": str(state_label),
                    "observations": len(group),
                    "next_1d_market_return_mean": float(one.mean()) if len(one) else np.nan,
                    "next_1d_down_frequency": float((one < 0.0).mean()) if len(one) else np.nan,
                    "next_5d_market_return_mean": float(five.mean()) if len(five) else np.nan,
                    "next_20d_market_vol_mean": float(vol.mean()) if len(vol) else np.nan,
                    "next_5d_dispersion_mean": float(disp.mean()) if len(disp) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def centroid_min_distance(centroids: np.ndarray) -> float:
    distance = squared_distances(centroids, centroids)
    distance[np.eye(len(centroids), dtype=bool)] = np.inf
    return float(np.sqrt(distance.min()))


def feature_coverage(frame: pd.DataFrame, mask: pd.Series) -> dict[str, float]:
    subset = frame.loc[mask]
    return {
        "row_complete_share": float(subset.notna().all(axis=1).mean()),
        "cell_nonmissing_share": float(subset.notna().mean().mean()),
        "rows": len(subset),
    }


def quality_report(
    features: pd.DataFrame,
    state_daily: pd.DataFrame,
    centroids: pd.DataFrame,
    transitions: np.ndarray,
) -> dict[str, Any]:
    dev_mask = segment_mask(features.index, START, DEVELOPMENT_END_EXCLUSIVE)
    oos_mask = segment_mask(features.index, DEVELOPMENT_END_EXCLUSIVE, END_EXCLUSIVE)
    dev_valid = state_daily.loc[dev_mask].dropna(subset=["state_id"])
    oos_valid = state_daily.loc[oos_mask].dropna(subset=["state_id"])
    occupancy = dev_valid["state_id"].value_counts(normalize=True)
    min_occupancy = float(occupancy.min()) if len(occupancy) else 0.0
    max_occupancy = float(occupancy.max()) if len(occupancy) else 1.0
    novelty_rate = float(oos_valid["novelty_flag"].mean()) if len(oos_valid) else 1.0
    mean_confidence = (
        float(oos_valid["assignment_confidence"].mean()) if len(oos_valid) else 0.0
    )
    dev_coverage = feature_coverage(features, dev_mask)
    oos_coverage = feature_coverage(features, oos_mask)
    minimum_distance = centroid_min_distance(centroids.to_numpy(float))
    checks = {
        "development_assignment_days": len(dev_valid)
        >= TECHNICAL_GATES["development_assignment_days_min"],
        "development_feature_row_coverage": dev_coverage["row_complete_share"]
        >= TECHNICAL_GATES["development_feature_row_coverage_min"],
        "oos_feature_row_coverage": oos_coverage["row_complete_share"]
        >= TECHNICAL_GATES["oos_feature_row_coverage_min"],
        "development_min_state_occupancy": min_occupancy
        >= TECHNICAL_GATES["development_min_state_occupancy"],
        "development_max_state_occupancy": max_occupancy
        <= TECHNICAL_GATES["development_max_state_occupancy"],
        "minimum_centroid_distance": minimum_distance
        >= TECHNICAL_GATES["minimum_centroid_distance"],
        "oos_novelty_rate": novelty_rate <= TECHNICAL_GATES["oos_novelty_rate_max"],
        "mean_assignment_confidence": mean_confidence
        >= TECHNICAL_GATES["mean_assignment_confidence_min"],
    }
    return {
        "technical_gates": TECHNICAL_GATES,
        "checks": checks,
        "passed": bool(all(checks.values())),
        "development_feature_coverage": dev_coverage,
        "oos_feature_coverage": oos_coverage,
        "development_assignment_days": len(dev_valid),
        "oos_assignment_days": len(oos_valid),
        "development_min_state_occupancy": min_occupancy,
        "development_max_state_occupancy": max_occupancy,
        "minimum_centroid_distance": minimum_distance,
        "oos_novelty_rate": novelty_rate,
        "oos_mean_assignment_confidence": mean_confidence,
        "mean_development_self_transition_probability": float(np.diag(transitions).mean()),
    }
