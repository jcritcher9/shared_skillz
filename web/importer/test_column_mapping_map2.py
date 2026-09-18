"""MAP-2 rem2 Django UI tests — rejected dispatch fail-closed + confirm recovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from .api_client import ApiRejectedError, MutationDispatchResult
from .column_mapping_views import (
    CATALOG_ACCOUNT_FIELDS_V1,
    CATALOG_CONTACT_FIELDS_V1,
    CATALOG_LEAD_FIELDS_V1,
    CATALOG_PEOPLE_MATCHING_FIELDS_V1,
    _catalog_id_for_setup,
    _destination_for_session,
)
from .models import ApiMutation, ImportSession, SourceFile
from .workflow_state import OWNER_SESSION_KEY


def _draft_plan(**overrides):
    body = {
        "plan_id": "cmp_testplan01",
        "schema_version": "column_mapping_plan.v1",
        "status": "draft",
        "owner_binding": "owner",
        "destination_mode": "catalog",
        "destination": {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.default_fields.v1",
        },
        "source_schema": [
            {"source_ordinal": 0, "source_header": "Email"},
            {"source_ordinal": 1, "source_header": "Notes"},
        ],
        "rows": [
            {
                "source_ordinal": 0,
                "source_header": "Email",
                "mapping_choice_id": "catalog:easyimports.default_fields.v1:person_email",
                "disposition": "mapped",
                "choice_origin": "auto_detected",
                "display_target_label": "Email",
            },
            {
                "source_ordinal": 1,
                "source_header": "Notes",
                "mapping_choice_id": None,
                "disposition": "unresolved",
                "choice_origin": None,
                "display_target_label": None,
            },
        ],
        "source_schema_digest": "a" * 64,
        "destination_digest": "b" * 64,
        "target_contract_digest": "c" * 64,
        "plan_content_digest": "d" * 64,
        "confirmed_digests": None,
    }
    body.update(overrides)
    return body


def _choices():
    return {
        "choices": [
            {
                "mapping_choice_id": "system:ignore",
                "label": "Ignore",
                "choice_kind": "ignore",
                "object_scope": None,
            },
            {
                "mapping_choice_id": "catalog:easyimports.default_fields.v1:person_email",
                "label": "Email",
                "choice_kind": "scalar",
                "object_scope": None,
            },
        ]
    }


def _rejected_mutation(*, code: str, message: str) -> ApiMutation:
    mutation = MagicMock(spec=ApiMutation)
    mutation.state = ApiMutation.State.REJECTED
    mutation.error_code = code
    mutation.error_message = message
    mutation.response_json = {"error": {"code": code, "message": message}}
    mutation.refresh_from_db = MagicMock()
    return mutation


def _completed_mutation(plan: dict) -> ApiMutation:
    mutation = MagicMock(spec=ApiMutation)
    mutation.state = ApiMutation.State.COMPLETED
    mutation.error_code = ""
    mutation.error_message = ""
    mutation.response_json = plan
    mutation.refresh_from_db = MagicMock()
    return mutation


class ColumnMappingMap2UiTests(TestCase):
    def setUp(self):
        self.client = Client()
        owner = uuid4()
        session = self.client.session
        session[OWNER_SESSION_KEY] = str(owner)
        session.save()
        self.owner = owner
        self.session = ImportSession.objects.create(
            owner_id=owner,
            status=ImportSession.Status.CREATED,
            setup_entity="people",
            setup_operation="clean_only",
            # MAP-R1: People clean-only requires singular people_output (setup_router).
            setup_people_output="contact",
            setup_reference_source="none",
        )
        self.source = SourceFile.objects.create(
            session=self.session,
            role="dataset",
            original_name="list.csv",
            stored_path="list.csv",
            columns=["Email", "Notes"],
            api_upload_id="upload_test_1",
        )

    def _use_matching_list_source(self) -> None:
        self.source.role = "raw_list"
        self.source.save(update_fields=["role"])

    def test_session_get_shows_upload_headers_not_paste(self):
        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email")
        self.assertContains(response, "Notes")
        self.assertNotContains(response, "Paste")
        self.assertNotContains(response, "canon")
        # People clean-only + contact → MAP-R1 contact catalog (not silent Account).
        dest = response.context["destination"]
        self.assertEqual(dest.get("destination_mode"), "catalog")
        self.assertEqual(dest.get("catalog_id"), "easyimports.contact_fields.v1")
        self.assertFalse(response.context.get("mapping_blocked"))

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_session_post_creates_via_journal(self, mock_client_cls, mock_create):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan()
        mutation = _completed_mutation(plan)
        mock_create.return_value = mutation
        api.dispatch.return_value = MutationDispatchResult(mutation, plan)

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        page = self.client.get(url)
        token = page.context["form_token"]
        response = self.client.post(url, {"form_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/column-mapping/{plan['plan_id']}/", response["Location"])

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_rejected_dispatch_is_not_success(self, mock_client_cls, mock_create):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan()
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        rejected = _rejected_mutation(
            code="column_mapping_plan_confirm_rejected",
            message="unresolved rows block confirm",
        )
        mock_create.return_value = rejected
        api.dispatch.return_value = MutationDispatchResult(
            rejected,
            {"error": {"code": rejected.error_code, "message": rejected.error_message}},
        )

        review_url = reverse(
            "importer:column_mapping_review",
            kwargs={"session_id": self.session.id, "plan_id": "cmp_testplan01"},
        )
        page = self.client.get(review_url)
        token = page.context["form_token"]
        response = self.client.post(
            review_url,
            {"form_token": token, "action": "confirm"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unresolved rows block confirm")
        self.assertNotContains(response, "Mapping plan confirmed")

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_confirm_identity_includes_content_digest(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan(plan_content_digest="digest_version_1")
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        confirmed = _draft_plan(
            status="confirmed",
            plan_content_digest="digest_version_1",
            confirmed_digests={
                "plan_content_digest": "digest_version_1",
                "source_schema_digest": "a" * 64,
                "destination_digest": "b" * 64,
                "target_contract_digest": "c" * 64,
            },
        )
        mutation = _completed_mutation(confirmed)
        mock_create.return_value = mutation
        api.dispatch.return_value = MutationDispatchResult(mutation, confirmed)

        review_url = reverse(
            "importer:column_mapping_review",
            kwargs={"session_id": self.session.id, "plan_id": "cmp_testplan01"},
        )
        page = self.client.get(review_url)
        token = page.context["form_token"]
        self.client.post(review_url, {"form_token": token, "action": "confirm"})
        identity = mock_create.call_args.kwargs["logical_action_identity"]
        self.assertIn("digest_version_1", identity)
        self.assertEqual(
            mock_create.call_args.kwargs["mutation_kind"],
            "column_mapping_plan_confirm",
        )

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_stale_draft_plan_not_resumed_after_upload_change(self, mock_client_cls):
        # Saved draft for different upload
        options = dict(self.session.options or {})
        options["column_mapping_map2"] = {
            "plan_id": "cmp_old",
            "upload_id": "upload_old",
            "destination": {
                "destination_mode": "catalog",
                "catalog_id": "easyimports.default_fields.v1",
            },
        }
        self.session.options = options
        self.session.save(update_fields=["options"])

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # GET is side-effect-free: stale plan_id stays until a POST map-again/create.
        self.assertNotContains(response, "no longer matches")
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_old")

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_crm_destination_uses_connection_provider(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.return_value = {
            "connection_id": "conn-1",
            "provider_key": "fake",
            "status": "connected",
        }
        self.session.setup_entity = "people"
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-1"
        # people_output is only for clean-only; must clear for matching intent.
        self.session.setup_people_output = ""
        self.session.save()
        self._use_matching_list_source()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        dest = response.context["destination"]
        # CMX-0/M1: primary plan destination is ingest catalog; write inventory
        # is the m1_write_destination sidecar.
        self.assertEqual(dest["destination_mode"], "catalog")
        self.assertEqual(dest["catalog_id"], "easyimports.ingest.fake.contact.v1")
        write = dest["m1_write_destination"]
        self.assertEqual(write["destination_mode"], "crm")
        self.assertEqual(write["provider_key"], "fake-crm-phase0b-v1")
        self.assertEqual(write["connection_id"], "conn-1")
        self.assertEqual(write["object_keys"], ["Contact"])
        self.assertFalse(response.context.get("mapping_blocked"))
        api.crm_connection.assert_called()

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_crm_accounts_destination_uses_account_object(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.return_value = {
            "connection_id": "conn-acct",
            "provider_key": "fake",
            "status": "connected",
        }
        self.session.setup_entity = "accounts"
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-acct"
        self.session.setup_people_output = ""
        self.session.save()
        self._use_matching_list_source()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        dest = response.context["destination"]
        self.assertEqual(dest["destination_mode"], "catalog")
        self.assertEqual(dest["catalog_id"], "easyimports.ingest.fake.account.v1")
        self.assertEqual(dest["m1_write_destination"]["object_keys"], ["Account"])
        self.assertFalse(response.context.get("mapping_blocked"))

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_salesforce_crm_destination_uses_contact_for_people(self, mock_client_cls):
        """M1: SF people match uses ingest contact catalog; write bind → Contact."""

        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.return_value = {
            "connection_id": "conn-sf",
            "provider_key": "salesforce",
            "status": "connected",
        }
        self.session.setup_entity = "people"
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-sf"
        self.session.setup_people_output = ""
        self.session.save()
        self._use_matching_list_source()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        dest = response.context["destination"]
        self.assertEqual(dest["destination_mode"], "catalog")
        self.assertEqual(
            dest["catalog_id"], "easyimports.ingest.salesforce.contact.v1"
        )
        write = dest["m1_write_destination"]
        self.assertEqual(write["provider_key"], "salesforce")
        self.assertEqual(write["object_keys"], ["Contact"])
        self.assertFalse(response.context.get("mapping_blocked"))

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_hubspot_accounts_destination_uses_company(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.return_value = {
            "connection_id": "conn-hs",
            "provider_key": "hubspot",
            "status": "connected",
        }
        self.session.setup_entity = "accounts"
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-hs"
        self.session.setup_people_output = ""
        self.session.save()
        self._use_matching_list_source()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        dest = response.context["destination"]
        self.assertEqual(dest["destination_mode"], "catalog")
        self.assertEqual(dest["catalog_id"], "easyimports.ingest.hubspot.account.v1")
        write = dest["m1_write_destination"]
        self.assertEqual(write["provider_key"], "hubspot")
        self.assertEqual(write["object_keys"], ["Company"])
        self.assertFalse(response.context.get("mapping_blocked"))

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_unsupported_crm_provider_fails_closed(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.return_value = {
            "connection_id": "conn-unknown",
            "provider_key": "acme-crm",
            "status": "connected",
        }
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-unknown"
        self.session.setup_people_output = ""
        self.session.save()
        self._use_matching_list_source()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not available yet")
        self.assertTrue(response.context.get("mapping_blocked"))
        # Must not invent a catalog destination when CRM inventory is unavailable.
        self.assertNotEqual(
            (response.context.get("destination") or {}).get("destination_mode"),
            "catalog",
        )
        self.assertNotContains(response, "Auto-detect and review")

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_crm_connection_not_connected_fails_closed(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.return_value = {
            "connection_id": "conn-pending",
            "provider_key": "fake",
            "status": "pending",
        }
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-pending"
        self.session.setup_people_output = ""
        self._use_matching_list_source()
        self.session.save()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not connected")
        self.assertTrue(response.context.get("mapping_blocked"))
        self.assertNotContains(response, "Auto-detect and review")

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_map_again_after_confirmed_does_not_resume_plan(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.get_column_mapping_plan.return_value = _draft_plan(status="confirmed")
        matching_dest = {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.contact_fields.v1",
        }
        options = dict(self.session.options or {})
        options["column_mapping_map2"] = {
            "plan_id": "cmp_testplan01",
            "upload_id": "upload_test_1",
            "status": "confirmed",
            "create_generation": 0,
            "destination": matching_dest,
        }
        self.session.options = options
        self.session.save(update_fields=["options"])

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        # GET is side-effect-free (R1): confirmed matching plan stays in draft.
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Map again")
        self.assertNotContains(response, "Auto-detect and review")
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_testplan01")
        self.assertEqual(draft.get("create_generation"), 0)

        # GET ?new=1 also must not rewrite the draft.
        response2 = self.client.get(url + "?new=1")
        self.assertEqual(response2.status_code, 200)
        self.session.refresh_from_db()
        draft2 = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft2.get("plan_id"), "cmp_testplan01")
        self.assertEqual(draft2.get("create_generation"), 0)

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_map_again_get_then_post_uses_new_generation_not_zero(
        self, mock_client_cls
    ):
        """Map-again POST bumps generation; GET ?new=1 does not."""

        api = MagicMock()
        mock_client_cls.return_value = api
        matching_dest = {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.contact_fields.v1",
        }
        options = dict(self.session.options or {})
        options["column_mapping_map2"] = {
            "plan_id": "cmp_confirmed_old",
            "upload_id": "upload_test_1",
            "status": "confirmed",
            "create_generation": 0,
            "destination": matching_dest,
        }
        self.session.options = options
        self.session.save(update_fields=["options"])
        api.get_column_mapping_plan.return_value = _draft_plan(
            plan_id="cmp_confirmed_old", status="confirmed"
        )

        new_plan = _draft_plan(plan_id="cmp_new_after_map_again", status="draft")
        # Real create_or_reuse_mutation — only API dispatch is mocked.
        def _dispatch(mutation, **_kwargs):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = new_plan
            mutation.error_code = ""
            mutation.error_message = ""
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "error_code",
                    "error_message",
                    "http_status",
                ]
            )
            return MutationDispatchResult(mutation, new_plan)

        api.dispatch.side_effect = _dispatch

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        page = self.client.get(url + "?new=1")
        self.assertEqual(page.status_code, 200)
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("plan_id"), "cmp_confirmed_old")
        self.assertEqual(draft.get("create_generation"), 0)
        token = page.context["form_token"]

        response = self.client.post(
            url, {"form_token": token, "action": "map_again"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("cmp_new_after_map_again", response["Location"])

        creates = list(
            self.session.api_mutations.filter(
                mutation_kind="column_mapping_plan_create"
            ).order_by("created_at")
        )
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0].logical_action_generation, 1)
        self.assertIn(":g1", creates[0].logical_action_identity)

        self.session.refresh_from_db()
        draft2 = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft2.get("plan_id"), "cmp_new_after_map_again")

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_create_rejects_replayed_confirmed_plan_resource(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        confirmed = _draft_plan(status="confirmed")
        mutation = _completed_mutation(confirmed)
        mock_create.return_value = mutation
        api.dispatch.return_value = MutationDispatchResult(mutation, confirmed)

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        page = self.client.get(url)
        token = page.context["form_token"]
        response = self.client.post(url, {"form_token": token}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not start a new mapping plan")
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("create_generation"), 1)

    def test_catalog_id_for_setup_matrix_and_fail_closed(self):
        """MAP-R1 routing: frozen combos only; no silent Account/People-matching."""

        self.assertEqual(
            _catalog_id_for_setup(
                entity="accounts", operation="clean_only", people_output=None
            ),
            CATALOG_ACCOUNT_FIELDS_V1,
        )
        self.assertEqual(
            _catalog_id_for_setup(
                entity="accounts", operation="crm_matching", people_output=None
            ),
            CATALOG_ACCOUNT_FIELDS_V1,
        )
        self.assertEqual(
            _catalog_id_for_setup(
                entity="people", operation="clean_only", people_output="contact"
            ),
            CATALOG_CONTACT_FIELDS_V1,
        )
        self.assertEqual(
            _catalog_id_for_setup(
                entity="people", operation="clean_only", people_output="lead"
            ),
            CATALOG_LEAD_FIELDS_V1,
        )
        self.assertEqual(
            _catalog_id_for_setup(
                entity="people", operation="crm_matching", people_output=None
            ),
            CATALOG_PEOPLE_MATCHING_FIELDS_V1,
        )
        self.assertEqual(
            _catalog_id_for_setup(
                entity="people", operation="crm_matching", people_output="contact"
            ),
            CATALOG_PEOPLE_MATCHING_FIELDS_V1,
        )
        # Incomplete / unfrozen contexts raise (no default substitution).
        with self.assertRaises(ValueError) as missing_setup:
            _catalog_id_for_setup(entity=None, operation=None, people_output=None)
        self.assertIn("incomplete", str(missing_setup.exception).lower())
        with self.assertRaises(ValueError) as people_clean:
            _catalog_id_for_setup(
                entity="people", operation="clean_only", people_output=None
            )
        self.assertIn("Contacts or Leads", str(people_clean.exception))
        with self.assertRaises(ValueError) as bad_combo:
            _catalog_id_for_setup(
                entity="widgets", operation="clean_only", people_output=None
            )
        self.assertIn("not available", str(bad_combo.exception).lower())

    def test_session_get_account_catalog(self):
        self.session.setup_entity = "accounts"
        self.session.setup_operation = "clean_only"
        self.session.setup_people_output = ""
        self.session.setup_reference_source = "none"
        self.session.save()
        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        dest = response.context["destination"]
        self.assertEqual(dest["catalog_id"], CATALOG_ACCOUNT_FIELDS_V1)
        self.assertContains(response, CATALOG_ACCOUNT_FIELDS_V1)
        self.assertFalse(response.context.get("mapping_blocked"))

    def test_session_get_lead_catalog(self):
        self.session.setup_entity = "people"
        self.session.setup_operation = "clean_only"
        self.session.setup_people_output = "lead"
        self.session.setup_reference_source = "none"
        self.session.save()
        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["destination"]["catalog_id"], CATALOG_LEAD_FIELDS_V1
        )

    def test_session_get_people_matching_catalog(self):
        self.session.setup_entity = "people"
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "uploaded"
        self.session.setup_people_output = ""
        self.session.setup_connection_id = ""
        self.session.save()
        self._use_matching_list_source()
        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["destination"]["catalog_id"],
            CATALOG_PEOPLE_MATCHING_FIELDS_V1,
        )

    def test_people_clean_only_without_output_blocks_mapping(self):
        self.session.setup_entity = "people"
        self.session.setup_operation = "clean_only"
        self.session.setup_people_output = ""
        self.session.setup_reference_source = "none"
        self.session.save()
        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("mapping_blocked"))
        self.assertContains(response, "Contacts or Leads")
        self.assertNotContains(response, "Auto-detect and review")
        # Must not silently land on Account or People-matching.
        self.assertNotContains(response, CATALOG_ACCOUNT_FIELDS_V1)
        self.assertNotContains(response, CATALOG_PEOPLE_MATCHING_FIELDS_V1)

    def test_missing_setup_blocks_mapping(self):
        self.session.setup_entity = ""
        self.session.setup_operation = ""
        self.session.setup_people_output = ""
        self.session.save()
        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("mapping_blocked"))
        self.assertContains(response, "incomplete")
        self.assertNotContains(response, "Auto-detect and review")

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_crm_resolution_failure_does_not_switch_to_catalog(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.crm_connection.side_effect = ApiRejectedError(
            "crm_connection_not_found", "connection missing"
        )
        self.session.setup_operation = "crm_matching"
        self.session.setup_reference_source = "connected_crm"
        self.session.setup_connection_id = "conn-gone"
        self.session.setup_people_output = ""
        self.session.save()
        self._use_matching_list_source()

        url = reverse(
            "importer:column_mapping_session", kwargs={"session_id": self.session.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("mapping_blocked"))
        self.assertContains(response, "Cannot resolve CRM connection")
        dest = response.context.get("destination") or {}
        self.assertNotEqual(dest.get("destination_mode"), "catalog")
        self.assertNotEqual(dest.get("catalog_id"), CATALOG_ACCOUNT_FIELDS_V1)
        self.assertNotContains(response, "Auto-detect and review")

    def test_destination_for_session_catalog_paths(self):
        self.session.setup_entity = "accounts"
        self.session.setup_operation = "clean_only"
        self.session.setup_people_output = ""
        self.session.setup_reference_source = "none"
        self.session.setup_connection_id = ""
        self.session.save()
        dest = _destination_for_session(
            self.session, owner_session="django-test", client=MagicMock()
        )
        self.assertEqual(dest["catalog_id"], CATALOG_ACCOUNT_FIELDS_V1)
