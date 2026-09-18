"""Phase 6: review summary renders counts, not per-group cards.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_duplicate_merge_execution_reliability_and_review_ux.md
Phase 6.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer.api_client import EasyImportsApiClient
from importer.crm_duplicate_merge_copy import (
    CONTINUE_TO_APPROVE_MERGE_PLAN_LABEL,
    EDIT_DISPOSITIONS_LABEL,
)
from importer.models import ImportSession
from importer.test_crm_duplicate_journey_phase5a import (
    _reviewed_disposition_window,
    _reviewed_result,
    _reviewed_result_summary,
)


def _complete_review_window() -> dict:
    return {
        "review_contract": "easyimports.crm.duplicate_review_complete.v1",
        "outcome": "complete",
        "groups": [],
        "remaining_group_count": 0,
        "terminal_message": "All duplicate groups have been reviewed.",
    }


def _review_session(*, owner) -> ImportSession:
    draft = ImportSession.objects.create(
        owner_id=owner,
        product_key="easyimports.duplicate_resolution",
        status=ImportSession.Status.RUNNING,
        options={
            "run_id": "run-source-p6",
            "review_run_id": "run-review-p6",
            "review_handoff_id": "review_p6",
            "apply_root_form_instance": str(uuid4()),
            "orchestrator_form_instance": str(uuid4()),
            "mutation_journal_id": "",
        },
    )
    draft.options = {
        **dict(draft.options or {}),
        "mutation_journal_id": str(draft.id),
    }
    draft.save(update_fields=["options", "updated_at"])
    return draft


class CrmDuplicateReviewSummaryCountsTemplateTests(SimpleTestCase):
    def test_summary_template_renders_counts_not_per_group_cards(self) -> None:
        html = render_to_string(
            "importer/crm_duplicate_journey_review_summary.html",
            {
                "reviewed_result": _reviewed_result(),
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
                "run_id": "run-review-p6",
                "terminal_message": "All duplicate groups have been reviewed.",
            },
        )
        self.assertIn('data-review-summary-counts="1"', html)
        self.assertIn("Approved for merge", html)
        self.assertIn(EDIT_DISPOSITIONS_LABEL, html)
        self.assertIn('data-edit-dispositions="1"', html)
        self.assertIn(CONTINUE_TO_APPROVE_MERGE_PLAN_LABEL, html)
        self.assertNotIn('class="group-card"', html)
        self.assertNotIn("Edit group dispositions", html)
        self.assertNotIn('name="group_order"', html)
        self.assertNotIn('name="survivor_g1"', html)
        self.assertNotIn("Save disposition changes", html)


class CrmDuplicateReviewSummaryCountsJourneyTests(TestCase):
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
    def test_completed_review_get_renders_counts_and_skips_disposition_window(
        self,
    ) -> None:
        draft = _review_session(owner=self.owner)
        summary = _reviewed_result_summary()
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
                            "duplicate_reviewed_disposition_window",
                        ) as window_get:
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
        self.assertIn('data-review-summary-counts="1"', body)
        self.assertIn("Approved for merge", body)
        self.assertIn(EDIT_DISPOSITIONS_LABEL, body)
        self.assertIn("edit=1", body)
        self.assertNotIn('class="group-card"', body)
        self.assertNotIn("g1", body)
        self.assertNotIn("g2", body)
        summary_get.assert_called_once()
        window_get.assert_not_called()
        full_get.assert_not_called()

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_edit_query_loads_disposition_window_for_group_review(
        self,
    ) -> None:
        draft = _review_session(owner=self.owner)
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
                        return_value=_reviewed_result_summary(),
                    ):
                        with patch.object(
                            EasyImportsApiClient,
                            "duplicate_reviewed_disposition_window",
                            return_value=_reviewed_disposition_window(),
                        ) as window_get:
                            with patch(
                                "importer.journey_views._load_source_oversized_quarantine_components",
                                return_value=((), None),
                            ):
                                with patch(
                                    "importer.journey_views.issue_form_token",
                                    return_value="token-p6",
                                ):
                                    response = self.client.get(
                                        reverse(
                                            "importer:crm_duplicate_journey_review",
                                            kwargs={"session_id": draft.id},
                                        )
                                        + "?edit=1"
                                    )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn('class="group-card"', body)
        self.assertIn("Edit group dispositions", body)
        self.assertIn("g1", body)
        window_get.assert_called_once()
