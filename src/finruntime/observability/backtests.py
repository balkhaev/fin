"""Identity-safe historical backtest reports for the paper control room."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any

_STRATEGY_IDS = (
    "funding-neutral",
    "consensus-wif-dot",
    "dyn-iv113",
    "atlas-nx",
)
_CAGR_THRESHOLD_PERCENT = 50.0
_DYN_WINDOW_START = "2024-07-26"
_DYN_WINDOW_END = "2026-07-26"
_DYN_TRADES_FILE = "dyniv113-two-year-trades.json"
_DYN_SOURCE_EPISODES_SHA256 = (
    "7a35e00cd449bc0d9359498137ad09f90f7a253497d69ec14e8b25ffde32815a"
)
_DYN_NORMALIZED_TRADES_SHA256 = (
    "32e2fabaedccb0cea99b19422222d89e7459a787a9b4ca00738b0eca4af69a90"
)
_TROPICAL_YEAR_DAYS = 365.2425
_DYN_RISK_METRICS = {
    "scope": "full_frozen_oos",
    "scope_label": "Полный frozen OOS (2,565 года)",
    "sharpe": 1.486256,
    "sortino": 1.895858,
    "max_drawdown_percent": -32.6774,
}


def backtest_strategy_ids() -> tuple[str, ...]:
    """Return the strategy identities covered by the report catalog."""

    return _STRATEGY_IDS


def _data_text(name: str) -> str:
    resource = files("finruntime.observability").joinpath("backtest_data", name)
    return resource.read_text(encoding="utf-8")


def _load_dyn_archive() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(_data_text("dyniv113-ledger-manifest.json"))
    if manifest.get("episodesPayloadSha256") != _DYN_SOURCE_EPISODES_SHA256:
        raise ValueError("DYN-IV113 source archive identity mismatch")

    episodes = json.loads(_data_text(_DYN_TRADES_FILE))
    if not isinstance(episodes, list):
        raise TypeError("DYN-IV113 trade archive must contain a list")
    canonical = json.dumps(
        episodes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != _DYN_NORMALIZED_TRADES_SHA256:
        raise ValueError("DYN-IV113 readable trade extract checksum mismatch")
    if len(episodes) != 53:
        raise ValueError("DYN-IV113 readable trade extract row count mismatch")
    if len({str(item["id"]) for item in episodes}) != len(episodes):
        raise ValueError("DYN-IV113 readable trade extract contains duplicate IDs")
    if any(
        str(item["entryDate"]) > _DYN_WINDOW_END
        or str(item["heldThrough"]) < _DYN_WINDOW_START
        for item in episodes
    ):
        raise ValueError("DYN-IV113 readable trade extract is outside its window")
    return manifest, episodes


def _normalize_trade(episode: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(episode["id"]),
        "asset": str(episode["asset"]),
        "direction": "LONG",
        "status": str(episode["status"]),
        "entry_date": str(episode["entryDate"]),
        "exit_date": str(episode["exitDate"]),
        "held_through": str(episode["heldThrough"]),
        "holding_days": int(episode["holdingDays"]),
        "entry_price": float(episode["entryPrice"]),
        "exit_price": float(episode["exitPrice"]),
        "asset_return_percent": (
            float(episode["assetReturn"]) * 100
            if episode.get("assetReturn") is not None
            else None
        ),
        "net_pnl_usd": float(episode["netContributionUsd"]),
        "order_count": int(episode["orderCount"]),
    }


def _dyn_metrics(manifest: dict[str, Any]) -> dict[str, Any]:
    start = date.fromisoformat(str(manifest["oosStart"]))
    end = date.fromisoformat(str(manifest["snapshotDate"]))
    years = (end - start).days / _TROPICAL_YEAR_DAYS
    starting_nav = float(manifest["modelStartNavUsd"])
    ending_nav = float(manifest["modelEndingNavUsd"])
    multiple = ending_nav / starting_nav
    return {
        **_DYN_RISK_METRICS,
        "cagr_percent": (multiple ** (1 / years) - 1) * 100,
        "total_return_percent": (multiple - 1) * 100,
        "years": years,
        "starting_nav_usd": starting_nav,
        "ending_nav_usd": ending_nav,
        "trade_episodes_oos": int(manifest["tradeEpisodes"]),
    }


@lru_cache(maxsize=1)
def _dyn_report() -> dict[str, Any]:
    manifest, episodes = _load_dyn_archive()
    metrics = _dyn_metrics(manifest)
    included = [
        episode
        for episode in episodes
        if str(episode["entryDate"]) <= _DYN_WINDOW_END
        and str(episode["heldThrough"]) >= _DYN_WINDOW_START
    ]
    trades = sorted(
        (_normalize_trade(episode) for episode in included),
        key=lambda item: (item["exit_date"] or item["held_through"], item["asset"]),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "strategy_id": "dyn-iv113",
        "strategy_identity": "DYN-IV113",
        "strategy_name": "DYN-IV113",
        "report_kind": "historical_backtest",
        "window": {
            "requested_years": 2,
            "start": _DYN_WINDOW_START,
            "end": _DYN_WINDOW_END,
            "label": "Последние 2 года frozen snapshot",
            "trade_inclusion": "Эпизод пересекает двухлетнее окно",
        },
        "evidence": {
            "status": "verified",
            "status_label": "Архив проверен",
            "cagr_threshold_percent": _CAGR_THRESHOLD_PERCENT,
            "cagr_threshold_passed": (
                metrics["cagr_percent"] >= _CAGR_THRESHOLD_PERCENT
            ),
            "headline": "Порог 50%+ CAGR подтверждён для DYN-IV113",
            "summary": (
                "Checksum-верифицированный model ledger подтверждает 112,638% CAGR "
                "на полном frozen OOS-периоде. Ниже показаны сделки, пересекающие "
                "последние два года снимка."
            ),
        },
        "metrics": metrics,
        "trade_count": len(trades),
        "trades": trades,
        "blockers": [],
        "limitations": [
            (
                "CAGR, Sharpe и Max DD относятся к полному frozen OOS 2024-01-01—"
                "2026-07-26; таблица сделок ограничена двухлетним окном."
            ),
            (
                "CAGR и Total выведены из checksum-проверенного manifest NAV; "
                "Sharpe и Max DD перенесены из frozen strategy monitor snapshot."
            ),
            (
                "Это исторический model account с simulated close fills, а не "
                "доходность текущего paper-счёта на $10 000 и не прогноз."
            ),
        ],
        "provenance": {
            "source_repository": "balkhaev/fin2",
            "source_commit": "2d48e5e362b5a3154312567239e5e9f8ba93ac27",
            "strategy_identity": str(manifest["strategy"]),
            "snapshot_date": str(manifest["snapshotDate"]),
            "oos_start": str(manifest["oosStart"]),
            "execution_mode": "SIMULATED_CLOSE_FILLS",
            "episodes_payload_sha256": str(manifest["episodesPayloadSha256"]),
            "normalized_trades_sha256": _DYN_NORMALIZED_TRADES_SHA256,
            "normalized_trades_format": "readable_json",
            "episode_count": int(manifest["tradeEpisodes"]),
            "order_leg_count": int(manifest["orderLegs"]),
            "risk_metrics_source": "apps/web/src/data/strategy-monitor-data.ts",
            "is_current_paper_account": False,
        },
        "historical_reference": None,
    }


def _insufficient_report(strategy_id: str) -> dict[str, Any]:
    details: dict[str, dict[str, Any]] = {
        "funding-neutral": {
            "identity": "funding-neutral",
            "name": "Funding Neutral",
            "repository": "balkhaev/fin",
            "headline": "50%+ CAGR не подтверждён",
            "summary": (
                "Paper-движок работает, но точный исторический replay этой версии "
                "не сохранён и её контракт прямо помечает profitability_proven=false."
            ),
            "blockers": [
                "Нет двухлетнего архива predicted funding с точными timestamps.",
                "Нет полного исторического стакана, OI и basis для входных фильтров.",
                "Без этих рядов текущую логику нельзя честно воспроизвести задним числом.",
            ],
            "provenance": "services/funding_router/README.md",
            "evidence_note": (
                "Текущий canonical contract фиксирует profitability_proven=false."
            ),
        },
        "consensus-wif-dot": {
            "identity": "consensus-wif-dot-v1",
            "name": "Consensus WIF + DOT",
            "repository": "balkhaev/trader",
            "headline": "50%+ CAGR не подтверждён",
            "summary": (
                "Paper-движок WIF + DOT реализован, но в репозитории нет полного "
                "двухлетнего набора сигналов и зафиксированного backtest output."
            ),
            "blockers": [
                "Нет committed WIF open-interest/premium input series за два года.",
                "Нет checksum-проверяемого списка сделок и equity curve этой версии.",
                "Упоминание historical 100% в README само по себе не является результатом.",
            ],
            "provenance": "balkhaev/trader docs/consensus-wif-dot.md",
            "evidence_note": (
                "README-упоминание historical 100% не имеет приложенного ledger и "
                "не используется как метрика."
            ),
        },
        "atlas-nx": {
            "identity": "atlas_nx_r1",
            "name": "Atlas NX R1",
            "repository": "balkhaev/fin",
            "headline": "50%+ CAGR не принадлежит Atlas NX R1",
            "summary": (
                "Atlas NX R1 — новая reconstructed identity. Migration contract "
                "запрещает переносить на неё исторические метрики V517/V524."
            ),
            "blockers": [
                "Exact V75 target producer, необходимый для V517 replay, отсутствует.",
                "У Atlas NX R1 сброшена историческая identity и начинается новый forward clock.",
                "Нужен собственный неизменяемый двухлетний replay именно Atlas NX R1.",
            ],
            "provenance": "docs/checkpoints/runtime-v1/ATLAS_NX_R1_RECONSTRUCTION_RU.md",
            "evidence_note": (
                "50,55% CAGR — неп pristine reference V517/V524, а не результат "
                "активного Atlas NX R1."
            ),
        },
    }
    detail = details[strategy_id]
    historical_reference = None
    if strategy_id == "atlas-nx":
        historical_reference = {
            "strategy_identity": "V517/V524",
            "cagr_percent": 50.55,
            "sharpe": 1.46,
            "max_drawdown_percent": -23.68,
            "belongs_to_active_strategy": False,
            "note": "Неп pristine predecessor reference; не результат Atlas NX R1.",
        }
    return {
        "schema_version": 1,
        "strategy_id": strategy_id,
        "strategy_identity": detail["identity"],
        "strategy_name": detail["name"],
        "report_kind": "historical_backtest",
        "window": {
            "requested_years": 2,
            "start": None,
            "end": None,
            "label": "Последние 2 года",
            "trade_inclusion": "Недоступно до появления воспроизводимого архива",
        },
        "evidence": {
            "status": "insufficient_evidence",
            "status_label": "Недостаточно данных",
            "cagr_threshold_percent": _CAGR_THRESHOLD_PERCENT,
            "cagr_threshold_passed": None,
            "headline": detail["headline"],
            "summary": detail["summary"],
        },
        "metrics": None,
        "trade_count": 0,
        "trades": [],
        "blockers": detail["blockers"],
        "limitations": [
            detail["evidence_note"],
            "Отсутствие доказательства не означает нулевую доходность; результат неизвестен.",
            "Текущий paper runtime продолжает работать и собирать forward-наблюдения.",
        ],
        "provenance": {
            "source_repository": detail["repository"],
            "strategy_identity": detail["identity"],
            "evidence_document": detail["provenance"],
            "is_current_paper_account": False,
        },
        "historical_reference": historical_reference,
    }


def backtest_report(strategy_id: str) -> dict[str, Any]:
    """Return a detached, JSON-serializable report for an active strategy."""

    if strategy_id not in _STRATEGY_IDS:
        raise KeyError(strategy_id)
    report = (
        _dyn_report()
        if strategy_id == "dyn-iv113"
        else _insufficient_report(strategy_id)
    )
    return copy.deepcopy(report)
