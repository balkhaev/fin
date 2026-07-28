#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from finruntime.canonical import ContractError, canonical_json_text
from finruntime.io import load_strategy_snapshot
from finruntime.profiles.v517_guard import (
    CompletedEquityObservation,
    V517RuntimeState,
    build_v517_shadow_snapshot,
)


def load_equity_history(path: Path) -> tuple[CompletedEquityObservation, ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"as_of_utc", "equity", "source_sha256"}
    if not rows:
        raise ContractError("V517 equity history is empty")
    if not required.issubset(rows[0]):
        raise ContractError(
            "V517 equity history requires as_of_utc,equity,source_sha256 columns"
        )
    return tuple(
        CompletedEquityObservation(
            as_of_utc=str(row["as_of_utc"]),
            equity=str(row["equity"]),
            source_sha256=str(row["source_sha256"]),
        )
        for row in rows
    )


def load_state(path: Path | None, *, initialize: bool) -> V517RuntimeState:
    if path is None:
        if not initialize:
            raise ContractError("--state is required unless --initialize-state is set")
        return V517RuntimeState()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError("V517 state must be a JSON object")
    state = V517RuntimeState(**value)
    state.validate()
    return state


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json_text(value) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic V517 shadow StrategySnapshot from a sealed V75 snapshot."
    )
    parser.add_argument("--primary-snapshot", type=Path, required=True)
    parser.add_argument("--equity-history", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--initialize-state", action="store_true")
    parser.add_argument("--profile-equity", required=True)
    parser.add_argument("--profile-high-water", required=True)
    parser.add_argument("--maximum-runtime-leverage", default="1.10")
    parser.add_argument("--output-snapshot", type=Path, required=True)
    parser.add_argument("--output-decision", type=Path, required=True)
    parser.add_argument("--output-state", type=Path, required=True)
    args = parser.parse_args()

    primary = load_strategy_snapshot(args.primary_snapshot)
    history = load_equity_history(args.equity_history)
    state = load_state(args.state, initialize=args.initialize_state)
    snapshot, decision = build_v517_shadow_snapshot(
        primary_snapshot=primary,
        observations=history,
        profile_equity=args.profile_equity,
        profile_high_water=args.profile_high_water,
        runtime_state=state,
        maximum_runtime_leverage=args.maximum_runtime_leverage,
    )
    write_json(args.output_snapshot, snapshot.to_dict())
    write_json(args.output_decision, decision.to_dict())
    write_json(args.output_state, decision.next_state.to_dict())
    print(
        canonical_json_text(
            {
                "strategy_id": snapshot.strategy_id,
                "target_hash": snapshot.target_hash,
                "decision_hash": decision.decision_hash,
                "market_state": decision.market.state_name,
                "requested_leverage": decision.requested_leverage,
                "selected_leverage": decision.selected_leverage,
                "live_execution_permitted": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
