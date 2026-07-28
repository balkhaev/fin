from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    canonical_json_bytes,
    require_decimal_string,
    sha256_id,
)
from finruntime.execution import (
    DEFAULT_PAPER_BROKER_POLICY,
    DEFAULT_PLANNER_POLICY,
    PaperBrokerPolicy,
    PaperQuote,
    PlannerPolicy,
    build_execution_plan,
    execute_paper_cycle,
)
from finruntime.io import object_dict
from finruntime.journal import AppendOnlyJournal, write_atomic_json
from finruntime.models import MarketSnapshot, StrategySnapshot
from finruntime.portfolio import (
    DEFAULT_RISK_LIMITS,
    PaperAccountState,
    ReferencePriceBook,
    RiskLimits,
    apply_pretrade_risk,
    build_forward_telemetry_row,
    build_reconciliation_report,
)

TELEMETRY_FIELDS = (
    "timestamp",
    "strategy_id",
    "source_bundle_sha256",
    "target_hash",
    "realized_position_hash",
    "gross_target",
    "gross_realized",
    "turnover",
    "modelled_slippage_bps",
    "paper_slippage_bps",
    "net_return",
    "equity",
    "drawdown",
    "reconciliation_ok",
    "source_hash_match",
    "data_stale",
    "execution_complete",
)


@dataclass(frozen=True, slots=True)
class PaperCycleRequest:
    market_snapshot: MarketSnapshot
    strategy_snapshot: StrategySnapshot
    starting_account: PaperAccountState
    quotes: Sequence[PaperQuote]
    reference_prices: ReferencePriceBook
    critical_sources: Sequence[str]
    onchain_sources: Sequence[str] = ()
    modelled_cost: str = "0"
    modelled_slippage_bps: str = "0"
    source_hash_match: bool = True
    data_stale: bool = False
    risk_limits: RiskLimits = DEFAULT_RISK_LIMITS
    planner_policy: PlannerPolicy = DEFAULT_PLANNER_POLICY
    broker_policy: PaperBrokerPolicy = DEFAULT_PAPER_BROKER_POLICY

    def validate(self) -> None:
        self.market_snapshot.validate()
        self.strategy_snapshot.validate()
        self.starting_account.validate()
        if self.strategy_snapshot.strategy_id != self.starting_account.strategy_id:
            raise ContractError("cycle strategy snapshot and account strategy_id mismatch")
        if self.strategy_snapshot.market_snapshot_id != self.market_snapshot.snapshot_id:
            raise ContractError("cycle strategy snapshot references another market snapshot")
        if not self.critical_sources:
            raise ContractError("paper cycle requires at least one critical source")
        for quote in self.quotes:
            quote.validate()
        require_decimal_string(
            self.modelled_cost,
            field="modelled_cost",
            minimum=Decimal("0"),
        )
        require_decimal_string(
            self.modelled_slippage_bps,
            field="modelled_slippage_bps",
            minimum=Decimal("0"),
        )
        self.risk_limits.validate()
        self.planner_policy.validate()
        self.broker_policy.validate()


@dataclass(frozen=True, slots=True)
class PaperCyclePaths:
    root: Path
    account_state: Path
    journal: Path
    telemetry_csv: Path

    @classmethod
    def under(cls, root: str | Path, strategy_id: str) -> "PaperCyclePaths":
        base = Path(root)
        strategy_root = base / strategy_id
        return cls(
            root=strategy_root,
            account_state=strategy_root / "account_state.json",
            journal=strategy_root / "events.jsonl",
            telemetry_csv=strategy_root / "forward_telemetry.csv",
        )


@dataclass(frozen=True, slots=True)
class PaperCycleResult:
    cycle_id: str
    status: str
    cycle_directory: str
    account_hash: str
    plan_id: str
    reconciliation_status: str
    telemetry_primary_key: tuple[str, str]
    restored_from_committed_cycle: bool


def _request_identity(request: PaperCycleRequest) -> dict[str, Any]:
    return {
        "contract": "runtime-v1-paper-cycle",
        "strategy_id": request.strategy_snapshot.strategy_id,
        "state_sequence": request.strategy_snapshot.state_sequence,
        "market_snapshot_id": request.market_snapshot.snapshot_id,
        "strategy_target_hash": request.strategy_snapshot.target_hash,
        "quotes": [asdict(quote) for quote in request.quotes],
        "reference_prices": request.reference_prices,
        "critical_sources": list(request.critical_sources),
        "onchain_sources": list(request.onchain_sources),
        "modelled_cost": request.modelled_cost,
        "modelled_slippage_bps": request.modelled_slippage_bps,
        "source_hash_match": request.source_hash_match,
        "data_stale": request.data_stale,
        "risk_limits": asdict(request.risk_limits),
        "planner_policy": asdict(request.planner_policy),
        "broker_policy": asdict(request.broker_policy),
    }


def cycle_id_for(request: PaperCycleRequest) -> str:
    request.validate()
    return sha256_id(_request_identity(request))


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ContractError(f"invalid telemetry boolean: {value!r}")


def _normalize_telemetry_csv_row(row: Mapping[str, str]) -> dict[str, object]:
    return {
        key: (
            _parse_bool(row[key])
            if key
            in {
                "reconciliation_ok",
                "source_hash_match",
                "data_stale",
                "execution_complete",
            }
            else row[key]
        )
        for key in TELEMETRY_FIELDS
    }


def append_telemetry_row_atomic(path: Path, row: Mapping[str, object]) -> None:
    if set(row) != set(TELEMETRY_FIELDS):
        raise ContractError("forward telemetry row has an unexpected schema")
    key = (str(row["timestamp"]), str(row["strategy_id"]))
    rows: list[dict[str, object]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != TELEMETRY_FIELDS:
                raise ContractError("existing forward telemetry CSV schema mismatch")
            for existing in reader:
                normalized = _normalize_telemetry_csv_row(existing)
                existing_key = (
                    str(normalized["timestamp"]),
                    str(normalized["strategy_id"]),
                )
                if existing_key == key:
                    if normalized != dict(row):
                        raise ContractError(
                            f"conflicting telemetry row for primary key {key}"
                        )
                    return
                rows.append(normalized)
    rows.append(dict(row))
    rows.sort(key=lambda value: (str(value["timestamp"]), str(value["strategy_id"])))

    output = tempfile.SpooledTemporaryFile(mode="w+", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(output, fieldnames=TELEMETRY_FIELDS, lineterminator="\n")
        writer.writeheader()
        for value in rows:
            writer.writerow(value)
        output.seek(0)
        payload = output.read().encode("utf-8")
    finally:
        output.close()
    _write_bytes_atomic(path, payload)


def _load_committed_cycle(cycle_directory: Path) -> dict[str, Any] | None:
    path = cycle_directory / "COMMITTED.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("committed paper-cycle marker is corrupt") from exc
    if not isinstance(value, dict):
        raise ContractError("committed paper-cycle marker must be an object")
    return value


def _load_cycle_account(cycle_directory: Path) -> PaperAccountState:
    raw = json.loads((cycle_directory / "account_state.json").read_text(encoding="utf-8"))
    state = PaperAccountState(**raw)
    state.validate()
    return state


def _restore_committed_side_effects(
    *,
    cycle_directory: Path,
    committed: Mapping[str, Any],
    paths: PaperCyclePaths,
) -> PaperCycleResult:
    final_state = _load_cycle_account(cycle_directory)
    expected_hash = committed.get("account_hash")
    if final_state.account_hash != expected_hash:
        raise ContractError("committed cycle account hash mismatch")

    if paths.account_state.exists():
        current_raw = json.loads(paths.account_state.read_text(encoding="utf-8"))
        current = PaperAccountState(**current_raw)
        current.validate()
        if current.account_hash != final_state.account_hash:
            if current.sequence > final_state.sequence:
                raise ContractError("refusing to roll paper account state backward")
            write_atomic_json(paths.account_state, final_state.to_dict())
    else:
        write_atomic_json(paths.account_state, final_state.to_dict())

    telemetry = json.loads(
        (cycle_directory / "forward_telemetry.json").read_text(encoding="utf-8")
    )
    append_telemetry_row_atomic(paths.telemetry_csv, telemetry)
    return PaperCycleResult(
        cycle_id=str(committed["cycle_id"]),
        status=str(committed["status"]),
        cycle_directory=str(cycle_directory),
        account_hash=final_state.account_hash,
        plan_id=str(committed["plan_id"]),
        reconciliation_status=str(committed["reconciliation_status"]),
        telemetry_primary_key=(
            str(telemetry["timestamp"]),
            str(telemetry["strategy_id"]),
        ),
        restored_from_committed_cycle=True,
    )


def _append_cycle_journal(
    *,
    journal: AppendOnlyJournal,
    request: PaperCycleRequest,
    plan: Any,
    execution: Any,
    reconciliation: Any,
) -> None:
    sequence = request.strategy_snapshot.state_sequence
    strategy_id = request.strategy_snapshot.strategy_id
    event_time = request.market_snapshot.decision_time_utc
    journal.append(
        event_type="SNAPSHOT_ACCEPTED",
        event_time_utc=event_time,
        strategy_id=strategy_id,
        sequence=sequence,
        payload=object_dict(request.market_snapshot),
    )
    journal.append(
        event_type="TARGET_COMPUTED",
        event_time_utc=event_time,
        strategy_id=strategy_id,
        sequence=sequence,
        payload=object_dict(request.strategy_snapshot),
    )
    journal.append(
        event_type="PLAN_CREATED",
        event_time_utc=plan.created_at_utc,
        strategy_id=strategy_id,
        sequence=sequence,
        payload=object_dict(plan),
    )
    for fill in execution.fill_events:
        journal.append(
            event_type="FILL_RECORDED",
            event_time_utc=fill.filled_at_utc,
            strategy_id=strategy_id,
            sequence=sequence,
            payload=object_dict(fill),
        )
    journal.append(
        event_type="STATE_COMMITTED",
        event_time_utc=execution.account_state.as_of_utc,
        strategy_id=strategy_id,
        sequence=sequence,
        payload=execution.account_state.to_dict(),
    )
    journal.append(
        event_type="RECONCILIATION_COMPLETED",
        event_time_utc=reconciliation.as_of_utc,
        strategy_id=strategy_id,
        sequence=sequence,
        payload=object_dict(reconciliation),
    )
    if reconciliation.status == "halt":
        journal.append(
            event_type="HALT_RAISED",
            event_time_utc=reconciliation.as_of_utc,
            strategy_id=strategy_id,
            sequence=sequence,
            payload={
                "report_hash": reconciliation.report_hash,
                "alerts": list(reconciliation.alerts),
            },
        )


def run_paper_cycle(
    *,
    request: PaperCycleRequest,
    paths: PaperCyclePaths,
) -> PaperCycleResult:
    request.validate()
    cycle_id = cycle_id_for(request)
    cycle_directory = paths.root / "cycles" / cycle_id.removeprefix("sha256:")
    committed = _load_committed_cycle(cycle_directory)
    if committed is not None:
        if committed.get("cycle_id") != cycle_id:
            raise ContractError("cycle directory contains another cycle id")
        return _restore_committed_side_effects(
            cycle_directory=cycle_directory,
            committed=committed,
            paths=paths,
        )

    paths.root.mkdir(parents=True, exist_ok=True)
    cycle_directory.mkdir(parents=True, exist_ok=True)
    prior_equity = request.starting_account.equity
    starting_positions = {
        "spot": dict(request.starting_account.spot_positions),
        "perp": dict(request.starting_account.perp_positions),
    }
    portfolio_state = request.starting_account.to_portfolio_state(
        last_market_snapshot_id=None,
        last_target_hash=None,
        last_plan_hash=None,
    )
    risk = apply_pretrade_risk(
        strategy_snapshot=request.strategy_snapshot,
        portfolio_state=portfolio_state,
        market_snapshot=request.market_snapshot,
        reference_prices=request.reference_prices,
        critical_sources=request.critical_sources,
        onchain_sources=request.onchain_sources,
        limits=request.risk_limits,
    )
    plan = build_execution_plan(
        strategy_snapshot=request.strategy_snapshot,
        portfolio_state=portfolio_state,
        market_snapshot=request.market_snapshot,
        risk_decision=risk,
        reference_prices=request.reference_prices,
        policy=request.planner_policy,
    )
    execution = execute_paper_cycle(
        plan=plan,
        account_state=request.starting_account,
        quotes=request.quotes,
        mark_prices=request.reference_prices,
        policy=request.broker_policy,
    )
    reconciliation = build_reconciliation_report(
        plan=plan,
        starting_positions=starting_positions,
        model_targets=request.strategy_snapshot.targets,
        account_state=execution.account_state,
        reference_prices=request.reference_prices,
        modelled_cost=request.modelled_cost,
        realized_paper_cost=execution.total_fees,
        source_hash_match=request.source_hash_match,
        data_stale=request.data_stale,
        execution_complete=execution.execution_complete,
    )
    telemetry = build_forward_telemetry_row(
        market_snapshot=request.market_snapshot,
        plan=plan,
        execution=execution,
        reconciliation=reconciliation,
        prior_equity=prior_equity,
        modelled_slippage_bps=request.modelled_slippage_bps,
        source_hash_match=request.source_hash_match,
        data_stale=request.data_stale,
    )

    write_atomic_json(cycle_directory / "request_identity.json", _request_identity(request))
    write_atomic_json(cycle_directory / "risk_decision.json", asdict(risk))
    write_atomic_json(cycle_directory / "execution_plan.json", object_dict(plan))
    write_atomic_json(
        cycle_directory / "fill_events.json",
        [object_dict(fill) for fill in execution.fill_events],
    )
    write_atomic_json(
        cycle_directory / "fill_outcomes.json",
        [asdict(outcome) for outcome in execution.outcomes],
    )
    write_atomic_json(cycle_directory / "account_state.json", execution.account_state.to_dict())
    write_atomic_json(
        cycle_directory / "reconciliation.json", object_dict(reconciliation)
    )
    write_atomic_json(cycle_directory / "forward_telemetry.json", telemetry)

    journal = AppendOnlyJournal(paths.journal)
    _append_cycle_journal(
        journal=journal,
        request=request,
        plan=plan,
        execution=execution,
        reconciliation=reconciliation,
    )
    journal.verify()

    status = "halt" if reconciliation.status == "halt" else "committed"
    commit = {
        "cycle_id": cycle_id,
        "status": status,
        "strategy_id": request.strategy_snapshot.strategy_id,
        "market_snapshot_id": request.market_snapshot.snapshot_id,
        "strategy_target_hash": request.strategy_snapshot.target_hash,
        "starting_account_hash": request.starting_account.account_hash,
        "account_hash": execution.account_state.account_hash,
        "plan_id": plan.plan_id,
        "reconciliation_status": reconciliation.status,
        "reconciliation_report_hash": reconciliation.report_hash,
        "telemetry_primary_key": [telemetry["timestamp"], telemetry["strategy_id"]],
        "journal_event_count": len(journal.verify()),
    }
    write_atomic_json(cycle_directory / "COMMITTED.json", commit)
    write_atomic_json(paths.account_state, execution.account_state.to_dict())
    append_telemetry_row_atomic(paths.telemetry_csv, telemetry)

    return PaperCycleResult(
        cycle_id=cycle_id,
        status=status,
        cycle_directory=str(cycle_directory),
        account_hash=execution.account_state.account_hash,
        plan_id=plan.plan_id,
        reconciliation_status=reconciliation.status,
        telemetry_primary_key=(
            str(telemetry["timestamp"]), str(telemetry["strategy_id"])
        ),
        restored_from_committed_cycle=False,
    )


def runtime_status(paths: PaperCyclePaths) -> dict[str, Any]:
    account: PaperAccountState | None = None
    if paths.account_state.exists():
        raw = json.loads(paths.account_state.read_text(encoding="utf-8"))
        account = PaperAccountState(**raw)
        account.validate()
    events = AppendOnlyJournal(paths.journal).verify()
    telemetry_rows = 0
    latest_telemetry: dict[str, object] | None = None
    if paths.telemetry_csv.exists():
        with paths.telemetry_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != TELEMETRY_FIELDS:
                raise ContractError("forward telemetry CSV schema mismatch")
            for raw in reader:
                latest_telemetry = _normalize_telemetry_csv_row(raw)
                telemetry_rows += 1
    cycle_count = sum(
        1
        for path in (paths.root / "cycles").glob("*/COMMITTED.json")
        if path.is_file()
    ) if (paths.root / "cycles").exists() else 0
    return {
        "strategy_root": str(paths.root),
        "account_available": account is not None,
        "account_hash": account.account_hash if account else None,
        "account_sequence": account.sequence if account else None,
        "equity": account.equity if account else None,
        "high_water": account.high_water if account else None,
        "journal_events": len(events),
        "committed_cycles": cycle_count,
        "telemetry_rows": telemetry_rows,
        "latest_telemetry": latest_telemetry,
        "live_execution_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }
