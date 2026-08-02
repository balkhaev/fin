#!/usr/bin/env python3
"""Apply the reviewed production fixes with strict marker checks.

This file is executed once by GitHub Actions and removed before the resulting
implementation commit is pushed.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one marker, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


# Shared typed error used by the data loaders and HTTP surface.
write(
    "src/finruntime/observability/errors.py",
    '''"""Typed observability errors exposed by the read-only control plane."""\n\n\nclass DataUnavailableError(RuntimeError):\n    """Required public market data could not be retrieved completely."""\n''',
)

# ---------------------------------------------------------------------------
# Backtest identity and wall-clock correctness.
# ---------------------------------------------------------------------------
replace_once(
    "src/finruntime/observability/backtest_runner.py",
    "from .atlas_v517_backtest import run_atlas_v517_replay\nfrom .factor_backtests import run_consensus_backtest, run_funding_backtest\n",
    "from .atlas_v517_backtest import run_atlas_v517_replay\nfrom .backtests import backtest_report\nfrom .factor_backtests import run_consensus_backtest, run_funding_backtest\n",
)
replace_once(
    "src/finruntime/observability/backtest_runner.py",
    '''    if strategy_id not in {\n        "funding-neutral",\n        "consensus-wif-dot",\n        "dyn-iv113",\n        "atlas-nx",\n    }:\n''',
    '''    if strategy_id not in {\n        "funding-neutral",\n        "consensus-wif-dot",\n        "dyn-iv113",\n        "dyn-iv113-risk50",\n        "dyn-iv113-band2",\n        "atlas-nx",\n        "atlas-v517-reference",\n    }:\n''',
)
replace_once(
    "src/finruntime/observability/backtest_runner.py",
    '''    started = (now or datetime.now(UTC)).astimezone(UTC)\n    run_id = str(uuid4())\n    window_end = started.date() - timedelta(days=1)\n''',
    '''    started = datetime.now(UTC)\n    window_anchor = (now or started).astimezone(UTC)\n    run_id = str(uuid4())\n    window_end = window_anchor.date() - timedelta(days=1)\n''',
)
replace_once(
    "src/finruntime/observability/backtest_runner.py",
    '''    if strategy_id == "atlas-nx":\n        return _atlas_v517_report(\n            started=started,\n            run_id=run_id,\n        )\n\n    history_start = window_start - timedelta(days=WARMUP_DAYS)\n    strategy_module = dyn_paper\n    engine_module = "dyn_paper"\n    strategy_identity = dyn_paper.STRATEGY_ID\n    execution_cost = dyn_paper.EXECUTION_COST\n''',
    '''    if strategy_id == "atlas-nx":\n        completed = datetime.now(UTC)\n        report = backtest_report("atlas-nx")\n        report["report_kind"] = "on_demand_unavailable"\n        report["execution"] = {\n            "status": "not_available",\n            "run_id": run_id,\n            "trigger": "user_click",\n            "started_at_utc": _utc_iso(started),\n            "completed_at_utc": _utc_iso(completed),\n            "duration_seconds": round((completed - started).total_seconds(), 3),\n        }\n        return report\n    if strategy_id == "atlas-v517-reference":\n        return _atlas_v517_report(\n            started=started,\n            run_id=run_id,\n        )\n\n    dyn_profile_name = {\n        "dyn-iv113": "baseline",\n        "dyn-iv113-risk50": "risk50",\n        "dyn-iv113-band2": "band2",\n    }[strategy_id]\n    profile = dyn_paper.get_profile(dyn_profile_name)\n    history_start = window_start - timedelta(days=WARMUP_DAYS)\n    strategy_module = dyn_paper\n    engine_module = "dyn_paper"\n    strategy_identity = profile.strategy_id\n    strategy_name = profile.label\n    execution_cost = dyn_paper.EXECUTION_COST\n''',
)
replace_once(
    "src/finruntime/observability/backtest_runner.py",
    "    engine = strategy_module.build_engine(histories, failures)\n",
    "    engine = strategy_module.build_profile_engine(histories, failures, profile)\n",
)
replace_once(
    "src/finruntime/observability/backtest_runner.py",
    '''        "strategy_identity": strategy_identity,\n        "strategy_name": strategy_identity,\n''',
    '''        "strategy_identity": strategy_identity,\n        "strategy_name": strategy_name,\n''',
)

# Static report catalogue: separate active Atlas NX from its predecessor.
replace_once(
    "src/finruntime/observability/backtests.py",
    "from typing import Any\n",
    '''from typing import Any\n\nfrom .atlas_v517_backtest import (\n    EXPECTED_FULL_CAGR,\n    EXPECTED_FULL_FINAL_EQUITY,\n    EXPECTED_FULL_MAX_DRAWDOWN,\n    EXPECTED_INPUT_SHA256,\n    EXPECTED_ROWS,\n)\n''',
)
replace_once(
    "src/finruntime/observability/backtests.py",
    '''    "dyn-iv113",\n    "atlas-nx",\n)\n''',
    '''    "dyn-iv113",\n    "atlas-nx",\n    "atlas-v517-reference",\n)\n''',
)
replace_once(
    "src/finruntime/observability/backtests.py",
    "\ndef _insufficient_report(strategy_id: str) -> dict[str, Any]:\n",
    '''\ndef _atlas_reference_report() -> dict[str, Any]:\n    """Return the pinned predecessor as an explicitly separate identity."""\n\n    return {\n        "schema_version": 1,\n        "strategy_id": "atlas-v517-reference",\n        "strategy_identity": "v517_v524_v75_tristate_guard",\n        "strategy_name": "Atlas V517 · historical reference",\n        "report_kind": "historical_reference",\n        "window": {\n            "requested_years": 2,\n            "start": "2021-01-01",\n            "end": "2026-06-30",\n            "label": "Pinned V517/V524 research period",\n            "trade_inclusion": "Account-level leverage episodes",\n        },\n        "evidence": {\n            "status": "verified",\n            "status_label": "Checksum-pinned predecessor",\n            "cagr_threshold_percent": _CAGR_THRESHOLD_PERCENT,\n            "cagr_threshold_passed": EXPECTED_FULL_CAGR * 100 >= _CAGR_THRESHOLD_PERCENT,\n            "headline": "V517 historical reference is verified separately from Atlas NX R1",\n            "summary": (\n                "The checksum-pinned V75 account stream can be replayed as V517/V524, "\n                "but these metrics do not belong to the active Atlas NX R1 identity."\n            ),\n        },\n        "metrics": {\n            "scope": "full_frozen_research",\n            "cagr_percent": EXPECTED_FULL_CAGR * 100,\n            "total_return_percent": (EXPECTED_FULL_FINAL_EQUITY / 10_000.0 - 1.0) * 100,\n            "sharpe": 1.4597904254441392,\n            "sortino": 2.5965329178472696,\n            "max_drawdown_percent": EXPECTED_FULL_MAX_DRAWDOWN * 100,\n            "starting_nav_usd": 10_000.0,\n            "ending_nav_usd": EXPECTED_FULL_FINAL_EQUITY,\n            "daily_observations": EXPECTED_ROWS,\n        },\n        "trade_count": 0,\n        "trades": [],\n        "blockers": [],\n        "limitations": [\n            "Parameters were informed by known history; this is not a pristine holdout.",\n            "The source is an account-level equity stream, not position-level fills.",\n            "Atlas NX R1 remains a distinct reconstructed paper identity.",\n        ],\n        "provenance": {\n            "source_repository": "balkhaev/fin",\n            "strategy_identity": "v517_v524_v75_tristate_guard",\n            "input_sha256": EXPECTED_INPUT_SHA256,\n            "is_current_paper_account": False,\n        },\n        "historical_reference": None,\n    }\n\n\ndef _insufficient_report(strategy_id: str) -> dict[str, Any]:\n''',
)
replace_once(
    "src/finruntime/observability/backtests.py",
    '''    report = (\n        _dyn_report()\n        if strategy_id == "dyn-iv113"\n        else _insufficient_report(strategy_id)\n    )\n''',
    '''    if strategy_id == "dyn-iv113":\n        report = _dyn_report()\n    elif strategy_id == "atlas-v517-reference":\n        report = _atlas_reference_report()\n    else:\n        report = _insufficient_report(strategy_id)\n''',
)

# ---------------------------------------------------------------------------
# Resilient factor data ingestion.
# ---------------------------------------------------------------------------
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    "import math\nimport statistics\n",
    "import math\nimport os\nimport statistics\n",
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    "from finruntime.strategies import consensus_paper\n",
    "from finruntime.strategies import consensus_paper\n\nfrom .errors import DataUnavailableError\n",
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    'BYBIT_API = "https://api.bybit.com"\n',
    '''BYBIT_API_BASES = tuple(\n    dict.fromkeys(\n        item.strip().rstrip("/")\n        for item in os.environ.get(\n            "FIN_BYBIT_API_BASES",\n            "https://api.bybit.com,https://api.bytick.com,https://api.bybit.kz",\n        ).split(",")\n        if item.strip()\n    )\n)\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''class DownloadAudit:\n    request_count: int = 0\n    byte_count: int = 0\n    payload_sha256: str = ""\n''',
    '''class DownloadAudit:\n    request_count: int = 0\n    byte_count: int = 0\n    payload_sha256: str = ""\n    missing_urls: tuple[str, ...] = ()\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''def _month_end(value: date) -> date:\n    return _next_month(value) - timedelta(days=1)\n''',
    '''def _month_end(value: date) -> date:\n    return _next_month(value) - timedelta(days=1)\n\n\ndef _recent_api_start(start: date, *, today: date | None = None) -> date:\n    """Cover current and previous month while monthly archives may be delayed."""\n\n    current_month = _month_start(today or datetime.now(UTC).date())\n    previous_month = _month_start(current_month - timedelta(days=1))\n    return max(start, previous_month)\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    audit = DownloadAudit(\n        request_count=len(payloads) + len(missing),\n        byte_count=total_bytes,\n        payload_sha256=digest.hexdigest(),\n    )\n''',
    '''    audit = DownloadAudit(\n        request_count=len(payloads) + len(missing),\n        byte_count=total_bytes,\n        payload_sha256=digest.hexdigest(),\n        missing_urls=tuple(sorted(missing)),\n    )\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    "\ndef run_consensus_backtest(start: date, end: date) -> dict[str, Any]:\n",
    '''\ndef _require_kline_coverage(\n    name: str,\n    rows: list[dict[str, float | int]],\n    start: date,\n    end: date,\n) -> None:\n    if not rows:\n        raise DataUnavailableError(f"{name} returned no usable klines")\n    first = datetime.fromtimestamp(int(rows[0]["timestamp_ms"]) / 1000, UTC).date()\n    last = datetime.fromtimestamp(int(rows[-1]["close_time_ms"]) / 1000, UTC).date()\n    if first > start + timedelta(days=1) or last < end:\n        raise DataUnavailableError(\n            f"{name} coverage is incomplete: {first.isoformat()}..{last.isoformat()}, "\n            f"required {start.isoformat()}..{end.isoformat()}"\n        )\n\n\ndef run_consensus_backtest(start: date, end: date) -> dict[str, Any]:\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    archive_rows, archive_audit = _download_archives(specs)\n    api_audits: list[DownloadAudit] = []\n    current_start = max(warmup_start, _month_start(datetime.now(UTC).date()))\n''',
    '''    archive_rows, archive_audit = _download_archives(specs, allow_missing=True)\n    api_audits: list[DownloadAudit] = []\n    current_start = _recent_api_start(warmup_start)\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''    wif_rows = _kline_rows(archive_rows.get("wif", []))\n    premium_rows = _kline_rows(archive_rows.get("premium", []))\n    dot_rows = _kline_rows(archive_rows.get("dot", []))\n    preliminary = _preliminary_wif_signals(wif_rows, premium_rows)\n''',
    '''    wif_rows = _kline_rows(archive_rows.get("wif", []))\n    premium_rows = _kline_rows(archive_rows.get("premium", []))\n    dot_rows = _kline_rows(archive_rows.get("dot", []))\n    _require_kline_coverage("WIF klines", wif_rows, warmup_start, end)\n    _require_kline_coverage("WIF premium", premium_rows, warmup_start, end)\n    _require_kline_coverage("DOT klines", dot_rows, warmup_start, end)\n    preliminary = _preliminary_wif_signals(wif_rows, premium_rows)\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''        "diagnostics": {\n            "wif_klines": len(wif_rows),\n''',
    '''        "diagnostics": {\n            "archive_missing_urls": list(archive_audit.missing_urls),\n            "wif_klines": len(wif_rows),\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''def _fetch_json(url: str, params: dict[str, str], timeout_seconds: float = 30.0) -> Any:\n    request = Request(\n        f"{url}?{urlencode(params)}",\n        headers={"Accept": "application/json", "User-Agent": "finruntime-backtest/1.0"},\n    )\n    with urlopen(request, timeout=timeout_seconds) as response:\n        return json.load(response)\n''',
    '''def _fetch_json(url: str, params: dict[str, str], timeout_seconds: float = 30.0) -> Any:\n    request = Request(\n        f"{url}?{urlencode(params)}",\n        headers={"Accept": "application/json", "User-Agent": "finruntime-backtest/1.0"},\n    )\n    with urlopen(request, timeout=timeout_seconds) as response:\n        return json.load(response)\n\n\ndef _fetch_bybit_json(\n    path: str, params: dict[str, str], timeout_seconds: float = 30.0\n) -> Any:\n    errors: list[str] = []\n    for base in BYBIT_API_BASES:\n        try:\n            return _fetch_json(f"{base}{path}", params, timeout_seconds)\n        except (HTTPError, OSError, ValueError) as error:\n            errors.append(f"{base}: {type(error).__name__}: {error}")\n    raise DataUnavailableError(\n        "Bybit public market data is unavailable across configured endpoints: "\n        + "; ".join(errors)\n    )\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''        payload = _fetch_json(\n            f"{BYBIT_API}/v5/market/funding/history",\n''',
    '''        payload = _fetch_bybit_json(\n            "/v5/market/funding/history",\n''',
)
replace_once(
    "src/finruntime/observability/factor_backtests.py",
    '''        payload = _fetch_json(\n            f"{BYBIT_API}/v5/market/mark-price-kline",\n''',
    '''        payload = _fetch_bybit_json(\n            "/v5/market/mark-price-kline",\n''',
)

# HTTP maps external data outages separately from strategy failures.
replace_once(
    "src/finruntime/observability/server.py",
    "from .control_room import build_runtime_snapshot, snapshot_digest\n",
    "from .control_room import build_runtime_snapshot, snapshot_digest\nfrom .errors import DataUnavailableError\n",
)
replace_once(
    "src/finruntime/observability/server.py",
    '''            except KeyError:\n                self._send_json(\n                    {"error": "unknown_strategy", "strategy_id": strategy_id},\n                    status=HTTPStatus.NOT_FOUND,\n                )\n            except (OSError, RuntimeError, TypeError, ValueError) as error:\n''',
    '''            except KeyError:\n                self._send_json(\n                    {"error": "unknown_strategy", "strategy_id": strategy_id},\n                    status=HTTPStatus.NOT_FOUND,\n                )\n            except DataUnavailableError as error:\n                self.log_error("backtest data unavailable for %s: %s", strategy_id, error)\n                self._send_json(\n                    {\n                        "error": "data_unavailable",\n                        "detail": str(error),\n                        "retryable": True,\n                    },\n                    status=HTTPStatus.SERVICE_UNAVAILABLE,\n                )\n            except (OSError, RuntimeError, TypeError, ValueError) as error:\n''',
)

# ---------------------------------------------------------------------------
# DYN profiles: pure parameterization, no mutation of the baseline identity.
# ---------------------------------------------------------------------------
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    "from concurrent.futures import ThreadPoolExecutor, as_completed\n",
    "from concurrent.futures import ThreadPoolExecutor, as_completed\nfrom dataclasses import dataclass\n",
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''ASSETS = tuple(symbol.removesuffix("USDT") for symbol in MARKET_SYMBOLS)\n\n\ndef _clamp''',
    '''ASSETS = tuple(symbol.removesuffix("USDT") for symbol in MARKET_SYMBOLS)\n\n\n@dataclass(frozen=True, slots=True)\nclass DynProfile:\n    name: str\n    strategy_id: str\n    label: str\n    target_volatility: float\n    maximum_gross: float\n    asset_cap: float\n    target_deadband: float = 0.0\n    mode: str = "shadow"\n\n\nDYN_PROFILES = {\n    "baseline": DynProfile(\n        name="baseline",\n        strategy_id=STRATEGY_ID,\n        label="DYN-IV113",\n        target_volatility=TARGET_VOLATILITY,\n        maximum_gross=MAXIMUM_GROSS,\n        asset_cap=ASSET_CAP,\n        mode="paper",\n    ),\n    "risk50": DynProfile(\n        name="risk50",\n        strategy_id="DYN-IV113-RISK50",\n        label="DYN-IV113 · target vol 50%",\n        target_volatility=0.50,\n        maximum_gross=MAXIMUM_GROSS,\n        asset_cap=ASSET_CAP,\n    ),\n    "band2": DynProfile(\n        name="band2",\n        strategy_id="DYN-IV113-BAND2",\n        label="DYN-IV113 · target deadband 2%",\n        target_volatility=TARGET_VOLATILITY,\n        maximum_gross=MAXIMUM_GROSS,\n        asset_cap=ASSET_CAP,\n        target_deadband=0.02,\n    ),\n}\n\n\ndef get_profile(profile: str | DynProfile = "baseline") -> DynProfile:\n    if isinstance(profile, DynProfile):\n        return profile\n    try:\n        return DYN_PROFILES[profile]\n    except KeyError as error:\n        raise ValueError(f"unknown DYN profile: {profile}") from error\n\n\ndef _clamp''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''def build_engine(\n    histories: list[dict[str, Any]], failed_symbols: list[dict[str, str]]\n) -> dict[str, Any]:\n''',
    '''def build_engine(\n    histories: list[dict[str, Any]],\n    failed_symbols: list[dict[str, str]],\n    *,\n    target_volatility: float = TARGET_VOLATILITY,\n    maximum_gross: float = MAXIMUM_GROSS,\n    asset_cap: float = ASSET_CAP,\n) -> dict[str, Any]:\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    "            _clamp(TARGET_VOLATILITY / previous_volatility, 0, MAXIMUM_GROSS)\n",
    "            _clamp(target_volatility / previous_volatility, 0, maximum_gross)\n",
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    "        [_clamp(weight * leverage[index], -ASSET_CAP, ASSET_CAP) for weight in row]\n",
    "        [_clamp(weight * leverage[index], -asset_cap, asset_cap) for weight in row]\n",
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    "\ndef _elapsed_days(previous: str, current: str) -> int:\n",
    '''\ndef _apply_target_deadband(\n    targets: list[list[float]], threshold: float\n) -> list[list[float]]:\n    if threshold <= 0 or not targets:\n        return [list(row) for row in targets]\n    held = _zero_row(len(targets[0]))\n    output: list[list[float]] = []\n    for row in targets:\n        next_row: list[float] = []\n        for old, new in zip(held, row, strict=True):\n            sign_flip = old * new < -EPSILON\n            exit_required = abs(old) > EPSILON and abs(new) <= EPSILON\n            if sign_flip or exit_required or abs(new - old) >= threshold:\n                next_row.append(float(new))\n            else:\n                next_row.append(float(old))\n        held = next_row\n        output.append(list(held))\n    return output\n\n\ndef build_profile_engine(\n    histories: list[dict[str, Any]],\n    failed_symbols: list[dict[str, str]],\n    profile: str | DynProfile = "baseline",\n) -> dict[str, Any]:\n    config = get_profile(profile)\n    engine = build_engine(\n        histories,\n        failed_symbols,\n        target_volatility=config.target_volatility,\n        maximum_gross=config.maximum_gross,\n        asset_cap=config.asset_cap,\n    )\n    engine["target"] = _apply_target_deadband(\n        engine["target"], config.target_deadband\n    )\n    engine["profile"] = {\n        "name": config.name,\n        "strategyId": config.strategy_id,\n        "label": config.label,\n        "mode": config.mode,\n        "targetVolatility": config.target_volatility,\n        "maximumGross": config.maximum_gross,\n        "assetCap": config.asset_cap,\n        "targetDeadband": config.target_deadband,\n    }\n    return engine\n\n\ndef _elapsed_days(previous: str, current: str) -> int:\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''def compute_forward_state(\n    histories: list[dict[str, Any]],\n    failed_symbols: list[dict[str, str]],\n    *,\n    reset_date: str = SNAPSHOT_DATE,\n    initial_nav_usd: float = 10_000.0,\n) -> dict[str, Any]:\n    generated_at = _utc_now()\n''',
    '''def compute_forward_state(\n    histories: list[dict[str, Any]],\n    failed_symbols: list[dict[str, str]],\n    *,\n    reset_date: str = SNAPSHOT_DATE,\n    initial_nav_usd: float = 10_000.0,\n    profile: str | DynProfile = "baseline",\n) -> dict[str, Any]:\n    generated_at = _utc_now()\n    profile_config = get_profile(profile)\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    "    engine = build_engine(histories, failed_symbols)\n",
    "    engine = build_profile_engine(histories, failed_symbols, profile_config)\n",
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''    return {\n        "schema_version": 1,\n        "absFamilyWeight": engine["absWeight"][latest_index],\n''',
    '''    return {\n        "schema_version": 1,\n        "mode": profile_config.mode,\n        "profile": engine["profile"],\n        "absFamilyWeight": engine["absWeight"][latest_index],\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '        "strategyId": STRATEGY_ID,\n',
    '        "strategyId": profile_config.strategy_id,\n',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''def run_once(path: Path, *, reset_date: str, initial_nav_usd: float) -> dict[str, Any]:\n    histories, failed_symbols = load_asset_histories()\n    snapshot = compute_forward_state(\n        histories,\n        failed_symbols,\n        reset_date=reset_date,\n        initial_nav_usd=initial_nav_usd,\n    )\n''',
    '''def run_once(\n    path: Path,\n    *,\n    reset_date: str,\n    initial_nav_usd: float,\n    profile: str | DynProfile = "baseline",\n) -> dict[str, Any]:\n    histories, failed_symbols = load_asset_histories()\n    snapshot = compute_forward_state(\n        histories,\n        failed_symbols,\n        reset_date=reset_date,\n        initial_nav_usd=initial_nav_usd,\n        profile=profile,\n    )\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''    parser.add_argument("--starting-cash", type=float, default=10_000.0)\n    parser.add_argument("--once", action="store_true")\n''',
    '''    parser.add_argument("--starting-cash", type=float, default=10_000.0)\n    parser.add_argument(\n        "--profile", choices=tuple(DYN_PROFILES), default="baseline"\n    )\n    parser.add_argument("--once", action="store_true")\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''                reset_date=args.reset_date,\n                initial_nav_usd=args.starting_cash,\n            )\n''',
    '''                reset_date=args.reset_date,\n                initial_nav_usd=args.starting_cash,\n                profile=args.profile,\n            )\n''',
)
replace_once(
    "src/finruntime/strategies/dyn_paper.py",
    '''                        "event": "dyn_paper_snapshot",\n                        "status": snapshot["status"],\n''',
    '''                        "event": "dyn_paper_snapshot",\n                        "profile": args.profile,\n                        "status": snapshot["status"],\n''',
)

# Optional shadow processes; baseline remains unchanged unless explicitly enabled.
replace_once(
    "scripts/run_paper_stack.py",
    '''DS40180_POLL_SECONDS = os.environ.get("FIN_DS40180_POLL_SECONDS", "300")\nTERMINATION_TIMEOUT_SECONDS = 10.0\n''',
    '''DS40180_POLL_SECONDS = os.environ.get("FIN_DS40180_POLL_SECONDS", "300")\nDYN_SHADOW_PROFILES = tuple(\n    profile.strip()\n    for profile in os.environ.get("FIN_DYN_SHADOW_PROFILES", "").split(",")\n    if profile.strip()\n)\nTERMINATION_TIMEOUT_SECONDS = 10.0\n''',
)
replace_once(
    "scripts/run_paper_stack.py",
    '''    ]\n    processes: list[subprocess.Popen[bytes]] = []\n''',
    '''    ]\n    shadow_specs: list[tuple[list[str], dict[str, str] | None]] = []\n    for profile in DYN_SHADOW_PROFILES:\n        snapshot = RUNTIME_ROOT / f"dyn_{profile}_snapshot.json"\n        shadow_specs.append(\n            (\n                [\n                    sys.executable,\n                    "-m",\n                    "finruntime.strategies.dyn_paper",\n                    "--snapshot",\n                    str(snapshot),\n                    "--poll-seconds",\n                    "60",\n                    "--starting-cash",\n                    "10000",\n                    "--profile",\n                    profile,\n                ],\n                None,\n            )\n        )\n    process_specs[3:3] = shadow_specs\n    processes: list[subprocess.Popen[bytes]] = []\n''',
)

# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------
replace_once(
    "tests/runtime/test_backtests.py",
    '''                "dyn-iv113",\n                "atlas-nx",\n            },\n''',
    '''                "dyn-iv113",\n                "atlas-nx",\n                "atlas-v517-reference",\n            },\n''',
)
replace_once(
    "tests/runtime/test_backtests.py",
    '''        self.assertEqual(first["execution"]["trigger"], "user_click")\n        self.assertEqual(first["report_kind"], "on_demand_backtest")\n''',
    '''        self.assertEqual(first["execution"]["trigger"], "user_click")\n        self.assertGreaterEqual(first["execution"]["duration_seconds"], 0.0)\n        self.assertLess(first["execution"]["duration_seconds"], 5.0)\n        self.assertEqual(first["report_kind"], "on_demand_backtest")\n''',
)
replace_once(
    "tests/runtime/test_backtests.py",
    '''        report = run_backtest(\n            "atlas-nx",\n            now=datetime(2026, 7, 30, 12, tzinfo=UTC),\n''',
    '''        report = run_backtest(\n            "atlas-v517-reference",\n            now=datetime(2026, 7, 30, 12, tzinfo=UTC),\n''',
)
replace_once(
    "tests/runtime/test_backtests.py",
    "    def test_click_run_executes_factor_strategies_without_ohlc_approximation(\n",
    '''    def test_click_atlas_nx_does_not_replay_predecessor_metrics(self) -> None:\n        report = run_backtest(\n            "atlas-nx", now=datetime(2026, 7, 30, 12, tzinfo=UTC)\n        )\n\n        self.assertEqual(report["strategy_identity"], "atlas_nx_r1")\n        self.assertEqual(report["execution"]["status"], "not_available")\n        self.assertIsNone(report["metrics"])\n        self.assertFalse(\n            report["historical_reference"]["belongs_to_active_strategy"]\n        )\n        self.assertLess(report["execution"]["duration_seconds"], 5.0)\n\n    def test_click_run_supports_preregistered_dyn_shadow_profiles(self) -> None:\n        now = datetime(2026, 7, 30, 12, tzinfo=UTC)\n        risk50 = run_backtest(\n            "dyn-iv113-risk50", now=now, history_loader=synthetic_history_loader\n        )\n        band2 = run_backtest(\n            "dyn-iv113-band2", now=now, history_loader=synthetic_history_loader\n        )\n\n        self.assertEqual(risk50["strategy_identity"], "DYN-IV113-RISK50")\n        self.assertEqual(band2["strategy_identity"], "DYN-IV113-BAND2")\n        self.assertEqual(risk50["report_kind"], "on_demand_backtest")\n        self.assertEqual(band2["report_kind"], "on_demand_backtest")\n\n    def test_click_run_executes_factor_strategies_without_ohlc_approximation(\n''',
)

write(
    "tests/runtime/test_factor_backtest_resilience.py",
    '''from __future__ import annotations\n\nimport unittest\nfrom datetime import date\nfrom unittest.mock import patch\nfrom urllib.error import HTTPError\n\nfrom finruntime.observability import factor_backtests as subject\nfrom finruntime.observability.errors import DataUnavailableError\n\n\nclass FactorBacktestResilienceTests(unittest.TestCase):\n    def test_recent_api_window_covers_previous_month(self) -> None:\n        self.assertEqual(\n            subject._recent_api_start(\n                date(2024, 1, 1), today=date(2026, 8, 1)\n            ),\n            date(2026, 7, 1),\n        )\n\n    def test_missing_archive_is_audited_when_fallback_is_allowed(self) -> None:\n        error = HTTPError("https://example.invalid/data.zip", 404, "missing", {}, None)\n        with patch.object(subject, "_fetch_bytes", side_effect=error):\n            rows, audit = subject._download_archives(\n                [subject.ArchiveSpec("test", "https://example.invalid/data.zip")],\n                allow_missing=True,\n            )\n        self.assertEqual(rows, {})\n        self.assertEqual(\n            audit.missing_urls, ("https://example.invalid/data.zip",)\n        )\n\n    def test_bybit_uses_next_official_endpoint_after_403(self) -> None:\n        forbidden = HTTPError("https://first", 403, "forbidden", {}, None)\n        with (\n            patch.object(subject, "BYBIT_API_BASES", ("https://first", "https://second")),\n            patch.object(subject, "_fetch_json", side_effect=[forbidden, {"retCode": 0}]) as fetch,\n        ):\n            result = subject._fetch_bybit_json("/v5/market/test", {"symbol": "BTCUSDT"})\n        self.assertEqual(result, {"retCode": 0})\n        self.assertEqual(fetch.call_count, 2)\n\n    def test_incomplete_coverage_is_rejected(self) -> None:\n        rows = [\n            {\n                "timestamp_ms": 1_750_032_000_000,\n                "close_time_ms": 1_750_032_899_999,\n            }\n        ]\n        with self.assertRaises(DataUnavailableError):\n            subject._require_kline_coverage(\n                "test", rows, date(2024, 1, 1), date(2026, 7, 31)\n            )\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
)

write(
    "tests/runtime/test_dyn_profiles.py",
    '''from __future__ import annotations\n\nimport unittest\n\nfrom finruntime.strategies import dyn_paper\n\n\nclass DynProfileTests(unittest.TestCase):\n    def test_profiles_are_distinct_and_baseline_is_unchanged(self) -> None:\n        baseline = dyn_paper.get_profile("baseline")\n        risk50 = dyn_paper.get_profile("risk50")\n        band2 = dyn_paper.get_profile("band2")\n\n        self.assertEqual(baseline.strategy_id, "DYN-IV113")\n        self.assertEqual(baseline.mode, "paper")\n        self.assertEqual(risk50.target_volatility, 0.50)\n        self.assertEqual(risk50.mode, "shadow")\n        self.assertEqual(band2.target_deadband, 0.02)\n        self.assertEqual(band2.mode, "shadow")\n\n    def test_deadband_holds_small_changes_but_executes_exit_and_flip(self) -> None:\n        targets = [\n            [0.10, -0.10],\n            [0.11, -0.11],\n            [0.13, 0.10],\n            [0.00, 0.10],\n        ]\n        actual = dyn_paper._apply_target_deadband(targets, 0.02)\n\n        self.assertEqual(actual[0], [0.10, -0.10])\n        self.assertEqual(actual[1], [0.10, -0.10])\n        self.assertEqual(actual[2], [0.13, 0.10])\n        self.assertEqual(actual[3], [0.00, 0.10])\n\n    def test_unknown_profile_is_rejected(self) -> None:\n        with self.assertRaises(ValueError):\n            dyn_paper.get_profile("unknown")\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
)

write(
    "docs/DYN_SHADOW_PROFILES_RU.md",
    '''# DYN-IV113: предзарегистрированные shadow-профили\n\nBaseline `DYN-IV113` не изменён. Добавлены два отдельных профиля с независимыми\nidentity и snapshot-файлами:\n\n- `risk50` / `DYN-IV113-RISK50`: target volatility 50%;\n- `band2` / `DYN-IV113-BAND2`: target deadband 2 процентных пункта.\n\nОба профиля имеют `mode=shadow`. Они запускаются только при явной настройке:\n\n```text\nFIN_DYN_SHADOW_PROFILES=risk50,band2\n```\n\nИх наличие в `main` не является разрешением реального капитала и не переносит\nметрики исследовательского окна на forward-счёт. Для сравнения следует хранить\nотдельные snapshots и не менять параметры до завершения заранее выбранного\nforward-периода.\n''',
)

print("production fixes applied")
