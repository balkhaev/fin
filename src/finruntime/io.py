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


def load_market_snapshot(path: str | Path) -> MarketSnapshot:
    raw = require_object(load_json(path), label="MarketSnapshot")
    source_map = raw.get("sources")
    if not isinstance(source_map, Mapping):
        raise ContractError("MarketSnapshot.sources must be an object")
    raw = dict(raw)
    raw["sources"] = {
        str(name): SourceObservation(**require_object(value, label=f"source {name}"))
        for name, value in source_map.items()
    }
    snapshot = MarketSnapshot(**raw)
    snapshot.validate()
    return snapshot


def load_strategy_snapshot(path: str | Path) -> StrategySnapshot:
    raw = require_object(load_json(path), label="StrategySnapshot")
    snapshot = StrategySnapshot(**raw)
    snapshot.validate()
    return snapshot


def load_paper_account(path: str | Path) -> PaperAccountState:
    raw = require_object(load_json(path), label="PaperAccountState")
    state = PaperAccountState(**raw)
    state.validate()
    return state


def load_paper_quotes(path: str | Path) -> tuple[PaperQuote, ...]:
    raw = load_json(path)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ContractError("paper quotes must be a JSON array")
    quotes: list[PaperQuote] = []
    for number, item in enumerate(raw):
        quote = PaperQuote(**require_object(item, label=f"paper quote {number}"))
        quote.validate()
        quotes.append(quote)
    return tuple(quotes)


def load_reference_prices(path: str | Path) -> dict[str, dict[str, object]]:
    raw = require_object(load_json(path), label="reference price book")
    unsupported = set(raw) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported reference-price sections: {sorted(unsupported)}")
    output: dict[str, dict[str, object]] = {"spot": {}, "perp": {}}
    for market_type in ("spot", "perp"):
        side = raw.get(market_type, {})
        if not isinstance(side, Mapping):
            raise ContractError(f"reference prices {market_type!r} must be an object")
        output[market_type] = {str(key): value for key, value in side.items()}
    return output


def object_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        result = value.to_dict()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ContractError("runtime object did not serialize to an object")
    return result
