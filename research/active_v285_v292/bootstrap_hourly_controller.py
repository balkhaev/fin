#!/usr/bin/env python3
from pathlib import Path
import base64
import subprocess
import sys
import zlib

root = Path(__file__).resolve().parent
parts = sorted((root / "source_parts").glob("part_*.txt"))
if not parts:
    raise SystemExit("missing V285 source parts")
payload = "".join(path.read_text().strip() for path in parts)
source = zlib.decompress(base64.b64decode(payload))
path = root / "run_research.py"
path.write_bytes(source)
patch = root / "apply_v286_column_alignment_fix.py"
subprocess.run([sys.executable, str(patch)], check=True)
patch.unlink()
for part in parts:
    part.unlink()
(root / "source_parts").rmdir()
print(f"materialized and corrected {path} ({path.stat().st_size} bytes)")
