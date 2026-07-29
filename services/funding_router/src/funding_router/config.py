from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def _number(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number")
    return float(value)


def _integer(section: Mapping[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return int(value)


def _boolean(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    poll_seconds: float = 5.0
    database_path: Path = Path("data/funding_router.sqlite3")
    snapshot_path: Path = Path("data/funding_router_snapshot.json")
    log_level: str = "INFO"
    order_book_limit: int = 50
    funding_history_limit: int = 3
    history_cache_seconds: float = 300.0
    candle_timeframe: str = "1m"
    candle_limit: int = 120


@dataclass(frozen=True, slots=True)
class RiskSettings:
    capital_usdt: float = 3_000.0
    notional_usdt: float = 1_000.0
    max_deployed_fraction: float = 0.80
    min_free_reserve_fraction: float = 0.20
    max_open_positions: int = 1
    min_current_spread_bps_8h: float = 8.0
    min_predicted_spread_bps_8h: float = 5.0
    min_expected_net_bps: float = 10.0
    exit_expected_net_bps: float = 1.0
    hold_hours: float = 24.0
    max_hold_hours: float = 72.0
    max_basis_bps: float = 35.0
    max_mark_divergence_bps: float = 75.0
    min_open_interest_usdt: float = 5_000_000.0
    allow_missing_open_interest: bool = False
    min_depth_multiple: float = 5.0
    require_predicted_confirmation: bool = True
    slippage_buffer_bps: float = 3.0
    exit_basis_buffer_bps: float = 8.0
    adverse_selection_buffer_bps: float = 3.0
    delta_tolerance_fraction: float = 0.005
    max_unhedged_seconds: float = 4.0
    max_retries: int = 3
    min_fill_fraction: float = 0.20


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    maker_timeout_seconds: float = 8.0
    order_poll_seconds: float = 0.5
    maker_offset_bps: float = 0.0
    close_retry_delay_seconds: float = 0.75
    paper_start_balance_usdt: float = 3_000.0
    paper_entry_extra_bps: float = 0.0
    paper_exit_extra_bps: float = 0.0


@dataclass(frozen=True, slots=True)
class LiveSettings:
    enabled: bool = False
    confirmation_phrase: str = "YES_I_UNDERSTAND"
    confirmation_env: str = "FUNDING_ROUTER_LIVE_CONFIRM"
    allow_external_positions: bool = False
    close_on_shutdown: bool = True
    require_balance_check: bool = True
    margin_safety_multiplier: float = 1.20


@dataclass(frozen=True, slots=True)
class ExchangeSettings:
    id: str
    exchange_class: str
    enabled: bool
    markets: tuple[str, ...]
    maker_fee_bps: float
    taker_fee_bps: float
    api_key_env: str
    secret_env: str
    password_env: str
    uid_env: str
    default_funding_interval_hours: float
    leverage: float
    sandbox: bool
    options: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def credentials(self) -> dict[str, str]:
        result: dict[str, str] = {}
        env_map = {
            "apiKey": self.api_key_env,
            "secret": self.secret_env,
            "password": self.password_env,
            "uid": self.uid_env,
        }
        for key, env_name in env_map.items():
            if env_name:
                value = os.getenv(env_name)
                if value:
                    result[key] = value
        return result


@dataclass(frozen=True, slots=True)
class Settings:
    service: ServiceSettings
    risk: RiskSettings
    execution: ExecutionSettings
    live: LiveSettings
    exchanges: tuple[ExchangeSettings, ...]
    source_path: Path

    @property
    def enabled_exchanges(self) -> tuple[ExchangeSettings, ...]:
        return tuple(exchange for exchange in self.exchanges if exchange.enabled)

    def exchange_map(self) -> dict[str, ExchangeSettings]:
        return {exchange.id: exchange for exchange in self.exchanges}

    def validate(self) -> None:
        if self.service.poll_seconds <= 0:
            raise ConfigError("service.poll_seconds must be positive")
        if self.service.order_book_limit <= 0:
            raise ConfigError("service.order_book_limit must be positive")
        if self.service.candle_limit < 2:
            raise ConfigError("service.candle_limit must be at least 2")
        if not self.service.candle_timeframe:
            raise ConfigError("service.candle_timeframe is required")
        if self.risk.capital_usdt <= 0 or self.risk.notional_usdt <= 0:
            raise ConfigError("risk capital and notional must be positive")
        if not (0 < self.risk.max_deployed_fraction <= 1):
            raise ConfigError("risk.max_deployed_fraction must be in (0, 1]")
        if not (0 <= self.risk.min_free_reserve_fraction < 1):
            raise ConfigError("risk.min_free_reserve_fraction must be in [0, 1)")
        if (
            self.risk.max_deployed_fraction
            > 1 - self.risk.min_free_reserve_fraction + 1e-12
        ):
            raise ConfigError(
                "max_deployed_fraction conflicts with min_free_reserve_fraction"
            )
        if (
            self.risk.notional_usdt
            > self.risk.capital_usdt * self.risk.max_deployed_fraction
        ):
            raise ConfigError("notional_usdt exceeds max deployed capital")
        if self.risk.hold_hours <= 0 or self.risk.max_hold_hours < self.risk.hold_hours:
            raise ConfigError("invalid hold_hours/max_hold_hours")
        if self.risk.min_depth_multiple < 1:
            raise ConfigError("min_depth_multiple must be at least 1")
        if self.risk.max_open_positions != 1:
            raise ConfigError(
                "this release intentionally supports exactly one open position"
            )
        if not (0 < self.risk.min_fill_fraction <= 1):
            raise ConfigError("min_fill_fraction must be in (0, 1]")
        if (
            self.execution.maker_timeout_seconds <= 0
            or self.execution.order_poll_seconds <= 0
        ):
            raise ConfigError("execution timeouts must be positive")
        if self.execution.order_poll_seconds > self.risk.max_unhedged_seconds:
            raise ConfigError("order_poll_seconds must not exceed max_unhedged_seconds")
        enabled = self.enabled_exchanges
        if len(enabled) < 2:
            raise ConfigError("at least two exchanges must be enabled")
        seen: set[str] = set()
        for exchange in self.exchanges:
            if not exchange.id or not exchange.exchange_class:
                raise ConfigError("exchange id and exchange_class are required")
            if exchange.id in seen:
                raise ConfigError(f"duplicate exchange id: {exchange.id}")
            seen.add(exchange.id)
            if exchange.enabled and not exchange.markets:
                raise ConfigError(f"enabled exchange {exchange.id} has no markets")
            if exchange.maker_fee_bps < 0 or exchange.taker_fee_bps < 0:
                raise ConfigError(f"negative fee for exchange {exchange.id}")
            if exchange.default_funding_interval_hours <= 0:
                raise ConfigError(
                    f"invalid funding interval for exchange {exchange.id}"
                )
            if not (1.0 <= exchange.leverage <= 5.0):
                raise ConfigError(
                    f"exchange leverage must be in [1, 5] for {exchange.id}"
                )
        if self.live.margin_safety_multiplier < 1.0:
            raise ConfigError("live.margin_safety_multiplier must be at least 1")


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a table")
    return value


def load_settings(path: str | Path) -> Settings:
    source = Path(path).expanduser().resolve()
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {source}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    service_raw = _section(raw, "service")
    risk_raw = _section(raw, "risk")
    execution_raw = _section(raw, "execution")
    live_raw = _section(raw, "live")

    database_raw = os.getenv(
        "FUNDING_ROUTER_DATABASE_PATH",
        str(service_raw.get("database_path", "data/funding_router.sqlite3")),
    )
    database_path = Path(database_raw).expanduser()
    if not database_path.is_absolute():
        database_path = (source.parent / database_path).resolve()
    snapshot_raw = os.getenv(
        "FUNDING_ROUTER_SNAPSHOT_PATH",
        str(service_raw.get("snapshot_path", "data/funding_router_snapshot.json")),
    )
    snapshot_path = Path(snapshot_raw).expanduser()
    if not snapshot_path.is_absolute():
        snapshot_path = (source.parent / snapshot_path).resolve()

    service = ServiceSettings(
        poll_seconds=_number(service_raw, "poll_seconds", 5.0),
        database_path=database_path,
        snapshot_path=snapshot_path,
        log_level=str(service_raw.get("log_level", "INFO")).upper(),
        order_book_limit=_integer(service_raw, "order_book_limit", 50),
        funding_history_limit=_integer(service_raw, "funding_history_limit", 3),
        history_cache_seconds=_number(service_raw, "history_cache_seconds", 300.0),
        candle_timeframe=str(service_raw.get("candle_timeframe", "1m")),
        candle_limit=_integer(service_raw, "candle_limit", 120),
    )
    risk = RiskSettings(
        capital_usdt=_number(risk_raw, "capital_usdt", 3_000.0),
        notional_usdt=_number(risk_raw, "notional_usdt", 1_000.0),
        max_deployed_fraction=_number(risk_raw, "max_deployed_fraction", 0.80),
        min_free_reserve_fraction=_number(risk_raw, "min_free_reserve_fraction", 0.20),
        max_open_positions=_integer(risk_raw, "max_open_positions", 1),
        min_current_spread_bps_8h=_number(risk_raw, "min_current_spread_bps_8h", 8.0),
        min_predicted_spread_bps_8h=_number(
            risk_raw, "min_predicted_spread_bps_8h", 5.0
        ),
        min_expected_net_bps=_number(risk_raw, "min_expected_net_bps", 10.0),
        exit_expected_net_bps=_number(risk_raw, "exit_expected_net_bps", 1.0),
        hold_hours=_number(risk_raw, "hold_hours", 24.0),
        max_hold_hours=_number(risk_raw, "max_hold_hours", 72.0),
        max_basis_bps=_number(risk_raw, "max_basis_bps", 35.0),
        max_mark_divergence_bps=_number(risk_raw, "max_mark_divergence_bps", 75.0),
        min_open_interest_usdt=_number(risk_raw, "min_open_interest_usdt", 5_000_000.0),
        allow_missing_open_interest=_boolean(
            risk_raw, "allow_missing_open_interest", False
        ),
        min_depth_multiple=_number(risk_raw, "min_depth_multiple", 5.0),
        require_predicted_confirmation=_boolean(
            risk_raw, "require_predicted_confirmation", True
        ),
        slippage_buffer_bps=_number(risk_raw, "slippage_buffer_bps", 3.0),
        exit_basis_buffer_bps=_number(risk_raw, "exit_basis_buffer_bps", 8.0),
        adverse_selection_buffer_bps=_number(
            risk_raw, "adverse_selection_buffer_bps", 3.0
        ),
        delta_tolerance_fraction=_number(risk_raw, "delta_tolerance_fraction", 0.005),
        max_unhedged_seconds=_number(risk_raw, "max_unhedged_seconds", 4.0),
        max_retries=_integer(risk_raw, "max_retries", 3),
        min_fill_fraction=_number(risk_raw, "min_fill_fraction", 0.20),
    )
    execution = ExecutionSettings(
        maker_timeout_seconds=_number(execution_raw, "maker_timeout_seconds", 8.0),
        order_poll_seconds=_number(execution_raw, "order_poll_seconds", 0.5),
        maker_offset_bps=_number(execution_raw, "maker_offset_bps", 0.0),
        close_retry_delay_seconds=_number(
            execution_raw, "close_retry_delay_seconds", 0.75
        ),
        paper_start_balance_usdt=_number(
            execution_raw, "paper_start_balance_usdt", 3_000.0
        ),
        paper_entry_extra_bps=_number(execution_raw, "paper_entry_extra_bps", 0.0),
        paper_exit_extra_bps=_number(execution_raw, "paper_exit_extra_bps", 0.0),
    )
    live = LiveSettings(
        enabled=_boolean(live_raw, "enabled", False),
        confirmation_phrase=str(
            live_raw.get("confirmation_phrase", "YES_I_UNDERSTAND")
        ),
        confirmation_env=str(
            live_raw.get("confirmation_env", "FUNDING_ROUTER_LIVE_CONFIRM")
        ),
        allow_external_positions=_boolean(live_raw, "allow_external_positions", False),
        close_on_shutdown=_boolean(live_raw, "close_on_shutdown", True),
        require_balance_check=_boolean(live_raw, "require_balance_check", True),
        margin_safety_multiplier=_number(live_raw, "margin_safety_multiplier", 1.20),
    )

    exchanges_raw = raw.get("exchanges", [])
    if not isinstance(exchanges_raw, list):
        raise ConfigError("[[exchanges]] entries are required")
    exchanges: list[ExchangeSettings] = []
    for item in exchanges_raw:
        if not isinstance(item, Mapping):
            raise ConfigError("each [[exchanges]] entry must be a table")
        markets_raw = item.get("markets", [])
        if not isinstance(markets_raw, list) or not all(
            isinstance(value, str) for value in markets_raw
        ):
            raise ConfigError("exchange markets must be a list of strings")
        options = item.get("options", {})
        params = item.get("params", {})
        if not isinstance(options, Mapping) or not isinstance(params, Mapping):
            raise ConfigError("exchange options and params must be tables")
        exchanges.append(
            ExchangeSettings(
                id=str(item.get("id", "")),
                exchange_class=str(item.get("exchange_class", item.get("id", ""))),
                enabled=_boolean(item, "enabled", True),
                markets=tuple(markets_raw),
                maker_fee_bps=_number(item, "maker_fee_bps", 2.0),
                taker_fee_bps=_number(item, "taker_fee_bps", 5.0),
                api_key_env=str(item.get("api_key_env", "")),
                secret_env=str(item.get("secret_env", "")),
                password_env=str(item.get("password_env", "")),
                uid_env=str(item.get("uid_env", "")),
                default_funding_interval_hours=_number(
                    item, "default_funding_interval_hours", 8.0
                ),
                leverage=_number(item, "leverage", 1.0),
                sandbox=_boolean(item, "sandbox", False),
                options=dict(options),
                params=dict(params),
            )
        )

    settings = Settings(
        service=service,
        risk=risk,
        execution=execution,
        live=live,
        exchanges=tuple(exchanges),
        source_path=source,
    )
    settings.validate()
    return settings
