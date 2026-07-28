from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ControlRoomContractTests(unittest.TestCase):
    def test_server_is_read_only(self) -> None:
        source = (ROOT / "src/finruntime/observability/server.py").read_text(encoding="utf-8")
        self.assertIn("read-only", source.lower())
        self.assertIn("do_POST", source)
        self.assertIn("METHOD_NOT_ALLOWED", source)
        self.assertIn('"exchange_submission_available": False', source)
        self.assertNotIn("submit_order", source)

    def test_runtime_parser_uses_journal_verification(self) -> None:
        source = (ROOT / "src/finruntime/observability/control_room.py").read_text(encoding="utf-8")
        self.assertIn("AppendOnlyJournal", source)
        self.assertIn("duplicate telemetry primary key", source)
        self.assertIn("source_hash_mismatch", source)
        self.assertIn("stale_data", source)

    def test_remote_binding_requires_explicit_opt_in(self) -> None:
        source = (ROOT / "src/finruntime/observability/server.py").read_text(encoding="utf-8")
        self.assertIn("--allow-remote", source)
        self.assertIn("non-loopback binding requires --allow-remote", source)


if __name__ == "__main__":
    unittest.main()
