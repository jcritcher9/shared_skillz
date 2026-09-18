"""Phase 5A: Django merge-summary + finalize handoff (network-free)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from .api_client import EasyImportsApiClient
from .crm_duplicate_merge_copy import (
    APPROVE_MERGE_PLAN_LABEL,
    LEGACY_FREEZE_MERGE_PLAN_LABEL,
)
from .models import CrmDuplicateMergePlanLease, ImportSession


def _reviewed_result(*, frozen: bool = False) -> dict:
    return {
        "review_contract": "easyimports.crm.duplicate_reviewed_result.v1",
        "entity": "account",
        "total_group_count": 2,
        "decided_group_count": 2,
        "approved_merge_group_count": 1,
        "declined_group_count": 1,
        "quarantined_group_count": 0,
        "survivor_count": 1,
        "loser_count": 1,
        "analyzed_record_count": 4,
        "complete": True,
        "merge_plan_frozen": frozen,
        "decision_set_content_digest": "sha256:deadbeef",
        "source_snapshot_digest": "sha256:snapshot",
        "analysis_work_digest": "sha256:analysis",
        "mapping_digest": None,
        "dispositions": [
            {
                "group_id": "g1",
                "disposition": "approved",
                "group_status": "reviewable",
                "allowed_actions": [
                    "approve",
                    "override_survivor",
                    "decline",
                    "quarantine",
                ],
                "recommended_survivor_id": "A1",
                "selected_survivor_id": "A1",
                "loser_ids": ["A2"],
                "member_ids": ["A1", "A2"],
                "members": [
                    {
                        "record_id": "A1",
                        "is_survivor": True,
                        "is_loser": False,
                        "survivor_eligible": True,
                        "recommended": True,
                    },
                    {
                        "record_id": "A2",
                        "is_survivor": False,
                        "is_loser": True,
                        "survivor_eligible": True,
                        "recommended": False,
                    },
                ],
                "advanced_review_required": False,
                "group_revision": "rev-g1",
                "group_evidence_digest": "sha256:g1",
                "decided_by": "op",
                "decided_at": "2026-08-04T12:00:00+00:00",
            },
            {
                "group_id": "g2",
                "disposition": "declined",
                "group_status": "reviewable",
                "allowed_actions": [
                    "approve",
                    "override_survivor",
                    "decline",
                    "quarantine",
                ],
                "recommended_survivor_id": "A3",
                "selected_survivor_id": "A3",
                "loser_ids": [],
                "member_ids": ["A3", "A4"],
                "members": [
                    {
                        "record_id": "A3",
                        "is_survivor": False,
                        "is_loser": False,
                        "survivor_eligible": True,
                        "recommended": True,
                    },
                    {
                        "record_id": "A4",
                        "is_survivor": False,
                        "is_loser": False,
                        "survivor_eligible": True,
                        "recommended": False,
                    },
                ],
                "advanced_review_required": False,
                "group_revision": "rev-g2",
                "group_evidence_digest": "sha256:g2",
                "decided_by": "op",
                "decided_at": "2026-08-04T12:01:00+00:00",
            },
        ],
        "expected_revision": 5,
        "review_run_id": "run-review-5a",
        "source_run_id": "run-source-5a",
        "review_handoff_id": "review_abc",
        "review_binding_digest": "sha256:binding",
    }


def _reviewed_result_summary(*, frozen: bool = False) -> dict:
    full = _reviewed_result(frozen=frozen)
    return {
        "summary_contract": "easyimports.crm.duplicate_reviewed_result_summary.v1",
        **{key: value for key, value in full.items() if key != "dispositions"},
        "reviewed_result_content_digest": "sha256:reviewed-result",
    }


def _reviewed_disposition_window() -> dict:
    reviewed = _reviewed_result()
    return {
        "window_contract": (
            "easyimports.crm.duplicate_reviewed_disposition_window.v1"
        ),
        "review_run_id": reviewed["review_run_id"],
        "reviewed_result_content_digest": "sha256:reviewed-result",
        "decision_set_content_digest": reviewed["decision_set_content_digest"],
        "expected_revision": reviewed["expected_revision"],
        "total_group_count": reviewed["total_group_count"],
        "window_start": 1,
        "window_end": 2,
        "window_token": "window-token",
        "next_cursor": None,
        "previous_cursor": None,
        "dispositions": reviewed["dispositions"],
    }


def _intent() -> dict:
    return {
        "intent_id": "intent-de",
        "track": "duplicate_execution",
        "gate_phase_id": "freeze_standalone_duplicate_execution_plan",
        "effect_phase_id": "standalone_duplicate_execution_effect",
        "maximum_mode": "execute",
        "supported_modes": ["preview", "dry_run", "execute"],
        "target_provider_id": "fake",
        "target_fingerprint": "fp",
        "work_digest": "wd",
        "confirmation": "authorize:intent-de:duplicate_execution:execute",
    }


def _handoff_receipt() -> dict:
    return {
        "command_id": "cmd-finalize",
        "command_kind": "finalize_duplicate_merge_plan_handoff",
        "run_id": "run-review-5a",
        "revision": 6,
        "workflow_status": "succeeded",
        "stage": "merge_plan_handoff",
        "outcome": "accepted",
        "error_code": None,
        "message": None,
        "resource": "/v1/workflows/run-review-5a/duplicate-merge-plan-handoff",
        "result": {
            "merge_plan_handoff": {
                "handoff_contract": "easyimports.crm.duplicate_merge_plan_handoff.v1",
                "disposition_window": _reviewed_disposition_window(),
                "decision_set_handoff": {
                    "handoff_id": "decisions_1",
                    "review_handoff_id": "review_abc",
                    "entity": "account",
                    "decision_count": 2,
                    "binding_digest": "sha256:binding",
                },
                "continuation_run_id": "run-cont-5a",
                "continuation_revision": 2,
                "workflow_status": "awaiting_effect_authorization",
                "effect_intent": _intent(),
                "supported_modes": ["preview", "dry_run", "execute"],
                "maximum_mode": "execute",
                "track": "duplicate_execution",
                "plan_content_digest": None,
                "write_authorization_required": True,
                "source_read_grant_authorizes_writes": False,
            }
        },
    }


class CrmDuplicateJourneyPhase5aTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="CRM dupe 5A",
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            target_provider_id="fake",
            options={
                "run_id": "run-source-5a",
                "review_run_id": "run-review-5a",
                "review_handoff_id": "review_abc",
                "apply_root_form_instance": str(uuid4()),
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        session = self.client.session
        session["easyimports_owner_id"] = str(self.owner)
        session.save()

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_get_is_read_only_shows_approve_cta(self):
        """GET never finalizes; operator must POST to approve the plan."""

        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result_summary",
                    return_value=_reviewed_result_summary(),
                ):
                    with patch(
                        "importer.journey_views._dispatch_json_mutation"
                    ) as dispatch:
                        response = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": self.session.id},
                            )
                        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Review merge plan", content)
        self.assertIn(APPROVE_MERGE_PLAN_LABEL, content)
        self.assertNotIn(LEGACY_FREEZE_MERGE_PLAN_LABEL, content)
        self.assertIn("Approved for merge", content)
        self.assertIn("does not approve", content.lower())
        dispatch.assert_not_called()
        self.session.refresh_from_db()
        self.assertNotIn("continuation_run_id", self.session.options or {})

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_post_finalize_freezes_plan(self):
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result_summary",
                    return_value=_reviewed_result_summary(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=[
                            {
                                "run_id": "run-review-5a",
                                "revision": 5,
                                "status": "succeeded",
                            },
                            {
                                "run_id": "run-cont-5a",
                                "revision": 2,
                                "status": "awaiting_effect_authorization",
                                "effect_intent": _intent(),
                            },
                        ],
                    ):
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            return_value=_handoff_receipt(),
                        ):
                            with patch(
                                "importer.journey_views.store_workflow_projection"
                            ):
                                # First GET for form token.
                                get_resp = self.client.get(
                                    reverse(
                                        "importer:crm_duplicate_journey_merge",
                                        kwargs={"session_id": self.session.id},
                                    )
                                )
                                token = get_resp.context["form_token"]
                                post_resp = self.client.post(
                                    reverse(
                                        "importer:crm_duplicate_journey_merge",
                                        kwargs={"session_id": self.session.id},
                                    ),
                                    data={
                                        "form_token": token,
                                        "merge_action": "finalize",
                                        "summary_expected_revision": 5,
                                        "summary_decision_set_content_digest": "sha256:deadbeef",
                                    },
                                )
        self.assertEqual(post_resp.status_code, 302)
        self.session.refresh_from_db()
        lease = CrmDuplicateMergePlanLease.objects.get(session=self.session)
        self.assertEqual(lease.continuation_run_id, "run-cont-5a")
        self.assertNotIn("merge_plan_finalized", self.session.options or {})
        self.assertNotIn("continuation_run_id", self.session.options or {})

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_get_never_fetches_full_reviewed_result(self):
        """R3 renders the bounded summary and never re-lists dispositions."""

        with self._patch_owner(), patch.object(
            EasyImportsApiClient, "assert_compatible", return_value=None
        ), patch.object(
            EasyImportsApiClient,
            "duplicate_reviewed_result_summary",
            return_value=_reviewed_result_summary(),
        ), patch.object(
            EasyImportsApiClient, "duplicate_reviewed_result"
        ) as full_result:
            response = self.client.get(
                reverse(
                    "importer:crm_duplicate_journey_merge",
                    kwargs={"session_id": self.session.id},
                )
            )
        self.assertEqual(response.status_code, 200)
        full_result.assert_not_called()
        self.assertNotIn("Group dispositions", response.content.decode("utf-8"))

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_finalize_rejects_stale_summary(self):
        """R3 binds the finalization form to the rendered summary identity."""

        stale = _reviewed_result_summary()
        current = _reviewed_result_summary()
        current["expected_revision"] = 6
        current["decision_set_content_digest"] = "sha256:new-decisions"
        with self._patch_owner(), patch.object(
            EasyImportsApiClient, "assert_compatible", return_value=None
        ), patch.object(
            EasyImportsApiClient,
            "duplicate_reviewed_result_summary",
            side_effect=[stale, current, current],
        ), patch(
            "importer.journey_views._dispatch_json_mutation"
        ) as dispatch:
            url = reverse(
                "importer:crm_duplicate_journey_merge",
                kwargs={"session_id": self.session.id},
            )
            get_response = self.client.get(url)
            post_response = self.client.post(
                url,
                data={
                    "form_token": get_response.context["form_token"],
                    "merge_action": "finalize",
                    "summary_expected_revision": 5,
                    "summary_decision_set_content_digest": "sha256:deadbeef",
                },
            )
        self.assertEqual(post_response.status_code, 200)
        self.assertContains(post_response, "summary is stale")
        dispatch.assert_not_called()

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_authorize_post_accepts_matching_form_token(self):
        """GET token identity must accept authorize POST without selected_mode."""

        CrmDuplicateMergePlanLease.objects.update_or_create(
            session=self.session,
            defaults={
                "epoch": 0,
                "continuation_run_id": "run-cont-5a",
                "decision_set_content_digest": "sha256:x",
            },
        )
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value={
                            "run_id": "run-cont-5a",
                            "revision": 2,
                            "status": "awaiting_effect_authorization",
                            "effect_intent": _intent(),
                        },
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection"
                        ):
                            get_resp = self.client.get(
                                reverse(
                                    "importer:crm_duplicate_journey_merge",
                                    kwargs={"session_id": self.session.id},
                                )
                            )
                            token = get_resp.context["form_token"]
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                return_value={
                                    "outcome": "accepted",
                                    "command_kind": "authorize_effect",
                                },
                            ) as dispatch:
                                post_resp = self.client.post(
                                    reverse(
                                        "importer:crm_duplicate_journey_merge",
                                        kwargs={"session_id": self.session.id},
                                    ),
                                    data={
                                        "form_token": token,
                                        "merge_action": "authorize",
                                        "selected_mode": "dry_run",
                                    },
                                )
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/sessions/", post_resp["Location"])
        dispatch.assert_called_once()
        kwargs = dispatch.call_args.kwargs
        self.assertEqual(kwargs["mutation_kind"], "authorize_effect")
        self.assertEqual(kwargs["body"]["selected_mode"], "dry_run")

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_invalidate_dispatches_durable_revoke(self):
        CrmDuplicateMergePlanLease.objects.update_or_create(
            session=self.session,
            defaults={
                "epoch": 0,
                "continuation_run_id": "run-cont-5a",
                "decision_set_content_digest": "sha256:x",
            },
        )
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(frozen=True),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value={
                            "run_id": "run-cont-5a",
                            "revision": 2,
                            "status": "awaiting_effect_authorization",
                            "effect_intent": _intent(),
                        },
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection"
                        ):
                            get_resp = self.client.get(
                                reverse(
                                    "importer:crm_duplicate_journey_merge",
                                    kwargs={"session_id": self.session.id},
                                )
                            )
                            html = get_resp.content.decode("utf-8")
                            self.assertIn('name="form_token"', html)
                            self.assertIn(
                                get_resp.context["invalidate_form_token"], html
                            )
                            token = get_resp.context["invalidate_form_token"]
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                return_value={
                                    "outcome": "accepted",
                                    "command_kind": (
                                        "invalidate_duplicate_merge_plan_freeze"
                                    ),
                                    "result": {
                                        "invalidated": True,
                                        "revoked_continuation_run_ids": [
                                            "run-cont-5a"
                                        ],
                                    },
                                },
                            ) as dispatch:
                                response = self.client.post(
                                    reverse(
                                        "importer:crm_duplicate_journey_merge",
                                        kwargs={"session_id": self.session.id},
                                    ),
                                    data={
                                        "form_token": token,
                                        "merge_action": "invalidate",
                                    },
                                )
        self.assertEqual(response.status_code, 302)
        dispatch.assert_called_once()
        self.assertEqual(
            dispatch.call_args.kwargs["mutation_kind"],
            "invalidate_duplicate_merge_plan_freeze",
        )
        self.session.refresh_from_db()
        self.assertNotIn("continuation_run_id", self.session.options or {})
        lease = CrmDuplicateMergePlanLease.objects.get(session=self.session)
        self.assertEqual(lease.continuation_run_id, "")
        self.assertEqual(lease.epoch, 1)
        self.assertEqual(lease.applied_finalize_mutation_id, "")

    def test_merge_url_registered(self):
        url = reverse(
            "importer:crm_duplicate_journey_merge",
            kwargs={"session_id": self.session.id},
        )
        self.assertIn("/crm-duplicates/merge/", url)
