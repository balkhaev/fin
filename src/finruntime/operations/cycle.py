from __future__ import annotations

import csv
import hashlib
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
from finruntime.journal import AppendOnlyJournal, write_atomic_json, write_once_json
from finruntime.locking import exclusive_file_lock
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

_CYCLE_ARTIFACTS = (
    "request_identity.json",
    "risk_decision.json",
    "execution_plan.json",
    "fill_events.json",
    "fill_outcomes.json",
    "account_state.json",
    "reconciliation.json",
    "forward_telemetry.json",
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
        if len(set(self.critical_sources)) != len(tuple(self.critical_sources)):
            raise ContractError("critical_sources must be unique")
        if len(set(self.onchain_sources)) != len(tuple(self.onchain_sources)):
            raise ContractError("onchain_sources must be unique")
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
    lock: Path

    @classmethod
    def under(cls, root: str | Path, strategy_id: str) -> "PaperCyclePaths":
        base = Path(root)
        strategy_root = base / strategy_id
        return cls(
            root=strategy_root,
            account_state=strategy_root / "account_state.json",
            journal=strategy_root / "events.jsonl",
            telemetry_csv=strategy_root / "forward_telemetry.csv",
            lock=strategy_root / ".paper-cycle.lock",
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


def _effective_data_stale(request: PaperCycleRequest) -> bool:
    if request.data_stale:
        return True
    for source in request.critical_sources:
        observation = request.market_snapshot.sources.get(source)
        if observation is None or observation.quality != "ok":
            return True
    return False


def _request_identity(request: PaperCycleRequest) -> dict[str, Any]:
    return {
        "contract": "runtime-v2-paper-cycle",
        "strategy_id": request.strategy_snapshot.strategy_id,
        "state_sequence": request.strategy_snapshot.state_sequence,
        "market_snapshot_id": request.market_snapshot.snapshot_id,
        "strategy_target_hash": request.strategy_snapshot.target_hash,
        "starting_account_hash": request.starting_account.account_hash,
        "starting_account_sequence": request.starting_account.sequence,
        "quotes": [asdict(quote) for quote in request.quotes],
        "reference_prices": request.reference_prices,
        "critical_sources": list(request.critical_sources),
        "onchain_sources": list(request.onchain_sources),
        "modelled_cost": request.modelled_cost,
        "modelled_slippage_bps": request.modelled_slippage_bps,
        "source_hash_match": request.source_hash_match,
        "data_stale": request.data_stale,
        "effective_data_stale": _effective_data_stale(request),
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _artifact_hashes(cycle_directory: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in _CYCLE_ARTIFACTS:
        path = cycle_directory / name
        if not path.is_file():
            raise ContractError(f"cycle artifact is missing: {name}")
        hashes[name] = _file_sha256(path)
    return hashes


def _verify_artifacts(cycle_directory: Path, committed: Mapping[str, Any]) -> None:
    expected = committed.get("artifact_hashes")
    if not isinstance(expected, Mapping):
        raise ContractError("committed cycle lacks artifact hashes")
    actual = _artifact_hashes(cycle_directory)
    if dict(expected) != actual:
        raise ContractError("committed cycle artifact hash mismatch")


def _journal_target_sequence(events: Sequence[Mapping[str, Any]], strategy_id: str) -> tuple[int | None, str | None]:
    matches = [
        event
        for event in events
        if event.get("event_type") == "TARGET_COMPUTED"
        and event.get("strategy_id") == strategy_id
    ]
    if not matches:
        return None, None
    latest = max(matches, key=lambda event: int(event["sequence"]))
    payload = latest.get("payload")
    target_hash = payload.get("target_hash") if isinstance(payload, Mapping) else None
    return int(latest["sequence"]), str(target_hash) if target_hash else None


def _assert_monotonic_strategy_sequence(
    request: PaperCycleRequest,
    events: Sequence[Mapping[str, Any]],
) -> None:
    previous_sequence, previous_hash = _journal_target_sequence(
        events, request.strategy_snapshot.strategy_id
    )
    current_sequence = request.strategy_snapshot.state_sequence
    current_hash = request.strategy_snapshot.target_hash
    if previous_sequence is None:
        return
    if current_sequence < previous_sequence:
        raise ContractError("strategy state_sequence moved backward")
    if current_sequence == previous_sequence and current_hash != previous_hash:
        raise ContractError("strategy state_sequence conflicts with committed target")


def _assert_starting_account(request: PaperCycleRequest, paths: PaperCyclePaths) -> None:
    if paths.account_state.exists():
        raw = json.loads(paths.account_state.read_text(encoding="utf-8"))
        current = PaperAccountState(**raw)
        current.validate()
        if current.account_hash != request.starting_account.account_hash:
            raise ContractError("paper-cycle starting account is stale or divergent")
        return
    has_evidence = paths.journal.exists() or any(
        (paths.root / "cycles").glob("*/COMMITTED.json")
    )
    if has_evidence or request.starting_account.sequence != 0:
        raise ContractError("paper account state is missing for a non-pristine runtime")


def _event_specs(
    *,
    request: PaperCycleRequest,
    plan: Any,
    execution: Any,
    reconciliation: Any,
) -> list[dict[str, Any]]:
    sequence = request.strategy_snapshot.state_sequence
    strategy_id = request.strategy_snapshot.strategy_id
    event_time = request.market_snapshot.decision_time_utc
    specs: list[dict[str, Any]] = [
        {
            "event_type": "SNAPSHOT_ACCEPTED",
            "event_time_utc": event_time,
            "strategy_id": strategy_id,
            "sequence": sequence,
            "payload": object_dict(request.market_snapshot),
        },
        {
            "event_type": "TARGET_COMPUTED",
            "event_time_utc": event_time,
            "strategy_id": strategy_id,
            "sequence": sequence,
            "payload": object_dict(request.strategy_snapshot),
        },
        {
            "event_type": "PLAN_CREATED",
            "event_time_utc": plan.created_at_utc,
            "strategy_id": strategy_id,
            "sequence": sequence,
            "payload": object_dict(plan),
        },
    ]
    specs.extend(
        {
            "event_type": "FILL_RECORDED",
            "event_time_utc": fill.filled_at_utc,
            "strategy_id": strategy_id,
            "sequence": sequence,
            "payload": object_dict(fill),
        }
        for fill in execution.fill_events
    )
    specs.extend(
        [
            {
                "event_type": "STATE_COMMITTED",
                "event_time_utc": execution.account_state.as_of_utc,
                "strategy_id": strategy_id,
                "sequence": sequence,
                "payload": execution.account_state.to_dict(),
            },
            {
                "event_type": "RECONCILIATION_COMPLETED",
                "event_time_utc": reconciliation.as_of_utc,
                "strategy_id": strategy_id,
                "sequence": sequence,
                "payload": object_dict(reconciliation),
            },
        ]
    )
    if reconciliation.status == "halt":
        specs.append(
            {
                "event_type": "HALT_RAISED",
                "event_time_utc": reconciliation.as_of_utc,
                "strategy_id": strategy_id,
                "sequence": sequence,
                "payload": {
                    "report_hash": reconciliation.report_hash,
                    "alerts": list(reconciliation.alerts),
                },
            }
        )
    return specs


def _verify_committed_journal(
    journal: AppendOnlyJournal,
    committed: Mapping[str, Any],
) -> list[dict[str, Any]]:
    events = journal.verify()
    expected = committed.get("journal_event_hashes")
    if not isinstance(expected, list) or not expected:
        raise ContractError("committed cycle lacks journal event hashes")
    actual_hashes = {str(event["event_hash"]) for event in events}
    if not set(map(str, expected)).issubset(actual_hashes):
        raise ContractError("committed cycle journal evidence is missing")
    return events


def _verify_commit_marker(
    *,
    request: PaperCycleRequest,
    cycle_id: str,
    cycle_directory: Path,
    committed: Mapping[str, Any],
    final_state: PaperAccountState,
) -> None:
    if committed.get("schema_version") != "2.0":
        raise ContractError("unsupported committed cycle schema")
    expected_pairs = {
        "cycle_id": cycle_id,
        "strategy_id": request.strategy_snapshot.strategy_id,
        "state_sequence": request.strategy_snapshot.state_sequence,
        "market_snapshot_id": request.market_snapshot.snapshot_id,
        "strategy_target_hash": request.strategy_snapshot.target_hash,
        "starting_account_hash": request.starting_account.account_hash,
        "starting_account_sequence": request.starting_account.sequence,
        "account_hash": final_state.account_hash,
        "account_sequence": final_state.sequence,
    }
    for field, expected in expected_pairs.items():
        if committed.get(field) != expected:
            raise ContractError(f"committed cycle {field} mismatch")

    plan = json.loads((cycle_directory / "execution_plan.json").read_text(encoding="utf-8"))
    reconciliation = json.loads(
        (cycle_directory / "reconciliation.json").read_text(encoding="utf-8")
    )
    telemetry = json.loads(
        (cycle_directory / "forward_telemetry.json").read_text(encoding="utf-8")
    )
    if not isinstance(plan, Mapping) or committed.get("plan_id") != plan.get("plan_id"):
        raise ContractError("committed cycle plan id mismatch")
    if not isinstance(reconciliation, Mapping):
        raise ContractError("committed cycle reconciliation is invalid")
    if committed.get("reconciliation_status") != reconciliation.get("status"):
        raise ContractError("committed cycle reconciliation status mismatch")
    if committed.get("reconciliation_report_hash") != reconciliation.get("report_hash"):
        raise ContractError("committed cycle reconciliation hash mismatch")
    expected_status = "halt" if reconciliation.get("status") == "halt" else "committed"
    if committed.get("status") != expected_status:
        raise ContractError("committed cycle status mismatch")
    if not isinstance(telemetry, Mapping):
        raise ContractError("committed cycle telemetry is invalid")
    key = [telemetry.get("timestamp"), telemetry.get("strategy_id")]
    if committed.get("telemetry_primary_key") != key:
        raise ContractError("committed cycle telemetry primary key mismatch")


def _restore_committed_side_effects(
    *,
    request: PaperCycleRequest,
    cycle_id: str,
    cycle_directory: Path,
    committed: Mapping[str, Any],
    paths: PaperCyclePaths,
) -> PaperCycleResult:
    if committed.get("cycle_id") != cycle_id:
        raise ContractError("cycle directory contains another cycle id")
    if committed.get("starting_account_hash") != request.starting_account.account_hash:
        raise ContractError("committed cycle starting account mismatch")
    identity = json.loads(
        (cycle_directory / "request_identity.json").read_text(encoding="utf-8")
    )
    if canonical_json_bytes(identity) != canonical_json_bytes(_request_identity(request)):
        raise ContractError("committed cycle request identity mismatch")
    _verify_artifacts(cycle_directory, committed)
    journal = AppendOnlyJournal(paths.journal)
    events = _verify_committed_journal(journal, committed)

    final_state = _load_cycle_account(cycle_directory)
    _verify_commit_marker(
        request=request,
        cycle_id=cycle_id,
        cycle_directory=cycle_directory,
        committed=committed,
        final_state=final_state,
    )

    if paths.account_state.exists():
        current_raw = json.loads(paths.account_state.read_text(encoding="utf-8"))
        current = PaperAccountState(**current_raw)
        current.validate()
        if current.account_hash == final_state.account_hash:
            pass
        elif current.account_hash == request.starting_account.account_hash:
            write_atomic_json(paths.account_state, final_state.to_dict())
        elif current.sequence > final_state.sequence:
            raise ContractError("refusing to roll paper account state backward")
        else:
            raise ContractError("refusing to overwrite divergent paper account state")
    else:
        later_targets = [
            int(event["sequence"])
            for event in events
            if event.get("event_type") == "TARGET_COMPUTED"
            and event.get("strategy_id") == request.strategy_snapshot.strategy_id
        ]
        if later_targets and max(later_targets) > request.strategy_snapshot.state_sequence:
            raise ContractError("cannot restore an old committed cycle over later evidence")
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


def _run_paper_cycle_locked(
    *,
    request: PaperCycleRequest,
    paths: PaperCyclePaths,
) -> PaperCycleResult:
    request.validate()
    cycle_id = cycle_id_for(request)
    cycle_directory = paths.root / "cycles" / cycle_id.removeprefix("sha256:")
    committed = _load_committed_cycle(cycle_directory)
    if committed is not None:
        return _restore_committed_side_effects(
            request=request,
            cycle_id=cycle_id,
            cycle_directory=cycle_directory,
            committed=committed,
            paths=paths,
        )

    paths.root.mkdir(parents=True, exist_ok=True)
    cycle_directory.mkdir(parents=True, exist_ok=True)
    _assert_starting_account(request, paths)
    journal = AppendOnlyJournal(paths.journal)
    existing_events = journal.verify()
    _assert_monotonic_strategy_sequence(request, existing_events)

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
    external_reasons: list[str] = []
    if not request.source_hash_match:
        external_reasons.append("source_hash_mismatch_blocks_risk_increase")
    risk = apply_pretrade_risk(
        strategy_snapshot=request.strategy_snapshot,
        portfolio_state=portfolio_state,
        market_snapshot=request.market_snapshot,
        reference_prices=request.reference_prices,
        critical_sources=request.critical_sources,
        onchain_sources=request.onchain_sources,
        limits=request.risk_limits,
        external_risk_increase_permitted=request.source_hash_match,
        external_blocking_reasons=external_reasons,
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
    effective_stale = _effective_data_stale(request)
    reconciliation = build_reconciliation_report(
        plan=plan,
        starting_positions=starting_positions,
        model_targets=request.strategy_snapshot.targets,
        account_state=execution.account_state,
        reference_prices=request.reference_prices,
        modelled_cost=request.modelled_cost,
        realized_paper_cost=execution.total_fees,
        source_hash_match=request.source_hash_match,
        data_stale=effective_stale,
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
        data_stale=effective_stale,
    )

    artifact_values = {
        "request_identity.json": _request_identity(request),
        "risk_decision.json": asdict(risk),
        "execution_plan.json": object_dict(plan),
        "fill_events.json": [object_dict(fill) for fill in execution.fill_events],
        "fill_outcomes.json": [asdict(outcome) for outcome in execution.outcomes],
        "account_state.json": execution.account_state.to_dict(),
        "reconciliation.json": object_dict(reconciliation),
        "forward_telemetry.json": telemetry,
    }
    for name, value in artifact_values.items():
        write_once_json(cycle_directory / name, value)

    journal_events = journal.append_many(
        _event_specs(
            request=request,
            plan=plan,
            execution=execution,
            reconciliation=reconciliation,
        )
    )
    journal.verify()

    status = "halt" if reconciliation.status == "halt" else "committed"
    commit = {
        "schema_version": "2.0",
        "cycle_id": cycle_id,
        "status": status,
        "strategy_id": request.strategy_snapshot.strategy_id,
        "state_sequence": request.strategy_snapshot.state_sequence,
        "market_snapshot_id": request.market_snapshot.snapshot_id,
        "strategy_target_hash": request.strategy_snapshot.target_hash,
        "starting_account_hash": request.starting_account.account_hash,
        "starting_account_sequence": request.starting_account.sequence,
        "account_hash": execution.account_state.account_hash,
        "account_sequence": execution.account_state.sequence,
        "plan_id": plan.plan_id,
        "reconciliation_status": reconciliation.status,
        "reconciliation_report_hash": reconciliation.report_hash,
        "telemetry_primary_key": [telemetry["timestamp"], telemetry["strategy_id"]],
        "artifact_hashes": _artifact_hashes(cycle_directory),
        "journal_event_hashes": [event["event_hash"] for event in journal_events],
        "journal_event_count": len(journal.verify()),
    }
    write_once_json(cycle_directory / "COMMITTED.json", commit)
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


def run_paper_cycle(
    *,
    request: PaperCycleRequest,
    paths: PaperCyclePaths,
    lock_timeout_seconds: float = 30.0,
) -> PaperCycleResult:
    request.validate()
    with exclusive_file_lock(
        paths.lock,
        timeout_seconds=lock_timeout_seconds,
    ):
        return _run_paper_cycle_locked(request=request, paths=paths)


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
    cycle_count = (
        sum(
            1
            for path in (paths.root / "cycles").glob("*/COMMITTED.json")
            if path.is_file()
        )
        if (paths.root / "cycles").exists()
        else 0
    )
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
