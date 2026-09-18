"""Phase 7C rem: Django auto-merge start UI + durable POST-only disposition.

Network-free Django tests for:
  - start form toggle + T≥90 fail-closed (no silent clamp)
  - durable root-attempt claim freezes auto_merge_min_confidence
  - GET never dispatches auto-disposition
  - expected_revision freezes before wire for exact-retry after local-save loss
  - progress POST applies auto-disposition once; all-auto → merge summary

Authority: completed_projects/crm_duplicate_operator_journey_redesign.md Phase 7C.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .api_client import EasyImportsApiClient, MutationReuseError
from .forms import CrmDuplicateJourneyForm
from .journey_views import (
    InvalidAutoMergeThresholdError,
    _auto_merge_pending,
    _claim_or_get_attempt_session,
    _form_auto_merge_threshold,
    _maybe_apply_auto_disposition,
    _merge_summary_counts,
    _normalize_auto_merge_intent,
    _root_attempt_intent,
    _session_auto_merge_threshold,
)
from .models import ApiMutation, CrmDuplicateJourneyAttemptClaim, ImportSession


def _connected_fake(*, duplicate_execution: str = "dry_run") -> list[dict]:
    return [
        {
            "connection_id": "crm_conn_test",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "status": "connected",
            "display_label": "Demo portal",
            "maximum_authorization": {
                "duplicate_execution": duplicate_execution,
                "reference_acquisition": "execute",
            },
        }
    ]


def _ready_projection(run_id: str = "run-src-7c") -> dict:
    return {
        "run_id": run_id,
        "revision": 3,
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 4,
        "status": "awaiting_review",
        "stage": "review_ready",
        "summary": {"duplicate_group_count": 2, "analyzed_record_count": 4},
        "decision": None,
        "review_handoff": {
            "handoff_id": "handoff-7c",
            "entity": "account",
            "group_count": 2,
            "binding_digest": "sha256:bind",
        },
        "effect_intent": None,
        "effect_grants": [],
        "target_provider_id": "fake",
        "duplicate_analysis_progress": {
            "stage": "complete",
            "review_ready": True,
            "review_window_ready": True,
        },
    }


class CrmDuplicateJourneyPhase7cFormTests(SimpleTestCase):
    def test_auto_merge_default_off_clears_threshold(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
                "auto_merge_min_confidence": "95",
            },
            connections=_connected_fake(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["auto_merge_min_confidence"])

    def test_auto_merge_enabled_accepts_floor_90(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "90",
            },
            connections=_connected_fake(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["auto_merge_min_confidence"], 90)

    def test_auto_merge_enabled_rejects_below_floor(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "89",
            },
            connections=_connected_fake(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("auto_merge_min_confidence", form.errors)

    def test_auto_merge_enabled_rejects_above_100(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "101",
            },
            connections=_connected_fake(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("auto_merge_min_confidence", form.errors)


class CrmDuplicateJourneyPhase7cHelperTests(SimpleTestCase):
    def test_intent_encodes_threshold(self):
        self.assertEqual(_normalize_auto_merge_intent(None), "none")
        self.assertEqual(_normalize_auto_merge_intent(90), "90")
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _normalize_auto_merge_intent(89)
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _normalize_auto_merge_intent(101)
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _normalize_auto_merge_intent(90.5)
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _normalize_auto_merge_intent("90.5")
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _normalize_auto_merge_intent(True)
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _normalize_auto_merge_intent("nope")
        with self.assertRaises(InvalidAutoMergeThresholdError):
            _session_auto_merge_threshold({"auto_merge_min_confidence": 89})
        # Whole float that is in range is accepted as integer.
        self.assertEqual(_normalize_auto_merge_intent(90.0), "90")
        off = _root_attempt_intent(
            source_mode="acquire_all",
            connection_id="c",
            entity_family="company",
            duplicate_execution_maximum="dry_run",
        )
        self.assertEqual(off["auto_merge_min_confidence"], "none")
        on = _root_attempt_intent(
            source_mode="acquire_all",
            connection_id="c",
            entity_family="company",
            duplicate_execution_maximum="dry_run",
            auto_merge_min_confidence=95,
        )
        self.assertEqual(on["auto_merge_min_confidence"], "95")
        self.assertNotEqual(off, on)

    def test_merge_summary_counts_split_auto_and_operator(self):
        reviewed = {
            "approved_merge_group_count": 3,
            "auto_approved_group_count": 2,
            "review_contract": "easyimports.crm.duplicate_reviewed_result.v2",
            "dispositions": [
                {
                    "disposition": "approved",
                    "decision_origin": "high_confidence_threshold",
                },
                {
                    "disposition": "approved",
                    "decision_origin": "high_confidence_threshold",
                },
                {"disposition": "approved", "decision_origin": "operator"},
            ],
        }
        counts = _merge_summary_counts(reviewed)
        self.assertEqual(counts["auto_approved_group_count"], 2)
        self.assertEqual(counts["operator_approved_group_count"], 1)

    def test_auto_merge_pending_helper(self):
        self.assertFalse(_auto_merge_pending({}))
        self.assertTrue(
            _auto_merge_pending({"auto_merge_min_confidence": 90})
        )
        self.assertFalse(
            _auto_merge_pending(
                {
                    "auto_merge_min_confidence": 90,
                    "auto_disposition_applied": True,
                }
            )
        )


class CrmDuplicateJourneyPhase7cClaimTests(TestCase):
    def test_claim_rejects_changed_auto_merge_threshold(self):
        owner = uuid4()
        request = MagicMock()
        request.session = {}
        with patch(
            "importer.journey_views.owner_id_for_request", return_value=owner
        ):
            journal = ImportSession.objects.create(
                owner_id=owner, product_key="crm.journey"
            )
            root = uuid4()
            first = _root_attempt_intent(
                source_mode="uploaded_population",
                connection_id="crm_conn_test",
                entity_family="company",
                duplicate_execution_maximum="dry_run",
                auto_merge_min_confidence=90,
            )

            def factory():
                return ImportSession.objects.create(
                    owner_id=owner,
                    product_key="easyimports.duplicate_resolution",
                    status=ImportSession.Status.CREATED,
                    options=dict(first),
                )

            session, created = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=first,
                factory=factory,
            )
            self.assertTrue(created)
            claim = CrmDuplicateJourneyAttemptClaim.objects.get(
                journal=journal, root_form_instance=root
            )
            self.assertEqual(claim.auto_merge_min_confidence, "90")

            changed = dict(first)
            changed["auto_merge_min_confidence"] = "95"
            with self.assertRaises(MutationReuseError):
                _claim_or_get_attempt_session(
                    request,
                    journal,
                    form_instance=root,
                    intent=changed,
                    factory=factory,
                )
            # Same intent still converges.
            again, created2 = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=first,
                factory=factory,
            )
            self.assertFalse(created2)
            self.assertEqual(again.id, session.id)


class CrmDuplicateJourneyPhase7cPageTests(TestCase):
    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_start_page_shows_auto_merge_controls(self):
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "crm_connections",
                    return_value={"connections": _connected_fake()},
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "crm_duplicate_journeys",
                        return_value={"journeys": []},
                    ):
                        response = self.client.get(
                            reverse("importer:crm_duplicate_journey")
                        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Auto-merge high-confidence groups", body)
        self.assertIn('name="auto_merge_high_confidence"', body)
        self.assertIn('min="90"', body)

    def _session_ready_for_auto(self, *, owner, threshold: int = 90):
        root = uuid4()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "redesign_phase": "4a",
                "run_id": "run-src-7c",
                "read_grant_id": "grant-7c",
                "apply_root_form_instance": str(root),
                "orchestrator_form_instance": str(root),
                "auto_merge_min_confidence": threshold,
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
            },
        )
        draft.options["mutation_journal_id"] = str(draft.id)
        draft.save(update_fields=["options", "updated_at"])
        return draft, root

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_get_progress_does_not_dispatch_auto_disposition(self):
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        draft, _root = self._session_ready_for_auto(owner=owner)
        dispatch = MagicMock()
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=_ready_projection(),
            ):
                with patch(
                    "importer.journey_views._dispatch_json_mutation",
                    dispatch,
                ):
                    with patch(
                        "importer.journey_views.store_workflow_projection",
                        return_value=None,
                    ):
                        response = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_progress",
                                kwargs={"session_id": draft.id},
                            )
                        )
        # RSF-1: window-ready progress GET opens review; GET still does not
        # journal auto-disposition.
        self.assertEqual(response.status_code, 302)
        self.assertIn("review", response["Location"])
        dispatch.assert_not_called()
        draft.refresh_from_db()
        self.assertFalse((draft.options or {}).get("auto_disposition_applied"))

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_get_review_redirects_when_auto_pending_without_dispatch(self):
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        draft, _root = self._session_ready_for_auto(owner=owner)
        dispatch = MagicMock()
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch(
                "importer.journey_views._dispatch_json_mutation",
                dispatch,
            ):
                response = self.client.get(
                    reverse(
                        "importer:crm_duplicate_journey_review",
                        kwargs={"session_id": draft.id},
                    )
                )
        self.assertEqual(response.status_code, 302)
        self.assertIn("progress", response["Location"])
        dispatch.assert_not_called()

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_post_progress_applies_auto_disposition_once_all_auto_to_merge(self):
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        draft, root = self._session_ready_for_auto(owner=owner)
        review_projection = {
            "run_id": "run-review-7c",
            "revision": 1,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "needs_decision",
            "stage": "awaiting_review",
            "summary": {},
            "decision": None,
            "review_handoff": None,
            "effect_intent": None,
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        auto_body = {
            "command_kind": "submit_duplicate_auto_disposition",
            "run_id": "run-review-7c",
            "expected_revision": 1,
            "applied_revision": 2,
            "auto_merge_min_confidence": 90,
            "auto_approved_group_count": 2,
            "manual_remaining_group_count": 0,
            "total_group_count": 2,
            "auto_approved_group_ids": ["g1", "g2"],
            "review_ready": True,
            "manual_queue_empty": True,
            "result_digest": "sha256:auto7c",
        }
        dispatch_calls: list[dict] = []

        def fake_dispatch(*, journal, mutation_kind, route, body, **kwargs):
            dispatch_calls.append(
                {
                    "mutation_kind": mutation_kind,
                    "route": route,
                    "body": dict(body),
                    "form_instance": kwargs.get("form_instance"),
                }
            )
            if mutation_kind == "crm_duplicate_implied_reference_authorization":
                return {"status": "accepted"}
            if mutation_kind == "start_review_workflow":
                return {"run_id": "run-review-7c", "status": "needs_decision"}
            if mutation_kind == "submit_duplicate_auto_disposition":
                return dict(auto_body)
            raise AssertionError(f"unexpected mutation {mutation_kind}")

        def workflow_side_effect(run_id, *, owner_session=None):
            if run_id == "run-src-7c":
                return _ready_projection()
            if run_id == "run-review-7c":
                return review_projection
            raise AssertionError(run_id)

        progress_url = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient, "workflow", side_effect=workflow_side_effect
            ):
                with patch(
                    "importer.journey_views._dispatch_json_mutation",
                    side_effect=fake_dispatch,
                ):
                    with patch(
                        "importer.journey_views.store_workflow_projection",
                        return_value=None,
                    ):
                        # Complete + only eligible remaining: stay on progress
                        # when auto_queued=1 so Continue can POST.
                        get_resp = self.client.get(progress_url + "?auto_queued=1")
                        self.assertEqual(get_resp.status_code, 200)
                        token = get_resp.context["form_token"]
                        auto_before = [
                            c
                            for c in dispatch_calls
                            if c["mutation_kind"]
                            == "submit_duplicate_auto_disposition"
                        ]
                        self.assertEqual(auto_before, [])
                        post_resp = self.client.post(
                            progress_url,
                            {"form_token": token},
                        )

        self.assertEqual(post_resp.status_code, 302)
        self.assertIn(
            reverse(
                "importer:crm_duplicate_journey_merge",
                kwargs={"session_id": draft.id},
            ),
            post_resp["Location"],
        )
        auto_calls = [
            c
            for c in dispatch_calls
            if c["mutation_kind"] == "submit_duplicate_auto_disposition"
        ]
        self.assertEqual(len(auto_calls), 1)
        self.assertEqual(auto_calls[0]["body"]["expected_revision"], 1)
        self.assertEqual(auto_calls[0]["body"]["auto_merge_min_confidence"], 90)
        draft.refresh_from_db()
        opts = draft.options or {}
        self.assertTrue(opts.get("auto_disposition_applied"))
        self.assertEqual(opts.get("auto_disposition_expected_revision"), 1)
        self.assertTrue(opts.get("manual_queue_empty"))

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_auto_disposition_freezes_revision_for_exact_retry(self):
        owner = uuid4()
        draft, root = self._session_ready_for_auto(owner=owner)
        journal = draft
        client = MagicMock()

        revisions_seen: list[int] = []

        def workflow_side_effect(run_id, *, owner_session=None):
            if run_id == "run-src-7c":
                return {
                    "run_id": run_id,
                    "revision": 3,
                    "review_handoff": {
                        "handoff_id": "handoff-7c",
                        "entity": "account",
                        "group_count": 1,
                        "binding_digest": "sha256:b",
                    },
                    "duplicate_analysis_progress": {
                        "stage": "complete",
                        "review_ready": True,
                        "review_window_ready": True,
                    },
                }
            if run_id == "run-review-7c":
                revisions_seen.append(11)
                return {"run_id": run_id, "revision": 11}
            raise AssertionError(run_id)

        client.workflow.side_effect = workflow_side_effect
        bodies: list[dict] = []

        def fake_dispatch(*, mutation_kind, body, **kwargs):
            if mutation_kind == "start_review_workflow":
                return {"run_id": "run-review-7c"}
            if mutation_kind == "submit_duplicate_auto_disposition":
                bodies.append(dict(body))
                return {
                    "command_kind": "submit_duplicate_auto_disposition",
                    "run_id": "run-review-7c",
                    "expected_revision": body["expected_revision"],
                    "applied_revision": int(body["expected_revision"]) + 1,
                    "auto_merge_min_confidence": 90,
                    "auto_approved_group_count": 1,
                    "manual_remaining_group_count": 0,
                    "total_group_count": 1,
                    "auto_approved_group_ids": ["g1"],
                    "review_ready": True,
                    "manual_queue_empty": True,
                    "result_digest": "sha256:r",
                }
            raise AssertionError(mutation_kind)

        with patch(
            "importer.journey_views.store_workflow_projection",
            return_value=None,
        ):
            with patch(
                "importer.journey_views._dispatch_json_mutation",
                side_effect=fake_dispatch,
            ):
                first = _maybe_apply_auto_disposition(
                    workflow_session=draft,
                    client=client,
                    owner_session="django-x",
                    source_run_id="run-src-7c",
                    journal=journal,
                    root_form_instance=root,
                )
                self.assertTrue(first.get("auto_disposition_applied"))
                frozen = int(first.get("auto_disposition_expected_revision"))
                self.assertEqual(bodies[0]["expected_revision"], frozen)
                self.assertEqual(frozen, 11)
                # Simulate commit-before-local-save: API completed, local flag lost.
                draft.options = {
                    **dict(draft.options or {}),
                    "auto_disposition_applied": False,
                    "auto_disposition_expected_revision": frozen,
                    "review_run_id": "run-review-7c",
                    "auto_merge_min_confidence": 90,
                }
                draft.save(update_fields=["options", "updated_at"])
                from .journey_views import _step_form_instance

                step = _step_form_instance(root, "auto-disposition")
                ApiMutation.objects.create(
                    session=journal,
                    form_instance=step,
                    mutation_kind="submit_duplicate_auto_disposition",
                    route="/v1/workflows/run-review-7c/duplicate-auto-disposition",
                    logical_action_identity="crm-dupe-4a:auto-disposition:retry",
                    state=ApiMutation.State.COMPLETED,
                    request_json={
                        "command_kind": "submit_duplicate_auto_disposition",
                        "expected_revision": frozen,
                        "auto_merge_min_confidence": 90,
                        "owner_session": "django-x",
                    },
                    response_json={
                        "command_kind": "submit_duplicate_auto_disposition",
                        "run_id": "run-review-7c",
                        "expected_revision": frozen,
                        "applied_revision": frozen + 1,
                        "auto_merge_min_confidence": 90,
                        "auto_approved_group_count": 1,
                        "manual_remaining_group_count": 0,
                        "total_group_count": 1,
                        "auto_approved_group_ids": ["g1"],
                        "review_ready": True,
                        "manual_queue_empty": True,
                        "result_digest": "sha256:r",
                    },
                    form_payload_digest="x",
                    request_digest="y",
                    idempotency_key=str(uuid4()),
                    resource_identity="run-review-7c",
                )
                recovered = _maybe_apply_auto_disposition(
                    workflow_session=draft,
                    client=client,
                    owner_session="django-x",
                    source_run_id="run-src-7c",
                    journal=journal,
                    root_form_instance=root,
                )
        self.assertTrue(recovered.get("auto_disposition_applied"))
        self.assertEqual(recovered.get("auto_approved_group_count"), 1)
        # Only one wire auto body from the first apply; recovery used journal.
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["expected_revision"], frozen)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_merge_summary_renders_auto_approved_counts(self):
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-src-merge",
                "review_run_id": "run-review-merge",
                "review_handoff_id": "handoff-merge",
                "auto_merge_min_confidence": 90,
                "auto_approved_group_count": 1,
                "auto_disposition_applied": True,
            },
        )
        reviewed = {
            "review_contract": "easyimports.crm.duplicate_reviewed_result.v2",
            "entity": "account",
            "total_group_count": 2,
            "decided_group_count": 2,
            "approved_merge_group_count": 2,
            "auto_approved_group_count": 1,
            "declined_group_count": 0,
            "quarantined_group_count": 0,
            "survivor_count": 2,
            "loser_count": 2,
            "analyzed_record_count": 4,
            "complete": True,
            "merge_plan_frozen": False,
            "decision_set_content_digest": "sha256:ds",
            "expected_revision": 3,
            "dispositions": [
                {
                    "group_id": "g1",
                    "disposition": "approved",
                    "decision_origin": "high_confidence_threshold",
                    "selected_survivor_id": "A1",
                    "group_revision": "r1",
                    "member_ids": ["A1", "A2"],
                },
                {
                    "group_id": "g2",
                    "disposition": "approved",
                    "decision_origin": "operator",
                    "selected_survivor_id": "A3",
                    "group_revision": "r2",
                    "member_ids": ["A3", "A4"],
                },
            ],
        }
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "duplicate_reviewed_result",
                return_value=reviewed,
            ):
                response = self.client.get(
                    reverse(
                        "importer:crm_duplicate_journey_merge",
                        kwargs={"session_id": draft.id},
                    )
                )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Auto-approved (high confidence)", body)
        self.assertIn("Approved by operator", body)
        self.assertIn("auto-approved (high confidence)", body)

    def test_form_threshold_helper_reads_cleaned_data(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "93",
            },
            connections=_connected_fake(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(_form_auto_merge_threshold(form), 93)
        self.assertEqual(_session_auto_merge_threshold({"auto_merge_min_confidence": 93}), 93)
