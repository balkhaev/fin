from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def pct(value: float) -> str:
    return f"{100.0 * float(value):+.2f}%"


def number(value: float) -> str:
    return f"{float(value):.3f}"


def metric_rows(label: str, payload: dict) -> list[str]:
    return [
        f"| {label} | {pct(payload['annualized_return'])} | {pct(payload['total_return'])} | "
        f"{pct(payload['max_drawdown'])} | {number(payload['sharpe'])} | "
        f"{float(payload.get('annual_turnover', 0.0)):.2f}× | "
        f"{float(payload.get('max_gross', 0.0)):.3f}× |"
    ]


def main() -> None:
    summary = json.loads((RESULTS / "summary.json").read_text())
    yearly = pd.read_csv(RESULTS / "v117_yearly.csv")
    checks = summary["checks"]

    lines = [
        "# Active V111–V118 TRIDENT",
        "",
        "TRIDENT объединяет только три ранее прошедших самостоятельные проверки кривые:",
        "",
        "1. V75 ATLAS-NX crypto/on-chain core;",
        "2. V95 global crisis-alpha;",
        "3. V103 fixed-universe sector/country rotation.",
        "",
        f"**Frozen status:** `{summary['status']}`",
        "",
        "Реальная торговля и реальное плечо не разрешены:",
        "",
        f"- `live_ready = {str(summary['live_ready']).lower()}`;",
        f"- `real_leverage_authorized = {str(summary['real_leverage_authorized']).lower()}`.",
        "",
        "## Selection",
        "",
        "Веса и portfolio-risk параметры выбирались только на 2021–2023. Holdout 2024–2025 и final 2026 H1 были открыты после selection proof.",
        "",
        f"- static candidate: `{summary['static_selected']}`;",
        f"- dynamic ensemble: `{', '.join(summary['dynamic_selected'])}`;",
        f"- selection proof SHA-256: `{summary['selection_proof_sha256']}`.",
        "",
        "## Основные метрики",
        "",
        "| Кривая | CAGR | Total return | Max DD | Sharpe | Оборот/год | Max gross |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines += metric_rows("ATLAS prefinal", summary["atlas_prefinal"])
    lines += metric_rows("TRIDENT prefinal", summary["trident_prefinal"])
    lines += [
        f"| TRIDENT holdout 2024–2025 | {pct(summary['trident_holdout']['annualized_return'])} | "
        f"{pct(summary['trident_holdout']['total_return'])} | {pct(summary['trident_holdout']['max_drawdown'])} | "
        f"{number(summary['trident_holdout']['sharpe'])} | — | — |",
        f"| TRIDENT final 2026 H1 | {pct(summary['trident_final_2026h1']['annualized_return'])} | "
        f"{pct(summary['trident_final_2026h1']['total_return'])} | {pct(summary['trident_final_2026h1']['max_drawdown'])} | "
        f"{number(summary['trident_final_2026h1']['sharpe'])} | — | — |",
        "",
        "## Frozen promotion checks",
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- [{'x' if passed else ' '}] `{name}`")
    lines += [
        "",
        "## Задержки и стоимость перевода",
        "",
        "| Вариант | CAGR | Max DD | Sharpe |",
        "|---|---:|---:|---:|",
    ]
    for name, payload in summary["latency_costs"].items():
        lines.append(
            f"| {name} | {pct(payload['cagr'])} | {pct(payload['dd'])} | {number(payload['sharpe'])} |"
        )
    lines += [
        "",
        "## Годовая доходность frozen dynamic ensemble",
        "",
        "| Год | Доходность | Max DD |",
        "|---:|---:|---:|",
    ]
    for row in yearly.to_dict(orient="records"):
        lines.append(f"| {int(row['year'])} | {pct(row['return'])} | {pct(row['max_drawdown'])} |")
    audit = summary["audit"]
    lines += [
        "",
        "## Концентрация и rolling windows",
        "",
        f"- best positive-year log-growth share: {pct(audit['best_positive_year_log_share'])};",
        f"- worst rolling 252-day result: {pct(audit['worst_rolling_252'])};",
        f"- worst rolling 504-day result: {pct(audit['worst_rolling_504'])};",
        f"- positive rolling-252 fraction: {pct(audit['positive_rolling_252_fraction'])};",
        f"- positive rolling-504 fraction: {pct(audit['positive_rolling_504_fraction'])}.",
        "",
        "## Ограничения",
        "",
        "- Это historical proxy simulation, а не live execution proof.",
        "- Выбор TRIDENT сделан после того, как вся исследовательская программа уже видела большую часть истории; pristine program-level holdout отсутствует.",
        "- Отдельные sleeves используют ETF/FX proxy prices, а не полные фьючерсные цепочки с roll, intraday bid/ask и broker-specific margin.",
        "- Плечо portfolio layer остаётся только моделируемым и не разрешается для реального счёта.",
        "",
        "Все отрицательные проверки и все параметры сохранены в `results/`.",
    ]
    (ROOT / "REPORT_RU.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
