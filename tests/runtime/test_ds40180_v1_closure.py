from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from finruntime.strategies._ds40180_v1_reference import (
    V1_REFERENCE_SOURCE_BLOBS,
    V1_REFERENCE_SOURCE_COMMIT,
)


ROOT = Path(__file__).resolve().parents[2]
FROZEN_ROOT = ROOT / "src/finruntime/strategies/_ds40180_v1"


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


class Ds40180V1ClosureTests(unittest.TestCase):
    def test_complete_reference_closure_matches_pinned_git_blobs(self) -> None:
        paths = {
            "common": FROZEN_ROOT / "_ds40180_common.py",
            "signals": FROZEN_ROOT / "_ds40180_signals.py",
            "engine": FROZEN_ROOT / "_ds40180_engine.py",
            "account": FROZEN_ROOT / "_ds40180_account.py",
        }
        self.assertEqual(
            V1_REFERENCE_SOURCE_COMMIT,
            "cb942798acdd0f27867b923476dc9b50eb67984f",
        )
        self.assertEqual(set(paths), set(V1_REFERENCE_SOURCE_BLOBS))
        for name, path in paths.items():
            with self.subTest(name=name):
                self.assertTrue(path.is_file())
                self.assertEqual(
                    git_blob_sha(path), V1_REFERENCE_SOURCE_BLOBS[name]
                )

    def test_active_account_remains_identical_to_frozen_v1_account(self) -> None:
        active = ROOT / "src/finruntime/strategies/_ds40180_account.py"
        frozen = FROZEN_ROOT / "_ds40180_account.py"
        self.assertEqual(git_blob_sha(active), V1_REFERENCE_SOURCE_BLOBS["account"])
        self.assertEqual(active.read_bytes(), frozen.read_bytes())

    def test_frozen_reference_has_no_exchange_submission_surface(self) -> None:
        forbidden = (
            "submit_order",
            "create_order",
            "place_order",
            "apiKey",
            "secretKey",
        )
        for path in FROZEN_ROOT.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for value in forbidden:
                    self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
