#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config import PERIODS, SPOT_COST_BPS, HEDGE_COST_BPS, FORCED_DELISTING_PENALTY_BPS
from inputs import load
from signals import build
from engine import simulate, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--v6", type=Path, required=True)
    parser.add_argument("--v5", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data, base, spot, po, pc, funding = load(args.v6, args.v5)
    hedge = build(pc, funding, spot, base)
    hedge.to_csv(args.output / "hedge_signal.csv")
    rows = []
    for name, (start, end) in PERIODS.items():
        account = simulate(
            data, base, po, pc, funding, hedge, start, end,
            SPOT_COST_BPS / 10_000.0,
            HEDGE_COST_BPS / 10_000.0,
            FORCED_DELISTING_PENALTY_BPS / 10_000.0,
        )
        account.to_csv(args.output / f"{name}_equity.csv")
        rows.append({"period": name, **metrics(account)})
    (args.output / "metrics.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
