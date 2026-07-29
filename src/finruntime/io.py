from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from finruntime.canonical import ContractError
from finruntime.execution.paper_broker import PaperQuote
from finruntime.models import MarketSnapshot, SourceObservation, StrategySnapshot
from finruntime.portfolio.accounting import PaperAccountState


def load_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid runtime JSON: {source}") from exc
    return value


def require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def parse_market_snapshot(value: Any) -> MarketSnapshot:
    raw = require_object(value, label="MarketSnapshot")
    source_map = raw.get("sources")
    if not isinstance(source_map, Mapping):
        raise ContractError("MarketSnapshot.sources must be an object")
    normalized = dict(raw)
    normalized["sources"] = {
        str(name): SourceObservation(**require_object(item, label=f"source {name}"))
        for name, item in source_map.items()
    }
    snapshot = MarketSnapshot(**normalized)
    snapshot.validate()
    return snapshot


def load_market_snapshot(path: str | Path) -> MarketSnapshot:
    return parse_market_snapshot(load_json(path))


def parse_strategy_snapshot(value: Any) -> StrategySnapshot:
    snapshot = StrategySnapshot(**require_object(value, label="StrategySnapshot"))
    snapshot.validate()
    return snapshot


def load_strategy_snapshot(path: str | Path) -> StrategySnapshot:
    return parse_strategy_snapshot(load_json(path))


def parse_paper_account(value: Any) -> PaperAccountState:
    state = PaperAccountState(**require_object(value, label="PaperAccountState"))
    state.validate()
    return state


def load_paper_account(path: str | Path) -> PaperAccountState:
    return parse_paper_account(load_json(path))


def parse_paper_quotes(value: Any) -> tuple[PaperQuote, ...]:
    raw = value
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ContractError("paper quotes must be a JSON array")
    quotes: list[PaperQuote] = []
    for number, item in enumerate(raw):
        quote = PaperQuote(**require_object(item, label=f"paper quote {number}"))
        quote.validate()
        quotes.append(quote)
    return tuple(quotes)


def load_paper_quotes(path: str | Path) -> tuple[PaperQuote, ...]:
    return parse_paper_quotes(load_json(path))


def parse_reference_prices(value: Any) -> dict[str, dict[str, object]]:
    raw = require_object(value, label="reference price book")
    unsupported = set(raw) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported reference-price sections: {sorted(unsupported)}")
    output: dict[str, dict[str, object]] = {"spot": {}, "perp": {}}
    for market_type in ("spot", "perp"):
        side = raw.get(market_type, {})
        if not isinstance(side, Mapping):
            raise ContractError(f"reference prices {market_type!r} must be an object")
        output[market_type] = {str(key): item for key, item in side.items()}
    return output


def load_reference_prices(path: str | Path) -> dict[str, dict[str, object]]:
    return parse_reference_prices(load_json(path))


def object_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        result = value.to_dict()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ContractError("runtime object did not serialize to an object")
    return result
