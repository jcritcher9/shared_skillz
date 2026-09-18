"""MAP-3 Django unit tests: confirmed bind required at preclaim."""

from __future__ import annotations

from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings

from importer.models import ApiMutation, ImportSession, SourceFile
from importer.setup_service import (
    SetupValidationError,
    confirmed_column_mapping_bind,
)


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class ColumnMappingMap3SetupBindTests(SimpleTestCase):
    """confirmed_column_mapping_bind fail-closed rules (no network)."""

    def test_missing_draft_raises(self):
        session = ImportSession(owner_id=uuid4(), options={})
        with self.assertRaises(SetupValidationError) as ctx:
            confirmed_column_mapping_bind(session)
        self.assertIn("Confirm column mapping", str(ctx.exception))

    def test_draft_not_confirmed_raises(self):
        session = ImportSession(
            owner_id=uuid4(),
            options={
                "column_mapping_map2": {
                    "plan_id": "cmp_x",
                    "status": "draft",
                    "confirmed_digests": {
                        "plan_content_digest": "a" * 64,
                        "source_schema_digest": "a" * 64,
                        "destination_digest": "a" * 64,
                        "target_contract_digest": "a" * 64,
                    },
                }
            },
        )
        with self.assertRaises(SetupValidationError):
            confirmed_column_mapping_bind(session)

    def test_confirmed_digests_alone_not_sufficient(self):
        """MAP-R5: digests without upload/destination currency fail closed."""

        digests = {
            "plan_content_digest": "b" * 64,
            "source_schema_digest": "c" * 64,
            "destination_digest": "d" * 64,
            "target_contract_digest": "e" * 64,
        }
        session = ImportSession(
            owner_id=uuid4(),
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            options={
                "column_mapping_map2": {
                    "plan_id": "cmp_confirmed_1",
                    "status": "confirmed",
                    "confirmed_digests": digests,
                }
            },
        )
        with self.assertRaises(SetupValidationError) as ctx:
            confirmed_column_mapping_bind(session)
        self.assertIn("no longer matches", str(ctx.exception).lower())


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class ColumnMappingMap3SetupBindCurrencyTests(TestCase):
    """Bind succeeds only when digests and upload/destination still match."""

    def test_confirmed_bind_payload_when_current(self):
        digests = {
            "plan_content_digest": "b" * 64,
            "source_schema_digest": "c" * 64,
            "destination_digest": "d" * 64,
            "target_contract_digest": "e" * 64,
        }
        session = ImportSession.objects.create(
            owner_id=uuid4(),
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            options={
                "column_mapping_map2": {
                    "plan_id": "cmp_confirmed_1",
                    "status": "confirmed",
                    "confirmed_digests": digests,
                    "upload_id": "upload-bind-1",
                    "destination": {
                        "destination_mode": "catalog",
                        "catalog_id": "easyimports.account_fields.v1",
                    },
                    "source_headers": ["account_name", "website"],
                }
            },
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-bind-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-bind-1",
        )
        SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="accounts.csv",
            stored_path="accounts.csv",
            api_upload_id="upload-bind-1",
            columns=["account_name", "website"],
            upload_mutation=mutation,
        )
        bind = confirmed_column_mapping_bind(session)
        self.assertEqual(bind["plan_id"], "cmp_confirmed_1")
        self.assertEqual(bind["plan_content_digest"], digests["plan_content_digest"])
        self.assertEqual(bind["source_schema_digest"], digests["source_schema_digest"])
