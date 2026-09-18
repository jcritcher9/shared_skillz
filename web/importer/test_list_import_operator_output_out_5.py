"""OUT-5A / OUT-5B Django mapping ignore-and-confirm.

Network-free. Django does not import mappings_2.

OUT-5A: one Ignore all unmapped control covers leftover unresolved rows.
OUT-5B: ignore that leaves zero unresolved rows dispatches intent=confirm,
persists the confirmed bind, and redirects to Configure only after success.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from .api_client import MutationDispatchResult
from .column_mapping_views import SYSTEM_IGNORE
from .models import ApiMutation, ImportSession
from .test_column_mapping_map_r4_ui import (
    _choices,
    _completed_mutation,
    _draft_plan,
)
from .workflow_state import OWNER_SESSION_KEY


def _leftover_plan(*, leftover_count: int = 20):
    """One mapped required field plus N unresolved leftovers."""

    source_schema = [
        {
            "source_ordinal": 0,
            "source_header": "account_name",
            "sample_values": ["Acme"],
        }
    ]
    rows = [
        {
            "source_ordinal": 0,
            "source_header": "account_name",
            "mapping_choice_id": "catalog:account:name",
            "disposition": "mapped",
            "choice_origin": "auto_detected",
            "display_target_label": "Account name",
        }
    ]
    for index in range(leftover_count):
        ordinal = index + 1
        header = f"leftover_{index:02d}"
        source_schema.append(
            {
                "source_ordinal": ordinal,
                "source_header": header,
                "sample_values": [],
            }
        )
        rows.append(
            {
                "source_ordinal": ordinal,
                "source_header": header,
                "mapping_choice_id": None,
                "disposition": "unresolved",
                "choice_origin": None,
                "display_target_label": None,
            }
        )
    return _draft_plan(source_schema=source_schema, rows=rows)


def _confirmed_plan(plan: dict) -> dict:
    confirmed = dict(plan)
    confirmed["status"] = "confirmed"
    confirmed["confirmed_digests"] = {
        "plan_content_digest": "e" * 64,
        "source_schema_digest": "a" * 64,
        "destination_digest": "b" * 64,
        "target_contract_digest": "c" * 64,
    }
    ignored_rows = []
    for row in plan.get("rows") or []:
        item = dict(row)
        if item.get("disposition") == "unresolved":
            item["mapping_choice_id"] = SYSTEM_IGNORE
            item["disposition"] = "ignored"
            item["choice_origin"] = "operator_selected"
            item["display_target_label"] = "Ignore"
        ignored_rows.append(item)
    confirmed["rows"] = ignored_rows
    return confirmed


class Out5IgnoreAllAndConfirmTests(TestCase):
    def setUp(self):
        self.client = Client()
        owner = uuid4()
        session = self.client.session
        session[OWNER_SESSION_KEY] = str(owner)
        session.save()
        self.session = ImportSession.objects.create(
            owner_id=owner,
            status=ImportSession.Status.CREATED,
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
        )

    def _review_url(self, plan_id: str = "cmp_mapr4_plan01") -> str:
        return reverse(
            "importer:column_mapping_review",
            kwargs={"session_id": self.session.id, "plan_id": plan_id},
        )

    def _configure_url(self) -> str:
        return reverse("importer:configure", kwargs={"session_id": self.session.id})

    def _map_draft(self) -> dict:
        self.session.refresh_from_db()
        options = self.session.options if isinstance(self.session.options, dict) else {}
        raw = options.get("column_mapping_map2")
        return dict(raw) if isinstance(raw, dict) else {}

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_review_offers_ignore_all_unmapped(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.get_column_mapping_plan.return_value = _draft_plan()
        api.list_column_mapping_choices.return_value = _choices()
        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ignore all unmapped")
        self.assertContains(response, 'value="bulk_ignore_all"')
        self.assertContains(response, "Ignore selected unresolved")
        ordinary = response.content.decode("utf-8").split("Technical details")[0]
        self.assertNotIn("select every leftover column", ordinary)

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_ignore_all_sends_one_confirm_payload_for_leftovers(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _leftover_plan(leftover_count=20)
        confirmed = _confirmed_plan(plan)
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        mock_create.return_value = _completed_mutation(confirmed)
        api.dispatch.return_value = MutationDispatchResult(
            mock_create.return_value, confirmed
        )

        page = self.client.get(self._review_url())
        token = page.context["form_token"]
        response = self.client.post(
            self._review_url(),
            {"form_token": token, "action": "bulk_ignore_all"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._configure_url())
        self.assertEqual(
            mock_create.call_args.kwargs["mutation_kind"],
            "column_mapping_plan_atomic_review",
        )
        body = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(body["intent"], "confirm")
        self.assertEqual(len(body["rows"]), 20)
        self.assertEqual(
            {row["mapping_choice_id"] for row in body["rows"]},
            {SYSTEM_IGNORE},
        )
        self.assertEqual(
            [row["source_ordinal"] for row in body["rows"]],
            list(range(1, 21)),
        )
        draft = self._map_draft()
        self.assertEqual(draft.get("status"), "confirmed")
        self.assertEqual(draft.get("plan_id"), "cmp_mapr4_plan01")
        self.assertIn("plan_content_digest", draft.get("confirmed_digests") or {})

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_ignore_selected_subset_stays_draft(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _leftover_plan(leftover_count=3)
        saved = dict(plan)
        saved["plan_content_digest"] = "e" * 64
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        mock_create.return_value = _completed_mutation(saved)
        api.dispatch.return_value = MutationDispatchResult(
            mock_create.return_value, saved
        )

        page = self.client.get(self._review_url())
        token = page.context["form_token"]
        response = self.client.post(
            self._review_url(),
            {
                "form_token": token,
                "action": "bulk_ignore",
                "bulk_ordinal": ["1"],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected columns were marked as ignored")
        self.assertIn("/column-mapping/", response.request["PATH_INFO"])
        body = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(body["intent"], "save_draft")
        self.assertEqual(
            body["rows"],
            [{"source_ordinal": 1, "mapping_choice_id": SYSTEM_IGNORE}],
        )
        draft = self._map_draft()
        self.assertNotEqual(draft.get("status"), "confirmed")
        self.assertFalse(draft.get("confirmed_digests"))

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_ignore_selected_that_clears_last_gap_confirms(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan()
        confirmed = _confirmed_plan(plan)
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        mock_create.return_value = _completed_mutation(confirmed)
        api.dispatch.return_value = MutationDispatchResult(
            mock_create.return_value, confirmed
        )

        page = self.client.get(self._review_url())
        token = page.context["form_token"]
        response = self.client.post(
            self._review_url(),
            {
                "form_token": token,
                "action": "bulk_ignore",
                "bulk_ordinal": ["2"],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._configure_url())
        body = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(body["intent"], "confirm")
        self.assertEqual(
            body["rows"],
            [{"source_ordinal": 2, "mapping_choice_id": SYSTEM_IGNORE}],
        )
        draft = self._map_draft()
        self.assertEqual(draft.get("status"), "confirmed")

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_ignore_all_confirm_rejection_stays_unconfirmed(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _leftover_plan(leftover_count=2)
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        rejected = MagicMock(spec=ApiMutation)
        rejected.state = ApiMutation.State.REJECTED
        rejected.error_code = "column_mapping_plan_confirm_rejected"
        rejected.error_message = "required destination field is unmapped"
        rejected.response_json = {
            "error": {
                "code": "column_mapping_plan_confirm_rejected",
                "message": "required destination field is unmapped",
            }
        }
        rejected.refresh_from_db = MagicMock()
        mock_create.return_value = rejected
        api.dispatch.return_value = MutationDispatchResult(
            rejected,
            {
                "error": {
                    "code": "column_mapping_plan_confirm_rejected",
                    "message": "required destination field is unmapped",
                }
            },
        )

        page = self.client.get(self._review_url())
        token = page.context["form_token"]
        response = self.client.post(
            self._review_url(),
            {"form_token": token, "action": "bulk_ignore_all"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required destination field is unmapped")
        self.assertIn("/column-mapping/", response.request["PATH_INFO"])
        body = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(body["intent"], "confirm")
        draft = self._map_draft()
        self.assertNotEqual(draft.get("status"), "confirmed")
        self.assertFalse(draft.get("confirmed_digests"))

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_save_draft_still_never_confirms(self, mock_client_cls, mock_create):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan()
        updated = dict(plan)
        updated["plan_content_digest"] = "e" * 64
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        mock_create.return_value = _completed_mutation(updated)
        api.dispatch.return_value = MutationDispatchResult(
            mock_create.return_value, updated
        )

        page = self.client.get(self._review_url())
        token = page.context["form_token"]
        response = self.client.post(
            self._review_url(),
            {
                "form_token": token,
                "action": "save_draft",
                "choice_2": SYSTEM_IGNORE,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft mapping saved")
        self.assertIn("/column-mapping/", response.request["PATH_INFO"])
        body = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(body["intent"], "save_draft")
        self.assertNotEqual(self._map_draft().get("status"), "confirmed")
