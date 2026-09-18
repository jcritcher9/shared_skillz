"""EFG-0A: review and auto-merge wait pages do not poll at 2.5 s.

Network-free. Django does not import mappings_2. GET never writes.
Wait copy stays honest from already-public progress fields.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_duplicate_exact_first_grouping_redesign.md
Phase 0A.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer.api_client import ApiRejectedError, EasyImportsApiClient
from importer.journey_views import (
    _auto_queued_review_copy,
    crm_duplicate_journey_review,
)
from importer.models import ImportSession


JOURNEY_REVIEW_TEMPLATE = (
    Path(__file__).resolve().parent
    / "templates"
    / "importer"
    / "crm_duplicate_journey_review.html"
)
JOURNEY_PROGRESS_TEMPLATE = (
    Path(__file__).resolve().parent
    / "templates"
    / "importer"
    / "crm_duplicate_journey_progress.html"
)
WORKFLOW_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "importer" / "workflow.html"
)

STILL_WORKING_COPY = (
    "Still working. Refresh this page to check again. GET does not start more work."
)


def _not_ready() -> ApiRejectedError:
    return ApiRejectedError(
        "duplicate_review_window_not_ready",
        "The next review window is not ready yet.",
        http_status=409,
    )


def _member(record_id: str, *, recommended: bool = False) -> dict:
    return {
        "record_id": record_id,
        "display_fields": {"Name": f"Name {record_id}", "Domain": f"{record_id}.example"},
        "recommended": recommended,
        "selected": recommended,
        "survivor_eligible": True,
        "ranking_evidence": {"score": 10 if recommended else 5},
    }


def _ready_window() -> dict:
    return {
        "review_contract": "easyimports.crm.duplicate_review_window.v1",
        "window_id": "win-efg0a",
        "window_digest": "sha256:window-efg0a",
        "expected_revision": 3,
        "group_start": 1,
        "group_end": 1,
        "page_size": 5,
        "total_group_count": 1,
        "decided_group_count": 0,
        "remaining_group_count": 1,
        "groups": [
            {
                "group_id": "g0",
                "group_revision": "rev-g0",
                "entity_family": "company",
                "group_status": "ready",
                "review_lane": "standard",
                "confidence_band": "high",
                "confidence_score": 92,
                "advanced_review_required": False,
                "execution_blockers": [],
                "allowed_actions": [
                    "approve",
                    "override_survivor",
                    "decline",
                    "quarantine",
                ],
                "recommended_survivor_id": "L0",
                "selected_survivor_id": "L0",
                "members": [_member("L0", recommended=True), _member("R0")],
                "conflicts": [],
                "evidence": [{"kind": "name_match"}],
            }
        ],
        "outcome": "next_window",
    }


def _incomplete_progress() -> dict:
    return {
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


def _complete_progress() -> dict:
    return {
        "run_id": "run-efg0a-source",
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "succeeded",
        "stage": "complete",
        "revision": 4,
        "summary": {"duplicate_group_count": 12, "analyzed_record_count": 24},
        "decision": None,
        "review_handoff": {
            "handoff_id": "handoff-efg0a",
            "entity": "account",
            "group_count": 12,
        },
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


class Efg0aTemplateContractTests(SimpleTestCase):
    def test_review_wait_has_no_tight_reload(self):
        source = JOURNEY_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn("2500", source)
        self.assertNotIn("window.location.reload();", source)
        self.assertNotIn("This page refreshes automatically.", source)
        self.assertIn("data-review-wait-refresh", source)
        self.assertIn(STILL_WORKING_COPY, source)
        self.assertIn('name="action_{{ card.group_id }}"', source)
        self.assertIn('value="decline"', source)
        self.assertIn('value="quarantine"', source)
        self.assertIn('name="survivor_{{ card.group_id }}"', source)

    def test_progress_auto_disposition_wait_does_not_emit_tight_reload(self):
        source = JOURNEY_PROGRESS_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("{% if not needs_auto_disposition %}", source)
        auto_wait_index = source.index("{% if not needs_auto_disposition %}")
        self.assertGreater(source.index("2500", auto_wait_index), auto_wait_index)
        prefix = source[:auto_wait_index]
        self.assertNotIn("2500", prefix)
        self.assertIn("data-auto-disposition-wait", source)

    def test_workflow_confirm_winner_skip_quarantine_still_present(self):
        source = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Confirm winner and continue", source)
        self.assertIn('name="duplicate_choice"', source)
        self.assertIn("2500", source)


class Efg0aReviewWaitTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-efg0a-source",
                "review_run_id": "run-efg0a-review",
                "redesign_phase": "4a",
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()

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

    def _enable_auto_merge(self):
        options = dict(self.session.options or {})
        options["auto_merge_min_confidence"] = 90
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_frontier_wait_does_not_poll_and_keeps_bounded_copy(self):
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=_not_ready(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=_incomplete_progress(),
                    ):
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=AssertionError("GET must not dispatch"),
                        ):
                            response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get("Location"))
        body = response.content.decode("utf-8")
        self.assertIn("Preparing the next groups to review — 5 of 12 ready", body)
        self.assertIn(STILL_WORKING_COPY, body)
        self.assertIn('data-frontier-wait', body)
        self.assertIn('data-review-wait-refresh', body)
        self.assertIn("Refresh status", body)
        self.assertIn(self._review_url(), body)
        self.assertNotIn("This page refreshes automatically.", body)
        self.assertNotIn("window.location.reload();", body)
        self.assertNotIn("2500", body)
        self.assertNotIn('class="review-group-card"', body)
        self.assertNotIn("import mappings_2", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_auto_queued_review_wait_does_not_poll(self):
        self._enable_auto_merge()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=_not_ready(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=_incomplete_progress(),
                    ):
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=AssertionError("GET must not dispatch"),
                        ):
                            response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(_auto_queued_review_copy(), body)
        self.assertIn(STILL_WORKING_COPY, body)
        self.assertIn("data-auto-queued-wait", body)
        self.assertNotIn("Preparing the next groups to review", body)
        self.assertNotIn("5 high-confidence", body)
        self.assertNotIn("window.location.reload();", body)
        self.assertNotIn("2500", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_auto_queued_progress_wait_does_not_poll(self):
        self._enable_auto_merge()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "workflow",
                    return_value=_complete_progress(),
                ):
                    with patch(
                        "importer.journey_views._dispatch_json_mutation",
                        side_effect=AssertionError("GET must not dispatch"),
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection",
                            return_value=None,
                        ):
                            response = self.client.get(
                                self._progress_url() + "?auto_queued=1"
                            )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(_auto_queued_review_copy(), body)
        self.assertIn("data-auto-disposition-wait", body)
        self.assertNotIn("window.location.reload();", body)
        self.assertNotIn("2500", body)
        self.assertIn("Continue to review", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_ready_review_still_renders_skip_quarantine_and_survivor(self):
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_ready_window(),
                ):
                    with patch(
                        "importer.journey_views._dispatch_json_mutation",
                        side_effect=AssertionError("GET must not dispatch"),
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn('class="review-group-card"', body)
        self.assertIn('name="action_g0"', body)
        self.assertIn('value="decline"', body)
        self.assertIn("Do not merge this group", body)
        self.assertIn('value="quarantine"', body)
        self.assertIn("Quarantine for manual resolution", body)
        self.assertIn('name="survivor_g0"', body)
        self.assertIn("Merge into selected survivor", body)
        self.assertNotIn("var frontierWait = true;", body)
        self.assertNotIn("2500", body)

    def test_review_view_does_not_import_mappings_2(self):
        source = inspect.getsource(crm_duplicate_journey_review)
        self.assertNotIn("mappings_2", source)
        module = Path(inspect.getsourcefile(crm_duplicate_journey_review) or "")
        self.assertNotIn("import mappings_2", module.read_text(encoding="utf-8"))
