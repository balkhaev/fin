#!/usr/bin/env python3
"""Apply the DS-40/180 forward A/B observability integration."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch marker not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_worker() -> None:
    path = ROOT / "src/finruntime/strategies/ds40180_t50c3_paper.py"
    text = path.read_text(encoding="utf-8")
    ab_import = (
        "from ._ds40180_ab import "
        "AB_SCHEMA_VERSION, AB_STUDY_ID, build_ab_snapshot\n"
    )
    if ab_import not in text:
        text = text.replace(
            "from ._ds40180_common import (\n",
            ab_import + "from ._ds40180_common import (\n",
            1,
        )

    if "ab_snapshot_path: Path | None" not in text:
        start = text.index("def run_once(\n")
        end = text.index("\n\ndef main(", start)
        run_once = '''def run_once(
    path: Path,
    *,
    reset_date: str,
    initial_nav_usd: float,
    ab_snapshot_path: Path | None = None,
    ab_journal_path: Path | None = None,
    enable_ab: bool = True,
) -> dict[str, Any]:
    histories, failures = load_market_data(reset_date=reset_date)
    snapshot = compute_forward_state(
        histories,
        failures,
        snapshot_path=path,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    if enable_ab:
        ab_snapshot_path = ab_snapshot_path or (
            path.parent / "ds40180_t50c3_ab_snapshot.json"
        )
        ab_journal_path = ab_journal_path or (
            path.parent / "ds40180_t50c3_ab_events.jsonl"
        )
        try:
            snapshot["comparison"] = build_ab_snapshot(
                histories,
                failures,
                v2_snapshot=snapshot,
                snapshot_path=ab_snapshot_path,
                journal_path=ab_journal_path,
                reset_date=reset_date,
                initial_nav_usd=initial_nav_usd,
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            ArithmeticError,
            json.JSONDecodeError,
        ) as error:
            error_text = f"{type(error).__name__}: {error}"
            warnings = snapshot.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append(f"forward A/B unavailable: {error_text}")
            snapshot["comparison"] = {
                "schema_version": AB_SCHEMA_VERSION,
                "studyId": AB_STUDY_ID,
                "mode": "paper_observability",
                "status": "unavailable",
                "generatedAt": _utc_now(),
                "forwardObservationDays": 0,
                "quality": {"matched": False},
                "error": error_text,
                "persistence": {
                    "snapshotPath": str(ab_snapshot_path),
                    "journalPath": str(ab_journal_path),
                },
                "exchange_submission_available": False,
                "live_ready": False,
                "real_leverage_authorized": False,
            }
    _write_atomic(path, snapshot)
    return snapshot
'''
        text = text[:start] + run_once + text[end:]

    if 'parser.add_argument("--ab-snapshot"' not in text:
        marker = (
            '    parser.add_argument("--starting-cash", type=float, default=10_000.0)\n'
            '    parser.add_argument("--verify-journal", type=Path)\n'
        )
        replacement = (
            '    parser.add_argument("--starting-cash", type=float, default=10_000.0)\n'
            '    parser.add_argument("--ab-snapshot", type=Path)\n'
            '    parser.add_argument("--ab-journal", type=Path)\n'
            '    parser.add_argument("--no-ab", action="store_true")\n'
            '    parser.add_argument("--verify-journal", type=Path)\n'
        )
        if marker not in text:
            raise RuntimeError("worker CLI marker not found")
        text = text.replace(marker, replacement, 1)

    if "ab_snapshot_path=args.ab_snapshot" not in text:
        marker = '''            snapshot = run_once(
                args.snapshot,
                reset_date=args.reset_date,
                initial_nav_usd=args.starting_cash,
            )
'''
        replacement = '''            snapshot = run_once(
                args.snapshot,
                reset_date=args.reset_date,
                initial_nav_usd=args.starting_cash,
                ab_snapshot_path=args.ab_snapshot,
                ab_journal_path=args.ab_journal,
                enable_ab=not args.no_ab,
            )
'''
        if marker not in text:
            raise RuntimeError("worker run_once call marker not found")
        text = text.replace(marker, replacement, 1)

    if '"ab_status"' not in text:
        marker = (
            '                        "crisis": bool('
            'snapshot["overlays"]["crisis4h"].get("active")),\n'
        )
        replacement = marker + (
            '                        "ab_status": '
            '(snapshot.get("comparison") or {}).get("status"),\n'
            '                        "ab_observations": int(\n'
            '                            (snapshot.get("comparison") or {})'
            '.get("forwardObservationDays") or 0\n'
            '                        ),\n'
        )
        if marker not in text:
            raise RuntimeError("worker log marker not found")
        text = text.replace(marker, replacement, 1)

    path.write_text(text, encoding="utf-8")


def patch_strategy_hub() -> None:
    path = ROOT / "src/finruntime/observability/strategy_hub.py"
    text = path.read_text(encoding="utf-8")
    if "ab_observations = int(" not in text:
        marker = '''    journal = persistence.get("journal")
    journal = journal if isinstance(journal, dict) else {}
    unavailable = snapshot is None or bool(error) or stale
'''
        replacement = '''    journal = persistence.get("journal")
    journal = journal if isinstance(journal, dict) else {}
    comparison = source.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    ab_observations = int(_number(comparison.get("forwardObservationDays")))
    ab_deltas = comparison.get("deltasV2MinusV1")
    ab_deltas = ab_deltas if isinstance(ab_deltas, dict) else {}
    unavailable = snapshot is None or bool(error) or stale
'''
        if marker not in text:
            raise RuntimeError("Strategy Hub A/B parse marker not found")
        text = text.replace(marker, replacement, 1)

    if '"forward_ab": comparison' not in text:
        marker = '''            "journal_valid": journal.get("valid"),
            "journal_events": journal.get("events"),
            "upstream_error": error,
'''
        replacement = '''            "journal_valid": journal.get("valid"),
            "journal_events": journal.get("events"),
            "forward_ab": comparison,
            "forward_ab_status": comparison.get("status"),
            "forward_ab_observations": ab_observations,
            "forward_ab_return_delta": ab_deltas.get("returnSinceReset"),
            "upstream_error": error,
'''
        if marker not in text:
            raise RuntimeError("Strategy Hub detail marker not found")
        text = text.replace(marker, replacement, 1)

    if '_metric("A/B forward"' not in text:
        marker = '''                _metric("Risk scale", f"{risk_scale:.2f}×"),
                _metric("Journal", str(journal.get("events") or 0)),
'''
        replacement = '''                _metric("Risk scale", f"{risk_scale:.2f}×"),
                _metric("A/B forward", f"{ab_observations}/90"),
                _metric("Journal", str(journal.get("events") or 0)),
'''
        if marker not in text:
            raise RuntimeError("Strategy Hub metrics marker not found")
        text = text.replace(marker, replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_healthcheck() -> None:
    path = ROOT / "scripts/check_paper_stack.py"
    text = path.read_text(encoding="utf-8")
    if "DS-40/180 A/B comparison is not an object" in text:
        return
    marker = '''    for key in ("statePath", "journalPath"):
        value = persistence.get(key)
        if not isinstance(value, str) or not Path(value).is_file():
            raise RuntimeError(f"DS-40/180 persistence file is unavailable: {key}")
'''
    addition = marker + '''    comparison = snapshot.get("comparison")
    if comparison is not None:
        if not isinstance(comparison, dict):
            raise TypeError("DS-40/180 A/B comparison is not an object")
        for key in (
            "exchange_submission_available",
            "live_ready",
            "real_leverage_authorized",
        ):
            if comparison.get(key) is not False:
                raise RuntimeError(f"DS-40/180 A/B safety flag is invalid: {key}")
        if comparison.get("status") != "unavailable":
            quality = comparison.get("quality")
            if not isinstance(quality, dict) or quality.get("matched") is not True:
                raise RuntimeError("DS-40/180 A/B arms are not matched")
            ab_persistence = comparison.get("persistence")
            ab_persistence = (
                ab_persistence if isinstance(ab_persistence, dict) else {}
            )
            ab_journal = ab_persistence.get("journal")
            if not isinstance(ab_journal, dict) or ab_journal.get("valid") is not True:
                raise RuntimeError("DS-40/180 A/B journal is invalid")
            if int(ab_journal.get("events") or 0) < 1:
                raise RuntimeError("DS-40/180 A/B journal is empty")
            for key in ("snapshotPath", "journalPath"):
                value = ab_persistence.get(key)
                if not isinstance(value, str) or not Path(value).is_file():
                    raise RuntimeError(
                        f"DS-40/180 A/B persistence file is unavailable: {key}"
                    )
'''
    if marker not in text:
        raise RuntimeError("healthcheck persistence marker not found")
    path.write_text(text.replace(marker, addition, 1), encoding="utf-8")


def patch_config() -> None:
    path = ROOT / "config/strategies/ds40180_t50c3_okx_paper.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["observability"] = {
        "forward_ab": {
            "enabled": True,
            "study_id": "ds40180_v1_v2_forward_ab",
            "legacy_reference_version": "okx-paper-v1",
            "legacy_source_commit": "cb942798acdd0f27867b923476dc9b50eb67984f",
            "legacy_source_blob": "dd573280ddec0e2ae50e33941d4f0154525d4809",
            "minimum_review_days": 30,
            "intermediate_review_days": 60,
            "preferred_review_days": 90,
            "automatic_winner_promotion": False,
        }
    }
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def patch_docs() -> None:
    path = ROOT / "docs/DS40180_T50C3_PAPER_RU.md"
    text = path.read_text(encoding="utf-8")
    if "## Forward A/B: v1 reference против v2" in text:
        return
    text += '''

## Forward A/B: v1 reference против v2

Каждый цикл v2 теперь параллельно пересчитывает **read-only** эталон старой
`okx-paper-v1` логики из закреплённого commit
`cb942798acdd0f27867b923476dc9b50eb67984f`. Исходный engine сохранён
байт-в-байт с blob `dd573280ddec0e2ae50e33941d4f0154525d4809`.
Эталон не регистрируется как активная стратегия и не влияет на позиции v2.

Файлы наблюдения:

```text
/data/runtime/ds40180_t50c3_ab_snapshot.json
/data/runtime/ds40180_t50c3_ab_events.jsonl
```

A/B journal добавляет не более одной пары на закрытый рыночный день. Повторные
внутридневные циклы обновляют snapshot, но не увеличивают число forward-дней.
Сравнение фиксирует NAV, return, maximum drawdown, realized volatility,
downside volatility, turnover, trading costs, funding и число исполнений.

Окна проверки:

- 30 forward-дней — первичный review;
- 60 дней — промежуточный review;
- 90 дней — предпочтительное окно решения.

Даже после 90 дней победитель автоматически не назначается: snapshot только
помечает исследование как `eligible_for_decision`, после чего требуется ручной
разбор качества данных, риска и исполнения.
'''
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_worker()
    patch_strategy_hub()
    patch_healthcheck()
    patch_config()
    patch_docs()
    print("DS-40/180 forward A/B integration applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
