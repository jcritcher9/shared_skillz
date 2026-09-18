"""ARW-2: keep the review handoff fresh before the first window opens.

The workflow page auto-reloads while a source run may still publish or
replace the first incremental handoff. GFC-6's child-review preparing
copy is unchanged. GET never dispatches. start_review still fails closed
on a mismatched handoff_id.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_review_window_integrity_and_identity_cost.md
Phase ARW-2.
"""

from __future__ import annotations

import inspect
import re
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
    _awaiting_first_review_window,
    _child_review_workflow,
    _review_is_preparing,
    _should_display_review_child,
    start_review,
    workflow,
)


def _progress(*, window_ready: bool, review_ready: bool = False, stage: str = "building_review_materials") -> dict:
    return {
        "stage": stage,
        "completed_count": 100 if window_ready else 0,
        "total_count": 2915,
        "count_unit": "groups",
        "review_ready": review_ready,
        "review_window_ready": window_ready,
        "review_groups_ready": 100 if window_ready else 0,
        "review_groups_total": 2915,
    }


def _projection(
    *,
    run_id: str,
    workflow_key: str,
    status: str,
    revision: int = 1,
    handoff_id: str | None = None,
    progress: dict | None = None,
) -> dict:
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
    if progress is not None:
        body["duplicate_analysis_progress"] = progress
    if handoff_id is not None:
        body["review_handoff"] = {
            "handoff_id": handoff_id,
            "entity": "Account",
            "group_count": 5,
        }
    if status == "needs_decision":
        body["decision"] = {
            "decision_id": "dec-arw2",
            "decision_type": "account_duplicate_review",
            "body": {},
        }
    return body


def _form_token(html: str) -> str:
    match = re.search(r'name="form_token" value="([^"]+)"', html)
    assert match, "page is missing form_token"
    return match.group(1)


class Arw2FirstWindowHandoffRefreshTests(TestCase):
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
                run_id="run-source-arw2",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="running",
                progress=_progress(window_ready=False),
            ),
            role=ApiWorkflow.Role.PRIMARY,
        )

    def _get(self, *, primary: dict, extras: dict | None = None):
        extras = extras or {}

        def _workflow(run_id, *, owner_session=None):
            if run_id == "run-source-arw2":
                return primary
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

    def test_page_reloads_before_review_window_ready_without_gfc6_copy(self):
        response, api_get = self._get(
            primary=_projection(
                run_id="run-source-arw2",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="running",
                progress=_progress(window_ready=False),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-review-handoff-refreshing")
        self.assertContains(response, "2500")
        self.assertNotContains(response, "data-review-preparing")
        self.assertNotContains(response, "Preparing your review")
        self.assertNotContains(response, _REVIEW_PREPARATION_COPY)
        self.assertNotContains(response, "Start reviewing")
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)
        self.assertIn("run-source-arw2", [call.args[0] for call in api_get.call_args_list])

    def test_page_keeps_refreshing_after_window_ready_and_shows_start(self):
        response, _api_get = self._get(
            primary=_projection(
                run_id="run-source-arw2",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_review",
                revision=2,
                handoff_id="handoff-fresh",
                progress=_progress(window_ready=True),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-review-handoff-refreshing")
        self.assertContains(response, "Start reviewing 5 group")
        self.assertContains(response, "handoff-fresh")
        self.assertNotContains(response, "data-review-preparing")
        self.assertNotContains(response, "Preparing your review")

    def test_left_open_until_ready_starts_review_with_fresh_handoff(self):
        first, _ = self._get(
            primary=_projection(
                run_id="run-source-arw2",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="running",
                handoff_id="handoff-stale",
                progress=_progress(window_ready=False),
            )
        )
        self.assertContains(first, "data-review-handoff-refreshing")
        self.assertContains(first, "handoff-stale")

        ready = _projection(
            run_id="run-source-arw2",
            workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            status="awaiting_review",
            revision=3,
            handoff_id="handoff-fresh",
            progress=_progress(window_ready=True),
        )
        second, _ = self._get(primary=ready)
        self.assertContains(second, "Start reviewing 5 group")
        self.assertContains(second, "handoff-fresh")
        self.assertNotContains(second, "handoff-stale")
        token = _form_token(second.content.decode("utf-8"))
        captured: dict[str, object] = {}

        def _dispatch(**kwargs):
            captured.update(kwargs)
            return object()

        with (
            patch.object(EasyImportsApiClient, "workflow", return_value=ready),
            patch("importer.workflow_views.dispatch_command", side_effect=_dispatch),
            patch("importer.workflow_views._record_command_outcome", return_value=None),
        ):
            posted = self.client.post(
                reverse("importer:start_review", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(captured.get("mutation_kind"), "start_review_workflow")
        self.assertEqual(
            (captured.get("body") or {}).get("review_handoff_id"),
            "handoff-fresh",
        )
        self.assertNotEqual(
            (captured.get("body") or {}).get("review_handoff_id"),
            "handoff-stale",
        )

    def test_child_preparing_still_uses_gfc6_path_only(self):
        store_workflow_projection(
            self.session,
            _projection(
                run_id="run-review-arw2",
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
            idempotency_key="idem-start-review-arw2",
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source-arw2/review-workflows",
            logical_action_identity="start-review-arw2",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            resource_identity="run-source-arw2",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-review-arw2",
                "resource": "/v1/workflows/run-review-arw2",
                "revision": 1,
            },
        )
        child = _projection(
            run_id="run-review-arw2",
            workflow_key="easyimports.duplicate_resolution.account_review",
            status="running",
        )
        response, api_get = self._get(
            primary=_projection(
                run_id="run-source-arw2",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_review",
                handoff_id="handoff-fresh",
                progress=_progress(window_ready=True),
            ),
            extras={"run-review-arw2": child},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-review-preparing")
        self.assertContains(response, "Preparing your review")
        self.assertNotContains(response, "data-review-handoff-refreshing")
        self.assertNotContains(response, "Start reviewing")
        self.assertIn("run-review-arw2", [call.args[0] for call in api_get.call_args_list])

    def test_complete_population_ready_does_not_keep_pre_window_refresh(self):
        response, _ = self._get(
            primary=_projection(
                run_id="run-source-arw2",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_review",
                handoff_id="handoff-final",
                progress=_progress(
                    window_ready=True,
                    review_ready=True,
                    stage="complete",
                ),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start reviewing 5 group")
        self.assertNotContains(response, "data-review-handoff-refreshing")
        self.assertNotContains(response, "data-review-preparing")

    def test_helpers_do_not_import_mappings_2(self):
        for function in (
            _awaiting_first_review_window,
            _child_review_workflow,
            _review_is_preparing,
            _should_display_review_child,
            start_review,
            workflow,
        ):
            self.assertNotIn("mappings_2", inspect.getsource(function))
