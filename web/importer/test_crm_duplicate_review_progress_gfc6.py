"""GFC-6: workflow page shows review-preparation progress via public GET.

Django identifies the child review workflow from the start_review receipt
and GET-polls GET /v1/workflows/{id}. It does not import mappings_2, does
not replace the active primary, and GET never dispatches.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_group_pipeline_efficiency.md
Phase GFC-6.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from importer.api_client import EasyImportsApiClient
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import (
    _REVIEW_PREPARATION_COPY,
    _child_review_workflow,
    _review_is_preparing,
    _should_display_review_child,
    workflow,
)


def _projection(*, run_id: str, workflow_key: str, status: str, revision: int = 1) -> dict:
    body = {
        "run_id": run_id,
        "workflow_key": workflow_key,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": status,
        "stage": "review",
        "revision": revision,
        "summary": {},
    }
    if status == "awaiting_review":
        body["review_handoff"] = {
            "handoff_id": "handoff-gfc6",
            "entity": "Account",
            "group_count": 12,
        }
    if status == "needs_decision":
        body["decision"] = {
            "decision_id": "dec-gfc6",
            "decision_type": "account_duplicate_review",
            "body": {},
        }
    return body


class Gfc6ReviewPreparationProgressTests(TestCase):
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
        )
        self.primary = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-source-gfc6",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_review",
            ),
            role=ApiWorkflow.Role.PRIMARY,
        )
        self.review = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-review-gfc6",
                workflow_key="easyimports.duplicate_resolution.account_review",
                status="running",
            ),
            role=ApiWorkflow.Role.REVIEW,
            source_workflow=self.primary,
            make_active=False,
        )
        ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            idempotency_key="idem-start-review-gfc6",
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source-gfc6/review-workflows",
            logical_action_identity="start-review-gfc6",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            resource_identity="run-source-gfc6",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-review-gfc6",
                "resource": "/v1/workflows/run-review-gfc6",
                "revision": 1,
            },
        )

    def _get_workflow(self, *, review_status: str, extra_projections: dict | None = None):
        primary = _projection(
            run_id="run-source-gfc6",
            workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            status="awaiting_review",
        )
        child = _projection(
            run_id="run-review-gfc6",
            workflow_key="easyimports.duplicate_resolution.account_review",
            status=review_status,
        )
        extras = extra_projections or {}

        def _workflow(run_id, *, owner_session=None):
            if run_id == "run-review-gfc6":
                return child
            if run_id in extras:
                return extras[run_id]
            return primary

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_workflow) as api_get,
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            response = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        return response, api_get

    def test_preparing_review_is_distinct_from_needs_decision(self):
        response, api_get = self._get_workflow(review_status="running")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preparing your review")
        self.assertContains(response, _REVIEW_PREPARATION_COPY)
        self.assertContains(response, 'data-review-preparing')
        self.assertContains(response, "2500")
        self.assertNotContains(response, "Start reviewing")
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)
        requested = [call.args[0] for call in api_get.call_args_list]
        self.assertIn("run-review-gfc6", requested)

    def test_needs_decision_does_not_show_preparation_copy(self):
        response, _api_get = self._get_workflow(review_status="needs_decision")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Preparing your review")
        self.assertNotContains(response, 'data-review-preparing')
        self.assertNotContains(response, "Start reviewing")
        self.assertContains(response, "Review required")
        self.assertContains(response, "run-review-gfc6")
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)

    def test_needs_decision_shows_child_review_not_start_form(self):
        preparing, _ = self._get_workflow(review_status="running")
        self.assertContains(preparing, "Preparing your review")
        self.assertNotContains(preparing, "Start reviewing")
        ready, api_get = self._get_workflow(review_status="needs_decision")
        self.assertEqual(ready.status_code, 200)
        self.assertNotContains(ready, "Preparing your review")
        self.assertNotContains(ready, "Start reviewing 12 group")
        self.assertNotContains(ready, "Start reviewing")
        self.assertContains(ready, "Review required")
        self.assertContains(ready, "run-review-gfc6")
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)
        requested = [call.args[0] for call in api_get.call_args_list]
        self.assertIn("run-review-gfc6", requested)

    def test_get_does_not_dispatch_and_review_prep_does_not_import_mappings_2(self):
        response, api_get = self._get_workflow(review_status="running")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(api_get.called)
        for function in (
            _child_review_workflow,
            _review_is_preparing,
            _should_display_review_child,
            workflow,
        ):
            source = inspect.getsource(function)
            self.assertNotIn("mappings_2", source)

    def test_completed_review_does_not_supersede_continuation(self):
        self.review.status = "needs_decision"
        self.review.projection = _projection(
            run_id="run-review-gfc6",
            workflow_key="easyimports.duplicate_resolution.account_review",
            status="needs_decision",
        )
        self.review.save(update_fields=["status", "projection", "updated_at"])
        continuation = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-continuation-gfc6",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_effect_authorization",
                revision=3,
            ),
            role=ApiWorkflow.Role.CONTINUATION,
            source_workflow=self.review,
            make_active=False,
        )
        ApiMutation.objects.create(
            session=self.session,
            workflow=self.review,
            form_instance=uuid4(),
            idempotency_key="idem-bind-decision-gfc6",
            mutation_kind="bind_decision_set_handoff",
            route="/v1/workflows/run-review-gfc6/decision-set-handoffs",
            logical_action_identity="bind-decision-gfc6",
            logical_action_generation=0,
            form_payload_digest="e" * 64,
            resource_identity="run-review-gfc6",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-continuation-gfc6",
                "resource": "/v1/workflows/run-continuation-gfc6",
                "revision": 3,
            },
        )
        continuation_projection = _projection(
            run_id="run-continuation-gfc6",
            workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            status="awaiting_effect_authorization",
            revision=3,
        )
        response, api_get = self._get_workflow(
            review_status="needs_decision",
            extra_projections={
                "run-continuation-gfc6": continuation_projection,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "run-continuation-gfc6")
        self.assertNotContains(response, "Preparing your review")
        self.assertNotContains(response, "Start reviewing")
        self.assertContains(response, "Ready for your approval")
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)
        requested = [call.args[0] for call in api_get.call_args_list]
        self.assertIn("run-continuation-gfc6", requested)
        self.assertNotIn("run-review-gfc6", requested)
        self.assertEqual(continuation.run_id, "run-continuation-gfc6")
