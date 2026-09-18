"""Phase 2: isolated import-card instant load + CRM-dupe drop-to-populate.

Network-free Django tests for the no-JS POST path and the static
progressive-enhancement contract. Interactive drop/auto-submit is the
same class of proof as MAP-R4 JS asset contracts.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .api_client import EasyImportsApiClient
from .models import ApiMutation, ImportSession, SourceFile
from .workflow_state import OWNER_SESSION_KEY


def _extract(html: str, name: str) -> str:
    marker = f'name="{name}" value="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


class FileDropUploadPhase2Tests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        session = self.client.session
        session[OWNER_SESSION_KEY] = str(self.owner)
        session.save()

    def _empty_upload_session(self) -> ImportSession:
        return ImportSession.objects.create(
            owner_id=self.owner,
            product_key="",
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Phase 2 Operator",
        )

    def _source(
        self,
        session: ImportSession,
        *,
        state: str,
        role: str = "dataset",
        replacement_authorized: bool = False,
    ) -> SourceFile:
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-p2-{state}-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=state,
            result_upload_id="upload-p2" if state == ApiMutation.State.COMPLETED else "",
            logical_action_identity=f"upload:{session.id}:{role}",
            logical_action_generation=0,
        )
        return SourceFile.objects.create(
            session=session,
            role=role,
            original_name="accounts.csv",
            stored_path="accounts.csv",
            api_upload_id="upload-p2" if state == ApiMutation.State.COMPLETED else "",
            upload_mutation=mutation,
            slot_generation=0,
            replacement_authorized_at=timezone.now() if replacement_authorized else None,
        )

    def test_file_options_precede_picker_on_isolated_card(self):
        session = self._empty_upload_session()
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        customer = body.split('<details class="card run-details technical-details">')[0]
        options_at = customer.index("File options")
        file_at = customer.index('name="file"')
        add_at = customer.index("Add this file")
        self.assertLess(options_at, file_at)
        self.assertLess(file_at, add_at)
        self.assertIn('name="csv_encoding"', customer)
        self.assertIn('name="xlsx_sheet_index"', customer)
        self.assertIn('data-file-drop="isolated"', customer)
        self.assertIn('data-file-drop-form="isolated"', customer)
        self.assertIn("file_drop_upload.js", body)

    def test_no_js_post_still_journals_register_upload_with_options(self):
        session = self._empty_upload_session()
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        body = page.content.decode("utf-8")
        token = _extract(body, "form_token")
        role = _extract(body, "role")
        self.assertEqual(role, "dataset")
        uploaded = SimpleUploadedFile(
            "accounts.csv",
            b"Name\nAcme\n",
            content_type="text/csv",
        )
        completed = SimpleNamespace(
            mutation=SimpleNamespace(state=ApiMutation.State.COMPLETED),
            source=SimpleNamespace(original_name="accounts.csv"),
        )
        with patch(
            "importer.workflow_views.save_and_register_upload",
            return_value=completed,
        ) as register:
            response = self.client.post(
                reverse("importer:upload", args=[session.id]),
                data={
                    "form_token": token,
                    "role": role,
                    "csv_encoding": "latin-1",
                    "xlsx_sheet_index": "2",
                    "file": uploaded,
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:upload", args=[session.id]),
        )
        register.assert_called_once()
        kwargs = register.call_args.kwargs
        self.assertEqual(kwargs["session"].id, session.id)
        self.assertEqual(kwargs["role"], "dataset")
        self.assertEqual(kwargs["csv_encoding"], "latin-1")
        self.assertEqual(kwargs["xlsx_sheet_index"], 2)
        self.assertEqual(kwargs["uploaded"].name, "accounts.csv")

    def test_pending_and_completed_cards_are_not_drop_targets(self):
        pending_session = self._empty_upload_session()
        self._source(pending_session, state=ApiMutation.State.PENDING)
        pending_page = self.client.get(
            reverse("importer:upload", args=[pending_session.id])
        )
        pending_body = pending_page.content.decode("utf-8")
        self.assertNotIn('data-file-drop="isolated"', pending_body)
        self.assertNotIn('name="file"', pending_body)
        self.assertIn("still being completed", pending_body)

        done_session = self._empty_upload_session()
        self._source(done_session, state=ApiMutation.State.COMPLETED)
        done_page = self.client.get(reverse("importer:upload", args=[done_session.id]))
        done_body = done_page.content.decode("utf-8")
        self.assertNotIn('data-file-drop="isolated"', done_body)
        self.assertNotIn('name="file"', done_body)
        self.assertIn("This file is ready for the import.", done_body)

    def test_authorized_replacement_card_is_a_drop_target(self):
        session = self._empty_upload_session()
        self._source(
            session,
            state=ApiMutation.State.REJECTED,
            replacement_authorized=True,
        )
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        body = page.content.decode("utf-8")
        self.assertIn('data-file-drop="isolated"', body)
        self.assertIn("Add this file", body)
        self.assertIn("Choose a new file to replace", body)


class CrmDupeDropPopulatePhase2Tests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        session = self.client.session
        session[OWNER_SESSION_KEY] = str(self.owner)
        session.save()

    def _start_page(self):
        connections = {
            "connections": [
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                    "maximum_authorization": {
                        "duplicate_execution": "execute",
                        "reference_acquisition": "execute",
                    },
                }
            ]
        }
        patches = (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=connections,
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": []},
            ),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            return self.client.get(reverse("importer:crm_duplicate_journey"))

    def test_start_form_is_populate_only_not_isolated(self):
        page = self._start_page()
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn('id="crm-duplicate-journey-form"', body)
        self.assertIn('data-file-drop="populate"', body)
        self.assertIn('name="population_file"', body)
        self.assertNotIn('data-file-drop="isolated"', body)
        self.assertNotIn('data-file-drop-form="isolated"', body)
        self.assertIn("Upload and continue", body)
        self.assertIn("This does not start the journey.", body)
        self.assertIn("file_drop_upload.js", body)

    def test_explicit_upload_and_continue_still_lands_on_record_id_map(self):
        page = self._start_page()
        token = _extract(page.content.decode("utf-8"), "form_token")
        pop_body = {
            "population_upload_id": "pop-p2",
            "upload_id": "upl-p2",
            "entity_family": "company",
            "connection_id": "crm_conn_test",
            "source_headers": ["Id", "Name"],
            "preview_rows": [["A1", "One"]],
            "suggested_source_column": "Id",
            "filename": "records.csv",
            "row_count": 1,
            "population_source": "uploaded_population",
            "status": "registered",
            "record_id_mapped": False,
            "requires_group_id": False,
            "requires_member_id": False,
            "byte_count": 10,
            "content_digest": "d",
            "created_at": "2026-08-04T00:00:00Z",
            "inspect_digest": "i",
            "media_type": "text/csv",
            "parser_contract": "p",
            "population_contract": "c",
            "schema_version": "1",
        }

        def fake_upload(**kwargs):
            mock = SimpleNamespace()
            mock.response = {
                "upload_id": "upl-p2",
                "filename": "records.csv",
            }
            return mock

        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "crm_connections",
                    return_value={
                        "connections": [
                            {
                                "connection_id": "crm_conn_test",
                                "provider_key": "fake",
                                "provider_label": "Fake CRM",
                                "status": "connected",
                                "display_label": "Demo portal",
                                "maximum_authorization": {
                                    "duplicate_execution": "execute",
                                    "reference_acquisition": "execute",
                                },
                            }
                        ]
                    },
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "crm_duplicate_journeys",
                        return_value={"journeys": []},
                    ):
                        with patch(
                            "importer.journey_views.save_and_register_upload",
                            side_effect=fake_upload,
                        ) as register:
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                return_value=pop_body,
                            ):
                                response = self.client.post(
                                    reverse("importer:crm_duplicate_journey"),
                                    data={
                                        "connection_id": "crm_conn_test",
                                        "entity_family": "company",
                                        "source_mode": "uploaded_population",
                                        "form_token": token,
                                        "population_file": SimpleUploadedFile(
                                            "records.csv",
                                            b"Id,Name\nA1,One\n",
                                            content_type="text/csv",
                                        ),
                                    },
                                )
        self.assertEqual(response.status_code, 302)
        draft = ImportSession.objects.get(
            product_key="easyimports.duplicate_resolution"
        )
        self.assertEqual(
            response["Location"],
            reverse(
                "importer:crm_duplicate_population_map",
                args=[draft.id],
            ),
        )
        register.assert_called_once()


class FileDropUploadJsAssetTests(TestCase):
    def test_js_asset_keeps_isolated_submit_and_populate_only_contracts(self):
        js_path = (
            Path(__file__).resolve().parent
            / "static"
            / "importer"
            / "js"
            / "file_drop_upload.js"
        )
        text = js_path.read_text(encoding="utf-8")
        self.assertIn('data-file-drop="isolated"', text)
        self.assertIn('data-file-drop="populate"', text)
        self.assertIn(".csv", text)
        self.assertIn(".xlsx", text)
        self.assertIn("requestSubmit", text)
        self.assertIn("Drop one CSV or Excel file at a time.", text)
        self.assertIn("Use a CSV or Excel (.xlsx) file.", text)
        self.assertIn("crm-duplicate-journey-form", text)
        self.assertNotIn("column_mapping_plan_create", text)
        populate_idx = text.index("function enhancePopulate")
        isolated_idx = text.index("function enhanceIsolated")
        populate_fn = text[populate_idx:]
        isolated_fn = text[isolated_idx:populate_idx]
        populate_submit = populate_fn.find("requestSubmit")
        isolated_submit = isolated_fn.find("requestSubmit")
        self.assertGreater(isolated_submit, -1)
        self.assertEqual(populate_submit, -1)
        self.assertIn("input starts disabled", populate_fn)
        self.assertNotIn("if (!input || input.disabled) return;", populate_fn)

    def test_css_highlights_drop_targets(self):
        css_path = (
            Path(__file__).resolve().parent
            / "static"
            / "importer"
            / "css"
            / "app.css"
        )
        text = css_path.read_text(encoding="utf-8")
        self.assertIn(".upload-card[data-file-drop=\"isolated\"].is-dragover", text)
        self.assertIn(".file-drop-zone.is-dragover", text)
        self.assertIn(".file-drop-error", text)
