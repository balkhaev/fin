"""Normalize FIN, trader and fin2 paper runtimes for one simple UI."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DEFAULT_FIN2_FORWARD_URL = "https://fin2.balkhaev.com/api/strategy/forward"
UPSTREAM_CACHE_SECONDS = 10.0
UPSTREAM_TIMEOUT_SECONDS = 4.0
DYN_STALE_AFTER_SECONDS = 300.0
ATLAS_STALE_AFTER_SECONDS = 300.0
DS40180_STALE_AFTER_SECONDS = 900.0


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _percent(pnl: float, starting_balance: float) -> float:
    return pnl / starting_balance * 100 if starting_balance > 0 else 0.0


def _metric(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


STRATEGY_GUIDES: dict[str, dict[str, Any]] = {
    "funding-neutral": {
        "summary": (
            "Рыночно-нейтральная paper-стратегия: она пытается заработать на "
            "разнице funding одного и того же бессрочного фьючерса на Binance и "
            "Bybit, а не на угадывании направления цены монеты."
        ),
        "steps": [
            {
                "title": "Сравнивает одинаковые рынки",
                "description": (
                    "Каждые 5 секунд сопоставляет mark price, текущий и прогнозный "
                    "funding, стакан и open interest одного актива на двух биржах."
                ),
            },
            {
                "title": "Считает доход после расходов",
                "description": (
                    "Из funding-спреда вычитает комиссии и ожидаемое проскальзывание; "
                    "пара проходит дальше только при достаточной ликвидности."
                ),
            },
            {
                "title": "Открывает две paper-ноги",
                "description": (
                    "Покупает контракт с более низким funding и одновременно шортит "
                    "контракт с более высоким funding, сохраняя близкую к нулю дельту."
                ),
            },
            {
                "title": "Закрывает пару целиком",
                "description": (
                    "Обе ноги переоцениваются вместе и закрываются синхронно, когда "
                    "преимущество исчезает или срабатывает ограничение удержания."
                ),
            },
        ],
        "entry_conditions": [
            "Текущий funding-спред не ниже 8 bps за 8 часов.",
            "Прогнозный funding-спред не ниже 5 bps за 8 часов.",
            "Ожидаемый результат после расходов не ниже 10 bps.",
            "Обе биржи дают свежие данные и достаточную ликвидность для двух ног.",
        ],
        "exit_conditions": [
            "Текущий funding-спред снизился до порога выхода — по умолчанию 1 bps.",
            "Прогнозный funding-спред стал нулевым или отрицательным.",
            "Достигнут предельный срок удержания — по умолчанию 72 часа.",
        ],
        "risk_controls": [
            "Лонг и шорт одного актива уменьшают направленный риск рынка.",
            "Одновременно допускается одна пара; до 80% капитала может быть размещено, 20% остаётся резервом.",
            "Вход блокируют слабый стакан, OI ниже лимита и чрезмерное расхождение mark/basis; допустимое время без хеджа — до 4 секунд.",
            "Неполные или устаревшие market data не создают новое решение и сохраняют текущее paper-состояние.",
            "Exchange submission отсутствует: реальные ордера отправить невозможно.",
        ],
        "data_scope": (
            "Публичные perpetual-данные Binance и Bybit, минутные свечи; сканирование "
            "примерно раз в 5 секунд."
        ),
    },
    "consensus-wif-dot": {
        "summary": (
            "Две независимые контртрендовые идеи в одном paper-счёте. WIF ищет "
            "капитуляцию цены и открытого интереса с сильным отскоком, а DOT — "
            "аномально отрицательный funding сразу после расчётного окна."
        ),
        "steps": [
            {
                "title": "Проверяет WIF-капитуляцию",
                "description": (
                    "На 15-минутных данных измеряет падение за 45 минут в ATR, "
                    "всплеск объёма, сброс OI, premium, нижнюю тень и силу закрытия."
                ),
            },
            {
                "title": "Проверяет DOT funding",
                "description": (
                    "В разрешённые дни ищет funding ниже дневного порога в окне "
                    "15–30 минут после его начисления."
                ),
            },
            {
                "title": "Выбирает размер по риску",
                "description": (
                    "Стоп в ATR задаёт расстояние риска, после чего paper-notional "
                    "подбирается для текущего режима base, boost или stopped."
                ),
            },
            {
                "title": "Выходит по заранее заданному правилу",
                "description": (
                    "После входа стратегия не импровизирует: ждёт stop-loss, "
                    "take-profit или максимальное время удержания."
                ),
            },
        ],
        "entry_conditions": [
            "WIF: падение за 45 минут не менее 2 ATR, volume z ≥ 1 и OI z ≤ −1.",
            "WIF: длинная нижняя тень, сильное закрытие свечи и суммарная сила ≥ 3.5.",
            "DOT: funding ≤ −2.25/−2.50 bps в разрешённый день недели.",
            "DOT: проверка выполняется через 15–30 минут после funding timestamp.",
        ],
        "exit_conditions": [
            "WIF: stop 1.25 ATR, цель 5R или максимум 60 минут.",
            "DOT: stop 6 ATR, цель 2R или максимум 480 минут.",
            "Любая позиция закрывается по первой наступившей причине.",
        ],
        "risk_controls": [
            "Base-риск: 3% для WIF и 5% для DOT; одновременно не более двух позиций.",
            "Boost включается только после +15% paper-прибыли на новом high-water.",
            "Просадка 8% возвращает boost в base; 15% необратимо включает stopped.",
            "Суммарная gross-экспозиция ограничена 3× и учитывает round-trip costs.",
        ],
        "data_scope": (
            "Публичные Binance USD-M данные WIFUSDT и DOTUSDT: 15-минутные свечи, "
            "funding, premium index и open interest; цикл примерно раз в минуту."
        ),
    },
    "dyn-iv113": {
        "summary": (
            "Дневная мультиактивная momentum-стратегия. Она выбирает самые ликвидные "
            "монеты, объединяет FLOW и absolute momentum, а состояние тренда BTC "
            "решает, разрешён ли риск вообще."
        ),
        "steps": [
            {
                "title": "Формирует ликвидную вселенную",
                "description": (
                    "Из 17 spot-активов оставляет до восьми с историей не короче "
                    "180 дней и медианным дневным объёмом не ниже $1 млн."
                ),
            },
            {
                "title": "Строит два семейства сигналов",
                "description": (
                    "FLOW оценивает положение закрытия внутри свечи с учётом объёма; "
                    "absolute momentum сравнивает цену на горизонтах 126 и 168 дней."
                ),
            },
            {
                "title": "Применяет BTC-фильтры",
                "description": (
                    "Риск разрешает хотя бы один трендовый фильтр: BTC/EMA100, "
                    "BTC/SMA150 или пересечение EMA50 и EMA200."
                ),
            },
            {
                "title": "Нормирует и исполняет paper-веса",
                "description": (
                    "Лучшие активы получают inverse-volatility веса; общий gross "
                    "масштабируется к целевой волатильности и ребалансируется недельно."
                ),
            },
        ],
        "entry_conditions": [
            "Не менее шести пригодных активов после фильтров истории и ликвидности.",
            "Хотя бы один из трёх BTC trend-фильтров разрешает рыночный риск.",
            "FLOW либо положительный absolute momentum выводит актив в верхнюю группу.",
            "Изменение веса достаточно велико для очередной недельной ребалансировки.",
        ],
        "exit_conditions": [
            "Актив теряет положительный momentum или выпадает из верхнего ранга.",
            "BTC-фильтры выключают режим риска — целевой портфель уходит в CASH.",
            "Актив перестаёт проходить требования истории, свежести или ликвидности.",
        ],
        "risk_controls": [
            "Inverse-volatility веса уменьшают вклад более волатильных монет.",
            "Gross ограничен 2.5×, вес одного актива — 1×.",
            "В расчёте учитываются 30 bps execution cost и 25% годового financing.",
            "Сигналы используют закрытые свечи с причинным лагом; режим fail-closed.",
        ],
        "data_scope": (
            "Публичные дневные spot-свечи Binance для 17 активов и live mark для "
            "переоценки paper-портфеля."
        ),
    },
    "atlas-nx-r1": {
        "summary": (
            "Новый paper-successor Atlas, восстановленный из доступных компонентов "
            "V27, V4 и V67. Он ищет устойчивый momentum, добавляет защитный BTC/ETH "
            "ансамбль и необратимо снижает риск после достижения high-water ступеней."
        ),
        "steps": [
            {
                "title": "Строит V27 momentum-ядро",
                "description": (
                    "По 9 spot-активам проверяет доходности за 63, 126 и 252 дня, "
                    "оставляет активы с двумя положительными горизонтами и выбирает top 3."
                ),
            },
            {
                "title": "Добавляет защитный V4",
                "description": (
                    "Для BTC и ETH объединяет breadth, dual momentum и Donchian "
                    "90/45; его вес растёт на high-water ступенях."
                ),
            },
            {
                "title": "Ограничивает общий риск",
                "description": (
                    "63-дневная волатильность задаёт множитель 1.0, 0.75 или 0.5, "
                    "после чего применяется gross-cap текущей risk stage."
                ),
            },
            {
                "title": "Ребалансирует только существенно",
                "description": (
                    "Новые веса вступают в силу по закрытой дневной свече, обычно в "
                    "понедельник и только при изменении turnover не меньше 10%."
                ),
            },
        ],
        "entry_conditions": [
            "Не менее семи активов имеют свежую достаточную дневную историю.",
            "У актива положительны минимум два горизонта из 63/126/252 дней.",
            "Актив входит в top 3 по среднему положительному momentum.",
            "Наступил недельный rebalance и изменение веса превышает no-trade band 10%.",
        ],
        "exit_conditions": [
            "Momentum больше не проходит два из трёх горизонтов или актив покидает top 3.",
            "Нулевой целевой портфель закрывается сразу, не дожидаясь понедельника.",
            "Устаревший или недоступный актив исключается из текущей вселенной.",
        ],
        "risk_controls": [
            "High-water 1.75× и 2.5× необратимо переводит risk stage с 0 на 1 и 2.",
            "Защитный вес растёт 0% → 10% → 20%, gross-cap падает 1.10× → 1.05× → 1.00×.",
            "При волатильности выше 25%/35% exposure уменьшается до 0.75×/0.50×.",
            "V67 равен нулю без on-chain публикации не старше 48 часов.",
            "Учитываются 40 bps turnover cost и 6% годового financing сверх 1× gross.",
        ],
        "data_scope": (
            "Публичные дневные spot-свечи Binance для ADA, BCH, BNB, BTC, DOGE, "
            "EOS, ETH, LTC и XRP; старые результаты V75 и его forward clock не наследуются."
        ),
    },
    "ds40180-t50c3": {
        "summary": (
            "Направленная multi-asset trend-following paper-стратегия с long и "
            "short рукавами, трёхступенчатым bear-режимом и контролируемым плечом."
        ),
        "steps": [
            {
                "title": "Строит три трендовых рукава",
                "description": (
                    "Long-only, light hedge и slow-bear используют причинные "
                    "Donchian, momentum и EMA сигналы на закрытых OKX 1Dutc свечах."
                ),
            },
            {
                "title": "Различает ранний и подтверждённый bear",
                "description": (
                    "40-дневный триггер раньше подключает половинный short budget, "
                    "а RE-180 включает полный slow-bear режим."
                ),
            },
            {
                "title": "Масштабирует только допустимый риск",
                "description": (
                    "Funding guard, covariance stress, корреляция и 4h crisis overlay "
                    "формируют paper target с динамическим gross-cap."
                ),
            },
            {
                "title": "Фиксирует forward ledger",
                "description": (
                    "Уже обработанные дни не пересчитываются: state пишется атомарно, "
                    "а события добавляются в hash-chain journal."
                ),
            },
        ],
        "entry_conditions": [
            "Не менее восьми ликвидных OKX USDT swaps имеют достаточную историю.",
            "Актив проходит Donchian/momentum/EMA фильтр соответствующего рукава.",
            "Funding, covariance stress и контрактные лимиты разрешают размер позиции.",
            "Изменение превышает no-trade band либо является выходом, sign flip или снижением риска.",
        ],
        "exit_conditions": [
            "Трендовый сигнал соответствующего рукава выключился.",
            "Режим рынка сократил long/short budget.",
            "Funding guard, dynamic gross-cap либо crisis overlay потребовал уменьшить риск.",
        ],
        "risk_controls": [
            "Абсолютный paper gross-cap 1.50×; stress/base/calm уровни 0.75×/1.25×/1.50×.",
            "Один контракт ограничен 25% NAV; 4h crisis overlay — 15% gross.",
            "Adverse funding выше 5/12/20% годовых последовательно уменьшает дорогую сторону.",
            "Paper fills используют публичный OKX bid/ask, impact и no-trade band.",
            "Реальные ордера и authenticated exchange client отсутствуют."
        ],
        "data_scope": (
            "Публичные OKX USDT perpetuals: закрытые 1Dutc и 4H свечи, "
            "mark price, ticker bid/ask, funding history и current funding."
        ),
    },
    "atlas-nx-blocked": {
        "summary": (
            "Зарезервированный runtime исходного V75 ATLAS-NX. Он остаётся "
            "fail-closed, пока точный канонический target producer отсутствует."
        ),
        "steps": [
            {
                "title": "Принимает только канонические веса",
                "description": "Runtime не подменяет отсутствующий V75 другим алгоритмом.",
            },
            {
                "title": "Проверяет provenance",
                "description": "Producer должен совпасть с зафиксированным SHA-256.",
            },
            {
                "title": "Ведёт отдельный paper-ledger",
                "description": (
                    "После материализации веса будут учитывать комиссии, funding и "
                    "сверку циклов отдельно от других стратегий."
                ),
            },
        ],
        "entry_conditions": [
            "Канонический V75 target producer доступен и прошёл проверку SHA-256.",
            "Scheduler получил свежие закрытые дневные веса.",
        ],
        "exit_conditions": [
            "Целевой вес закрывается каноническим producer либо fail-closed проверкой.",
        ],
        "risk_controls": [
            "Без точного producer новые позиции запрещены.",
            "Исторические результаты не используются как разрешение live-торговли.",
            "Exchange submission отсутствует: runtime остаётся paper-only.",
        ],
        "data_scope": "Только проверенные веса V75 и отдельная runtime-телеметрия.",
    },
}


def _full_description(strategy_id: str, current_state: str) -> dict[str, Any]:
    guide = STRATEGY_GUIDES[strategy_id]
    return {**guide, "current_state": current_state}


def _number_text(value: object, digits: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):.{digits}f}"


def _best_funding_spread(scan: dict[str, Any]) -> float | None:
    values: list[float] = []
    for candidate in scan.get("candidates") or []:
        if isinstance(candidate, dict):
            value = candidate.get("current_spread_bps_8h")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    for rejection in scan.get("rejections") or []:
        if not isinstance(rejection, dict):
            continue
        details = rejection.get("details")
        if not isinstance(details, dict):
            continue
        value = details.get("current_spread_bps_8h")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return max(values) if values else None


def _failed_summary(
    context: dict[str, Any], labels: dict[str, str], *, fallback: str
) -> str:
    failed = context.get("failed")
    if not isinstance(failed, list) or not failed:
        return fallback
    descriptions = [labels.get(str(item), str(item)) for item in failed]
    visible = descriptions[:2]
    suffix = f" и ещё {len(descriptions) - 2}" if len(descriptions) > 2 else ""
    return f"{', '.join(visible)}{suffix}"


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "FIN-Strategy-Hub/1.0"})
    with urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError(f"upstream did not return a JSON object: {url}")
    return value


class UpstreamSnapshotCache:
    def __init__(
        self,
        fetcher: Callable[[str, float], dict[str, Any]] = _fetch_json,
        *,
        ttl_seconds: float = UPSTREAM_CACHE_SECONDS,
        timeout_seconds: float = UPSTREAM_TIMEOUT_SECONDS,
    ) -> None:
        self.fetcher = fetcher
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, url: str) -> tuple[dict[str, Any] | None, str | None, bool]:
        with self._lock:
            cached = self._items.get(url)
            if cached and time.monotonic() - cached[0] < self.ttl_seconds:
                return dict(cached[1]), None, False
            try:
                value = self.fetcher(url, self.timeout_seconds)
            except (OSError, TypeError, ValueError) as error:
                if cached:
                    return dict(cached[1]), f"{type(error).__name__}: {error}", True
                return None, f"{type(error).__name__}: {error}", True
            self._items[url] = (time.monotonic(), dict(value))
            return value, None, False


def read_consensus_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 1,
            "strategy_id": "consensus-wif-dot-v1",
            "mode": "paper",
            "health": "starting",
            "paper": {
                "starting_balance_usdt": 10_000.0,
                "equity_usdt": 10_000.0,
                "realized_pnl_usdt": 0.0,
                "unrealized_pnl_usdt": 0.0,
                "closed_positions": 0,
                "positions": [],
                "equity_history": [],
            },
            "risk_state": {
                "mode": "base",
                "initial_equity_usdt": 10_000.0,
                "equity_usdt": 10_000.0,
                "high_water_equity_usdt": 10_000.0,
                "last_derisk_high_water_equity_usdt": 10_000.0,
            },
            "candles": [],
            "signals": [],
            "signal_context": {"wif": None, "dot": None},
            "errors": ["WIF/DOT paper worker is starting"],
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported WIF/DOT paper snapshot")
    return value


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (
        parsed.astimezone(UTC)
        if parsed.tzinfo is not None
        else parsed.replace(tzinfo=UTC)
    )


def read_dyn_snapshot(
    path: Path, *, stale_after_seconds: float = DYN_STALE_AFTER_SECONDS
) -> tuple[dict[str, Any] | None, str | None, bool]:
    if not path.is_file():
        return None, "local DYN snapshot is not available", True
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}", True
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None, "unsupported local DYN snapshot schema", True
    if value.get("strategyId") != "DYN-IV113":
        return None, "unexpected local DYN strategy identity", True
    generated_at = _parse_timestamp(value.get("generatedAt"))
    if generated_at is None:
        return value, "local DYN snapshot has no valid generatedAt", True
    age_seconds = max(0.0, (datetime.now(UTC) - generated_at).total_seconds())
    if age_seconds > stale_after_seconds:
        return value, f"local DYN snapshot is stale ({age_seconds:.1f}s)", True
    return value, None, False


def read_atlas_snapshot(
    path: Path, *, stale_after_seconds: float = ATLAS_STALE_AFTER_SECONDS
) -> tuple[dict[str, Any] | None, str | None, bool]:
    if not path.is_file():
        return None, "local Atlas NX R1 snapshot is not available", True
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}", True
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None, "unsupported local Atlas NX R1 snapshot schema", True
    if value.get("strategyId") != "atlas_nx_r1":
        return None, "unexpected local Atlas NX R1 strategy identity", True
    generated_at = _parse_timestamp(value.get("generatedAt"))
    if generated_at is None:
        return value, "local Atlas NX R1 snapshot has no valid generatedAt", True
    age_seconds = max(0.0, (datetime.now(UTC) - generated_at).total_seconds())
    if age_seconds > stale_after_seconds:
        return value, f"local Atlas NX R1 snapshot is stale ({age_seconds:.1f}s)", True
    return value, None, False


def read_ds40180_snapshot(
    path: Path, *, stale_after_seconds: float = DS40180_STALE_AFTER_SECONDS
) -> tuple[dict[str, Any] | None, str | None, bool]:
    if not path.is_file():
        return None, "local DS-40/180 snapshot is not available", True
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}", True
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        return None, "unsupported local DS-40/180 snapshot schema", True
    if value.get("strategyId") != "ds40180_t50c3_okx_paper":
        return None, "unexpected local DS-40/180 strategy identity", True
    generated_at = _parse_timestamp(value.get("generatedAt"))
    if generated_at is None:
        return value, "local DS-40/180 snapshot has no valid generatedAt", True
    age_seconds = max(0.0, (datetime.now(UTC) - generated_at).total_seconds())
    if age_seconds > stale_after_seconds:
        return value, f"local DS-40/180 snapshot is stale ({age_seconds:.1f}s)", True
    return value, None, False


def _funding_strategy(paper_snapshot: dict[str, Any]) -> dict[str, Any]:
    account = paper_snapshot.get("paper")
    account = account if isinstance(account, dict) else {}
    position = account.get("open_position")
    scan = paper_snapshot.get("scan")
    scan = scan if isinstance(scan, dict) else {}
    risk = paper_snapshot.get("risk")
    risk = risk if isinstance(risk, dict) else {}
    candidates = scan.get("candidates")
    candidates = candidates if isinstance(candidates, list) else []
    rejections = scan.get("rejections")
    rejections = rejections if isinstance(rejections, list) else []
    best_spread = _best_funding_spread(scan)
    current_threshold = _number(risk.get("min_current_spread_bps_8h"), 8.0)
    predicted_threshold = _number(risk.get("min_predicted_spread_bps_8h"), 5.0)
    net_threshold = _number(risk.get("min_expected_net_bps"), 10.0)
    starting = _number(account.get("starting_balance_usdt"), 10_000.0)
    equity = _number(account.get("equity_usdt"), starting)
    pnl = equity - starting
    open_count = 1 if isinstance(position, dict) else 0
    if open_count:
        why_now = (
            "Paper-пара уже открыта: одна биржа куплена, другая продана, "
            "поэтому движение цены в основном взаимно компенсируется."
        )
        waiting_for = (
            "Ждём, пока чистая доходность по funding исчезнет, либо сработает "
            "лимит удержания; затем обе paper-ноги закроются вместе."
        )
    elif candidates:
        why_now = (
            f"Найдено подходящих пар: {len(candidates)}. Paper-вход должен "
            "появиться в ближайшем цикле сканера."
        )
        waiting_for = "Ждём одновременного открытия лонга и шорта в paper-паре."
    else:
        spread_text = (
            f"{best_spread:.2f} bps за 8ч"
            if best_spread is not None
            else "не рассчитан"
        )
        why_now = (
            f"Сделки нет: лучший текущий спред — {spread_text}, "
            f"а для входа нужно минимум {current_threshold:.2f} bps."
        )
        waiting_for = (
            f"Текущий спред ≥ {current_threshold:.2f} bps, прогноз ≥ "
            f"{predicted_threshold:.2f} bps и ожидаемый доход после расходов ≥ "
            f"{net_threshold:.2f} bps при достаточной ликвидности."
        )
    return {
        "id": "funding-neutral",
        "repository": "fin",
        "name": "Funding Neutral",
        "description": "Нейтральный funding-спред между Binance и Bybit.",
        "mode": "paper",
        "status": "running"
        if paper_snapshot.get("health") == "healthy"
        else "degraded",
        "status_label": "Позиция открыта" if open_count else "Сканирует спреды",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": open_count,
        "closed_positions": int(_number(account.get("closed_positions"))),
        "market": "Binance ↔ Bybit perpetuals",
        "timeframe": "5 sec · 1m candles",
        "updated_at_ms": paper_snapshot.get("updated_at_ms"),
        "positions": [position] if isinstance(position, dict) else [],
        "equity_history": account.get("equity_history") or [],
        "signals": candidates,
        "detail": {
            "markets": len(paper_snapshot.get("markets") or []),
            "entry_threshold_bps": risk.get("min_current_spread_bps_8h"),
            "rejections": len(rejections),
        },
        "context": {
            "how_it_works": (
                "Покупает бессрочный фьючерс там, где funding ниже, и одновременно "
                "шортит тот же актив там, где funding выше."
            ),
            "why_now": why_now,
            "waiting_for": waiting_for,
            "metrics": [
                _metric(
                    "Лучший спред",
                    f"{best_spread:.2f} bps / 8ч" if best_spread is not None else "—",
                ),
                _metric("Порог входа", f"{current_threshold:.2f} bps / 8ч"),
                _metric("Кандидаты", str(len(candidates))),
            ],
            "full_description": _full_description("funding-neutral", why_now),
        },
    }


def _atlas_strategy(runtime: dict[str, Any]) -> dict[str, Any]:
    strategies = runtime.get("strategies")
    rows = strategies if isinstance(strategies, list) else []
    source = next(
        (item for item in rows if item.get("strategy_id") == "v75_atlas_nx"), {}
    )
    account = source.get("account") if isinstance(source, dict) else {}
    account = account if isinstance(account, dict) else {}
    starting = 10_000.0
    equity = _number(account.get("equity"), starting)
    pnl = equity - starting
    observations = int(_number(source.get("observation_count")))
    committed_cycles = int(_number(source.get("committed_cycles")))
    scheduler_state = runtime.get("scheduler", {}).get("state")
    source_health = str(source.get("health", "starting"))
    status = "blocked" if observations == 0 else source_health
    if status == "healthy":
        status = "running"
    why_now = (
        "Циклов ещё не было: канонический V75 target producer отсутствует "
        "в main и реестр runtime запрещает заменять его другим алгоритмом."
        if observations == 0
        else (
            f"Обработано наблюдений: {observations}; завершено циклов: "
            f"{committed_cycles}."
        )
    )
    waiting_for = (
        "Нужен исходник V75 с SHA-256 "
        "3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc; "
        "после его материализации scheduler сможет выполнять paper-циклы."
        if observations == 0
        else "Ждём следующие закрытые дневные веса и плановый цикл scheduler."
    )
    return {
        "id": "atlas-nx",
        "repository": "fin",
        "name": "Atlas NX",
        "description": "Портфельный FIN runtime с проверяемым paper-ledger.",
        "mode": "paper",
        "status": status,
        "status_label": "Наблюдает" if observations else "Нет V75 producer",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": int(_number(account.get("spot_position_count")))
        + int(_number(account.get("perp_position_count"))),
        "closed_positions": int(_number(source.get("committed_cycles"))),
        "market": "Multi-asset portfolio",
        "timeframe": "scheduler",
        "updated_at_ms": None,
        "positions": [],
        "equity_history": [],
        "signals": [],
        "detail": {
            "observations": observations,
            "committed_cycles": committed_cycles,
            "scheduler": scheduler_state,
        },
        "context": {
            "how_it_works": (
                "Получает дневные веса портфеля V75 ATLAS-NX и проводит их "
                "через отдельный paper-счёт с комиссиями, funding и сверкой."
            ),
            "why_now": why_now,
            "waiting_for": waiting_for,
            "metrics": [
                _metric("Наблюдения", str(observations)),
                _metric("Циклы", str(committed_cycles)),
                _metric("Scheduler", str(scheduler_state or "—")),
            ],
            "full_description": _full_description("atlas-nx-blocked", why_now),
        },
    }


def _atlas_reconstructed_strategy(
    snapshot: dict[str, Any] | None, error: str | None, stale: bool
) -> dict[str, Any]:
    source = snapshot or {}
    paper = source.get("paper") if isinstance(source, dict) else {}
    paper = paper if isinstance(paper, dict) else {}
    account = paper.get("account")
    account = account if isinstance(account, dict) else {}
    starting = _number(account.get("initialNavUsd"), 10_000.0)
    equity = _number(paper.get("navUsd"), starting)
    pnl = equity - starting
    positions = source.get("positions")
    positions = positions if isinstance(positions, list) else []
    target_gross = _number(source.get("targetGross"))
    cash_weight = _number(source.get("cashWeight"), 1.0)
    stage = int(_number(source.get("ratchetStage")))
    defensive_weight = _number(source.get("defensiveWeight"))
    vol_multiplier = _number(source.get("volatilityMultiplier"))
    accelerator = _number(source.get("onchainAcceleratorScale"))
    unavailable = snapshot is None or bool(error) or stale
    status = "degraded" if unavailable else str(source.get("status", "starting"))
    if status == "ready":
        status = "running"
    if unavailable:
        why_now = (
            "Локальный расчёт Atlas NX R1 пока недоступен или устарел; последнее "
            "состояние показано только справочно."
        )
    elif positions:
        why_now = (
            f"Открыто paper-позиций: {len(positions)}, целевая gross-экспозиция "
            f"{target_gross:.2f}×. Риск-ступень {stage}, волатильностный множитель "
            f"{vol_multiplier:.2f}×."
        )
    elif vol_multiplier <= 0:
        why_now = (
            "Стратегия в кэше: для устойчивой оценки волатильности пока недостаточно "
            "закрытых свечей после применения причинного лага."
        )
    else:
        why_now = (
            "Стратегия в кэше: недельный ребаланс и momentum/trend-фильтры пока не "
            "разрешили материальную позицию."
        )
    updated_at = source.get("marketDataAt") or source.get("generatedAt")
    updated_at_datetime = _parse_timestamp(updated_at)
    return {
        "id": "atlas-nx",
        "repository": "fin",
        "name": "Atlas NX R1",
        "description": "Восстановленный портфель V27 + V4 с fail-closed V67.",
        "mode": "paper",
        "status": status,
        "status_label": (
            "Нет данных" if unavailable else "В рынке" if positions else "Режим CASH"
        ),
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": len(positions),
        "closed_positions": int(_number(paper.get("totalExecutions"))),
        "market": "Binance spot · 9 assets",
        "timeframe": "daily close · live marks",
        "updated_at_ms": (
            int(updated_at_datetime.timestamp() * 1000)
            if updated_at_datetime is not None
            else None
        ),
        "updated_at": updated_at,
        "positions": positions,
        "candles": source.get("candles") or [],
        "equity_history": paper.get("daily") or [],
        "signals": [],
        "detail": {
            "strategy_id": source.get("strategyId", "atlas_nx_r1"),
            "predecessor_strategy_id": source.get(
                "predecessorStrategyId", "v75_atlas_nx"
            ),
            "identity_kind": source.get("identityKind", "reconstruction"),
            "historical_metrics_inherited": source.get(
                "historicalMetricsInherited", False
            ),
            "target_gross": target_gross,
            "cash_weight": cash_weight,
            "ratchet_stage": stage,
            "defensive_weight": defensive_weight,
            "volatility_multiplier": vol_multiplier,
            "onchain_accelerator_scale": accelerator,
            "onchain_status": source.get("onchainStatus"),
            "upstream_error": error,
            "upstream_stale": stale,
        },
        "context": {
            "how_it_works": (
                "Под новой identity объединяет momentum/trend ядро V27 и защитный "
                "ансамбль V4; high-water ratchet, gross-cap и волатильностный фильтр "
                "необратимо уменьшают риск. Старые результаты V75 не наследуются."
            ),
            "why_now": why_now,
            "waiting_for": (
                "Ждём следующую закрытую дневную свечу и недельный ребаланс. V67 "
                "on-chain ускоритель останется нулевым, пока нет свежего снимка "
                "публикаций не старше 48 часов."
            ),
            "metrics": [
                _metric("Risk stage", f"{stage}/2"),
                _metric("Target gross", f"{target_gross:.2f}×"),
                _metric("V4 защита", f"{defensive_weight * 100:.0f}%"),
                _metric("V67 accelerator", f"{accelerator:.2f}×"),
            ],
            "full_description": _full_description("atlas-nx-r1", why_now),
        },
    }


def _ds40180_strategy(
    snapshot: dict[str, Any] | None, error: str | None, stale: bool
) -> dict[str, Any]:
    source = snapshot or {}
    paper = source.get("paper") if isinstance(source, dict) else {}
    paper = paper if isinstance(paper, dict) else {}
    account = paper.get("account")
    account = account if isinstance(account, dict) else {}
    starting = _number(account.get("initialNavUsd"), 10_000.0)
    equity = _number(paper.get("navUsd"), starting)
    pnl = equity - starting
    positions = source.get("positions")
    positions = positions if isinstance(positions, list) else []
    target_gross = _number(source.get("targetGross"))
    target_net = _number(source.get("targetNet"))
    dynamic_cap = _number(source.get("dynamicGrossCap"), 1.25)
    risk_scale = _number(source.get("riskScale"), 1.0)
    regime = source.get("regime")
    regime = regime if isinstance(regime, dict) else {}
    regime_state = str(regime.get("state") or "unknown")
    overlays = source.get("overlays")
    overlays = overlays if isinstance(overlays, dict) else {}
    crisis = overlays.get("crisis4h")
    crisis = crisis if isinstance(crisis, dict) else {}
    persistence = source.get("persistence")
    persistence = persistence if isinstance(persistence, dict) else {}
    journal = persistence.get("journal")
    journal = journal if isinstance(journal, dict) else {}
    comparison = source.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    ab_observations = int(_number(comparison.get("forwardObservationDays")))
    ab_deltas = comparison.get("deltasV2MinusV1")
    ab_deltas = ab_deltas if isinstance(ab_deltas, dict) else {}
    unavailable = snapshot is None or bool(error) or stale
    status = "degraded" if unavailable else str(source.get("status", "starting"))
    if status == "ready":
        status = "running"
    if unavailable:
        why_now = (
            "Локальный DS-40/180 snapshot отсутствует или устарел; последнее "
            "состояние нельзя считать действующим paper-решением."
        )
        waiting_for = "Ждём свежий OKX public-data цикл и валидный hash-chain journal."
    elif positions:
        why_now = (
            f"Открыто paper-позиций: {len(positions)}; target gross {target_gross:.2f}×, "
            f"net {target_net:.2f}×, режим {regime_state}, dynamic cap {dynamic_cap:.2f}×."
        )
        waiting_for = (
            "Ждём следующую закрытую 1Dutc свечу; funding, covariance stress и "
            "4h crisis overlay продолжают переоцениваться внутри paper-контура."
        )
    else:
        why_now = (
            f"Стратегия без открытых позиций: режим {regime_state}, target gross "
            f"{target_gross:.2f}× и dynamic cap {dynamic_cap:.2f}×."
        )
        waiting_for = "Ждём причинный трендовый сигнал, проходящий funding и risk guards."
    updated_at = source.get("marketDataAt") or source.get("generatedAt")
    updated_at_datetime = _parse_timestamp(updated_at)
    return {
        "id": "ds40180-t50c3",
        "repository": "fin",
        "name": "DS-40/180 T50-C3 v2",
        "description": "OKX long/short trend paper с funding и covariance guards.",
        "mode": "paper",
        "status": status,
        "status_label": (
            "Нет данных"
            if unavailable
            else "Crisis short"
            if crisis.get("active")
            else "В рынке"
            if positions
            else "Режим CASH"
        ),
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": len(positions),
        "closed_positions": int(_number(paper.get("totalExecutions"))),
        "market": "OKX USDT swaps · 13 assets",
        "timeframe": "1Dutc core · 4H crisis",
        "updated_at_ms": (
            int(updated_at_datetime.timestamp() * 1000)
            if updated_at_datetime is not None
            else None
        ),
        "updated_at": updated_at,
        "positions": positions,
        "candles": source.get("candles") or [],
        "equity_history": paper.get("daily") or [],
        "signals": [],
        "detail": {
            "strategy_id": source.get("strategyId", "ds40180_t50c3_okx_paper"),
            "strategy_version": source.get("strategyVersion", "okx-paper-v2"),
            "target_gross": target_gross,
            "target_net": target_net,
            "dynamic_gross_cap": dynamic_cap,
            "gross_cap_regime": source.get("grossCapRegime"),
            "risk_scale": risk_scale,
            "regime": regime,
            "crisis_4h": crisis,
            "journal_valid": journal.get("valid"),
            "journal_events": journal.get("events"),
            "forward_ab": comparison,
            "forward_ab_status": comparison.get("status"),
            "forward_ab_observations": ab_observations,
            "forward_ab_return_delta": ab_deltas.get("returnSinceReset"),
            "upstream_error": error,
            "upstream_stale": stale,
        },
        "context": {
            "how_it_works": (
                "Объединяет long-only, light-hedge и slow-bear рукава; затем funding, "
                "covariance stress, 4h crisis overlay и no-trade band формируют paper fills."
            ),
            "why_now": why_now,
            "waiting_for": waiting_for,
            "metrics": [
                _metric("Режим", regime_state),
                _metric("Target gross", f"{target_gross:.2f}×"),
                _metric("Dynamic cap", f"{dynamic_cap:.2f}×"),
                _metric("Risk scale", f"{risk_scale:.2f}×"),
                _metric("A/B forward", f"{ab_observations}/90"),
                _metric("Journal", str(journal.get("events") or 0)),
            ],
            "full_description": _full_description("ds40180-t50c3", why_now),
        },
    }


def _consensus_strategy(snapshot: dict[str, Any]) -> dict[str, Any]:
    account = snapshot.get("paper")
    account = account if isinstance(account, dict) else {}
    starting = _number(account.get("starting_balance_usdt"), 10_000.0)
    equity = _number(account.get("equity_usdt"), starting)
    pnl = equity - starting
    positions = account.get("positions")
    positions = positions if isinstance(positions, list) else []
    signals = snapshot.get("signals")
    signals = signals if isinstance(signals, list) else []
    risk_state = snapshot.get("risk_state")
    risk_state = risk_state if isinstance(risk_state, dict) else {}
    signal_context = snapshot.get("signal_context")
    signal_context = signal_context if isinstance(signal_context, dict) else {}
    wif_context = signal_context.get("wif")
    wif_context = wif_context if isinstance(wif_context, dict) else {}
    dot_context = signal_context.get("dot")
    dot_context = dot_context if isinstance(dot_context, dict) else {}
    wif_reason = _failed_summary(
        wif_context,
        {
            "weekday": "сегодня не сигнальный день UTC",
            "move_45m_atr": "падение слабее 2 ATR",
            "volume_z": "нет всплеска объёма",
            "lower_wick": "нижняя тень короче 50%",
            "close_location": "закрытие недостаточно высоко",
            "taker_imbalance": "продавцы слишком агрессивны",
            "oi_z": "открытый интерес ещё не сброшен",
            "strength": "общая сила ниже 3.5",
        },
        fallback="полный WIF-сигнал не сформирован",
    )
    dot_reason = _failed_summary(
        dot_context,
        {
            "weekday": "сегодня DOT-модуль выключен",
            "window": "сейчас не окно 15–30 минут после funding",
            "funding": "funding недостаточно отрицательный",
        },
        fallback="полный DOT-сигнал не сформирован",
    )
    if positions:
        assets = ", ".join(
            str(item.get("asset") or item.get("symbol")) for item in positions
        )
        why_now = f"Открыты paper-позиции: {assets}. Риск-контроль продолжает следить за стопом и временем удержания."
    elif signals:
        assets = ", ".join(
            str(item.get("asset") or item.get("symbol")) for item in signals
        )
        why_now = f"Сигнал подтверждён для {assets}; paper-счёт обрабатывает вход."
    else:
        why_now = f"Сделки нет. WIF: {wif_reason}. DOT: {dot_reason}."
    return {
        "id": "consensus-wif-dot",
        "repository": "trader",
        "name": "Consensus WIF + DOT",
        "description": "WIF OI-flush и DOT negative-funding rebound.",
        "mode": "paper",
        "status": "running"
        if snapshot.get("health") == "healthy"
        else snapshot.get("health", "starting"),
        "status_label": "Позиция открыта" if positions else "Ищет подтверждение",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": len(positions),
        "closed_positions": int(_number(account.get("closed_positions"))),
        "market": "Binance USD-M · WIF / DOT",
        "timeframe": "60 sec · 15m signals",
        "updated_at_ms": snapshot.get("market_data_at_ms"),
        "positions": positions,
        "equity_history": account.get("equity_history") or [],
        "signals": signals,
        "candles": snapshot.get("candles") or [],
        "detail": {
            "risk_mode": risk_state.get("mode", "base"),
            "high_water_equity_usdt": risk_state.get("high_water_equity_usdt"),
            "max_gross": MAX_GROSS_LEVERAGE,
            "errors": snapshot.get("errors") or [],
            **(snapshot.get("diagnostics") or {}),
        },
        "context": {
            "how_it_works": (
                "Покупает WIF после резкого сброса цены и открытого интереса либо "
                "DOT после аномально отрицательного funding."
            ),
            "why_now": why_now,
            "waiting_for": (
                "WIF: падение ≥ 2 ATR со всплеском объёма, сбросом OI и сильным "
                "отскоком свечи. DOT: funding ≤ −2.25/−2.50 bps в окне 15–30 минут."
            ),
            "metrics": [
                _metric(
                    "WIF 45м", f"{_number_text(wif_context.get('move_45m_atr'))} ATR"
                ),
                _metric("WIF OI z", _number_text(wif_context.get("oi_z"))),
                _metric(
                    "DOT funding",
                    f"{_number_text(dot_context.get('funding_rate_bps'))} bps",
                ),
                _metric("Риск-режим", str(risk_state.get("mode", "base"))),
            ],
            "full_description": _full_description("consensus-wif-dot", why_now),
        },
    }


def _dyn_strategy(
    snapshot: dict[str, Any] | None, error: str | None, stale: bool
) -> dict[str, Any]:
    source = snapshot or {}
    paper = source.get("paper") if isinstance(source, dict) else {}
    paper = paper if isinstance(paper, dict) else {}
    account = paper.get("account")
    account = account if isinstance(account, dict) else {}
    starting = _number(account.get("initialNavUsd"), 10_000.0)
    equity = _number(paper.get("navUsd"), starting)
    pnl = equity - starting
    positions = source.get("positions")
    positions = positions if isinstance(positions, list) else []
    eligible_assets = source.get("eligibleAssets")
    eligible_assets = eligible_assets if isinstance(eligible_assets, list) else []
    btc_filters = source.get("btcFilters")
    btc_filters = btc_filters if isinstance(btc_filters, list) else []
    btc_score = int(_number(source.get("btcConsensusScore")))
    target_gross = _number(source.get("targetGross"))
    cash_weight = _number(source.get("cashWeight"), 1.0)
    status = "degraded" if error or stale else str(source.get("status", "starting"))
    if status == "ready":
        status = "running"
    unavailable = snapshot is None or bool(error) or stale
    if unavailable:
        why_now = (
            "Рыночные данные DYN сейчас недоступны, поэтому CASH не считается "
            "решением стратегии. Последний известный снимок показан только как справочный."
        )
        waiting_for = (
            "Ждём свежий локальный расчёт по 17 закрытым дневным свечным рядам Binance."
        )
    elif positions:
        why_now = (
            f"В портфеле {len(positions)} paper-позиций; целевая gross-экспозиция "
            f"{target_gross:.2f}×."
        )
        waiting_for = (
            "Ждём следующей недельной ребалансировки либо отключения BTC-фильтра."
        )
    elif btc_score == 0:
        why_now = (
            "Стратегия в кэше: ни один из трёх трендовых BTC-фильтров сейчас "
            "не разрешает рыночный риск."
        )
        waiting_for = (
            "Ждём хотя бы один положительный BTC-фильтр: BTC выше EMA100 с "
            "EMA20 выше EMA100, BTC выше SMA150 или EMA50 выше EMA200."
        )
    elif not eligible_assets:
        why_now = "Стратегия в кэше: после фильтров ликвидности не осталось доступных активов."
        waiting_for = "Ждём активы с историей ≥ 180 дней и достаточным объёмом."
    else:
        why_now = (
            "BTC-фильтр разрешает риск, но итоговый вес FLOW/momentum пока равен нулю."
        )
        waiting_for = "Ждём положительный FLOW или absolute momentum на ребалансировке."
    updated_at = source.get("marketDataAt") or source.get("generatedAt")
    updated_at_datetime = _parse_timestamp(updated_at)
    return {
        "id": "dyn-iv113",
        "repository": "fin2",
        "name": "DYN-IV113",
        "description": "Dynamic FLOW + Absolute Momentum на закрытых свечах Binance.",
        "mode": "paper",
        "status": status,
        "status_label": (
            "Нет данных" if unavailable else "В рынке" if positions else "Режим CASH"
        ),
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": len(positions),
        "closed_positions": int(_number(paper.get("totalExecutions"))),
        "market": "Binance spot · 17 assets",
        "timeframe": "daily close · live marks",
        "updated_at_ms": (
            int(updated_at_datetime.timestamp() * 1000)
            if updated_at_datetime is not None
            else None
        ),
        "updated_at": updated_at,
        "positions": positions,
        "candles": source.get("candles") or [],
        "equity_history": paper.get("daily") or [],
        "signals": [],
        "detail": {
            "target_gross": target_gross,
            "cash_weight": cash_weight,
            "btc_consensus_score": btc_score,
            "btc_filters": btc_filters,
            "eligible_assets": eligible_assets,
            "upstream_error": error,
            "upstream_stale": stale,
        },
        "context": {
            "how_it_works": (
                "Ранжирует ликвидные монеты по FLOW и положительному momentum, "
                "а трендовые BTC-фильтры решают, держать портфель или уйти в кэш."
            ),
            "why_now": why_now,
            "waiting_for": waiting_for,
            "metrics": [
                _metric("BTC режим", f"{btc_score}/3"),
                _metric("Доступно активов", str(len(eligible_assets))),
                _metric("Target gross", f"{target_gross:.2f}×"),
                _metric("Cash", f"{cash_weight * 100:.0f}%"),
            ],
            "full_description": _full_description("dyn-iv113", why_now),
        },
    }


MAX_GROSS_LEVERAGE = 3.0


class StrategyHub:
    def __init__(
        self,
        *,
        fin2_url: str | None = None,
        dyn_snapshot_path: Path | None = None,
        atlas_snapshot_path: Path | None = None,
        ds40180_snapshot_path: Path | None = None,
        dyn_stale_after_seconds: float = DYN_STALE_AFTER_SECONDS,
        atlas_stale_after_seconds: float = ATLAS_STALE_AFTER_SECONDS,
        ds40180_stale_after_seconds: float = DS40180_STALE_AFTER_SECONDS,
        cache: UpstreamSnapshotCache | None = None,
    ) -> None:
        self.fin2_url = fin2_url or os.environ.get(
            "FIN2_FORWARD_URL", DEFAULT_FIN2_FORWARD_URL
        )
        self.dyn_snapshot_path = dyn_snapshot_path
        self.atlas_snapshot_path = atlas_snapshot_path
        self.ds40180_snapshot_path = ds40180_snapshot_path
        self.dyn_stale_after_seconds = dyn_stale_after_seconds
        self.atlas_stale_after_seconds = atlas_stale_after_seconds
        self.ds40180_stale_after_seconds = ds40180_stale_after_seconds
        self.cache = cache or UpstreamSnapshotCache()

    def snapshot(
        self,
        *,
        funding: dict[str, Any],
        runtime: dict[str, Any],
        consensus: dict[str, Any],
    ) -> dict[str, Any]:
        if self.dyn_snapshot_path is None:
            dyn, dyn_error, dyn_stale = self.cache.get(self.fin2_url)
        else:
            dyn, dyn_error, dyn_stale = read_dyn_snapshot(
                self.dyn_snapshot_path,
                stale_after_seconds=self.dyn_stale_after_seconds,
            )
        if self.atlas_snapshot_path is None:
            atlas = _atlas_strategy(runtime)
        else:
            atlas_snapshot, atlas_error, atlas_stale = read_atlas_snapshot(
                self.atlas_snapshot_path,
                stale_after_seconds=self.atlas_stale_after_seconds,
            )
            atlas = _atlas_reconstructed_strategy(
                atlas_snapshot, atlas_error, atlas_stale
            )
        if self.ds40180_snapshot_path is None:
            ds40180_snapshot = None
            ds40180_error = "local DS-40/180 snapshot path is not configured"
            ds40180_stale = True
        else:
            ds40180_snapshot, ds40180_error, ds40180_stale = read_ds40180_snapshot(
                self.ds40180_snapshot_path,
                stale_after_seconds=self.ds40180_stale_after_seconds,
            )
        strategies = [
            _funding_strategy(funding),
            _consensus_strategy(consensus),
            _dyn_strategy(dyn, dyn_error, dyn_stale),
            _ds40180_strategy(ds40180_snapshot, ds40180_error, ds40180_stale),
            atlas,
        ]
        paper_strategies = [item for item in strategies if item["mode"] == "paper"]
        equity = sum(_number(item.get("equity_usdt")) for item in paper_strategies)
        starting = sum(
            _number(item.get("starting_balance_usdt")) for item in paper_strategies
        )
        return {
            "schema_version": 1,
            "mode": "paper",
            "generated_at_ms": int(time.time() * 1000),
            "exchange_submission_available": False,
            "summary": {
                "strategy_count": len(strategies),
                "running_count": sum(
                    item["status"] == "running" for item in strategies
                ),
                "paper_equity_usdt": equity,
                "paper_starting_balance_usdt": starting,
                "paper_pnl_usdt": equity - starting,
                "open_positions": sum(
                    int(item["open_positions"]) for item in paper_strategies
                ),
            },
            "strategies": strategies,
        }
