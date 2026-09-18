"""X2 residual terminal copy: execution mode, in-progress N of M, dry-run verified.

Does not replace the landed Phase 5 "X merged, Y failed" card.

Adjacent suite (same view): importer.tests.WorkflowInterfaceTests.
Four failures in that class predate X2 on main and are not this file's gate.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_execute_retry_and_terminal_ux_reliability.md
Phase X2.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from importer.api_client import EasyImportsApiClient
from importer.models import ApiMutation, CrmDuplicateMergePlanLease, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import (
    _duplicate_execution_authorization_mode,
    _duplicate_execution_planned_count,
    _merge_execution_presentation,
)


def _outcomes_execute_success() -> list[dict]:
    return [
        {
            "duplicate_group_id": "g-ok",
            "survivor_id": "S1",
            "merged": ["A2", "A3"],
            "failed": [],
            "status": "verified",
        }
    ]


def _outcomes_mixed() -> list[dict]:
    return [
        {
            "duplicate_group_id": "g-mixed",
            "survivor_id": "S1",
            "merged": ["A2"],
            "failed": [
                {
                    "loser_id": "A3",
                    "reason": "Attributable HubSpot merge failure for 'A3'",
                }
            ],
            "status": "incomplete",
        }
    ]


def _continuation_projection(
    *,
    status: str = "succeeded",
    authorization: str = "execute",
    outcome: str = "succeeded",
    outcomes: list[dict] | None = None,
    action_count: int | None = None,
    grants: list[dict] | None = None,
) -> dict:
    rows = outcomes if outcomes is not None else _outcomes_mixed()
    merged = sum(len(row.get("merged") or ()) for row in rows)
    failed = sum(len(row.get("failed") or ()) for row in rows)
    planned = action_count if action_count is not None else merged + failed
    evidence = {
        "merged_loser_count": merged,
        "failed_loser_count": failed,
        "group_execution_outcomes": rows,
        "action_count": planned,
    }
    return {
        "run_id": "run-cont-x2",
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": status,
        "stage": "complete" if status == "succeeded" else "running",
        "revision": 6,
        "summary": {
            "merged_loser_count": merged,
            "failed_loser_count": failed,
            "group_execution_count": len(rows),
            "action_count": planned,
        },
        "effect_grants": grants or [],
        "terminal_evidence": {
            "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
            "workflow_version": 5,
            "receipts": [
                {
                    "track": "duplicate_execution",
                    "outcome": outcome,
                    "authorization": authorization,
                    "reason": "",
                    "evidence": evidence,
                }
            ],
            "accountability": {"dispositions": [{"group_id": "g-x2"}]},
            "delivery_manifest": None,
        },
        "effect_intent": None,
        "decision": None,
        "links": {},
    }


class CrmDuplicateExecuteRetryX2Tests(TestCase):
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
                "crm_journey_id": "crm_journey_x2",
                "run_id": "run-source-x2",
                "review_run_id": "run-review-x2",
            },
        )
        self.primary = store_workflow_projection(
            self.session,
            {
                "run_id": "run-source-x2",
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
            _continuation_projection(),
            role="continuation",
            source_workflow=self.primary,
            make_active=False,
        )
        CrmDuplicateMergePlanLease.objects.create(
            session=self.session,
            epoch=0,
            continuation_run_id=self.continuation.run_id,
            decision_set_content_digest="sha256:deadbeef",
        )
        ApiMutation.objects.create(
            session=self.session,
            workflow=self.continuation,
            form_instance=uuid4(),
            idempotency_key="idem-authorize-x2",
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-cont-x2/effect-authorizations",
            logical_action_identity="authorize-x2",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            request_digest="e" * 64,
            resource_identity="run-cont-x2",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-cont-x2",
                "revision": 6,
                "resource": "/v1/workflows/run-cont-x2",
                "command_kind": "authorize_effect",
            },
        )

    def _get(self, projection: dict | None = None):
        value = projection or _continuation_projection()

        def _api_workflow(run_id, *, owner_session=None):
            if run_id == "run-cont-x2":
                return value
            return self.primary.projection

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_api_workflow),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            return self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )

    def test_execute_in_progress_renders_n_of_m_without_dropping_counts_card(self):
        page = self._get(
            _continuation_projection(
                status="running",
                authorization="execute",
                outcome="succeeded",
                outcomes=[
                    {
                        "duplicate_group_id": "g-partial",
                        "survivor_id": "S1",
                        "merged": ["A2"],
                        "failed": [],
                        "status": "verified",
                    }
                ],
                action_count=5,
            )
        )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("data-merge-execution-outcome", body)
        self.assertIn("1 merged, 0 failed", body)
        self.assertIn('data-merge-execution-mode="execute"', body)
        self.assertIn("data-merge-execution-progress", body)
        self.assertIn("1 of 5 merged so far", body)
        self.assertNotIn("verified, zero CRM changes", body)
        self.assertNotIn("no action needed", body)

    def test_dry_run_completed_renders_verified_zero_crm_changes(self):
        page = self._get(
            _continuation_projection(
                status="succeeded",
                authorization="dry_run",
                outcome="dry_run",
                outcomes=_outcomes_execute_success(),
            )
        )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("data-merge-execution-outcome", body)
        self.assertIn("2 merged, 0 failed", body)
        self.assertIn('data-merge-execution-mode="dry_run"', body)
        self.assertIn("verified, zero CRM changes", body)
        self.assertNotIn("data-merge-execution-progress", body)
        self.assertNotIn("merged so far", body)
        self.assertNotIn("no action needed", body)
        self.assertNotIn("Retry failed merges", body)

    def test_dry_run_zero_count_still_renders_verified_copy(self):
        page = self._get(
            _continuation_projection(
                status="succeeded",
                authorization="dry_run",
                outcome="dry_run",
                outcomes=[],
                action_count=0,
            )
        )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("data-merge-execution-outcome", body)
        self.assertIn("0 merged, 0 failed", body)
        self.assertIn("verified, zero CRM changes", body)

    def test_execute_succeeded_keeps_phase5_counts_and_omits_residual_progress(self):
        page = self._get()
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("data-merge-execution-outcome", body)
        self.assertIn("1 merged, 1 failed", body)
        self.assertIn("data-merge-execution-failures", body)
        self.assertIn("Retry failed merges", body)
        self.assertIn('data-merge-execution-mode="execute"', body)
        self.assertNotIn("merged so far", body)
        self.assertNotIn("verified, zero CRM changes", body)
        self.assertNotIn("no action needed", body)
        self.assertNotIn("This merge run could not be completed.", body)

    def test_execute_failed_without_outcomes_names_the_run_failure(self):
        page = self._get(
            _continuation_projection(
                status="failed",
                authorization="execute",
                outcome="succeeded",
                outcomes=[],
                action_count=3,
            )
        )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("data-merge-execution-outcome", body)
        self.assertIn("0 merged, 0 failed", body)
        self.assertIn('data-merge-execution-mode="execute"', body)
        self.assertIn("data-merge-execution-run-failure", body)
        self.assertIn("This merge run could not be completed.", body)
        self.assertNotIn("merged so far", body)
        self.assertNotIn("verified, zero CRM changes", body)

    def test_mode_and_planned_count_helpers_stay_django_local(self):
        running = _continuation_projection(
            status="running",
            authorization="execute",
            action_count=5,
            outcomes=[
                {
                    "duplicate_group_id": "g-partial",
                    "survivor_id": "S1",
                    "merged": ["A2"],
                    "failed": [],
                    "status": "verified",
                }
            ],
        )
        self.assertEqual(_duplicate_execution_authorization_mode(running), "execute")
        self.assertEqual(_duplicate_execution_planned_count(running, 1, 0), 5)
        dry = _continuation_projection(
            authorization="dry_run",
            outcome="dry_run",
            outcomes=_outcomes_execute_success(),
        )
        self.assertEqual(_duplicate_execution_authorization_mode(dry), "dry_run")
        granted = {
            "run_id": "run-cont-x2",
            "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
            "status": "running",
            "summary": {},
            "effect_grants": [
                {
                    "track": "duplicate_execution",
                    "selected_mode": "execute",
                }
            ],
            "terminal_evidence": None,
        }
        self.assertEqual(_duplicate_execution_authorization_mode(granted), "execute")
        presented = _merge_execution_presentation(running, session=self.session)
        self.assertIsNotNone(presented)
        self.assertEqual(presented["progress_copy"], "1 of 5 merged so far")
        self.assertEqual(presented["headline"], "1 merged, 0 failed")
        for function in (
            _duplicate_execution_authorization_mode,
            _duplicate_execution_planned_count,
            _merge_execution_presentation,
        ):
            self.assertNotIn("mappings_2", inspect.getsource(function))
