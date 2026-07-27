#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parent / "run_research.py"
source = path.read_text()

old = "    executed_daily = daily_weights.shift(1 + audit.alpha_delay_days)\n"
new = (
    "    executed_daily = daily_weights.reindex(columns=SYMBOLS).shift(\n"
    "        1 + audit.alpha_delay_days\n"
    "    )\n"
)
if old not in source:
    raise SystemExit("V286 execution alignment insertion point not found")
source = source.replace(old, new, 1)

old = '''    daily_weights.loc[:, columns[:3]] = TARGET_GROSS / 6
    daily_weights.loc[:, columns[3:]] = -TARGET_GROSS / 6
    account, diagnostics = simulate_hourly(
'''
new = '''    daily_weights.loc[:, columns[:3]] = TARGET_GROSS / 6
    daily_weights.loc[:, columns[3:]] = -TARGET_GROSS / 6
    # Adversarially scramble source columns. The simulator must explicitly
    # realign them to the frozen SYMBOLS order before NumPy conversion.
    daily_weights = daily_weights.loc[:, list(reversed(daily_weights.columns))]
    account, diagnostics = simulate_hourly(
'''
if old not in source:
    raise SystemExit("V286 self-test alignment insertion point not found")
source = source.replace(old, new, 1)

path.write_text(source)
print("V286 explicit daily/hourly column alignment applied")
