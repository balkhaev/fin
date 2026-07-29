from __future__ import annotations

import unittest

from finruntime.canonical import ContractError, sha256_id
from finruntime.provenance import StrategyMigrationRecord, parse_strategy_migration

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


class StrategyMigrationStrictTypeTests(unittest.TestCase):
    def record(self) -> StrategyMigrationRecord:
        return StrategyMigrationRecord.create(
            migration_kind="reconstruction",
            status="planned",
            predecessor_strategy_id="v75_atlas_nx",
            successor_strategy_id="v75_reconstructed_v1",
            created_at_utc="2026-07-30T00:00:00Z",
            reason="Exact predecessor artifacts are unavailable; use a new identity.",
            source_audits={"docs/audit.json": HASH_A},
            predecessor_artifact_hashes={"research/missing/engine.py": HASH_B},
            inherited_parameters={"gross_cap": "1.10"},
            changed_components=("source_reconstruction",),
            allowed_modes=("paper", "shadow"),
            forward_clock_reset=True,
            successor_provenance_complete=False,
        )

    @staticmethod
    def rehash(raw: dict[str, object]) -> dict[str, object]:
        raw["migration_id"] = sha256_id(
            {
                key: value
                for key, value in raw.items()
                if key != "migration_id"
            }
        )
        return raw

    def test_json_numbers_cannot_substitute_for_booleans(self) -> None:
        record = self.record()
        for field in (
            "forward_clock_reset",
            "successor_provenance_complete",
            "capital_authorization_carried_forward",
            "live_ready",
            "real_leverage_authorized",
            "exchange_submission_available",
        ):
            with self.subTest(field=field):
                raw = record.to_dict()
                raw[field] = 1 if raw[field] is True else 0
                with self.assertRaises(ContractError):
                    parse_strategy_migration(self.rehash(raw))

    def test_parameter_sections_must_be_json_objects(self) -> None:
        record = self.record()
        for field in ("inherited_parameters", "changed_parameters"):
            with self.subTest(field=field):
                raw = record.to_dict()
                raw[field] = []
                with self.assertRaises(ContractError):
                    parse_strategy_migration(self.rehash(raw))

    def test_hash_manifests_require_string_sha256_values(self) -> None:
        raw = self.record().to_dict()
        raw["source_audits"] = {"docs/audit.json": 7}
        with self.assertRaises(ContractError):
            parse_strategy_migration(self.rehash(raw))

    def test_identity_status_and_timestamp_require_strings(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("migration_id", 7),
            ("migration_kind", []),
            ("status", {}),
            ("created_at_utc", 0),
            ("predecessor_strategy_id", 1),
            ("successor_strategy_id", False),
        )
        record = self.record()
        for field, value in cases:
            with self.subTest(field=field):
                raw = record.to_dict()
                raw[field] = value
                if field != "migration_id":
                    raw = self.rehash(raw)
                with self.assertRaises(ContractError):
                    parse_strategy_migration(raw)


if __name__ == "__main__":
    unittest.main()
