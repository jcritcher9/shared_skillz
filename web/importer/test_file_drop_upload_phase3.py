"""Phase 3: auto-start mapping review after isolated upload (R1 / R18 / R19)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .api_client import MutationDispatchResult
from .models import ApiMutation, ImportSession, SourceFile
from .setup_service import authoritative_mapping_source
from .workflow_state import OWNER_SESSION_KEY


def _token_for_role(html: str, role: str) -> str:
    role_at = html.index(f'name="role" value="{role}"')
    marker = 'name="form_token" value="'
    start = html.rindex(marker, 0, role_at) + len(marker)
    end = html.index('"', start)
    return html[start:end]


def _plan(*, plan_id: str = "cmp_p3", status: str = "draft", headers=None) -> dict:
    headers = list(headers or ["Name", "Website"])
    return {
        "plan_id": plan_id,
        "status": status,
        "destination": {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.account_fields.v1",
        },
        "source_schema": [
            {"source_ordinal": i, "source_header": header}
            for i, header in enumerate(headers)
        ],
        "rows": [],
    }


class Phase3AutoStartMappingTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(self.owner)
        browser.save()

    def _session(self, **kwargs) -> ImportSession:
        defaults = {
            "owner_id": self.owner,
            "product_key": "",
            "setup_entity": "accounts",
            "setup_operation": "clean_only",
            "setup_reference_source": "none",
            "setup_revision": 1,
            "target_provider_id": "fake-preview-v1",
            "operator_label": "Phase 3 Operator",
        }
        defaults.update(kwargs)
        return ImportSession.objects.create(**defaults)

    def _completed_source(
        self,
        session: ImportSession,
        *,
        role: str,
        upload_id: str,
        name: str,
        columns: list[str],
    ) -> SourceFile:
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"p3-{role}-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id=upload_id,
            logical_action_identity=f"upload:{session.id}:{role}",
        )
        return SourceFile.objects.create(
            session=session,
            role=role,
            original_name=name,
            stored_path=name,
            api_upload_id=upload_id,
            columns=columns,
            upload_mutation=mutation,
        )

    def _complete_plan_dispatch(self, plan: dict):
        def _dispatch(mutation, **_kwargs):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = plan
            mutation.http_status = 201
            mutation.save(
                update_fields=["state", "response_json", "http_status"]
            )
            return MutationDispatchResult(mutation, plan)

        return _dispatch

    def test_isolated_upload_ready_journals_plan_create_and_redirects_review(self):
        session = self._session()
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        token = _token_for_role(page.content.decode("utf-8"), "dataset")
        plan = _plan(headers=["Name"])

        def fake_register(**kwargs):
            return SimpleNamespace(
                mutation=self._completed_source(
                    session,
                    role="dataset",
                    upload_id="upl-dataset",
                    name="accounts.csv",
                    columns=["Name"],
                ).upload_mutation,
                source=SourceFile.objects.get(session=session, role="dataset"),
            )

        with patch(
            "importer.workflow_views.save_and_register_upload",
            side_effect=fake_register,
        ):
            with patch(
                "importer.column_mapping_views.EasyImportsApiClient"
            ) as mock_cls:
                api = MagicMock()
                mock_cls.return_value = api
                api.dispatch.side_effect = self._complete_plan_dispatch(plan)
                response = self.client.post(
                    reverse("importer:upload", args=[session.id]),
                    data={
                        "form_token": token,
                        "role": "dataset",
                        "csv_encoding": "utf-8-sig",
                        "xlsx_sheet_index": "0",
                        "file": SimpleUploadedFile(
                            "accounts.csv", b"Name\nAcme\n", content_type="text/csv"
                        ),
                    },
                )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/column-mapping/cmp_p3/", response["Location"])
        creates = session.api_mutations.filter(
            mutation_kind="column_mapping_plan_create"
        )
        self.assertEqual(creates.count(), 1)
        source = authoritative_mapping_source(session)
        self.assertIsNotNone(source)
        self.assertEqual(source.role, "dataset")
        session.refresh_from_db()
        draft = (session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_p3")
        self.assertEqual(draft.get("upload_id"), "upl-dataset")

    def test_accounts_first_does_not_create_plan_until_raw_list(self):
        session = self._session(
            setup_operation="crm_matching",
            setup_reference_source="uploaded",
        )
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        token = _token_for_role(page.content.decode("utf-8"), "accounts")

        def fake_accounts(**kwargs):
            return SimpleNamespace(
                mutation=self._completed_source(
                    session,
                    role="accounts",
                    upload_id="upl-accounts",
                    name="crm-accounts.csv",
                    columns=["Id", "Name"],
                ).upload_mutation,
                source=SourceFile.objects.get(session=session, role="accounts"),
            )

        with patch(
            "importer.workflow_views.save_and_register_upload",
            side_effect=fake_accounts,
        ):
            with patch(
                "importer.column_mapping_views.EasyImportsApiClient"
            ) as mock_cls:
                api = MagicMock()
                mock_cls.return_value = api
                response = self.client.post(
                    reverse("importer:upload", args=[session.id]),
                    data={
                        "form_token": token,
                        "role": "accounts",
                        "csv_encoding": "utf-8-sig",
                        "xlsx_sheet_index": "0",
                        "file": SimpleUploadedFile(
                            "crm-accounts.csv",
                            b"Id,Name\n1,Acme\n",
                            content_type="text/csv",
                        ),
                    },
                )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:upload", args=[session.id]),
        )
        self.assertEqual(
            session.api_mutations.filter(
                mutation_kind="column_mapping_plan_create"
            ).count(),
            0,
        )
        api.dispatch.assert_not_called()

        page2 = self.client.get(reverse("importer:upload", args=[session.id]))
        token2 = _token_for_role(page2.content.decode("utf-8"), "raw_list")
        plan = _plan(headers=["Company"])

        def fake_raw(**kwargs):
            return SimpleNamespace(
                mutation=self._completed_source(
                    session,
                    role="raw_list",
                    upload_id="upl-raw",
                    name="new-accounts.csv",
                    columns=["Company"],
                ).upload_mutation,
                source=SourceFile.objects.get(session=session, role="raw_list"),
            )

        with patch(
            "importer.workflow_views.save_and_register_upload",
            side_effect=fake_raw,
        ):
            with patch(
                "importer.column_mapping_views.EasyImportsApiClient"
            ) as mock_cls:
                api = MagicMock()
                mock_cls.return_value = api
                api.dispatch.side_effect = self._complete_plan_dispatch(plan)
                response2 = self.client.post(
                    reverse("importer:upload", args=[session.id]),
                    data={
                        "form_token": token2,
                        "role": "raw_list",
                        "csv_encoding": "utf-8-sig",
                        "xlsx_sheet_index": "0",
                        "file": SimpleUploadedFile(
                            "new-accounts.csv",
                            b"Company\nBeta\n",
                            content_type="text/csv",
                        ),
                    },
                )
        self.assertEqual(response2.status_code, 302)
        self.assertIn("/column-mapping/cmp_p3/", response2["Location"])
        source = authoritative_mapping_source(session)
        self.assertEqual(source.role, "raw_list")
        self.assertEqual(source.api_upload_id, "upl-raw")

    def test_people_matching_waits_for_contacts_then_plans_from_raw_list(self):
        session = self._session(
            setup_entity="people",
            setup_operation="crm_matching",
            setup_reference_source="uploaded",
            setup_people_output="",
        )
        self._completed_source(
            session,
            role="raw_list",
            upload_id="upl-people",
            name="people.csv",
            columns=["Email"],
        )
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        token = _token_for_role(page.content.decode("utf-8"), "contacts")

        def fake_contacts(**kwargs):
            return SimpleNamespace(
                mutation=self._completed_source(
                    session,
                    role="contacts",
                    upload_id="upl-contacts",
                    name="contacts.csv",
                    columns=["Id", "Email"],
                ).upload_mutation,
                source=SourceFile.objects.get(session=session, role="contacts"),
            )

        plan = _plan(
            headers=["Email"],
            plan_id="cmp_people",
        )
        plan["destination"]["catalog_id"] = "easyimports.people_matching_fields.v1"
        with patch(
            "importer.workflow_views.save_and_register_upload",
            side_effect=fake_contacts,
        ):
            with patch(
                "importer.column_mapping_views.EasyImportsApiClient"
            ) as mock_cls:
                api = MagicMock()
                mock_cls.return_value = api
                api.dispatch.side_effect = self._complete_plan_dispatch(plan)
                response = self.client.post(
                    reverse("importer:upload", args=[session.id]),
                    data={
                        "form_token": token,
                        "role": "contacts",
                        "csv_encoding": "utf-8-sig",
                        "xlsx_sheet_index": "0",
                        "file": SimpleUploadedFile(
                            "contacts.csv",
                            b"Id,Email\n1,a@b.c\n",
                            content_type="text/csv",
                        ),
                    },
                )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/column-mapping/cmp_people/", response["Location"])
        source = authoritative_mapping_source(session)
        self.assertEqual(source.role, "raw_list")
        self.assertEqual(source.api_upload_id, "upl-people")

    def test_matching_confirmed_later_upload_stays_on_files(self):
        session = self._session(
            setup_operation="crm_matching",
            setup_reference_source="uploaded",
        )
        raw = self._completed_source(
            session,
            role="raw_list",
            upload_id="upl-raw",
            name="list.csv",
            columns=["Company"],
        )
        dest = {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.account_fields.v1",
        }
        session.options = {
            "column_mapping_map2": {
                "plan_id": "cmp_confirmed",
                "upload_id": raw.api_upload_id,
                "status": "confirmed",
                "destination": dest,
                "create_generation": 0,
            }
        }
        session.save(update_fields=["options"])
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        token = _token_for_role(page.content.decode("utf-8"), "accounts")

        def fake_accounts(**kwargs):
            return SimpleNamespace(
                mutation=self._completed_source(
                    session,
                    role="accounts",
                    upload_id="upl-accounts",
                    name="accounts.csv",
                    columns=["Id"],
                ).upload_mutation,
                source=SourceFile.objects.get(session=session, role="accounts"),
            )

        with patch(
            "importer.workflow_views.save_and_register_upload",
            side_effect=fake_accounts,
        ):
            with patch(
                "importer.column_mapping_views.EasyImportsApiClient"
            ) as mock_cls:
                api = MagicMock()
                mock_cls.return_value = api
                response = self.client.post(
                    reverse("importer:upload", args=[session.id]),
                    data={
                        "form_token": token,
                        "role": "accounts",
                        "csv_encoding": "utf-8-sig",
                        "xlsx_sheet_index": "0",
                        "file": SimpleUploadedFile(
                            "accounts.csv", b"Id\n1\n", content_type="text/csv"
                        ),
                    },
                )
        self.assertEqual(
            response["Location"],
            reverse("importer:upload", args=[session.id]),
        )
        self.assertEqual(
            session.api_mutations.filter(
                mutation_kind="column_mapping_plan_create"
            ).count(),
            0,
        )
        session.refresh_from_db()
        draft = (session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_confirmed")
        self.assertEqual(draft.get("status"), "confirmed")

    def test_get_new_is_side_effect_free(self):
        session = self._session()
        self._completed_source(
            session,
            role="dataset",
            upload_id="upl-dataset",
            name="accounts.csv",
            columns=["Name"],
        )
        dest = {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.account_fields.v1",
        }
        session.options = {
            "column_mapping_map2": {
                "plan_id": "cmp_keep",
                "upload_id": "upl-dataset",
                "status": "confirmed",
                "create_generation": 4,
                "destination": dest,
            }
        }
        session.save(update_fields=["options"])
        url = reverse("importer:column_mapping_session", args=[session.id])
        with patch("importer.column_mapping_views.EasyImportsApiClient") as mock_cls:
            api = MagicMock()
            mock_cls.return_value = api
            api.get_column_mapping_plan.return_value = _plan(
                plan_id="cmp_keep", status="confirmed"
            )
            response = self.client.get(url + "?new=1")
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        draft = (session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_keep")
        self.assertEqual(draft.get("create_generation"), 4)
        self.assertEqual(draft.get("status"), "confirmed")

    def test_get_matching_unconfirmed_redirects_review(self):
        session = self._session()
        self._completed_source(
            session,
            role="dataset",
            upload_id="upl-dataset",
            name="accounts.csv",
            columns=["Name"],
        )
        dest = {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.account_fields.v1",
        }
        session.options = {
            "column_mapping_map2": {
                "plan_id": "cmp_draft",
                "upload_id": "upl-dataset",
                "destination": dest,
                "create_generation": 0,
            }
        }
        session.save(update_fields=["options"])
        url = reverse("importer:column_mapping_session", args=[session.id])
        with patch("importer.column_mapping_views.EasyImportsApiClient") as mock_cls:
            api = MagicMock()
            mock_cls.return_value = api
            api.get_column_mapping_plan.return_value = _plan(
                plan_id="cmp_draft", status="draft"
            )
            response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/column-mapping/cmp_draft/", response["Location"])

    def test_upload_completed_plan_create_rejected_does_not_reupload(self):
        session = self._session()
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        token = _token_for_role(page.content.decode("utf-8"), "dataset")

        def fake_register(**kwargs):
            return SimpleNamespace(
                mutation=self._completed_source(
                    session,
                    role="dataset",
                    upload_id="upl-dataset",
                    name="accounts.csv",
                    columns=["Name"],
                ).upload_mutation,
                source=SourceFile.objects.get(session=session, role="dataset"),
            )

        rejected = ApiMutation(
            state=ApiMutation.State.REJECTED,
            error_code="column_mapping_plan_create_rejected",
            error_message="destination blocked",
        )
        rejected.refresh_from_db = MagicMock()

        with patch(
            "importer.workflow_views.save_and_register_upload",
            side_effect=fake_register,
        ) as register:
            with patch(
                "importer.column_mapping_views.EasyImportsApiClient"
            ) as mock_cls:
                api = MagicMock()
                mock_cls.return_value = api
                api.dispatch.return_value = MutationDispatchResult(
                    rejected,
                    {
                        "error": {
                            "code": "column_mapping_plan_create_rejected",
                            "message": "destination blocked",
                        }
                    },
                )
                response = self.client.post(
                    reverse("importer:upload", args=[session.id]),
                    data={
                        "form_token": token,
                        "role": "dataset",
                        "csv_encoding": "utf-8-sig",
                        "xlsx_sheet_index": "0",
                        "file": SimpleUploadedFile(
                            "accounts.csv", b"Name\nAcme\n", content_type="text/csv"
                        ),
                    },
                )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:column_mapping_session", args=[session.id]),
        )
        self.assertEqual(register.call_count, 1)
        self.assertEqual(
            session.files.filter(role="dataset").count(),
            1,
        )
        self.assertEqual(
            session.api_mutations.filter(mutation_kind="register_upload").count(),
            1,
        )


class Phase3PlanCreateRecoveryTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="",
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Phase 3 Recovery",
        )
        mutation = ApiMutation.objects.create(
            session=self.session,
            idempotency_key=f"p3-rec-upl-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upl-dataset",
            logical_action_identity=f"upload:{self.session.id}:dataset",
        )
        self.source = SourceFile.objects.create(
            session=self.session,
            role="dataset",
            original_name="accounts.csv",
            stored_path="accounts.csv",
            api_upload_id="upl-dataset",
            columns=["Name"],
            upload_mutation=mutation,
        )
        self.destination = {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.account_fields.v1",
        }

    def _seed_plan_create(self, *, state: str, acknowledged: bool = False) -> ApiMutation:
        from .api_client import create_or_reuse_mutation
        from .column_mapping_views import (
            _destination_fingerprint,
            _source_schema_from_upload,
        )

        body = {
            "source_schema": _source_schema_from_upload(self.source),
            "destination": self.destination,
            "run_auto_detect": True,
            "owner_session": f"django-{self.owner}",
        }
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="column_mapping_plan_create",
            route="/v1/column-mapping-plans",
            logical_action_identity=(
                f"column-map-create:{self.session.id}:upl-dataset:"
                f"{_destination_fingerprint(self.destination)}:g0"
            ),
            logical_action_generation=0,
            request_json=body,
            resource_identity="upl-dataset",
        )
        mutation.state = state
        if state == ApiMutation.State.PENDING:
            from django.utils import timezone

            mutation.attempt_count = 1
            mutation.dispatched_at = timezone.now()
            mutation.http_status = 202
        if state == ApiMutation.State.REJECTED:
            mutation.error_code = "column_mapping_plan_create_rejected"
            mutation.error_message = "plan create failed"
            mutation.response_json = {
                "error": {
                    "code": "column_mapping_plan_create_rejected",
                    "message": "plan create failed",
                }
            }
        if acknowledged:
            from django.utils import timezone

            mutation.acknowledged_at = timezone.now()
        mutation.save()
        return mutation

    def _start_token(self) -> str:
        page = self.client.get(
            reverse("importer:column_mapping_session", args=[self.session.id])
        )
        return page.context["form_token"]

    def test_unknown_auto_detect_exact_retries_same_mutation(self):
        seeded = self._seed_plan_create(state=ApiMutation.State.UNKNOWN)
        page = self.client.get(
            reverse("importer:column_mapping_session", args=[self.session.id])
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Retry saved mapping action")
        self.assertNotContains(page, "Auto-detect and review")

        plan = _plan(headers=["Name"])
        seen = {}

        def dispatch(mutation, *, explicit_retry=False, **_kwargs):
            seen["explicit_retry"] = explicit_retry
            seen["id"] = str(mutation.id)
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = plan
            mutation.save(update_fields=["state", "response_json"])
            return MutationDispatchResult(mutation, plan)

        token = page.context["form_token"]
        with patch("importer.column_mapping_views.EasyImportsApiClient") as mock_cls:
            api = MagicMock()
            mock_cls.return_value = api
            api.dispatch.side_effect = dispatch
            response = self.client.post(
                reverse("importer:column_mapping_session", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/column-mapping/cmp_p3/", response["Location"])
        self.assertTrue(seen.get("explicit_retry"))
        self.assertEqual(seen.get("id"), str(seeded.id))
        self.assertEqual(
            self.session.api_mutations.filter(
                mutation_kind="register_upload"
            ).count(),
            1,
        )
        self.assertEqual(
            self.session.api_mutations.filter(
                mutation_kind="column_mapping_plan_create"
            ).count(),
            1,
        )

    def test_pending_auto_detect_polls_without_second_upload(self):
        seeded = self._seed_plan_create(state=ApiMutation.State.PENDING)
        token = self._start_token()
        with patch("importer.column_mapping_views.EasyImportsApiClient") as mock_cls:
            api = MagicMock()
            mock_cls.return_value = api
            api.mutation_status.return_value = {
                "status": "pending",
                "retryable": False,
                "retry_after_seconds": 2,
            }
            response = self.client.post(
                reverse("importer:column_mapping_session", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:column_mapping_session", args=[self.session.id]),
        )
        api.mutation_status.assert_called_once()
        api.dispatch.assert_not_called()
        seeded.refresh_from_db()
        self.assertEqual(seeded.state, ApiMutation.State.PENDING)
        self.assertEqual(
            self.session.api_mutations.filter(
                mutation_kind="register_upload"
            ).count(),
            1,
        )
        self.assertEqual(
            self.session.api_mutations.filter(
                mutation_kind="column_mapping_plan_create"
            ).count(),
            1,
        )

    def test_rejected_stays_frozen_until_ack_then_advances_once(self):
        seeded = self._seed_plan_create(state=ApiMutation.State.REJECTED)
        page = self.client.get(
            reverse("importer:column_mapping_session", args=[self.session.id])
        )
        self.assertContains(page, "Acknowledge and try mapping again")
        self.assertNotContains(page, "Auto-detect and review")
        ack = page.context["plan_create_ack_token"]
        ack_post = self.client.post(
            reverse(
                "importer:acknowledge_rejection",
                args=[self.session.id, seeded.id],
            ),
            {"form_token": ack},
        )
        self.assertEqual(ack_post.status_code, 302)
        self.assertEqual(
            ack_post["Location"],
            reverse("importer:column_mapping_session", args=[self.session.id]),
        )
        seeded.refresh_from_db()
        self.assertIsNotNone(seeded.acknowledged_at)

        page2 = self.client.get(
            reverse("importer:column_mapping_session", args=[self.session.id])
        )
        self.assertContains(page2, "Auto-detect and review")
        token = page2.context["form_token"]
        plan = _plan(plan_id="cmp_p3_retry", headers=["Name"])

        def dispatch(mutation, **_kwargs):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = plan
            mutation.save(update_fields=["state", "response_json"])
            return MutationDispatchResult(mutation, plan)

        with patch("importer.column_mapping_views.EasyImportsApiClient") as mock_cls:
            api = MagicMock()
            mock_cls.return_value = api
            api.dispatch.side_effect = dispatch
            response = self.client.post(
                reverse("importer:column_mapping_session", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/column-mapping/cmp_p3_retry/", response["Location"])
        creates = list(
            self.session.api_mutations.filter(
                mutation_kind="column_mapping_plan_create"
            ).order_by("logical_action_generation")
        )
        self.assertEqual(len(creates), 2)
        self.assertEqual(creates[0].id, seeded.id)
        self.assertEqual(creates[0].logical_action_generation, 0)
        self.assertEqual(creates[1].logical_action_generation, 1)
        self.assertEqual(creates[1].replacement_of_id, seeded.id)
        self.assertEqual(
            self.session.api_mutations.filter(
                mutation_kind="register_upload"
            ).count(),
            1,
        )

    def test_get_mutation_status_does_not_write_mapping_draft(self):
        seeded = self._seed_plan_create(state=ApiMutation.State.COMPLETED)
        seeded.response_json = _plan(headers=["Name"])
        seeded.save(update_fields=["response_json"])
        original = {
            "column_mapping_map2": {
                "plan_id": "cmp_keep",
                "upload_id": "upl-other",
                "create_generation": 3,
                "status": "confirmed",
                "destination": {"destination_mode": "catalog", "catalog_id": "keep"},
            }
        }
        self.session.options = original
        self.session.save(update_fields=["options"])
        before = json.dumps(self.session.options, sort_keys=True, separators=(",", ":"))
        response = self.client.get(
            reverse(
                "importer:mutation_status",
                args=[self.session.id, seeded.id],
            )
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(
            payload["redirect_url"],
            reverse("importer:column_mapping_session", args=[self.session.id]),
        )
        self.session.refresh_from_db()
        after = json.dumps(
            self.session.options, sort_keys=True, separators=(",", ":")
        )
        self.assertEqual(after, before)
        self.assertEqual(
            self.session.options["column_mapping_map2"]["plan_id"], "cmp_keep"
        )

    def test_stale_retry_after_setup_change_does_not_adopt(self):
        from .column_mapping_views import adopt_completed_plan_create

        seeded = self._seed_plan_create(state=ApiMutation.State.UNKNOWN)
        page = self.client.get(
            reverse("importer:column_mapping_session", args=[self.session.id])
        )
        retry_token = page.context["plan_create_retry_token"]
        self.assertTrue(retry_token)
        newer = {
            "plan_id": "cmp_newer",
            "upload_id": "upl-dataset",
            "create_generation": 4,
            "status": "draft",
            "destination": self.destination,
        }
        self.session.setup_revision = int(self.session.setup_revision) + 1
        self.session.options = {"column_mapping_map2": newer}
        self.session.save(update_fields=["setup_revision", "options"])
        response = self.client.post(
            reverse(
                "importer:retry_mutation",
                args=[self.session.id, seeded.id],
            ),
            {"form_token": retry_token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:column_mapping_session", args=[self.session.id]),
        )
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_newer")
        self.assertEqual(draft.get("create_generation"), 4)
        seeded.state = ApiMutation.State.COMPLETED
        seeded.response_json = _plan(headers=["Name"])
        seeded.save(update_fields=["state", "response_json"])
        self.assertIsNone(adopt_completed_plan_create(self.session, seeded))
        self.session.refresh_from_db()
        draft2 = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft2.get("plan_id"), "cmp_newer")

    def test_signed_post_retry_adopts_current_completed_plan(self):
        seeded = self._seed_plan_create(state=ApiMutation.State.UNKNOWN)
        page = self.client.get(
            reverse("importer:column_mapping_session", args=[self.session.id])
        )
        retry_token = page.context["plan_create_retry_token"]
        plan = _plan(headers=["Name"])

        def dispatch(mutation, *, explicit_retry=False, **_kwargs):
            self.assertTrue(explicit_retry)
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = plan
            mutation.save(update_fields=["state", "response_json"])
            return MutationDispatchResult(mutation, plan)

        with patch("importer.workflow_views.EasyImportsApiClient") as mock_cls:
            api = MagicMock()
            mock_cls.return_value = api
            api.dispatch.side_effect = dispatch
            response = self.client.post(
                reverse(
                    "importer:retry_mutation",
                    args=[self.session.id, seeded.id],
                ),
                {"form_token": retry_token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/column-mapping/cmp_p3/", response["Location"])
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_p3")
        self.assertEqual(draft.get("upload_id"), "upl-dataset")
        self.assertEqual(
            self.session.api_mutations.filter(
                mutation_kind="register_upload"
            ).count(),
            1,
        )

    def test_garbage_token_does_not_adopt_completed_plan(self):
        seeded = self._seed_plan_create(state=ApiMutation.State.COMPLETED)
        seeded.response_json = _plan(headers=["Name"])
        seeded.save(update_fields=["response_json"])
        self.session.options = {}
        self.session.save(update_fields=["options"])
        response = self.client.post(
            reverse(
                "importer:retry_mutation",
                args=[self.session.id, seeded.id],
            ),
            {"form_token": "garbage-token"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:column_mapping_session", args=[self.session.id]),
        )
        self.assertNotIn("/column-mapping/cmp_p3/", response["Location"])
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertNotEqual(draft.get("plan_id"), "cmp_p3")
        self.assertFalse(draft)

    def test_old_setup_revision_token_does_not_adopt_completed_plan(self):
        from .column_mapping_views import plan_create_retry_action_id
        from .workflow_state import issue_form_token

        seeded = self._seed_plan_create(state=ApiMutation.State.COMPLETED)
        seeded.response_json = _plan(headers=["Name"])
        seeded.save(update_fields=["response_json"])
        self.assertEqual(self.session.setup_revision, 1)
        token = issue_form_token(
            owner_id=self.owner,
            session=self.session,
            action_kind="retry",
            action_id=plan_create_retry_action_id(self.session, seeded),
            logical_action_identity=seeded.logical_action_identity,
            logical_action_generation=seeded.logical_action_generation,
        )
        self.session.setup_revision = 2
        self.session.options = {}
        self.session.save(update_fields=["setup_revision", "options"])
        response = self.client.post(
            reverse(
                "importer:retry_mutation",
                args=[self.session.id, seeded.id],
            ),
            {"form_token": token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("importer:column_mapping_session", args=[self.session.id]),
        )
        self.assertNotIn("/column-mapping/cmp_p3/", response["Location"])
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertNotEqual(draft.get("plan_id"), "cmp_p3")
        self.assertFalse(draft)
        self.assertEqual(self.session.setup_revision, 2)
