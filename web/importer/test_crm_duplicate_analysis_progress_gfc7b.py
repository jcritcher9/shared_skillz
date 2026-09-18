"""GFC-7B: Django renders routing_domain_groups as a distinct stage-only sentence.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_group_pipeline_efficiency.md
Phase GFC-7B.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from importer.journey_views import _analysis_progress_context


class Gfc7bRoutingProgressCopyTests(SimpleTestCase):
    def test_routing_domain_groups_has_distinct_stage_only_copy(self):
        context = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "routing_domain_groups",
                    "completed_count": 0,
                    "total_count": 1,
                    "count_unit": "stage",
                    "review_ready": False,
                }
            }
        )
        self.assertEqual(context["analysis_progress_stage"], "routing_domain_groups")
        self.assertEqual(
            context["analysis_progress_copy"],
            "Routing duplicate groups by domain",
        )
        self.assertNotIn("%", context["analysis_progress_copy"])
        self.assertNotIn("of", context["analysis_progress_copy"])

    def test_unknown_stage_still_falls_through_empty(self):
        context = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "not_a_stage",
                    "completed_count": 0,
                    "total_count": 1,
                    "count_unit": "stage",
                    "review_ready": False,
                }
            }
        )
        self.assertIsNone(context["analysis_progress_copy"])
