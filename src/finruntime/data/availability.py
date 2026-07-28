from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Mapping

from finruntime.canonical import ContractError, parse_utc
from finruntime.models import MarketSnapshot, SourceObservation


@dataclass(frozen=True, slots=True)
class AvailabilityDecision:
    risk_increase_permitted: bool
    accelerator_permitted: bool
    blocking_reasons: tuple[str, ...]
    quality_flags: tuple[str, ...]


def seal_sources(observations: Iterable[SourceObservation]) -> dict[str, SourceObservation]:
    sealed: dict[str, SourceObservation] = {}
    for observation in observations:
        existing = sealed.get(observation.source)
        if existing is not None and existing.payload_sha256 != observation.payload_sha256:
            raise ContractError(
                f"conflicting payload hashes for source {observation.source!r}"
            )
        sealed[observation.source] = observation
    if not sealed:
        raise ContractError("at least one source observation is required")
    return sealed


def evaluate_availability(
    snapshot: MarketSnapshot,
    *,
    critical_sources: Iterable[str],
    onchain_sources: Iterable[str] = (),
    onchain_stale_after_hours: int = 48,
) -> AvailabilityDecision:
    snapshot.validate()
    critical = set(critical_sources)
    onchain = set(onchain_sources)
    decision_time = parse_utc(snapshot.decision_time_utc)
    blocking: list[str] = []
    flags: list[str] = list(snapshot.quality_flags)
    accelerator_permitted = True

    for source in sorted(critical):
        observation = snapshot.sources.get(source)
        if observation is None:
            blocking.append(f"missing critical source:{source}")
            continue
        if observation.quality in {"missing", "future", "invalid", "stale"}:
            blocking.append(f"critical source {source}:{observation.quality}")

    stale_limit = timedelta(hours=onchain_stale_after_hours)
    for source in sorted(onchain):
        observation = snapshot.sources.get(source)
        if observation is None:
            accelerator_permitted = False
            flags.append(f"onchain_missing:{source}")
            continue
        source_time = parse_utc(observation.source_timestamp_utc)
        if observation.quality != "ok" or decision_time - source_time > stale_limit:
            accelerator_permitted = False
            flags.append(f"onchain_stale:{source}")

    return AvailabilityDecision(
        risk_increase_permitted=not blocking,
        accelerator_permitted=accelerator_permitted,
        blocking_reasons=tuple(blocking),
        quality_flags=tuple(sorted(set(flags))),
    )


def validate_source_map(
    sources: Mapping[str, SourceObservation],
    *,
    decision_time_utc: str,
) -> None:
    for name, observation in sources.items():
        if name != observation.source:
            raise ContractError(f"source map key mismatch: {name!r}")
        observation.validate(decision_time_utc=decision_time_utc)
