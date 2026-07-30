from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from finruntime.canonical import ContractError
from finruntime.cli import main
from finruntime.provenance import StrategyMigrationRecord, parse_strategy_migration

ROOT = Path(__file__).resolve().parents[2]
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64


class StrategyMigrationRecordTests(unittest.TestCase):
    def reconstruction(self, **overrides: object) -> StrategyMigrationRecord:
        values: dict[str, object] = {
            "migration_kind": "reconstruction",
            "status": "planned",
            "predecessor_strategy_id": "v75_atlas_nx",
            "successor_strategy_id": "v75_reconstructed_v1",
            "created_at_utc": "2026-07-30T00:00:00Z",
            "reason": "Exact predecessor artifacts are unavailable; use a new identity.",
            "source_audits": {
                "docs/checkpoints/runtime-v1/V75_MATERIALIZATION_AUDIT.json": HASH_A
            },
            "predecessor_artifact_hashes": {
                "research/missing/v75_engine.py": HASH_B
            },
            "successor_source_hashes": {},
            "regression_fixture_hashes": {},
            "inherited_parameters": {"gross_cap": "1.10"},
            "changed_parameters": {},
            "changed_components": ("dependency_closure", "source_reconstruction"),
            "allowed_modes": ("paper", "shadow"),
            "forward_clock_reset": True,
            "successor_provenance_complete": False,
        }
        values.update(overrides)
        return StrategyMigrationRecord.create(**values)  # type: ignore[arg-type]

    def test_planned_reconstruction_is_deterministic_and_canonical(self) -> None:
        first = self.reconstruction(
            source_audits={
                "docs/z-audit.json": HASH_B,
                "docs/a-audit.json": HASH_A,
            },
            changed_components=("source_reconstruction", "dependency_closure"),
            allowed_modes=("shadow", "paper"),
        )
        second = self.reconstruction(
            source_audits={
                "docs/a-audit.json": HASH_A,
                "docs/z-audit.json": HASH_B,
            },
            changed_components=("dependency_closure", "source_reconstruction"),
            allowed_modes=("paper", "shadow"),
        )

        self.assertEqual(first.migration_id, second.migration_id)
        self.assertEqual(first.source_audits, second.source_audits)
        self.assertEqual(
            first.changed_components,
            ("dependency_closure", "source_reconstruction"),
        )
        self.assertEqual(first.allowed_modes, ("paper", "shadow"))
        first.validate()

    def test_json_object_member_order_does_not_change_identity(self) -> None:
        record = self.reconstruction(
            source_audits={
                "docs/a-audit.json": HASH_A,
                "docs/z-audit.json": HASH_B,
            }
        )
        raw = record.to_dict()
        raw["source_audits"] = {
            "docs/z-audit.json": HASH_B,
            "docs/a-audit.json": HASH_A,
        }

        parsed = parse_strategy_migration(raw)
        self.assertEqual(parsed.migration_id, record.migration_id)

    def test_reconstruction_requires_new_identity_and_forward_reset(self) -> None:
        with self.assertRaises(ContractError):
            self.reconstruction(successor_strategy_id="v75_atlas_nx")
        with self.assertRaises(ContractError):
            self.reconstruction(forward_clock_reset=False)
        with self.assertRaises(ContractError):
            self.reconstruction(changed_components=())

    def test_migration_never_carries_live_or_capital_authorization(self) -> None:
        unsafe_fields = (
            "capital_authorization_carried_forward",
            "live_ready",
            "real_leverage_authorized",
            "exchange_submission_available",
        )
        for field in unsafe_fields:
            with self.subTest(field=field):
                with self.assertRaises(ContractError):
                    self.reconstruction(**{field: True})

        with self.assertRaises(ContractError):
            self.reconstruction(allowed_modes=("live",))

    def test_status_lifecycle_is_fail_closed(self) -> None:
        with self.assertRaises(ContractError):
            self.reconstruction(
                status="planned",
                successor_source_hashes={"src/new_engine.py": HASH_C},
            )
        with self.assertRaises(ContractError):
            self.reconstruction(status="implemented")
        with self.assertRaises(ContractError):
            self.reconstruction(
                status="implemented",
                successor_source_hashes={"src/new_engine.py": HASH_C},
                successor_provenance_complete=True,
            )
        with self.assertRaises(ContractError):
            self.reconstruction(
                status="validated",
                successor_source_hashes={"src/new_engine.py": HASH_C},
                successor_provenance_complete=True,
            )

        implemented = self.reconstruction(
            status="implemented",
            successor_source_hashes={"src/new_engine.py": HASH_C},
        )
        self.assertFalse(implemented.successor_provenance_complete)

        validated = self.reconstruction(
            status="validated",
            successor_source_hashes={"src/new_engine.py": HASH_C},
            regression_fixture_hashes={"tests/fixtures/targets.csv": HASH_D},
            successor_provenance_complete=True,
        )
        self.assertTrue(validated.successor_provenance_complete)

    def test_byte_identical_materialization_preserves_identity_and_manifest(self) -> None:
        manifest = {
            "research/exact/engine.py": HASH_B,
            "research/exact/targets.csv": HASH_C,
        }
        record = StrategyMigrationRecord.create(
            migration_kind="byte_identical_materialization",
            status="validated",
            predecessor_strategy_id="v75_atlas_nx",
            successor_strategy_id="v75_atlas_nx",
            created_at_utc="2026-07-30T00:00:00Z",
            reason="Externally retained exact bytes match every pinned artifact hash.",
            source_audits={
                "docs/checkpoints/runtime-v1/V75_MATERIALIZATION_AUDIT.json": HASH_A
            },
            predecessor_artifact_hashes=manifest,
            successor_source_hashes=manifest,
            regression_fixture_hashes={"tests/fixtures/exact_replay.csv": HASH_D},
            inherited_parameters={"gross_cap": "1.10"},
            changed_parameters={},
            changed_components=(),
            allowed_modes=("paper", "shadow"),
            forward_clock_reset=False,
            successor_provenance_complete=True,
        )

        self.assertEqual(record.predecessor_strategy_id, record.successor_strategy_id)
        self.assertFalse(record.forward_clock_reset)
        record.validate()

        with self.assertRaises(ContractError):
            StrategyMigrationRecord.create(
                migration_kind="byte_identical_materialization",
                status="validated",
                predecessor_strategy_id="v75_atlas_nx",
                successor_strategy_id="v75_atlas_nx_v2",
                created_at_utc="2026-07-30T00:00:00Z",
                reason="Invalid identity change for byte-identical evidence.",
                source_audits={"docs/audit.json": HASH_A},
                predecessor_artifact_hashes=manifest,
                successor_source_hashes=manifest,
                regression_fixture_hashes={"tests/fixtures/replay.csv": HASH_D},
                changed_components=(),
                forward_clock_reset=False,
                successor_provenance_complete=True,
            )

        with self.assertRaises(ContractError):
            StrategyMigrationRecord.create(
                migration_kind="byte_identical_materialization",
                status="validated",
                predecessor_strategy_id="v75_atlas_nx",
                successor_strategy_id="v75_atlas_nx",
                created_at_utc="2026-07-30T00:00:00Z",
                reason="Invalid changed source for byte-identical evidence.",
                source_audits={"docs/audit.json": HASH_A},
                predecessor_artifact_hashes=manifest,
                successor_source_hashes={"research/exact/engine.py": HASH_C},
                regression_fixture_hashes={"tests/fixtures/replay.csv": HASH_D},
                changed_components=(),
                forward_clock_reset=False,
                successor_provenance_complete=True,
            )

    def test_paths_parameters_and_hash_identity_are_strict(self) -> None:
        with self.assertRaises(ContractError):
            self.reconstruction(source_audits={"../audit.json": HASH_A})
        with self.assertRaises(ContractError):
            self.reconstruction(source_audits={"/tmp/audit.json": HASH_A})
        with self.assertRaises(ContractError):
            self.reconstruction(
                inherited_parameters={"gross_cap": "1.10"},
                changed_parameters={"gross_cap": "1.00"},
            )

        record = self.reconstruction()
        tampered = replace(record, reason="A different migration reason.")
        with self.assertRaises(ContractError):
            tampered.validate()

    def test_cli_validates_record_and_reports_safety_state(self) -> None:
        record = self.reconstruction()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.json"
            path.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["validate-migration", str(path)])

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["migration_id"], record.migration_id)
        self.assertEqual(payload["successor_strategy_id"], "v75_reconstructed_v1")
        self.assertTrue(payload["forward_clock_reset"])
        self.assertFalse(payload["capital_authorization_carried_forward"])
        self.assertFalse(payload["live_ready"])
        self.assertFalse(payload["real_leverage_authorized"])
        self.assertFalse(payload["exchange_submission_available"])

    def test_schema_declares_required_safety_and_lifecycle_fields(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "schemas/runtime/strategy_migration_record.schema.json"
            ).read_text(encoding="utf-8")
        )
        required = set(schema["required"])
        self.assertIn("forward_clock_reset", required)
        self.assertIn("successor_provenance_complete", required)
        self.assertIn("capital_authorization_carried_forward", required)
        self.assertIn("live_ready", required)
        self.assertIn("real_leverage_authorized", required)
        self.assertIn("exchange_submission_available", required)
        for field in (
            "capital_authorization_carried_forward",
            "live_ready",
            "real_leverage_authorized",
            "exchange_submission_available",
        ):
            self.assertFalse(schema["properties"][field]["const"])


if __name__ == "__main__":
    unittest.main()
