"""GFC-3: oversized-only analysis is not the no-groups path."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .api_client import ApiUnavailableError, EasyImportsApiClient
from .journey_views import (
    _OVERSIZED_SOURCE_UNAVAILABLE_COPY,
    _is_review_ready,
    _oversized_quarantine_only,
    _progress_message,
)
from .models import ImportSession
from .test_crm_duplicate_journey_phase5a import (
    _reviewed_disposition_window,
    _reviewed_result,
    _reviewed_result_summary,
)
from .workflow_state import store_workflow_projection
from django.template.loader import render_to_string


def _all_oversized_projection() -> dict:
    return {
        "run_id": "run-gfc3-all-oversized",
        "revision": 3,
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 5,
        "status": "succeeded",
        "stage": "complete",
        "summary": {
            "analyzed_record_count": 4,
            "duplicate_group_count": 0,
            "reviewable_group_count": 0,
            "oversized_quarantine_component_count": 1,
            "oversized_quarantine_record_count": 4,
        },
        "oversized_quarantine_components": [
            {
                "component_id": "comp-oversized",
                "member_ids": ["A", "B", "C", "D"],
                "reason": "component_exceeds_automatic_resolution_size",
                "record_count": 4,
            }
        ],
        "decision": None,
        "review_handoff": None,
        "effect_intent": None,
        "effect_grants": [],
        "target_provider_id": "fake",
        "terminal_evidence": {
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 5,
            "entity": "account",
            "receipts": [],
            "accountability": {"dispositions": []},
            "delivery_manifest": None,
            "oversized_quarantine_components": [
                {
                    "component_id": "comp-oversized",
                    "member_ids": ["A", "B", "C", "D"],
                    "reason": "component_exceeds_automatic_resolution_size",
                    "record_count": 4,
                }
            ],
        },
    }


class CrmDuplicateOversizedQuarantineCopyTests(SimpleTestCase):
    def test_all_oversized_is_not_no_duplicate_groups_copy(self) -> None:
        projection = _all_oversized_projection()
        self.assertTrue(_oversized_quarantine_only(projection))
        self.assertTrue(_is_review_ready(projection))
        message = _progress_message(projection)
        self.assertIn("too large to auto-resolve", message)
        self.assertNotIn("No duplicate groups found", message)

    def test_summary_lists_distinct_components_not_group_dispositions(self) -> None:
        html = render_to_string(
            "importer/crm_duplicate_journey_review_summary.html",
            {
                "reviewed_result": _reviewed_result(),
                "edit_dispositions": False,
                "edit_dispositions_url": "/review/?edit=1",
                "edit_dispositions_label": "Edit dispositions",
                "oversized_quarantine_components": [
                    {
                        "component_id": "comp-oversized",
                        "member_ids": ["A", "B", "C", "D"],
                        "reason": "component_exceeds_automatic_resolution_size",
                        "record_count": 4,
                    }
                ],
                "form_errors": [],
                "form_token": "token",
                "merge_url": "/merge/",
                "progress_url": "/progress/",
                "workflow_url": "/workflow/",
                "start_url": "/",
                "run_id": "run-review-gfc3",
                "terminal_message": "All duplicate groups have been reviewed.",
            },
        )
        self.assertIn('data-oversized-quarantine="1"', html)
        self.assertIn("not duplicate groups", html.lower())
        self.assertIn(">A<", html)
        self.assertIn('data-review-summary-counts="1"', html)
        self.assertNotIn('class="group-card"', html)
        self.assertNotIn("Edit group dispositions", html)
        self.assertNotIn("comp-oversized", html)


class CrmDuplicateOversizedProgressJourneyTests(TestCase):
    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_progress_redirects_to_workflow_not_review(self) -> None:
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "redesign_phase": "4a",
                "run_id": "run-gfc3-all-oversized",
                "read_grant_id": "grant-1",
                "apply_form_instance": str(uuid4()),
            },
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_all_oversized_projection(),
            ):
                response = self.client.get(
                    reverse(
                        "importer:crm_duplicate_journey_progress",
                        kwargs={"session_id": draft.id},
                    )
                )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("importer:workflow", kwargs={"session_id": draft.id}),
        )
        self.assertNotIn("review", response.url)


def _review_session(*, owner, source_run_id: str) -> ImportSession:
    draft = ImportSession.objects.create(
        owner_id=owner,
        product_key="easyimports.duplicate_resolution",
        status=ImportSession.Status.RUNNING,
        options={
            "run_id": source_run_id,
            "review_run_id": "run-review-gfc3",
            "review_handoff_id": "review_abc",
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


def _complete_review_window() -> dict:
    return {
        "review_contract": "easyimports.crm.duplicate_review_complete.v1",
        "outcome": "complete",
        "groups": [],
        "remaining_group_count": 0,
        "terminal_message": "All duplicate groups have been reviewed.",
    }


class CrmDuplicateOversizedAccountabilityJourneyTests(TestCase):
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

    def _store_source(self, draft: ImportSession) -> None:
        store_workflow_projection(draft, _all_oversized_projection())

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_uses_stored_source_projection_when_live_get_fails(self) -> None:
        draft = _review_session(owner=self.owner, source_run_id="run-gfc3-all-oversized")
        self._store_source(draft)
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result_summary",
                    return_value=_reviewed_result_summary(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=ApiUnavailableError("source unavailable"),
                    ):
                        response = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": draft.id},
                            )
                        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn('data-oversized-quarantine="1"', body)
        self.assertIn(">A<", body)
        self.assertNotIn("data-oversized-quarantine-unavailable", body)
        self.assertNotIn(_OVERSIZED_SOURCE_UNAVAILABLE_COPY, body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_failed_source_get_is_visible(self) -> None:
        draft = _review_session(owner=self.owner, source_run_id="run-gfc3-all-oversized")
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result_summary",
                    return_value=_reviewed_result_summary(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=ApiUnavailableError("source unavailable"),
                    ):
                        response = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": draft.id},
                            )
                        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("data-oversized-quarantine-unavailable", body)
        self.assertIn(_OVERSIZED_SOURCE_UNAVAILABLE_COPY, body)
        self.assertNotIn('data-oversized-quarantine="1"', body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_review_summary_uses_stored_source_projection_when_live_get_fails(
        self,
    ) -> None:
        draft = _review_session(owner=self.owner, source_run_id="run-gfc3-all-oversized")
        self._store_source(draft)
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
                        ):
                            with patch.object(
                                EasyImportsApiClient,
                                "workflow",
                                side_effect=ApiUnavailableError("source unavailable"),
                            ):
                                response = self.client.get(
                                    reverse(
                                        "importer:crm_duplicate_journey_review",
                                        kwargs={"session_id": draft.id},
                                    )
                                )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn('data-oversized-quarantine="1"', body)
        self.assertIn(">A<", body)
        self.assertNotIn("data-oversized-quarantine-unavailable", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_review_summary_failed_source_get_is_visible(self) -> None:
        draft = _review_session(owner=self.owner, source_run_id="run-gfc3-all-oversized")
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
                        ):
                            with patch.object(
                                EasyImportsApiClient,
                                "workflow",
                                side_effect=ApiUnavailableError("source unavailable"),
                            ):
                                response = self.client.get(
                                    reverse(
                                        "importer:crm_duplicate_journey_review",
                                        kwargs={"session_id": draft.id},
                                    )
                                )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("data-oversized-quarantine-unavailable", body)
        self.assertIn(_OVERSIZED_SOURCE_UNAVAILABLE_COPY, body)
        self.assertNotIn('data-oversized-quarantine="1"', body)
