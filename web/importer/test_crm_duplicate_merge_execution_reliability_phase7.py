"""Phase 7: hide declined groups from summary/merge presentation only.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_duplicate_merge_execution_reliability_and_review_ux.md
Phase 7.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer.api_client import EasyImportsApiClient
from importer.crm_duplicate_merge_copy import EDIT_DISPOSITIONS_LABEL
from importer.journey_views import _visible_disposition_rows
from importer.models import ImportSession
from importer.test_crm_duplicate_journey_phase5a import (
    _reviewed_disposition_window,
    _reviewed_result,
    _reviewed_result_summary,
)
from importer.test_crm_duplicate_merge_execution_reliability_phase6 import (
    _complete_review_window,
    _review_session,
)


class VisibleDispositionRowsTests(SimpleTestCase):
    def test_drops_declined_and_keeps_other_rows(self) -> None:
        rows = _visible_disposition_rows(
            [
                {"group_id": "g1", "disposition": "approved"},
                {"group_id": "g2", "disposition": "declined"},
                {"group_id": "g3", "disposition": "quarantined"},
            ]
        )
        self.assertEqual([item["group_id"] for item in rows], ["g1", "g3"])

    def test_does_not_mutate_source(self) -> None:
        source = [{"group_id": "g2", "disposition": "declined"}]
        rows = _visible_disposition_rows(source)
        self.assertEqual(rows, [])
        self.assertEqual(source[0]["disposition"], "declined")


class CrmDuplicateHideDeclinedTemplateTests(SimpleTestCase):
    def test_summary_omits_declined_tile_and_keeps_counts(self) -> None:
        html = render_to_string(
            "importer/crm_duplicate_journey_review_summary.html",
            {
                "reviewed_result": _reviewed_result(),
                "visible_dispositions": _visible_disposition_rows(
                    _reviewed_result()["dispositions"]
                ),
                "auto_approved_group_count": 0,
                "operator_approved_group_count": 1,
                "edit_dispositions": False,
                "edit_dispositions_url": "/review/?edit=1",
                "edit_dispositions_label": EDIT_DISPOSITIONS_LABEL,
                "form_errors": [],
                "merge_url": "/merge/",
                "progress_url": "/progress/",
                "workflow_url": "/workflow/",
                "start_url": "/",
                "run_id": "run-review-p7",
                "terminal_message": "All duplicate groups have been reviewed.",
            },
        )
        self.assertIn('data-review-summary-counts="1"', html)
        self.assertIn("Approved for merge", html)
        self.assertNotIn("Declined (no merge)", html)
        self.assertNotIn('class="group-card"', html)

    def test_edit_mode_hides_declined_cards_but_keeps_hidden_fields(self) -> None:
        html = render_to_string(
            "importer/crm_duplicate_journey_review_summary.html",
            {
                "reviewed_result": _reviewed_result(),
                "disposition_window": _reviewed_disposition_window(),
                "visible_dispositions": _visible_disposition_rows(
                    _reviewed_disposition_window()["dispositions"]
                ),
                "edit_dispositions": True,
                "edit_dispositions_url": "/review/?edit=1",
                "edit_dispositions_label": EDIT_DISPOSITIONS_LABEL,
                "form_token": "token-p7",
                "form_errors": [],
                "window_cursor": "",
                "expanded_group_id": "",
                "merge_url": "/merge/",
                "progress_url": "/progress/",
                "workflow_url": "/workflow/",
                "start_url": "/",
                "run_id": "run-review-p7",
                "terminal_message": "All duplicate groups have been reviewed.",
            },
        )
        self.assertIn("Group g1", html)
        self.assertNotIn("Group g2", html)
        self.assertIn('name="action_g2"', html)
        self.assertIn('name="group_order"', html)
        self.assertIn("g1,g2", html)

    def test_merge_omits_declined_tile(self) -> None:
        html = render_to_string(
            "importer/crm_duplicate_journey_merge.html",
            {
                "reviewed_result": _reviewed_result(),
                "visible_dispositions": _visible_disposition_rows(
                    _reviewed_result()["dispositions"]
                ),
                "auto_approved_group_count": 0,
                "operator_approved_group_count": 1,
                "form_errors": [],
                "form_token": "token",
                "supported_modes": [],
                "plan_frozen": False,
                "already_authorized": False,
                "review_url": "/review/",
                "workflow_url": "/workflow/",
                "start_url": "/",
                "merge_url": "/merge/",
                "field_fill_empty_sentence": "Empty fields stay empty.",
            },
        )
        self.assertIn("Approved for merge", html)
        self.assertNotIn("Declined (no merge)", html)
        self.assertNotIn("Group g2", html)


class CrmDuplicateHideDeclinedJourneyTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(self.owner)
        session.save()

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_summary_get_hides_declined_tile_and_keeps_digest(self) -> None:
        draft = _review_session(owner=self.owner)
        summary = _reviewed_result_summary()
        self.assertEqual(summary["declined_group_count"], 1)
        digest = summary["decision_set_content_digest"]
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_complete_review_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "duplicate_reviewed_result_summary",
                        return_value=summary,
                    ) as summary_get:
                        with patch.object(
                            EasyImportsApiClient,
                            "duplicate_reviewed_result",
                        ) as full_get:
                            with patch(
                                "importer.journey_views._load_source_oversized_quarantine_components",
                                return_value=((), None),
                            ):
                                response = self.client.get(
                                    reverse(
                                        "importer:crm_duplicate_journey_review",
                                        kwargs={"session_id": draft.id},
                                    )
                                )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("Declined (no merge)", body)
        self.assertIn(digest, body)
        self.assertEqual(summary_get.call_args.args[0], "run-review-p6")
        self.assertEqual(summary["declined_group_count"], 1)
        self.assertEqual(summary["decision_set_content_digest"], digest)
        full_get.assert_not_called()

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_get_hides_declined_tile_and_keeps_digest(self) -> None:
        draft = _review_session(owner=self.owner)
        summary = _reviewed_result_summary()
        digest = summary["decision_set_content_digest"]
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result_summary",
                    return_value=summary,
                ):
                    with patch(
                        "importer.journey_views._load_source_oversized_quarantine_components",
                        return_value=((), None),
                    ):
                        response = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": draft.id},
                            )
                        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("Declined (no merge)", body)
        self.assertIn(digest, body)
        self.assertEqual(summary["declined_group_count"], 1)
        self.assertEqual(summary["decision_set_content_digest"], digest)
