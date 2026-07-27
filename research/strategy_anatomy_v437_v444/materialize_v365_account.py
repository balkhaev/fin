#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("v365_pinned_source", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import pinned V365 source from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()

    source = args.source_root / "research" / "active_v365_v372" / "run_research.py"
    v365 = load_module(source)
    v365.configure_engine()

    config = v365.base.V9Config(
        symbols=tuple(v365.v.SYMBOLS),
        start="2020-01-01",
        end_exclusive=v365.END_EXCLUSIVE,
        interval="1d",
        starting_equity=v365.base.INITIAL_EQUITY,
        max_gross=v365.TARGET_GROSS,
        forced_exit_penalty_bps=v365.FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = v365.base.load_v9(config, args.cache, False)
    records = sorted(
        records,
        key=lambda row: (
            str(row.get("kind", "")),
            str(row.get("symbol", "")),
            str(row.get("month", "")),
            str(row.get("url", "")),
        ),
    )
    quality = sorted(quality, key=lambda row: str(row.get("symbol", "")))
    gate = v365.base.data_gate(klines, records)
    if not gate.get("passed"):
        raise RuntimeError("pinned V365 data gate failed")

    market = v365.base.Market(klines, funding)
    weights, components = v365.build_ensemble(market)
    account, diagnostics = v365.base.simulate(
        market,
        weights,
        v365.START,
        v365.END_EXCLUSIVE,
        v365.AUDITS[0],
    )
    if account.empty:
        raise RuntimeError("pinned V365 account is empty")
    if len(account) != 2007:
        raise RuntimeError(f"unexpected V365 account rows: {len(account)}")
    if not np.isfinite(account.equity.to_numpy(float)).all():
        raise RuntimeError("non-finite V365 equity")

    args.output.mkdir(parents=True, exist_ok=True)
    account.index.name = "open_time"
    account_path = args.output / "v365_equity_base.csv"
    account.to_csv(account_path)
    weights_path = args.output / "v365_frozen_ensemble_weights.csv"
    weights.to_csv(weights_path)

    component_hashes: dict[str, str] = {}
    for name, frame in components.items():
        path = args.output / f"component_{name}.csv"
        frame.to_csv(path)
        component_hashes[name] = sha256_file(path)

    provenance = {
        "program": "V437_V444_STATE_CONDITIONED_STRATEGY_ANATOMY",
        "materialized_strategy": "V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE",
        "source_commit": args.source_commit,
        "source_path": str(source.relative_to(args.source_root)),
        "source_sha256": sha256_file(source),
        "account_sha256": sha256_file(account_path),
        "weights_sha256": sha256_file(weights_path),
        "component_hashes": component_hashes,
        "data_manifest_sha256": v365.canonical_hash(records),
        "data_quality": quality,
        "coverage_gate": gate,
        "rows": len(account),
        "start": account.index.min(),
        "end": account.index.max(),
        "diagnostics": diagnostics,
        "audit": clean(asdict(v365.AUDITS[0])),
        "neighboring_parameters_tested": 0,
    }
    (args.output / "V365_MATERIALIZATION.json").write_text(
        json.dumps(clean(provenance), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(clean(provenance), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
