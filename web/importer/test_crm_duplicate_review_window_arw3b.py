"""ARW-3B: auto-merge journeys do not render the unfiltered child card.

The child's sequential decision is the first pending group and does not
apply the window eligibility filter. When T is frozen, the workflow page
must not show that card. A CRM-dupe journey is sent to the filtered
window page instead.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_review_window_integrity_and_identity_cost.md
Phase ARW-3B.
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from importer.api_client import EasyImportsApiClient, MutationDispatchResult
from importer.journey_views import _discover_existing_review_run
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import (
    _auto_merge_configured,
    _bind_review_run_from_existing_child,
    _journey_review_redirect,
    start_review,
    workflow,
)


def _progress(*, window_ready: bool = True) -> dict:
    return {
        "stage": "building_review_materials",
        "completed_count": 5,
        "total_count": 8,
        "count_unit": "groups",
        "review_ready": False,
        "review_window_ready": window_ready,
        "review_groups_ready": 5,
        "review_groups_total": 8,
    }


def _source_projection(*, run_id: str = "run-source-arw3b", revision: int = 2) -> dict:
    return {
        "run_id": run_id,
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_review",
        "stage": "review",
        "revision": revision,
        "summary": {},
        "duplicate_analysis_progress": _progress(),
        "review_handoff": {
            "handoff_id": "handoff-arw3b",
            "entity": "Account",
            "group_count": 5,
        },
    }


def _child_projection(*, run_id: str = "run-review-arw3b", status: str = "needs_decision") -> dict:
    body = {
        "run_id": run_id,
        "workflow_key": "easyimports.duplicate_resolution.account_review",
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": status,
        "stage": "review_account_duplicate_groups",
        "revision": 3,
        "summary": {},
    }
    if status == "needs_decision":
        body["decision"] = {
            "decision_id": "dec-arw3b",
            "decision_type": "account_duplicate_group_review",
            "body": {
                "duplicate_group_id": "account_dupe_score90",
                "confidence_score": 90,
                "confidence_band": "high",
                "allowed_actions": [
                    "approve",
                    "decline",
                    "quarantine",
                    "override_survivor",
                ],
                "recommended_survivor_id": "A1",
                "selected_survivor_id": "A1",
                "advanced_review_required": False,
                "execution_blockers": [],
                "title": "Review this group",
                "message": "Choose a winner.",
                "group_members_df": {"columns": [], "rows": []},
            },
        }
    return body


class Arw3bChildCardSuppressedTests(TestCase):
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
                "crm_journey_id": "crm_journey_arw3b",
                "auto_merge_min_confidence": 90,
                "run_id": "run-source-arw3b",
                "review_run_id": "run-review-arw3b",
            },
        )
        self.primary = store_workflow_projection(
            self.session,
            _source_projection(),
            role=ApiWorkflow.Role.PRIMARY,
        )
        self.review = store_workflow_projection(
            self.session,
            _child_projection(status="needs_decision"),
            role=ApiWorkflow.Role.REVIEW,
            source_workflow=self.primary,
            make_active=False,
        )
        ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            idempotency_key="idem-start-review-arw3b",
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source-arw3b/review-workflows",
            logical_action_identity="start-review-arw3b",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            resource_identity="run-source-arw3b",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-review-arw3b",
                "resource": "/v1/workflows/run-review-arw3b",
                "revision": 3,
            },
        )

    def _get(self, *, extras: dict | None = None, primary: dict | None = None):
        extras = extras or {}
        source = primary or _source_projection()

        def _workflow(run_id, *, owner_session=None):
            if run_id == "run-review-arw3b":
                return extras.get(run_id, _child_projection())
            if run_id in extras:
                return extras[run_id]
            return source

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_workflow),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            return self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )

    def test_journey_with_threshold_redirects_to_filtered_window(self):
        response = self._get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "importer:crm_duplicate_journey_review",
                kwargs={"session_id": self.session.id},
            ),
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)

    def test_without_threshold_still_shows_child_card(self):
        options = dict(self.session.options or {})
        options["auto_merge_min_confidence"] = None
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm winner and continue")
        self.assertContains(response, "account_dupe_score90")
        self.assertNotContains(response, "Start reviewing")

    def test_non_journey_with_threshold_does_not_render_child_card(self):
        options = dict(self.session.options or {})
        options.pop("crm_journey_id", None)
        options.pop("redesign_phase", None)
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Confirm winner and continue")
        self.assertNotContains(response, "account_dupe_score90")
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)

    def test_start_review_post_redirects_journey_to_window(self):
        self.review.delete()
        ApiMutation.objects.filter(
            session=self.session, mutation_kind="start_review_workflow"
        ).delete()
        root = uuid4()
        options = dict(self.session.options or {})
        options.pop("review_run_id", None)
        options["orchestrator_form_instance"] = str(root)
        options["apply_root_form_instance"] = str(root)
        options["mutation_journal_id"] = str(self.session.id)
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])

        with (
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_source_projection(),
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            token_page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        self.assertEqual(token_page.status_code, 200)
        html = token_page.content.decode("utf-8")
        self.assertIn("handoff-arw3b", html)
        match = re.search(r'name="form_token" value="([^"]+)"', html)
        self.assertIsNotNone(match)
        token = match.group(1)

        child_projection = _child_projection(status="needs_decision")

        def _dispatch(**kwargs):
            self.assertEqual(kwargs["mutation_kind"], "start_review_workflow")
            mutation = ApiMutation.objects.create(
                session=kwargs["session"],
                workflow=kwargs.get("workflow"),
                form_instance=kwargs["form_instance"],
                idempotency_key=str(uuid4()),
                mutation_kind=kwargs["mutation_kind"],
                route=kwargs["route"],
                logical_action_identity=kwargs["logical_action_identity"],
                logical_action_generation=kwargs["logical_action_generation"],
                form_payload_digest=kwargs["form_payload_digest"],
                request_digest="c" * 64,
                resource_identity=kwargs["workflow"].run_id,
                request_json=kwargs.get("body") or {},
                state=ApiMutation.State.COMPLETED,
                http_status=201,
                response_json={
                    "outcome": "accepted",
                    "run_id": "run-review-arw3b",
                    "resource": "/v1/workflows/run-review-arw3b",
                    "revision": 3,
                },
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        def _workflow(run_id, *, owner_session=None):
            if run_id == "run-review-arw3b":
                return child_projection
            if run_id == "run-source-arw3b":
                return _source_projection()
            raise AssertionError(run_id)

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_workflow),
            patch("importer.workflow_views.dispatch_command", side_effect=_dispatch),
        ):
            posted = self.client.post(
                reverse("importer:start_review", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(posted.status_code, 302)
        review_url = reverse(
            "importer:crm_duplicate_journey_review",
            kwargs={"session_id": self.session.id},
        )
        self.assertEqual(posted["Location"], review_url)
        self.session.refresh_from_db()
        self.assertEqual(
            (self.session.options or {}).get("review_run_id"),
            "run-review-arw3b",
        )
        self.assertEqual(
            ApiMutation.objects.filter(
                mutation_kind="start_review_workflow"
            ).count(),
            1,
        )

        get_dispatches: list[str] = []

        def _journey_dispatch(**kwargs):
            get_dispatches.append(str(kwargs.get("mutation_kind") or ""))
            raise AssertionError(
                f"GET must not dispatch {kwargs.get('mutation_kind')}"
            )

        window_body = {
            "outcome": "next_window",
            "window_id": "win-arw3b",
            "window_digest": "d" * 64,
            "expected_revision": 3,
            "total_group_count": 5,
            "decided_group_count": 0,
            "groups": [],
            "page_size": 5,
        }
        with (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(EasyImportsApiClient, "workflow", side_effect=_workflow),
            patch.object(
                EasyImportsApiClient,
                "duplicate_review_window",
                return_value=window_body,
            ),
            patch(
                "importer.journey_views._dispatch_json_mutation",
                side_effect=_journey_dispatch,
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            followed = self.client.get(posted["Location"])
        self.assertEqual(get_dispatches, [])
        self.assertNotEqual(followed.status_code, 302)
        self.assertEqual(
            ApiMutation.objects.filter(
                mutation_kind="start_review_workflow"
            ).count(),
            1,
        )

    def test_helpers_do_not_import_mappings_2(self):
        for function in (
            _auto_merge_configured,
            _bind_review_run_from_existing_child,
            _discover_existing_review_run,
            _journey_review_redirect,
            start_review,
            workflow,
        ):
            source = inspect.getsource(function)
            self.assertNotIn("mappings_2", source)
        self.assertTrue(_auto_merge_configured(self.session))
        dest = _journey_review_redirect(self.session)
        self.assertIsNotNone(dest)
        self.assertEqual(
            dest["Location"],
            reverse(
                "importer:crm_duplicate_journey_review",
                kwargs={"session_id": self.session.id},
            ),
        )
