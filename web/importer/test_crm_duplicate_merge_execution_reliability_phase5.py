"""Phase 5: terminal merge-outcome screen from durable evidence.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_duplicate_merge_execution_reliability_and_review_ux.md
Phase 5.
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
    _is_operator_safe_evidence_key,
    _merge_execution_presentation,
    _operator_safe_merge_reason,
    _sanitize_operator_visible_mapping,
)


def _mixed_outcomes() -> list[dict]:
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


def _continuation_projection(*, outcomes: list[dict] | None = None) -> dict:
    rows = outcomes if outcomes is not None else _mixed_outcomes()
    merged = sum(len(row.get("merged") or ()) for row in rows)
    failed = sum(len(row.get("failed") or ()) for row in rows)
    return {
        "run_id": "run-cont-p5",
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "succeeded",
        "stage": "complete",
        "revision": 6,
        "summary": {
            "merged_loser_count": merged,
            "failed_loser_count": failed,
            "group_execution_count": len(rows),
        },
        "terminal_evidence": {
            "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
            "workflow_version": 5,
            "receipts": [
                {
                    "track": "duplicate_execution",
                    "outcome": "completed_with_failures",
                    "authorization": "execute",
                    "reason": "",
                    "evidence": {
                        "merged_loser_count": merged,
                        "failed_loser_count": failed,
                        "group_execution_outcomes": rows,
                    },
                }
            ],
            "accountability": {"dispositions": [{"group_id": "g-mixed"}]},
            "delivery_manifest": None,
        },
        "effect_intent": None,
        "decision": None,
        "links": {},
    }


class CrmDuplicateMergeReliabilityPhase5Tests(TestCase):
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
                "crm_journey_id": "crm_journey_p5",
                "run_id": "run-source-p5",
                "review_run_id": "run-review-p5",
            },
        )
        self.primary = store_workflow_projection(
            self.session,
            {
                "run_id": "run-source-p5",
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
            idempotency_key="idem-authorize-p5",
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-cont-p5/effect-authorizations",
            logical_action_identity="authorize-p5",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            request_digest="e" * 64,
            resource_identity="run-cont-p5",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-cont-p5",
                "revision": 6,
                "resource": "/v1/workflows/run-cont-p5",
                "command_kind": "authorize_effect",
            },
        )

    def _get(self, projection: dict | None = None):
        value = projection or _continuation_projection()

        def _api_workflow(run_id, *, owner_session=None):
            if run_id == "run-cont-p5":
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

    def test_mixed_success_failure_renders_counts_and_per_group_failures(self):
        page = self._get()
        self.assertEqual(page.status_code, 200)
        self.assertIn("/workflow/", page.wsgi_request.path)
        body = page.content.decode("utf-8")
        self.assertIn("data-merge-execution-outcome", body)
        self.assertIn("1 merged, 1 failed", body)
        self.assertIn("data-merge-execution-failures", body)
        self.assertIn("Group g-mixed", body)
        self.assertIn("survivor S1", body)
        self.assertIn("record A3", body)
        self.assertIn("Attributable HubSpot merge failure for", body)
        self.assertIn("A3", body)
        self.assertIn("Retry failed merges", body)
        merge_url = reverse(
            "importer:crm_duplicate_journey_merge",
            kwargs={"session_id": self.session.id},
        )
        self.assertIn(merge_url, body)
        self.assertNotIn("/crm-duplicates/review/", body)

    def test_raw_exception_and_secrets_are_not_rendered(self):
        leaked = _continuation_projection(
            outcomes=[
                {
                    "duplicate_group_id": "g-leak",
                    "survivor_id": "S9",
                    "merged": [],
                    "failed": [
                        {
                            "loser_id": "A9",
                            "reason": (
                                "AttributeError: 'NoneType' object has no "
                                "attribute 'decisions'\nTraceback (most recent "
                                "call last):\n  File \"runtime.py\", line 12\n"
                                "Authorization: Bearer super-secret-token"
                            ),
                        }
                    ],
                    "status": "failed",
                }
            ]
        )
        page = self._get(leaked)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("0 merged, 1 failed", body)
        self.assertIn("This merge could not be completed.", body)
        self.assertNotIn("AttributeError", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("super-secret-token", body)
        self.assertNotIn("Bearer", body)
        self.assertNotIn("runtime.py", body)

    def test_unsafe_strings_outside_reason_are_not_rendered(self):
        """Receipt evidence keys other than reason must not leak secrets."""

        leaked = _continuation_projection()
        evidence = leaked["terminal_evidence"]["receipts"][0]["evidence"]
        evidence["transport_diagnostic"] = (
            "Authorization: Bearer critic-secret-token"
        )
        page = self._get(leaked)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("1 merged, 1 failed", body)
        self.assertNotIn("critic-secret-token", body)
        self.assertNotIn("Bearer", body)
        self.assertNotIn("Authorization:", body)

    def test_credential_keys_and_exception_forms_are_not_rendered(self):
        """Credential keys and Exception: values redact even without Bearer markers."""

        leaked = _continuation_projection()
        evidence = leaked["terminal_evidence"]["receipts"][0]["evidence"]
        evidence["access_token"] = "plain-access-value"
        evidence["refresh_token"] = "plain-refresh-value"
        evidence["client_secret"] = "plain-client-secret"
        evidence["password"] = "plain-password-value"
        evidence["diagnostic"] = "Exception: merge worker died"
        page = self._get(leaked)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("1 merged, 1 failed", body)
        self.assertNotIn("plain-access-value", body)
        self.assertNotIn("plain-refresh-value", body)
        self.assertNotIn("plain-client-secret", body)
        self.assertNotIn("plain-password-value", body)
        self.assertNotIn("Exception: merge worker died", body)
        self.assertNotIn("merge worker died", body)

    def test_unknown_evidence_fields_fail_closed_on_the_page(self):
        """Allowlisted display omits unknown credential fields and dotted exceptions."""

        leaked = _continuation_projection()
        evidence = leaked["terminal_evidence"]["receipts"][0]["evidence"]
        evidence["authorization"] = "opaque-credential-value"
        evidence["credentials"] = "opaque-credential-bundle"
        evidence["basic_auth"] = "opaque-basic-value"
        evidence["cookie"] = "opaque-session-value"
        leaked["terminal_evidence"]["receipts"][0]["reason"] = (
            "requests.exceptions.HTTPError: provider failed"
        )
        page = self._get(leaked)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("1 merged, 1 failed", body)
        self.assertNotIn("opaque-credential-value", body)
        self.assertNotIn("opaque-credential-bundle", body)
        self.assertNotIn("opaque-basic-value", body)
        self.assertNotIn("opaque-session-value", body)
        self.assertNotIn("requests.exceptions.HTTPError", body)
        self.assertNotIn("provider failed", body)

    def test_all_success_omits_retry_and_failure_list(self):
        page = self._get(
            _continuation_projection(
                outcomes=[
                    {
                        "duplicate_group_id": "g-ok",
                        "survivor_id": "S1",
                        "merged": ["A2", "A3"],
                        "failed": [],
                        "status": "verified",
                    }
                ]
            )
        )
        body = page.content.decode("utf-8")
        self.assertIn("2 merged, 0 failed", body)
        self.assertNotIn("data-merge-execution-failures", body)
        self.assertNotIn("Retry failed merges", body)

    def test_operator_safe_reason_and_helpers_stay_django_local(self):
        self.assertEqual(
            _operator_safe_merge_reason("Stale property/updatedAt for record 'X'"),
            "Stale property/updatedAt for record 'X'",
        )
        self.assertEqual(
            _operator_safe_merge_reason(
                "AttributeError: 'NoneType' object has no attribute 'decisions'"
            ),
            "This merge could not be completed.",
        )
        self.assertEqual(
            _operator_safe_merge_reason("Exception: merge worker died"),
            "This merge could not be completed.",
        )
        self.assertEqual(
            _operator_safe_merge_reason(
                "requests.exceptions.HTTPError: provider failed"
            ),
            "This merge could not be completed.",
        )
        for function in (
            _is_operator_safe_evidence_key,
            _operator_safe_merge_reason,
            _sanitize_operator_visible_mapping,
            _merge_execution_presentation,
        ):
            self.assertNotIn("mappings_2", inspect.getsource(function))
        self.assertTrue(_is_operator_safe_evidence_key("merged_loser_count"))
        self.assertFalse(_is_operator_safe_evidence_key("access_token"))
        self.assertFalse(_is_operator_safe_evidence_key("credentials"))
        self.assertFalse(_is_operator_safe_evidence_key("cookie"))
        redacted = _sanitize_operator_visible_mapping(
            {
                "merged_loser_count": 1,
                "authorization": "execute",
                "credentials": "opaque-credential-bundle",
                "basic_auth": "opaque-basic-value",
                "cookie": "opaque-session-value",
                "access_token": "plain-access-value",
                "reason": "requests.exceptions.HTTPError: provider failed",
            }
        )
        self.assertEqual(redacted["merged_loser_count"], 1)
        self.assertEqual(redacted["authorization"], "execute")
        self.assertNotIn("credentials", redacted)
        self.assertNotIn("basic_auth", redacted)
        self.assertNotIn("cookie", redacted)
        self.assertNotIn("access_token", redacted)
        self.assertEqual(redacted["reason"], "Unavailable for display.")
        opaque_auth = _sanitize_operator_visible_mapping(
            {"authorization": "opaque-credential-value"}
        )
        self.assertEqual(
            opaque_auth["authorization"], "Unavailable for display."
        )
        presented = _merge_execution_presentation(
            _continuation_projection(),
            session=self.session,
        )
        self.assertIsNotNone(presented)
        self.assertEqual(presented["headline"], "1 merged, 1 failed")
        self.assertEqual(len(presented["failures"]), 1)
        self.assertTrue(presented["retryable"])
