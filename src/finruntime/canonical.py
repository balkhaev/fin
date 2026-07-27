from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

UTC = timezone.utc
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class ContractError(ValueError):
    """Raised when a runtime object violates a frozen contract."""


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if not value.endswith("Z"):
            raise ContractError(f"UTC timestamp must end with Z: {value!r}")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ContractError(f"invalid UTC timestamp: {value!r}") from exc
    else:
        raise ContractError(f"expected UTC timestamp, got {type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ContractError(f"timestamp must be timezone-aware UTC: {value!r}")
    return parsed.astimezone(UTC)


def format_utc(value: str | datetime) -> str:
    parsed = parse_utc(value)
    if parsed.microsecond:
        text = parsed.isoformat(timespec="microseconds")
    else:
        text = parsed.isoformat(timespec="seconds")
    return text.replace("+00:00", "Z")


def require_sha256(value: str, *, field: str = "hash") -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ContractError(f"{field} must be a lowercase SHA-256 hex digest")
    return value if value.startswith("sha256:") else f"sha256:{value}"


def require_decimal_string(
    value: str,
    *,
    field: str,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
        raise ContractError(f"{field} must be a plain decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError(f"{field} is not a valid decimal") from exc
    if not number.is_finite():
        raise ContractError(f"{field} must be finite")
    if minimum is not None and number < minimum:
        raise ContractError(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ContractError(f"{field} must be <= {maximum}")
    return number


def normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return normalize(dataclasses.asdict(value))
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError("NaN and Infinity are forbidden in canonical JSON")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise ContractError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def sha256_hex(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def sha256_id(value: Any) -> str:
    return f"sha256:{sha256_hex(canonical_json_bytes(value))}"
