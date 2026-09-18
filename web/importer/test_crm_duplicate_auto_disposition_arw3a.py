"""ARW-3A: do not dispatch auto-disposition before source review_ready.

GET never writes. A review-page CTA retries at a new generation with a
fresh form_instance after a duplicate_analysis_incomplete rejection, or
starts generation 0 when no attempt exists and the source is now ready.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_review_window_integrity_and_identity_cost.md
Phase ARW-3A.
"""

from __future__ import annotations

import inspect
import re
import threading
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from importer.api_client import (
    ApiUnavailableError,
    EasyImportsApiClient,
    create_or_reuse_mutation,
)
from importer.command_service import action_generation
from importer import journey_views as journey_views_mod
from importer.journey_views import (
    AUTO_DISPOSITION_COMMAND_KIND,
    AUTO_DISPOSITION_INCOMPLETE_CODE,
    _acknowledge_incomplete_predecessor,
    _auto_disposition_cta_context,
    _auto_disposition_bindings_match,
    _auto_disposition_identity,
    _handle_auto_disposition_cta_post,
    _is_documented_legacy_auto_disposition_predecessor,
    _is_legacy_pre_source_binding_request,
    _maybe_apply_auto_disposition,
    _source_is_review_ready_for_auto_disposition,
    _step_form_instance,
    crm_duplicate_journey_review,
)
from importer.models import ApiMutation, ImportSession


def _owner_session(owner) -> str:
    return f"django-{owner}"


def _source_projection(*, review_ready: bool, run_id: str = "run-src-arw3a") -> dict:
    return {
        "run_id": run_id,
        "revision": 3,
        "workflow_key": "easyimports.duplicate_resolution",
        "status": "running" if not review_ready else "awaiting_review",
        "stage": "building_review_materials" if not review_ready else "complete",
        "review_handoff": {
            "handoff_id": "handoff-arw3a",
            "entity": "account",
            "group_count": 2,
        },
        "duplicate_analysis_progress": {
            "stage": "building_review_materials" if not review_ready else "complete",
            "review_ready": review_ready,
            "review_window_ready": True,
            "review_groups_ready": 2,
            "review_groups_total": 6,
        },
    }


def _review_projection(*, revision: int = 1, run_id: str = "run-review-arw3a") -> dict:
    return {
        "run_id": run_id,
        "revision": revision,
        "workflow_key": "easyimports.duplicate_resolution.account_review",
        "status": "needs_decision",
        "stage": "awaiting_review",
        "summary": {},
    }


def _window() -> dict:
    return {
        "review_contract": "easyimports.crm.duplicate_review_window.v1",
        "window_id": "win-arw3a",
        "window_digest": "sha256:win-arw3a",
        "expected_revision": 1,
        "group_start": 1,
        "group_end": 1,
        "page_size": 5,
        "total_group_count": 2,
        "decided_group_count": 0,
        "remaining_group_count": 2,
        "groups": [
            {
                "group_id": "g-low",
                "group_revision": "rev-g-low",
                "entity_family": "company",
                "group_status": "ready",
                "review_lane": "standard",
                "confidence_band": "medium",
                "confidence_score": 80,
                "advanced_review_required": False,
                "execution_blockers": [],
                "allowed_actions": ["approve", "decline", "quarantine"],
                "recommended_survivor_id": "A1",
                "selected_survivor_id": "A1",
                "members": [
                    {
                        "record_id": "A1",
                        "display_fields": {"Name": "Acme"},
                        "recommended": True,
                        "selected": True,
                        "survivor_eligible": True,
                        "ranking_evidence": {},
                    },
                    {
                        "record_id": "A2",
                        "display_fields": {"Name": "Acme Inc"},
                        "recommended": False,
                        "selected": False,
                        "survivor_eligible": True,
                        "ranking_evidence": {},
                    },
                ],
                "conflicts": [],
                "evidence": [],
            }
        ],
        "outcome": "next_window",
    }


def _auto_body(*, revision: int = 1) -> dict:
    return {
        "command_kind": AUTO_DISPOSITION_COMMAND_KIND,
        "run_id": "run-review-arw3a",
        "expected_revision": revision,
        "applied_revision": revision + 1,
        "auto_merge_min_confidence": 90,
        "auto_approved_group_count": 1,
        "manual_remaining_group_count": 1,
        "total_group_count": 2,
        "auto_approved_group_ids": ["g-high"],
        "review_ready": True,
        "manual_queue_empty": False,
        "result_digest": "sha256:arw3a",
    }


def _form_token(html: str, *, cta: bool = False) -> str:
    if cta:
        match = re.search(
            r'data-auto-disposition-cta[\s\S]*?name="form_token" value="([^"]+)"',
            html,
        )
    else:
        match = re.search(r'name="form_token" value="([^"]+)"', html)
    assert match, "missing form_token"
    return match.group(1)


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class Arw3aAutoDispositionTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.root = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "redesign_phase": "4a",
                "run_id": "run-src-arw3a",
                "source_run_id": "run-src-arw3a",
                "review_run_id": "run-review-arw3a",
                "review_handoff_id": "handoff-arw3a",
                "apply_root_form_instance": str(self.root),
                "orchestrator_form_instance": str(self.root),
                "auto_merge_min_confidence": 90,
                "mutation_journal_id": "",
            },
        )
        self.session.options["mutation_journal_id"] = str(self.session.id)
        self.session.save(update_fields=["options", "updated_at"])
        self.owner_session = _owner_session(self.owner)
        self.identity = _auto_disposition_identity(
            owner_session=self.owner_session,
            root_form_instance=self.root,
            review_run_id="run-review-arw3a",
            threshold=90,
        )

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    def _review_url(self):
        return reverse(
            "importer:crm_duplicate_journey_review",
            kwargs={"session_id": self.session.id},
        )

    def _progress_url(self):
        return reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": self.session.id},
        )

    def _new_rejected_request_json(self) -> dict:
        return {
            "command_kind": AUTO_DISPOSITION_COMMAND_KIND,
            "expected_revision": 1,
            "auto_merge_min_confidence": 90,
            "owner_session": self.owner_session,
            "source_run_id": "run-src-arw3a",
        }

    def _captured_pre_remediation_request_json(self) -> dict:
        """Documented live payload: four keys, no source_run_id."""

        return {
            "command_kind": AUTO_DISPOSITION_COMMAND_KIND,
            "expected_revision": 3,
            "auto_merge_min_confidence": 90,
            "owner_session": self.owner_session,
        }

    def _plant_rejected(
        self,
        *,
        generation: int = 0,
        acknowledged: bool = False,
        request_json: dict | None = None,
        form_instance=None,
    ):
        return ApiMutation.objects.create(
            session=self.session,
            form_instance=(
                form_instance
                if form_instance is not None
                else _step_form_instance(self.root, "auto-disposition")
            ),
            idempotency_key=f"idem-arw3a-{generation}",
            mutation_kind="submit_duplicate_auto_disposition",
            route="/v1/workflows/run-review-arw3a/duplicate-auto-disposition",
            logical_action_identity=self.identity,
            logical_action_generation=generation,
            form_payload_digest="d" * 64,
            request_digest="c" * 64,
            resource_identity="run-review-arw3a",
            request_json=(
                dict(request_json)
                if request_json is not None
                else self._new_rejected_request_json()
            ),
            state=ApiMutation.State.REJECTED,
            error_code=AUTO_DISPOSITION_INCOMPLETE_CODE,
            error_message="Duplicate analysis is not complete.",
            http_status=422,
            response_json={
                "error": {
                    "message": "Duplicate analysis is not complete.",
                    "details": {"error_code": AUTO_DISPOSITION_INCOMPLETE_CODE},
                }
            },
            acknowledged_at=timezone.now() if acknowledged else None,
        )

    def _fake_auto_dispatch(self, *, revision: int = 1):
        body = _auto_body(revision=revision)

        def _dispatch(**kwargs):
            if kwargs["mutation_kind"] != "submit_duplicate_auto_disposition":
                if kwargs["mutation_kind"] == "start_review_workflow":
                    return {"run_id": "run-review-arw3a"}
                raise AssertionError(kwargs["mutation_kind"])
            mutation = create_or_reuse_mutation(
                session=kwargs["journal"],
                form_instance=kwargs["form_instance"],
                mutation_kind=kwargs["mutation_kind"],
                route=kwargs["route"],
                logical_action_identity=kwargs["logical_action_identity"],
                logical_action_generation=kwargs.get("logical_action_generation", 0),
                request_json={
                    **kwargs["body"],
                    "owner_session": kwargs["owner_session"],
                },
                resource_identity=kwargs.get("resource_identity") or "",
            )
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.save(update_fields=["state", "response_json", "updated_at"])
            return body

        return _dispatch

    def _workflow(self, *, source_ready: bool, review_revision: int = 1):
        source = _source_projection(review_ready=source_ready)
        review = _review_projection(revision=review_revision)

        def _side_effect(run_id, *, owner_session=None):
            if run_id == "run-src-arw3a":
                return source
            if run_id == "run-review-arw3a":
                return review
            raise AssertionError(run_id)

        return _side_effect

    def test_maybe_apply_skips_dispatch_when_source_not_review_ready(self):
        client = type("C", (), {})()
        client.workflow = self._workflow(source_ready=False)
        dispatches: list[str] = []

        def _dispatch(**kwargs):
            dispatches.append(kwargs["mutation_kind"])
            raise AssertionError("must not dispatch")

        with patch(
            "importer.journey_views._dispatch_json_mutation",
            side_effect=_dispatch,
        ):
            options = _maybe_apply_auto_disposition(
                workflow_session=self.session,
                client=client,
                owner_session=self.owner_session,
                source_run_id="run-src-arw3a",
                journal=self.session,
                root_form_instance=self.root,
            )
        self.assertFalse(options.get("auto_disposition_applied"))
        self.assertEqual(dispatches, [])
        self.assertEqual(
            ApiMutation.objects.filter(
                mutation_kind="submit_duplicate_auto_disposition"
            ).count(),
            0,
        )

    def test_get_cta_does_not_write_or_advance_generation(self):
        predecessor = self._plant_rejected()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-auto-disposition-cta")
        predecessor.refresh_from_db()
        self.assertIsNone(predecessor.acknowledged_at)
        self.assertEqual(
            ApiMutation.objects.filter(logical_action_identity=self.identity).count(),
            1,
        )
        self.assertEqual(action_generation(self.session, self.identity), 0)

    def test_stale_new_attempt_does_not_acknowledge_or_create(self):
        predecessor = self._plant_rejected()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True, review_revision=1),
                    ):
                        page = self.client.get(self._review_url())
                    token = _form_token(page.content.decode("utf-8"), cta=True)
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True, review_revision=9),
                    ):
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=AssertionError("must not dispatch"),
                        ):
                            posted = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
        self.assertEqual(posted.status_code, 200)
        predecessor.refresh_from_db()
        self.assertIsNone(predecessor.acknowledged_at)
        self.assertFalse(
            ApiMutation.objects.filter(
                logical_action_identity=self.identity,
                logical_action_generation=1,
            ).exists()
        )

    def test_valid_cta_acknowledges_and_creates_generation(self):
        predecessor = self._plant_rejected()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        page = self.client.get(self._review_url())
                        token = _form_token(page.content.decode("utf-8"), cta=True)
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=self._fake_auto_dispatch(),
                        ):
                            posted = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
        self.assertEqual(posted.status_code, 302)
        predecessor.refresh_from_db()
        self.assertIsNotNone(predecessor.acknowledged_at)
        created = ApiMutation.objects.get(
            logical_action_identity=self.identity,
            logical_action_generation=1,
        )
        self.assertEqual(created.state, ApiMutation.State.COMPLETED)
        self.assertNotEqual(
            created.form_instance,
            _step_form_instance(self.root, "auto-disposition"),
        )
        self.session.refresh_from_db()
        self.assertTrue(self.session.options.get("auto_disposition_applied"))

    def test_sequential_double_submit_replays_without_revision_check(self):
        self._plant_rejected()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True, review_revision=1),
                    ):
                        page = self.client.get(self._review_url())
                        token = _form_token(page.content.decode("utf-8"), cta=True)
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=self._fake_auto_dispatch(),
                        ):
                            first = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
                    self.assertEqual(first.status_code, 302)
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True, review_revision=2),
                    ):
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=self._fake_auto_dispatch(revision=1),
                        ):
                            second = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            ApiMutation.objects.filter(
                logical_action_identity=self.identity,
                logical_action_generation=1,
            ).count(),
            1,
        )

    def test_crash_after_ack_before_create_recovers(self):
        predecessor = self._plant_rejected(acknowledged=True)
        self.assertEqual(action_generation(self.session, self.identity), 1)
        self.assertFalse(
            ApiMutation.objects.filter(
                logical_action_identity=self.identity,
                logical_action_generation=1,
            ).exists()
        )
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        page = self.client.get(self._review_url())
                        token = _form_token(page.content.decode("utf-8"), cta=True)
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=self._fake_auto_dispatch(),
                        ):
                            posted = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
        self.assertEqual(posted.status_code, 302)
        predecessor.refresh_from_db()
        self.assertIsNotNone(predecessor.acknowledged_at)
        self.assertTrue(
            ApiMutation.objects.filter(
                logical_action_identity=self.identity,
                logical_action_generation=1,
                state=ApiMutation.State.COMPLETED,
            ).exists()
        )

    def test_unknown_and_unrelated_rejection_do_not_offer_cta(self):
        ApiMutation.objects.create(
            session=self.session,
            form_instance=uuid4(),
            idempotency_key="idem-unknown",
            mutation_kind="submit_duplicate_auto_disposition",
            route="/v1/workflows/run-review-arw3a/duplicate-auto-disposition",
            logical_action_identity=self.identity,
            logical_action_generation=0,
            form_payload_digest="e" * 64,
            request_digest="c" * 64,
            resource_identity="run-review-arw3a",
            state=ApiMutation.State.UNKNOWN,
            error_message="Read timed out",
        )
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        unknown_page = self.client.get(self._review_url())
        self.assertContains(unknown_page, "data-auto-disposition-needs-attention")
        self.assertNotContains(unknown_page, "data-auto-disposition-cta")
        ApiMutation.objects.all().delete()
        ApiMutation.objects.create(
            session=self.session,
            form_instance=uuid4(),
            idempotency_key="idem-other",
            mutation_kind="submit_duplicate_auto_disposition",
            route="/v1/workflows/run-review-arw3a/duplicate-auto-disposition",
            logical_action_identity=self.identity,
            logical_action_generation=0,
            form_payload_digest="f" * 64,
            request_digest="c" * 64,
            resource_identity="run-review-arw3a",
            state=ApiMutation.State.REJECTED,
            error_code="other_error",
            response_json={"error": {"details": {"error_code": "other_error"}}},
        )
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        other_page = self.client.get(self._review_url())
        self.assertContains(other_page, "data-auto-disposition-needs-attention")
        self.assertNotContains(other_page, "data-auto-disposition-cta")

    def test_natural_operator_sequence_surfaces_cta_after_source_completes(self):
        client = type("C", (), {})()
        client.workflow = self._workflow(source_ready=False)
        with patch(
            "importer.journey_views._dispatch_json_mutation",
            side_effect=AssertionError("progress must not dispatch"),
        ):
            skipped = _maybe_apply_auto_disposition(
                workflow_session=self.session,
                client=client,
                owner_session=self.owner_session,
                source_run_id="run-src-arw3a",
                journal=self.session,
                root_form_instance=self.root,
            )
        self.assertFalse(skipped.get("auto_disposition_applied"))
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=False),
                    ):
                        reviewing = self.client.get(self._review_url())
                    self.assertEqual(reviewing.status_code, 200)
                    self.assertNotContains(reviewing, "data-auto-disposition-cta")
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        ready = self.client.get(self._review_url())
                        self.assertContains(ready, "data-auto-disposition-cta")
                        token = _form_token(ready.content.decode("utf-8"), cta=True)
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=self._fake_auto_dispatch(),
                        ):
                            posted = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
        self.assertEqual(posted.status_code, 302)
        created = ApiMutation.objects.get(
            logical_action_identity=self.identity,
            logical_action_generation=0,
        )
        self.assertEqual(created.state, ApiMutation.State.COMPLETED)

    def test_captured_rejected_row_recovers_without_session_reset(self):
        captured_payload = self._captured_pre_remediation_request_json()
        self.assertNotIn("source_run_id", captured_payload)
        predecessor = self._plant_rejected(request_json=captured_payload)
        self.assertEqual(predecessor.request_json, captured_payload)
        self.assertTrue(_is_legacy_pre_source_binding_request(predecessor.request_json))
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        page = self.client.get(self._review_url())
                        predecessor.refresh_from_db()
                        self.assertEqual(predecessor.request_json, captured_payload)
                        self.assertNotIn("source_run_id", predecessor.request_json)
                        self.assertEqual(predecessor.request_digest, "c" * 64)
                        self.assertIsNone(predecessor.acknowledged_at)
                        self.assertEqual(
                            action_generation(self.session, self.identity), 0
                        )
                        self.assertContains(page, "data-auto-disposition-cta")
                        token = _form_token(page.content.decode("utf-8"), cta=True)
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=self._fake_auto_dispatch(),
                        ):
                            posted = self.client.post(
                                self._review_url(),
                                {
                                    "form_token": token,
                                    "auto_disposition_cta": "1",
                                },
                            )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(
            self.session.id,
            ImportSession.objects.get(pk=self.session.id).id,
        )
        created = ApiMutation.objects.get(
            logical_action_identity=self.identity,
            logical_action_generation=1,
            state=ApiMutation.State.COMPLETED,
        )
        self.assertEqual(created.request_json.get("source_run_id"), "run-src-arw3a")
        predecessor.refresh_from_db()
        self.assertIsNotNone(predecessor.acknowledged_at)
        self.assertEqual(predecessor.request_json, captured_payload)
        self.assertNotIn("source_run_id", predecessor.request_json)
        self.assertEqual(predecessor.request_digest, "c" * 64)

    def test_missing_root_form_instance_fails_closed(self):
        options = dict(self.session.options or {})
        options.pop("apply_root_form_instance", None)
        options.pop("orchestrator_form_instance", None)
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        page = self.client.get(self._review_url())
        self.assertContains(page, "data-auto-disposition-root-missing")
        self.assertNotContains(page, "data-auto-disposition-cta")
        self.assertNotContains(page, "data-auto-disposition-needs-attention")

    def test_already_acknowledged_predecessor_is_valid_on_retry(self):
        self._plant_rejected()
        kwargs = {
            "journal": self.session,
            "identity": self.identity,
            "generation": 1,
            "review_run_id": "run-review-arw3a",
            "threshold": 90,
            "source_run_id": "run-src-arw3a",
        }
        _acknowledge_incomplete_predecessor(**kwargs)
        _acknowledge_incomplete_predecessor(**kwargs)
        predecessor = ApiMutation.objects.get(
            logical_action_identity=self.identity,
            logical_action_generation=0,
        )
        self.assertIsNotNone(predecessor.acknowledged_at)
        self.assertEqual(action_generation(self.session, self.identity), 1)

    def test_helpers_do_not_import_mappings_2(self):
        for function in (
            _acknowledge_incomplete_predecessor,
            _auto_disposition_cta_context,
            _auto_disposition_identity,
            _auto_disposition_bindings_match,
            _handle_auto_disposition_cta_post,
            _is_documented_legacy_auto_disposition_predecessor,
            _is_legacy_pre_source_binding_request,
            _maybe_apply_auto_disposition,
            _source_is_review_ready_for_auto_disposition,
            crm_duplicate_journey_review,
        ):
            self.assertNotIn("mappings_2", inspect.getsource(function))

    def test_fetch_failure_and_malformed_progress_do_not_dispatch(self):
        client = type("C", (), {})()

        def _raise(*, owner_session=None):
            raise ApiUnavailableError("source unavailable")

        client.workflow = _raise
        with patch(
            "importer.journey_views._dispatch_json_mutation",
            side_effect=AssertionError("must not dispatch"),
        ):
            options = _maybe_apply_auto_disposition(
                workflow_session=self.session,
                client=client,
                owner_session=self.owner_session,
                source_run_id="run-src-arw3a",
                journal=self.session,
                root_form_instance=self.root,
            )
        self.assertFalse(options.get("auto_disposition_applied"))

        def _malformed(run_id, *, owner_session=None):
            return {"run_id": run_id, "status": "awaiting_review"}

        client.workflow = _malformed
        with patch(
            "importer.journey_views._dispatch_json_mutation",
            side_effect=AssertionError("must not dispatch"),
        ):
            again = _maybe_apply_auto_disposition(
                workflow_session=self.session,
                client=client,
                owner_session=self.owner_session,
                source_run_id="run-src-arw3a",
                journal=self.session,
                root_form_instance=self.root,
            )
        self.assertFalse(again.get("auto_disposition_applied"))
        self.assertFalse(_source_is_review_ready_for_auto_disposition({}))
        self.assertFalse(
            _source_is_review_ready_for_auto_disposition(
                {"duplicate_analysis_progress": {"review_window_ready": True}}
            )
        )
        self.assertFalse(
            _source_is_review_ready_for_auto_disposition(
                {"duplicate_analysis_progress": {"review_ready": False}}
            )
        )
        self.assertTrue(
            _source_is_review_ready_for_auto_disposition(
                {"duplicate_analysis_progress": {"review_ready": True}}
            )
        )

    def test_pending_and_idempotency_conflict_do_not_offer_cta(self):
        ApiMutation.objects.create(
            session=self.session,
            form_instance=uuid4(),
            idempotency_key="idem-pending",
            mutation_kind="submit_duplicate_auto_disposition",
            route="/v1/workflows/run-review-arw3a/duplicate-auto-disposition",
            logical_action_identity=self.identity,
            logical_action_generation=0,
            form_payload_digest="e" * 64,
            request_digest="c" * 64,
            resource_identity="run-review-arw3a",
            request_json={
                "auto_merge_min_confidence": 90,
                "source_run_id": "run-src-arw3a",
            },
            state=ApiMutation.State.PENDING,
        )
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        pending_page = self.client.get(self._review_url())
        self.assertContains(pending_page, "data-auto-disposition-needs-attention")
        self.assertNotContains(pending_page, "data-auto-disposition-cta")
        ApiMutation.objects.all().delete()
        conflict = ApiMutation.objects.create(
            session=self.session,
            form_instance=uuid4(),
            idempotency_key="idem-conflict",
            mutation_kind="submit_duplicate_auto_disposition",
            route="/v1/workflows/run-review-arw3a/duplicate-auto-disposition",
            logical_action_identity=self.identity,
            logical_action_generation=0,
            form_payload_digest="f" * 64,
            request_digest="c" * 64,
            resource_identity="run-review-arw3a",
            request_json={
                "auto_merge_min_confidence": 90,
                "source_run_id": "run-src-arw3a",
            },
            state=ApiMutation.State.REJECTED,
            error_code="idempotency_conflict",
            response_json={"error": {"details": {"error_code": "idempotency_conflict"}}},
        )
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        conflict_page = self.client.get(self._review_url())
        self.assertContains(conflict_page, "data-auto-disposition-needs-attention")
        self.assertNotContains(conflict_page, "data-auto-disposition-cta")
        with self.assertRaises(ValueError):
            _acknowledge_incomplete_predecessor(
                journal=self.session,
                identity=self.identity,
                generation=1,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        conflict.refresh_from_db()
        self.assertIsNone(conflict.acknowledged_at)

    def test_malformed_root_form_instance_fails_closed(self):
        options = dict(self.session.options or {})
        options["apply_root_form_instance"] = "not-a-uuid"
        options["orchestrator_form_instance"] = "still-not-a-uuid"
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._workflow(source_ready=True),
                    ):
                        page = self.client.get(self._review_url())
        self.assertContains(page, "data-auto-disposition-root-missing")
        self.assertNotContains(page, "data-auto-disposition-cta")

    def test_bindings_require_exact_source_review_and_threshold(self):
        mutation = self._plant_rejected()
        self.assertTrue(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        mutation.resource_identity = ""
        self.assertFalse(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        mutation.resource_identity = "run-review-arw3a"
        mutation.request_json = {
            "auto_merge_min_confidence": 90,
            "owner_session": self.owner_session,
        }
        self.assertFalse(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        mutation.request_json = {
            "source_run_id": "run-src-arw3a",
            "owner_session": self.owner_session,
        }
        self.assertFalse(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        captured = self._captured_pre_remediation_request_json()
        mutation.request_json = captured
        self.assertTrue(_is_legacy_pre_source_binding_request(captured))
        self.assertFalse(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        extra = dict(captured)
        extra["note"] = "not the captured shape"
        mutation.request_json = extra
        mutation.save(update_fields=["request_json"])
        self.assertFalse(_is_legacy_pre_source_binding_request(extra))
        self.assertFalse(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        with self.assertRaises(ValueError):
            _acknowledge_incomplete_predecessor(
                journal=self.session,
                identity=self.identity,
                generation=1,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        mutation.refresh_from_db()
        self.assertIsNone(mutation.acknowledged_at)
        self.assertEqual(mutation.request_json, extra)
        self.assertNotIn("source_run_id", mutation.request_json)
        mutation.request_json = {
            **captured,
            "source_run_id": "run-other",
        }
        self.assertFalse(
            _auto_disposition_bindings_match(
                mutation,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )

    def test_legacy_predecessor_ack_is_restricted_to_documented_lineage(self):
        captured = self._captured_pre_remediation_request_json()
        generation_zero = self._plant_rejected(request_json=captured)
        digest = generation_zero.request_digest
        self.assertFalse(
            _auto_disposition_bindings_match(
                generation_zero,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-other-source",
            )
        )
        self.assertTrue(
            _is_documented_legacy_auto_disposition_predecessor(
                generation_zero,
                review_run_id="run-review-arw3a",
                threshold=90,
            )
        )
        _acknowledge_incomplete_predecessor(
            journal=self.session,
            identity=self.identity,
            generation=1,
            review_run_id="run-review-arw3a",
            threshold=90,
            source_run_id="run-src-arw3a",
        )
        generation_zero.refresh_from_db()
        self.assertIsNotNone(generation_zero.acknowledged_at)
        self.assertEqual(generation_zero.request_json, captured)
        self.assertEqual(generation_zero.request_digest, digest)
        self.assertNotIn("source_run_id", generation_zero.request_json)

        later = self._plant_rejected(
            generation=7,
            request_json=captured,
            form_instance=uuid4(),
        )
        self.assertFalse(
            _auto_disposition_bindings_match(
                later,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        )
        self.assertFalse(
            _is_documented_legacy_auto_disposition_predecessor(
                later,
                review_run_id="run-review-arw3a",
                threshold=90,
            )
        )
        with self.assertRaises(ValueError):
            _acknowledge_incomplete_predecessor(
                journal=self.session,
                identity=self.identity,
                generation=8,
                review_run_id="run-review-arw3a",
                threshold=90,
                source_run_id="run-src-arw3a",
            )
        later.refresh_from_db()
        self.assertIsNone(later.acknowledged_at)
        self.assertEqual(later.request_json, captured)
        self.assertEqual(later.request_digest, "c" * 64)


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class Arw3aConcurrentCtaTests(TransactionTestCase):
    def setUp(self):
        self.owner = uuid4()
        self.root = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "redesign_phase": "4a",
                "run_id": "run-src-arw3a",
                "source_run_id": "run-src-arw3a",
                "review_run_id": "run-review-arw3a",
                "review_handoff_id": "handoff-arw3a",
                "apply_root_form_instance": str(self.root),
                "orchestrator_form_instance": str(self.root),
                "auto_merge_min_confidence": 90,
            },
        )
        self.session.options["mutation_journal_id"] = str(self.session.id)
        self.session.save(update_fields=["options", "updated_at"])
        self.owner_session = _owner_session(self.owner)
        self.identity = _auto_disposition_identity(
            owner_session=self.owner_session,
            root_form_instance=self.root,
            review_run_id="run-review-arw3a",
            threshold=90,
        )
        ApiMutation.objects.create(
            session=self.session,
            form_instance=_step_form_instance(self.root, "auto-disposition"),
            idempotency_key="idem-arw3a-concurrent",
            mutation_kind="submit_duplicate_auto_disposition",
            route="/v1/workflows/run-review-arw3a/duplicate-auto-disposition",
            logical_action_identity=self.identity,
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            request_digest="c" * 64,
            resource_identity="run-review-arw3a",
            request_json={
                "command_kind": AUTO_DISPOSITION_COMMAND_KIND,
                "expected_revision": 1,
                "auto_merge_min_confidence": 90,
                "owner_session": self.owner_session,
                "source_run_id": "run-src-arw3a",
            },
            state=ApiMutation.State.REJECTED,
            error_code=AUTO_DISPOSITION_INCOMPLETE_CODE,
            error_message="Duplicate analysis is not complete.",
            http_status=422,
            response_json={
                "error": {
                    "details": {"error_code": AUTO_DISPOSITION_INCOMPLETE_CODE},
                }
            },
        )

    def test_concurrent_double_submit_converges_on_one_generation(self):
        def _workflow(run_id, *, owner_session=None):
            if run_id == "run-src-arw3a":
                return _source_projection(review_ready=True)
            if run_id == "run-review-arw3a":
                return _review_projection(revision=1)
            raise AssertionError(run_id)

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        ):
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_window(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=_workflow,
                    ):
                        page = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_review",
                                kwargs={"session_id": self.session.id},
                            )
                        )
                        token = _form_token(page.content.decode("utf-8"), cta=True)
                        start = threading.Event()
                        both_saw_g_absent = threading.Barrier(2)
                        statuses: list[int] = []
                        errors: list[BaseException] = []
                        ack_threads: list[int] = []
                        session_key = self.client.session.session_key
                        cookie_name = settings.SESSION_COOKIE_NAME
                        real_ack = journey_views_mod._acknowledge_incomplete_predecessor

                        def _ack_only_after_both_saw_g_absent(*args, **kwargs):
                            ack_threads.append(threading.get_ident())
                            both_saw_g_absent.wait(timeout=5)
                            return real_ack(*args, **kwargs)

                        def _worker():
                            close_old_connections()
                            start.wait(timeout=5)
                            try:
                                with patch(
                                    "importer.journey_views._dispatch_json_mutation",
                                    side_effect=Arw3aAutoDispositionTests._fake_auto_dispatch(
                                        self
                                    ),
                                ):
                                    client = self.client_class()
                                    client.cookies[cookie_name] = session_key
                                    response = client.post(
                                        reverse(
                                            "importer:crm_duplicate_journey_review",
                                            kwargs={"session_id": self.session.id},
                                        ),
                                        {
                                            "form_token": token,
                                            "auto_disposition_cta": "1",
                                        },
                                    )
                                    statuses.append(response.status_code)
                            except BaseException as exc:
                                errors.append(exc)
                            finally:
                                close_old_connections()

                        with patch(
                            "importer.journey_views._acknowledge_incomplete_predecessor",
                            side_effect=_ack_only_after_both_saw_g_absent,
                        ):
                            threads = [
                                threading.Thread(target=_worker),
                                threading.Thread(target=_worker),
                            ]
                            for thread in threads:
                                thread.start()
                            start.set()
                            for thread in threads:
                                thread.join(timeout=15)
                            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ack_threads)), 2)
        self.assertEqual(sorted(statuses), [302, 302])
        self.assertEqual(
            ApiMutation.objects.filter(
                logical_action_identity=self.identity,
                logical_action_generation=1,
            ).count(),
            1,
        )
