#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parent / "data.py"
source = path.read_text()
old = """from config import (\n    END,\n    MIN_FULL_MONTHS,"""
new = """from config import (\n    DEVELOPMENT_END,\n    END,\n    HOLDOUT_END,\n    HOLDOUT_START,\n    MIN_FULL_MONTHS,"""
if old not in source:
    raise SystemExit("expected config import block not found")
source = source.replace(old, new, 1)
old = """    START,\n    SYMBOLS,\n)"""
new = """    START,\n    SYMBOLS,\n    VALIDATION_END,\n    VALIDATION_START,\n)"""
if old not in source:
    raise SystemExit("expected trailing config import block not found")
path.write_text(source.replace(old, new, 1))
print("V198 data imports fixed")
