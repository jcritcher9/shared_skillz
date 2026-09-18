"""RSF-1: Django surfaces below-threshold groups while auto-merge is pending.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_review_surfacing_and_field_fill_efficiency.md
Phase RSF-1.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer.api_client import ApiRejectedError, EasyImportsApiClient
from importer.journey_views import (
    _analysis_complete_for_auto_disposition,
    _auto_queued_review_copy,
)
from importer.models import ImportSession


class Rsf1CopyHelperTests(SimpleTestCase):
    def test_auto_queued_copy_is_count_free_without_withheld_count(self):
        self.assertEqual(
            _auto_queued_review_copy(),
            "No groups need your review yet — high-confidence groups are queued "
            "for automatic approval.",
        )
        self.assertEqual(
            _auto_queued_review_copy(None),
            "No groups need your review yet — high-confidence groups are queued "
            "for automatic approval.",
        )

    def test_auto_queued_copy_names_only_an_exact_withheld_count(self):
        self.assertEqual(
            _auto_queued_review_copy(7),
            "No groups need your review yet — 7 high-confidence groups are "
            "queued for automatic approval.",
        )
        self.assertEqual(
            _auto_queued_review_copy(1),
            "No groups need your review yet — 1 high-confidence group is "
            "queued for automatic approval.",
        )

    def test_analysis_complete_uses_review_ready_not_handoff(self):
        self.assertTrue(
            _analysis_complete_for_auto_disposition(
                {
                    "status": "running",
                    "stage": "building_review_materials",
                    "review_handoff": {"handoff_id": "h1"},
                    "duplicate_analysis_progress": {
                        "review_ready": True,
                        "review_window_ready": True,
                    },
                }
            )
        )
        self.assertFalse(
            _analysis_complete_for_auto_disposition(
                {
                    "status": "running",
                    "stage": "building_review_materials",
                    "review_handoff": {"handoff_id": "h1"},
                    "duplicate_analysis_progress": {
                        "review_ready": False,
                        "review_window_ready": True,
                    },
                }
            )
        )


class Rsf1ReviewWaitTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-rsf1-source",
                "review_run_id": "run-rsf1-review",
                "redesign_phase": "4a",
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
                "auto_merge_min_confidence": 90,
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        django_session = self.client.session
        django_session["easyimports_owner_id"] = str(self.owner)
        django_session.save()

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    def _review_url(self):
        return reverse(
            "importer:crm_duplicate_journey_review",
            kwargs={"session_id": self.session.id},
        )

    def _progress_url(self):
        return reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": self.session.id},
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_auto_queued_copy_is_distinct_from_frontier_wait(self):
        not_ready = ApiRejectedError(
            "duplicate_review_window_not_ready",
            "The next review window is not ready yet.",
            http_status=409,
        )
        source_projection = {
            "status": "running",
            "stage": "building_review_materials",
            "duplicate_analysis_progress": {
                "stage": "building_review_materials",
                "completed_count": 5,
                "total_count": 12,
                "count_unit": "groups",
                "review_ready": False,
                "review_window_ready": True,
                "review_groups_ready": 5,
                "review_groups_total": 12,
            },
        }
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=not_ready,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=source_projection,
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(
            "No groups need your review yet — high-confidence groups are queued "
            "for automatic approval.",
            body,
        )
        self.assertNotIn("5 high-confidence", body)
        self.assertNotIn("Preparing the next groups to review", body)
        self.assertIn("var frontierWait = true;", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_complete_auto_queued_redirects_to_progress(self):
        not_ready = ApiRejectedError(
            "duplicate_review_window_not_ready",
            "The next review window is not ready yet.",
            http_status=409,
        )
        source_projection = {
            "status": "succeeded",
            "stage": "complete",
            "duplicate_analysis_progress": {
                "stage": "complete",
                "completed_count": 12,
                "total_count": 12,
                "count_unit": "groups",
                "review_ready": True,
                "review_window_ready": True,
                "review_groups_ready": 12,
                "review_groups_total": 12,
            },
        }
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=not_ready,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=source_projection,
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(self._progress_url(), response["Location"])
        self.assertIn("auto_queued=1", response["Location"])
