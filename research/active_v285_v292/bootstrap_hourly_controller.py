#!/usr/bin/env python3
from pathlib import Path
import base64
import zlib

root = Path(__file__).resolve().parent
parts = sorted((root / "source_parts").glob("part_*.txt"))
if not parts:
    raise SystemExit("missing V285 source parts")
payload = "".join(path.read_text().strip() for path in parts)
source = zlib.decompress(base64.b64decode(payload))
path = root / "run_research.py"
path.write_bytes(source)
print(f"materialized {path} from {len(parts)} parts ({len(source)} bytes)")
