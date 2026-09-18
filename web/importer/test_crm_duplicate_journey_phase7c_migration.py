"""Phase 7C: upgrade path for claim auto_merge backfill (0010 → 0011)."""

from __future__ import annotations

from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CrmDuplicateJourneyPhase7cMigrationTests(TransactionTestCase):
    """Apply original 0010, seed claim/session, then 0011 backfill."""

    available_apps = ["importer"]

    def test_0011_backfills_threshold_from_claimed_session_options(self):
        executor = MigrationExecutor(connection)
        # Roll to pre-0010, then apply original 0010 only (field default none).
        executor.migrate([("importer", "0009_importsession_setup_vocabulary")])
        executor.loader.build_graph()
        executor.migrate([("importer", "0010_crm_dupe_7c_attempt_auto_merge")])
        executor.loader.build_graph()

        state = executor.loader.project_state(
            [("importer", "0010_crm_dupe_7c_attempt_auto_merge")]
        )
        apps = state.apps
        ImportSession = apps.get_model("importer", "ImportSession")
        Claim = apps.get_model("importer", "CrmDuplicateJourneyAttemptClaim")

        journal = ImportSession.objects.create(
            id=uuid4(),
            owner_id=uuid4(),
            product_key="crm.journey",
            options={},
        )
        claimed = ImportSession.objects.create(
            id=uuid4(),
            owner_id=journal.owner_id,
            product_key="easyimports.duplicate_resolution",
            options={
                "source_mode": "acquire_all",
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "duplicate_execution_maximum": "dry_run",
                "auto_merge_min_confidence": 95,
            },
        )
        claim = Claim.objects.create(
            id=uuid4(),
            journal=journal,
            root_form_instance=uuid4(),
            claimed_session=claimed,
            source_mode="acquire_all",
            connection_id="crm_conn_test",
            entity_family="company",
            duplicate_execution_maximum="dry_run",
            auto_merge_min_confidence="none",
        )
        self.assertEqual(claim.auto_merge_min_confidence, "none")

        executor.loader.build_graph()
        executor.migrate(
            [("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill")]
        )
        executor.loader.build_graph()

        # Reload with post-0011 historical models.
        state_after = executor.loader.project_state(
            [("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill")]
        )
        ClaimAfter = state_after.apps.get_model(
            "importer", "CrmDuplicateJourneyAttemptClaim"
        )
        refreshed = ClaimAfter.objects.get(pk=claim.pk)
        self.assertEqual(refreshed.auto_merge_min_confidence, "95")

        # Return the test DB to HEAD so other suites see current models.
        executor.migrate([("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill")])

    def test_0011_fails_closed_on_corrupt_session_threshold(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("importer", "0009_importsession_setup_vocabulary")])
        executor.loader.build_graph()
        executor.migrate([("importer", "0010_crm_dupe_7c_attempt_auto_merge")])
        executor.loader.build_graph()

        state = executor.loader.project_state(
            [("importer", "0010_crm_dupe_7c_attempt_auto_merge")]
        )
        apps = state.apps
        ImportSession = apps.get_model("importer", "ImportSession")
        Claim = apps.get_model("importer", "CrmDuplicateJourneyAttemptClaim")

        journal = ImportSession.objects.create(
            id=uuid4(),
            owner_id=uuid4(),
            product_key="crm.journey",
            options={},
        )
        claimed = ImportSession.objects.create(
            id=uuid4(),
            owner_id=journal.owner_id,
            product_key="easyimports.duplicate_resolution",
            options={"auto_merge_min_confidence": 89},
        )
        Claim.objects.create(
            id=uuid4(),
            journal=journal,
            root_form_instance=uuid4(),
            claimed_session=claimed,
            source_mode="acquire_all",
            connection_id="c",
            entity_family="company",
            duplicate_execution_maximum="dry_run",
            auto_merge_min_confidence="none",
        )

        with self.assertRaises(ValueError):
            executor.loader.build_graph()
            executor.migrate(
                [("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill")]
            )

        # Recover DB to HEAD for other tests in the process.
        # Fake apply of 0011 by marking it applied after fixing data, or migrate full.
        claimed.options = {"auto_merge_min_confidence": 90}
        claimed.save(update_fields=["options"])
        executor.loader.build_graph()
        try:
            executor.migrate(
                [("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill")]
            )
        except Exception:
            # If partial migration left dirty state, force full app migrate.
            executor.migrate([("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill")])
