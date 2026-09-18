"""P7 / Phase 6: Django continue/finish buttons and mutation kinds."""

from __future__ import annotations

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
from importer.workflow_views import _merge_execution_presentation


def _parked_projection() -> dict:
    return {
        "run_id": "run-cont-p6",
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
        "group_execution_outcomes": [
            {
                "duplicate_group_id": "g-ok",
                "survivor_id": "A1",
                "merged": ["A2"],
                "failed": [],
                "status": "verified",
            },
            {
                "duplicate_group_id": "g-deferred",
                "survivor_id": "B1",
                "merged": [],
                "failed": [{"loser_id": "B2", "reason": "needs attention"}],
                "status": "failed",
            },
        ],
        "terminal_evidence": None,
        "effect_intent": None,
        "decision": None,
        "links": {},
    }


class DuplicateExecutionContinuationDjangoTests(TestCase):
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
                "crm_journey_id": "crm_journey_p6",
                "run_id": "run-source-p6",
                "review_run_id": "run-review-p6",
            },
        )
        self.primary = store_workflow_projection(
            self.session,
            {
                "run_id": "run-source-p6",
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
            _parked_projection(),
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

    def _get(self, projection: dict | None = None):
        value = projection or _parked_projection()

        def _api_workflow(run_id, *, owner_session=None):
            if run_id == "run-cont-p6":
                return value
            return self.primary.projection

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_api_workflow),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            return self.client.get(reverse("importer:workflow", args=[self.session.id]))

    def test_continue_and_finish_buttons_post_cycle_commands_not_authorize(self):
        page = self._get()
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Continue remaining merges", body)
        self.assertIn("Finish with remaining exceptions", body)
        self.assertIn(
            reverse("importer:continue_duplicate_execution", args=[self.session.id]),
            body,
        )
        self.assertIn(
            reverse("importer:finish_duplicate_execution", args=[self.session.id]),
            body,
        )
        self.assertNotIn("Retry failed merges", body)
        self.assertNotIn("/effect-authorizations", body)

    def test_merge_page_get_does_not_create_an_attempt(self):
        with (
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_parked_projection(),
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            page = self.client.get(
                reverse(
                    "importer:crm_duplicate_journey_merge",
                    args=[self.session.id],
                )
            )
        self.assertEqual(page.status_code, 200)
        self.assertFalse(
            ApiMutation.objects.filter(
                session=self.session,
                mutation_kind="continue_duplicate_execution",
            ).exists()
        )
        self.assertFalse(
            ApiMutation.objects.filter(
                session=self.session,
                mutation_kind="authorize_effect",
            ).exists()
        )

    def test_continue_post_dispatches_continue_duplicate_execution(self):
        captured: dict[str, object] = {}

        def _dispatch(**kwargs):
            captured.update(kwargs)

            class _Result:
                flash_message = ""
                mutation = type(
                    "M",
                    (),
                    {
                        "state": ApiMutation.State.COMPLETED,
                        "mutation_kind": "continue_duplicate_execution",
                        "response_json": {},
                    },
                )()

            return _Result()

        with (
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_parked_projection(),
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=_dispatch,
            ),
            patch("importer.workflow_views._record_command_outcome"),
        ):
            page = self._get()
            token_start = body = page.content.decode("utf-8")
            marker = 'name="form_token" value="'
            self.assertIn(marker, token_start)
            token = token_start.split(marker, 1)[1].split('"', 1)[0]
            response = self.client.post(
                reverse(
                    "importer:continue_duplicate_execution",
                    args=[self.session.id],
                ),
                {"form_token": token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(captured.get("mutation_kind"), "continue_duplicate_execution")
        self.assertNotEqual(captured.get("mutation_kind"), "authorize_effect")
        self.assertIn(
            "/duplicate-execution-continuations",
            str(captured.get("route") or ""),
        )

    def test_finish_post_dispatches_finish_duplicate_execution(self):
        captured: dict[str, object] = {}

        def _dispatch(**kwargs):
            captured.update(kwargs)

            class _Result:
                flash_message = ""
                mutation = type(
                    "M",
                    (),
                    {
                        "state": ApiMutation.State.COMPLETED,
                        "mutation_kind": "finish_duplicate_execution",
                        "response_json": {},
                    },
                )()

            return _Result()

        with (
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_parked_projection(),
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=_dispatch,
            ),
            patch("importer.workflow_views._record_command_outcome"),
        ):
            page = self._get()
            body = page.content.decode("utf-8")
            marker = 'name="form_token" value="'
            tokens = [chunk.split('"', 1)[0] for chunk in body.split(marker)[1:]]
            self.assertGreaterEqual(len(tokens), 2)
            response = self.client.post(
                reverse(
                    "importer:finish_duplicate_execution",
                    args=[self.session.id],
                ),
                {"form_token": tokens[1]},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(captured.get("mutation_kind"), "finish_duplicate_execution")
        self.assertNotEqual(captured.get("mutation_kind"), "authorize_effect")
        self.assertIn(
            "/duplicate-execution-completion",
            str(captured.get("route") or ""),
        )

    def test_presentation_marks_continuation_available(self):
        presentation = _merge_execution_presentation(
            _parked_projection(), session=self.session
        )
        self.assertIsNotNone(presentation)
        assert presentation is not None
        self.assertTrue(presentation["continuation_available"])
        self.assertEqual(presentation["continuation_generation"], 0)
