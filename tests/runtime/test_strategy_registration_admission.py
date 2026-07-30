from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from finruntime.canonical import ContractError
from finruntime.portfolio import PaperAccountState
from finruntime.provenance import (
    ForwardClockResetRecord,
    StrategyMigrationRecord,
    validate_identity_policy,
    validate_strategy_registration,
)
from scripts.verify_runtime import (
    EXPECTED_LEGACY_STRATEGY_IDS,
    _verify_initial_account_state,
    load_identity_policy,
    load_source_registry,
    verify_configs_and_schemas,
)

ROOT = Path(__file__).resolve().parents[2]
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64
SUCCESSOR = "v75_reconstructed_v1"
PREDECESSOR = "v75_atlas_nx"


class StrategyRegistrationAdmissionTests(unittest.TestCase):
    def policy(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "allowed_identity_origins": ["legacy_frozen", "migration"],
            "grandfathered_legacy_strategy_ids": sorted(
                EXPECTED_LEGACY_STRATEGY_IDS
            ),
            "requirements": {
                "validated_migration_record": True,
                "complete_successor_provenance": True,
                "separate_forward_clock_reset_record": True,
                "new_strategy_id_for_reconstruction": True,
                "native_registration_available": False,
                "forward_state_reuse_permitted": False,
                "historical_evidence_carried_forward": False,
                "capital_authorization_carried_forward": False,
                "live_ready": False,
                "real_leverage_authorized": False,
                "exchange_submission_available": False,
            },
        }

    def migration(
        self,
        **overrides: object,
    ) -> StrategyMigrationRecord:
        values: dict[str, object] = {
            "migration_kind": "reconstruction",
            "status": "validated",
            "predecessor_strategy_id": PREDECESSOR,
            "successor_strategy_id": SUCCESSOR,
            "created_at_utc": "2026-07-30T00:00:00Z",
            "reason": "Build a new identity because exact V75 bytes are unavailable.",
            "source_audits": {"docs/audits/v75.json": HASH_A},
            "predecessor_artifact_hashes": {
                "research/missing/v75.py": HASH_B
            },
            "successor_source_hashes": {"src/strategies/v75_v1.py": HASH_C},
            "regression_fixture_hashes": {
                "tests/fixtures/v75_v1_targets.csv": HASH_D
            },
            "inherited_parameters": {"gross_cap": "1.10"},
            "changed_parameters": {},
            "changed_components": ("dependency_closure", "signal_engine"),
            "allowed_modes": ("paper", "shadow"),
            "forward_clock_reset": True,
            "successor_provenance_complete": True,
        }
        values.update(overrides)
        return StrategyMigrationRecord.create(**values)  # type: ignore[arg-type]

    def reset(
        self,
        migration: StrategyMigrationRecord,
        **overrides: object,
    ) -> ForwardClockResetRecord:
        values: dict[str, object] = {
            "strategy_id": SUCCESSOR,
            "migration_id": migration.migration_id,
            "created_at_utc": "2026-07-30T00:01:00Z",
            "reason": "Start successor paper evidence from a pristine account.",
            "initial_account_state_path": "docs/migrations/initial_account.json",
            "initial_account_state_sha256": HASH_A,
            "initial_account_hash": HASH_B,
        }
        values.update(overrides)
        return ForwardClockResetRecord.create(**values)  # type: ignore[arg-type]

    def config(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "strategy_id": SUCCESSOR,
            "strategy_version": "runtime-v1",
            "role": "primary",
            "allowed_modes": ["paper", "shadow"],
            "identity_origin": "migration",
            "migration_record_path": "docs/migrations/migration.json",
            "forward_clock_reset_record_path": "docs/migrations/reset.json",
            "predecessor_strategy_id": PREDECESSOR,
            "provenance_profile": SUCCESSOR,
            "forward_clock_reset": True,
            "forward_state_reuse_permitted": False,
            "historical_evidence_carried_forward": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "exchange_submission_available": False,
        }
        values.update(overrides)
        return values

    def source_registry(self, **profile_overrides: object) -> dict[str, object]:
        profile: dict[str, object] = {
            "provenance_complete": True,
            "source_paths": {"src/strategies/v75_v1.py": "c" * 64},
            "regression_fixture_paths": {
                "tests/fixtures/v75_v1_targets.csv": "d" * 64
            },
        }
        profile.update(profile_overrides)
        return {"profiles": {SUCCESSOR: profile}}

    def admit(
        self,
        *,
        config: dict[str, object] | None = None,
        migration: StrategyMigrationRecord | None = None,
        reset: ForwardClockResetRecord | None = None,
        source_registry: dict[str, object] | None = None,
        policy: dict[str, object] | None = None,
    ):
        migration = migration or self.migration()
        reset = reset or self.reset(migration)
        return validate_strategy_registration(
            config or self.config(),
            policy=policy or self.policy(),
            source_registry=source_registry or self.source_registry(),
            migration_record=migration,
            reset_record=reset,
        )

    def test_validated_reconstruction_is_admitted(self) -> None:
        result = self.admit()

        self.assertTrue(result.admitted)
        self.assertEqual(result.strategy_id, SUCCESSOR)
        self.assertEqual(result.identity_origin, "migration")
        self.assertEqual(result.predecessor_strategy_id, PREDECESSOR)
        self.assertEqual(result.allowed_modes, ("paper", "shadow"))
        self.assertEqual(result.provenance_profile, SUCCESSOR)
        self.assertIsNotNone(result.migration_id)
        self.assertIsNotNone(result.reset_id)

    def test_unknown_legacy_id_and_reclassified_grandfathered_id_are_rejected(self) -> None:
        legacy = self.config(
            strategy_id="new_legacy_strategy",
            identity_origin="legacy_frozen",
            migration_record_path=None,
            forward_clock_reset_record_path=None,
            predecessor_strategy_id=None,
            provenance_profile=None,
            forward_clock_reset=False,
        )
        with self.assertRaises(ContractError):
            validate_strategy_registration(
                legacy,
                policy=self.policy(),
                source_registry={"profiles": {}},
            )

        with self.assertRaises(ContractError):
            self.admit(config=self.config(strategy_id=PREDECESSOR))

    def test_current_repository_legacy_set_is_exact_and_admitted(self) -> None:
        policy = load_identity_policy()
        source_registry = load_source_registry()
        self.assertEqual(
            set(validate_identity_policy(policy)),
            EXPECTED_LEGACY_STRATEGY_IDS,
        )

        config_paths = sorted((ROOT / "config/strategies").glob("*.json"))
        observed: set[str] = set()
        for path in config_paths:
            config = json.loads(path.read_text(encoding="utf-8"))
            observed.add(config["strategy_id"])
            result = validate_strategy_registration(
                config,
                policy=policy,
                source_registry=source_registry,
            )
            self.assertTrue(result.admitted, path.name)
            self.assertEqual(result.identity_origin, "legacy_frozen")
        self.assertEqual(observed, EXPECTED_LEGACY_STRATEGY_IDS)

    def test_planned_implemented_or_incomplete_migration_is_rejected(self) -> None:
        planned = self.migration(
            status="planned",
            successor_source_hashes={},
            regression_fixture_hashes={},
            successor_provenance_complete=False,
        )
        with self.assertRaises(ContractError):
            self.admit(migration=planned, reset=self.reset(planned))

        implemented = self.migration(
            status="implemented",
            regression_fixture_hashes={},
            successor_provenance_complete=False,
        )
        with self.assertRaises(ContractError):
            self.admit(migration=implemented, reset=self.reset(implemented))

        with self.assertRaises(ContractError):
            self.admit(
                source_registry=self.source_registry(provenance_complete=False)
            )
        with self.assertRaises(ContractError):
            self.admit(
                source_registry=self.source_registry(
                    unmaterialized_requirements={"dependency": {"status": "missing"}}
                )
            )

    def test_identity_modes_and_manifests_must_match_all_evidence_layers(self) -> None:
        with self.assertRaises(ContractError):
            self.admit(config=self.config(predecessor_strategy_id="v28_growth_control"))
        with self.assertRaises(ContractError):
            self.admit(config=self.config(provenance_profile="another_profile"))
        with self.assertRaises(ContractError):
            self.admit(config=self.config(allowed_modes=["shadow"]))
        with self.assertRaises(ContractError):
            self.admit(
                source_registry=self.source_registry(
                    source_paths={"src/strategies/v75_v1.py": "e" * 64}
                )
            )
        with self.assertRaises(ContractError):
            self.admit(
                source_registry=self.source_registry(
                    regression_fixture_paths={
                        "tests/fixtures/v75_v1_targets.csv": "e" * 64
                    }
                )
            )

    def test_reset_record_must_reference_same_successor_and_migration(self) -> None:
        migration = self.migration()
        with self.assertRaises(ContractError):
            self.admit(
                migration=migration,
                reset=self.reset(migration, strategy_id="another_successor"),
            )
        with self.assertRaises(ContractError):
            self.admit(
                migration=migration,
                reset=self.reset(migration, migration_id=HASH_A),
            )

    def test_state_history_mode_and_live_escalation_are_rejected(self) -> None:
        unsafe_configs = (
            {"forward_clock_reset": False},
            {"forward_state_reuse_permitted": True},
            {"historical_evidence_carried_forward": True},
            {"live_ready": True},
            {"real_leverage_authorized": True},
            {"exchange_submission_available": True},
            {"allowed_modes": ["live"]},
        )
        for override in unsafe_configs:
            with self.subTest(override=override):
                with self.assertRaises(ContractError):
                    self.admit(config=self.config(**override))

    def test_forward_reset_requires_zero_counters_and_false_reuse_flags(self) -> None:
        migration = self.migration()
        invalid_reset_values = (
            {"initial_account_sequence": 1},
            {"calendar_days_observed": 1},
            {"target_changes_observed": 1},
            {"closed_paper_trades": 1},
            {"nonzero_accelerator_regimes": 1},
            {"forward_observations": 1},
            {"unexplained_delta_mismatches": 1},
            {"state_recovery_failures": 1},
            {"account_state_reused": True},
            {"historical_evidence_reused": True},
            {"capital_authorization_carried_forward": True},
            {"live_ready": True},
        )
        for override in invalid_reset_values:
            with self.subTest(override=override):
                with self.assertRaises(ContractError):
                    self.reset(migration, **override)

    def test_initial_account_evidence_is_pristine_and_hash_bound(self) -> None:
        migration = self.migration()
        state = PaperAccountState.empty(
            strategy_id=SUCCESSOR,
            as_of_utc="2026-07-30T00:01:00Z",
            starting_cash="10000",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("docs/migrations/initial_account.json")
            path = root / relative
            path.parent.mkdir(parents=True)
            payload = json.dumps(
                state.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            path.write_bytes(payload)
            reset = self.reset(
                migration,
                initial_account_state_path=relative.as_posix(),
                initial_account_state_sha256=(
                    "sha256:" + hashlib.sha256(payload).hexdigest()
                ),
                initial_account_hash=state.account_hash,
            )
            with patch("scripts.verify_runtime.ROOT", root):
                _verify_initial_account_state(reset)

            tampered = replace(
                reset,
                initial_account_state_sha256=HASH_A,
            )
            with patch("scripts.verify_runtime.ROOT", root):
                with self.assertRaises(SystemExit):
                    _verify_initial_account_state(tampered)

    def test_repository_contract_verifier_accepts_current_registry(self) -> None:
        verify_configs_and_schemas()


if __name__ == "__main__":
    unittest.main()
