"""MAP-R4 Django column-mapping table UI + atomic multi-row review."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from .api_client import ApiUnavailableError, MutationDispatchResult
from .column_mapping_views import (
    SYSTEM_IGNORE,
    append_ignore_updates_for_ordinals,
    bound_sample_cells,
    build_review_table_rows,
    choices_public_json,
    maps_to_display_label,
    operator_status_label,
    parse_row_updates_from_post,
    remaining_unresolved_after_updates,
    review_error_anchor,
    review_filter_counts,
    unresolved_plan_ordinals,
)
from .models import ApiMutation, ImportSession
from .workflow_state import OWNER_SESSION_KEY


def _draft_plan(**overrides):
    body = {
        "plan_id": "cmp_mapr4_plan01",
        "schema_version": "column_mapping_plan.v1",
        "status": "draft",
        "owner_binding": "owner",
        "destination_mode": "catalog",
        "destination": {
            "destination_mode": "catalog",
            "catalog_id": "easyimports.account_fields.v1",
        },
        "source_schema": [
            {
                "source_ordinal": 0,
                "source_header": "account_name",
                "sample_values": ["3R8", "5Dm Group", "5Skye"],
            },
            {
                "source_ordinal": 1,
                "source_header": "website",
                "sample_values": ["a.example", "b.example"],
            },
            {
                "source_ordinal": 2,
                "source_header": "temporary_notes",
                "sample_values": [],
            },
        ],
        "rows": [
            {
                "source_ordinal": 0,
                "source_header": "account_name",
                "mapping_choice_id": "catalog:account:name",
                "disposition": "mapped",
                "choice_origin": "auto_detected",
                "display_target_label": "Account name",
            },
            {
                "source_ordinal": 1,
                "source_header": "website",
                "mapping_choice_id": "catalog:account:website",
                "disposition": "mapped",
                "choice_origin": "auto_detected",
                "display_target_label": "Website",
            },
            {
                "source_ordinal": 2,
                "source_header": "temporary_notes",
                "mapping_choice_id": None,
                "disposition": "unresolved",
                "choice_origin": None,
                "display_target_label": None,
                "suggested_mapping_choice_id": None,
                "suggested_display_label": None,
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
                "mapping_choice_id": SYSTEM_IGNORE,
                "label": "Ignore",
                "choice_kind": "ignore",
                "object_scope": None,
            },
            {
                "mapping_choice_id": "catalog:account:name",
                "label": "Account name",
                "choice_kind": "scalar",
                "object_scope": "Account",
            },
            {
                "mapping_choice_id": "catalog:account:website",
                "label": "Website",
                "choice_kind": "scalar",
                "object_scope": "Account",
                "search_aliases": [
                    "website",
                    "url",
                    "homepage",
                    "company website",
                ],
            },
        ]
    }


def _completed_mutation(plan: dict) -> ApiMutation:
    mutation = MagicMock(spec=ApiMutation)
    mutation.state = ApiMutation.State.COMPLETED
    mutation.error_code = ""
    mutation.error_message = ""
    mutation.response_json = plan
    mutation.refresh_from_db = MagicMock()
    return mutation


class MapR4PresentationHelpersTests(TestCase):
    def test_operator_copy_rules(self):
        self.assertEqual(
            operator_status_label("mapped", "auto_detected"),
            "Matched automatically",
        )
        self.assertEqual(
            operator_status_label("mapped", "operator_selected"),
            "Changed by you",
        )
        self.assertEqual(operator_status_label("ignored"), "Ignore column")
        self.assertEqual(operator_status_label("unresolved"), "Needs review")

    def test_maps_to_and_samples(self):
        self.assertEqual(
            maps_to_display_label(
                disposition="mapped",
                display_target_label="Account name",
                mapping_choice_id="x",
                object_scope="Account",
                multi_object=True,
            ),
            "Account · Account name",
        )
        self.assertEqual(
            maps_to_display_label(
                disposition="unresolved",
                display_target_label=None,
                mapping_choice_id=None,
                object_scope="Account",
                suggested_display_label="Website",
                multi_object=False,
            ),
            "Suggested: Website",
        )
        self.assertEqual(
            bound_sample_cells(["a", "b"]),
            ["a", "b", "—"],
        )
        self.assertEqual(bound_sample_cells(None), ["—", "—", "—"])

    def test_table_rows_stable_order_and_filters(self):
        plan = _draft_plan()
        choices = _choices()["choices"]
        rows = build_review_table_rows(plan, choices)
        self.assertEqual([r["source_ordinal"] for r in rows], [0, 1, 2])
        self.assertEqual(rows[0]["sample_cells"], ["3R8", "5Dm Group", "5Skye"])
        self.assertEqual(rows[2]["filter_key"], "needs_review")
        self.assertEqual(rows[2]["action_label"], "Change")
        counts = review_filter_counts(rows)
        self.assertEqual(counts["all"], 3)
        self.assertEqual(counts["mapped"], 2)
        self.assertEqual(counts["needs_review"], 1)

    def test_parse_row_updates_only_changes(self):
        plan_rows = _draft_plan()["rows"]

        class _Post(dict):
            def getlist(self, key):
                return list(self.get(key + "__list") or [])

        post = _Post(
            {
                "choice_0": "catalog:account:name",  # unchanged
                "choice_1": "catalog:account:website",
                "choice_2": SYSTEM_IGNORE,
            }
        )
        updates = parse_row_updates_from_post(post, plan_rows)
        self.assertEqual(
            updates,
            [{"source_ordinal": 2, "mapping_choice_id": SYSTEM_IGNORE}],
        )

    def test_choices_public_json_includes_search_aliases(self):
        payload = choices_public_json(_choices()["choices"])
        by_id = {c["mapping_choice_id"]: c for c in payload}
        self.assertIn("homepage", by_id["catalog:account:website"]["search_aliases"])
        self.assertNotIn("choice_kind", by_id["catalog:account:website"])

    def test_review_error_anchor_prefers_first_row_update(self):
        self.assertEqual(
            review_error_anchor(
                action="confirm_mapping",
                row_updates=[
                    {"source_ordinal": 2, "mapping_choice_id": SYSTEM_IGNORE},
                    {"source_ordinal": 0, "mapping_choice_id": "x"},
                ],
            ),
            "#mapping-row-0",
        )
        self.assertEqual(
            review_error_anchor(action="save_draft", row_updates=[]),
            "#mapping-review-form",
        )
        self.assertEqual(
            review_error_anchor(action="bulk_ignore_all", row_updates=[]),
            "#mapping-review-form",
        )

    def test_ignore_all_helpers_cover_leftovers_only(self):
        plan_rows = _draft_plan()["rows"]
        self.assertEqual(unresolved_plan_ordinals(plan_rows), [2])
        updates = append_ignore_updates_for_ordinals([], [2])
        self.assertEqual(
            updates,
            [{"source_ordinal": 2, "mapping_choice_id": SYSTEM_IGNORE}],
        )
        self.assertEqual(remaining_unresolved_after_updates(plan_rows, updates), [])
        self.assertEqual(
            remaining_unresolved_after_updates(plan_rows, []),
            [2],
        )
        # Existing mapped/ignore updates are not overwritten.
        already = [{"source_ordinal": 2, "mapping_choice_id": "catalog:account:name"}]
        append_ignore_updates_for_ordinals(already, [2])
        self.assertEqual(
            already,
            [{"source_ordinal": 2, "mapping_choice_id": "catalog:account:name"}],
        )


class MapR4ReviewUiTests(TestCase):
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

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_review_table_copy_and_samples(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.get_column_mapping_plan.return_value = _draft_plan()
        api.list_column_mapping_choices.return_value = _choices()

        url = reverse(
            "importer:column_mapping_review",
            kwargs={
                "session_id": self.session.id,
                "plan_id": "cmp_mapr4_plan01",
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")

        # Semantic table
        self.assertContains(response, "Action")
        self.assertContains(response, "Maps to")
        self.assertContains(response, "Source column")
        self.assertContains(response, "Example 1")
        self.assertContains(response, "account_name")
        self.assertContains(response, "3R8")
        self.assertContains(response, "5Dm Group")
        self.assertContains(response, "Needs review")
        self.assertContains(response, "Matched automatically")
        self.assertContains(response, "Ignore column")
        self.assertContains(response, "Save draft")
        self.assertContains(response, "Confirm mapping")
        self.assertContains(response, 'value="confirm_mapping"')
        self.assertContains(response, 'name="choice_2"')
        self.assertContains(response, "mapping-field-picker")
        self.assertContains(response, "Technical details")

        # Ordinary UI must not leak internal jargon / raw plan id outside details.
        # Plan id appears only inside Technical details (closed by default).
        self.assertNotIn("scalar", body.split("Technical details")[0])
        self.assertNotIn("composite", body.split("Technical details")[0])
        self.assertNotIn("Column 0", body)
        self.assertNotIn("Column 2", body)
        # Before technical details, no bare plan id code block in page head.
        ordinary = body.split("Technical details")[0]
        self.assertNotIn("cmp_mapr4_plan01", ordinary)

        # Filters with counts
        self.assertEqual(response.context["filter_counts"]["all"], 3)
        self.assertEqual(response.context["filter_counts"]["needs_review"], 1)
        self.assertFalse(response.context["can_confirm"])

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_zero_editor_confirm_when_fully_mapped(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan(
            rows=[
                {
                    "source_ordinal": 0,
                    "source_header": "account_name",
                    "mapping_choice_id": "catalog:account:name",
                    "disposition": "mapped",
                    "choice_origin": "auto_detected",
                    "display_target_label": "Account name",
                },
                {
                    "source_ordinal": 1,
                    "source_header": "website",
                    "mapping_choice_id": "catalog:account:website",
                    "disposition": "mapped",
                    "choice_origin": "auto_detected",
                    "display_target_label": "Website",
                },
                {
                    "source_ordinal": 2,
                    "source_header": "temporary_notes",
                    "mapping_choice_id": SYSTEM_IGNORE,
                    "disposition": "ignored",
                    "choice_origin": "operator_selected",
                    "display_target_label": "Ignore",
                },
            ]
        )
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        url = reverse(
            "importer:column_mapping_review",
            kwargs={
                "session_id": self.session.id,
                "plan_id": "cmp_mapr4_plan01",
            },
        )
        response = self.client.get(url)
        self.assertTrue(response.context["can_confirm"])
        self.assertNotContains(response, 'disabled aria-disabled="true"')

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_save_draft_uses_atomic_review(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan()
        updated = _draft_plan(
            plan_content_digest="e" * 64,
            rows=[
                plan["rows"][0],
                plan["rows"][1],
                {
                    "source_ordinal": 2,
                    "source_header": "temporary_notes",
                    "mapping_choice_id": SYSTEM_IGNORE,
                    "disposition": "ignored",
                    "choice_origin": "operator_selected",
                    "display_target_label": "Ignore",
                },
            ],
        )
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        mutation = _completed_mutation(updated)
        mock_create.return_value = mutation
        api.dispatch.return_value = MutationDispatchResult(mutation, updated)

        url = reverse(
            "importer:column_mapping_review",
            kwargs={
                "session_id": self.session.id,
                "plan_id": "cmp_mapr4_plan01",
            },
        )
        page = self.client.get(url)
        token = page.context["form_token"]
        response = self.client.post(
            url,
            {
                "form_token": token,
                "action": "save_draft",
                "choice_0": "catalog:account:name",
                "choice_1": "catalog:account:website",
                "choice_2": SYSTEM_IGNORE,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft mapping saved")
        self.assertEqual(
            mock_create.call_args.kwargs["mutation_kind"],
            "column_mapping_plan_atomic_review",
        )
        body = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(body["intent"], "save_draft")
        self.assertEqual(
            body["rows"],
            [{"source_ordinal": 2, "mapping_choice_id": SYSTEM_IGNORE}],
        )
        self.assertIn("/atomic-review", mock_create.call_args.kwargs["route"])

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_confirm_mapping_atomic_persists_bind(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan(
            rows=[
                {
                    "source_ordinal": 0,
                    "source_header": "account_name",
                    "mapping_choice_id": "catalog:account:name",
                    "disposition": "mapped",
                    "choice_origin": "auto_detected",
                    "display_target_label": "Account name",
                },
                {
                    "source_ordinal": 1,
                    "source_header": "website",
                    "mapping_choice_id": "catalog:account:website",
                    "disposition": "mapped",
                    "choice_origin": "auto_detected",
                    "display_target_label": "Website",
                },
                {
                    "source_ordinal": 2,
                    "source_header": "temporary_notes",
                    "mapping_choice_id": SYSTEM_IGNORE,
                    "disposition": "ignored",
                    "choice_origin": "operator_selected",
                    "display_target_label": "Ignore",
                },
            ]
        )
        confirmed = dict(plan)
        confirmed["status"] = "confirmed"
        confirmed["confirmed_digests"] = {
            "plan_content_digest": "d" * 64,
            "source_schema_digest": "a" * 64,
            "destination_digest": "b" * 64,
            "target_contract_digest": "c" * 64,
        }
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        mutation = _completed_mutation(confirmed)
        mock_create.return_value = mutation
        api.dispatch.return_value = MutationDispatchResult(mutation, confirmed)

        url = reverse(
            "importer:column_mapping_review",
            kwargs={
                "session_id": self.session.id,
                "plan_id": "cmp_mapr4_plan01",
            },
        )
        page = self.client.get(url)
        token = page.context["form_token"]
        self.client.post(
            url,
            {
                "form_token": token,
                "action": "confirm_mapping",
                "choice_0": "catalog:account:name",
                "choice_1": "catalog:account:website",
                "choice_2": SYSTEM_IGNORE,
            },
        )
        self.assertEqual(
            mock_create.call_args.kwargs["mutation_kind"],
            "column_mapping_plan_atomic_review",
        )
        self.assertEqual(
            mock_create.call_args.kwargs["request_json"]["intent"],
            "confirm",
        )
        self.session.refresh_from_db()
        draft = (self.session.options or {}).get("column_mapping_map2") or {}
        self.assertEqual(draft.get("status"), "confirmed")
        self.assertEqual(draft.get("plan_id"), "cmp_mapr4_plan01")
        self.assertIn("plan_content_digest", draft.get("confirmed_digests") or {})

    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_choices_load_failure_blocks_mapping_actions(self, mock_client_cls):
        api = MagicMock()
        mock_client_cls.return_value = api
        api.get_column_mapping_plan.return_value = _draft_plan()
        api.list_column_mapping_choices.side_effect = ApiUnavailableError(
            "API down"
        )
        url = reverse(
            "importer:column_mapping_review",
            kwargs={
                "session_id": self.session.id,
                "plan_id": "cmp_mapr4_plan01",
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["mapping_actions_blocked"])
        self.assertIsNotNone(response.context["choices_load_error"])
        self.assertContains(response, "mapping-choices-error")
        self.assertContains(response, "Destination fields unavailable")
        self.assertFalse(response.context["can_confirm"])
        # Enhanced picker still present in HTML but actions disabled.
        self.assertContains(response, 'value="save_draft"')
        self.assertContains(response, "disabled")

    @patch("importer.column_mapping_views.create_or_reuse_mutation")
    @patch("importer.column_mapping_views.EasyImportsApiClient")
    def test_atomic_reject_redirects_to_row_anchor(
        self, mock_client_cls, mock_create
    ):
        api = MagicMock()
        mock_client_cls.return_value = api
        plan = _draft_plan()
        api.get_column_mapping_plan.return_value = plan
        api.list_column_mapping_choices.return_value = _choices()
        rejected = MagicMock(spec=ApiMutation)
        rejected.state = ApiMutation.State.REJECTED
        rejected.error_code = "column_mapping_plan_confirm_rejected"
        rejected.error_message = "collision blocks confirm"
        rejected.response_json = {
            "error": {
                "code": "column_mapping_plan_confirm_rejected",
                "message": "collision blocks confirm",
            }
        }
        rejected.refresh_from_db = MagicMock()
        mock_create.return_value = rejected
        api.dispatch.return_value = MutationDispatchResult(
            rejected,
            {
                "error": {
                    "code": "column_mapping_plan_confirm_rejected",
                    "message": "collision blocks confirm",
                }
            },
        )
        url = reverse(
            "importer:column_mapping_review",
            kwargs={
                "session_id": self.session.id,
                "plan_id": "cmp_mapr4_plan01",
            },
        )
        page = self.client.get(url)
        token = page.context["form_token"]
        response = self.client.post(
            url,
            {
                "form_token": token,
                "action": "confirm_mapping",
                "choice_2": SYSTEM_IGNORE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("#mapping-row-2", response["Location"])

    def test_js_asset_implements_focus_and_alias_search_contracts(self):
        """Static contract: enhanced script keeps focus + alias search helpers.

        Interactive Chromium proof lives in
        ``test_column_mapping_map_r4_a11y.py`` (Playwright).
        """
        from pathlib import Path

        js_path = (
            Path(__file__).resolve().parent
            / "static"
            / "importer"
            / "js"
            / "column_mapping_review.js"
        )
        text = js_path.read_text(encoding="utf-8")
        self.assertIn("ensureRowFocusable", text)
        self.assertIn("restoreFocusToRowAction", text)
        self.assertIn("search_aliases", text)
        self.assertIn("choiceSearchHaystack", text)
        self.assertIn('setAttribute("tabindex", "-1")', text)
        self.assertIn('ev.key === "ArrowDown"', text)
        self.assertIn('ev.key === "ArrowUp"', text)
