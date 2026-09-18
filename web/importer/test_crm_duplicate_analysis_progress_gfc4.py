"""GFC-4: recommendations vs approval-bundle progress copy stay distinct.

Django copy only. Stage, count_unit, and completed/total semantics are
unchanged. `complete` keeps the existing terminal sentence.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_group_pipeline_efficiency.md
Phase GFC-4.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from importer.journey_views import _analysis_progress_context


def _progress(stage: str, completed: int, total: int, *, unit: str = "groups"):
    return _analysis_progress_context(
        {
            "duplicate_analysis_progress": {
                "stage": stage,
                "completed_count": completed,
                "total_count": total,
                "count_unit": unit,
                "review_ready": stage == "complete",
            }
        }
    )


class Gfc4ProgressCopyTests(SimpleTestCase):
    def test_recommendation_and_approval_bundle_copy_are_distinct(self):
        recommendations = _progress("building_recommendations", 2915, 2915)
        approvals = _progress("building_approval_bundle", 0, 2915)

        self.assertEqual(
            recommendations["analysis_progress_copy"],
            "Building merge recommendations — 2,915 of 2,915 groups",
        )
        self.assertEqual(
            approvals["analysis_progress_copy"],
            "Preparing group approvals — 0 of 2,915 groups",
        )
        self.assertNotEqual(
            recommendations["analysis_progress_copy"],
            approvals["analysis_progress_copy"],
        )
        self.assertEqual(recommendations["analysis_progress_stage"], "building_recommendations")
        self.assertEqual(approvals["analysis_progress_stage"], "building_approval_bundle")

    def test_same_counts_still_render_distinct_stage_sentences(self):
        recommendations = _progress("building_recommendations", 100, 2915)
        approvals = _progress("building_approval_bundle", 100, 2915)

        self.assertIn("100 of 2,915", recommendations["analysis_progress_copy"])
        self.assertIn("100 of 2,915", approvals["analysis_progress_copy"])
        self.assertNotEqual(
            recommendations["analysis_progress_copy"],
            approvals["analysis_progress_copy"],
        )
        self.assertTrue(
            recommendations["analysis_progress_copy"].startswith(
                "Building merge recommendations"
            )
        )
        self.assertTrue(
            approvals["analysis_progress_copy"].startswith("Preparing group approvals")
        )

    def test_complete_keeps_existing_terminal_copy(self):
        complete = _progress("complete", 2915, 2915)
        self.assertEqual(
            complete["analysis_progress_copy"],
            "Preparing duplicate groups — 2,915 of 2,915 groups",
        )
        self.assertIs(complete["analysis_progress_review_ready"], True)

    def test_finalizing_and_ranking_copy_unchanged(self):
        finalizing = _progress("finalizing_groups", 0, 1, unit="stage")
        ranking = _progress("ranking_survivors", 1, 1, unit="stage")
        self.assertEqual(finalizing["analysis_progress_copy"], "Finalizing duplicate groups")
        self.assertEqual(ranking["analysis_progress_copy"], "Ranking survivors")
        self.assertNotIn("%", finalizing["analysis_progress_copy"])
        self.assertNotIn("%", ranking["analysis_progress_copy"])
