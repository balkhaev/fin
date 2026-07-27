#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_materializer() -> None:
    path = ROOT / "materialize_v365_account.py"
    text = path.read_text(encoding="utf-8")
    if "from dataclasses import asdict" not in text:
        text = replace_once(
            text,
            "import sys\nfrom pathlib import Path\n",
            "import sys\nfrom dataclasses import asdict\nfrom pathlib import Path\n",
            "materializer dataclasses import",
        )
    text = text.replace(
        '"audit": clean(v365.AUDITS[0].__dict__),',
        '"audit": clean(asdict(v365.AUDITS[0])),',
    )
    if '"audit": clean(asdict(v365.AUDITS[0])),' not in text:
        raise SystemExit("materializer audit correction missing")
    path.write_text(text, encoding="utf-8")


def patch_analyzer() -> None:
    path = ROOT / "run_anatomy.py"
    text = path.read_text(encoding="utf-8")
    old_state_check = '''    if state[list(required - {"state_label", "novelty_flag"})].isna().all(axis=None):
        raise ValueError("market-state numeric fields are empty")
'''
    new_state_check = '''    numeric_required = sorted(required - {"state_label", "novelty_flag"})
    if state[numeric_required].isna().to_numpy().all():
        raise ValueError("market-state numeric fields are empty")
'''
    if old_state_check in text:
        text = replace_once(text, old_state_check, new_state_check, "state numeric check")
    elif new_state_check not in text:
        raise SystemExit("state numeric correction missing")

    old_signature = "def finalize_account(account: pd.DataFrame) -> pd.DataFrame:\n    if len(account) != EXPECTED_ROWS:\n"
    new_signature = "def finalize_account(\n    account: pd.DataFrame, expected_rows: int = EXPECTED_ROWS\n) -> pd.DataFrame:\n    if len(account) != expected_rows:\n"
    if old_signature in text:
        text = replace_once(text, old_signature, new_signature, "finalize signature")
    elif new_signature not in text:
        raise SystemExit("finalize signature correction missing")

    old_self_test = "    account = finalize_account(account)\n    joined = join_state(account, state)\n"
    new_self_test = "    account = finalize_account(account, expected_rows=len(account))\n    joined = join_state(account, state)\n"
    if old_self_test in text:
        text = replace_once(text, old_self_test, new_self_test, "self-test row count")
    elif new_self_test not in text:
        raise SystemExit("self-test correction missing")

    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_materializer()
    patch_analyzer()
    print("V437-V444 deterministic source corrections applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
