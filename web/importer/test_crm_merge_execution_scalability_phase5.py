"""P8 / Phase 5: Django first-page exception render + generated-client GET."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from importer.api_client import EasyImportsApiClient
from importer.models import CrmDuplicateMergePlanLease, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import _merge_execution_presentation


def _exception_projection(*, exception_count: int = 2, next_cursor: str | None = None) -> dict:
    exceptions = [
        {
            "group_id": f"g-{index}",
            "reason_code": "pre_mutation_throttle",
            "sanitized_display": "This group was deferred because the CRM asked to slow down before any write.",
            "populate_status": "deferred",
            "group_execution_status": "failed",
        }
        for index in range(exception_count)
    ]
    return {
        "run_id": "run-cont-p8",
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_execution_continuation",
        "stage": "awaiting_execution_continuation",
        "revision": 6,
        "continuation_generation": 0,
        "outcome_generation": 1,
        "continuation_available": True,
        "summary": {
            "merged_loser_count": 2,
            "failed_loser_count": 1,
            "group_execution_count": 2,
        },
        "duplicate_execution_exception_page": {
            "run_id": "run-cont-p8",
            "plan_digest": "digest-p8",
            "outcome_generation": 1,
            "aggregates": {
                "merged_count": 1,
                "needs_attention_count": 0,
                "deferred_count": exception_count,
                "failed_count": 0,
                "verified_unmerged_count": 0,
            },
            "exceptions": exceptions,
            "next_cursor": next_cursor,
        },
        "group_execution_outcomes": [],
        "terminal_evidence": None,
        "effect_intent": None,
        "decision": None,
        "links": {},
    }


class DuplicateExecutionExceptionPageDjangoTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            target_provider_id="fake-preview-v1",
            operator_label="Operator",
            options={
                "crm_journey_id": "crm_journey_p8",
                "run_id": "run-source-p8",
                "review_run_id": "run-review-p8",
            },
        )
        self.primary = store_workflow_projection(
            self.session,
            {
                "run_id": "run-source-p8",
                "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
                "workflow_version": 5,
                "target_provider_id": "fake-preview-v1",
                "status": "succeeded",
                "stage": "complete",
                "revision": 2,
                "summary": {},
                "terminal_evidence": None,
            },
            role="primary",
        )
        self.continuation = store_workflow_projection(
            self.session,
            _exception_projection(),
            role="continuation",
            source_workflow=self.primary,
            make_active=True,
        )
        CrmDuplicateMergePlanLease.objects.create(
            session=self.session,
            epoch=0,
            continuation_run_id=self.continuation.run_id,
            decision_set_content_digest="sha256:deadbeef",
        )

    def test_presentation_caps_exception_rows_at_50(self):
        projection = _exception_projection(exception_count=51, next_cursor="cursor-2")
        presentation = _merge_execution_presentation(
            projection, session=self.session
        )
        self.assertIsNotNone(presentation)
        assert presentation is not None
        self.assertEqual(len(presentation["exceptions"]), 50)
        self.assertTrue(presentation["more_url"])
        self.assertIn("duplicate-execution/exceptions", presentation["more_url"])
        self.assertEqual(presentation["failures"], [])

    def test_presentation_does_not_embed_unbounded_structural_failures(self):
        projection = _exception_projection(exception_count=50, next_cursor="cursor-2")
        projection["terminal_evidence"] = {
            "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
            "workflow_version": 5,
            "receipts": [
                {
                    "track": "duplicate_execution",
                    "outcome": "completed_with_failures",
                    "authorization": "execute",
                    "reason": "",
                    "evidence": {
                        "group_execution_outcomes": [
                            {
                                "duplicate_group_id": f"g-struct-{index}",
                                "survivor_id": "S1",
                                "merged": [],
                                "failed": [
                                    {
                                        "loser_id": f"L-{index}",
                                        "reason": "This merge could not be completed.",
                                    }
                                ],
                                "status": "failed",
                            }
                            for index in range(120)
                        ]
                    },
                }
            ],
            "accountability": None,
            "delivery_manifest": None,
        }
        presentation = _merge_execution_presentation(
            projection, session=self.session
        )
        self.assertIsNotNone(presentation)
        assert presentation is not None
        self.assertEqual(len(presentation["exceptions"]), 50)
        self.assertEqual(presentation["failures"], [])
        self.assertTrue(presentation["more_url"])

    def test_presentation_does_not_silently_truncate_failures_without_page(self):
        projection = _exception_projection(exception_count=0)
        projection["duplicate_execution_exception_page"] = None
        projection["group_execution_outcomes"] = [
            {
                "duplicate_group_id": f"g-{index}",
                "survivor_id": "S1",
                "merged": [],
                "failed": [{"loser_id": f"L-{index}", "reason": "x"}],
                "status": "failed",
            }
            for index in range(120)
        ]
        presentation = _merge_execution_presentation(
            projection, session=self.session
        )
        self.assertIsNotNone(presentation)
        assert presentation is not None
        self.assertEqual(presentation["failures"], [])
        self.assertEqual(presentation["exceptions"], [])

    def test_workflow_page_renders_sanitized_exception_copy(self):
        def _api_workflow(run_id, *, owner_session=None):
            if run_id == "run-cont-p8":
                return _exception_projection()
            return self.primary.projection

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_api_workflow),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Groups that need attention", body)
        self.assertIn("g-0", body)
        self.assertIn("pre_mutation_throttle", body)
        self.assertNotIn("error_code:null", body)
        self.assertNotIn("error_code: null", body)

    def test_more_page_uses_generated_client_get(self):
        captured: dict[str, object] = {}

        def _api_page(run_id, *, owner_session, cursor=None, limit=50):
            captured["run_id"] = run_id
            captured["cursor"] = cursor
            captured["limit"] = limit
            captured["owner_session"] = owner_session
            return {
                "run_id": run_id,
                "plan_digest": "digest-p8",
                "outcome_generation": 1,
                "aggregates": {
                    "merged_count": 1,
                    "needs_attention_count": 0,
                    "deferred_count": 2,
                    "failed_count": 0,
                    "verified_unmerged_count": 0,
                },
                "exceptions": [
                    {
                        "group_id": "g-50",
                        "reason_code": "pre_mutation_throttle",
                        "sanitized_display": "This group was deferred because the CRM asked to slow down before any write.",
                        "populate_status": "deferred",
                        "group_execution_status": "failed",
                    }
                ]
                * 50,
                "next_cursor": None,
            }

        with (
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_exception_projection(next_cursor="cursor-2"),
            ),
            patch.object(
                EasyImportsApiClient,
                "duplicate_execution_exceptions",
                side_effect=_api_page,
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            page = self.client.get(
                reverse(
                    "importer:duplicate_execution_exceptions",
                    args=[self.session.id],
                )
                + "?cursor=cursor-2"
            )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(captured.get("cursor"), "cursor-2")
        self.assertEqual(captured.get("limit"), 50)
        self.assertEqual(len(page.context["exceptions"]), 50)
        body = page.content.decode("utf-8")
        self.assertIn("g-50", body)
        self.assertNotIn("error_code:null", body)
