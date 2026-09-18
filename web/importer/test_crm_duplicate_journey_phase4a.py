"""Phase 4A Grade D rem: redesigned CRM duplicate start/map/progress UI.

Network-free Django tests for stable identities, capability ceilings, failure
routing, primary-page hygiene, and multi-upload lifecycle helpers.
"""

from __future__ import annotations

from datetime import timedelta
import threading
import re
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, close_old_connections, connection
from django.http import HttpResponseRedirect
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .api_client import (
    ApiOperationInProgressError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationReuseError,
    create_or_reuse_mutation,
)
from .forms import CrmDuplicateJourneyForm
from .journey_views import (
    RecordIdMappingForm,
    _adopt_winning_claim,
    _claim_or_get_attempt_session,
    _duplicate_execution_ceiling,
    _get_attempt_claim,
    _handle_crm_scan_start,
    _handle_uploaded_population_start,
    _annotate_journeys_for_resume,
    _intent_from_claim,
    _is_failed,
    _is_review_ready,
    _progress_message,
    _remember_dismissed_journey,
    _root_attempt_intent,
    _step_form_instance,
    _step_identity,
)
from .models import (
    ApiMutation,
    CrmDuplicateJourneyAttemptClaim,
    CrmDuplicateJourneyDismissal,
    ImportSession,
)


def _claim_for(journal: ImportSession, root) -> CrmDuplicateJourneyAttemptClaim:
    return CrmDuplicateJourneyAttemptClaim.objects.get(
        journal=journal,
        root_form_instance=root,
    )


def _upload_intent(
    *,
    connection_id: str = "crm_conn_test",
    entity_family: str = "company",
    ceiling: str = "execute",
) -> dict[str, str]:
    return _root_attempt_intent(
        source_mode="uploaded_population",
        connection_id=connection_id,
        entity_family=entity_family,
        duplicate_execution_maximum=ceiling,
    )


def _scan_intent(
    *,
    connection_id: str = "crm_conn_test",
    entity_family: str = "company",
    ceiling: str = "dry_run",
) -> dict[str, str]:
    return _root_attempt_intent(
        source_mode="acquire_all",
        connection_id=connection_id,
        entity_family=entity_family,
        duplicate_execution_maximum=ceiling,
    )


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


class CrmDuplicateJourneyPhase4aFormTests(SimpleTestCase):
    def test_primary_source_choices_are_scan_and_uploaded_population(self):
        form = CrmDuplicateJourneyForm(connections=_connected_fake())
        values = [value for value, _label in form.fields["source_mode"].choices]
        self.assertEqual(values, ["acquire_all", "uploaded_population"])
        self.assertIn("population_file", form.fields)
        self.assertIn("form_token", form.fields)
        self.assertFalse(form.fields["duplicate_execution_maximum"].required)

    def test_scan_mode_does_not_require_upload(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
            },
            connections=_connected_fake(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        # Posted merge ceiling is ignored (capability applied in the view).
        self.assertIsNone(form.cleaned_data["duplicate_execution_maximum"])

    def test_uploaded_population_requires_ordinary_file(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "uploaded_population",
                "form_token": "token",
            },
            connections=_connected_fake(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("population_file", form.errors)

    def test_uploaded_population_accepts_csv_without_group_columns(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "uploaded_population",
                "form_token": "token",
            },
            files={
                "population_file": SimpleUploadedFile(
                    "records.csv",
                    b"Id,Name\nA1,One\nA2,Two\n",
                    content_type="text/csv",
                )
            },
            connections=_connected_fake(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(form.cleaned_data["population_file"])
        self.assertIsNone(form.cleaned_data.get("candidate_groups"))

    def test_population_help_text_omits_group_id_member_id(self):
        form = CrmDuplicateJourneyForm(connections=_connected_fake())
        help_text = form.fields["population_file"].help_text
        self.assertNotIn("group_id", help_text)
        self.assertNotIn("member_id", help_text)

    def test_record_id_mapping_form_requires_column(self):
        form = RecordIdMappingForm(
            data={"form_token": "t"},
            headers=["Id", "Name"],
            suggested="Id",
        )
        self.assertFalse(form.is_valid())
        form = RecordIdMappingForm(
            data={"source_column": "Id", "form_token": "t"},
            headers=["Id", "Name"],
            suggested="Id",
        )
        self.assertTrue(form.is_valid(), form.errors)


class CrmDuplicateJourneyPhase4aHelperTests(SimpleTestCase):
    def test_resume_effect_wire_requests_async_processing(self):
        client = EasyImportsApiClient()
        http = MagicMock()
        http.post.return_value = MagicMock(status_code=202, content=b"{}")
        client.http = http
        mutation = MagicMock()
        mutation.mutation_kind = "resume_effect"
        mutation.request_kind = "json"
        mutation.route = "/v1/workflows/run-async/effect-resumptions"
        mutation.idempotency_key = "resume-async-1"
        mutation.request_json = {
            "expected_revision": 7,
            "owner_session": "django-owner",
        }
        mutation.multipart_metadata = None
        mutation.session = MagicMock(owner_id=None)

        client._send(mutation, file_path=None)

        self.assertEqual(
            http.post.call_args.kwargs["headers"].get("Prefer"),
            "respond-async",
        )

    def test_implied_authorization_wire_requests_async_processing(self):
        client = EasyImportsApiClient()
        http = MagicMock()
        http.post.return_value = MagicMock(status_code=202, content=b"{}")
        client.http = http
        mutation = MagicMock()
        mutation.mutation_kind = "crm_duplicate_implied_reference_authorization"
        mutation.request_kind = "json"
        mutation.route = (
            "/v1/crm/duplicate-read-grants/grant-1/implied-reference-authorization"
        )
        mutation.idempotency_key = "apply-async-1"
        mutation.request_json = {
            "run_id": "run-async-1",
            "owner_session": "django-owner",
        }
        mutation.multipart_metadata = None
        mutation.session = MagicMock(owner_id=None)

        client._send(mutation, file_path=None)

        self.assertEqual(
            http.post.call_args.kwargs["headers"].get("Prefer"),
            "respond-async",
        )

    def test_capability_ceiling_from_connection(self):
        self.assertEqual(
            _duplicate_execution_ceiling(
                {"maximum_authorization": {"duplicate_execution": "execute"}}
            ),
            "execute",
        )
        self.assertEqual(
            _duplicate_execution_ceiling(
                {"maximum_authorization": {"duplicate_execution": "dry_run"}}
            ),
            "dry_run",
        )
        self.assertEqual(_duplicate_execution_ceiling({}), "dry_run")
        self.assertEqual(_duplicate_execution_ceiling(None), "dry_run")

    def test_failed_is_not_review_ready(self):
        failed = {"status": "failed", "stage": "reference_acquisition", "summary": {}}
        self.assertTrue(_is_failed(failed))
        self.assertFalse(_is_review_ready(failed))
        message = _progress_message(failed)
        self.assertIn("failed", message.lower())
        self.assertNotIn("Analysis complete", message)

    def test_zero_groups_is_review_ready_terminal(self):
        zero = {
            "status": "succeeded",
            "stage": "no_duplicate_groups",
            "summary": {"no_duplicate_groups": 1, "analyzed_record_count": 3},
        }
        self.assertTrue(_is_review_ready(zero))
        self.assertIn("No duplicate groups found", _progress_message(zero))

    def test_paused_verification_explains_bounded_read_continuation(self):
        paused = {
            "status": "paused_verification",
            "stage": "paused_verification",
            "summary": {"analyzed_record_count": 500},
        }
        message = _progress_message(paused)
        self.assertIn("safe CRM read batch", message)
        self.assertIn("500 records", message)

    def test_step_identity_is_stable_for_form_instance(self):
        form_instance = UUID("00000000-0000-0000-0000-0000000000aa")
        first = _step_identity(
            step="journey",
            owner_session="django-owner",
            form_instance=form_instance,
            resource="scan:c1:company",
        )
        second = _step_identity(
            step="journey",
            owner_session="django-owner",
            form_instance=form_instance,
            resource="scan:c1:company",
        )
        self.assertEqual(first, second)
        self.assertIn("00000000-0000-0000-0000-0000000000aa", first)

    def test_step_form_instances_are_distinct_and_stable(self):
        root = UUID("00000000-0000-0000-0000-0000000000bb")
        journey = _step_form_instance(root, "journey")
        grant = _step_form_instance(root, "grant")
        apply = _step_form_instance(root, "apply")
        self.assertNotEqual(journey, grant)
        self.assertNotEqual(grant, apply)
        self.assertEqual(journey, _step_form_instance(root, "journey"))
        self.assertEqual(apply, _step_form_instance(root, "apply"))


class CrmDuplicateJourneyPhase4aJournalTests(TestCase):
    """Unmocked journal-level interruption / multi-step form_instance tests."""

    def test_multi_step_same_root_form_instance_does_not_conflict(self):
        """Journey then grant with distinct step form instances succeeds."""

        journal = ImportSession.objects.create(
            owner_id=uuid4(),
            product_key="crm.journey",
        )
        root = uuid4()
        owner = f"django-{journal.owner_id}"
        journey_body = {
            "connection_id": "c1",
            "entity_family": "company",
            "source_mode": "acquire_all",
            "duplicate_execution_maximum": "execute",
            "owner_session": owner,
        }
        grant_body = {
            "population_source": "crm_scan",
            "connection_id": "c1",
            "entity_family": "company",
            "owner_session": owner,
        }
        first = create_or_reuse_mutation(
            session=journal,
            form_instance=_step_form_instance(root, "journey"),
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity=_step_identity(
                step="journey",
                owner_session=owner,
                form_instance=root,
                resource="scan:c1:company",
            ),
            request_json=journey_body,
        )
        second = create_or_reuse_mutation(
            session=journal,
            form_instance=_step_form_instance(root, "grant"),
            mutation_kind="crm_duplicate_read_grant_create",
            route="/v1/crm/duplicate-read-grants",
            logical_action_identity=_step_identity(
                step="grant",
                owner_session=owner,
                form_instance=root,
                resource="c1:company",
            ),
            request_json=grant_body,
        )
        self.assertNotEqual(first.id, second.id)
        # Exact retry of journey converges on the same row.
        again = create_or_reuse_mutation(
            session=journal,
            form_instance=_step_form_instance(root, "journey"),
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity=_step_identity(
                step="journey",
                owner_session=owner,
                form_instance=root,
                resource="scan:c1:company",
            ),
            request_json=journey_body,
        )
        self.assertEqual(again.id, first.id)
        self.assertEqual(ApiMutation.objects.filter(session=journal).count(), 2)

    def test_reusing_root_form_instance_across_steps_conflicts(self):
        """Documents the journal rule that motivated per-step form instances."""

        journal = ImportSession.objects.create(
            owner_id=uuid4(),
            product_key="crm.journey",
        )
        root = uuid4()
        owner = f"django-{journal.owner_id}"
        create_or_reuse_mutation(
            session=journal,
            form_instance=root,
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity="step-a",
            request_json={"a": 1, "owner_session": owner},
        )
        with self.assertRaises(MutationReuseError):
            create_or_reuse_mutation(
                session=journal,
                form_instance=root,
                mutation_kind="crm_duplicate_read_grant_create",
                route="/v1/crm/duplicate-read-grants",
                logical_action_identity="step-b",
                request_json={"b": 2, "owner_session": owner},
            )

    def test_atomic_claim_freezes_intent_and_serializes_retries(self):
        """Same-root exact retry reuses one draft and freezes intent on the map."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        request = MagicMock()
        request.session = {}
        creates: list[ImportSession] = []
        intent = _upload_intent()

        def factory():
            draft = ImportSession.objects.create(
                owner_id=owner,
                product_key="easyimports.duplicate_resolution",
                status=ImportSession.Status.CREATED,
                options={
                    "population_source": "uploaded_population",
                    **intent,
                },
            )
            creates.append(draft)
            return draft

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            first, created1 = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=intent,
                factory=factory,
            )
            second, created2 = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=intent,
                factory=factory,
            )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(creates), 1)
        claim = _claim_for(journal, root)
        self.assertEqual(claim.claimed_session_id, first.id)
        self.assertEqual(_intent_from_claim(claim), intent)

    def test_atomic_claim_rejects_changed_source_or_connection(self):
        """Same token cannot resume upload as scan or switch connections."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        request = MagicMock()
        upload = _upload_intent(connection_id="connection-A", ceiling="execute")

        def factory_upload():
            return ImportSession.objects.create(
                owner_id=owner,
                product_key="easyimports.duplicate_resolution",
                status=ImportSession.Status.CREATED,
                options={"population_source": "uploaded_population", **upload},
            )

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            claimed, created = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=upload,
                factory=factory_upload,
            )
            self.assertTrue(created)
            scan_other = _scan_intent(connection_id="connection-B", ceiling="execute")
            with self.assertRaises(MutationReuseError) as ctx:
                _claim_or_get_attempt_session(
                    request,
                    journal,
                    form_instance=root,
                    intent=scan_other,
                    factory=factory_upload,
                )
            self.assertIn("different source", str(ctx.exception).lower())
            # Unchanged draft; no second session.
            self.assertEqual(
                ImportSession.objects.filter(
                    product_key="easyimports.duplicate_resolution",
                    owner_id=owner,
                ).count(),
                1,
            )
            claimed.refresh_from_db()
            self.assertEqual(claimed.options.get("source_mode"), "uploaded_population")
            self.assertEqual(claimed.options.get("connection_id"), "connection-A")

    def test_upload_handler_claims_before_upload_and_resumes(self):
        """Production upload handler: fail mid-upload, exact retry reuses draft."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        form = MagicMock()
        form.cleaned_data = {
            "connection_id": "crm_conn_test",
            "entity_family": "company",
            "population_file": SimpleUploadedFile(
                "records.csv", b"Id\nA1\n", content_type="text/csv"
            ),
        }
        request = MagicMock()
        client = MagicMock()
        call_count = {"n": 0}

        def upload_once(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ApiUnavailableError("API blip after draft claim")
            mock = MagicMock()
            mock.response = {"upload_id": "upl-resume", "filename": "records.csv"}
            return mock

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            with patch(
                "importer.journey_views.save_and_register_upload",
                side_effect=upload_once,
            ):
                with self.assertRaises(ApiUnavailableError):
                    _handle_uploaded_population_start(
                        request,
                        form=form,
                        journal=journal,
                        client=client,
                        owner_session=f"django-{owner}",
                        form_instance=root,
                        execution_ceiling="execute",
                    )
                claim = _claim_for(journal, root)
                claimed_id = str(claim.claimed_session_id)
                self.assertEqual(
                    _intent_from_claim(claim),
                    _upload_intent(connection_id="crm_conn_test", ceiling="execute"),
                )
                drafts_after_fail = ImportSession.objects.filter(
                    product_key="easyimports.duplicate_resolution",
                    owner_id=owner,
                )
                self.assertEqual(drafts_after_fail.count(), 1)
                self.assertEqual(str(drafts_after_fail.get().id), claimed_id)

                # Exact retry with same root reuses the claimed draft.
                pop_body = {
                    "population_upload_id": "pop-resume",
                    "upload_id": "upl-resume",
                    "source_headers": ["Id"],
                    "preview_rows": [["A1"]],
                    "suggested_source_column": "Id",
                    "filename": "records.csv",
                    "row_count": 1,
                }
                with patch(
                    "importer.journey_views._dispatch_json_mutation",
                    return_value=pop_body,
                ):
                    with patch(
                        "importer.journey_views.redirect",
                        side_effect=lambda *a, **k: ("redirect", a, k),
                    ) as _redir:
                        result = _handle_uploaded_population_start(
                            request,
                            form=form,
                            journal=journal,
                            client=client,
                            owner_session=f"django-{owner}",
                            form_instance=root,
                            execution_ceiling="execute",
                        )
                self.assertEqual(result[0], "redirect")
                self.assertEqual(
                    ImportSession.objects.filter(
                        product_key="easyimports.duplicate_resolution",
                        owner_id=owner,
                    ).count(),
                    1,
                )
                draft = ImportSession.objects.get(pk=claimed_id)
                self.assertEqual(
                    draft.options.get("population_upload_id"), "pop-resume"
                )

    def test_upload_interrupted_rejects_scan_retry_on_other_connection(self):
        """Production-handler reproduction: upload on A cannot resume as scan on B."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        request = MagicMock()
        client = MagicMock()
        upload_form = MagicMock()
        upload_form.cleaned_data = {
            "connection_id": "connection-A",
            "entity_family": "company",
            "population_file": SimpleUploadedFile(
                "records.csv", b"Id\nA1\n", content_type="text/csv"
            ),
        }
        scan_form = MagicMock()
        scan_form.cleaned_data = {
            "connection_id": "connection-B",
            "entity_family": "company",
        }

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            with patch(
                "importer.journey_views.save_and_register_upload",
                side_effect=ApiUnavailableError("interrupted after claim"),
            ):
                with self.assertRaises(ApiUnavailableError):
                    _handle_uploaded_population_start(
                        request,
                        form=upload_form,
                        journal=journal,
                        client=client,
                        owner_session=f"django-{owner}",
                        form_instance=root,
                        execution_ceiling="execute",
                    )

            with self.assertRaises(MutationReuseError):
                _handle_crm_scan_start(
                    request,
                    form=scan_form,
                    journal=journal,
                    client=client,
                    owner_session=f"django-{owner}",
                    form_instance=root,
                    execution_ceiling="execute",
                )

            sessions = list(
                ImportSession.objects.filter(
                    product_key="easyimports.duplicate_resolution",
                    owner_id=owner,
                )
            )
            self.assertEqual(len(sessions), 1)
            draft = sessions[0]
            self.assertEqual(draft.options.get("connection_id"), "connection-A")
            self.assertEqual(draft.options.get("source_mode"), "uploaded_population")
            self.assertEqual(
                draft.options.get("population_source"), "uploaded_population"
            )
            self.assertIsNone(draft.options.get("run_id"))
            # Scan must not have dispatched journey mutations after reject.
            client.workflow.assert_not_called()

    def test_scan_handler_claims_shell_before_projection_fetch(self):
        """Production scan handler: projection failure still leaves claimed session."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        form = MagicMock()
        form.cleaned_data = {
            "connection_id": "crm_conn_test",
            "entity_family": "company",
        }
        request = MagicMock()
        journey_resp = {
            "journey_id": "j1",
            "connection_id": "crm_conn_test",
            "entity_family": "company",
            "source_mode": "acquire_all",
            "run_id": "run-scan-1",
            "status": "workflow_started",
            "provider_key": "fake",
            "target_provider_id": "fake-crm-v1",
            "candidate_group_count": 0,
            "acquired_record_count": 0,
        }
        grant_resp = {"grant_id": "g1"}
        apply_resp = {"run_id": "run-scan-1", "track": "reference_acquisition"}

        def dispatch_side_effect(**kwargs):
            kind = kwargs["mutation_kind"]
            if kind == "crm_duplicate_journey_start":
                return journey_resp
            if kind == "crm_duplicate_read_grant_create":
                return grant_resp
            if kind == "crm_duplicate_implied_reference_authorization":
                return apply_resp
            raise AssertionError(kind)

        client = MagicMock()
        client.workflow.side_effect = ApiUnavailableError("projection blip")

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            with patch(
                "importer.journey_views._dispatch_json_mutation",
                side_effect=dispatch_side_effect,
            ):
                with self.assertRaises(ApiUnavailableError):
                    _handle_crm_scan_start(
                        request,
                        form=form,
                        journal=journal,
                        client=client,
                        owner_session=f"django-{owner}",
                        form_instance=root,
                        execution_ceiling="dry_run",
                    )
                claim = _claim_for(journal, root)
                shell = ImportSession.objects.get(pk=claim.claimed_session_id)
                self.assertEqual(shell.options.get("run_id"), "run-scan-1")
                self.assertEqual(shell.options.get("read_grant_id"), "g1")
                self.assertEqual(
                    shell.options.get("mutation_journal_id"), str(journal.id)
                )
                self.assertEqual(
                    _intent_from_claim(claim),
                    _scan_intent(connection_id="crm_conn_test", ceiling="dry_run"),
                )

                # Exact retry reuses shell; does not create a second local session.
                client.workflow.side_effect = None
                client.workflow.return_value = {
                    "run_id": "run-scan-1",
                    "revision": 3,
                    "workflow_key": "easyimports.duplicate_resolution",
                    "workflow_version": 4,
                    "status": "awaiting_review",
                    "stage": "review_ready",
                    "summary": {},
                    "decision": None,
                    "review_handoff": {"group_count": 1},
                    "effect_intent": None,
                    "effect_grants": [],
                    "target_provider_id": "fake-crm-v1",
                }
                with patch(
                    "importer.journey_views.redirect",
                    side_effect=lambda *a, **k: ("redirect", a, k),
                ):
                    result = _handle_crm_scan_start(
                        request,
                        form=form,
                        journal=journal,
                        client=client,
                        owner_session=f"django-{owner}",
                        form_instance=root,
                        execution_ceiling="dry_run",
                    )
                self.assertEqual(result[0], "redirect")
                self.assertEqual(
                    ImportSession.objects.filter(
                        product_key="easyimports.duplicate_resolution",
                        owner_id=owner,
                    ).count(),
                    1,
                )
                claim = _claim_for(journal, root)
                self.assertEqual(
                    _intent_from_claim(claim),
                    _scan_intent(connection_id="crm_conn_test", ceiling="dry_run"),
                )

    def test_scan_handler_redirects_to_progress_when_apply_is_pending(self):
        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        form = MagicMock()
        form.cleaned_data = {
            "connection_id": "crm_conn_test",
            "entity_family": "company",
        }
        request = MagicMock()
        journey_resp = {
            "journey_id": "j-pending",
            "connection_id": "crm_conn_test",
            "entity_family": "company",
            "source_mode": "acquire_all",
            "run_id": "run-pending-1",
            "status": "workflow_started",
            "provider_key": "fake",
            "target_provider_id": "fake-crm-v1",
            "candidate_group_count": 0,
            "acquired_record_count": 0,
        }

        def dispatch_side_effect(**kwargs):
            kind = kwargs["mutation_kind"]
            if kind == "crm_duplicate_journey_start":
                return journey_resp
            if kind == "crm_duplicate_read_grant_create":
                return {"grant_id": "g-pending"}
            if kind == "crm_duplicate_implied_reference_authorization":
                raise ApiOperationInProgressError(
                    "EasyImports is processing this action."
                )
            raise AssertionError(kind)

        client = MagicMock()
        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            with patch(
                "importer.journey_views._dispatch_json_mutation",
                side_effect=dispatch_side_effect,
            ):
                with patch(
                    "importer.journey_views.redirect",
                    side_effect=lambda *a, **k: ("redirect", a, k),
                ):
                    result = _handle_crm_scan_start(
                        request,
                        form=form,
                        journal=journal,
                        client=client,
                        owner_session=f"django-{owner}",
                        form_instance=root,
                        execution_ceiling="dry_run",
                    )
        self.assertEqual(result[0], "redirect")
        self.assertEqual(result[1][0], "importer:crm_duplicate_journey_progress")
        claim = _claim_for(journal, root)
        shell = ImportSession.objects.get(pk=claim.claimed_session_id)
        self.assertEqual(shell.options.get("run_id"), "run-pending-1")
        self.assertEqual(shell.options.get("read_grant_id"), "g-pending")
        self.assertEqual(shell.options.get("mutation_journal_id"), str(journal.id))
        client.workflow.assert_not_called()


class CrmDuplicateJourneyPhase4aConcurrentClaimTests(TransactionTestCase):
    """Contention coverage for the unique attempt-claim table."""

    def test_factory_second_write_error_does_not_orphan_on_retry(self):
        """OperationalError after the first factory write must not leave orphans.

        Upload drafts historically did create() then save() for
        mutation_journal_id. If the second write hit a SQLite lock and claim
        retried the whole factory, the first session stayed behind. Factory
        attempts run under transaction.atomic(), and the production upload
        factory is a single INSERT.
        """

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        intent = _upload_intent()
        request = MagicMock()
        attempts = {"n": 0}

        def multi_write_factory() -> ImportSession:
            """Reproduces the old two-write factory; second write fails once."""

            attempts["n"] += 1
            draft = ImportSession.objects.create(
                owner_id=owner,
                product_key="easyimports.duplicate_resolution",
                status=ImportSession.Status.CREATED,
                options={
                    "population_source": "uploaded_population",
                    "mutation_journal_id": "",
                    **intent,
                },
            )
            draft.options = {
                **dict(draft.options or {}),
                "mutation_journal_id": str(draft.id),
            }
            if attempts["n"] == 1:
                # First write already committed if not wrapped; second write fails.
                draft.save(update_fields=["options", "updated_at"])
                raise OperationalError("database is locked")
            draft.save(update_fields=["options", "updated_at"])
            return draft

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            session, created = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=intent,
                factory=multi_write_factory,
            )

        self.assertTrue(created)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(
            ImportSession.objects.filter(
                product_key="easyimports.duplicate_resolution",
                owner_id=owner,
            ).count(),
            1,
        )
        self.assertEqual(session.options.get("mutation_journal_id"), str(session.id))
        claim = _claim_for(journal, root)
        self.assertEqual(claim.claimed_session_id, session.id)

    def test_upload_draft_factory_is_single_insert(self):
        """Production upload factory freezes journal id without a second save."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        form = MagicMock()
        form.cleaned_data = {
            "connection_id": "crm_conn_test",
            "entity_family": "company",
            "population_file": SimpleUploadedFile(
                "records.csv", b"Id\nA1\n", content_type="text/csv"
            ),
        }
        request = MagicMock()
        client = MagicMock()
        save_calls = {"n": 0}
        original_save = ImportSession.save

        def counting_save(self, *args, **kwargs):
            save_calls["n"] += 1
            return original_save(self, *args, **kwargs)

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            with patch.object(ImportSession, "save", counting_save):
                with patch(
                    "importer.journey_views.save_and_register_upload",
                    side_effect=ApiUnavailableError("stop after claim"),
                ):
                    with self.assertRaises(ApiUnavailableError):
                        _handle_uploaded_population_start(
                            request,
                            form=form,
                            journal=journal,
                            client=client,
                            owner_session=f"django-{owner}",
                            form_instance=root,
                            execution_ceiling="execute",
                        )

        drafts = ImportSession.objects.filter(
            product_key="easyimports.duplicate_resolution",
            owner_id=owner,
        )
        self.assertEqual(drafts.count(), 1)
        draft = drafts.get()
        # create() may invoke save once; a follow-up options save would be 2+.
        self.assertLessEqual(save_calls["n"], 1)
        self.assertEqual(draft.options.get("mutation_journal_id"), str(draft.id))

    def test_peer_lookup_lock_during_claim_install_is_retryable(self):
        """OperationalError on peer lookup after claim insert must not leak."""

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        intent = _upload_intent()
        peer = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.CREATED,
            options={"population_source": "uploaded_population", **intent},
        )
        CrmDuplicateJourneyAttemptClaim.objects.create(
            journal=journal,
            root_form_instance=root,
            claimed_session=peer,
            source_mode=intent["source_mode"],
            connection_id=intent["connection_id"],
            entity_family=intent["entity_family"],
            duplicate_execution_maximum=intent["duplicate_execution_maximum"],
        )
        orphan = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.CREATED,
            options={"population_source": "uploaded_population", **intent},
        )
        lookups = {"n": 0}

        def flaky_get(journal_arg, *, form_instance):
            lookups["n"] += 1
            if lookups["n"] <= 2:
                raise OperationalError(
                    "database table is locked: "
                    "importer_crmduplicatejourneyattemptclaim"
                )
            return _get_attempt_claim(journal_arg, form_instance=form_instance)

        with patch(
            "importer.journey_views._get_attempt_claim",
            side_effect=flaky_get,
        ):
            session, created = _adopt_winning_claim(
                orphan=orphan,
                owner_id=owner,
                journal=journal,
                root=root,
                posted=intent,
            )

        self.assertFalse(created)
        self.assertEqual(session.id, peer.id)
        self.assertFalse(ImportSession.objects.filter(pk=orphan.pk).exists())
        self.assertGreaterEqual(lookups["n"], 3)

    def test_claim_install_race_drops_orphan_and_reuses_winner(self):
        """Losing the unique-row INSERT race drops the orphan draft.

        Peer installs the claim row during factory (same state a second
        connection would observe after the winner commits). Loser must not leave
        an extra session behind.
        """

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        intent = _upload_intent()
        request = MagicMock()
        winner_id: dict[str, str] = {}

        def factory_loses_install_race() -> ImportSession:
            peer = ImportSession.objects.create(
                owner_id=owner,
                product_key="easyimports.duplicate_resolution",
                status=ImportSession.Status.CREATED,
                options={"population_source": "uploaded_population", **intent},
            )
            winner_id["id"] = str(peer.id)
            CrmDuplicateJourneyAttemptClaim.objects.create(
                journal=journal,
                root_form_instance=root,
                claimed_session=peer,
                source_mode=intent["source_mode"],
                connection_id=intent["connection_id"],
                entity_family=intent["entity_family"],
                duplicate_execution_maximum=intent["duplicate_execution_maximum"],
            )
            orphan = ImportSession.objects.create(
                owner_id=owner,
                product_key="easyimports.duplicate_resolution",
                status=ImportSession.Status.CREATED,
                options={"population_source": "uploaded_population", **intent},
            )
            return orphan

        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=owner,
        ):
            session, created = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=intent,
                factory=factory_loses_install_race,
            )

        self.assertFalse(created)
        self.assertEqual(str(session.id), winner_id["id"])
        self.assertEqual(
            ImportSession.objects.filter(
                product_key="easyimports.duplicate_resolution",
                owner_id=owner,
            ).count(),
            1,
        )
        claim = _claim_for(journal, root)
        self.assertEqual(str(claim.claimed_session_id), winner_id["id"])
        self.assertEqual(_intent_from_claim(claim), intent)

    def test_atomic_claim_serializes_concurrent_database_connections(self):
        """Two DB connections race the unique claim INSERT and converge.

        Drafts are pre-created so the race is only on
        ``CrmDuplicateJourneyAttemptClaim`` (not multi-write factories). Unique
        constraint + orphan adopt leave exactly one live session.
        """

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            product_key="crm.journey",
        )
        root = uuid4()
        intent = _upload_intent()
        prebuilt = [
            ImportSession.objects.create(
                owner_id=owner,
                product_key="easyimports.duplicate_resolution",
                status=ImportSession.Status.CREATED,
                options={"population_source": "uploaded_population", **intent},
            )
            for _ in range(2)
        ]
        draft_queue = list(prebuilt)
        queue_lock = threading.Lock()
        # Start claim together, then rendezvous inside factory so both workers
        # pass the empty-map lookup before either installs the unique claim.
        # Without the factory barrier, one can claim before the other finishes
        # lookup; the second reuses the winner and never consumes its prebuilt
        # draft — the leftover fixture is not a production orphan.
        start_barrier = threading.Barrier(2, timeout=10)
        factory_barrier = threading.Barrier(2, timeout=10)
        results: list[tuple[str, bool]] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def _enable_sqlite_busy_timeout() -> None:
            connection.ensure_connection()
            if connection.vendor == "sqlite" and connection.connection is not None:
                connection.connection.execute("PRAGMA busy_timeout = 20000")

        def factory() -> ImportSession:
            with queue_lock:
                draft = draft_queue.pop(0)
            factory_barrier.wait()
            return draft

        def worker() -> None:
            close_old_connections()
            _enable_sqlite_busy_timeout()
            request = MagicMock()
            try:
                start_barrier.wait()
                with patch(
                    "importer.journey_views.owner_id_for_request",
                    return_value=owner,
                ):
                    session, created = _claim_or_get_attempt_session(
                        request,
                        journal,
                        form_instance=root,
                        intent=intent,
                        factory=factory,
                    )
                with result_lock:
                    results.append((str(session.id), created))
            except BaseException as exc:  # noqa: BLE001 — collect for main thread
                with result_lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())

        self.assertEqual(errors, [], msg=f"concurrent claim errors: {errors!r}")
        self.assertEqual(len(results), 2, results)
        session_ids = {session_id for session_id, _created in results}
        self.assertEqual(len(session_ids), 1, results)
        created_flags = [created for _session_id, created in results]
        self.assertEqual(created_flags.count(True), 1, results)
        self.assertEqual(created_flags.count(False), 1, results)
        claim = _claim_for(journal, root)
        self.assertEqual(str(claim.claimed_session_id), next(iter(session_ids)))
        self.assertEqual(_intent_from_claim(claim), intent)
        self.assertEqual(
            ImportSession.objects.filter(
                product_key="easyimports.duplicate_resolution",
                owner_id=owner,
            ).count(),
            1,
        )
        self.assertEqual(
            CrmDuplicateJourneyAttemptClaim.objects.filter(journal=journal).count(),
            1,
        )


class CrmDuplicateJourneyPhase4aPageTests(TestCase):
    def _paused_resume_fixture(self, suffix: str):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        run_id = f"run-paused-{suffix}"
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": run_id,
                "read_grant_id": f"grant-paused-{suffix}",
            },
        )
        projection = {
            "run_id": run_id,
            "revision": 9,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "paused_verification",
            "stage": "paused_verification",
            "summary": {"analyzed_record_count": 500},
            "decision": None,
            "review_handoff": None,
            "effect_intent": None,
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        return owner, draft, projection, route

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_start_page_hygiene_and_two_source_panels(self):
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
        self.assertIn("Where should EasyImports look?", body)
        self.assertIn("Find duplicates in CRM", body)
        self.assertIn("Upload records to analyze", body)
        self.assertIn('name="form_token"', body)
        self.assertNotIn("Maximum CRM change level", body)
        self.assertNotIn("I already know the groups", body)
        self.assertNotIn('name="reference_acquisition"', body)
        # Frozen screen: no group_id/member_id vocabulary on primary page.
        self.assertNotIn("group_id", body)
        self.assertNotIn("member_id", body)
        # Pre-grouped / selected-ID controls are not in the primary DOM.
        self.assertNotIn('data-source-mode-panel="candidate_upload"', body)
        self.assertNotIn('data-source-mode-panel="selected_ids"', body)
        self.assertNotIn("id_candidate_groups_file", body)
        self.assertNotIn("id_candidate_groups_json", body)
        self.assertIn("crm-dupe-starting-status", body)
        self.assertIn("Starting read-only duplicate discovery", body)
        self.assertIn("No merge or CRM write is authorized", body)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_restored_start_page_conflict_gets_fresh_token_for_new_attempt(self):
        api_patches = (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": _connected_fake()},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": []},
            ),
        )
        with api_patches[0], api_patches[1], api_patches[2], api_patches[3]:
            page = self.client.get(reverse("importer:crm_duplicate_journey"))
            old_token = re.search(
                r'name="form_token" value="([^"]+)"',
                page.content.decode("utf-8"),
            ).group(1)
            with patch(
                "importer.journey_views._handle_crm_scan_start",
                side_effect=MutationReuseError(
                    "This logical action was already submitted with different values."
                ),
            ):
                conflicted = self.client.post(
                    reverse("importer:crm_duplicate_journey"),
                    data={
                        "connection_id": "crm_conn_test",
                        "entity_family": "company",
                        "source_mode": "acquire_all",
                        "form_token": old_token,
                    },
                )

            self.assertEqual(conflicted.status_code, 200)
            conflict_body = conflicted.content.decode("utf-8")
            self.assertIn("belonged to an earlier duplicate attempt", conflict_body)
            self.assertNotIn("already submitted with different values", conflict_body)
            fresh_token = re.search(
                r'name="form_token" value="([^"]+)"', conflict_body
            ).group(1)
            self.assertNotEqual(fresh_token, old_token)
            self.assertIn("no-store", conflicted.headers.get("Cache-Control", ""))

            with patch(
                "importer.journey_views._handle_crm_scan_start",
                return_value=HttpResponseRedirect("/new-attempt-started/"),
            ) as start_scan:
                retried = self.client.post(
                    reverse("importer:crm_duplicate_journey"),
                    data={
                        "connection_id": "crm_conn_test",
                        "entity_family": "company",
                        "source_mode": "acquire_all",
                        "form_token": fresh_token,
                    },
                )
            self.assertEqual(retried.status_code, 302)
            self.assertEqual(retried.url, "/new-attempt-started/")
            start_scan.assert_called_once()

    def test_map_and_progress_routes_resolve(self):
        self.assertTrue(
            reverse(
                "importer:crm_duplicate_population_map",
                kwargs={"session_id": "00000000-0000-0000-0000-000000000001"},
            ).endswith("/crm-duplicates/map-record-id/")
        )
        self.assertTrue(
            reverse(
                "importer:crm_duplicate_journey_progress",
                kwargs={"session_id": "00000000-0000-0000-0000-000000000001"},
            ).endswith("/crm-duplicates/progress/")
        )

    def test_failed_progress_stays_on_progress_page(self):
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.FAILED,
            options={
                "redesign_phase": "4a",
                "run_id": "run-failed-1",
                "read_grant_id": "grant-1",
                "apply_form_instance": str(uuid4()),
            },
            error_message="provider fault",
        )
        failed_projection = {
            "run_id": "run-failed-1",
            "revision": 4,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "failed",
            "stage": "reference_acquisition",
            "summary": {},
            "error": {"message": "provider fault"},
            "decision": None,
            "review_handoff": None,
            "effect_intent": None,
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=failed_projection,
            ):
                response = self.client.get(
                    reverse(
                        "importer:crm_duplicate_journey_progress",
                        kwargs={"session_id": draft.id},
                    )
                )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Analysis failed", body)
        self.assertIn("Return to source", body)
        self.assertNotIn("Analysis complete", body)
        # Did not redirect to workflow.
        self.assertEqual(response.request["PATH_INFO"], response.wsgi_request.path)

    def test_paused_read_auto_continues_with_revision_bound_resume(self):
        import re

        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-paused-read",
                "read_grant_id": "grant-paused-read",
                "crm_journey_id": "journey-paused-read",
            },
        )
        paused = {
            "run_id": "run-paused-read",
            "revision": 7,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "paused_verification",
            "stage": "paused_verification",
            "summary": {"analyzed_record_count": 500},
            "decision": None,
            "review_handoff": None,
            "effect_intent": None,
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        ready = {
            **paused,
            "revision": 8,
            "status": "awaiting_review",
            "stage": "review_ready",
            "summary": {
                "analyzed_record_count": 650,
                "duplicate_group_count": 2,
            },
            "review_handoff": {"handoff_id": "review-ready"},
        }
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(EasyImportsApiClient, "workflow", return_value=paused):
                page = self.client.get(route)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Continue reading next batch", body)
        self.assertIn("data-auto-continue-read", body)
        token = re.search(r'name="form_token" value="([^"]+)"', body).group(1)

        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "workflow",
                side_effect=[paused, ready],
            ):
                with patch(
                    "importer.journey_views._dispatch_json_mutation",
                    return_value={"outcome": "accepted"},
                ) as dispatch:
                    response = self.client.post(route, data={"form_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/crm-duplicates/review/", response["Location"])
        self.assertEqual(
            dispatch.call_args.kwargs["mutation_kind"],
            "resume_effect",
        )
        self.assertEqual(
            dispatch.call_args.kwargs["route"],
            "/v1/workflows/run-paused-read/effect-resumptions",
        )
        self.assertEqual(dispatch.call_args.kwargs["body"], {"expected_revision": 7})

    def test_async_paused_read_renders_polling_without_second_auto_submit(self):
        import re

        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-paused-async",
                "read_grant_id": "grant-paused-async",
            },
        )
        paused = {
            "run_id": "run-paused-async",
            "revision": 9,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "paused_verification",
            "stage": "paused_verification",
            "summary": {"analyzed_record_count": 500},
            "decision": None,
            "review_handoff": None,
            "effect_intent": None,
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(EasyImportsApiClient, "workflow", return_value=paused):
                page = self.client.get(route)
                token = re.search(
                    r'name="form_token" value="([^"]+)"',
                    page.content.decode("utf-8"),
                ).group(1)
                with patch(
                    "importer.journey_views._dispatch_json_mutation",
                    side_effect=ApiOperationInProgressError(
                        "EasyImports is processing this action."
                    ),
                ):
                    response = self.client.post(route, data={"form_token": token})

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Reading the next CRM batch in the background", body)
        self.assertNotIn(
            '<form method="post" style="display:inline;" data-auto-continue-read',
            body,
        )
        self.assertIn("var resumeProcessing = true", body)

    def test_progress_post_apply_pending_polls_without_error(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-apply-pending",
                "read_grant_id": "grant-apply-pending",
                "apply_root_form_instance": str(uuid4()),
            },
        )
        awaiting = {
            "run_id": "run-apply-pending",
            "revision": 2,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "awaiting_effect_authorization",
            "stage": "awaiting_effect_authorization",
            "summary": {},
            "decision": None,
            "review_handoff": None,
            "effect_intent": {"track": "reference_acquisition"},
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(EasyImportsApiClient, "workflow", return_value=awaiting):
                page = self.client.get(route)
                token = re.search(
                    r'name="form_token" value="([^"]+)"',
                    page.content.decode("utf-8"),
                ).group(1)
                with patch(
                    "importer.journey_views._dispatch_json_mutation",
                    side_effect=ApiOperationInProgressError(
                        "EasyImports is processing this action."
                    ),
                ):
                    response = self.client.post(route, data={"form_token": token})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("in the background", body)
        self.assertIn("var resumeProcessing = true", body)

    def test_progress_get_reconciles_pending_implied_authorization(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-apply-reconcile",
                "read_grant_id": "grant-apply-reconcile",
            },
        )
        awaiting = {
            "run_id": "run-apply-reconcile",
            "revision": 2,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "status": "awaiting_effect_authorization",
            "stage": "awaiting_effect_authorization",
            "summary": {},
            "decision": None,
            "review_handoff": None,
            "effect_intent": {"track": "reference_acquisition"},
            "effect_grants": [],
            "target_provider_id": "fake",
        }
        ready = {
            **awaiting,
            "status": "awaiting_review",
            "stage": "review_ready",
            "summary": {"analyzed_record_count": 12, "duplicate_group_count": 1},
            "review_handoff": {"handoff_id": "review-ready"},
            "effect_intent": None,
        }
        mutation = create_or_reuse_mutation(
            session=draft,
            form_instance=uuid4(),
            mutation_kind="crm_duplicate_implied_reference_authorization",
            route=(
                "/v1/crm/duplicate-read-grants/grant-apply-reconcile/"
                "implied-reference-authorization"
            ),
            logical_action_identity="apply:run-apply-reconcile",
            request_json={
                "run_id": "run-apply-reconcile",
                "owner_session": f"django-{owner}",
            },
            resource_identity="run-apply-reconcile",
        )
        mutation.http_status = 202
        mutation.save(update_fields=["http_status", "updated_at"])
        completed_status = {
            "mutation_id": "api-apply-ok",
            "mutation_kind": "crm_duplicate_implied_reference_authorization",
            "status": "completed",
            "http_status": 200,
            "response": {
                "run_id": "run-apply-reconcile",
                "track": "reference_acquisition",
                "selected_mode": "execute",
            },
            "retryable": False,
            "retry_after_seconds": 2,
        }
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "workflow",
                side_effect=[awaiting, ready],
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "mutation_status",
                    return_value=completed_status,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "reconcile_mutation_status",
                    ) as reconcile:
                        def _complete(mut, status):
                            mut.state = ApiMutation.State.COMPLETED
                            mut.save(update_fields=["state", "updated_at"])
                            return MagicMock(mutation=mut)

                        reconcile.side_effect = _complete
                        response = self.client.get(route)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/crm-duplicates/review/", response["Location"])

    def test_get_reconciles_frozen_resume_rejection_and_stops_reload_loop(self):
        owner, draft, paused, route = self._paused_resume_fixture("rejected")
        # API preclaim advances before the background provider read finishes.
        paused["revision"] = 10
        owner_session = f"django-{owner}"
        identity = f"crm-dupe-4a:resume-read-r9:{owner_session}:" "run-paused-rejected"
        mutation = create_or_reuse_mutation(
            session=draft,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route="/v1/workflows/run-paused-rejected/effect-resumptions",
            logical_action_identity=identity,
            request_json={
                "expected_revision": 9,
                "owner_session": owner_session,
            },
            resource_identity="run-paused-rejected",
        )
        mutation.http_status = 202
        mutation.save(update_fields=["http_status", "updated_at"])
        completed_error = {
            "mutation_id": "api-resume-rejected",
            "mutation_kind": "resume_effect",
            "status": "completed",
            "http_status": 503,
            "response": {
                "error": {
                    "code": "crm_reference_read_failed",
                    "message": "HubSpot rejected the background read.",
                }
            },
            "retryable": False,
            "retry_after_seconds": 2,
        }

        with patch.object(EasyImportsApiClient, "workflow", return_value=paused):
            with patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value=completed_error,
            ) as poll:
                response = self.client.get(route)

        self.assertEqual(response.status_code, 200)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.REJECTED)
        self.assertEqual(mutation.error_code, "crm_reference_read_failed")
        body = response.content.decode("utf-8")
        self.assertIn("HubSpot rejected the background read", body)
        self.assertNotIn(
            '<form method="post" style="display:inline;" data-auto-continue-read',
            body,
        )
        self.assertIn("var resumeProcessing = false", body)
        poll.assert_called_once_with(mutation.idempotency_key)

        # The reconciled frozen error remains visible without polling a
        # terminal mutation or re-entering the reload loop.
        with patch.object(EasyImportsApiClient, "workflow", return_value=paused):
            with patch.object(
                EasyImportsApiClient,
                "mutation_status",
                side_effect=AssertionError("terminal mutation was polled again"),
            ):
                repeated = self.client.get(route)
        self.assertEqual(repeated.status_code, 200)
        self.assertIn(
            "HubSpot rejected the background read",
            repeated.content.decode("utf-8"),
        )

    def test_get_marks_unowned_resume_unknown_for_saved_exact_retry(self):
        owner, draft, paused, route = self._paused_resume_fixture("retryable")
        # The saved revision-9 command must win over a new revision-10 submit.
        paused["revision"] = 10
        owner_session = f"django-{owner}"
        mutation = create_or_reuse_mutation(
            session=draft,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route="/v1/workflows/run-paused-retryable/effect-resumptions",
            logical_action_identity=(
                f"crm-dupe-4a:resume-read-r9:{owner_session}:" "run-paused-retryable"
            ),
            request_json={
                "expected_revision": 9,
                "owner_session": owner_session,
            },
            resource_identity="run-paused-retryable",
        )
        mutation.http_status = 202
        mutation.save(update_fields=["http_status", "updated_at"])
        retryable_status = {
            "mutation_id": "api-resume-retryable",
            "mutation_kind": "resume_effect",
            "status": "pending",
            "http_status": None,
            "response": None,
            "retryable": True,
            "retry_after_seconds": 2,
        }

        with patch.object(EasyImportsApiClient, "workflow", return_value=paused):
            with patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value=retryable_status,
            ):
                response = self.client.get(route)

        self.assertEqual(response.status_code, 200)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)
        self.assertIsNone(mutation.http_status)
        body = response.content.decode("utf-8")
        self.assertIn("retry the saved exact request", body)
        self.assertIn(
            '<form method="post" style="display:inline;" data-auto-continue-read',
            body,
        )
        self.assertIn("var resumeProcessing = false", body)

        token = re.search(
            r'name="form_token" value="([^"]+)"',
            body,
        ).group(1)
        exact_result = MagicMock(
            mutation=mutation,
            response={"outcome": "accepted"},
        )
        with patch.object(
            EasyImportsApiClient,
            "workflow",
            side_effect=[paused, paused],
        ):
            with patch.object(
                EasyImportsApiClient,
                "dispatch",
                return_value=exact_result,
            ) as dispatch:
                retried = self.client.post(route, data={"form_token": token})
        self.assertEqual(retried.status_code, 200)
        retried_mutation = dispatch.call_args.args[0]
        self.assertEqual(retried_mutation.id, mutation.id)
        self.assertEqual(retried_mutation.idempotency_key, mutation.idempotency_key)
        self.assertEqual(retried_mutation.request_json, mutation.request_json)
        self.assertTrue(dispatch.call_args.kwargs["explicit_retry"])

    def test_revision_advance_still_polls_active_prior_resume(self):
        owner, draft, paused, route = self._paused_resume_fixture("advanced")
        paused["revision"] = 10
        owner_session = f"django-{owner}"
        mutation = create_or_reuse_mutation(
            session=draft,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route="/v1/workflows/run-paused-advanced/effect-resumptions",
            logical_action_identity=(
                f"crm-dupe-4a:resume-read-r9:{owner_session}:" "run-paused-advanced"
            ),
            request_json={
                "expected_revision": 9,
                "owner_session": owner_session,
            },
            resource_identity="run-paused-advanced",
        )
        mutation.http_status = 202
        mutation.save(update_fields=["http_status", "updated_at"])
        active_status = {
            "mutation_id": "api-resume-active",
            "mutation_kind": "resume_effect",
            "status": "pending",
            "http_status": None,
            "response": None,
            "retryable": False,
            "retry_after_seconds": 2,
        }

        with patch.object(EasyImportsApiClient, "workflow", return_value=paused):
            with patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value=active_status,
            ) as poll:
                response = self.client.get(route)

        self.assertEqual(response.status_code, 200)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        poll.assert_called_once_with(mutation.idempotency_key)
        body = response.content.decode("utf-8")
        self.assertIn("Reading the next CRM batch in the background", body)
        self.assertNotIn(
            '<form method="post" style="display:inline;" data-auto-continue-read',
            body,
        )
        self.assertIn("var resumeProcessing = true", body)

    def test_predispatch_pending_resume_without_active_lease_becomes_exact_retry(
        self,
    ):
        for lease_case in ("missing", "expired"):
            with self.subTest(lease_case=lease_case):
                suffix = f"predispatch-{lease_case}"
                owner, draft, paused, route = self._paused_resume_fixture(suffix)
                paused["revision"] = 10
                owner_session = f"django-{owner}"
                run_id = f"run-paused-{suffix}"
                mutation = create_or_reuse_mutation(
                    session=draft,
                    form_instance=uuid4(),
                    mutation_kind="resume_effect",
                    route=f"/v1/workflows/{run_id}/effect-resumptions",
                    logical_action_identity=(
                        f"crm-dupe-4a:resume-read-r9:{owner_session}:{run_id}"
                    ),
                    request_json={
                        "expected_revision": 9,
                        "owner_session": owner_session,
                    },
                    resource_identity=run_id,
                )
                if lease_case == "expired":
                    mutation.lease_token = uuid4()
                    mutation.lease_expires_at = timezone.now() - timedelta(seconds=1)
                    mutation.save(
                        update_fields=[
                            "lease_token",
                            "lease_expires_at",
                            "updated_at",
                        ]
                    )
                self.assertEqual(mutation.state, ApiMutation.State.PENDING)
                self.assertIsNone(mutation.http_status)

                with patch.object(
                    EasyImportsApiClient,
                    "workflow",
                    return_value=paused,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "mutation_status",
                        side_effect=AssertionError(
                            "a never-dispatched mutation has no API status to poll"
                        ),
                    ):
                        response = self.client.get(route)

                self.assertEqual(response.status_code, 200)
                mutation.refresh_from_db()
                self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)
                self.assertIsNone(mutation.lease_token)
                body = response.content.decode("utf-8")
                self.assertIn("retry the saved exact request", body)
                self.assertIn(
                    '<form method="post" style="display:inline;" '
                    "data-auto-continue-read",
                    body,
                )
                self.assertIn("var resumeProcessing = false", body)

    def test_clear_journey_hides_row_and_archives_local_session(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        local = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            options={
                "crm_journey_id": "journey-clear-me",
                "run_id": "run-source",
            },
        )
        clear_url = reverse(
            "importer:crm_duplicate_journey_clear",
            kwargs={"journey_id": "journey-clear-me"},
        )
        response = self.client.post(clear_url)
        self.assertEqual(response.status_code, 302)
        local.refresh_from_db()
        self.assertIsNotNone(local.archived_at)

        journey = {
            "journey_id": "journey-clear-me",
            "run_id": "run-continuation",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "entity_family": "company",
            "source_mode": "acquire_all",
            "status": "workflow_started",
            "workflow_status": "failed",
        }
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with (
                patch.object(
                    EasyImportsApiClient,
                    "crm_providers",
                    return_value={"providers": [{"provider_key": "fake"}]},
                ),
                patch.object(
                    EasyImportsApiClient,
                    "crm_connections",
                    return_value={"connections": _connected_fake()},
                ),
                patch.object(
                    EasyImportsApiClient,
                    "crm_duplicate_journeys",
                    return_value={"journeys": [journey]},
                ),
            ):
                page = self.client.get(reverse("importer:crm_duplicate_journey"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["journeys"], [])

    def test_recent_journey_resumes_by_journey_id_after_run_advances(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        local = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            options={
                "crm_journey_id": "journey-advanced",
                "run_id": "run-source",
            },
        )
        request = self.client.get("/").wsgi_request
        annotated = _annotate_journeys_for_resume(
            request,
            [
                {
                    "journey_id": "journey-advanced",
                    "run_id": "run-continuation",
                }
            ],
        )
        self.assertEqual(annotated[0]["resume_session_id"], str(local.id))
        self.assertIn(str(local.id), annotated[0]["resume_url"])

    def test_second_upload_uses_distinct_draft_sessions(self):
        """Each uploaded_population start creates a new session for upload slot."""

        import re

        connections = {"connections": _connected_fake(duplicate_execution="execute")}

        def fake_upload(**kwargs):
            sess = kwargs["session"]
            self.assertEqual(sess.product_key, "easyimports.duplicate_resolution")
            mock = MagicMock()
            mock.response = {
                "upload_id": f"upl-{sess.id.hex[:8]}",
                "filename": "records.csv",
            }
            return mock

        pop_body = {
            "population_upload_id": "pop-1",
            "upload_id": "upl-1",
            "entity_family": "company",
            "connection_id": "crm_conn_test",
            "source_headers": ["Id", "Name"],
            "preview_rows": [["A1", "One"]],
            "suggested_source_column": "Id",
            "filename": "records.csv",
            "row_count": 1,
            "population_source": "uploaded_population",
            "status": "registered",
            "record_id_mapped": False,
            "requires_group_id": False,
            "requires_member_id": False,
            "byte_count": 10,
            "content_digest": "d",
            "created_at": "2026-08-04T00:00:00Z",
            "inspect_digest": "i",
            "media_type": "text/csv",
            "parser_contract": "p",
            "population_contract": "c",
            "schema_version": "1",
        }

        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "crm_connections",
                    return_value=connections,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "crm_duplicate_journeys",
                        return_value={"journeys": []},
                    ):
                        get_response = self.client.get(
                            reverse("importer:crm_duplicate_journey")
                        )
                        token = re.search(
                            r'name="form_token" value="([^"]+)"',
                            get_response.content.decode("utf-8"),
                        ).group(1)
                        with patch(
                            "importer.journey_views.save_and_register_upload",
                            side_effect=fake_upload,
                        ):
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                return_value=pop_body,
                            ) as dispatch:
                                response = self.client.post(
                                    reverse("importer:crm_duplicate_journey"),
                                    data={
                                        "connection_id": "crm_conn_test",
                                        "entity_family": "company",
                                        "source_mode": "uploaded_population",
                                        "form_token": token,
                                        "population_file": SimpleUploadedFile(
                                            "records.csv",
                                            b"Id,Name\nA1,One\n",
                                            content_type="text/csv",
                                        ),
                                    },
                                )
        self.assertEqual(
            response.status_code, 302, response.content.decode("utf-8")[:800]
        )
        drafts = ImportSession.objects.filter(
            product_key="easyimports.duplicate_resolution",
        )
        self.assertEqual(drafts.count(), 1)
        draft = drafts.get()
        self.assertEqual(draft.options.get("duplicate_execution_maximum"), "execute")
        # Population bind used the draft session journal, not shared crm.journey.
        self.assertTrue(dispatch.called)
        self.assertEqual(dispatch.call_args.kwargs["journal"].id, draft.id)
        journals = ImportSession.objects.filter(product_key="crm.journey")
        self.assertEqual(journals.count(), 1)
        self.assertNotEqual(draft.id, journals.get().id)


class CrmDuplicateJourneyDismissalConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_clear_requests_preserve_both_dismissals(self):
        owner_session_id = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner_session_id,
            product_key="crm.journey",
            status="active",
            options={},
        )
        start = threading.Barrier(2)
        failures: list[BaseException] = []
        failure_guard = threading.Lock()

        def dismiss(journey_id: str) -> None:
            close_old_connections()
            try:
                local_journal = ImportSession.objects.get(pk=journal.pk)
                start.wait(timeout=5)
                _remember_dismissed_journey(local_journal, journey_id)
            except BaseException as exc:  # pragma: no cover - asserted below
                with failure_guard:
                    failures.append(exc)
            finally:
                close_old_connections()

        workers = [
            threading.Thread(target=dismiss, args=("journey-a",)),
            threading.Thread(target=dismiss, args=("journey-b",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertEqual(
            set(
                CrmDuplicateJourneyDismissal.objects.filter(
                    journal=journal,
                ).values_list("journey_id", flat=True)
            ),
            {"journey-a", "journey-b"},
        )
