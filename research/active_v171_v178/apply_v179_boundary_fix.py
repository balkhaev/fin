#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "data.py"

NEW_FUNCTION = r'''def _funding_blocks(
    frame: pd.DataFrame, venue: str
) -> pd.DataFrame:
    output_columns = [
        "timestamp",
        f"funding_{venue}",
        f"funding_count_{venue}",
        f"funding_hours_{venue}",
        f"funding_max_jitter_seconds_{venue}",
    ]
    if frame.empty:
        return pd.DataFrame(columns=output_columns)

    values = frame.copy()
    values["timestamp"] = pd.to_datetime(
        values["timestamp"], utc=True, errors="coerce", format="mixed"
    )
    values["rate"] = pd.to_numeric(values["rate"], errors="coerce")
    values = values.dropna(subset=["timestamp", "rate"]).copy()
    if values.empty:
        return pd.DataFrame(columns=output_columns)

    # Exchange archives stamp scheduled payments a few milliseconds late (and
    # early Hyperliquid records can be tens of minutes late).  A raw floor
    # therefore splits economically identical 08:00 payments between adjacent
    # blocks.  Snap only observations within a strict half-hour tolerance.
    values["scheduled_timestamp"] = values["timestamp"].dt.round("h")
    jitter = (values["timestamp"] - values["scheduled_timestamp"]).abs()
    if jitter.gt(pd.Timedelta(minutes=30)).any():
        bad = values.loc[
            jitter.gt(pd.Timedelta(minutes=30)),
            ["timestamp", "scheduled_timestamp"],
        ].head(5)
        raise ValueError(f"funding timestamp outside schedule tolerance: {bad}")
    values["jitter_seconds"] = jitter.dt.total_seconds()
    values = values.drop_duplicates("scheduled_timestamp", keep="last")
    values = values.sort_values("scheduled_timestamp").reset_index(drop=True)

    if "interval_hours" in values.columns:
        interval = pd.to_numeric(values["interval_hours"], errors="coerce")
    else:
        # Hyperliquid changed from 8-hour to hourly payments in the retained
        # history.  Infer the local schedule from the nearest valid neighbour;
        # using the minimum neighbour gap prevents a missing event from being
        # mistaken for a longer, fully covered interval.
        scheduled = values["scheduled_timestamp"]
        previous = scheduled.diff().dt.total_seconds().div(3600.0)
        following = scheduled.shift(-1).sub(scheduled).dt.total_seconds().div(3600.0)
        neighbours = pd.concat([previous, following], axis=1)
        neighbours = neighbours.where(neighbours.gt(0))
        interval = neighbours.min(axis=1, skipna=True)

    values["coverage_hours"] = (
        interval.fillna(float(BLOCK_HOURS))
        .clip(lower=1.0, upper=float(BLOCK_HOURS))
        .round(6)
    )

    # A payment stamped at T settles the interval ending at T.  After schedule
    # normalization, subtracting one nanosecond assigns it to [T-8h, T).
    values["block"] = (
        values["scheduled_timestamp"] - pd.Timedelta(nanoseconds=1)
    ).dt.floor(f"{BLOCK_HOURS}h")
    grouped = values.groupby("block", as_index=False).agg(
        rate=("rate", "sum"),
        count=("rate", "count"),
        coverage_hours=("coverage_hours", "sum"),
        max_jitter_seconds=("jitter_seconds", "max"),
    )
    return grouped.rename(
        columns={
            "block": "timestamp",
            "rate": f"funding_{venue}",
            "count": f"funding_count_{venue}",
            "coverage_hours": f"funding_hours_{venue}",
            "max_jitter_seconds": f"funding_max_jitter_seconds_{venue}",
        }
    )[output_columns]
'''

OLD_COMPLETENESS = '''    frame["funding_complete"] = (
        frame.funding_count_binance.fillna(0).ge(1)
        & frame.funding_count_hyperliquid.fillna(0).ge(8)
    )
'''

NEW_COMPLETENESS = '''    required_hours = float(BLOCK_HOURS) - 1e-6
    frame["funding_complete"] = (
        frame.funding_hours_binance.fillna(0).ge(required_hours)
        & frame.funding_hours_hyperliquid.fillna(0).ge(required_hours)
    )
'''

OLD_CACHE_PARSE = 'pd.to_datetime(frame["timestamp"], utc=True)'
NEW_CACHE_PARSE = 'pd.to_datetime(frame["timestamp"], utc=True, format="mixed")'


def main() -> int:
    source = TARGET.read_text()
    start = source.index("def _funding_blocks(")
    end = source.index("\ndef build_asset_frame(", start)
    source = source[:start] + NEW_FUNCTION.rstrip() + "\n\n" + source[end + 1 :]
    if OLD_COMPLETENESS not in source:
        raise SystemExit("expected funding completeness block not found")
    source = source.replace(OLD_COMPLETENESS, NEW_COMPLETENESS, 1)

    parse_count = source.count(OLD_CACHE_PARSE)
    if parse_count < 3:
        raise SystemExit(f"expected cached timestamp parsers, found {parse_count}")
    source = source.replace(OLD_CACHE_PARSE, NEW_CACHE_PARSE)

    TARGET.write_text(source)
    print(
        "V179 funding schedule normalization applied; "
        f"mixed timestamp parsers updated: {parse_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
