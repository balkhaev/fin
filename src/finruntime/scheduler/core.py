from __future__ import annotations

import hashlib
import json
import os
import signal
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any, Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    format_utc,
    parse_utc,
    require_decimal_string,
    require_sha256,
    sha256_id,
)
from finruntime.execution import (
    DEFAULT_PAPER_BROKER_POLICY,
    DEFAULT_PLANNER_POLICY,
    PaperBrokerPolicy,
    PlannerPolicy,
)
from finruntime.io import (
    object_dict,
    parse_market_snapshot,
    parse_paper_account,
    parse_paper_quotes,
    parse_reference_prices,
    parse_strategy_snapshot,
)
from finruntime.journal import AppendOnlyJournal, write_atomic_json, write_once_json
from finruntime.locking import LockUnavailableError, exclusive_file_lock
from finruntime.models import MarketSnapshot, StrategySnapshot
from finruntime.operations import (
    PaperCyclePaths,
    PaperCycleRequest,
    cycle_id_for,
    run_paper_cycle,
)
from finruntime.portfolio import DEFAULT_RISK_LIMITS, PaperAccountState, RiskLimits

_MAX_REQUEST_TTL = timedelta(days=7)
_ENVELOPE_FIELDS = {
    "schema_version",
    "request_id",
    "created_at_utc",
    "not_before_utc",
    "expires_at_utc",
    "strategy_id",
    "expected_account_hash",
    "expected_account_sequence",
    "starting_account",
    "market_snapshot",
    "strategy_snapshot",
    "quotes",
    "reference_prices",
    "critical_sources",
    "onchain_sources",
    "modelled_cost",
    "modelled_slippage_bps",
    "source_hash_match",
    "data_stale",
    "risk_limits",
    "planner_policy",
    "broker_policy",
}


def require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return value


def require_sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractError(f"{label} must be an array")
    return value


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{label} must be a JSON boolean")
    return value


def asdict_or_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return object_dict(value)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _risk_payload(policy: RiskLimits) -> dict[str, str]:
    policy.validate()
    return {
        "gross_cap": _decimal_text(policy.gross_cap),
        "initial_margin_ratio": _decimal_text(policy.initial_margin_ratio),
        "operational_reserve": _decimal_text(policy.operational_reserve),
    }


def _planner_payload(policy: PlannerPolicy) -> dict[str, object]:
    policy.validate()
    return {
        "spot_max_slippage_bps": _decimal_text(policy.spot_max_slippage_bps),
        "perp_max_slippage_bps": _decimal_text(policy.perp_max_slippage_bps),
        "intent_ttl_seconds": policy.intent_ttl_seconds,
        "minimum_quantity": _decimal_text(policy.minimum_quantity),
    }


def _broker_payload(policy: PaperBrokerPolicy) -> dict[str, object]:
    policy.validate()
    return {
        "spot_commission_bps": _decimal_text(policy.spot_commission_bps),
        "perp_commission_bps": _decimal_text(policy.perp_commission_bps),
        "proxy_half_spread_bps": _decimal_text(policy.proxy_half_spread_bps),
        "impact_bps": _decimal_text(policy.impact_bps),
        "participation_rate": _decimal_text(policy.participation_rate),
        "permit_stale_quotes": policy.permit_stale_quotes,
    }


def _parse_risk_policy(value: Mapping[str, Any]) -> RiskLimits:
    required = {"gross_cap", "initial_margin_ratio", "operational_reserve"}
    if set(value) != required:
        raise ContractError("scheduler risk_limits schema mismatch")
    policy = RiskLimits(
        gross_cap=require_decimal_string(str(value["gross_cap"]), field="risk_limits.gross_cap"),
        initial_margin_ratio=require_decimal_string(
            str(value["initial_margin_ratio"]), field="risk_limits.initial_margin_ratio"
        ),
        operational_reserve=require_decimal_string(
            str(value["operational_reserve"]), field="risk_limits.operational_reserve"
        ),
    )
    policy.validate()
    return policy


def _parse_planner_policy(value: Mapping[str, Any]) -> PlannerPolicy:
    required = {
        "spot_max_slippage_bps",
        "perp_max_slippage_bps",
        "intent_ttl_seconds",
        "minimum_quantity",
    }
    if set(value) != required:
        raise ContractError("scheduler planner_policy schema mismatch")
    if isinstance(value["intent_ttl_seconds"], bool):
        raise ContractError("planner_policy.intent_ttl_seconds must be an integer")
    policy = PlannerPolicy(
        spot_max_slippage_bps=require_decimal_string(
            str(value["spot_max_slippage_bps"]),
            field="planner_policy.spot_max_slippage_bps",
        ),
        perp_max_slippage_bps=require_decimal_string(
            str(value["perp_max_slippage_bps"]),
            field="planner_policy.perp_max_slippage_bps",
        ),
        intent_ttl_seconds=int(value["intent_ttl_seconds"]),
        minimum_quantity=require_decimal_string(
            str(value["minimum_quantity"]), field="planner_policy.minimum_quantity"
        ),
    )
    policy.validate()
    return policy


def _parse_broker_policy(value: Mapping[str, Any]) -> PaperBrokerPolicy:
    required = {
        "spot_commission_bps",
        "perp_commission_bps",
        "proxy_half_spread_bps",
        "impact_bps",
        "participation_rate",
        "permit_stale_quotes",
    }
    if set(value) != required:
        raise ContractError("scheduler broker_policy schema mismatch")
    policy = PaperBrokerPolicy(
        spot_commission_bps=require_decimal_string(
            str(value["spot_commission_bps"]), field="broker_policy.spot_commission_bps"
        ),
        perp_commission_bps=require_decimal_string(
            str(value["perp_commission_bps"]), field="broker_policy.perp_commission_bps"
        ),
        proxy_half_spread_bps=require_decimal_string(
            str(value["proxy_half_spread_bps"]),
            field="broker_policy.proxy_half_spread_bps",
        ),
        impact_bps=require_decimal_string(
            str(value["impact_bps"]), field="broker_policy.impact_bps"
        ),
        participation_rate=require_decimal_string(
            str(value["participation_rate"]), field="broker_policy.participation_rate"
        ),
        permit_stale_quotes=require_bool(
            value["permit_stale_quotes"], "broker_policy.permit_stale_quotes"
        ),
    )
    policy.validate()
    return policy


@dataclass(frozen=True, slots=True)
class PaperCycleEnvelope:
    """Fully sealed request for one deterministic paper cycle.

    The request embeds the exact starting account and every execution policy. A software
    update cannot silently change an already queued cycle, and a crash after the cycle
    commit can be recovered from the same envelope.
    """

    schema_version: str
    request_id: str
    created_at_utc: str
    not_before_utc: str
    expires_at_utc: str
    strategy_id: str
    expected_account_hash: str
    expected_account_sequence: int
    starting_account: Mapping[str, Any]
    market_snapshot: Mapping[str, Any]
    strategy_snapshot: Mapping[str, Any]
    quotes: Sequence[Mapping[str, Any]]
    reference_prices: Mapping[str, Mapping[str, object]]
    critical_sources: Sequence[str]
    onchain_sources: Sequence[str]
    modelled_cost: str
    modelled_slippage_bps: str
    source_hash_match: bool
    data_stale: bool
    risk_limits: Mapping[str, Any]
    planner_policy: Mapping[str, Any]
    broker_policy: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        created_at_utc: str,
        not_before_utc: str,
        expires_at_utc: str,
        account: PaperAccountState,
        market_snapshot: MarketSnapshot,
        strategy_snapshot: StrategySnapshot,
        quotes: Sequence[Any],
        reference_prices: Mapping[str, Mapping[str, object]],
        critical_sources: Sequence[str],
        onchain_sources: Sequence[str] = (),
        modelled_cost: str = "0",
        modelled_slippage_bps: str = "0",
        source_hash_match: bool = True,
        data_stale: bool = False,
        risk_limits: RiskLimits = DEFAULT_RISK_LIMITS,
        planner_policy: PlannerPolicy = DEFAULT_PLANNER_POLICY,
        broker_policy: PaperBrokerPolicy = DEFAULT_PAPER_BROKER_POLICY,
    ) -> "PaperCycleEnvelope":
        account.validate()
        market_snapshot.validate()
        strategy_snapshot.validate()
        provisional = cls(
            schema_version="2.0",
            request_id="sha256:" + "0" * 64,
            created_at_utc=format_utc(created_at_utc),
            not_before_utc=format_utc(not_before_utc),
            expires_at_utc=format_utc(expires_at_utc),
            strategy_id=strategy_snapshot.strategy_id,
            expected_account_hash=account.account_hash,
            expected_account_sequence=account.sequence,
            starting_account=account.to_dict(),
            market_snapshot=market_snapshot.to_dict(),
            strategy_snapshot=strategy_snapshot.to_dict(),
            quotes=tuple(asdict_or_mapping(item) for item in quotes),
            reference_prices={side: dict(values) for side, values in reference_prices.items()},
            critical_sources=tuple(str(item) for item in critical_sources),
            onchain_sources=tuple(str(item) for item in onchain_sources),
            modelled_cost=str(modelled_cost),
            modelled_slippage_bps=str(modelled_slippage_bps),
            source_hash_match=bool(source_hash_match),
            data_stale=bool(data_stale),
            risk_limits=_risk_payload(risk_limits),
            planner_policy=_planner_payload(planner_policy),
            broker_policy=_broker_payload(broker_policy),
        )
        result = replace(provisional, request_id=sha256_id(provisional.identity_payload()))
        result.validate()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PaperCycleEnvelope":
        if set(value) != _ENVELOPE_FIELDS:
            missing = sorted(_ENVELOPE_FIELDS - set(value))
            extra = sorted(set(value) - _ENVELOPE_FIELDS)
            raise ContractError(
                f"scheduler envelope schema mismatch; missing={missing}, extra={extra}"
            )
        sequence = value["expected_account_sequence"]
        if isinstance(sequence, bool):
            raise ContractError("expected_account_sequence must be an integer")
        envelope = cls(
            schema_version=str(value["schema_version"]),
            request_id=str(value["request_id"]),
            created_at_utc=str(value["created_at_utc"]),
            not_before_utc=str(value["not_before_utc"]),
            expires_at_utc=str(value["expires_at_utc"]),
            strategy_id=str(value["strategy_id"]),
            expected_account_hash=str(value["expected_account_hash"]),
            expected_account_sequence=int(sequence),
            starting_account=dict(require_mapping(value["starting_account"], "starting_account")),
            market_snapshot=dict(require_mapping(value["market_snapshot"], "market_snapshot")),
            strategy_snapshot=dict(require_mapping(value["strategy_snapshot"], "strategy_snapshot")),
            quotes=tuple(
                dict(require_mapping(item, "quote"))
                for item in require_sequence(value["quotes"], "quotes")
            ),
            reference_prices={
                str(side): dict(require_mapping(items, f"reference_prices.{side}"))
                for side, items in require_mapping(
                    value["reference_prices"], "reference_prices"
                ).items()
            },
            critical_sources=tuple(
                str(item)
                for item in require_sequence(value["critical_sources"], "critical_sources")
            ),
            onchain_sources=tuple(
                str(item)
                for item in require_sequence(value["onchain_sources"], "onchain_sources")
            ),
            modelled_cost=str(value["modelled_cost"]),
            modelled_slippage_bps=str(value["modelled_slippage_bps"]),
            source_hash_match=require_bool(value["source_hash_match"], "source_hash_match"),
            data_stale=require_bool(value["data_stale"], "data_stale"),
            risk_limits=dict(require_mapping(value["risk_limits"], "risk_limits")),
            planner_policy=dict(require_mapping(value["planner_policy"], "planner_policy")),
            broker_policy=dict(require_mapping(value["broker_policy"], "broker_policy")),
        )
        envelope.validate()
        return envelope

    def identity_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("request_id")
        return value

    def validate(self) -> None:
        if self.schema_version != "2.0":
            raise ContractError("unsupported scheduler envelope schema")
        require_sha256(self.request_id, field="request_id")
        require_sha256(self.expected_account_hash, field="expected_account_hash")
        if self.expected_account_sequence < 0:
            raise ContractError("expected_account_sequence must be non-negative")
        created = parse_utc(self.created_at_utc)
        not_before = parse_utc(self.not_before_utc)
        expires = parse_utc(self.expires_at_utc)
        if not_before < created:
            raise ContractError("not_before_utc cannot precede created_at_utc")
        if expires <= not_before:
            raise ContractError("expires_at_utc must be after not_before_utc")
        if expires - created > _MAX_REQUEST_TTL:
            raise ContractError("scheduler request TTL exceeds seven days")
        if not self.strategy_id:
            raise ContractError("scheduler strategy_id is required")
        if not self.critical_sources:
            raise ContractError("scheduler request requires critical_sources")
        if len(set(self.critical_sources)) != len(tuple(self.critical_sources)):
            raise ContractError("scheduler critical_sources must be unique")
        if len(set(self.onchain_sources)) != len(tuple(self.onchain_sources)):
            raise ContractError("scheduler onchain_sources must be unique")

        account = parse_paper_account(self.starting_account)
        market = parse_market_snapshot(self.market_snapshot)
        strategy = parse_strategy_snapshot(self.strategy_snapshot)
        quotes = parse_paper_quotes(self.quotes)
        parse_reference_prices(self.reference_prices)
        _parse_risk_policy(self.risk_limits)
        _parse_planner_policy(self.planner_policy)
        _parse_broker_policy(self.broker_policy)

        if account.account_hash != self.expected_account_hash:
            raise ContractError("embedded account hash does not match expected_account_hash")
        if account.sequence != self.expected_account_sequence:
            raise ContractError("embedded account sequence does not match expected_account_sequence")
        if account.strategy_id != self.strategy_id or strategy.strategy_id != self.strategy_id:
            raise ContractError("scheduler strategy identity mismatch")
        if strategy.market_snapshot_id != market.snapshot_id:
            raise ContractError("scheduler snapshots are not linked")
        if created < parse_utc(market.decision_time_utc):
            raise ContractError("scheduler envelope was created before the market decision")
        for source in self.critical_sources:
            if source not in market.sources:
                raise ContractError(f"critical source is absent from market snapshot: {source}")
        for source in self.onchain_sources:
            if source not in market.sources:
                raise ContractError(f"onchain source is absent from market snapshot: {source}")
        for quote in quotes:
            if parse_utc(quote.observed_at_utc) < parse_utc(market.decision_time_utc):
                raise ContractError("paper quote predates the cycle decision time")
        require_decimal_string(self.modelled_cost, field="modelled_cost", minimum=Decimal("0"))
        require_decimal_string(
            self.modelled_slippage_bps,
            field="modelled_slippage_bps",
            minimum=Decimal("0"),
        )
        if self.request_id != sha256_id(self.identity_payload()):
            raise ContractError("scheduler request_id hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def starting_account_state(self) -> PaperAccountState:
        return parse_paper_account(self.starting_account)

    def to_request(self) -> PaperCycleRequest:
        return PaperCycleRequest(
            market_snapshot=parse_market_snapshot(self.market_snapshot),
            strategy_snapshot=parse_strategy_snapshot(self.strategy_snapshot),
            starting_account=self.starting_account_state(),
            quotes=parse_paper_quotes(self.quotes),
            reference_prices=parse_reference_prices(self.reference_prices),
            critical_sources=tuple(self.critical_sources),
            onchain_sources=tuple(self.onchain_sources),
            modelled_cost=self.modelled_cost,
            modelled_slippage_bps=self.modelled_slippage_bps,
            source_hash_match=self.source_hash_match,
            data_stale=self.data_stale,
            risk_limits=_parse_risk_policy(self.risk_limits),
            planner_policy=_parse_planner_policy(self.planner_policy),
            broker_policy=_parse_broker_policy(self.broker_policy),
        )


@dataclass(frozen=True, slots=True)
class SchedulerPaths:
    runtime_root: Path
    root: Path
    inbox: Path
    processing: Path
    completed: Path
    rejected: Path
    status: Path
    events: Path
    lock: Path
    daemon_lock: Path

    @classmethod
    def under(cls, runtime_root: str | Path) -> "SchedulerPaths":
        runtime = Path(runtime_root).expanduser().resolve()
        root = runtime / ".scheduler"
        return cls(
            runtime_root=runtime,
            root=root,
            inbox=root / "inbox",
            processing=root / "processing",
            completed=root / "completed",
            rejected=root / "rejected",
            status=root / "status.json",
            events=root / "events.jsonl",
            lock=root / ".scheduler.lock",
            daemon_lock=root / ".daemon.lock",
        )

    def ensure(self) -> None:
        for path in (
            self.runtime_root,
            self.root,
            self.inbox,
            self.processing,
            self.completed,
            self.rejected,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class SchedulerRunResult:
    processed: int
    completed: int
    halted: int
    rejected: int
    blocked: int
    last_request_id: str | None
    last_status: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return format_utc(value)


def load_envelope(path: str | Path) -> PaperCycleEnvelope:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid scheduler envelope: {source}") from exc
    if not isinstance(value, Mapping):
        raise ContractError("scheduler envelope must be an object")
    return PaperCycleEnvelope.from_dict(value)


def _request_name(request_id: str) -> str:
    return request_id.removeprefix("sha256:") + ".json"


def _archive_name(request_id: str, suffix: str) -> str:
    return request_id.removeprefix("sha256:") + suffix


def _move_immutable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.read_bytes() != destination.read_bytes():
            raise ContractError(f"scheduler archive conflict: {destination}")
        source.unlink(missing_ok=True)
        return
    os.replace(source, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _count(paths: SchedulerPaths) -> dict[str, int]:
    return {
        "queued": sum(1 for path in paths.inbox.glob("*.json") if path.is_file()),
        "processing": sum(1 for path in paths.processing.glob("*.json") if path.is_file()),
        "completed": sum(
            1 for path in paths.completed.glob("*.result.json") if path.is_file()
        ),
        "rejected": sum(
            1 for path in paths.rejected.glob("*.error.json") if path.is_file()
        ),
    }


def _event_sequence(paths: SchedulerPaths) -> int:
    events = AppendOnlyJournal(paths.events).verify()
    return max((int(item["sequence"]) for item in events), default=0) + 1


def _scheduler_event(
    paths: SchedulerPaths,
    *,
    event_type: str,
    timestamp: datetime,
    payload: Mapping[str, Any],
) -> None:
    # All callers hold ``paths.lock``; this makes the event sequence process-safe.
    AppendOnlyJournal(paths.events).append(
        event_type=event_type,
        event_time_utc=_iso(timestamp),
        strategy_id="fin-paper-scheduler",
        sequence=_event_sequence(paths),
        payload=dict(payload),
    )


def scheduler_status(paths: SchedulerPaths) -> dict[str, Any]:
    paths.ensure()
    counts = _count(paths)
    previous: dict[str, Any] = {}
    if paths.status.is_file():
        try:
            value = json.loads(paths.status.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                previous = value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            previous = {}
    return {
        "schema_version": 1,
        "service": "fin-paper-scheduler",
        "generated_at_utc": _iso(utc_now()),
        "runtime_root": str(paths.runtime_root),
        "spool_root": str(paths.root),
        "state": previous.get("state", "idle"),
        **counts,
        "last_request_id": previous.get("last_request_id"),
        "last_result": previous.get("last_result"),
        "last_error": previous.get("last_error"),
        "heartbeat_sequence": int(previous.get("heartbeat_sequence", 0)),
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }


def _write_status(
    paths: SchedulerPaths,
    *,
    state: str,
    last_request_id: str | None = None,
    last_result: Mapping[str, Any] | None = None,
    last_error: str | None = None,
    preserve_error: bool = False,
) -> dict[str, Any]:
    current = scheduler_status(paths)
    value = {
        **current,
        "generated_at_utc": _iso(utc_now()),
        "state": state,
        "last_request_id": last_request_id,
        "last_result": dict(last_result) if last_result is not None else current.get("last_result"),
        "last_error": current.get("last_error") if preserve_error else last_error,
        "heartbeat_sequence": int(current.get("heartbeat_sequence", 0)) + 1,
    }
    value.update(_count(paths))
    write_atomic_json(paths.status, value)
    return value


def enqueue_envelope(
    paths: SchedulerPaths,
    envelope: PaperCycleEnvelope,
    *,
    lock_timeout_seconds: float = 30.0,
) -> Path:
    envelope.validate()
    paths.ensure()
    with exclusive_file_lock(paths.lock, timeout_seconds=lock_timeout_seconds):
        completed_result = paths.completed / _archive_name(
            envelope.request_id, ".result.json"
        )
        rejected_result = paths.rejected / _archive_name(
            envelope.request_id, ".error.json"
        )
        if completed_result.exists() or rejected_result.exists():
            return completed_result if completed_result.exists() else rejected_result
        destination = paths.inbox / _request_name(envelope.request_id)
        if destination.exists():
            write_once_json(destination, envelope.to_dict())
            return destination
        write_once_json(destination, envelope.to_dict())
        now = utc_now()
        _scheduler_event(
            paths,
            event_type="REQUEST_ENQUEUED",
            timestamp=now,
            payload={
                "request_id": envelope.request_id,
                "strategy_id": envelope.strategy_id,
                "expected_account_hash": envelope.expected_account_hash,
            },
        )
        _write_status(
            paths,
            state="idle",
            last_request_id=envelope.request_id,
            last_result={"status": "queued", "path": str(destination)},
        )
        return destination


def _reject(
    paths: SchedulerPaths,
    source: Path,
    *,
    request_id: str,
    strategy_id: str | None,
    reason: str,
    now: datetime,
    parseable_request: bool = True,
) -> None:
    suffix = ".request.json" if parseable_request else ".request.raw"
    destination = paths.rejected / _archive_name(request_id, suffix)
    if source.exists():
        _move_immutable(source, destination)
    error = {
        "schema_version": 1,
        "request_id": request_id,
        "strategy_id": strategy_id,
        "rejected_at_utc": _iso(now),
        "reason": reason,
        "request_artifact": destination.name if destination.exists() else None,
        "exchange_submission_available": False,
    }
    write_once_json(paths.rejected / _archive_name(request_id, ".error.json"), error)
    _scheduler_event(
        paths,
        event_type="REQUEST_REJECTED",
        timestamp=now,
        payload=error,
    )


def _safe_file_id(source: Path) -> str:
    try:
        payload = source.read_bytes()
    except OSError:
        payload = str(source).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _committed_cycle_path(paths: SchedulerPaths, envelope: PaperCycleEnvelope) -> Path:
    cycle_id = cycle_id_for(envelope.to_request())
    return (
        PaperCyclePaths.under(paths.runtime_root, envelope.strategy_id).root
        / "cycles"
        / cycle_id.removeprefix("sha256:")
        / "COMMITTED.json"
    )


def _due_candidates(
    paths: SchedulerPaths, now: datetime
) -> list[tuple[int, datetime, datetime, str, Path, PaperCycleEnvelope]]:
    candidates: list[tuple[int, datetime, datetime, str, Path, PaperCycleEnvelope]] = []
    for priority, folder in ((0, paths.processing), (1, paths.inbox)):
        for source in sorted(folder.glob("*.json")):
            try:
                envelope = load_envelope(source)
            except ContractError as exc:
                _reject(
                    paths,
                    source,
                    request_id=_safe_file_id(source),
                    strategy_id=None,
                    reason=str(exc),
                    now=now,
                    parseable_request=False,
                )
                continue
            not_before = parse_utc(envelope.not_before_utc)
            if not_before > now:
                continue
            if parse_utc(envelope.expires_at_utc) <= now:
                _reject(
                    paths,
                    source,
                    request_id=envelope.request_id,
                    strategy_id=envelope.strategy_id,
                    reason="scheduler request expired before execution",
                    now=now,
                )
                continue
            candidates.append(
                (
                    priority,
                    not_before,
                    parse_utc(envelope.created_at_utc),
                    envelope.request_id,
                    source,
                    envelope,
                )
            )
    candidates.sort(key=lambda item: item[:4])
    return candidates


def _claim_next(
    paths: SchedulerPaths,
    now: datetime,
) -> tuple[Path, PaperCycleEnvelope] | None:
    for priority, _not_before, _created, _request_id, source, envelope in _due_candidates(
        paths, now
    ):
        # If the deterministic cycle already committed before a scheduler crash, claim it
        # for archival recovery even when the global account has advanced to final state.
        committed = _committed_cycle_path(paths, envelope)
        if committed.is_file():
            if priority == 1:
                destination = paths.processing / source.name
                os.replace(source, destination)
                source = destination
            return source, envelope

        account_path = PaperCyclePaths.under(
            paths.runtime_root, envelope.strategy_id
        ).account_state
        if not account_path.is_file():
            continue
        try:
            account = parse_paper_account(
                json.loads(account_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
            _reject(
                paths,
                source,
                request_id=envelope.request_id,
                strategy_id=envelope.strategy_id,
                reason=f"runtime account is invalid: {exc}",
                now=now,
            )
            continue
        if account.account_hash == envelope.expected_account_hash:
            if priority == 1:
                destination = paths.processing / source.name
                os.replace(source, destination)
                source = destination
            return source, envelope
        if account.sequence < envelope.expected_account_sequence:
            continue
        _reject(
            paths,
            source,
            request_id=envelope.request_id,
            strategy_id=envelope.strategy_id,
            reason="scheduler request references a stale or divergent account",
            now=now,
        )
    return None


def _complete(
    paths: SchedulerPaths,
    source: Path,
    envelope: PaperCycleEnvelope,
    result: Mapping[str, Any],
    now: datetime,
) -> None:
    request_destination = paths.completed / _archive_name(
        envelope.request_id, ".request.json"
    )
    _move_immutable(source, request_destination)
    payload = {
        "schema_version": 1,
        "request_id": envelope.request_id,
        "strategy_id": envelope.strategy_id,
        "completed_at_utc": _iso(now),
        **dict(result),
        "exchange_submission_available": False,
        "live_ready": False,
    }
    write_once_json(
        paths.completed / _archive_name(envelope.request_id, ".result.json"), payload
    )
    _scheduler_event(
        paths,
        event_type="REQUEST_COMPLETED",
        timestamp=now,
        payload=payload,
    )


def run_scheduler_once(
    paths: SchedulerPaths,
    *,
    max_items: int = 1,
    now: datetime | None = None,
    lock_timeout_seconds: float = 30.0,
) -> SchedulerRunResult:
    if max_items < 1:
        raise ValueError("max_items must be positive")
    paths.ensure()
    current = (now or utc_now()).astimezone(timezone.utc)
    processed = completed = halted = rejected = blocked = 0
    last_request_id: str | None = None
    last_status = "idle"
    last_error: str | None = None
    with exclusive_file_lock(paths.lock, timeout_seconds=lock_timeout_seconds):
        _write_status(paths, state="running")
        for _ in range(max_items):
            before_rejected = _count(paths)["rejected"]
            claimed = _claim_next(paths, current)
            after_rejected = _count(paths)["rejected"]
            rejected += max(0, after_rejected - before_rejected)
            if claimed is None:
                counts = _count(paths)
                blocked = counts["queued"] + counts["processing"]
                break
            source, envelope = claimed
            processed += 1
            last_request_id = envelope.request_id
            try:
                request = envelope.to_request()
                cycle_result = run_paper_cycle(
                    request=request,
                    paths=PaperCyclePaths.under(paths.runtime_root, envelope.strategy_id),
                    lock_timeout_seconds=lock_timeout_seconds,
                )
                result = asdict(cycle_result)
                _complete(paths, source, envelope, result, current)
                completed += 1
                if cycle_result.status == "halt":
                    halted += 1
                last_status = cycle_result.status
                _write_status(
                    paths,
                    state="warn" if cycle_result.status == "halt" else "running",
                    last_request_id=envelope.request_id,
                    last_result=result,
                )
            except LockUnavailableError:
                if source.parent == paths.processing:
                    os.replace(source, paths.inbox / source.name)
                last_status = "blocked"
                blocked += 1
                break
            except Exception as exc:  # fail closed and preserve evidence for review.
                last_error = f"{type(exc).__name__}: {exc}"
                _reject(
                    paths,
                    source,
                    request_id=envelope.request_id,
                    strategy_id=envelope.strategy_id,
                    reason=last_error,
                    now=current,
                )
                rejected += 1
                last_status = "rejected"
                _write_status(
                    paths,
                    state="halt",
                    last_request_id=envelope.request_id,
                    last_result={"status": "rejected"},
                    last_error=last_error,
                )
        final_state = "halt" if rejected else ("warn" if halted else "idle")
        _write_status(
            paths,
            state=final_state,
            last_request_id=last_request_id,
            last_result={
                "processed": processed,
                "completed": completed,
                "halted": halted,
                "rejected": rejected,
                "blocked": blocked,
            },
            last_error=last_error,
        )
    return SchedulerRunResult(
        processed=processed,
        completed=completed,
        halted=halted,
        rejected=rejected,
        blocked=blocked,
        last_request_id=last_request_id,
        last_status=last_status,
    )


def _verify_result(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"scheduler result must be an object: {path}")
    request_id = require_sha256(str(value.get("request_id")), field="request_id")
    if path.name != _archive_name(request_id, ".result.json"):
        raise ContractError(f"scheduler result filename mismatch: {path}")
    return request_id


def _verify_error(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"scheduler rejection must be an object: {path}")
    request_id = require_sha256(str(value.get("request_id")), field="request_id")
    if path.name != _archive_name(request_id, ".error.json"):
        raise ContractError(f"scheduler rejection filename mismatch: {path}")
    artifact = value.get("request_artifact")
    if artifact:
        request_path = path.parent / str(artifact)
        if not request_path.is_file():
            raise ContractError(f"scheduler rejected request artifact is missing: {artifact}")
        if request_path.name.endswith(".request.raw"):
            actual = "sha256:" + hashlib.sha256(request_path.read_bytes()).hexdigest()
            if actual != request_id:
                raise ContractError("rejected raw request hash mismatch")
        else:
            envelope = load_envelope(request_path)
            if envelope.request_id != request_id:
                raise ContractError("rejected request id mismatch")
    return request_id


def verify_scheduler(paths: SchedulerPaths) -> dict[str, Any]:
    paths.ensure()
    events = AppendOnlyJournal(paths.events).verify()
    verified_envelopes = 0
    for folder in (paths.inbox, paths.processing):
        for path in folder.glob("*.json"):
            load_envelope(path)
            verified_envelopes += 1
    completed_ids: set[str] = set()
    for result_path in paths.completed.glob("*.result.json"):
        request_id = _verify_result(result_path)
        request_path = paths.completed / _archive_name(request_id, ".request.json")
        envelope = load_envelope(request_path)
        if envelope.request_id != request_id:
            raise ContractError("completed request id mismatch")
        completed_ids.add(request_id)
        verified_envelopes += 1
    rejected_ids = {_verify_error(path) for path in paths.rejected.glob("*.error.json")}
    if completed_ids & rejected_ids:
        raise ContractError("scheduler request exists in completed and rejected archives")
    if paths.status.is_file():
        status = json.loads(paths.status.read_text(encoding="utf-8"))
        if not isinstance(status, dict) or status.get("service") != "fin-paper-scheduler":
            raise ContractError("invalid scheduler status artifact")
    return {
        "valid": True,
        "verified_envelopes": verified_envelopes,
        "scheduler_events": len(events),
        **_count(paths),
        "exchange_submission_available": False,
        "live_ready": False,
    }


def serve_scheduler(
    paths: SchedulerPaths,
    *,
    poll_seconds: float = 5.0,
    max_items_per_pass: int = 10,
    lock_timeout_seconds: float = 30.0,
) -> int:
    if poll_seconds < 0.2:
        raise ValueError("poll_seconds must be at least 0.2")
    stop = Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)

    paths.ensure()
    try:
        with exclusive_file_lock(paths.daemon_lock, timeout_seconds=0.0):
            with exclusive_file_lock(paths.lock, timeout_seconds=lock_timeout_seconds):
                _scheduler_event(
                    paths,
                    event_type="SCHEDULER_STARTED",
                    timestamp=utc_now(),
                    payload={"pid": os.getpid(), "poll_seconds": poll_seconds},
                )
                _write_status(paths, state="running")
            while not stop.is_set():
                try:
                    run_scheduler_once(
                        paths,
                        max_items=max_items_per_pass,
                        lock_timeout_seconds=lock_timeout_seconds,
                    )
                except Exception as exc:  # keep the daemon observable but fail closed.
                    detail = f"{type(exc).__name__}: {exc}"
                    with exclusive_file_lock(paths.lock, timeout_seconds=lock_timeout_seconds):
                        _write_status(paths, state="halt", last_error=detail)
                        try:
                            _scheduler_event(
                                paths,
                                event_type="SCHEDULER_LOOP_FAILED",
                                timestamp=utc_now(),
                                payload={"pid": os.getpid(), "error": detail},
                            )
                        except Exception:
                            pass
                stop.wait(poll_seconds)
            with exclusive_file_lock(paths.lock, timeout_seconds=lock_timeout_seconds):
                _scheduler_event(
                    paths,
                    event_type="SCHEDULER_STOPPED",
                    timestamp=utc_now(),
                    payload={"pid": os.getpid()},
                )
                _write_status(paths, state="stopped")
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0
