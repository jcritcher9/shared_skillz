from __future__ import annotations

from uuid import uuid4

from django.test import SimpleTestCase, TestCase

from importer.journey_views import _analysis_progress_context, _read_progress_context
from importer.models import ApiWorkflow, ImportSession
from importer.workflow_state import store_workflow_projection


class AnalysisProgressCopyTests(SimpleTestCase):
    def test_evidence_copy(self):
        ctx = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "building_evidence",
                    "completed_count": 12,
                    "total_count": 40,
                    "count_unit": "candidate_pairs",
                    "review_ready": False,
                }
            }
        )
        self.assertEqual(
            ctx["analysis_progress_copy"],
            "Comparing possible Company matches — 12 of 40 comparisons checked",
        )

    def test_stage_labels_have_no_percentage(self):
        finalizing = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "finalizing_groups",
                    "completed_count": 0,
                    "total_count": 1,
                    "count_unit": "stage",
                    "review_ready": False,
                }
            }
        )
        self.assertEqual(finalizing["analysis_progress_copy"], "Finalizing duplicate groups")
        self.assertNotIn("%", finalizing["analysis_progress_copy"])

    def test_recommendation_copy(self):
        ctx = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "building_recommendations",
                    "completed_count": 20,
                    "total_count": 100,
                    "count_unit": "groups",
                    "review_ready": False,
                }
            }
        )
        self.assertEqual(
            ctx["analysis_progress_copy"],
            "Building merge recommendations — 20 of 100 groups",
        )

    def test_read_context_still_includes_analysis_keys(self):
        ctx = _read_progress_context({}, {"entity_family": "company"})
        self.assertIn("analysis_progress_copy", ctx)
        self.assertIsNone(ctx["analysis_progress_copy"])

    def test_polls_increase_before_review_is_actionable(self):
        first = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "building_evidence",
                    "completed_count": 12,
                    "total_count": 40,
                    "count_unit": "candidate_pairs",
                    "review_ready": False,
                }
            }
        )
        second = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "building_evidence",
                    "completed_count": 24,
                    "total_count": 40,
                    "count_unit": "candidate_pairs",
                    "review_ready": False,
                }
            }
        )
        complete = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "complete",
                    "completed_count": 2,
                    "total_count": 2,
                    "count_unit": "groups",
                    "review_ready": True,
                }
            }
        )
        self.assertIn("12 of 40", first["analysis_progress_copy"])
        self.assertIn("24 of 40", second["analysis_progress_copy"])
        self.assertNotEqual(
            first["analysis_progress_copy"], second["analysis_progress_copy"]
        )
        self.assertIs(first["analysis_progress_review_ready"], False)
        self.assertIs(second["analysis_progress_review_ready"], False)
        self.assertIs(complete["analysis_progress_review_ready"], True)


class PersistedWorkflowProgressPollTests(TestCase):
    def test_reload_from_stored_workflow_shows_increased_progress(self):
        session = ImportSession.objects.create(
            owner_id=uuid4(),
            product_key="easyimports.duplicate_resolution",
            target_provider_id="fake-preview-v1",
        )

        def _projection(completed: int, revision: int) -> dict:
            return {
                "run_id": "ddr2-progress-run",
                "workflow_key": "easyimports.duplicate_resolution",
                "workflow_version": 5,
                "status": "running",
                "stage": "discover_product_duplicate_groups",
                "revision": revision,
                "duplicate_analysis_progress": {
                    "stage": "building_evidence",
                    "completed_count": completed,
                    "total_count": 10,
                    "count_unit": "candidate_pairs",
                    "review_ready": False,
                },
            }

        store_workflow_projection(session, _projection(3, 2))
        first = ApiWorkflow.objects.get(run_id="ddr2-progress-run")
        first_ctx = _read_progress_context(first.projection, {"entity_family": "company"})
        store_workflow_projection(session, _projection(7, 3))
        second = ApiWorkflow.objects.get(run_id="ddr2-progress-run")
        second_ctx = _read_progress_context(
            second.projection, {"entity_family": "company"}
        )
        self.assertIn("3 of 10", first_ctx["analysis_progress_copy"])
        self.assertIn("7 of 10", second_ctx["analysis_progress_copy"])
        self.assertIs(first_ctx["analysis_progress_review_ready"], False)
        self.assertIs(second_ctx["analysis_progress_review_ready"], False)
        self.assertNotEqual(
            first_ctx["analysis_progress_copy"], second_ctx["analysis_progress_copy"]
        )
        self.assertEqual(second.revision, 3)
