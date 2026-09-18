"""Phase 4: post-authorize merge must land on terminal workflow, not review.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_duplicate_merge_execution_reliability_and_review_ux.md
Phase 4.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from importer.api_client import EasyImportsApiClient
from importer.models import ApiMutation, CrmDuplicateMergePlanLease, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import (
    _duplicate_execution_authorize_for_lease,
    _journey_review_redirect,
    _merge_continuation_post_authorize,
    workflow,
)


def _progress() -> dict:
    return {
        "stage": "building_review_materials",
        "completed_count": 5,
        "total_count": 8,
        "count_unit": "groups",
        "review_ready": False,
        "review_window_ready": True,
        "review_groups_ready": 5,
        "review_groups_total": 8,
    }


def _source_projection(*, run_id: str = "run-source-p4") -> dict:
    return {
        "run_id": run_id,
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_review",
        "stage": "review",
        "revision": 2,
        "summary": {},
        "duplicate_analysis_progress": _progress(),
        "review_handoff": {
            "handoff_id": "handoff-p4",
            "entity": "Account",
            "group_count": 5,
        },
    }


def _child_projection(*, run_id: str = "run-review-p4") -> dict:
    return {
        "run_id": run_id,
        "workflow_key": "easyimports.duplicate_resolution.account_review",
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "needs_decision",
        "stage": "review_account_duplicate_groups",
        "revision": 3,
        "summary": {},
        "decision": {
            "decision_id": "dec-p4",
            "decision_type": "account_duplicate_group_review",
            "body": {
                "duplicate_group_id": "account_dupe_p4",
                "confidence_score": 90,
                "confidence_band": "high",
                "allowed_actions": ["approve", "decline"],
                "recommended_survivor_id": "A1",
                "selected_survivor_id": "A1",
                "advanced_review_required": False,
                "execution_blockers": [],
                "title": "Review this group",
                "message": "Choose a winner.",
                "group_members_df": {"columns": [], "rows": []},
            },
        },
    }


def _continuation_projection(
    *,
    run_id: str = "run-cont-p4",
    status: str = "running",
    revision: int = 4,
) -> dict:
    return {
        "run_id": run_id,
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": status,
        "stage": "complete" if status == "succeeded" else "duplicate_execution",
        "revision": revision,
        "summary": {},
        "terminal_evidence": None,
        "effect_intent": {
            "intent_id": "intent-de",
            "track": "duplicate_execution",
            "gate_phase_id": "freeze_standalone_duplicate_execution_plan",
            "effect_phase_id": "standalone_duplicate_execution_effect",
            "maximum_mode": "execute",
            "supported_modes": ["preview", "dry_run", "execute"],
            "target_provider_id": "fake-preview-v1",
            "target_fingerprint": "fp",
            "work_digest": "wd",
            "confirmation": "authorize:intent-de:duplicate_execution:execute",
        },
    }


def _reviewed_result_summary() -> dict:
    return {
        "summary_contract": "easyimports.crm.duplicate_reviewed_result_summary.v1",
        "review_contract": "easyimports.crm.duplicate_reviewed_result.v1",
        "entity": "account",
        "total_group_count": 1,
        "decided_group_count": 1,
        "approved_merge_group_count": 1,
        "declined_group_count": 0,
        "quarantined_group_count": 0,
        "survivor_count": 1,
        "loser_count": 1,
        "analyzed_record_count": 2,
        "complete": True,
        "merge_plan_frozen": True,
        "decision_set_content_digest": "sha256:deadbeef",
        "source_snapshot_digest": "sha256:snapshot",
        "analysis_work_digest": "sha256:analysis",
        "mapping_digest": None,
        "expected_revision": 5,
        "review_run_id": "run-review-p4",
        "source_run_id": "run-source-p4",
        "review_handoff_id": "handoff-p4",
        "review_binding_digest": "sha256:binding",
        "reviewed_result_content_digest": "sha256:reviewed-result",
    }


class CrmDuplicateMergeReliabilityPhase4Tests(TestCase):
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
                "redesign_phase": "4a",
                "crm_journey_id": "crm_journey_p4",
                "auto_merge_min_confidence": 90,
                "run_id": "run-source-p4",
                "review_run_id": "run-review-p4",
                "apply_root_form_instance": str(uuid4()),
                "orchestrator_form_instance": str(uuid4()),
            },
        )
        options = dict(self.session.options or {})
        options["mutation_journal_id"] = str(self.session.id)
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])
        self.primary = store_workflow_projection(
            self.session,
            _source_projection(),
            role="primary",
        )
        self.review = store_workflow_projection(
            self.session,
            _child_projection(),
            role="review",
            source_workflow=self.primary,
            make_active=False,
        )
        ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            idempotency_key="idem-start-review-p4",
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source-p4/review-workflows",
            logical_action_identity="start-review-p4",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            request_digest="e" * 64,
            resource_identity="run-source-p4",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-review-p4",
                "resource": "/v1/workflows/run-review-p4",
                "revision": 3,
            },
        )

    def _store_continuation(self, *, status: str = "running"):
        continuation = store_workflow_projection(
            self.session,
            _continuation_projection(status=status),
            role="continuation",
            source_workflow=self.primary,
            make_active=False,
        )
        CrmDuplicateMergePlanLease.objects.update_or_create(
            session=self.session,
            defaults={
                "epoch": 0,
                "continuation_run_id": continuation.run_id,
                "decision_set_content_digest": "sha256:deadbeef",
            },
        )
        return continuation

    def _add_authorize_mutation(self, *, state: str) -> ApiMutation:
        return ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            idempotency_key=f"idem-authorize-p4-{state}",
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-cont-p4/effect-authorizations",
            logical_action_identity="authorize-p4",
            logical_action_generation=0,
            form_payload_digest="f" * 64,
            request_digest="g" * 64,
            resource_identity="run-cont-p4",
            state=state,
            request_json={"track": "duplicate_execution", "selected_mode": "execute"},
            response_json=(
                {
                    "outcome": "accepted",
                    "run_id": "run-cont-p4",
                    "revision": 4,
                    "command_kind": "authorize_effect",
                }
                if state == ApiMutation.State.COMPLETED
                else None
            ),
        )

    def _get_workflow(self, extras: dict | None = None):
        extras = extras or {}

        def _api_workflow(run_id, *, owner_session=None):
            if run_id in extras:
                return extras[run_id]
            if run_id == "run-review-p4":
                return _child_projection()
            if run_id == "run-cont-p4":
                return _continuation_projection()
            return _source_projection()

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

    def test_pre_authorize_auto_merge_journey_still_redirects_to_review(self):
        response = self._get_workflow()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "importer:crm_duplicate_journey_review",
                kwargs={"session_id": self.session.id},
            ),
        )

    def test_prior_reference_acquisition_authorize_still_redirects_to_review(self):
        """A prior CRM-read authorize_effect must not skip the review redirect."""

        ra = ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            idempotency_key="idem-authorize-ra-p4",
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-source-p4/effect-authorizations",
            logical_action_identity="authorize-ra-p4",
            logical_action_generation=0,
            form_payload_digest="a" * 64,
            request_digest="b" * 64,
            resource_identity="run-source-p4",
            state=ApiMutation.State.COMPLETED,
            request_json={
                "track": "reference_acquisition",
                "selected_mode": "execute",
            },
            response_json={
                "outcome": "accepted",
                "run_id": "run-source-p4",
                "revision": 2,
                "resource": "/v1/workflows/run-source-p4",
                "command_kind": "authorize_effect",
            },
        )
        start_review = ApiMutation.objects.get(
            session=self.session, mutation_kind="start_review_workflow"
        )
        ra.created_at = start_review.created_at - timedelta(minutes=1)
        ra.first_submitted_at = ra.created_at
        ra.save(update_fields=["created_at", "first_submitted_at"])
        response = self._get_workflow()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "importer:crm_duplicate_journey_review",
                kwargs={"session_id": self.session.id},
            ),
        )
        self.assertFalse(_merge_continuation_post_authorize(self.session))

    def test_pending_authorize_does_not_loop_to_review(self):
        self._store_continuation(status="awaiting_effect_authorization")
        self._add_authorize_mutation(state=ApiMutation.State.PENDING)
        response = self._get_workflow(
            {
                "run-cont-p4": _continuation_projection(
                    status="running",
                )
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("/crm-duplicates/review/", response.get("Location", ""))
        body = response.content.decode("utf-8")
        self.assertNotIn("account_dupe_p4", body)
        self.assertIn("/sessions/", response.wsgi_request.path)
        self.assertIn("/workflow/", response.wsgi_request.path)

    def test_succeeded_continuation_does_not_loop_to_review(self):
        self._store_continuation(status="succeeded")
        self._add_authorize_mutation(state=ApiMutation.State.COMPLETED)
        response = self._get_workflow(
            {"run-cont-p4": _continuation_projection(status="succeeded")}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("/crm-duplicates/review/", response.get("Location", ""))
        self.assertIn("/workflow/", response.wsgi_request.path)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_authorize_post_lands_on_workflow_not_review(self):
        continuation = self._store_continuation(
            status="awaiting_effect_authorization"
        )
        continuation.status = "awaiting_effect_authorization"
        continuation.save(update_fields=["status"])

        def _dispatch(**kwargs):
            self.assertEqual(kwargs["mutation_kind"], "authorize_effect")
            ApiMutation.objects.create(
                session=self.session,
                workflow=continuation,
                form_instance=uuid4(),
                idempotency_key="idem-authorize-post-p4",
                mutation_kind="authorize_effect",
                route=kwargs["route"],
                logical_action_identity="authorize-post-p4",
                logical_action_generation=0,
                form_payload_digest="h" * 64,
                request_digest="i" * 64,
                resource_identity="run-cont-p4",
                state=ApiMutation.State.COMPLETED,
                request_json=kwargs.get("body") or {},
                response_json={
                    "outcome": "accepted",
                    "command_kind": "authorize_effect",
                    "run_id": "run-cont-p4",
                    "revision": 5,
                    "resource": "/v1/workflows/run-cont-p4",
                },
            )
            return {
                "outcome": "accepted",
                "command_kind": "authorize_effect",
                "run_id": "run-cont-p4",
                "revision": 5,
                "resource": "/v1/workflows/run-cont-p4",
            }

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        ), patch.object(
            EasyImportsApiClient, "assert_compatible", return_value=None
        ), patch.object(
            EasyImportsApiClient,
            "duplicate_reviewed_result_summary",
            return_value=_reviewed_result_summary(),
        ), patch.object(
            EasyImportsApiClient,
            "workflow",
            return_value=_continuation_projection(
                status="awaiting_effect_authorization",
                revision=4,
            ),
        ), patch(
            "importer.journey_views.store_workflow_projection"
        ):
            merge_url = reverse(
                "importer:crm_duplicate_journey_merge",
                kwargs={"session_id": self.session.id},
            )
            get_resp = self.client.get(merge_url)
            self.assertEqual(get_resp.status_code, 200)
            token = get_resp.context["form_token"]
            with patch(
                "importer.journey_views._dispatch_json_mutation",
                side_effect=_dispatch,
            ):
                post_resp = self.client.post(
                    merge_url,
                    data={
                        "form_token": token,
                        "merge_action": "authorize",
                        "selected_mode": "execute",
                    },
                )
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/workflow/", post_resp["Location"])
        self.assertNotIn("/crm-duplicates/review/", post_resp["Location"])

        followed = self._get_workflow(
            {"run-cont-p4": _continuation_projection(status="running", revision=5)}
        )
        self.assertEqual(followed.status_code, 200)
        self.assertNotIn("/crm-duplicates/review/", followed.get("Location", ""))
        self.assertIn("/workflow/", followed.wsgi_request.path)

    def test_helpers_do_not_import_mappings_2(self):
        for function in (
            _duplicate_execution_authorize_for_lease,
            _merge_continuation_post_authorize,
            _journey_review_redirect,
            workflow,
        ):
            source = inspect.getsource(function)
            self.assertNotIn("mappings_2", source)
        self.assertFalse(_merge_continuation_post_authorize(self.session))
        self._add_authorize_mutation(state=ApiMutation.State.PENDING)
        self.assertFalse(_merge_continuation_post_authorize(self.session))
        self._store_continuation(status="awaiting_effect_authorization")
        self.assertTrue(_merge_continuation_post_authorize(self.session))
