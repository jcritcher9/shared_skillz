from __future__ import annotations

from datetime import timedelta
import json
from itertools import product as cartesian_product
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .api_client import (
    ApiConsistencyError,
    ApiOperationInProgressError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationBusyError,
    MutationReuseError,
    MutationExplicitRetryRequired,
    MutationDispatchResult,
    canonical_digest,
    create_or_reuse_mutation,
)
from .connection_views import _dispatch_connection_mutation
from .query_views import (
    _attach_journey_workflow_session,
    _delete_identity_for_form,
    _dispatch_query_mutation,
    _handoff_form_identity,
    _handoff_identity_for_form,
    _rejection_message,
    _should_preserve_token,
)
from .api_contract import (
    ApiContractError,
    validate_decision_command,
    validate_effect_authorization,
    validate_health,
    validate_workflow_create,
    validate_workflow_resource,
)
from .api_contract_generated import OPENAPI_SHA256
from .command_service import (
    action_generation,
    crm_connect_action_generation,
    dispatch_command,
    materialize_accepted_workflow,
)
from .forms import CatalogCompatibilityError, WorkflowConfigurationForm
from .models import (
    ApiArtifact,
    ApiMutation,
    ApiWorkflow,
    ImportSession,
    Payment,
    SourceFile,
)
from .upload_service import (
    authorize_rejected_upload_replacement,
    save_and_register_upload,
)
from .workflow_state import (
    FormTokenError,
    OWNER_SESSION_KEY,
    decode_form_token,
    freeze_operator_label,
    issue_form_token,
    owner_id_for_request,
    owner_session_for_import_session,
    validate_form_token,
)


def response(status: int, payload) -> requests.Response:
    value = requests.Response()
    value.status_code = status
    value._content = (
        payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    )
    value.raw = Mock()
    value.headers["Content-Type"] = "application/json"
    return value


def receipt(*, outcome="accepted", result=None):
    value = {
        "command_id": "cmd-1",
        "command_kind": "create_workflow",
        "run_id": "run-1",
        "revision": 1,
        "workflow_status": "running",
        "stage": "start",
        "outcome": outcome,
        "error_code": None if outcome == "accepted" else "request_rejected",
        "message": None if outcome == "accepted" else "No",
        "resource": "/v1/workflows/run-1",
    }
    if result is not None:
        value["result"] = result
    return value


def projection(**changes):
    value = {
        "run_id": "run-1",
        "revision": 1,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 1,
        "target_provider_id": "fake",
        "status": "running",
        "stage": "start",
        "decision": None,
        "effect_intent": None,
        "effect_grants": [],
        "review_handoff": None,
        "terminal_evidence": None,
        "summary": {},
        "error": None,
        "paused_effect_diagnostic": None,
        "reference_acquisition_progress": None,
        "links": {},
    }
    value.update(changes)
    return value


class MutationJournalTests(TestCase):
    def setUp(self):
        self.session = ImportSession.objects.create(owner_id=uuid4())

    def mutation(self, form_instance=None, route="/v1/workflows"):
        form_instance = form_instance or uuid4()
        return create_or_reuse_mutation(
            session=self.session,
            form_instance=form_instance,
            mutation_kind="create_workflow",
            route=route,
            logical_action_identity=f"test-action:{form_instance}",
            request_json={"product_key": "easyimports.list_import"},
        )

    def api_client(self, post_response):
        from importer.api_contract_generated import API_VERSION as DJANGO_API_VERSION

        http = Mock()
        http.get.return_value = response(
            200, {"status": "ok", "api_version": DJANGO_API_VERSION}
        )
        http.post.return_value = post_response
        return EasyImportsApiClient(http=http), http

    def test_200_rejected_receipt_is_authoritative_and_exactly_replayed(self):
        mutation = self.mutation()
        api, http = self.api_client(response(200, receipt(outcome="rejected")))
        first = api.dispatch(mutation)
        self.assertEqual(first.mutation.state, ApiMutation.State.REJECTED)
        self.assertEqual(first.mutation.error_code, "request_rejected")
        again = api.dispatch(mutation)
        self.assertEqual(again.response, first.response)
        self.assertEqual(http.post.call_count, 1)

    def test_resource_and_decision_set_result_are_frozen(self):
        result = {
            "decision_set_handoff": {
                "handoff_id": "ds-1",
                "review_handoff_id": "rh-1",
                "entity": "account",
                "decision_count": 2,
                "binding_digest": "abc",
            }
        }
        mutation = self.mutation()
        api, _ = self.api_client(response(200, receipt(result=result)))
        dispatched = api.dispatch(mutation)
        dispatched.mutation.refresh_from_db()
        self.assertEqual(
            dispatched.mutation.response_json["resource"], "/v1/workflows/run-1"
        )
        self.assertEqual(dispatched.mutation.response_json["result"], result)

    def test_refresh_happens_only_after_receipt_persistence(self):
        mutation = self.mutation()
        api, _ = self.api_client(response(201, receipt()))
        dispatched = api.dispatch(mutation)
        observed = []

        def get_workflow(_run_id, owner_session=None):
            del owner_session
            mutation.refresh_from_db()
            observed.append(mutation.state)
            return projection()

        api.workflow = get_workflow
        materialize_accepted_workflow(self.session, dispatched, client=api)
        self.assertEqual(observed, [ApiMutation.State.COMPLETED])

    def test_owner_session_binding_for_import_session(self):
        self.assertEqual(
            owner_session_for_import_session(self.session),
            f"django-{self.session.owner_id}",
        )

    def test_query_mutation_dispatch_enables_exact_retry_when_unknown(self):
        mutation = self.mutation(route="/v1/crm/queries")
        mutation.state = ApiMutation.State.UNKNOWN
        mutation.save(update_fields=["state", "updated_at"])
        api = Mock()
        api.dispatch = Mock(
            return_value=MutationDispatchResult(
                mutation, {"query_id": "q1", "status": "workflow_started"}
            )
        )
        _dispatch_query_mutation(api, mutation)
        api.dispatch.assert_called_once()
        _, kwargs = api.dispatch.call_args
        self.assertTrue(kwargs.get("explicit_retry"))
        mutation.state = ApiMutation.State.PENDING
        mutation.save(update_fields=["state", "updated_at"])
        api.dispatch.reset_mock()
        _dispatch_query_mutation(api, mutation)
        _, kwargs = api.dispatch.call_args
        self.assertFalse(kwargs.get("explicit_retry"))

    def test_query_rejection_message_uses_api_error_fields(self):
        mutation = self.mutation()
        mutation.state = ApiMutation.State.REJECTED
        mutation.error_code = "crm_query_invalid"
        mutation.error_message = "Query snapshot cleanup failed."
        mutation.save(
            update_fields=["state", "error_code", "error_message", "updated_at"]
        )
        text = _rejection_message(mutation)
        self.assertIn("Query snapshot cleanup failed", text)
        self.assertIn("crm_query_invalid", text)

    def test_query_delete_identity_is_form_instance_scoped(self):
        a = _delete_identity_for_form(
            query_id="q1", owner_session="django-o", form_instance="fi-1"
        )
        b = _delete_identity_for_form(
            query_id="q1", owner_session="django-o", form_instance="fi-2"
        )
        self.assertNotEqual(a, b)
        self.assertIn("fi-1", a)
        self.assertIn("q1", a)

    def test_h1a_handoff_identity_is_form_instance_scoped(self):
        form_id = _handoff_form_identity(query_id="q1", owner_session="django-o")
        a = _handoff_identity_for_form(
            query_id="q1", owner_session="django-o", form_instance="fi-1"
        )
        b = _handoff_identity_for_form(
            query_id="q1", owner_session="django-o", form_instance="fi-2"
        )
        self.assertIn("q1", form_id)
        self.assertIn("django-o", form_id)
        self.assertNotEqual(a, b)
        self.assertIn("fi-1", a)
        self.assertIn("q1", a)

    def test_h1a_handoff_double_submit_reuses_same_mutation(self):
        form_instance = uuid4()
        identity = _handoff_identity_for_form(
            query_id="q-handoff",
            owner_session=f"django-{self.session.owner_id}",
            form_instance=form_instance,
        )
        body = {
            "connection_id": "crm_conn_1",
            "entity_family": "company",
            "source_mode": "selected_ids",
            "selected_ids": ["A1", "A2"],
            "query_id": "q-handoff",
            "query_result_digest": "d" * 64,
            "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            "owner_session": f"django-{self.session.owner_id}",
        }
        first = create_or_reuse_mutation(
            session=self.session,
            form_instance=form_instance,
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity=identity,
            request_json=body,
        )
        second = create_or_reuse_mutation(
            session=self.session,
            form_instance=form_instance,
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity=identity,
            request_json=body,
        )
        self.assertEqual(first.id, second.id)

    def test_h1a_handoff_changed_payload_on_same_form_is_rejected(self):
        form_instance = uuid4()
        identity = _handoff_identity_for_form(
            query_id="q-handoff",
            owner_session=f"django-{self.session.owner_id}",
            form_instance=form_instance,
        )
        body = {
            "connection_id": "crm_conn_1",
            "entity_family": "company",
            "source_mode": "selected_ids",
            "selected_ids": ["A1", "A2"],
            "query_id": "q-handoff",
            "query_result_digest": "d" * 64,
            "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            "owner_session": f"django-{self.session.owner_id}",
        }
        create_or_reuse_mutation(
            session=self.session,
            form_instance=form_instance,
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity=identity,
            request_json=body,
        )
        changed = dict(body)
        changed["selected_ids"] = ["A1", "A3"]
        with self.assertRaises(MutationReuseError):
            create_or_reuse_mutation(
                session=self.session,
                form_instance=form_instance,
                mutation_kind="crm_duplicate_journey_start",
                route="/v1/crm/duplicate-journeys",
                logical_action_identity=identity,
                request_json=changed,
            )

    def test_h1a_attach_journey_session_is_idempotent(self):
        journey = {
            "journey_id": "journey-1",
            "run_id": "run-1",
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "entity_family": "company",
            "source_mode": "selected_ids",
            "status": "workflow_started",
            "target_provider_id": "fake-crm-phase0b-v1",
        }
        api = Mock()
        api.workflow = Mock(
            return_value={
                "run_id": "run-1",
                "status": "awaiting_effect_authorization",
                "stage": "reference_acquisition",
                "revision": 1,
                "workflow_key": "easyimports.duplicate_resolution",
                "workflow_version": 4,
                "target_provider_id": "fake-crm-phase0b-v1",
            }
        )
        first = _attach_journey_workflow_session(
            client=api,
            owner_id=self.session.owner_id,
            owner_session=f"django-{self.session.owner_id}",
            journey=journey,
            query_id="q-handoff",
        )
        second = _attach_journey_workflow_session(
            client=api,
            owner_id=self.session.owner_id,
            owner_session=f"django-{self.session.owner_id}",
            journey=journey,
            query_id="q-handoff",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            ImportSession.objects.filter(
                owner_id=self.session.owner_id,
                options__crm_journey_id="journey-1",
            ).count(),
            1,
        )

    def test_query_token_preserve_includes_pending(self):
        mutation = self.mutation()
        mutation.state = ApiMutation.State.PENDING
        mutation.save(update_fields=["state", "updated_at"])
        self.assertTrue(_should_preserve_token(mutation))
        mutation.state = ApiMutation.State.REJECTED
        mutation.save(update_fields=["state", "updated_at"])
        self.assertFalse(_should_preserve_token(mutation))

    def test_h1a_handoff_view_double_submit_converges_on_one_journey(self):
        """POST handoff twice with the same signed form starts one mutation."""

        from django.contrib.messages.middleware import MessageMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware

        from .query_views import crm_query_detail

        owner = self.session.owner_id
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM query",
            product_key="crm.query",
        )
        owner_session = f"django-{owner}"
        query_id = "q-view-handoff"
        form_identity = _handoff_form_identity(
            query_id=query_id, owner_session=owner_session
        )
        token = issue_form_token(
            owner_id=owner,
            session=journal,
            action_kind="crm_h1a_handoff",
            action_id=query_id,
            logical_action_identity=form_identity,
        )
        claims = validate_form_token(
            token,
            owner_id=owner,
            session=journal,
            action_kind="crm_h1a_handoff",
            action_id=query_id,
            logical_action_identity=form_identity,
        )
        journey = {
            "journey_id": "journey-view-1",
            "run_id": "run-view-1",
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "entity_family": "company",
            "source_mode": "selected_ids",
            "status": "workflow_started",
            "target_provider_id": "fake-crm-phase0b-v1",
            "candidate_group_count": 0,
            "acquired_record_count": 2,
            "acquisition": {},
            "next_steps": [],
        }
        projection = {
            "run_id": "run-view-1",
            "status": "awaiting_effect_authorization",
            "stage": "reference_acquisition",
            "revision": 1,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "target_provider_id": "fake-crm-phase0b-v1",
        }
        post_data = {
            "action": "handoff",
            "form_token": token,
            "query_id": query_id,
            "query_result_digest": "d" * 64,
            "connection_id": "crm_conn_1",
            "object_family": "companies",
            "selected_ids": "A1,A2",
            "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
        }
        factory = RequestFactory()

        def fake_dispatch(mutation, **kwargs):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = journey
            mutation.http_status = 201
            mutation.save(
                update_fields=["state", "response_json", "http_status", "updated_at"]
            )
            return MutationDispatchResult(mutation, journey)

        def prepare(request):
            SessionMiddleware(lambda r: None).process_request(request)
            request.session.save()
            MessageMiddleware(lambda r: None).process_request(request)
            return request

        with (
            patch(
                "importer.query_views.owner_id_for_request",
                return_value=owner,
            ),
            patch(
                "importer.query_views._query_journal_session",
                return_value=journal,
            ),
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch),
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=projection,
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_query",
                return_value={
                    "query_id": query_id,
                    "status": "snapshot_ready",
                    "result_digest": "d" * 64,
                    "connection_id": "crm_conn_1",
                    "object_family": "companies",
                    "snapshot_id": "qs_1",
                    "run_id": "query-run",
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_query_rows",
                return_value={
                    "columns": ["record_id"],
                    "rows": [["A1"], ["A2"]],
                    "returned": 2,
                    "total_rows": 2,
                    "offset": 0,
                    "page_size": 50,
                    "has_more": False,
                    "next_cursor": None,
                    "result_digest": "d" * 64,
                },
            ),
        ):
            first = crm_query_detail(
                prepare(factory.post(f"/crm/queries/{query_id}/", data=post_data)),
                query_id=query_id,
            )
            self.assertEqual(first.status_code, 302)
            second = crm_query_detail(
                prepare(factory.post(f"/crm/queries/{query_id}/", data=post_data)),
                query_id=query_id,
            )
            self.assertEqual(second.status_code, 302)

        mutations = ApiMutation.objects.filter(
            session=journal,
            mutation_kind="crm_duplicate_journey_start",
        )
        self.assertEqual(mutations.count(), 1)
        mut = mutations.get()
        self.assertEqual(mut.form_instance, claims["form_instance"])
        self.assertEqual(
            mut.logical_action_identity,
            _handoff_identity_for_form(
                query_id=query_id,
                owner_session=owner_session,
                form_instance=claims["form_instance"],
            ),
        )
        self.assertEqual(
            ImportSession.objects.filter(
                owner_id=owner,
                options__crm_journey_id="journey-view-1",
            ).count(),
            1,
        )

    def test_same_form_changed_payload_or_route_is_rejected_locally(self):
        form_instance = uuid4()
        self.mutation(form_instance=form_instance)
        with self.assertRaises(MutationReuseError):
            create_or_reuse_mutation(
                session=self.session,
                form_instance=form_instance,
                mutation_kind="create_workflow",
                route="/v1/uploads",
                logical_action_identity=f"test-action:{form_instance}",
                request_json={"different": True},
            )

    def test_malformed_success_becomes_unknown_and_only_exact_retry_is_possible(self):
        mutation = self.mutation()
        api, _ = self.api_client(response(200, {"not": "a receipt"}))
        with self.assertRaises(ApiUnavailableError):
            api.dispatch(mutation)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(mutation.idempotency_key, f"web-{mutation.id.hex}")
        with self.assertRaises(MutationExplicitRetryRequired):
            api.dispatch(mutation)

    def test_incompatible_health_fails_before_dispatch(self):
        mutation = self.mutation()
        http = Mock()
        http.get.return_value = response(200, {"status": "ok", "api_version": "1.1.0"})
        api = EasyImportsApiClient(http=http)
        with self.assertRaises(ApiUnavailableError):
            api.dispatch(mutation)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.attempt_count, 0)
        http.post.assert_not_called()

    def test_contract_valid_404_is_rejected(self):
        mutation = self.mutation()
        api, _ = self.api_client(
            response(404, {"error": {"code": "not_found", "message": "No"}})
        )
        result = api.dispatch(mutation)
        self.assertEqual(result.mutation.state, ApiMutation.State.REJECTED)
        self.assertEqual(result.mutation.error_code, "not_found")

    def test_202_operation_status_stays_pending_until_polled_completion(self):
        mutation = self.mutation()
        api, _ = self.api_client(
            response(
                202,
                {
                    "mutation_id": "mutation-safe",
                    "mutation_kind": "create_workflow",
                    "status": "pending",
                    "http_status": None,
                    "response": None,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            )
        )
        with self.assertRaises(ApiOperationInProgressError):
            api.dispatch(mutation)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.http_status, 202)
        self.assertTrue(mutation.operation_in_progress)
        self.assertFalse(mutation.retry_available)

        completed_status = {
            "mutation_id": "mutation-safe",
            "mutation_kind": "create_workflow",
            "status": "completed",
            "http_status": 201,
            "response": receipt(),
            "retryable": False,
            "retry_after_seconds": 2,
        }
        result = api.reconcile_mutation_status(mutation, completed_status)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(result.response["outcome"], "accepted")

    def test_mutation_in_progress_409_is_transient_not_rejected(self):
        mutation = self.mutation()
        api, _ = self.api_client(
            response(
                409,
                {
                    "error": {
                        "code": "mutation_in_progress",
                        "message": "Still processing.",
                    }
                },
            )
        )
        with self.assertRaises(ApiOperationInProgressError):
            api.dispatch(mutation)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.http_status, 409)
        self.assertEqual(mutation.error_code, "")
        self.assertFalse(mutation.retry_available)

    @override_settings(EASYIMPORTS_API_MUTATION_READ_TIMEOUT=180)
    def test_effect_dispatch_prefers_async_and_uses_long_mutation_timeout(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-1/effect-authorizations",
            logical_action_identity="effect:test",
            request_json={"selected_mode": "execute"},
        )
        api, http = self.api_client(response(200, receipt()))
        api.dispatch(mutation)
        posted = http.post.call_args
        self.assertEqual(posted.kwargs["headers"]["Prefer"], "respond-async")
        self.assertEqual(posted.kwargs["timeout"], (3.0, 180.0))

    def test_oauth_complete_dispatch_prefers_async(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route="/v1/crm/connections/crm_conn_live/oauth/complete",
            logical_action_identity="crm-complete:crm_conn_live:django-owner",
            request_json={
                "authorization_code": "one-time-code",
                "state": "state-1",
                "owner_session": "django-owner",
            },
        )
        api, http = self.api_client(
            response(
                202,
                {
                    "mutation_id": "oauth-complete-safe",
                    "mutation_kind": "crm_connection_oauth_complete",
                    "status": "pending",
                    "http_status": None,
                    "response": None,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            )
        )
        with self.assertRaises(ApiOperationInProgressError):
            api.dispatch(mutation)
        posted = http.post.call_args
        self.assertEqual(posted.kwargs["headers"]["Prefer"], "respond-async")
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.http_status, 202)
        self.assertTrue(mutation.operation_in_progress)

    def test_run_output_package_dispatch_prefers_async(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_run_output_package",
            route="/v1/workflows/run-1/run-output-packages",
            logical_action_identity="package:run-1:1:group:",
            request_json={
                "layout_version": "run_output_package.v1",
                "owner_session": "django-owner",
            },
        )
        api, http = self.api_client(
            response(
                202,
                {
                    "mutation_id": "package-safe",
                    "mutation_kind": "create_run_output_package",
                    "status": "pending",
                    "http_status": None,
                    "response": None,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            )
        )
        with self.assertRaises(ApiOperationInProgressError):
            api.dispatch(mutation)
        posted = http.post.call_args
        self.assertEqual(posted.kwargs["headers"]["Prefer"], "respond-async")
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.http_status, 202)
        self.assertTrue(mutation.operation_in_progress)

    def test_export_artifacts_dispatch_prefers_async(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_export_artifacts",
            route="/v1/workflows/run-1/export-artifacts",
            logical_action_identity="export:run-1:1:group:",
            request_json={
                "profile_key": "people",
                "tables": [{"kind": "prepared", "name": "contacts"}],
                "owner_session": "django-owner",
            },
        )
        api, http = self.api_client(
            response(
                202,
                {
                    "mutation_id": "export-safe",
                    "mutation_kind": "create_export_artifacts",
                    "status": "pending",
                    "http_status": None,
                    "response": None,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            )
        )
        with self.assertRaises(ApiOperationInProgressError):
            api.dispatch(mutation)
        posted = http.post.call_args
        self.assertEqual(posted.kwargs["headers"]["Prefer"], "respond-async")
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.http_status, 202)
        self.assertTrue(mutation.operation_in_progress)

    def test_submit_decision_dispatch_prefers_async(self):
        """CUM-1D: finish_for_now / decisions use Prefer: respond-async."""

        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="submit_decision",
            route="/v1/workflows/run-1/decisions",
            logical_action_identity="decision:finish",
            request_json={
                "decision_id": "d1",
                "decision_type": "account_duplicate_group_review",
                "expected_revision": 1,
                "response": {
                    "action": "finish_for_now",
                    "remaining_group_disposition": "export",
                },
            },
        )
        api, http = self.api_client(
            response(
                202,
                {
                    "mutation_id": "decision-safe",
                    "mutation_kind": "submit_decision",
                    "status": "pending",
                    "http_status": None,
                    "response": None,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            )
        )
        with self.assertRaises(ApiOperationInProgressError):
            api.dispatch(mutation)
        posted = http.post.call_args
        self.assertEqual(posted.kwargs["headers"]["Prefer"], "respond-async")
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertEqual(mutation.http_status, 202)
        self.assertTrue(mutation.operation_in_progress)

        finished = receipt(outcome="accepted")
        finished["command_kind"] = "submit_decision"
        finished["workflow_status"] = "succeeded"
        finished["stage"] = "complete"
        completed_status = {
            "mutation_id": "decision-safe",
            "mutation_kind": "submit_decision",
            "status": "completed",
            "http_status": 200,
            "response": finished,
            "retryable": False,
            "retry_after_seconds": 2,
        }
        result = api.reconcile_mutation_status(mutation, completed_status)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(result.response["outcome"], "accepted")

    def test_api_timeout_overrides_must_be_strictly_positive(self):
        from easyimports_web.timeouts import parse_positive_timeout

        self.assertEqual(
            parse_positive_timeout(
                "EASYIMPORTS_API_MUTATION_READ_TIMEOUT",
                None,
                default="180",
            ),
            180.0,
        )
        for raw in ("0", "-1", "nan", "inf", "", "abc"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_positive_timeout(
                        "EASYIMPORTS_API_MUTATION_READ_TIMEOUT",
                        raw,
                        default="180",
                    )

    def test_idempotency_and_checkpoint_conflicts_are_frozen_rejections(self):
        for status, code in (
            (409, "idempotency_key_reused"),
            (422, "checkpoint_contract_unsupported"),
        ):
            mutation = self.mutation()
            original_key = mutation.idempotency_key
            api, _ = self.api_client(
                response(status, {"error": {"code": code, "message": "Rejected"}})
            )
            result = api.dispatch(mutation)
            self.assertEqual(result.mutation.state, ApiMutation.State.REJECTED)
            self.assertEqual(result.mutation.error_code, code)
            self.assertEqual(result.mutation.idempotency_key, original_key)

    def test_expired_lease_stays_pending_and_is_reacquired_with_new_fence(self):
        mutation = self.mutation()
        mutation.lease_token = uuid4()
        mutation.lease_expires_at = timezone.now() - timedelta(seconds=1)
        mutation.save(update_fields=["lease_token", "lease_expires_at"])
        api, _ = self.api_client(response(201, receipt()))
        result = api.dispatch(mutation)
        self.assertEqual(result.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(result.mutation.attempt_count, 1)

    def test_late_worker_cannot_overwrite_newer_frozen_result(self):
        mutation = self.mutation()
        api, _ = self.api_client(response(201, receipt()))
        old_token = api._acquire_lease(mutation)
        mutation.refresh_from_db()
        mutation.lease_token = uuid4()
        mutation.state = ApiMutation.State.COMPLETED
        mutation.response_json = receipt()
        from .api_client import canonical_digest

        mutation.response_digest = canonical_digest(mutation.response_json)
        mutation.save()
        with self.assertRaises(ApiConsistencyError):
            api._persist_final(
                mutation,
                old_token,
                state=ApiMutation.State.COMPLETED,
                http_status=200,
                payload={**receipt(), "stage": "different"},
                error_code="",
                error_message="",
            )


class TrustBoundaryTests(TestCase):
    def test_owner_scope_hides_uuid_from_other_browser(self):
        first = self.client
        first.post(reverse("importer:start_session"))
        session = ImportSession.objects.get()
        second = self.client_class()
        self.assertEqual(
            second.get(reverse("importer:upload", args=[session.id])).status_code, 404
        )

    def test_local_settings_rejects_remote_peer_with_spoofed_loopback_xff(self):
        """Phase 7A-P: X-Forwarded-For must not grant Settings access."""

        from django.test import RequestFactory

        from . import settings_views

        factory = RequestFactory()
        remote = factory.get(
            "/settings/",
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_FORWARDED_FOR="127.0.0.1",
        )
        self.assertFalse(settings_views._is_loopback_request(remote))
        response = settings_views.local_settings(remote)
        self.assertEqual(response.status_code, 403)

        loopback = factory.get("/settings/", REMOTE_ADDR="127.0.0.1")
        self.assertTrue(settings_views._is_loopback_request(loopback))


class LocalSettingsHubSpotTests(TestCase):
    """Phase 7B-P: Django Settings HubSpot registration handlers (loopback)."""

    def _loopback_post(self, path, data):
        factory = RequestFactory()
        return factory.post(path, data=data, REMOTE_ADDR="127.0.0.1")

    def test_hubspot_private_app_registration_proxies_and_does_not_retain_secret(self):
        from . import settings_views

        mock_client = Mock()
        mock_mutation = Mock()
        mock_mutation.id = "mut-1"
        request = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/",
            {
                "label": "Work HS",
                "auth_mode": "private_app",
                "access_token": "pat-must-not-persist",
                "expected_hub_id": "12345678",
            },
        )
        with (
            patch.object(settings_views, "_client", return_value=mock_client),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(settings_views, "_registration_mutation_slot", return_value=(uuid4(), 0)),
            patch.object(settings_views, "_registration_generation", return_value=0),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets") as stage,
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"provider_key": "hubspot"},
            ),
            patch.object(settings_views, "messages") as mock_messages,
        ):
            response = settings_views.local_settings_hubspot_registration(request)
        self.assertEqual(response.status_code, 302)
        mock_create.assert_called_once()
        journal = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(journal["auth_mode"], "private_app")
        self.assertEqual(journal["expected_hub_id"], "12345678")
        self.assertNotIn("access_token", journal)
        self.assertIn("access_token_digest", journal)
        stage.assert_called_once()
        self.assertEqual(
            stage.call_args.kwargs.get("access_token")
            or stage.call_args[1].get("access_token"),
            "pat-must-not-persist",
        )
        mock_messages.success.assert_called_once()
        self.assertFalse(
            any(
                "pat-must-not-persist" in str(call)
                for call in mock_messages.method_calls
            )
        )

    def test_hubspot_oauth_registration_uses_canonical_redirect(self):
        from . import settings_views

        mock_mutation = Mock()
        mock_mutation.id = "mut-2"
        request = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/",
            {
                "label": "OAuth HS",
                "auth_mode": "oauth",
                "client_id": "HSCLIENT",
                "client_secret": "oauth-secret",
                "expected_hub_id": "87654321",
            },
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(settings_views, "_registration_mutation_slot", return_value=(uuid4(), 0)),
            patch.object(settings_views, "_registration_generation", return_value=0),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets") as stage,
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"provider_key": "hubspot"},
            ),
            patch.object(settings_views, "messages"),
        ):
            response = settings_views.local_settings_hubspot_registration(request)
        self.assertEqual(response.status_code, 302)
        journal = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(journal["auth_mode"], "oauth")
        self.assertEqual(journal["client_id"], "HSCLIENT")
        self.assertEqual(
            journal["redirect_uri"], "https://127.0.0.1:8001/crm/oauth/callback/"
        )
        self.assertNotIn("client_secret", journal)
        self.assertIn("client_secret_digest", journal)
        stage.assert_called_once()
        self.assertEqual(
            stage.call_args.kwargs.get("client_secret")
            or stage.call_args[1].get("client_secret"),
            "oauth-secret",
        )

    def test_hubspot_rotate_private_app_and_oauth_paths(self):
        from . import settings_views

        mock_mutation = Mock()
        mock_mutation.id = "mut-rot"
        private = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/rotate-secret/",
            {"auth_mode": "private_app", "access_token": "new-pat"},
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(settings_views, "_registration_mutation_slot", return_value=(uuid4(), 0)),
            patch.object(settings_views, "_registration_generation", return_value=0),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets") as stage,
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"client_secret_configured": True},
            ),
            patch.object(settings_views, "messages"),
        ):
            settings_views.local_settings_rotate_hubspot_secret(private)
        journal = mock_create.call_args.kwargs["request_json"]
        self.assertIn("access_token_digest", journal)
        self.assertNotIn("access_token", journal)
        self.assertEqual(
            stage.call_args.kwargs.get("access_token")
            or stage.call_args[1].get("access_token"),
            "new-pat",
        )

        oauth = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/rotate-secret/",
            {"auth_mode": "oauth", "client_secret": "new-secret"},
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(settings_views, "_registration_mutation_slot", return_value=(uuid4(), 0)),
            patch.object(settings_views, "_registration_generation", return_value=0),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets") as stage,
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"client_secret_configured": True},
            ),
            patch.object(settings_views, "messages"),
        ):
            settings_views.local_settings_rotate_hubspot_secret(oauth)
        journal = mock_create.call_args.kwargs["request_json"]
        self.assertIn("client_secret_digest", journal)
        self.assertEqual(
            stage.call_args.kwargs.get("client_secret")
            or stage.call_args[1].get("client_secret"),
            "new-secret",
        )

    def test_hubspot_remove_requires_confirm_and_proxies_delete(self):
        from . import settings_views

        unconfirmed = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/remove/",
            {},
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "messages") as mock_messages,
            patch.object(settings_views, "create_or_reuse_mutation") as mock_create,
        ):
            response = settings_views.local_settings_remove_registration(
                unconfirmed, "hubspot"
            )
        self.assertEqual(response.status_code, 302)
        mock_create.assert_not_called()
        mock_messages.error.assert_called_once()

        mock_mutation = Mock()
        mock_mutation.id = "mut-del"
        confirmed = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/remove/",
            {"confirm": "1"},
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(settings_views, "_registration_mutation_slot", return_value=(uuid4(), 0)),
            patch.object(settings_views, "_registration_generation", return_value=0),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"provider_key": "hubspot", "status": "deleted"},
            ) as mock_dispatch,
            patch.object(settings_views, "messages"),
        ):
            response = settings_views.local_settings_remove_registration(
                confirmed, "hubspot"
            )
        self.assertEqual(response.status_code, 302)
        mock_create.assert_called_once()
        self.assertEqual(
            mock_create.call_args.kwargs["mutation_kind"],
            "crm_app_registration_delete",
        )
        mock_dispatch.assert_called_once()

    def test_hubspot_settings_reject_remote_peer(self):
        from . import settings_views

        factory = RequestFactory()
        remote = factory.post(
            "/settings/crm-app-registrations/hubspot/",
            data={"label": "x", "auth_mode": "private_app"},
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_FORWARDED_FOR="127.0.0.1",
        )
        response = settings_views.local_settings_hubspot_registration(remote)
        self.assertEqual(response.status_code, 403)

    def test_hubspot_registration_api_error_surfaces_without_crashing(self):
        from . import settings_views

        mock_mutation = Mock()
        mock_mutation.id = "mut-err"
        request = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/",
            {
                "label": "Broken",
                "auth_mode": "private_app",
                "access_token": "x",
            },
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(settings_views, "_registration_mutation_slot", return_value=(uuid4(), 0)),
            patch.object(settings_views, "_registration_generation", return_value=0),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ),
            patch.object(settings_views, "stage_ephemeral_registration_secrets"),
            patch.object(
                settings_views,
                "_dispatch_registration",
                side_effect=ApiRejectedError(
                    "crm_app_registration_rejected",
                    "expected_hub_id required",
                ),
            ),
            patch.object(settings_views, "messages") as mock_messages,
        ):
            response = settings_views.local_settings_hubspot_registration(request)
        self.assertEqual(response.status_code, 302)
        mock_messages.error.assert_called_once()
        mock_messages.success.assert_not_called()
        self.assertIn("expected_hub_id", str(mock_messages.error.call_args))

    def test_registration_form_digest_includes_environment_and_org_fields(self):
        from . import settings_views

        a = {
            "label": "SF",
            "login_environment": "sandbox",
            "client_id": "CID",
            "redirect_uri": "https://127.0.0.1:8001/crm/oauth/callback/",
            "my_domain_host": None,
            "expected_org_id": "00D000000000001EAA",
            "client_secret_digest": "abc",
        }
        b = dict(a)
        b["login_environment"] = "production"
        c = dict(a)
        c["expected_org_id"] = "00D000000000002EAA"
        d = dict(a)
        d["my_domain_host"] = "example.my.salesforce.com"
        self.assertNotEqual(
            settings_views._registration_form_digest(a),
            settings_views._registration_form_digest(b),
        )
        self.assertNotEqual(
            settings_views._registration_form_digest(a),
            settings_views._registration_form_digest(c),
        )
        self.assertNotEqual(
            settings_views._registration_form_digest(a),
            settings_views._registration_form_digest(d),
        )

    def test_registration_generation_advances_after_completed(self):
        from . import settings_views
        from .models import ApiMutation, ImportSession

        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner, product_key="crm.settings"
        )
        identity = "crm-reg:salesforce:put:local"
        digest = "same-digest"
        ApiMutation.objects.create(
            session=session,
            form_instance=uuid4(),
            idempotency_key="web-a",
            mutation_kind="crm_app_registration_salesforce_put",
            route="/v1/settings/crm-app-registrations/salesforce",
            logical_action_identity=identity,
            logical_action_generation=0,
            form_payload_digest=digest,
            request_kind="json",
            request_json={},
            request_digest="rd",
            state=ApiMutation.State.COMPLETED,
        )
        gen = settings_views._registration_generation(
            session, identity=identity, form_payload_digest=digest
        )
        self.assertEqual(gen, 1)

    def test_registration_generation_pins_open_mutation_regardless_of_digest(self):
        """Changed values must not fork PENDING/UNKNOWN via a new generation."""

        from . import settings_views
        from .api_client import MutationReuseError, create_or_reuse_mutation
        from .models import ApiMutation, ImportSession

        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner, product_key="crm.settings"
        )
        identity = "crm-reg:salesforce:put:local"
        form_a = uuid4()
        ApiMutation.objects.create(
            session=session,
            form_instance=form_a,
            idempotency_key="web-open",
            mutation_kind="crm_app_registration_salesforce_put",
            route="/v1/settings/crm-app-registrations/salesforce",
            logical_action_identity=identity,
            logical_action_generation=3,
            form_payload_digest="digest-a",
            request_kind="json",
            request_json={"client_id": "A"},
            request_digest="rd-a",
            state=ApiMutation.State.PENDING,
        )
        gen = settings_views._registration_generation(
            session, identity=identity, form_payload_digest="digest-b"
        )
        self.assertEqual(gen, 3)
        with self.assertRaises(MutationReuseError):
            create_or_reuse_mutation(
                session=session,
                form_instance=uuid4(),
                mutation_kind="crm_app_registration_salesforce_put",
                route="/v1/settings/crm-app-registrations/salesforce",
                logical_action_identity=identity,
                logical_action_generation=gen,
                form_payload_digest="digest-b",
                request_json={"client_id": "B"},
                resource_identity="salesforce",
            )

    def test_registration_form_instance_replays_completed_double_submit(self):
        from . import settings_views
        from .api_client import create_or_reuse_mutation
        from .models import ApiMutation, ImportSession

        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner, product_key="crm.settings"
        )
        identity = "crm-reg:salesforce:put:local"
        form_instance = uuid4()
        digest = "completed-digest"
        existing = ApiMutation.objects.create(
            session=session,
            form_instance=form_instance,
            idempotency_key="web-done",
            mutation_kind="crm_app_registration_salesforce_put",
            route="/v1/settings/crm-app-registrations/salesforce",
            logical_action_identity=identity,
            logical_action_generation=0,
            form_payload_digest=digest,
            request_kind="json",
            request_json={"client_id": "A"},
            request_digest="rd",
            state=ApiMutation.State.COMPLETED,
            response_json={"provider_key": "salesforce"},
        )
        # New form tokens advance generation; same form_instance reuses gen 0.
        gen_new_form = settings_views._registration_generation(
            session, identity=identity, form_payload_digest=digest
        )
        self.assertEqual(gen_new_form, 1)
        reused = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_salesforce_put",
            route="/v1/settings/crm-app-registrations/salesforce",
            logical_action_identity=identity,
            logical_action_generation=0,
            form_payload_digest=digest,
            request_json={"client_id": "A"},
            resource_identity="salesforce",
        )
        self.assertEqual(reused.id, existing.id)

    def test_blank_registration_secret_is_validation_error(self):
        from .api_client import (
            RegistrationSecretError,
            stage_ephemeral_registration_secrets,
        )

        with self.assertRaises(RegistrationSecretError):
            stage_ephemeral_registration_secrets(uuid4(), client_secret="")
        with self.assertRaises(RegistrationSecretError):
            stage_ephemeral_registration_secrets(uuid4(), access_token="   ")

    def test_blank_salesforce_secret_creates_no_mutation_then_valid_succeeds(self):
        """View-level: blank secret must not poison PENDING generation."""

        from . import settings_views
        from .models import ApiMutation, ImportSession

        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner, product_key="crm.settings"
        )
        request = self._loopback_post(
            "/settings/crm-app-registrations/salesforce/",
            {
                "label": "Dev SF",
                "login_environment": "sandbox",
                "client_id": "CLIENT1234",
                "client_secret": "",
            },
        )
        request.session = self.client.session
        mock_mutation = Mock()
        mock_mutation.id = "should-not-create"
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=session),
            patch.object(
                settings_views,
                "_registration_mutation_slot",
                return_value=(uuid4(), 0),
            ) as mock_slot,
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets") as stage,
            patch.object(settings_views, "_dispatch_registration") as mock_dispatch,
            patch.object(settings_views, "messages") as mock_messages,
        ):
            response = settings_views.local_settings_salesforce_registration(request)
        self.assertEqual(response.status_code, 302)
        mock_create.assert_not_called()
        mock_slot.assert_not_called()
        stage.assert_not_called()
        mock_dispatch.assert_not_called()
        mock_messages.error.assert_called_once()
        mock_messages.success.assert_not_called()
        self.assertIn("secret", str(mock_messages.error.call_args).lower())
        self.assertEqual(
            ApiMutation.objects.filter(session=session).count(),
            0,
        )

        # Subsequent valid submission still creates and dispatches.
        valid = self._loopback_post(
            "/settings/crm-app-registrations/salesforce/",
            {
                "label": "Dev SF",
                "login_environment": "sandbox",
                "client_id": "CLIENT1234",
                "client_secret": "real-secret",
            },
        )
        valid.session = self.client.session
        mock_mutation2 = Mock()
        mock_mutation2.id = "mut-ok"
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=session),
            patch.object(
                settings_views,
                "_registration_mutation_slot",
                return_value=(uuid4(), 0),
            ),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation2,
            ) as mock_create2,
            patch.object(settings_views, "stage_ephemeral_registration_secrets"),
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"provider_key": "salesforce"},
            ) as mock_dispatch2,
            patch.object(settings_views, "messages") as mock_messages2,
        ):
            response2 = settings_views.local_settings_salesforce_registration(valid)
        self.assertEqual(response2.status_code, 302)
        mock_create2.assert_called_once()
        mock_dispatch2.assert_called_once()
        mock_messages2.success.assert_called_once()
        mock_messages2.error.assert_not_called()


class TrustBoundaryFormTokenTests(TestCase):
    """Split from TrustBoundaryTests after 7B-P Settings coverage insert."""

    def test_signed_token_binds_revision_and_projection(self):
        owner = uuid4()
        session = ImportSession.objects.create(owner_id=owner)
        workflow = ApiWorkflow.objects.create(
            session=session,
            run_id="run-x",
            workflow_key="key",
            workflow_version=1,
            status="awaiting_decision",
            stage="x",
            revision=1,
            resource_url="/x",
            projection=projection(run_id="run-x"),
            projection_digest="one",
        )
        token = issue_form_token(
            owner_id=owner,
            session=session,
            workflow=workflow,
            action_kind="decision",
            action_id="d1",
        )
        workflow.revision = 2
        workflow.save(update_fields=["revision"])
        with self.assertRaises(FormTokenError):
            validate_form_token(
                token,
                owner_id=owner,
                session=session,
                workflow=workflow,
                action_kind="decision",
                action_id="d1",
            )

    def test_get_does_not_retry_unknown_mutation(self):
        owner = uuid4()
        session = ImportSession.objects.create(owner_id=owner)
        workflow = ApiWorkflow.objects.create(
            session=session,
            run_id="run-x",
            workflow_key="key",
            workflow_version=1,
            status="running",
            stage="x",
            revision=1,
            resource_url="/x",
            projection=projection(run_id="run-x"),
            projection_digest="one",
        )
        session.active_workflow = workflow
        session.save(update_fields=["active_workflow"])
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route="/v1/workflows/run-x/effect-resumptions",
            logical_action_identity="run:run-x:revision:1:resume::group:",
            request_json={"expected_revision": 1},
        )
        mutation.state = ApiMutation.State.UNKNOWN
        mutation.save(update_fields=["state"])
        browser = self.client_class()
        browser.session["easyimports_owner_id"] = str(owner)
        browser.session.save()
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            browser.get(reverse("importer:workflow", args=[session.id]))
        mutation.refresh_from_db()
        self.assertEqual(mutation.attempt_count, 0)


class ContractAndFormTests(TestCase):
    def product(self, key, tracks):
        return {"product_key": key, "tracks": tracks}

    def target(self, tracks):
        return {"target_provider_id": "fake", "maximum_modes": tracks}

    def test_health_requires_exact_api_version(self):
        from importer.api_contract_generated import API_VERSION

        self.assertEqual(
            validate_health({"status": "ok", "api_version": API_VERSION})["status"],
            "ok",
        )
        with self.assertRaises(ValueError):
            validate_health({"status": "ok", "api_version": "1.1.0"})

    def test_unknown_resource_status_and_decision_are_safe_for_read_only_rendering(
        self,
    ):
        value = projection(
            status="future_status",
            decision={
                "decision_id": "d",
                "decision_type": "future_decision",
                "phase_id": "p",
                "body": {},
                "future_decision_field": "visible",
            },
            future_workflow_field="visible",
        )
        validated = validate_workflow_resource(value)
        self.assertEqual(validated["status"], "future_status")
        self.assertEqual(validated["future_workflow_field"], "visible")
        self.assertEqual(validated["decision"]["future_decision_field"], "visible")

    def test_account_list_requires_accounts_when_reference_disabled(self):
        product = self.product(
            "easyimports.account_list_import",
            {
                "reference_acquisition": ["disabled", "execute"],
                "account_provisioning": ["disabled"],
                "account_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        )
        target = self.target(
            {
                "reference_acquisition": "execute",
                "account_provisioning": "disabled",
                "account_writes": "disabled",
                "delivery": "preview",
            }
        )
        form = WorkflowConfigurationForm(
            {
                "form_token": "x",
                "mode__reference_acquisition": "disabled",
                "mode__account_provisioning": "disabled",
                "mode__account_writes": "disabled",
                "mode__delivery": "preview",
                "list_duplicate_policy": "surface",
                "crm_account_multi_match_policy": "review",
            },
            product_entry=product,
            target=target,
            uploaded_roles={"raw_list"},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Accounts upload", str(form.errors))

    def test_acquired_duplicate_references_reject_supplied_files(self):
        product = self.product(
            "easyimports.duplicate_resolution",
            {
                "reference_acquisition": ["disabled", "execute"],
                "duplicate_execution": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        )
        target = self.target(
            {
                "reference_acquisition": "execute",
                "duplicate_execution": "disabled",
                "delivery": "preview",
            }
        )
        form = WorkflowConfigurationForm(
            {
                "form_token": "x",
                "mode__reference_acquisition": "execute",
                "mode__duplicate_execution": "disabled",
                "mode__delivery": "preview",
                "entity": "account",
                "analysis_as_of_date": "2026-07-20",
            },
            product_entry=product,
            target=target,
            uploaded_roles={"canonical_records"},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cannot include supplied", str(form.errors))


class LogicalActionConcurrencyTests(TestCase):
    def setUp(self):
        self.session = ImportSession.objects.create(owner_id=uuid4())
        self.identity = f"session:{self.session.id}:create_workflow"

    def test_distinct_concurrent_form_instances_converge_on_logical_action(self):
        first = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_workflow",
            route="/v1/workflows",
            logical_action_identity=self.identity,
            request_json={"value": 1},
        )
        second_form = uuid4()
        second = create_or_reuse_mutation(
            session=self.session,
            form_instance=second_form,
            mutation_kind="create_workflow",
            route="/v1/workflows",
            logical_action_identity=self.identity,
            request_json={"value": 1},
        )
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.form_instance, first.form_instance)
        self.assertNotEqual(second.form_instance, second_form)
        self.assertEqual(ApiMutation.objects.count(), 1)

    def test_concurrent_token_with_changed_values_is_rejected_locally(self):
        create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_workflow",
            route="/v1/workflows",
            logical_action_identity=self.identity,
            form_payload_digest=canonical_digest({"editable": "first"}),
            request_json={"value": 1},
        )
        with self.assertRaises(MutationReuseError):
            create_or_reuse_mutation(
                session=self.session,
                form_instance=uuid4(),
                mutation_kind="create_workflow",
                route="/v1/workflows",
                logical_action_identity=self.identity,
                form_payload_digest=canonical_digest({"editable": "changed"}),
                request_json={"value": 1},
            )
        self.assertEqual(ApiMutation.objects.count(), 1)

    def test_first_submission_timestamp_is_frozen_inside_winning_mutation(self):
        built_at = []

        def build(value):
            built_at.append(value)
            return {"decided_at": value.isoformat()}

        first = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="submit_decision",
            route="/v1/workflows/run/decisions",
            logical_action_identity=self.identity,
            form_payload_digest="same-editable-payload",
            request_builder=build,
        )
        second = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="submit_decision",
            route="/v1/workflows/run/decisions",
            logical_action_identity=self.identity,
            form_payload_digest="same-editable-payload",
            request_builder=build,
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(built_at), 1)
        self.assertEqual(
            first.request_json["decided_at"], first.first_submitted_at.isoformat()
        )

    def test_rejected_acknowledgement_advances_only_non_conflicted_action(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_workflow",
            route="/v1/workflows",
            logical_action_identity=self.identity,
            request_json={"value": 1},
        )
        mutation.state = ApiMutation.State.REJECTED
        mutation.acknowledged_at = timezone.now()
        mutation.save(update_fields=["state", "acknowledged_at"])
        self.assertEqual(action_generation(self.session, self.identity), 1)
        mutation.error_code = "idempotency_conflict"
        mutation.save(update_fields=["error_code"])
        self.assertEqual(action_generation(self.session, self.identity), 0)

class _UploadDispatchClient:
    def __init__(self, state):
        self.state = state

    def dispatch(self, mutation, *, file_path=None, explicit_retry=False):
        if self.state == ApiMutation.State.UNKNOWN:
            mutation.state = ApiMutation.State.UNKNOWN
            mutation.error_message = "connection lost"
            mutation.save(update_fields=["state", "error_message"])
            raise ApiUnavailableError("The API response is uncertain.")
        if self.state == ApiMutation.State.REJECTED:
            payload = {"error": {"code": "bad_upload", "message": "No"}}
            mutation.error_code = "bad_upload"
            mutation.error_message = "No"
        else:
            payload = {
                "upload_id": f"upload-{mutation.id.hex[:8]}",
                "filename": mutation.multipart_metadata["filename"],
                "media_type": mutation.multipart_metadata["media_type"],
                "byte_count": mutation.multipart_metadata["byte_count"],
                "size_limit_bytes": 100000,
                "content_digest": mutation.multipart_metadata["content_digest"],
                "parser_contract": "csv-v1",
                "csv_encoding": mutation.multipart_metadata["form"].get("csv_encoding"),
                "xlsx_sheet": None,
                "row_count": 1,
                "columns": ["Name"],
                "created_at": "2026-07-20T00:00:00Z",
            }
        mutation.state = self.state
        mutation.response_json = payload
        mutation.response_digest = canonical_digest(payload)
        mutation.save(
            update_fields=[
                "state",
                "response_json",
                "response_digest",
                "error_code",
                "error_message",
            ]
        )
        return MutationDispatchResult(mutation, payload)


class UploadSlotTransactionTests(TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="easyimports-upload-tests-")
        self.override = override_settings(SESSIONS_ROOT=Path(self.directory))
        self.override.enable()
        self.session = ImportSession.objects.create(owner_id=uuid4())

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.directory, ignore_errors=True)

    @staticmethod
    def uploaded(name="rows.csv", content=b"Name\nExample\n"):
        return SimpleUploadedFile(name, content, content_type="text/csv")

    def register(
        self, *, generation, client, name="rows.csv", content=b"Name\nExample\n"
    ):
        return save_and_register_upload(
            session=self.session,
            role="raw_list",
            uploaded=self.uploaded(name, content),
            form_instance=uuid4(),
            logical_action_generation=generation,
            csv_encoding="utf-8-sig",
            xlsx_sheet_index=0,
            client=client,
        )

    def test_unknown_upload_reserves_slot_and_allows_exact_retry_only(self):
        with self.assertRaises(ApiUnavailableError):
            self.register(
                generation=0,
                client=_UploadDispatchClient(ApiMutation.State.UNKNOWN),
            )
        source = SourceFile.objects.get()
        original_path = source.path
        with self.assertRaises(MutationReuseError):
            self.register(
                generation=0,
                client=_UploadDispatchClient(ApiMutation.State.COMPLETED),
                content=b"Name\nChanged\n",
            )
        self.assertEqual(SourceFile.objects.count(), 1)
        self.assertEqual(ApiMutation.objects.count(), 1)
        self.assertTrue(original_path.exists())

    def test_rejected_slot_rotation_is_transactional_and_reuses_source_row(self):
        rejected = self.register(
            generation=0,
            client=_UploadDispatchClient(ApiMutation.State.REJECTED),
        )
        source_id = rejected.source.id
        old_path = rejected.source.path
        authorize_rejected_upload_replacement(rejected.source)
        completed = self.register(
            generation=1,
            client=_UploadDispatchClient(ApiMutation.State.COMPLETED),
            name="replacement.csv",
            content=b"Name\nReplacement\n",
        )
        self.assertEqual(completed.source.id, source_id)
        self.assertEqual(SourceFile.objects.count(), 1)
        self.assertEqual(ApiMutation.objects.count(), 2)
        self.assertEqual(completed.mutation.replacement_of_id, rejected.mutation.id)
        self.assertFalse(old_path.exists())
        self.assertTrue(completed.source.path.exists())

    def test_failed_rotation_rolls_back_mutation_and_retains_rejected_bytes(self):
        rejected = self.register(
            generation=0,
            client=_UploadDispatchClient(ApiMutation.State.REJECTED),
        )
        authorize_rejected_upload_replacement(rejected.source)
        old_path = rejected.source.path
        with patch.object(SourceFile, "save", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.register(
                    generation=1,
                    client=_UploadDispatchClient(ApiMutation.State.COMPLETED),
                    name="replacement.csv",
                    content=b"Name\nReplacement\n",
                )
        source = SourceFile.objects.get()
        self.assertEqual(source.upload_mutation_id, rejected.mutation.id)
        self.assertEqual(ApiMutation.objects.count(), 1)
        self.assertTrue(old_path.exists())

    def test_mutation_and_source_creation_roll_back_together(self):
        with patch(
            "importer.upload_service.SourceFile.objects.create",
            side_effect=RuntimeError("source crash"),
        ):
            with self.assertRaises(RuntimeError):
                self.register(
                    generation=0,
                    client=_UploadDispatchClient(ApiMutation.State.COMPLETED),
                )
        self.assertEqual(ApiMutation.objects.count(), 0)
        self.assertEqual(SourceFile.objects.count(), 0)
        self.assertEqual(list(self.session.uploads_dir.glob("*")), [])

    def test_database_enforces_one_active_source_per_role(self):
        first = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="register_upload",
            route="/v1/uploads",
            logical_action_identity=f"upload:{self.session.id}:raw_list",
            multipart_metadata={"filename": "one.csv"},
        )
        second = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="register_upload",
            route="/v1/uploads",
            logical_action_identity=f"upload:{self.session.id}:raw_list:other",
            multipart_metadata={"filename": "two.csv"},
        )
        SourceFile.objects.create(
            session=self.session,
            role="raw_list",
            original_name="one.csv",
            stored_path="one.csv",
            upload_mutation=first,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceFile.objects.create(
                session=self.session,
                role="raw_list",
                original_name="two.csv",
                stored_path="two.csv",
                upload_mutation=second,
            )


def dataframe(columns, rows):
    return {"columns": columns, "rows": rows}


class WorkflowInterfaceTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def make_session_workflow(
        self, value, *, role=ApiWorkflow.Role.PRIMARY, source=None
    ):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=value["workflow_key"],
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        workflow = ApiWorkflow.objects.create(
            session=session,
            source_workflow=source,
            role=role,
            run_id=value["run_id"],
            workflow_key=value["workflow_key"],
            workflow_version=value["workflow_version"],
            target_provider_id=value.get("target_provider_id") or "",
            status=value["status"],
            stage=value["stage"],
            revision=value["revision"],
            resource_url=f"/v1/workflows/{value['run_id']}",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        session.active_workflow = workflow
        session.save(update_fields=["active_workflow"])
        return session, workflow

    def make_completed_setup_upload(
        self,
        *,
        product_key="easyimports.single_dataset_import",
        role="dataset",
        entity="accounts",
        operation="clean_only",
        reference_source="none",
        people_output="",
    ):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            # product_key remains empty until workflow create freezes it.
            product_key="",
            setup_entity=entity,
            setup_operation=operation,
            setup_reference_source=reference_source,
            setup_people_output=people_output,
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-internal-1",
        )
        SourceFile.objects.create(
            session=session,
            role=role,
            original_name="customers.csv",
            stored_path="customers.csv",
            api_upload_id="upload-internal-1",
            upload_mutation=mutation,
        )
        return session

    def test_landing_uses_customer_labels_for_recent_imports(self):
        ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            target_provider_id="fake-preview-v1",
            status=ImportSession.Status.RUNNING,
        )
        page = self.client.get(reverse("importer:landing"))
        self.assertContains(page, "Prepare CRM imports with confidence.")
        self.assertContains(page, "Accounts")
        self.assertContains(page, "clean and prepare")
        self.assertContains(page, "In progress")
        self.assertNotContains(page, ">easyimports.single_dataset_import<")

    def test_product_selection_uses_friendly_destination_label(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        with (
            patch.object(
                EasyImportsApiClient,
                "targets",
                return_value={
                    "targets": [
                        {
                            "target_provider_id": "fake-preview-v1",
                            "maximum_modes": {},
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:product", args=[session.id]))
        self.assertContains(page, "Choose what you want to prepare")
        self.assertContains(page, "What are you preparing?")
        self.assertContains(page, "What should EasyImports do?")
        # Phase 7A/7B: match-first default; target field hidden (D6).
        self.assertContains(page, "Match it against my CRM")
        self.assertContains(page, "Clean and prepare my file (no matching)")
        self.assertNotContains(page, "Where should the results go?")
        self.assertNotContains(page, "Not needed for clean-only")
        self.assertContains(page, "Technical details")
        self.assertNotContains(page, "Single dataset")
        # Target remains only in technical details / hidden input, not a visible label.
        self.assertNotContains(page, "Preview files only")
        self.assertNotContains(page, ">fake-preview-v1<")
        self.assertNotIn(
            b'<details class="card run-details technical-details" open',
            page.content,
        )

    def test_setup_service_failure_keeps_backend_error_in_closed_details(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        with patch.object(
            EasyImportsApiClient,
            "targets",
            side_effect=ApiUnavailableError("backend connection detail"),
        ):
            page = self.client.get(reverse("importer:product", args=[session.id]))
        customer_view = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"EasyImports is temporarily unavailable", customer_view)
        self.assertIn(b"No import action was", customer_view)
        self.assertNotIn(b"backend connection detail", customer_view)
        self.assertContains(page, "backend connection detail")

    def test_upload_page_hides_upload_identifier_in_closed_details(self):
        session = self.make_completed_setup_upload()
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        customer_view = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Add your files", customer_view)
        self.assertIn(b"Accounts", customer_view)
        self.assertIn(b"customers.csv", customer_view)
        self.assertIn(b"Ready", customer_view)
        self.assertNotIn(b"upload-internal-1", customer_view)
        self.assertContains(page, "upload-internal-1")

    def test_configuration_uses_plain_language_and_closed_contract_details(self):
        session = self.make_completed_setup_upload()
        # MAP-R5: Start import is gated until mapping is confirmed and current.
        digests = {
            "plan_content_digest": "a" * 64,
            "source_schema_digest": "b" * 64,
            "destination_digest": "c" * 64,
            "target_contract_digest": "d" * 64,
        }
        session.options = {
            "column_mapping_map2": {
                "plan_id": "cmp_plain_language",
                "status": "confirmed",
                "confirmed_digests": digests,
                "upload_id": "upload-internal-1",
                "destination": {
                    "destination_mode": "catalog",
                    "catalog_id": "easyimports.account_fields.v1",
                },
                "source_headers": ["Company", "Website"],
            }
        }
        session.save(update_fields=["options", "updated_at"])
        # Align upload headers with draft binding for currency check.
        source = session.files.filter(api_upload_id="upload-internal-1").first()
        if source is not None:
            source.columns = ["Company", "Website"]
            source.save(update_fields=["columns"])
        products = {
            "products": [
                {
                    "product_key": "easyimports.single_dataset_import",
                    "tracks": {
                        "dataset_writes": ["disabled"],
                        "delivery": ["disabled", "preview"],
                    },
                }
            ]
        }
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "preview",
                    },
                }
            ]
        }
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:configure", args=[session.id]))
        customer_view = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        for expected in (
            b"Review your import settings",
            b"Start import",
            b"Preview files only",
        ):
            self.assertIn(expected, customer_view)
        # OUT-6B: clean-only hides Dataset changes; the field still submits.
        self.assertNotIn(b"Dataset changes", customer_view)
        self.assertNotIn(
            b"Choose whether downloadable result files should be created",
            customer_view,
        )
        self.assertNotIn(b"Output files", customer_view)
        # Product key stays technical-details only.
        self.assertNotIn(b"easyimports.single_dataset_import", customer_view)
        self.assertNotIn(b"fake-preview-v1", customer_view)
        self.assertNotIn(b"upload-internal-1", customer_view)
        self.assertContains(page, "upload-internal-1")
        self.assertContains(page, "easyimports.single_dataset_import")

    @staticmethod
    def interactive_decision(dtype):
        row = {
            "option_id": "option-1",
            "group_id": "group-1",
            "contact_email_final": "old@example.com",
        }
        if dtype in {
            "list_duplicates",
            "multiple_crm_matches",
            "multiple_crm_account_matches",
            "uploaded_account_id_disagreement",
            "uploaded_person_id_disagreement",
        }:
            row.update(
                {
                    "option_kind": "candidate",
                    "option_label": "Use this option",
                    "selected": True,
                    "is_recommended": True,
                }
            )
        body = {
            "decision_id": f"decision-{dtype}",
            "decision_type": dtype,
            "title": f"Review {dtype}",
            "message": "Inspect the stored candidates.",
            "rows_df": dataframe(list(row), [row]),
            "columns": [
                {"name": key, "label": key, "visible": True, "editable": False}
                for key in row
            ],
            "actions": (
                ["submit_edits", "exclude_remaining_invalid_rows"]
                if dtype == "validation_failed"
                else ["submit_selections"]
            ),
            "option_id_col": "option_id",
            "group_id_col": "group_id",
            "status": "open",
            "resolved_group_ids": [],
            "audit_df": dataframe(["event"], [{"event": "created"}]),
        }
        return {
            "decision_id": body["decision_id"],
            "decision_type": dtype,
            "phase_id": "review",
            "body": body,
        }

    @staticmethod
    def duplicate_review_decision(dtype):
        member_key = "account_id" if dtype.startswith("account_") else "person_id"
        member_id = "001-survivor" if member_key == "account_id" else "003-survivor"
        common = {
            "object_type": "Account" if member_key == "account_id" else "Person",
            "duplicate_group_id": "group-1",
            "group_revision": "revision-1",
            "confidence_score": 92,
            "confidence_band": "high",
            "review_lane": "standard",
            "advanced_review_required": False,
            "recommended_survivor_id": member_id,
            "selected_survivor_id": member_id,
            "allowed_actions": [
                "approve",
                "decline",
                "quarantine",
                "override_survivor",
            ],
            "title": "Duplicate group review",
            "message": "Review members, evidence, conflicts, and survivor projection.",
        }
        if member_key == "person_id":
            common.update(
                {
                    "group_status": "review_required",
                    "execution_blockers": [],
                    "recommended_survivor_type": "contact",
                    "selected_survivor_type": "contact",
                }
            )
        table_names = [
            "group_df",
            "group_members_df",
            "accounts_df" if member_key == "account_id" else "people_df",
            "evidence_df",
            "conflicts_df",
            "edge_decisions_df",
            "ranking_df",
            "recommended_survivor_df",
            "selected_survivor_df",
            "projected_survivor_df",
            "field_recommendations_df",
            "merge_conflicts_df",
            "revision_df",
        ]
        if member_key == "person_id":
            table_names.append("quarantined_people_df")
        for name in table_names:
            common[name] = dataframe(
                [member_key, "evidence"],
                [{member_key: member_id, "evidence": name}],
            )
        return {
            "decision_id": f"decision-{dtype}",
            "decision_type": dtype,
            "phase_id": "duplicate_review",
            "body": common,
        }

    @staticmethod
    def reject_dispatch(mutation, **_kwargs):
        payload = receipt(outcome="rejected")
        mutation.state = ApiMutation.State.REJECTED
        mutation.response_json = payload
        mutation.response_digest = canonical_digest(payload)
        mutation.error_code = "request_rejected"
        mutation.error_message = "No"
        mutation.save()
        return MutationDispatchResult(mutation, payload)

    def test_all_six_decision_types_render_and_submit_strict_commands(self):
        decision_types = (
            "validation_failed",
            "list_duplicates",
            "multiple_crm_matches",
            "multiple_crm_account_matches",
            "uploaded_account_id_disagreement",
            "uploaded_person_id_disagreement",
            "account_duplicate_group_review",
            "person_duplicate_group_review",
        )
        for index, dtype in enumerate(decision_types):
            with self.subTest(dtype=dtype):
                decision = (
                    self.duplicate_review_decision(dtype)
                    if dtype.endswith("duplicate_group_review")
                    else self.interactive_decision(dtype)
                )
                value = projection(
                    run_id=f"run-decision-{index}",
                    status="needs_decision",
                    stage="review",
                    decision=decision,
                )
                session, workflow = self.make_session_workflow(value)
                with patch(
                    "importer.workflow_views.refresh_workflow",
                    return_value=workflow,
                ):
                    page = self.client.get(
                        reverse("importer:workflow", args=[session.id])
                    )
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, decision["body"]["title"])
                if dtype.endswith("duplicate_group_review"):
                    self.assertContains(page, "Projected Survivor")
                    self.assertContains(page, "Evidence")
                    self.assertContains(page, "Conflicts")
                    self.assertContains(page, "Choose the winner for this group")
                    self.assertContains(page, "Confirm recommended winner")
                    self.assertContains(page, "Confirm winner and continue")
                    self.assertContains(page, "Skip this group")
                    self.assertContains(page, 'class="recommendation-badge"')
                    self.assertContains(page, 'name="duplicate_choice"')
                    self.assertContains(
                        page, "Default winner is pre-selected"
                    )
                    post = {
                        "form_token": page.context["tokens"]["decision"],
                        "duplicate_choice": (
                            "survivor:"
                            + decision["body"]["selected_survivor_id"]
                        ),
                    }
                elif dtype == "validation_failed":
                    post = {
                        "form_token": page.context["tokens"]["decision"],
                        "action": "submit_edits",
                        "email__option-1": "new@example.com",
                    }
                else:
                    post = {
                        "form_token": page.context["tokens"]["decision"],
                        "action": "submit_selections",
                        "selected__0": "option-1",
                    }
                with patch.object(
                    EasyImportsApiClient,
                    "dispatch",
                    side_effect=self.reject_dispatch,
                ):
                    submitted = self.client.post(
                        reverse("importer:submit_decision", args=[session.id]),
                        post,
                    )
                self.assertEqual(submitted.status_code, 302)
                mutation = session.api_mutations.get(mutation_kind="submit_decision")
                validated = validate_decision_command(mutation.request_json)
                self.assertEqual(validated["decision_type"], dtype)
                if dtype.endswith("duplicate_group_review"):
                    self.assertEqual(validated["response"]["action"], "approve")
                    self.assertEqual(
                        validated["response"]["selected_survivor_id"],
                        decision["body"]["selected_survivor_id"],
                    )
                    self.assertEqual(
                        validated["response"]["decided_by"], "Alice Operator"
                    )
                    self.assertEqual(
                        validated["response"]["decided_at"],
                        mutation.first_submitted_at.isoformat().replace("+00:00", "Z"),
                    )
                    session.refresh_from_db()
                    self.assertIsNotNone(session.operator_label_frozen_at)

    def test_duplicate_group_review_skip_and_override_survivor_choices(self):
        """Phase 4D: skip and change-survivor map to decline / override_survivor."""

        for index, (dtype, action, choice_suffix) in enumerate(
            (
                (
                    "account_duplicate_group_review",
                    "decline",
                    "skip",
                ),
                (
                    "person_duplicate_group_review",
                    "override_survivor",
                    "survivor:003-other",
                ),
            )
        ):
            with self.subTest(dtype=dtype, action=action):
                decision = self.duplicate_review_decision(dtype)
                if action == "override_survivor":
                    member_key = "person_id"
                    decision["body"]["group_members_df"] = dataframe(
                        [member_key, "evidence"],
                        [
                            {
                                member_key: decision["body"]["selected_survivor_id"],
                                "evidence": "primary",
                            },
                            {member_key: "003-other", "evidence": "alternate"},
                        ],
                    )
                value = projection(
                    run_id=f"run-dup-outcome-{index}",
                    status="needs_decision",
                    stage="review",
                    decision=decision,
                )
                session, workflow = self.make_session_workflow(value)
                with patch(
                    "importer.workflow_views.refresh_workflow",
                    return_value=workflow,
                ):
                    page = self.client.get(
                        reverse("importer:workflow", args=[session.id])
                    )
                self.assertEqual(page.status_code, 200)
                with patch.object(
                    EasyImportsApiClient,
                    "dispatch",
                    side_effect=self.reject_dispatch,
                ):
                    submitted = self.client.post(
                        reverse("importer:submit_decision", args=[session.id]),
                        {
                            "form_token": page.context["tokens"]["decision"],
                            "duplicate_choice": choice_suffix,
                        },
                    )
                self.assertEqual(submitted.status_code, 302)
                mutation = session.api_mutations.get(mutation_kind="submit_decision")
                validated = validate_decision_command(mutation.request_json)
                self.assertEqual(validated["response"]["action"], action)
                if action == "override_survivor":
                    self.assertEqual(
                        validated["response"]["selected_survivor_id"], "003-other"
                    )
                else:
                    self.assertEqual(
                        validated["response"]["selected_survivor_id"],
                        decision["body"]["selected_survivor_id"],
                    )

    def test_duplicate_review_finish_for_now_exports_remaining_groups(self):
        decision = self.duplicate_review_decision(
            "account_duplicate_group_review"
        )
        value = projection(
            run_id="run-finish-for-now",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        value["review_progress"] = {
            "entity": "account",
            "decided_group_count": 2,
            "remaining_group_count": 8,
            "finish_for_now_available": True,
            "finished_for_now": False,
            "deferred_group_count": 0,
            "remaining_groups_exported": False,
        }
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[session.id])
            )
        self.assertContains(page, "Finish for now")
        self.assertContains(page, "8 remaining")

        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=self.reject_dispatch,
        ):
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": page.context["tokens"]["decision"],
                    "action": "finish_for_now",
                    "remaining_group_disposition": "export",
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="submit_decision")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["response"]["action"], "finish_for_now")
        self.assertEqual(
            validated["response"]["remaining_group_disposition"], "export"
        )
        self.assertFalse(validated["response"]["advanced_review"])

    def test_grouped_match_decisions_render_labeled_radio_tables(self):
        for index, dtype in enumerate(
            (
                "list_duplicates",
                "multiple_crm_matches",
                "multiple_crm_account_matches",
            )
        ):
            with self.subTest(dtype=dtype):
                rows = [
                    {
                        "option_id": "option-1",
                        "group_id": "group-1",
                        "option_kind": "candidate",
                        "option_label": "Use this record",
                        "name": "First candidate",
                        "match_score": 95,
                        "selected": True,
                        "is_recommended": False,
                    },
                    {
                        "option_id": "option-2",
                        "group_id": "group-1",
                        "option_kind": {
                            "list_duplicates": "exclude_group",
                            "multiple_crm_matches": "continue_unmatched",
                            "multiple_crm_account_matches": (
                                "force_lead_without_account"
                            ),
                        }[dtype],
                        "option_label": {
                            "list_duplicates": "Exclude every row in this group",
                            "multiple_crm_matches": "Continue with a new person",
                            "multiple_crm_account_matches": (
                                "Continue without an Account and route this "
                                "person as a Lead"
                            ),
                        }[dtype],
                        "name": "Second candidate",
                        "match_score": 85,
                        "selected": False,
                        "is_recommended": False,
                    },
                    {
                        "option_id": "option-3",
                        "group_id": "group-2",
                        "option_kind": "candidate",
                        "option_label": "Use this record",
                        "name": "Third candidate",
                        "match_score": 90,
                        "selected": False,
                        "is_recommended": True,
                    },
                    {
                        "option_id": "option-4",
                        "group_id": "group-2",
                        "option_kind": "candidate",
                        "option_label": "Use this record",
                        "name": "Fourth candidate",
                        "match_score": 80,
                        "selected": False,
                        "is_recommended": False,
                    },
                ]
                decision = self.interactive_decision(dtype)
                decision["body"]["rows_df"] = dataframe(list(rows[0]), rows)
                decision["body"]["columns"] = [
                    {
                        "name": "option_id",
                        "label": "Option ID",
                        "visible": False,
                        "editable": False,
                    },
                    {
                        "name": "group_id",
                        "label": "Group ID",
                        "visible": False,
                        "editable": False,
                    },
                    {
                        "name": "name",
                        "label": "Candidate",
                        "visible": True,
                        "editable": False,
                    },
                    {
                        "name": "match_score",
                        "label": "Match score",
                        "visible": True,
                        "editable": False,
                    },
                    {
                        "name": "selected",
                        "label": "Selected",
                        "visible": True,
                        "editable": False,
                    },
                    {
                        "name": "is_recommended",
                        "label": "Recommended",
                        "visible": True,
                        "editable": False,
                    },
                ]
                value = projection(
                    run_id=f"run-grouped-decision-{index}",
                    status="needs_decision",
                    stage="review",
                    decision=decision,
                )
                session, workflow = self.make_session_workflow(value)
                with patch(
                    "importer.workflow_views.refresh_workflow",
                    return_value=workflow,
                ):
                    page = self.client.get(
                        reverse("importer:workflow", args=[session.id])
                    )

                self.assertEqual(page.status_code, 200)
                # OUT-7: only review-core ∪ typed extras that exist on the
                # fixture. ``name`` is a CRM-person extra alias, not a
                # list-dupe or Account-match extra. ``match_score`` is a
                # CRM-match extra only.
                self.assertEqual(
                    [
                        column["label"]
                        for column in page.context["decision_group_columns"]
                    ],
                    {
                        "list_duplicates": [],
                        "multiple_crm_matches": ["Candidate", "Match score"],
                        "multiple_crm_account_matches": ["Match score"],
                    }[dtype],
                )
                groups = page.context["decision_groups"]
                self.assertEqual(len(groups), 2)
                self.assertEqual(
                    [group["input_name"] for group in groups],
                    ["selected__0", "selected__1"],
                )
                self.assertTrue(groups[0]["rows"][0]["selected"])
                self.assertTrue(groups[0]["rows"][1]["synthetic"])
                self.assertFalse(groups[0]["rows"][1]["read_only"])
                self.assertEqual(
                    groups[0]["rows"][1]["option_label"],
                    rows[1]["option_label"],
                )
                self.assertFalse(groups[1]["rows"][0]["selected"])
                self.assertTrue(groups[1]["rows"][0]["recommended"])
                self.assertContains(page, 'type="radio"', count=4)
                self.assertContains(page, 'class="recommendation-badge"', count=1)
                self.assertNotContains(page, "Select the stored option IDs")

                with patch.object(
                    EasyImportsApiClient,
                    "dispatch",
                    side_effect=self.reject_dispatch,
                ):
                    submitted = self.client.post(
                        reverse("importer:submit_decision", args=[session.id]),
                        {
                            "form_token": page.context["tokens"]["decision"],
                            "action": "submit_selections",
                            "selected__0": "option-2",
                            "selected__1": "option-4",
                        },
                    )
                self.assertEqual(submitted.status_code, 302)
                mutation = session.api_mutations.get(mutation_kind="submit_decision")
                self.assertEqual(
                    mutation.request_json["response"]["groups"],
                    [
                        {
                            "group_id": "group-1",
                            "selected_option_id": "option-2",
                        },
                        {
                            "group_id": "group-2",
                            "selected_option_id": "option-4",
                        },
                    ],
                )
                with patch(
                    "importer.workflow_views.refresh_workflow",
                    return_value=workflow,
                ):
                    restored = self.client.get(
                        reverse("importer:workflow", args=[session.id])
                    )
                restored_groups = restored.context["decision_groups"]
                self.assertEqual(
                    [
                        [row["option_id"] for row in group["rows"] if row["selected"]]
                        for group in restored_groups
                    ],
                    [["option-2"], ["option-4"]],
                )
                self.assertTrue(restored_groups[1]["rows"][0]["recommended"])

    def test_crm_match_comparison_separates_uploaded_context_from_candidate_evidence(
        self,
    ):
        decision = self.interactive_decision("multiple_crm_matches")
        rows = [
            {
                "option_id": "candidate-1",
                "group_id": "group-1",
                "option_kind": "candidate",
                "option_label": "Use this CRM person",
                "incoming_contact_full_name": "Pat Example",
                "incoming_contact_email": "pat@example.com",
                "incoming_acct_name": "Example Co",
                "candidate_person_full_name": "Pat Example",
                "candidate_person_email": "pat@example.com",
                "candidate_person_type": "Contact",
                "candidate_person_title": "VP Sales",
                "candidate_acct_name": "Example Co",
                "candidate_source": "exact_email",
                "match_score": 100,
                "selected": True,
                "is_recommended": True,
            },
            {
                "option_id": "candidate-2",
                "group_id": "group-1",
                "option_kind": "candidate",
                "option_label": "Use this CRM person",
                "incoming_contact_full_name": "Pat Example",
                "incoming_contact_email": "pat@example.com",
                "incoming_acct_name": "Example Co",
                "candidate_person_full_name": "Patrick Example",
                "candidate_person_email": "patrick@example.com",
                "candidate_person_type": "Lead",
                "candidate_person_title": "Sales Director",
                "candidate_acct_name": "Example Co",
                "candidate_source": "fuzzy_name",
                "match_score": 87,
                "selected": False,
                "is_recommended": False,
            },
        ]
        decision["body"]["rows_df"] = dataframe(list(rows[0]), rows)
        decision["body"]["columns"] = [
            {
                "name": name,
                "label": {
                    "incoming_contact_full_name": "Uploaded Name",
                    "incoming_contact_email": "Uploaded Email",
                    "incoming_acct_name": "Uploaded Account",
                    "candidate_person_full_name": "CRM Name",
                    "candidate_person_email": "CRM Email",
                    "candidate_person_type": "CRM Object",
                    "candidate_person_title": "CRM Title",
                    "candidate_acct_name": "CRM Account",
                    "candidate_source": "Match Method",
                    "match_score": "Match Score",
                }.get(name, name),
                "visible": True,
                "editable": False,
            }
            for name in rows[0]
        ]
        value = projection(
            run_id="run-crm-match-evidence",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        # OUT-7 extras for multiple_crm_matches are candidate name / email /
        # type / match score. Title, Account, and match method are not
        # review-core and are not typed extras.
        self.assertEqual(
            [item["label"] for item in page.context["decision_group_columns"]],
            [
                "CRM Name",
                "CRM Email",
                "CRM Object",
                "Match Score",
            ],
        )
        self.assertEqual(
            page.context["decision_groups"][0]["context_items"],
            [
                {"label": "Uploaded person", "value": "Pat Example"},
                {"label": "Email", "value": "pat@example.com"},
                {"label": "Account", "value": "Example Co"},
            ],
        )
        self.assertContains(page, "Match score 100")
        self.assertContains(page, "Matched by exact email")
        self.assertContains(page, "Existing Contact")

    def test_crm_match_context_dual_reads_current_and_omits_empty_account(self):
        """ACCT-API rem: current account keys render; empty account context omitted."""

        from importer.workflow_views import _grouped_context_items

        current_row = {
            "incoming_contact_full_name": "Pat Example",
            "incoming_contact_email": "pat@example.com",
            "incoming_account_name": "Example Co",
        }
        items = _grouped_context_items("multiple_crm_matches", current_row)
        self.assertEqual(
            items,
            [
                {"label": "Uploaded person", "value": "Pat Example"},
                {"label": "Email", "value": "pat@example.com"},
                {"label": "Account", "value": "Example Co"},
            ],
        )
        empty_account = {
            "incoming_contact_full_name": "Pat Example",
            "incoming_contact_email": "pat@example.com",
        }
        items_empty = _grouped_context_items("multiple_crm_matches", empty_account)
        self.assertEqual(
            [item["label"] for item in items_empty],
            ["Uploaded person", "Email"],
        )

    def test_list_duplicate_comparison_explains_completeness_ranking(self):
        decision = self.interactive_decision("list_duplicates")
        rows = [
            {
                "option_id": "row-1",
                "group_id": "group-1",
                "option_kind": "uploaded_row",
                "option_label": "Keep this uploaded row",
                "contact_full_name_final": "Taylor Example",
                "contact_email_final": "taylor@example.com",
                "acct_name_final": "Example Co",
                "list_duplicate_evidence_rules": "exact_email",
                "list_duplicate_has_account_identity": True,
                "list_duplicate_has_email": True,
                "list_duplicate_has_full_name": True,
                "list_duplicate_filled_field_count": 8,
                "list_duplicate_recommendation_rank": 1,
                "selected": True,
                "is_recommended": True,
            },
            {
                "option_id": "row-2",
                "group_id": "group-1",
                "option_kind": "uploaded_row",
                "option_label": "Keep this uploaded row",
                "contact_full_name_final": "Taylor Example",
                "contact_email_final": "taylor@example.com",
                "acct_name_final": "",
                "list_duplicate_evidence_rules": "exact_email",
                "list_duplicate_has_account_identity": False,
                "list_duplicate_has_email": True,
                "list_duplicate_has_full_name": True,
                "list_duplicate_filled_field_count": 5,
                "list_duplicate_recommendation_rank": 2,
                "selected": False,
                "is_recommended": False,
            },
        ]
        decision["body"]["rows_df"] = dataframe(list(rows[0]), rows)
        decision["body"]["columns"] = [
            {
                "name": name,
                "label": _label,
                "visible": True,
                "editable": False,
            }
            for name, _label in (
                ("option_id", "Internal option"),
                ("group_id", "Internal group"),
                ("contact_full_name_final", "Name"),
                ("contact_email_final", "Primary Email"),
                ("acct_name_final", "Account"),
                ("list_duplicate_evidence_rules", "Matched Because"),
                ("list_duplicate_has_account_identity", "Has Account Identity"),
                ("list_duplicate_has_email", "Has Email"),
                ("list_duplicate_has_full_name", "Has Full Name"),
                ("list_duplicate_filled_field_count", "Useful Fields"),
                ("list_duplicate_recommendation_rank", "Recommendation Rank"),
                ("selected", "Winner"),
                ("is_recommended", "Recommended"),
            )
        ]
        value = projection(
            run_id="run-list-rationale",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        self.assertContains(page, "Completeness rank 1")
        self.assertContains(page, "8 useful fields")
        self.assertContains(page, "Has account identity")
        self.assertContains(page, "Matched on exact email")

    def test_duplicate_group_review_surfaces_progress_and_default_winner(self):
        decision = self.duplicate_review_decision("account_duplicate_group_review")
        value = projection(
            run_id="run-dup-progress",
            workflow_key="easyimports.duplicate_resolution",
            status="needs_decision",
            stage="review",
            decision=decision,
            review_progress={
                "entity": "account",
                "decided_group_count": 1,
                "remaining_group_count": 3,
                "finish_for_now_available": True,
                "finished_for_now": False,
                "deferred_group_count": 0,
                "remaining_groups_exported": False,
            },
        )
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Group 2 of 4")
        self.assertContains(page, "1 confirmed")
        self.assertContains(page, "3 remaining")
        self.assertContains(page, "Default winner is pre-selected")
        self.assertContains(page, "Confirm recommended winner")
        self.assertContains(page, "Confirm winner and continue")
        progress = page.context["duplicate_review_progress"]
        self.assertEqual(progress["current_index"], 2)
        self.assertEqual(progress["total_group_count"], 4)
        self.assertEqual(
            progress["selected_survivor_id"],
            decision["body"]["selected_survivor_id"],
        )

    def test_duplicate_execution_effect_names_merge_authorization(self):
        value = projection(
            run_id="run-dup-effect",
            workflow_key="easyimports.duplicate_resolution",
            status="awaiting_effect_authorization",
            stage="duplicate_execution",
            effect_intent={
                "intent_id": "intent-merge-1",
                "track": "duplicate_execution",
                "gate_phase_id": "gate",
                "effect_phase_id": "effect",
                "maximum_mode": "execute",
                "supported_modes": ["dry_run", "execute"],
                "target_provider_id": "fake-crm-v1",
                "target_fingerprint": "fp-1",
                "work_digest": "digest-1",
                "confirmation": "Apply approved duplicate merges",
            },
            summary={"approved_groups": 2, "declined_groups": 0},
        )
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Authorize CRM merges")
        self.assertContains(page, "Apply approved merges in CRM")
        self.assertContains(page, "Test merges without changing CRM")
        self.assertContains(page, "Authorize merge step")
        effect = page.context["effect_presentation"]
        self.assertEqual(effect["submit_label"], "Authorize merge step")
        mode_labels = {item["value"]: item["label"] for item in effect["mode_options"]}
        self.assertEqual(mode_labels["execute"], "Apply approved merges in CRM")

    def test_duplicate_resolution_review_complete_uses_merge_path_labels(self):
        value = projection(
            run_id="run-review-done",
            workflow_key="easyimports.duplicate_resolution.account_review",
            status="succeeded",
            stage="complete",
        )
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        source = ApiWorkflow.objects.create(
            session=session,
            role=ApiWorkflow.Role.PRIMARY,
            run_id="run-source-dr",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-preview-v1",
            status="awaiting_review",
            stage="review_handoff",
            revision=1,
            resource_url="/v1/workflows/run-source-dr",
            projection=projection(
                run_id="run-source-dr",
                workflow_key="easyimports.duplicate_resolution",
                status="awaiting_review",
                stage="review_handoff",
            ),
            projection_digest=canonical_digest(
                projection(run_id="run-source-dr")
            ),
        )
        review = ApiWorkflow.objects.create(
            session=session,
            source_workflow=source,
            role=ApiWorkflow.Role.REVIEW,
            run_id="run-review-done",
            workflow_key="easyimports.duplicate_resolution.account_review",
            workflow_version=1,
            target_provider_id="fake-preview-v1",
            status="succeeded",
            stage="complete",
            revision=1,
            resource_url="/v1/workflows/run-review-done",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        session.active_workflow = review
        session.save(update_fields=["active_workflow"])
        with (
            patch(
                "importer.workflow_views.refresh_workflow", return_value=review
            ),
            patch.object(
                EasyImportsApiClient,
                "artifacts",
                return_value={"artifacts": []},
            ),
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertContains(page, "Review complete — continue to merge authorization")
        self.assertContains(page, "Package confirmed winners")
        self.assertNotContains(page, "Continue source import")

    def test_duplicate_survivor_comparison_joins_identity_and_ranking_evidence(self):
        decision = self.duplicate_review_decision(
            "account_duplicate_group_review"
        )
        decision["body"]["recommended_survivor_id"] = "acct-a"
        decision["body"]["selected_survivor_id"] = "acct-a"
        decision["body"]["group_members_df"] = dataframe(
            ["account_id", "member_order"],
            [
                {"account_id": "acct-a", "member_order": 1},
                {"account_id": "acct-b", "member_order": 2},
            ],
        )
        decision["body"]["accounts_df"] = dataframe(
            [
                "__dr_account_id",
                "acct_name_final",
                "acct_domain_final",
                "acct_type",
                "acct_billing_city",
                "acct_billing_state",
            ],
            [
                {
                    "__dr_account_id": "acct-a",
                    "acct_name_final": "Acme Incorporated",
                    "acct_domain_final": "acme.example",
                    "acct_type": "Customer",
                    "acct_billing_city": "Boston",
                    "acct_billing_state": "MA",
                },
                {
                    "__dr_account_id": "acct-b",
                    "acct_name_final": "Acme Inc",
                    "acct_domain_final": "acme.example",
                    "acct_type": "Prospect",
                    "acct_billing_city": "Cambridge",
                    "acct_billing_state": "MA",
                },
            ],
        )
        decision["body"]["ranking_df"] = dataframe(
            [
                "account_id",
                "rank",
                "total_health_score",
                "is_customer",
                "open_opportunity_count",
                "last_activity_age_days",
                "owner_display_value",
                "is_preferred_owner",
                "completeness_score",
            ],
            [
                {
                    "account_id": "acct-a",
                    "rank": 1,
                    "total_health_score": 850,
                    "is_customer": True,
                    "open_opportunity_count": 2,
                    "last_activity_age_days": 5,
                    "owner_display_value": "Preferred Rep",
                    "is_preferred_owner": True,
                    "completeness_score": 300,
                },
                {
                    "account_id": "acct-b",
                    "rank": 2,
                    "total_health_score": 420,
                    "is_customer": False,
                    "open_opportunity_count": 0,
                    "last_activity_age_days": 90,
                    "owner_display_value": "Other Rep",
                    "is_preferred_owner": False,
                    "completeness_score": 180,
                },
            ],
        )
        value = projection(
            run_id="run-survivor-rationale",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        self.assertEqual(
            [item["label"] for item in page.context["duplicate_choice_columns"]],
            ["Account", "Domain", "Type", "Owner", "Location"],
        )
        self.assertContains(page, "Acme Incorporated")
        self.assertContains(page, "Health score 850")
        self.assertContains(page, "Customer record")
        self.assertContains(page, "2 open opportunities")
        self.assertContains(page, "Preferred owner")
        self.assertNotContains(page, '<th scope="col">Record</th>')
        self.assertNotContains(page, "<details open")

    def test_unknown_grouped_option_kind_is_read_only_and_cannot_be_submitted(self):
        decision = self.interactive_decision("multiple_crm_matches")
        rows = [
            {
                "option_id": "candidate",
                "group_id": "group-1",
                "option_kind": "candidate",
                "option_label": "Use this CRM person",
                "selected": True,
                "is_recommended": True,
            },
            {
                "option_id": "future",
                "group_id": "group-1",
                "option_kind": "future_outcome",
                "option_label": "Future outcome",
                "selected": False,
                "is_recommended": False,
            },
        ]
        decision["body"]["rows_df"] = dataframe(list(rows[0]), rows)
        value = projection(
            run_id="run-unknown-grouped-option",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        group_rows = page.context["decision_groups"][0]["rows"]
        self.assertFalse(group_rows[0]["read_only"])
        self.assertTrue(group_rows[1]["read_only"])
        self.assertContains(page, "Unavailable in this web version")

        submitted = self.client.post(
            reverse("importer:submit_decision", args=[session.id]),
            {
                "form_token": page.context["tokens"]["decision"],
                "action": "submit_selections",
                "selected__0": "future",
            },
        )
        self.assertEqual(submitted.status_code, 302)
        self.assertFalse(
            session.api_mutations.filter(mutation_kind="submit_decision").exists()
        )

    def test_effect_interface_renders_full_intent_and_freezes_command_fields(self):
        intent = {
            "intent_id": "intent-1",
            "track": "delivery",
            "gate_phase_id": "gate",
            "effect_phase_id": "deliver",
            "maximum_mode": "execute",
            "supported_modes": ["disabled", "preview", "execute"],
            "target_provider_id": "fake-preview-v1",
            "target_fingerprint": "target-fingerprint",
            "work_digest": "work-digest",
            "confirmation": "Confirm delivery of 3 artifacts",
        }
        value = projection(
            run_id="run-effect",
            status="awaiting_effect_authorization",
            stage="delivery",
            effect_intent=intent,
            summary={"artifact_count": 3},
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        for expected in (
            "Ready for your approval",
            "Generate output files",
            "Create downloadable preview files for this run.",
            "Preview only",
            "Generate files",
            "Technical details",
        ):
            self.assertContains(page, expected)
        self.assertIn(
            b'<details class="card run-details technical-details">',
            page.content,
        )
        self.assertNotIn(
            b'<details class="card run-details technical-details" open',
            page.content,
        )
        for expected in (
            "Intent ID",
            "intent-1",
            "Gate phase ID",
            "gate",
            "Effect phase ID",
            "deliver",
            "target-fingerprint",
            "work-digest",
            "Confirm delivery of 3 artifacts",
            "artifact_count",
        ):
            self.assertContains(page, expected)
        with patch.object(
            EasyImportsApiClient, "dispatch", side_effect=self.reject_dispatch
        ):
            self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {
                    "form_token": page.context["tokens"]["effect"],
                    "selected_mode": "preview",
                    "target_fingerprint": "browser-tampering-is-ignored",
                },
            )
        command = session.api_mutations.get(
            mutation_kind="authorize_effect"
        ).request_json
        self.assertEqual(command["target_fingerprint"], "target-fingerprint")
        self.assertEqual(command["work_digest"], "work-digest")
        self.assertEqual(command["selected_mode"], "preview")

    def test_developer_target_html_contains_only_opaque_identity(self):
        opaque_target = "step14-developer-target-v1"
        intent = {
            "intent_id": "intent-step14",
            "track": "account_provisioning",
            "gate_phase_id": "gate_account_list_provisioning",
            "effect_phase_id": "execute_account_list_provisioning",
            "maximum_mode": "dry_run",
            "supported_modes": ["disabled", "preview", "dry_run"],
            "target_provider_id": "developer-dry-run-v1",
            "target_fingerprint": opaque_target,
            "work_digest": "step14-work-digest",
            "confirmation": "Confirm Account dry run",
        }
        value = projection(
            run_id="run-step14-html",
            workflow_key="easyimports.account_list_import",
            workflow_version=4,
            target_provider_id="developer-dry-run-v1",
            status="awaiting_effect_authorization",
            stage="account_provisioning",
            effect_intent=intent,
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, opaque_target)
        for forbidden in (
            "00D000000000001AAA",
            "00D000000000001",
            "synthetic.my.salesforce.com",
            "synthetic-access-token",
            "step14-synthetic-client-secret",
            "step14-synthetic-refresh-token",
        ):
            self.assertNotContains(page, forbidden)

    def test_stale_product_submission_cannot_overwrite_frozen_operator(self):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="Alice Operator",
        )
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="select_product",
        )
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "preview",
                    },
                }
            ]
        }

        def freeze_after_stale_form_load(*_args, **_kwargs):
            freeze_operator_label(ImportSession.objects.get(pk=session.pk))
            return {}

        with (
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
            patch(
                "importer.workflow_views.validate_form_token",
                side_effect=freeze_after_stale_form_load,
            ),
        ):
            submitted = self.client.post(
                reverse("importer:product", args=[session.id]),
                {
                    "form_token": token,
                    "operator_label": "Changed Operator",
                    "entity": "accounts",
                    "operation": "clean_only",
                    "reference_source": "",
                    "people_output": "",
                    "target_provider_id": "fake-preview-v1",
                    "setup_revision": 0,
                },
            )

        self.assertEqual(submitted.status_code, 200)
        self.assertContains(submitted, "operator label was frozen")
        session.refresh_from_db()
        self.assertEqual(session.operator_label, "Alice Operator")
        self.assertIsNotNone(session.operator_label_frozen_at)
        self.assertEqual(session.product_key, "")
        self.assertEqual(session.setup_entity, "")
        self.assertEqual(session.target_provider_id, "")

    def test_intent_wizard_freezes_setup_draft_not_product_key(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="select_product",
        )
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {},
                }
            ]
        }
        with (
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            submitted = self.client.post(
                reverse("importer:product", args=[session.id]),
                {
                    "form_token": token,
                    "operator_label": "Alice Operator",
                    "entity": "accounts",
                    "operation": "clean_only",
                    "reference_source": "",
                    "people_output": "",
                    "target_provider_id": "fake-preview-v1",
                    "setup_revision": 0,
                },
            )
        self.assertEqual(submitted.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.setup_entity, "accounts")
        self.assertEqual(session.setup_operation, "clean_only")
        self.assertEqual(session.setup_reference_source, "none")
        self.assertEqual(session.setup_revision, 1)
        self.assertEqual(session.product_key, "")
        self.assertEqual(session.target_provider_id, "fake-preview-v1")

    def test_matching_without_references_blocks_configure(self):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="accounts",
            setup_operation="crm_matching",
            setup_reference_source="uploaded",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-raw-only",
        )
        SourceFile.objects.create(
            session=session,
            role="raw_list",
            original_name="new_accounts.csv",
            stored_path="new_accounts.csv",
            api_upload_id="upload-raw-only",
            upload_mutation=mutation,
        )
        products = {
            "products": [
                {
                    "product_key": "easyimports.account_list_import",
                    "tracks": {
                        "reference_acquisition": ["disabled", "execute"],
                        "account_provisioning": ["disabled", "preview"],
                        "account_writes": ["disabled"],
                        "delivery": ["disabled", "preview"],
                    },
                }
            ]
        }
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {
                        "reference_acquisition": "execute",
                        "account_provisioning": "preview",
                        "account_writes": "disabled",
                        "delivery": "preview",
                    },
                }
            ]
        }
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:configure", args=[session.id]))
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.url, reverse("importer:upload", args=[session.id]))

    def test_stale_setup_revision_rejected_on_product_post(self):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_revision=3,
            operator_label="Alice Operator",
        )
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="select_product",
        )
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {},
                }
            ]
        }
        with (
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            submitted = self.client.post(
                reverse("importer:product", args=[session.id]),
                {
                    "form_token": token,
                    "operator_label": "Alice Operator",
                    "entity": "people",
                    "operation": "clean_only",
                    "people_output": "contact",
                    "target_provider_id": "fake-preview-v1",
                    "setup_revision": 1,
                },
            )
        self.assertEqual(submitted.status_code, 200)
        self.assertContains(submitted, "stale")
        session.refresh_from_db()
        self.assertEqual(session.setup_entity, "")
        self.assertEqual(session.setup_revision, 3)

    def test_incompatible_upload_blocks_ready_and_can_be_detached(self):
        from importer.setup_service import (
            detach_active_upload,
            next_upload_generation,
        )
        from importer.setup_router import DETACHED_ROLE_PREFIX
        from importer.setup_service import SetupValidationError

        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="people",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_people_output="contact",
            setup_revision=2,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        dataset_mut = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-ds-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-dataset",
            logical_action_identity=f"upload:{session.id}:dataset",
            logical_action_generation=0,
        )
        SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="people.csv",
            stored_path="people.csv",
            api_upload_id="upload-dataset",
            upload_mutation=dataset_mut,
            slot_generation=0,
        )
        contacts_mut = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-ct-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest-2",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-contacts",
            logical_action_identity=f"upload:{session.id}:contacts",
            logical_action_generation=0,
        )
        contacts = SourceFile.objects.create(
            session=session,
            role="contacts",
            original_name="contacts.csv",
            stored_path="contacts.csv",
            api_upload_id="upload-contacts",
            upload_mutation=contacts_mut,
            slot_generation=0,
        )
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        self.assertContains(page, "Not used for this setup")
        self.assertContains(page, "Remove from this setup")
        # Ready must stay false while prohibited completed role remains active.
        self.assertFalse(page.context["required_ready"])

        stored_path = contacts.stored_path
        detached = detach_active_upload(contacts, expected_setup_revision=2)
        self.assertTrue(detached.role.startswith(DETACHED_ROLE_PREFIX))
        self.assertEqual(detached.original_role, "contacts")
        self.assertIsNotNone(detached.detached_at)
        self.assertEqual(detached.stored_path, stored_path)
        self.assertEqual(detached.api_upload_id, "upload-contacts")
        self.assertEqual(next_upload_generation(session, "contacts"), 1)
        page2 = self.client.get(reverse("importer:upload", args=[session.id]))
        self.assertTrue(page2.context["required_ready"])

    def test_pending_upload_cannot_be_detached(self):
        from importer.setup_service import detach_active_upload, SetupValidationError

        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        pending = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-pending-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.PENDING,
            logical_action_identity=f"upload:{session.id}:dataset",
            logical_action_generation=0,
        )
        source = SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="accounts.csv",
            stored_path="accounts.csv",
            upload_mutation=pending,
            slot_generation=0,
        )
        with self.assertRaises(SetupValidationError):
            detach_active_upload(source, expected_setup_revision=1)

    def test_detach_then_reuse_same_role_uses_next_generation(self):
        from importer.setup_service import detach_active_upload, next_upload_generation
        from importer.upload_service import save_and_register_upload
        from django.core.files.uploadedfile import SimpleUploadedFile

        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="people",
            setup_operation="crm_matching",
            setup_reference_source="uploaded",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        first = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-first-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-contacts-1",
            logical_action_identity=f"upload:{session.id}:contacts",
            logical_action_generation=0,
        )
        source = SourceFile.objects.create(
            session=session,
            role="contacts",
            original_name="contacts-a.csv",
            stored_path="contacts-a.csv",
            api_upload_id="upload-contacts-1",
            upload_mutation=first,
            slot_generation=0,
        )
        # Switch to clean-only so contacts becomes prohibited, then detach.
        session.setup_operation = "clean_only"
        session.setup_reference_source = "none"
        session.setup_people_output = "contact"
        session.setup_revision = 2
        session.save()
        detach_active_upload(source, expected_setup_revision=2)
        # Switch back to matching so contacts is allowed again.
        session.setup_operation = "crm_matching"
        session.setup_reference_source = "uploaded"
        session.setup_people_output = ""
        session.setup_revision = 3
        session.save()
        self.assertEqual(next_upload_generation(session, "contacts"), 1)

        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=lambda mutation, **kwargs: type(
                "R",
                (),
                {
                    "mutation": mutation,
                    "response": {
                        "upload_id": "upload-contacts-2",
                        "row_count": 1,
                        "columns": ["Id"],
                    },
                },
            )(
                # mutate completed
            ),
        ):
            def fake_dispatch(mutation, **kwargs):
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = {
                    "upload_id": "upload-contacts-2",
                    "row_count": 1,
                    "columns": ["Id"],
                }
                mutation.result_upload_id = "upload-contacts-2"
                mutation.save()
                return type(
                    "R",
                    (),
                    {"mutation": mutation, "response": mutation.response_json},
                )()

            with patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
                registration = save_and_register_upload(
                    session=session,
                    role="contacts",
                    uploaded=SimpleUploadedFile(
                        "contacts-b.csv",
                        b"Id\n1\n",
                        content_type="text/csv",
                    ),
                    form_instance=uuid4(),
                    logical_action_generation=1,
                    csv_encoding="utf-8-sig",
                    xlsx_sheet_index=0,
                )
        self.assertEqual(registration.mutation.logical_action_generation, 1)
        self.assertEqual(registration.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(registration.source.role, "contacts")
        self.assertEqual(registration.source.api_upload_id, "upload-contacts-2")
        self.assertIsNone(registration.source.detached_at)

    def test_stale_detach_revision_is_rejected(self):
        from importer.setup_service import detach_active_upload
        from importer.workflow_state import FormTokenError

        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="people",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_people_output="contact",
            setup_revision=5,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-stale-detach-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-contacts",
            logical_action_identity=f"upload:{session.id}:contacts",
            logical_action_generation=0,
        )
        source = SourceFile.objects.create(
            session=session,
            role="contacts",
            original_name="contacts.csv",
            stored_path="contacts.csv",
            api_upload_id="upload-contacts",
            upload_mutation=mutation,
            slot_generation=0,
        )
        with self.assertRaises(FormTokenError):
            detach_active_upload(source, expected_setup_revision=1)

    def test_materialize_freezes_product_key_from_api_projection(self):
        from importer.command_service import materialize_accepted_workflow
        from importer.api_client import MutationDispatchResult

        session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="Alice Operator",
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            product_key="",
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"create-{uuid4()}",
            mutation_kind="create_workflow",
            route="/v1/workflows",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-recover-product",
                "resource": "/v1/workflows/run-recover-product",
            },
            result_run_id="run-recover-product",
        )
        value = projection(
            run_id="run-recover-product",
            workflow_key="easyimports.single_dataset_import",
            status="running",
            stage="source",
        )
        with patch.object(EasyImportsApiClient, "workflow", return_value=value):
            workflow = materialize_accepted_workflow(
                session,
                MutationDispatchResult(mutation, mutation.response_json),
            )
        self.assertIsNotNone(workflow)
        session.refresh_from_db()
        self.assertEqual(session.product_key, "easyimports.single_dataset_import")
        self.assertEqual(session.active_workflow_id, workflow.id)

    def test_conflicting_frozen_product_key_fails_closed(self):
        from importer.setup_service import freeze_product_key, SetupValidationError

        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.list_import",
            target_provider_id="fake-preview-v1",
        )
        with self.assertRaises(SetupValidationError):
            freeze_product_key(session, "easyimports.single_dataset_import")
        session.refresh_from_db()
        self.assertEqual(session.product_key, "easyimports.list_import")

    def test_product_key_only_session_can_post_configuration(self):
        """Pre-migration sessions with product_key but no setup draft still create."""

        from importer.api_client import MutationDispatchResult

        digests = {
            "plan_content_digest": "a" * 64,
            "source_schema_digest": "b" * 64,
            "destination_digest": "c" * 64,
            "target_contract_digest": "d" * 64,
        }
        # Pre-migration product_key-only sessions still require a current MAP bind.
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.single_dataset_import",
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
            setup_revision=0,
            options={
                "column_mapping_map2": {
                    "plan_id": "cmp_legacy_product",
                    "status": "confirmed",
                    "confirmed_digests": digests,
                    "upload_id": "upload-dataset",
                    "destination": {
                        "destination_mode": "catalog",
                        "catalog_id": "easyimports.account_fields.v1",
                    },
                    "source_headers": ["name"],
                }
            },
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-legacy-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-dataset",
            logical_action_identity=f"upload:{session.id}:dataset",
            logical_action_generation=0,
        )
        SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="dataset.csv",
            stored_path="dataset.csv",
            api_upload_id="upload-dataset",
            columns=["name"],
            upload_mutation=mutation,
            slot_generation=0,
        )
        products = {
            "products": [
                {
                    "product_key": "easyimports.single_dataset_import",
                    "tracks": {
                        "dataset_writes": ["disabled"],
                        "delivery": ["disabled", "preview"],
                    },
                }
            ]
        }
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "preview",
                    },
                }
            ]
        }
        created = {
            "outcome": "accepted",
            "run_id": "run-legacy-product",
            "resource": "/v1/workflows/run-legacy-product",
            "revision": 1,
        }
        value = projection(
            run_id="run-legacy-product",
            workflow_key="easyimports.single_dataset_import",
            status="running",
            stage="source",
        )
        identity = f"session:{session.id}:create_workflow"
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="create_workflow",
            logical_action_identity=identity,
            logical_action_generation=0,
        )

        def fake_dispatch(mutation_obj, **kwargs):
            mutation_obj.state = ApiMutation.State.COMPLETED
            mutation_obj.response_json = created
            mutation_obj.result_run_id = "run-legacy-product"
            mutation_obj.save()
            return MutationDispatchResult(mutation_obj, created)

        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch),
            patch.object(EasyImportsApiClient, "workflow", return_value=value),
        ):
            submitted = self.client.post(
                reverse("importer:configure", args=[session.id]),
                {
                    "form_token": token,
                    "setup_revision": 0,
                    "mode__dataset_writes": "disabled",
                    "mode__delivery": "preview",
                    "target_object": "account",
                    "content_type": "accounts",
                    "canon_profile": "accounts",
                    "list_duplicate_policy": "surface",
                },
            )
        self.assertEqual(submitted.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.product_key, "easyimports.single_dataset_import")
        self.assertIsNotNone(session.active_workflow_id)

    def test_connected_crm_reference_source_accepted_for_import_setup(self):
        from importer.setup_router import derive_route, parse_operator_intent

        intent = parse_operator_intent(
            entity="accounts",
            operation="crm_matching",
            reference_source="connected_crm",
        )
        self.assertEqual(intent.reference_source, "connected_crm")
        route = derive_route(intent)
        self.assertEqual(route.reference_acquisition_requirement, "execute")
        self.assertEqual(route.required_upload_roles, frozenset({"raw_list"}))

    def test_detach_and_preclaim_use_session_then_source_lock_order(self):
        """Regression: opposite lock order between detach and create deadlocks."""

        from importer.setup_service import (
            detach_active_upload,
            lock_session_sources,
            preclaim_create_workflow,
        )

        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        mut = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"lock-order-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-1",
            logical_action_identity=f"upload:{session.id}:dataset",
            logical_action_generation=0,
        )
        source = SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="a.csv",
            stored_path="a.csv",
            api_upload_id="upload-1",
            upload_mutation=mut,
            slot_generation=0,
        )
        order: list[str] = []
        real_session_sfu = ImportSession.objects.select_for_update
        real_source_sfu = SourceFile.objects.select_for_update

        def session_sfu(*args, **kwargs):
            order.append("session")
            return real_session_sfu(*args, **kwargs)

        def source_sfu(*args, **kwargs):
            order.append("source")
            return real_source_sfu(*args, **kwargs)

        with (
            patch.object(ImportSession.objects, "select_for_update", side_effect=session_sfu),
            patch.object(SourceFile.objects, "select_for_update", side_effect=source_sfu),
        ):
            detach_active_upload(source, expected_setup_revision=1)
        self.assertEqual(order[0], "session")
        self.assertIn("source", order)
        self.assertLess(order.index("session"), order.index("source"))

        # Recreate an active source for preclaim path lock ordering.
        session.setup_revision = 2
        session.save(update_fields=["setup_revision"])
        mut2 = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"lock-order-2-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="digest2",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-2",
            logical_action_identity=f"upload:{session.id}:dataset",
            logical_action_generation=1,
            replacement_of=mut,
        )
        SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="b.csv",
            stored_path="b.csv",
            api_upload_id="upload-2",
            upload_mutation=mut2,
            slot_generation=1,
        )
        products = [
            {
                "product_key": "easyimports.single_dataset_import",
                "tracks": {
                    "dataset_writes": ["disabled"],
                    "delivery": ["disabled", "preview"],
                },
            }
        ]
        targets = [
            {
                "target_provider_id": "fake-preview-v1",
                "maximum_modes": {
                    "dataset_writes": "disabled",
                    "delivery": "preview",
                },
            }
        ]
        form = WorkflowConfigurationForm(
            {
                "form_token": "x",
                "setup_revision": 2,
                "mode__dataset_writes": "disabled",
                "mode__delivery": "preview",
                "target_object": "account",
                "content_type": "accounts",
                "canon_profile": "accounts",
                "list_duplicate_policy": "surface",
            },
            product_entry=products[0],
            target=targets[0],
            uploaded_roles={"dataset"},
            route_defaults={
                "target_object": "account",
                "content_type": "accounts",
                "canon_profile": "accounts",
                "person_kind": None,
            },
            reference_acquisition_requirement="prohibited",
        )
        self.assertTrue(form.is_valid(), form.errors)
        order.clear()
        with (
            patch.object(ImportSession.objects, "select_for_update", side_effect=session_sfu),
            patch.object(SourceFile.objects, "select_for_update", side_effect=source_sfu),
        ):
            preclaim_create_workflow(
                session,
                expected_revision=2,
                products=products,
                targets=targets,
                connections=[],
                form=form,
                form_instance=uuid4(),
                form_payload_digest="digest",
                logical_action_identity=f"session:{session.id}:create_workflow",
                logical_action_generation=0,
            )
        self.assertEqual(order[0], "session")
        self.assertIn("source", order)
        self.assertLess(order.index("session"), order.index("source"))
        # lock_session_sources is the shared helper used by both paths.
        locked = lock_session_sources(session)
        self.assertEqual([item.pk for item in locked], sorted(item.pk for item in locked))

    def test_reuse_after_detach_binds_replacement_of(self):
        from importer.setup_service import detach_active_upload
        from importer.upload_service import save_and_register_upload
        from django.core.files.uploadedfile import SimpleUploadedFile

        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="people",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_people_output="contact",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        first = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"repl-of-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-old",
            logical_action_identity=f"upload:{session.id}:contacts",
            logical_action_generation=0,
        )
        source = SourceFile.objects.create(
            session=session,
            role="contacts",
            original_name="old.csv",
            stored_path="old.csv",
            api_upload_id="upload-old",
            upload_mutation=first,
            slot_generation=0,
        )
        detach_active_upload(source, expected_setup_revision=1)

        def fake_dispatch(mutation, **kwargs):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "upload_id": "upload-new",
                "row_count": 1,
                "columns": ["Id"],
            }
            mutation.result_upload_id = "upload-new"
            mutation.save()
            return type(
                "R",
                (),
                {"mutation": mutation, "response": mutation.response_json},
            )()

        with patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            registration = save_and_register_upload(
                session=session,
                role="contacts",
                uploaded=SimpleUploadedFile(
                    "new.csv", b"Id\n1\n", content_type="text/csv"
                ),
                form_instance=uuid4(),
                logical_action_generation=1,
                csv_encoding="utf-8-sig",
                xlsx_sheet_index=0,
            )
        self.assertEqual(registration.mutation.replacement_of_id, first.id)

    def test_malformed_detach_revision_does_not_500(self):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            setup_entity="people",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_people_output="contact",
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        mut = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"bad-rev-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id="upload-c",
            logical_action_identity=f"upload:{session.id}:contacts",
            logical_action_generation=0,
        )
        source = SourceFile.objects.create(
            session=session,
            role="contacts",
            original_name="c.csv",
            stored_path="c.csv",
            api_upload_id="upload-c",
            upload_mutation=mut,
            slot_generation=0,
        )
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="detach_upload",
            action_id=f"{source.id}:1",
            logical_action_identity=f"upload:{session.id}:contacts",
            logical_action_generation=0,
        )
        response = self.client.post(
            reverse("importer:detach_upload", args=[session.id, source.id]),
            {"form_token": token, "setup_revision": "not-a-number"},
        )
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertIsNone(source.detached_at)

    def test_paused_effect_variants_render_explicit_resume(self):
        for index, status in enumerate(("paused_unknown", "paused_verification")):
            with self.subTest(status=status):
                value = projection(
                    run_id=f"run-paused-{index}", status=status, stage="effect"
                )
                session, workflow = self.make_session_workflow(value)
                with patch(
                    "importer.workflow_views.refresh_workflow",
                    return_value=workflow,
                ):
                    page = self.client.get(
                        reverse("importer:workflow", args=[session.id])
                    )
                self.assertContains(page, "Verify and continue")
                self.assertIn("resume", page.context["tokens"])

    def test_unknown_status_and_decision_remain_visible_and_read_only(self):
        value = projection(
            run_id="run-future",
            status="future_status",
            stage="future",
            decision={
                "decision_id": "future-1",
                "decision_type": "future_decision",
                "phase_id": "future",
                "body": {"title": "Future decision", "opaque": "visible"},
            },
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertContains(page, "future_status")
        self.assertContains(page, "Future decision")
        self.assertContains(page, "read-only", count=2)
        self.assertNotContains(page, "Submit decision")

    def test_rejected_receipt_is_persistent_blocks_action_and_requires_ack(self):
        decision = self.interactive_decision("list_duplicates")
        value = projection(
            run_id="run-rejected-ui",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        identity = (
            f"run:{workflow.run_id}:revision:{workflow.revision}:decision:"
            f"{decision['decision_id']}:group:"
        )
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="submit_decision",
            route=f"/v1/workflows/{workflow.run_id}/decisions",
            logical_action_identity=identity,
            form_payload_digest="frozen",
            request_json={"frozen": True},
        )
        mutation.state = ApiMutation.State.REJECTED
        mutation.response_json = receipt(outcome="rejected")
        mutation.error_code = "revision_conflict"
        mutation.error_message = "Reload the projection"
        mutation.save()
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertContains(page, "Action could not be completed")
        self.assertContains(page, "revision_conflict")
        self.assertNotContains(page, "Submit decision")
        token = page.context["rejection_tokens"][str(mutation.id)]
        response = self.client.post(
            reverse("importer:acknowledge_rejection", args=[session.id, mutation.id]),
            {"form_token": token},
        )
        self.assertEqual(response.status_code, 302)
        mutation.refresh_from_db()
        self.assertIsNotNone(mutation.acknowledged_at)
        self.assertEqual(action_generation(session, identity), 1)

    def test_idempotency_conflict_cannot_be_acknowledged_automatically(self):
        value = projection(run_id="run-conflict")
        session, workflow = self.make_session_workflow(value)
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route=f"/v1/workflows/{workflow.run_id}/effect-resumptions",
            logical_action_identity="conflicted-action",
            request_json={"expected_revision": 1},
        )
        mutation.state = ApiMutation.State.REJECTED
        mutation.error_code = "idempotency_conflict"
        mutation.error_message = "Key collision"
        mutation.response_json = {
            "error": {"code": "idempotency_conflict", "message": "Key collision"}
        }
        mutation.save()
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="acknowledge_rejection",
            action_id=str(mutation.id),
            logical_action_identity=mutation.logical_action_identity,
            logical_action_generation=0,
        )
        self.client.post(
            reverse("importer:acknowledge_rejection", args=[session.id, mutation.id]),
            {"form_token": token},
        )
        mutation.refresh_from_db()
        self.assertIsNone(mutation.acknowledged_at)
        self.assertEqual(action_generation(session, "conflicted-action"), 0)

    def test_recorded_form_replay_wins_before_projection_staleness(self):
        decision = self.interactive_decision("list_duplicates")
        value = projection(
            run_id="run-replay",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["decision"]
        claims = decode_form_token(
            token,
            owner_id=self.owner,
            session=session,
            action_kind="decision",
        )
        post_digest = canonical_digest(
            {"action": ["submit_selections"], "selected": ["option-1"]}
        )
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="submit_decision",
            route=f"/v1/workflows/{workflow.run_id}/decisions",
            logical_action_identity=claims["logical_action_identity"],
            logical_action_generation=claims["logical_action_generation"],
            form_payload_digest=post_digest,
            request_json={"already": "submitted"},
        )
        frozen = receipt()
        mutation.state = ApiMutation.State.COMPLETED
        mutation.response_json = frozen
        mutation.response_digest = canonical_digest(frozen)
        mutation.save()
        workflow.revision = 2
        workflow.projection_digest = "newer-projection"
        workflow.save(update_fields=["revision", "projection_digest"])
        with patch(
            "importer.workflow_views.materialize_stored_receipt",
            return_value=workflow,
        ):
            response_value = self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": token,
                    "action": "submit_selections",
                    "selected": "option-1",
                },
            )
        self.assertEqual(response_value.status_code, 302)
        self.assertEqual(session.api_mutations.count(), 1)
        mutation.refresh_from_db()
        self.assertEqual(mutation.response_json, frozen)

    def test_unseen_stale_token_fails_without_creating_mutation(self):
        decision = self.interactive_decision("list_duplicates")
        value = projection(
            run_id="run-unseen-stale",
            status="needs_decision",
            stage="review",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        workflow.revision = 2
        workflow.projection_digest = "changed"
        workflow.save(update_fields=["revision", "projection_digest"])
        self.client.post(
            reverse("importer:submit_decision", args=[session.id]),
            {
                "form_token": page.context["tokens"]["decision"],
                "action": "submit_selections",
                "selected": "option-1",
            },
        )
        self.assertFalse(session.api_mutations.exists())

    def test_terminal_receipts_accountability_and_manifest_are_rendered(self):
        terminal = {
            "workflow_key": "easyimports.list_import",
            "workflow_version": 1,
            "receipts": [
                {
                    "track": "delivery",
                    "outcome": "executed",
                    "authorization": "preview",
                    "capability": "artifact_delivery",
                    "reason": "preview generated",
                    "evidence": {"artifact_count": 1},
                }
            ],
            "accountability": {
                "original_row_ids": ["row-1"],
                "output_row_ids": ["row-1"],
                "excluded_row_count": 0,
                "failed_row_ids": [],
                "missing_ids": [],
                "double_counted_ids": [],
                "duplicate_output_ids": [],
                "reconciles": True,
            },
            "delivery_manifest": {
                "status": "planned",
                "target_fingerprint": "delivery-target",
                "collision_policy": "error",
                "plan_digest": "plan-digest",
                "logical_manifest_digest": "logical-digest",
                "artifact_count": 1,
                "serializer_contract": "csv-v1",
                "scalar_contract": "scalar-v1",
                "namespace_contract": "namespace-v1",
                "physical_manifest_digest": None,
            },
        }
        value = projection(
            run_id="run-terminal",
            status="succeeded",
            stage="complete",
            terminal_evidence=terminal,
        )
        session, workflow = self.make_session_workflow(value)
        with (
            patch("importer.workflow_views.refresh_workflow", return_value=workflow),
            patch.object(
                EasyImportsApiClient,
                "artifacts",
                return_value={"artifacts": []},
            ),
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        for expected in (
            "Import complete",
            "Your results are ready to review and download.",
            "No live CRM changes were authorized for this run.",
            "Rows processed",
            "Rows ready",
            "Rows excluded",
            "Rows failed",
            "Output files",
            "Completed",
            "Technical details",
        ):
            self.assertContains(page, expected)
        for expected in (
            "preview generated",
            "original_row_ids",
            "row-1",
            "delivery-target",
            "logical-digest",
        ):
            self.assertContains(page, expected)

    def test_decision_set_receipt_result_drives_review_continuation_ui(self):
        source_projection = projection(
            run_id="run-source-review",
            status="awaiting_review",
            stage="review_handoff",
            review_handoff={
                "handoff_id": "review-handoff-1",
                "entity": "account",
                "group_count": 2,
                "binding_digest": "review-binding",
            },
        )
        session, source = self.make_session_workflow(source_projection)
        review_projection = projection(
            run_id="run-review",
            workflow_key="easyimports.duplicate_resolution",
            status="succeeded",
            stage="complete",
        )
        review = ApiWorkflow.objects.create(
            session=session,
            source_workflow=source,
            role=ApiWorkflow.Role.REVIEW,
            run_id="run-review",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=1,
            target_provider_id="fake-preview-v1",
            status="succeeded",
            stage="complete",
            revision=1,
            resource_url="/v1/workflows/run-review",
            projection=review_projection,
            projection_digest=canonical_digest(review_projection),
        )
        session.active_workflow = review
        session.save(update_fields=["active_workflow"])
        handoff_result = {
            "decision_set_handoff": {
                "handoff_id": "decision-set-1",
                "review_handoff_id": "review-handoff-1",
                "entity": "account",
                "decision_count": 2,
                "binding_digest": "decision-binding",
            }
        }
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=review,
            form_instance=uuid4(),
            mutation_kind="create_decision_set_handoff",
            route="/v1/workflows/run-review/decision-set-handoffs",
            logical_action_identity="decision-set-action",
            request_json={"frozen": True},
        )
        frozen_receipt = receipt(result=handoff_result)
        frozen_receipt["run_id"] = review.run_id
        frozen_receipt["resource"] = review.resource_url
        mutation.state = ApiMutation.State.COMPLETED
        mutation.response_json = frozen_receipt
        mutation.response_digest = canonical_digest(frozen_receipt)
        mutation.save()
        with (
            patch("importer.workflow_views.refresh_workflow", return_value=review),
            patch.object(
                EasyImportsApiClient,
                "artifacts",
                return_value={"artifacts": []},
            ),
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertContains(page, "decision-set-1")
        self.assertContains(page, "decision-binding")
        self.assertContains(page, "Continue source import")
        self.assertIn("continue", page.context["tokens"])
        with patch.object(
            EasyImportsApiClient, "dispatch", side_effect=self.reject_dispatch
        ):
            submitted = self.client.post(
                reverse("importer:continue_source", args=[session.id]),
                {"form_token": page.context["tokens"]["continue"]},
            )
        self.assertEqual(submitted.status_code, 302)
        continuation = session.api_mutations.get(
            mutation_kind="bind_decision_set_handoff"
        )
        self.assertEqual(
            continuation.request_json,
            {
                "decision_set_handoff_id": "decision-set-1",
                "review_run_id": "run-review",
            },
        )

    def test_pending_effect_renders_automatic_progress_polling(self):
        value = projection(
            run_id="run-effect-progress",
            status="pending_effect_authorization",
            stage="effect_authorization",
        )
        session, workflow = self.make_session_workflow(value)
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="authorize_effect",
            route=f"/v1/workflows/{workflow.run_id}/effect-authorizations",
            logical_action_identity="effect-progress",
            request_json={"selected_mode": "execute"},
        )
        mutation.http_status = 202
        mutation.response_json = {
            "mutation_id": "safe-progress-id",
            "mutation_kind": "authorize_effect",
            "status": "pending",
            "http_status": None,
            "response": None,
            "retryable": False,
            "retry_after_seconds": 2,
        }
        mutation.save(update_fields=["http_status", "response_json"])

        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        status_url = reverse(
            "importer:mutation_status",
            args=[session.id, mutation.id],
        )
        self.assertContains(page, status_url)
        self.assertContains(page, "This page will update automatically")
        self.assertNotContains(page, "Retry saved action")

    def test_nested_api_error_receipt_renders_without_template_failure(self):
        value = projection(run_id="run-nested-error")
        session, workflow = self.make_session_workflow(value)
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route=f"/v1/workflows/{workflow.run_id}/effect-resumptions",
            logical_action_identity="nested-error",
            request_json={"expected_revision": workflow.revision},
        )
        mutation.state = ApiMutation.State.REJECTED
        mutation.http_status = 409
        mutation.response_json = {
            "error": {
                "code": "mutation_in_progress",
                "message": "The original request is still processing.",
            }
        }
        mutation.save(update_fields=["state", "http_status", "response_json"])

        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "mutation_in_progress")
        self.assertContains(page, "The original request is still processing.")

    def test_mutation_status_poll_freezes_receipt_and_refreshes_projection(self):
        value = projection(run_id="run-1", status="pending_effect_authorization")
        session, workflow = self.make_session_workflow(value)
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-1/effect-authorizations",
            logical_action_identity="poll-completion",
            request_json={"selected_mode": "execute"},
        )
        mutation.http_status = 202
        mutation.save(update_fields=["http_status"])
        completed = {
            "mutation_id": "safe-progress-id",
            "mutation_kind": "authorize_effect",
            "status": "completed",
            "http_status": 200,
            "response": receipt(),
            "retryable": False,
            "retry_after_seconds": 2,
        }
        refreshed = projection(run_id="run-1", revision=2, stage="execute")

        with (
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value=completed,
            ),
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=refreshed,
            ),
        ):
            polled = self.client.get(
                reverse(
                    "importer:mutation_status",
                    args=[session.id, mutation.id],
                )
            )

        self.assertEqual(polled.status_code, 200)
        self.assertEqual(polled.json()["status"], "completed")
        mutation.refresh_from_db()
        workflow.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.response_json["outcome"], "accepted")
        self.assertEqual(workflow.revision, 2)

    def test_finish_for_now_async_poll_refreshes_deferred_artifact(self):
        """CUM-1D: Django partial-finish path 202 → pending card → poll → deferred CSV."""

        decision = self.duplicate_review_decision("account_duplicate_group_review")
        value = projection(
            run_id="run-finish-async",
            status="needs_decision",
            stage="review",
            decision=decision,
            review_progress={
                "entity": "account",
                "decided_group_count": 1,
                "remaining_group_count": 2,
                "finish_for_now_available": True,
                "finished_for_now": False,
                "deferred_group_count": 0,
                "remaining_groups_exported": False,
            },
        )
        session, workflow = self.make_session_workflow(value)
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="submit_decision",
            route=f"/v1/workflows/{workflow.run_id}/decisions",
            logical_action_identity="decision:finish-for-now-async",
            request_json={
                "decision_id": decision["decision_id"],
                "decision_type": "account_duplicate_group_review",
                "expected_revision": workflow.revision,
                "response": {
                    "action": "finish_for_now",
                    "remaining_group_disposition": "export",
                },
            },
        )
        mutation.http_status = 202
        mutation.response_json = {
            "mutation_id": "finish-async-id",
            "mutation_kind": "submit_decision",
            "status": "pending",
            "http_status": None,
            "response": None,
            "retryable": False,
            "retry_after_seconds": 2,
        }
        mutation.save(update_fields=["http_status", "response_json"])

        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))

        status_url = reverse(
            "importer:mutation_status",
            args=[session.id, mutation.id],
        )
        self.assertContains(page, status_url)
        self.assertContains(page, "This page will update automatically")
        self.assertNotContains(page, "Retry saved action")

        finished_receipt = receipt(outcome="accepted")
        finished_receipt["command_kind"] = "submit_decision"
        finished_receipt["run_id"] = "run-finish-async"
        finished_receipt["revision"] = 2
        finished_receipt["workflow_status"] = "succeeded"
        finished_receipt["stage"] = "complete"
        completed = {
            "mutation_id": "finish-async-id",
            "mutation_kind": "submit_decision",
            "status": "completed",
            "http_status": 200,
            "response": finished_receipt,
            "retryable": False,
            "retry_after_seconds": 2,
        }
        refreshed = projection(
            run_id="run-finish-async",
            revision=2,
            status="succeeded",
            stage="complete",
            decision=None,
            review_progress={
                "entity": "account",
                "decided_group_count": 1,
                "remaining_group_count": 0,
                "finish_for_now_available": False,
                "finished_for_now": True,
                "deferred_group_count": 2,
                "remaining_groups_exported": True,
            },
        )
        deferred_artifact = {
            "artifact_id": "deferred_finish_async",
            "filename": "deferred_duplicate_groups.csv",
            "media_type": "text/csv",
            "byte_count": 128,
            "row_count": 2,
            "content_digest": "a" * 64,
            "availability": "delivered",
            "download_url": (
                "/v1/workflows/run-finish-async/artifacts/"
                "deferred_finish_async/content"
            ),
        }

        with (
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value=completed,
            ),
            patch.object(
                EasyImportsApiClient,
                "workflow",
                return_value=refreshed,
            ),
        ):
            polled = self.client.get(status_url)

        self.assertEqual(polled.status_code, 200)
        self.assertEqual(polled.json()["status"], "completed")
        mutation.refresh_from_db()
        workflow.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.response_json["outcome"], "accepted")
        self.assertEqual(workflow.revision, 2)
        self.assertTrue(
            workflow.projection["review_progress"]["finished_for_now"]
        )
        self.assertEqual(
            workflow.projection["review_progress"]["deferred_group_count"], 2
        )

        with (
            patch(
                "importer.workflow_views.refresh_workflow", return_value=workflow
            ),
            patch.object(
                EasyImportsApiClient,
                "artifacts",
                return_value={"artifacts": [deferred_artifact]},
            ),
        ):
            after = self.client.get(reverse("importer:workflow", args=[session.id]))

        self.assertEqual(after.status_code, 200)
        stored = workflow.artifacts.get(artifact_id="deferred_finish_async")
        self.assertEqual(
            stored.metadata["filename"], "deferred_duplicate_groups.csv"
        )
        self.assertContains(after, "deferred_duplicate_groups.csv")

    def test_start_review_freezes_operator_and_copies_stored_handoff(self):
        value = projection(
            run_id="run-start-review",
            status="awaiting_review",
            stage="review_handoff",
            review_handoff={
                "handoff_id": "review-handoff-start",
                "entity": "person",
                "group_count": 4,
                "binding_digest": "binding-start",
            },
        )
        session, workflow = self.make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        with patch.object(
            EasyImportsApiClient, "dispatch", side_effect=self.reject_dispatch
        ):
            self.client.post(
                reverse("importer:start_review", args=[session.id]),
                {"form_token": page.context["tokens"]["review"]},
            )
        mutation = session.api_mutations.get(mutation_kind="start_review_workflow")
        self.assertEqual(
            mutation.request_json,
            {"review_handoff_id": "review-handoff-start"},
        )
        session.refresh_from_db()
        self.assertEqual(session.operator_label, "Alice Operator")
        self.assertIsNotNone(session.operator_label_frozen_at)


class _ArtifactStream:
    def __init__(self, content: bytes, *, etag: str, media_type="text/csv"):
        self.content = content
        self.headers = {"ETag": etag, "Content-Type": media_type}
        self.closed = False

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), max(1, chunk_size // 2)):
            yield self.content[start : start + max(1, chunk_size // 2)]

    def close(self):
        self.closed = True


class ArtifactProxyAndArchiveTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()
        value = projection(run_id="run-artifacts", status="running")
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="Artifact Operator",
            product_key="easyimports.list_import",
        )
        self.workflow = ApiWorkflow.objects.create(
            session=self.session,
            run_id=value["run_id"],
            workflow_key=value["workflow_key"],
            workflow_version=1,
            target_provider_id="fake-preview-v1",
            status=value["status"],
            stage=value["stage"],
            revision=1,
            resource_url="/v1/workflows/run-artifacts",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        self.session.active_workflow = self.workflow
        self.session.save(update_fields=["active_workflow"])

    def metadata(self, content=b"Name\nExample\n", **changes):
        value = {
            "artifact_id": "artifact-1",
            "kind": "table",
            "name": "records",
            "filename": "records.csv",
            "availability": "preview",
            "content_digest": __import__("hashlib").sha256(content).hexdigest(),
            "byte_count": len(content),
            "row_count": 1,
            "columns": ["Name"],
            "download_url": "/v1/workflows/run-artifacts/artifacts/artifact-1/content",
        }
        value.update(changes)
        return value

    def test_artifact_links_are_suppressed_without_download_url_or_state(self):
        for index, metadata in enumerate(
            (
                self.metadata(download_url=None),
                self.metadata(
                    artifact_id="artifact-2",
                    availability="staged",
                    download_url="/v1/workflows/run-artifacts/artifacts/artifact-2/content",
                ),
            )
        ):
            ApiArtifact.objects.create(
                workflow=self.workflow,
                artifact_id=metadata["artifact_id"],
                metadata=metadata,
            )
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ):
            page = self.client.get(reverse("importer:workflow", args=[self.session.id]))
        self.assertContains(page, "records.csv", count=2)
        self.assertNotContains(
            page,
            reverse(
                "importer:artifact",
                args=[self.session.id, "run-artifacts", "artifact-1"],
            ),
        )
        self.assertNotContains(
            page,
            reverse(
                "importer:artifact",
                args=[self.session.id, "run-artifacts", "artifact-2"],
            ),
        )

    def test_customer_downloads_prioritize_prepared_and_actionable_files(self):
        for artifact_id, filename, row_count in (
            ("prepared", "prepared_source.csv", 1),
            ("excluded", "exclusion_ledger.csv", 1),
            ("failed-empty", "terminal_failure_ledger.csv", 0),
            ("diagnostic", "row_accountability.csv", 10),
        ):
            ApiArtifact.objects.create(
                workflow=self.workflow,
                artifact_id=artifact_id,
                metadata=self.metadata(
                    artifact_id=artifact_id,
                    filename=filename,
                    row_count=row_count,
                    download_url=(
                        f"/v1/workflows/run-artifacts/artifacts/{artifact_id}/content"
                    ),
                ),
            )
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ):
            page = self.client.get(reverse("importer:workflow", args=[self.session.id]))
        customer_view = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Prepared data", customer_view)
        self.assertIn(b"1 row", customer_view)
        self.assertIn(b"Excluded rows", customer_view)
        self.assertNotIn(b"Failed rows", customer_view)
        self.assertNotIn(b"Row processing report", customer_view)
        self.assertContains(page, "terminal_failure_ledger.csv")
        self.assertContains(page, "row_accountability.csv")

    def test_preview_and_delivered_artifacts_stream_with_verified_etag(self):
        for index, availability in enumerate(("preview", "delivered")):
            with self.subTest(availability=availability):
                content = f"Name\nExample {index}\n".encode()
                artifact_id = f"artifact-{index}"
                metadata = self.metadata(
                    content,
                    artifact_id=artifact_id,
                    availability=availability,
                    download_url=(
                        f"/v1/workflows/run-artifacts/artifacts/{artifact_id}/content"
                    ),
                )
                ApiArtifact.objects.create(
                    workflow=self.workflow,
                    artifact_id=artifact_id,
                    metadata=metadata,
                )
                etag = f'"sha256:{metadata["content_digest"]}"'
                upstream = _ArtifactStream(content, etag=etag)
                with patch.object(
                    EasyImportsApiClient,
                    "stream_artifact",
                    return_value=upstream,
                ) as streamed:
                    response_value = self.client.get(
                        reverse(
                            "importer:artifact",
                            args=[self.session.id, "run-artifacts", artifact_id],
                        )
                    )
                    downloaded = b"".join(response_value.streaming_content)
                self.assertEqual(response_value.status_code, 200)
                self.assertEqual(downloaded, content)
                self.assertEqual(response_value["ETag"], etag)
                streamed.assert_called_once_with(
                    metadata["download_url"],
                    run_id="run-artifacts",
                    artifact_id=artifact_id,
                )
                self.assertTrue(upstream.closed)

    def test_proxy_fails_closed_on_etag_or_content_mismatch(self):
        content = b"Name\nExample\n"
        for index, upstream in enumerate(
            (
                _ArtifactStream(content, etag='"sha256:wrong"'),
                _ArtifactStream(
                    b"tampered",
                    etag=f'"sha256:{self.metadata(content)["content_digest"]}"',
                ),
            )
        ):
            artifact_id = f"bad-{index}"
            metadata = self.metadata(
                content,
                artifact_id=artifact_id,
                download_url=(
                    f"/v1/workflows/run-artifacts/artifacts/{artifact_id}/content"
                ),
            )
            ApiArtifact.objects.create(
                workflow=self.workflow,
                artifact_id=artifact_id,
                metadata=metadata,
            )
            with patch.object(
                EasyImportsApiClient, "stream_artifact", return_value=upstream
            ):
                response_value = self.client.get(
                    reverse(
                        "importer:artifact",
                        args=[self.session.id, "run-artifacts", artifact_id],
                    )
                )
            self.assertEqual(response_value.status_code, 404)
            self.assertTrue(upstream.closed)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_client_requires_exact_artifact_content_path_and_rejects_redirects(self):
        http = Mock()
        http.get.return_value = response(200, {"status": "ok", "api_version": "1.14.1"})
        api = EasyImportsApiClient(http=http)
        for invalid in (
            "https://evil.example/v1/workflows/run/artifacts/artifact/content",
            "/v1/workflows/other/artifacts/artifact/content",
            "/v1/workflows/run/artifacts/other/content",
            "/v1/workflows/run/artifacts/artifact/content?token=browser",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ApiRejectedError):
                api.stream_artifact(invalid, run_id="run", artifact_id="artifact")

        redirect = response(302, b"")
        http.get.side_effect = [
            response(200, {"status": "ok", "api_version": "1.14.1"}),
            redirect,
        ]
        with self.assertRaises(ApiRejectedError):
            api.stream_artifact(
                "/v1/workflows/run/artifacts/artifact/content",
                run_id="run",
                artifact_id="artifact",
            )

    def test_archived_session_is_unavailable_to_every_normal_route(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            workflow=self.workflow,
            form_instance=uuid4(),
            mutation_kind="resume_effect",
            route="/v1/workflows/run-artifacts/effect-resumptions",
            logical_action_identity="archive-test",
            request_json={"expected_revision": 1},
        )
        source = SourceFile.objects.create(
            session=self.session,
            role="raw_list",
            original_name="rows.csv",
            stored_path=str(self.session.uploads_dir / "rows.csv"),
            upload_mutation=mutation,
        )
        artifact = ApiArtifact.objects.create(
            workflow=self.workflow,
            artifact_id="artifact-archive",
            metadata=self.metadata(artifact_id="artifact-archive"),
        )
        archived = self.client.post(
            reverse("importer:delete_session", args=[self.session.id])
        )
        self.assertEqual(archived.status_code, 302)
        get_routes = (
            reverse("importer:product", args=[self.session.id]),
            reverse("importer:upload", args=[self.session.id]),
            reverse("importer:configure", args=[self.session.id]),
            reverse("importer:workflow", args=[self.session.id]),
            reverse(
                "importer:artifact",
                args=[self.session.id, self.workflow.run_id, artifact.artifact_id],
            ),
        )
        for route in get_routes:
            self.assertEqual(self.client.get(route).status_code, 404, route)
        post_routes = (
            reverse("importer:retry_mutation", args=[self.session.id, mutation.id]),
            reverse(
                "importer:acknowledge_rejection",
                args=[self.session.id, mutation.id],
            ),
            reverse("importer:replace_upload", args=[self.session.id, source.id]),
            reverse("importer:submit_decision", args=[self.session.id]),
            reverse("importer:authorize_effect", args=[self.session.id]),
            reverse("importer:resume_effect", args=[self.session.id]),
            reverse("importer:start_review", args=[self.session.id]),
            reverse("importer:create_decision_set", args=[self.session.id]),
            reverse("importer:continue_source", args=[self.session.id]),
        )
        for route in post_routes:
            self.assertEqual(self.client.post(route).status_code, 404, route)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ImportSession.Status.ARCHIVED)


class CompleteContractTests(TestCase):
    def test_all_four_complete_workflow_request_shapes_validate(self):
        values = (
            {
                "product_key": "easyimports.list_import",
                "target_provider_id": "fake-preview-v1",
                "uploads": {"raw_list": "upload-raw", "contacts": "upload-contacts"},
                "maximum_modes": {
                    "reference_acquisition": "disabled",
                    "account_provisioning": "disabled",
                    "person_duplicate_resolution": "disabled",
                    "people_writes": "disabled",
                    "campaign_member_writes": "disabled",
                    "delivery": "preview",
                },
                "options": {
                    "interaction_mode": "interactive",
                    "batch_validation_policy": "quarantine",
                    "contacts_only": False,
                    "list_duplicate_policy": "surface",
                    "crm_match_duplicate_policy": "drop_repeats",
                    "crm_account_multi_match_policy": "review",
                    "fill_missing_emails": False,
                    "blank_non_north_america_address": False,
                    "strict_validation": False,
                },
                "duplicate_analysis_as_of_date": None,
                "column_mapping": {
                    "plan_id": "cmp_schema_only_list",
                    "plan_content_digest": "e" * 64,
                    "source_schema_digest": "e" * 64,
                    "destination_digest": "e" * 64,
                    "target_contract_digest": "e" * 64,
                },
            },
            {
                "product_key": "easyimports.account_list_import",
                "target_provider_id": "fake-preview-v1",
                "uploads": {"raw_list": "upload-raw", "accounts": "upload-accounts"},
                "maximum_modes": {
                    "reference_acquisition": "disabled",
                    "account_provisioning": "disabled",
                    "account_writes": "disabled",
                    "delivery": "preview",
                },
                "options": {
                    "interaction_mode": "interactive",
                    "list_duplicate_policy": "surface",
                    "crm_account_multi_match_policy": "review",
                },
                "column_mapping": {
                    "plan_id": "cmp_schema_only_acct",
                    "plan_content_digest": "e" * 64,
                    "source_schema_digest": "e" * 64,
                    "destination_digest": "e" * 64,
                    "target_contract_digest": "e" * 64,
                },
            },
            {
                "product_key": "easyimports.single_dataset_import",
                "target_provider_id": "fake-preview-v1",
                "uploads": {"dataset": "upload-dataset"},
                "maximum_modes": {"dataset_writes": "disabled", "delivery": "preview"},
                "target_object": "contact",
                "content_type": "people",
                "canon_profile": "contacts",
                "person_kind": "contact",
                "options": {"list_duplicate_policy": "surface"},
                "column_mapping": {
                    "plan_id": "cmp_schema_only_single",
                    "plan_content_digest": "e" * 64,
                    "source_schema_digest": "e" * 64,
                    "destination_digest": "e" * 64,
                    "target_contract_digest": "e" * 64,
                },
            },
            {
                "product_key": "easyimports.duplicate_resolution",
                "target_provider_id": "fake-preview-v1",
                "uploads": {"canonical_records": "upload-records"},
                "maximum_modes": {
                    "reference_acquisition": "disabled",
                    "duplicate_execution": "disabled",
                    "delivery": "preview",
                },
                "entity": "account",
                "analysis_as_of_date": "2026-07-20",
            },
        )
        for value in values:
            with self.subTest(product=value["product_key"]):
                self.assertEqual(
                    validate_workflow_create(value)["product_key"],
                    value["product_key"],
                )

    def product(self, key, tracks):
        return {"product_key": key, "tracks": tracks}

    def target(self, tracks):
        return {"target_provider_id": "fake", "maximum_modes": tracks}

    def single_dataset_form(self, *, target_object, content_type, profile, kind):
        return WorkflowConfigurationForm(
            {
                "form_token": "x",
                "mode__dataset_writes": "disabled",
                "mode__delivery": "preview",
                "target_object": target_object,
                "content_type": content_type,
                "canon_profile": profile,
                "person_kind": kind,
                "list_duplicate_policy": "surface",
            },
            product_entry=self.product(
                "easyimports.single_dataset_import",
                {
                    "dataset_writes": ["disabled"],
                    "delivery": ["disabled", "preview"],
                },
            ),
            target=self.target({"dataset_writes": "disabled", "delivery": "preview"}),
            uploaded_roles={"dataset"},
        )

    @staticmethod
    def expected_single_dataset_contract(*, target_object, content_type, profile, kind):
        content = content_type or (
            "accounts" if target_object == "account" else "people_and_accounts"
        )
        effective_profile = profile or (
            "accounts" if target_object == "account" else "new_list"
        )
        effective_kind = kind or None
        if content in {"people", "people_and_accounts"}:
            source_valid = effective_profile in {"new_list", "contacts", "leads"}
            effective_kind = effective_kind or "generic"
        else:
            source_valid = effective_profile == "accounts" and effective_kind is None
        output_valid = (
            content in {"accounts", "people_and_accounts"}
            if target_object == "account"
            else content in {"people", "people_and_accounts"}
        )
        return (
            source_valid and output_valid,
            (content, effective_profile, effective_kind),
        )

    def test_single_dataset_metadata_and_output_compatibility_matrix(self):
        for target_object, content_type, profile, kind in cartesian_product(
            ("account", "contact", "lead"),
            ("", "accounts", "people", "people_and_accounts"),
            ("", "new_list", "accounts", "contacts", "leads"),
            ("", "contact", "lead", "mixed", "generic"),
        ):
            with self.subTest(
                target=target_object,
                content=content_type or "default",
                profile=profile or "default",
                kind=kind or "default",
            ):
                expected, effective = self.expected_single_dataset_contract(
                    target_object=target_object,
                    content_type=content_type,
                    profile=profile,
                    kind=kind,
                )
                form = self.single_dataset_form(
                    target_object=target_object,
                    content_type=content_type,
                    profile=profile,
                    kind=kind,
                )
                self.assertEqual(form.is_valid(), expected, form.errors)
                if expected:
                    self.assertEqual(
                        (
                            form.cleaned_data["content_type"],
                            form.cleaned_data["canon_profile"],
                            form.cleaned_data["person_kind"],
                        ),
                        effective,
                    )
                    request = form.workflow_request(
                        upload_ids={"dataset": "upload-dataset"},
                        target_provider_id="fake",
                    )
                    dig = "e" * 64
                    request = {
                        **request,
                        "column_mapping": {
                            "plan_id": "cmp_schema_only_matrix",
                            "plan_content_digest": dig,
                            "source_schema_digest": dig,
                            "destination_digest": dig,
                            "target_contract_digest": dig,
                        },
                    }
                    self.assertEqual(
                        validate_workflow_create(request)["target_object"],
                        target_object,
                    )

    def test_list_acquisition_rejects_all_supplied_reference_roles(self):
        product = self.product(
            "easyimports.list_import",
            {
                "reference_acquisition": ["disabled", "execute"],
                "account_provisioning": ["disabled"],
                "person_duplicate_resolution": ["disabled"],
                "people_writes": ["disabled"],
                "campaign_member_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        )
        target = self.target(
            {
                "reference_acquisition": "execute",
                "account_provisioning": "disabled",
                "person_duplicate_resolution": "disabled",
                "people_writes": "disabled",
                "campaign_member_writes": "disabled",
                "delivery": "preview",
            }
        )
        data = {
            "form_token": "x",
            "mode__reference_acquisition": "execute",
            "mode__account_provisioning": "disabled",
            "mode__person_duplicate_resolution": "disabled",
            "mode__people_writes": "disabled",
            "mode__campaign_member_writes": "disabled",
            "mode__delivery": "preview",
            "list_duplicate_policy": "surface",
            "crm_match_duplicate_policy": "drop_repeats",
            "crm_account_multi_match_policy": "review",
            "batch_validation_policy": "quarantine",
        }
        for role in ("accounts", "contacts", "leads"):
            with self.subTest(role=role):
                form = WorkflowConfigurationForm(
                    data,
                    product_entry=product,
                    target=target,
                    uploaded_roles={"raw_list", role},
                )
                self.assertFalse(form.is_valid())
                self.assertIn("cannot include supplied", str(form.errors))

    def test_account_list_provisioning_requires_acquired_references(self):
        product = self.product(
            "easyimports.account_list_import",
            {
                "reference_acquisition": ["disabled", "execute"],
                "account_provisioning": ["disabled", "dry_run", "execute"],
                "account_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        )
        target = self.target(
            {
                "reference_acquisition": "execute",
                "account_provisioning": "execute",
                "account_writes": "disabled",
                "delivery": "preview",
            }
        )
        form = WorkflowConfigurationForm(
            {
                "form_token": "x",
                "mode__reference_acquisition": "disabled",
                "mode__account_provisioning": "dry_run",
                "mode__account_writes": "disabled",
                "mode__delivery": "preview",
                "list_duplicate_policy": "surface",
                "crm_account_multi_match_policy": "review",
            },
            product_entry=product,
            target=target,
            uploaded_roles={"raw_list", "accounts"},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("requires acquired references", str(form.errors))

    def test_catalog_unknown_mode_fails_with_compatibility_error_not_key_error(self):
        with self.assertRaises(CatalogCompatibilityError):
            WorkflowConfigurationForm(
                product_entry=self.product(
                    "easyimports.single_dataset_import",
                    {
                        "dataset_writes": ["disabled"],
                        "delivery": ["disabled", "future_mode"],
                    },
                ),
                target=self.target(
                    {"dataset_writes": "disabled", "delivery": "future_mode"}
                ),
                uploaded_roles={"dataset"},
            )

    def test_generated_models_forbid_unknown_request_fields(self):
        with self.assertRaises(ApiContractError):
            validate_workflow_create(
                {
                    "product_key": "easyimports.single_dataset_import",
                    "target_provider_id": "fake",
                    "uploads": {"dataset": "upload"},
                    "target_object": "account",
                    "browser_only": True,
                }
            )


class _PricingResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


WOO_SETTINGS = {
    "WOOCOMMERCE_STORE_URL": "https://shop.example.com",
    "WOOCOMMERCE_CONSUMER_KEY": "ck_test",
    "WOOCOMMERCE_CONSUMER_SECRET": "cs_test",
    "WOOCOMMERCE_PRODUCT_ID": "123",
    "WOOCOMMERCE_CHECKOUT_URL": "",
}
TREASURY_PAYLOAD = {
    "data": [
        {
            "record_date": "2026-07-01",
            "tot_pub_debt_out_amt": "40000000000000.25",
        }
    ]
}
TREASURY_PRICE_LABEL = "$40,000,000,000,000.25"


class BillingRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.pricing = patch(
            "importer.debt_pricing.requests.get",
            return_value=_PricingResponse(TREASURY_PAYLOAD),
        )
        self.pricing.start()

    def tearDown(self):
        self.pricing.stop()
        cache.clear()

    @override_settings(**WOO_SETTINGS)
    def test_pricing_page_still_renders_live_price(self):
        page = self.client.get(reverse("importer:pricing"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, TREASURY_PRICE_LABEL)
        self.assertContains(page, "Pay with WooCommerce")

    @override_settings(**WOO_SETTINGS)
    def test_checkout_still_creates_payment_and_redirects(self):
        order = {
            "id": 555,
            "order_key": "wc_order_abc",
            "payment_url": "https://shop.example.com/checkout/order-pay/555/?key=wc_order_abc",
        }
        with patch(
            "importer.payments.requests.post",
            return_value=_PricingResponse(order),
        ):
            response_value = self.client.post(
                reverse("importer:checkout"), {"email": "a@b.com"}
            )
        self.assertEqual(response_value.status_code, 302)
        self.assertEqual(response_value["Location"], order["payment_url"])
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount_label, TREASURY_PRICE_LABEL)

    @override_settings(**WOO_SETTINGS)
    def test_payment_return_still_marks_completed(self):
        payment = Payment.objects.create(wc_order_id=777, status=Payment.Status.PENDING)
        with patch("importer.payments.fetch_order_status", return_value="completed"):
            page = self.client.get(
                reverse("importer:payment_return"), {"order_id": "777"}
            )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Payment complete")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.COMPLETED)


class _LostAfterDispatchSession:
    """Let the API commit a POST, then simulate losing the response in transit."""

    @staticmethod
    def get(url, **kwargs):
        return requests.get(url, **kwargs)

    @staticmethod
    def post(url, **kwargs):
        requests.post(url, **kwargs)
        raise requests.ConnectionError("response lost after dispatch")


class RealProcessFakeTargetTests(TransactionTestCase):
    """Prove Django's consumer against a separate API process, without Salesforce."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        cls.state_dir = tempfile.TemporaryDirectory()
        cls._start_api()

    @classmethod
    def _start_api(cls):
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = cls.state_dir.name
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{cls.port}/health"
        for _ in range(50):
            try:
                if requests.get(url, timeout=0.2).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            cls.process.terminate()
            raise RuntimeError("Fake-target API process did not start.")

    @classmethod
    def _stop_api(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)

    @classmethod
    def _restart_api(cls):
        cls._stop_api()
        cls._start_api()

    @classmethod
    def tearDownClass(cls):
        cls._stop_api()
        cls.state_dir.cleanup()
        super().tearDownClass()

    def test_catalog_and_journaled_upload_cross_real_http_boundary(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=f"http://127.0.0.1:{self.port}"):
            api = EasyImportsApiClient()
            from .api_contract_generated import API_VERSION as DJANGO_API_VERSION

            self.assertEqual(
                api.assert_compatible()["api_version"], DJANGO_API_VERSION
            )
            schema = requests.get(
                f"http://127.0.0.1:{self.port}/openapi.json", timeout=2
            ).json()
            canonical = json.dumps(
                schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            self.assertEqual(
                __import__("hashlib").sha256(canonical).hexdigest(), OPENAPI_SHA256
            )
            generated = subprocess.run(
                [
                    sys.executable,
                    "web/tools/generate_api_contract.py",
                    "--schema-url",
                    f"http://127.0.0.1:{self.port}/openapi.json",
                    "--check",
                ],
                cwd=Path(__file__).resolve().parents[2],
                text=True,
                capture_output=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertEqual(len(api.products()["products"]), 4)
            self.assertEqual(
                api.targets()["targets"][0]["target_provider_id"], "fake-preview-v1"
            )
            session = ImportSession.objects.create(owner_id=uuid4())
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "rows.csv"
                path.write_bytes(b"Name\nExample\n")
                digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                mutation = create_or_reuse_mutation(
                    session=session,
                    form_instance=uuid4(),
                    mutation_kind="register_upload",
                    route="/v1/uploads",
                    logical_action_identity=f"upload:{session.id}:raw_list",
                    multipart_metadata={
                        "filename": "rows.csv",
                        "media_type": "text/csv",
                        "content_digest": digest,
                        "byte_count": path.stat().st_size,
                        "form": {"csv_encoding": "utf-8-sig"},
                    },
                )
                result = api.dispatch(mutation, file_path=path)
            self.assertEqual(result.mutation.state, ApiMutation.State.COMPLETED)
            self.assertTrue(result.response["upload_id"].startswith("upload_"))

    def test_single_dataset_matrix_reaches_real_workflow_creation_boundary(self):
        base_url = f"http://127.0.0.1:{self.port}"
        upload = requests.post(
            f"{base_url}/v1/uploads",
            files={
                "file": (
                    "single-matrix.csv",
                    (
                        b"Account Name,Website,First Name,Last Name,Email\n"
                        b"Acme,https://acme.example,Example,Person,"
                        b"example@example.com\n"
                    ),
                    "text/csv",
                )
            },
            headers={"Idempotency-Key": f"matrix-upload-{uuid4().hex}"},
            timeout=10,
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        upload_id = upload.json()["upload_id"]
        product_entry = {
            "product_key": "easyimports.single_dataset_import",
            "tracks": {
                "dataset_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        }
        target_catalog = {
            "target_provider_id": "fake-preview-v1",
            "maximum_modes": {
                "dataset_writes": "disabled",
                "delivery": "preview",
            },
        }
        cases = (
            ("account", "", "", "", True),
            ("contact", "", "", "", True),
            ("lead", "", "", "", True),
            ("account", "people_and_accounts", "contacts", "generic", True),
            ("account", "people", "new_list", "generic", False),
            ("contact", "accounts", "accounts", "", False),
            ("account", "people_and_accounts", "", "", False),
            ("lead", "people", "accounts", "generic", False),
        )
        for index, (target, content, profile, kind, expected) in enumerate(cases):
            with self.subTest(
                target=target,
                content=content or "default",
                profile=profile or "default",
                kind=kind or "default",
            ):
                form = WorkflowConfigurationForm(
                    {
                        "form_token": "matrix",
                        "mode__dataset_writes": "disabled",
                        "mode__delivery": "disabled",
                        "target_object": target,
                        "content_type": content,
                        "canon_profile": profile,
                        "person_kind": kind,
                        "list_duplicate_policy": "surface",
                    },
                    product_entry=product_entry,
                    target=target_catalog,
                    uploaded_roles={"dataset"},
                )
                self.assertEqual(form.is_valid(), expected, form.errors)

                body = {
                    "product_key": "easyimports.single_dataset_import",
                    "target_provider_id": "fake-preview-v1",
                    "uploads": {"dataset": upload_id},
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "disabled",
                    },
                    "target_object": target,
                    "options": {"list_duplicate_policy": "surface"},
                }
                for name, value in (
                    ("content_type", content),
                    ("canon_profile", profile),
                    ("person_kind", kind),
                ):
                    if value:
                        body[name] = value
                created = requests.post(
                    f"{base_url}/v1/workflows",
                    json=body,
                    headers={"Idempotency-Key": f"single-matrix-{index}-{uuid4().hex}"},
                    timeout=10,
                )
                self.assertEqual(created.status_code == 201, expected, created.text)
                if expected:
                    self.assertEqual(created.json()["outcome"], "accepted")
                else:
                    # Do not freeze the backend's current internal_failure/500;
                    # the separately tracked API correction may make this 422.
                    self.assertFalse(200 <= created.status_code < 300)

    def test_real_workflow_survives_lost_response_restart_and_reaches_artifacts(self):
        base_url = f"http://127.0.0.1:{self.port}"
        with (
            tempfile.TemporaryDirectory() as directory,
            self.settings(
                EASYIMPORTS_API_BASE_URL=base_url,
                SESSIONS_ROOT=Path(directory),
            ),
        ):
            session = ImportSession.objects.create(
                owner_id=uuid4(),
                operator_label="Real Process Operator",
                product_key="easyimports.single_dataset_import",
                target_provider_id="fake-preview-v1",
            )
            api = EasyImportsApiClient()
            registration = save_and_register_upload(
                session=session,
                role="dataset",
                uploaded=SimpleUploadedFile(
                    "dataset.csv",
                    b"First Name,Last Name,Email\nExample,Person,example@example.com\n",
                    content_type="text/csv",
                ),
                form_instance=uuid4(),
                logical_action_generation=0,
                csv_encoding="utf-8-sig",
                xlsx_sheet_index=0,
                client=api,
            )
            # MAP-3: create body includes column_mapping; dual-process path
            # still validates schema before network dispatch (bind enforced by API).
            dig = "f" * 64
            body = validate_workflow_create(
                {
                    "product_key": "easyimports.single_dataset_import",
                    "target_provider_id": "fake-preview-v1",
                    "uploads": {"dataset": registration.response["upload_id"]},
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "preview",
                    },
                    "target_object": "contact",
                    "content_type": "people",
                    "canon_profile": "contacts",
                    "person_kind": "contact",
                    "options": {"list_duplicate_policy": "surface"},
                    "column_mapping": {
                        "plan_id": "cmp_real_process_schema",
                        "plan_content_digest": dig,
                        "source_schema_digest": dig,
                        "destination_digest": dig,
                        "target_contract_digest": dig,
                    },
                }
            )
            creation = create_or_reuse_mutation(
                session=session,
                form_instance=uuid4(),
                mutation_kind="create_workflow",
                route="/v1/workflows",
                logical_action_identity=f"session:{session.id}:create_workflow",
                form_payload_digest=canonical_digest(body),
                request_json=body,
            )
            lost_client = EasyImportsApiClient(http=_LostAfterDispatchSession())
            with self.assertRaises(ApiUnavailableError):
                lost_client.dispatch(creation)
            creation.refresh_from_db()
            self.assertEqual(creation.state, ApiMutation.State.UNKNOWN)
            frozen_key = creation.idempotency_key
            frozen_request = creation.request_json

            self._restart_api()
            api = EasyImportsApiClient()
            recovered = api.dispatch(creation, explicit_retry=True)
            self.assertEqual(recovered.mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(recovered.mutation.idempotency_key, frozen_key)
            self.assertEqual(recovered.mutation.request_json, frozen_request)
            workflow = materialize_accepted_workflow(session, recovered, client=api)
            self.assertIsNotNone(workflow)
            self.assertEqual(workflow.status, "awaiting_effect_authorization")

            intent = workflow.projection["effect_intent"]
            effect_command = {
                "expected_revision": workflow.revision,
                "selected_mode": "preview",
            }
            for key in (
                "intent_id",
                "track",
                "maximum_mode",
                "target_provider_id",
                "target_fingerprint",
                "work_digest",
                "confirmation",
            ):
                effect_command[key] = intent[key]
            effect_command = validate_effect_authorization(effect_command)
            with self.assertRaises(ApiOperationInProgressError):
                dispatch_command(
                    session=session,
                    workflow=workflow,
                    form_instance=uuid4(),
                    mutation_kind="authorize_effect",
                    route=f"/v1/workflows/{workflow.run_id}/effect-authorizations",
                    logical_action_identity=(
                        f"run:{workflow.run_id}:revision:{workflow.revision}:"
                        f"effect:{intent['intent_id']}:group:"
                    ),
                    logical_action_generation=0,
                    form_payload_digest=canonical_digest(
                        {"selected_mode": "preview"}
                    ),
                    body=effect_command,
                    client=api,
                )
            effect_mutation = session.api_mutations.get(
                mutation_kind="authorize_effect"
            )
            deadline = time.monotonic() + 10
            effect_result = None
            while effect_result is None:
                operation_status = api.mutation_status(
                    effect_mutation.idempotency_key
                )
                effect_result = api.reconcile_mutation_status(
                    effect_mutation,
                    operation_status,
                )
                if effect_result is None:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)
            terminal = materialize_accepted_workflow(session, effect_result, client=api)
            self.assertEqual(terminal.status, "succeeded")
            self.assertTrue(
                terminal.projection["terminal_evidence"]["accountability"]["reconciles"]
            )
            artifacts = api.artifacts(terminal.run_id)["artifacts"]
            self.assertGreater(len(artifacts), 0)
            artifact = artifacts[0]
            upstream = api.stream_artifact(
                artifact["download_url"],
                run_id=terminal.run_id,
                artifact_id=artifact["artifact_id"],
            )
            try:
                digest = __import__("hashlib").sha256()
                for chunk in upstream.iter_content(64 * 1024):
                    digest.update(chunk)
                self.assertEqual(digest.hexdigest(), artifact["content_digest"])
                self.assertEqual(
                    upstream.headers["ETag"],
                    f'"sha256:{artifact["content_digest"]}"',
                )
            finally:
                upstream.close()

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _run_fake_crm_seed_helper(self, *extra_args: str) -> None:
        """Seed/inject fake CRM org via a **separate** process (no mappings_2 import)."""

        repo = self._repo_root()
        helper = repo / "web" / "tools" / "seed_fake_crm_org.py"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repo)
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--state-root",
                self.state_dir.name,
                *extra_args,
            ],
            cwd=str(repo),
            env=environment,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            self.fail(
                "seed_fake_crm_org helper failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )

    def _seed_fake_company_duplicates(
        self,
        *,
        fail_loser_ids: set[str] | frozenset[str] | None = None,
        lost_response_loser_ids: set[str] | frozenset[str] | None = None,
        restart: bool = True,
        field_fill_pair: bool = False,
        ids: str = "A1,A2,A3",
    ) -> None:
        """Pre-seed durable fake org (and optional FE-EXEC injection controls)."""

        if restart:
            self._stop_api()
        if field_fill_pair:
            args = ["--field-fill-pair", "--name", "Acme", "--clear-injections"]
        else:
            args = ["--ids", ids, "--name", "Acme", "--clear-injections"]
        if fail_loser_ids:
            args.extend(["--fail-loser-ids", ",".join(sorted(fail_loser_ids))])
        if lost_response_loser_ids:
            args.extend(
                [
                    "--lost-response-loser-ids",
                    ",".join(sorted(lost_response_loser_ids)),
                ]
            )
        self._run_fake_crm_seed_helper(*args)
        if restart:
            self._start_api()
            self._wait_api_healthy()

    def _org_account_fields(self, record_id: str) -> dict:
        """Read one Account field bag from durable org.json (no mappings_2)."""

        org_path = Path(self.state_dir.name) / "fake_crm" / "org.json"
        payload = json.loads(org_path.read_text(encoding="utf-8"))
        records = payload.get("records") or {}
        rec = records.get(record_id) or {}
        if not isinstance(rec, dict):
            return {}
        fields = rec.get("fields") or {}
        return dict(fields) if isinstance(fields, dict) else {}

    def _durable_populate_projection_digest(self, group_id: str) -> str | None:
        """Read verified field-merge populate projection_digest from SQLite ledger."""

        import sqlite3
        from contextlib import closing

        ledger_path = (
            Path(self.state_dir.name) / "fake_crm" / "account_ledger.sqlite3"
        )
        if not ledger_path.exists():
            return None
        action_id = f"field_merge_populate:{group_id}"
        with closing(sqlite3.connect(str(ledger_path))) as connection:
            rows = connection.execute(
                """
                SELECT request_json, status FROM prepared_requests
                WHERE action_id = ?
                ORDER BY request_id
                """,
                (action_id,),
            ).fetchall()
        for request_json, status in rows:
            if str(status or "") != "verified":
                continue
            try:
                payload = json.loads(request_json or "{}")
            except json.JSONDecodeError:
                continue
            digest = str(payload.get("projection_digest") or "").strip()
            if digest:
                return digest
        return None

    def _inject_fake_merge_controls(
        self,
        *,
        fail_loser_ids: set[str] | frozenset[str] | None = None,
        lost_response_loser_ids: set[str] | frozenset[str] | None = None,
    ) -> None:
        """Restart the real API process with durable merge failure injection."""

        self._stop_api()
        args = ["--inject-only", "--clear-injections"]
        if fail_loser_ids:
            args.extend(["--fail-loser-ids", ",".join(sorted(fail_loser_ids))])
        if lost_response_loser_ids:
            args.extend(
                [
                    "--lost-response-loser-ids",
                    ",".join(sorted(lost_response_loser_ids)),
                ]
            )
        self._run_fake_crm_seed_helper(*args)
        self._start_api()

    def _org_mutation_snapshot(self) -> dict:
        """Read durable org.json mutation evidence without importing mappings_2."""

        org_path = Path(self.state_dir.name) / "fake_crm" / "org.json"
        payload = json.loads(org_path.read_text(encoding="utf-8"))
        records = payload.get("records") or {}
        deleted = sorted(
            rid
            for rid, rec in records.items()
            if isinstance(rec, dict)
            and rec.get("object_type") == "Account"
            and rec.get("is_deleted")
        )
        return {
            "mutation_count": int(payload.get("mutation_count") or 0),
            "deleted_account_ids": deleted,
            "fail_loser_ids": list(payload.get("fail_loser_ids") or []),
            "lost_response_loser_ids": list(
                payload.get("lost_response_loser_ids") or []
            ),
        }

    def _wait_api_healthy(self, *, attempts: int = 50) -> None:
        """Block until the real API process answers /health (post-restart)."""

        url = f"http://127.0.0.1:{self.port}/health"
        for _ in range(attempts):
            try:
                if requests.get(url, timeout=0.3).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.1)
        self.fail(f"API process not healthy at {url}")

    def _poll_pending_mutations(self, session: ImportSession, *, timeout: float = 90.0):
        """Drive Django mutation_status / exact-retry until no open effects remain."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            session.refresh_from_db()
            open_mutations = list(
                session.api_mutations.filter(
                    state__in=[
                        ApiMutation.State.PENDING,
                        ApiMutation.State.UNKNOWN,
                    ]
                )
            )
            if not open_mutations:
                return
            for mutation in open_mutations:
                if mutation.state == ApiMutation.State.PENDING:
                    response = self.client.get(
                        reverse(
                            "importer:mutation_status",
                            args=[session.id, mutation.id],
                        )
                    )
                    # 503 while API restarts after seed/inject — wait and retry.
                    if response.status_code == 503:
                        self._wait_api_healthy()
                        time.sleep(0.1)
                        continue
                    self.assertEqual(response.status_code, 200, response.content)
                    continue
                # UNKNOWN: exact-retry the frozen journal entry against the live API.
                api = EasyImportsApiClient()
                try:
                    result = api.dispatch(mutation, explicit_retry=True)
                except (
                    ApiOperationInProgressError,
                    ApiUnavailableError,
                    MutationExplicitRetryRequired,
                ):
                    self._wait_api_healthy()
                    time.sleep(0.1)
                    continue
                if result.mutation.state == ApiMutation.State.COMPLETED:
                    workflow = mutation.workflow
                    materialize_accepted_workflow(
                        session,
                        result,
                        role=(
                            workflow.role
                            if workflow is not None
                            else ApiWorkflow.Role.PRIMARY
                        ),
                        source_workflow=(
                            workflow.source_workflow
                            if workflow is not None
                            else None
                        ),
                        client=api,
                    )
                elif result.mutation.state == ApiMutation.State.REJECTED:
                    self.fail(
                        "Mutation rejected during poll: "
                        f"{mutation.mutation_kind} "
                        f"{result.mutation.error_code} "
                        f"{result.mutation.error_message} "
                        f"{result.mutation.response_json}"
                    )
            time.sleep(0.05)
        session.refresh_from_db()
        self.fail(
            "Timed out waiting for open mutations: "
            + ", ".join(
                f"{m.mutation_kind}:{m.state}:{m.error_message}"
                for m in session.api_mutations.filter(
                    state__in=[
                        ApiMutation.State.PENDING,
                        ApiMutation.State.UNKNOWN,
                    ]
                )
            )
        )

    def _authorize_execute_and_await(self, session: ImportSession, page) -> None:
        """POST execute authorization and wait until the mutation is terminal."""

        before_ids = set(
            session.api_mutations.filter(
                mutation_kind="authorize_effect"
            ).values_list("id", flat=True)
        )
        response = self.client.post(
            reverse("importer:authorize_effect", args=[session.id]),
            {
                "form_token": page.context["tokens"]["effect"],
                "selected_mode": "execute",
            },
        )
        self.assertEqual(response.status_code, 302)
        self._poll_pending_mutations(session)
        session.refresh_from_db()
        newest = (
            session.api_mutations.filter(mutation_kind="authorize_effect")
            .exclude(id__in=before_ids)
            .order_by("-created_at")
            .first()
        )
        if newest is None:
            # Form may have replayed an existing exact token; use latest.
            newest = (
                session.api_mutations.filter(mutation_kind="authorize_effect")
                .order_by("-created_at")
                .first()
            )
        self.assertIsNotNone(newest)
        self.assertEqual(
            newest.state,
            ApiMutation.State.COMPLETED,
            f"{newest.state} {newest.error_code} {newest.error_message} "
            f"{newest.response_json}",
        )
        self.assertEqual(newest.request_json.get("selected_mode"), "execute")

    def _workflow_page(self, session: ImportSession):
        session.refresh_from_db()
        page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200, page.content[:500])
        return page

    def _drive_to_duplicate_execute_gate(self):
        """Connect → journey → review → continuation execute gate (real API)."""

        self._seed_fake_company_duplicates()
        owner = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(owner)
        browser.save()

        connect = self.client.post(
            reverse("importer:crm_connect"),
            {"provider_key": "fake"},
        )
        self.assertEqual(connect.status_code, 302)
        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        start = journal.api_mutations.get(mutation_kind="crm_connection_start")
        self.assertEqual(start.state, ApiMutation.State.COMPLETED)
        connection_id = start.response_json["connection_id"]
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.COMPLETED)

        # Phase 4C: structural journey POST must carry the signed form_token
        # (same contract as the field-fill Seam E helper).
        journey_get = self.client.get(reverse("importer:crm_duplicate_journey"))
        self.assertEqual(journey_get.status_code, 200, journey_get.content[:500])
        form_token = journey_get.context["form_token"]
        journey_post = self.client.post(
            reverse("importer:crm_duplicate_journey"),
            {
                "form_token": form_token,
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": json.dumps(
                    [{"group_id": "g1", "member_ids": ["A1", "A2", "A3"]}]
                ),
                "duplicate_execution_maximum": "execute",
            },
        )
        self.assertEqual(journey_post.status_code, 302, journey_post.content[:800])
        session = ImportSession.objects.filter(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
        ).latest("created_at")
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.assertEqual(
            page.context["projection"]["effect_intent"]["track"],
            "reference_acquisition",
            page.context["projection"].get("error"),
        )
        self.client.post(
            reverse("importer:authorize_effect", args=[session.id]),
            {
                "form_token": page.context["tokens"]["effect"],
                "selected_mode": "execute",
            },
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.assertEqual(page.context["projection"]["status"], "awaiting_review")
        self.client.post(
            reverse("importer:start_review", args=[session.id]),
            {"form_token": page.context["tokens"]["review"]},
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        decision = page.context["projection"]["decision"]
        self.assertEqual(
            decision["decision_type"], "account_duplicate_group_review"
        )
        body = decision["body"]
        survivor = (
            body.get("selected_survivor_id")
            or body.get("recommended_survivor_id")
        )
        self.assertTrue(survivor)
        self.client.post(
            reverse("importer:submit_decision", args=[session.id]),
            {
                "form_token": page.context["tokens"]["decision"],
                "duplicate_choice": f"survivor:{survivor}",
            },
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.assertEqual(page.context["workflow"].status, "succeeded")
        self.client.post(
            reverse("importer:create_decision_set", args=[session.id]),
            {"form_token": page.context["tokens"]["decision_set"]},
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.client.post(
            reverse("importer:continue_source", args=[session.id]),
            {"form_token": page.context["tokens"]["continue"]},
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        intent = page.context["projection"]["effect_intent"]
        self.assertIsNotNone(intent)
        self.assertEqual(intent["track"], "duplicate_execution")
        self.assertIn("execute", intent["supported_modes"])
        return session, page

    def _drive_to_duplicate_execute_gate_field_fill(self):
        """Connect → journey (A1/A2 field-fill seed) → review force A1 → execute gate.

        Returns (session, page, displayed_projection_digest, group_id).
        """

        self._seed_fake_company_duplicates(field_fill_pair=True)
        owner = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(owner)
        browser.save()

        connect = self.client.post(
            reverse("importer:crm_connect"),
            {"provider_key": "fake"},
        )
        self.assertEqual(connect.status_code, 302)
        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        self._poll_pending_mutations(journal)
        start = journal.api_mutations.get(mutation_kind="crm_connection_start")
        self.assertEqual(
            start.state,
            ApiMutation.State.COMPLETED,
            f"{start.state} {start.error_code} {start.error_message}",
        )
        connection_id = start.response_json["connection_id"]
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.COMPLETED)

        journey_get = self.client.get(reverse("importer:crm_duplicate_journey"))
        self.assertEqual(journey_get.status_code, 200, journey_get.content[:500])
        form_token = journey_get.context["form_token"]
        journey_post = self.client.post(
            reverse("importer:crm_duplicate_journey"),
            {
                "form_token": form_token,
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": json.dumps(
                    [{"group_id": "g1", "member_ids": ["A1", "A2"]}]
                ),
                "duplicate_execution_maximum": "execute",
            },
        )
        self.assertEqual(journey_post.status_code, 302, journey_post.content[:800])
        session = ImportSession.objects.filter(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
        ).latest("created_at")
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.assertEqual(
            page.context["projection"]["effect_intent"]["track"],
            "reference_acquisition",
            page.context["projection"].get("error"),
        )
        self.client.post(
            reverse("importer:authorize_effect", args=[session.id]),
            {
                "form_token": page.context["tokens"]["effect"],
                "selected_mode": "execute",
            },
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.assertEqual(
            page.context["projection"]["status"],
            "awaiting_review",
            page.context["projection"].get("error"),
        )
        self.client.post(
            reverse("importer:start_review", args=[session.id]),
            {"form_token": page.context["tokens"]["review"]},
        )
        self._poll_pending_mutations(session)

        displayed_projection_digest = None
        group_id = None
        saw_fill_keys = False
        step = 0
        while step < 10:
            step += 1
            page = self._workflow_page(session)
            projection = page.context["projection"]
            decision = projection.get("decision")
            if decision is None:
                break
            self.assertEqual(
                decision["decision_type"], "account_duplicate_group_review"
            )
            body = decision["body"]
            group_id = body.get("duplicate_group_id") or group_id
            fmp = body.get("field_merge_plan") or {}
            populate_keys = fmp.get("populate_field_keys") or []
            if populate_keys:
                saw_fill_keys = True
                displayed_projection_digest = fmp.get("field_projection_digest")
            elif displayed_projection_digest is None:
                displayed_projection_digest = (
                    fmp.get("field_projection_digest")
                    or body.get("field_projection_digest")
                )
            # Prefer empty-field survivor A1 (override when recommended is A2).
            choice = "survivor:A1"
            self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": page.context["tokens"]["decision"],
                    "duplicate_choice": choice,
                },
            )
            self._poll_pending_mutations(session)
        else:
            self.fail("review decision loop did not terminate")

        page = self._workflow_page(session)
        self.assertEqual(
            page.context["workflow"].status,
            "succeeded",
            page.context["projection"].get("error"),
        )
        self.assertTrue(
            saw_fill_keys,
            msg="review must surface non-empty populate_field_keys for A1 fills",
        )
        self.assertTrue(displayed_projection_digest)
        self.assertTrue(group_id)

        self.client.post(
            reverse("importer:create_decision_set", args=[session.id]),
            {"form_token": page.context["tokens"]["decision_set"]},
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        self.client.post(
            reverse("importer:continue_source", args=[session.id]),
            {"form_token": page.context["tokens"]["continue"]},
        )
        self._poll_pending_mutations(session)

        page = self._workflow_page(session)
        intent = page.context["projection"]["effect_intent"]
        self.assertIsNotNone(intent)
        self.assertEqual(intent["track"], "duplicate_execution")
        self.assertIn("execute", intent["supported_modes"])
        return session, page, displayed_projection_digest, group_id

    def test_fe_exec_browser_django_api_execute_e2e(self):
        """FE-EXEC: real browser → Django → API execute on the fake stack."""

        base_url = f"http://127.0.0.1:{self.port}"
        with self.settings(EASYIMPORTS_API_BASE_URL=base_url):
            session, page = self._drive_to_duplicate_execute_gate()
            self._authorize_execute_and_await(session, page)

            page = self._workflow_page(session)
            projection = page.context["projection"]
            self.assertEqual(projection["status"], "succeeded")
            terminal = projection.get("terminal_evidence") or {}
            receipts = terminal.get("receipts") or []
            self.assertTrue(
                any(
                    r.get("track") == "duplicate_execution"
                    and r.get("authorization") == "execute"
                    for r in receipts
                ),
                receipts,
            )
            presentation = page.context["terminal_presentation"]
            self.assertFalse(presentation["no_crm_changes"])
            self.assertContains(page, "Groups processed")
            body_html = page.content.decode("utf-8")
            self.assertNotIn("access_token", body_html)
            self.assertNotIn("refresh_token", body_html)
            self.assertNotIn("client_secret", body_html)

            exec_mutations = list(
                session.api_mutations.filter(
                    mutation_kind="authorize_effect",
                    state=ApiMutation.State.COMPLETED,
                ).order_by("created_at")
            )
            self.assertGreaterEqual(len(exec_mutations), 2)
            last_exec = exec_mutations[-1]
            self.assertEqual(
                last_exec.request_json.get("selected_mode"), "execute"
            )
            frozen = last_exec.response_json
            api = EasyImportsApiClient()
            replay = api.dispatch(last_exec)
            self.assertEqual(replay.mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(replay.response, frozen)

    def test_fe_exec_django_api_field_merge_empty_survivor_fill(self):
        """4A-WIRE Seam E: real Django merge action fills empty survivor fields.

        Proves browser → Django form → API → field-aware Fake runtime with a
        meaningful empty-survivor projection (not FastAPI TestClient-only).
        """

        base_url = f"http://127.0.0.1:{self.port}"
        with self.settings(EASYIMPORTS_API_BASE_URL=base_url):
            (
                session,
                page,
                displayed_digest,
                group_id,
            ) = self._drive_to_duplicate_execute_gate_field_fill()
            before_fields = self._org_account_fields("A1")
            self.assertIsNone(before_fields.get("account_website"))
            self.assertIsNone(before_fields.get("account_linkedin_url"))

            self._authorize_execute_and_await(session, page)

            page = self._workflow_page(session)
            projection = page.context["projection"]
            self.assertEqual(
                projection["status"],
                "succeeded",
                projection.get("error"),
            )
            # Survivor A1 received empty-fills; loser A2 consolidated away.
            after_a1 = self._org_account_fields("A1")
            self.assertEqual(
                after_a1.get("account_website"), "https://from-a2.example"
            )
            self.assertEqual(
                after_a1.get("account_linkedin_url"),
                "https://linkedin.com/company/a2",
            )
            snap = self._org_mutation_snapshot()
            self.assertIn("A2", snap["deleted_account_ids"])
            self.assertNotIn("A1", snap["deleted_account_ids"])

            # Displayed review digest equals durable populate projection_digest.
            executed = self._durable_populate_projection_digest(str(group_id))
            self.assertTrue(executed, msg="missing durable verified populate row")
            self.assertEqual(executed, displayed_digest)

            terminal = projection.get("terminal_evidence") or {}
            receipts = terminal.get("receipts") or []
            self.assertTrue(
                any(
                    r.get("track") == "duplicate_execution"
                    and r.get("authorization") == "execute"
                    for r in receipts
                ),
                receipts,
            )
            body_html = page.content.decode("utf-8")
            self.assertNotIn("access_token", body_html)
            self.assertNotIn("client_secret", body_html)

    def test_fe_exec_real_process_partial_failure_surfaces_in_django(self):
        """FE-EXEC: attributable partial failure via real Django → API process."""

        base_url = f"http://127.0.0.1:{self.port}"
        with self.settings(EASYIMPORTS_API_BASE_URL=base_url):
            session, page = self._drive_to_duplicate_execute_gate()
            # Inject after plan-ready gate so reference acquisition still succeeds.
            self._inject_fake_merge_controls(
                fail_loser_ids={"A1", "A2", "A3"}
            )
            before = self._org_mutation_snapshot()
            page = self._workflow_page(session)
            self.assertEqual(
                page.context["projection"]["effect_intent"]["track"],
                "duplicate_execution",
            )
            self._authorize_execute_and_await(session, page)

            page = self._workflow_page(session)
            projection = page.context["projection"]
            # Deterministic: all loser merges fail closed without CRM mutation.
            self.assertEqual(projection["status"], "succeeded", projection.get("error"))
            terminal = projection.get("terminal_evidence") or {}
            dupe = [
                r
                for r in (terminal.get("receipts") or [])
                if r.get("track") == "duplicate_execution"
            ]
            self.assertEqual(len(dupe), 1, dupe)
            self.assertEqual(dupe[0].get("authorization"), "execute")
            self.assertEqual(dupe[0].get("outcome"), "completed_with_failures")
            evidence = dupe[0].get("evidence") or {}
            self.assertEqual(
                (evidence.get("status_counts") or {}).get("failed"),
                1,
                evidence,
            )
            dispositions = (terminal.get("accountability") or {}).get(
                "dispositions"
            ) or []
            self.assertEqual(len(dispositions), 1, dispositions)
            disposition = dispositions[0]
            self.assertEqual(disposition.get("status"), "failed")
            self.assertEqual(
                disposition.get("error_code"), "duplicate_action_incomplete"
            )
            self.assertEqual(disposition.get("verified_action_ids") or [], [])
            self.assertEqual(
                set(disposition.get("member_ids") or []),
                {"A1", "A2", "A3"},
            )
            presentation = page.context["terminal_presentation"]
            self.assertFalse(presentation["no_crm_changes"])
            self.assertContains(page, "Completed with issues")
            after = self._org_mutation_snapshot()
            self.assertEqual(after["mutation_count"], before["mutation_count"])
            self.assertEqual(after["deleted_account_ids"], [])
            body_html = page.content.decode("utf-8")
            self.assertNotIn("access_token", body_html)

    def test_fe_exec_real_process_paused_unknown_resume_via_django(self):
        """FE-EXEC: lost-response pauses unknown; Django resume reconciles once."""

        base_url = f"http://127.0.0.1:{self.port}"
        with self.settings(EASYIMPORTS_API_BASE_URL=base_url):
            session, page = self._drive_to_duplicate_execute_gate()
            # Survivor is A1; losers A2/A3 each lose one response then resume.
            self._inject_fake_merge_controls(
                lost_response_loser_ids={"A2", "A3"}
            )
            before = self._org_mutation_snapshot()
            page = self._workflow_page(session)
            self._authorize_execute_and_await(session, page)

            page = self._workflow_page(session)
            projection = page.context["projection"]
            self.assertEqual(
                projection["status"],
                "paused_unknown",
                projection.get("error"),
            )
            self.assertIsNone(projection.get("terminal_evidence"))
            self.assertContains(page, "Effect checkpoint paused")
            self.assertContains(page, "Verify and continue")
            self.assertIn("resume", page.context["tokens"])
            mid = self._org_mutation_snapshot()
            self.assertGreater(mid["mutation_count"], before["mutation_count"])
            self.assertEqual(len(mid["deleted_account_ids"]), 1)
            self.assertTrue(
                set(mid["deleted_account_ids"]).issubset({"A2", "A3"})
            )

            # Resume until terminal success (bounded).
            resume_posts = 0
            for _step in range(1, 5):
                page = self._workflow_page(session)
                status = page.context["projection"]["status"]
                if status == "succeeded":
                    break
                self.assertIn(
                    status,
                    {"paused_unknown", "paused_verification"},
                    status,
                )
                resume = self.client.post(
                    reverse("importer:resume_effect", args=[session.id]),
                    {"form_token": page.context["tokens"]["resume"]},
                )
                self.assertEqual(resume.status_code, 302)
                resume_posts += 1
                self._poll_pending_mutations(session)
            else:
                self.fail("did not reach succeeded after resumes")
            self.assertGreaterEqual(resume_posts, 1)

            page = self._workflow_page(session)
            after = page.context["projection"]
            self.assertEqual(after["status"], "succeeded", after.get("error"))
            terminal = after.get("terminal_evidence") or {}
            dupe = [
                r
                for r in (terminal.get("receipts") or [])
                if r.get("track") == "duplicate_execution"
            ]
            self.assertEqual(len(dupe), 1, dupe)
            self.assertEqual(dupe[0].get("authorization"), "execute")
            self.assertEqual(dupe[0].get("outcome"), "succeeded")
            final = self._org_mutation_snapshot()
            # Exactly the two losers are merged; no further CRM writes after terminal.
            self.assertEqual(set(final["deleted_account_ids"]), {"A2", "A3"})
            self.assertEqual(final["mutation_count"], 2)
            # Exact resume replay must not re-mutate CRM.
            resume_mutations = list(
                session.api_mutations.filter(
                    mutation_kind="resume_effect",
                    state=ApiMutation.State.COMPLETED,
                ).order_by("created_at")
            )
            self.assertGreaterEqual(len(resume_mutations), 1)
            last_resume = resume_mutations[-1]
            api = EasyImportsApiClient()
            replay = api.dispatch(last_resume)
            self.assertEqual(replay.mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(replay.response, last_resume.response_json)
            replay_snap = self._org_mutation_snapshot()
            self.assertEqual(
                replay_snap["mutation_count"], final["mutation_count"]
            )
            self.assertEqual(
                set(replay_snap["deleted_account_ids"]),
                set(final["deleted_account_ids"]),
            )
            body_html = page.content.decode("utf-8")
            self.assertNotIn("access_token", body_html)
            self.assertNotIn("refresh_token", body_html)


class CrmConnectionSetupUxPhase2Tests(TestCase):
    """Phase 2: Guided setup wizard shell (network-free)."""

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "form_token missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def test_hub_setup_cta_enters_wizard_pick(self):
        response = self.client.get(reverse("importer:crm_setup_start"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("importer:crm_setup_pick"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        self.assertEqual(pick.status_code, 200)
        body = pick.content.decode("utf-8")
        self.assertIn("Set up new CRM connection", body)
        self.assertIn("Practice CRM", body)
        self.assertIn("Salesforce", body)
        self.assertIn("HubSpot", body)
        self.assertIn('name="form_token"', body)
        self.assertIn(reverse("importer:crm_setup_choose"), body)
        self.assertNotIn("client_secret", body)
        self.assertNotIn("access_token", body)
        # Server draft session exists (D3).
        self.assertTrue(
            ImportSession.objects.filter(product_key="crm.setup").exists()
        )

    def _extract_csrf_token(self, html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "csrfmiddlewaretoken missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def test_choose_requires_signed_token(self):
        self.client.get(reverse("importer:crm_setup_start"))
        bare = self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "fake"},
        )
        self.assertEqual(bare.status_code, 302)
        self.assertEqual(bare["Location"], reverse("importer:crm_setup_pick"))

        pick = self.client.get(reverse("importer:crm_setup_pick"))
        token = self._extract_form_token(pick.content.decode("utf-8"))
        ok = self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "fake", "form_token": token},
        )
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(ok["Location"], reverse("importer:crm_setup_practice"))
        session = ImportSession.objects.get(product_key="crm.setup")
        draft = (session.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("provider_key"), "fake")
        self.assertEqual(draft.get("step"), "provider")

    def test_wizard_choose_enforces_csrf(self):
        """CSRF middleware is active; prove choose POST requires the token."""
        csrf_client = Client(enforce_csrf_checks=True)
        start = csrf_client.get(reverse("importer:crm_setup_start"))
        self.assertEqual(start.status_code, 302)
        pick = csrf_client.get(reverse("importer:crm_setup_pick"))
        self.assertEqual(pick.status_code, 200)
        body = pick.content.decode("utf-8")
        form_token = self._extract_form_token(body)
        csrf_token = self._extract_csrf_token(body)

        denied = csrf_client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "fake", "form_token": form_token},
        )
        self.assertEqual(denied.status_code, 403)

        allowed = csrf_client.post(
            reverse("importer:crm_setup_choose"),
            data={
                "provider_key": "fake",
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf_token,
            },
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(
            allowed["Location"], reverse("importer:crm_setup_practice")
        )

    def test_hubspot_choose_enters_guided_path(self):
        """Phase 3B: HubSpot choose lands on guided credentials (not pending shell)."""
        self.client.get(reverse("importer:crm_setup_start"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        token = self._extract_form_token(pick.content.decode("utf-8"))
        chosen = self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "hubspot", "form_token": token},
        )
        self.assertEqual(chosen.status_code, 302)
        self.assertEqual(
            chosen["Location"], reverse("importer:crm_setup_hubspot")
        )
        # /crm/setup/hubspot/ is the guided path (dedicated route wins over the
        # legacy pending shell pattern with the same URL).
        page = self.client.get(reverse("importer:crm_setup_hubspot"))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Set up HubSpot", body)
        self.assertIn('name="access_token"', body)
        self.assertNotIn("coming soon", body.lower())

    def test_salesforce_choose_enters_guided_path(self):
        self.client.get(reverse("importer:crm_setup_start"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        token = self._extract_form_token(pick.content.decode("utf-8"))
        chosen = self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "salesforce", "form_token": token},
        )
        self.assertEqual(chosen.status_code, 302)
        self.assertEqual(
            chosen["Location"], reverse("importer:crm_setup_salesforce")
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_practice_wizard_connects_and_shows_success(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_wizard_practice"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": "https://example.test/oauth",
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "connected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self.client.get(reverse("importer:crm_setup_start"))
            pick = self.client.get(reverse("importer:crm_setup_pick"))
            choose_token = self._extract_form_token(pick.content.decode("utf-8"))
            chosen = self.client.post(
                reverse("importer:crm_setup_choose"),
                data={"provider_key": "fake", "form_token": choose_token},
            )
            self.assertEqual(
                chosen["Location"], reverse("importer:crm_setup_practice")
            )
            practice = self.client.get(reverse("importer:crm_setup_practice"))
            self.assertEqual(practice.status_code, 200)
            practice_body = practice.content.decode("utf-8")
            self.assertNotIn("client_secret", practice_body)
            connect_token = self._extract_form_token(practice_body)
            connected = self.client.post(
                reverse("importer:crm_setup_practice_connect"),
                data={"form_token": connect_token},
            )
            self.assertEqual(connected.status_code, 302)
            self.assertEqual(
                connected["Location"], reverse("importer:crm_setup_complete")
            )
            complete = self.client.get(reverse("importer:crm_setup_complete"))
            self.assertEqual(complete.status_code, 200)
            complete_body = complete.content.decode("utf-8")
            self.assertIn("Connected", complete_body)
            self.assertIn("Practice CRM", complete_body)
            self.assertIn(connection_id, complete_body)
            self.assertIn(reverse("importer:crm_duplicate_journey"), complete_body)
            self.assertNotIn("client_secret", complete_body)
            self.assertNotIn("access_token", complete_body)

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        self.assertTrue(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_start",
                state=ApiMutation.State.COMPLETED,
            ).exists()
        )
        self.assertTrue(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_oauth_complete",
                state=ApiMutation.State.COMPLETED,
            ).exists()
        )
        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("outcome"), "connected")
        self.assertEqual(draft.get("connection_id"), connection_id)
        self.assertNotIn("client_secret", str(setup.options))
        self.assertNotIn("access_token", str(setup.options))

    def test_practice_connect_rejects_stale_token(self):
        self.client.get(reverse("importer:crm_setup_start"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        token = self._extract_form_token(pick.content.decode("utf-8"))
        self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "fake", "form_token": token},
        )
        rejected = self.client.post(
            reverse("importer:crm_setup_practice_connect"),
            data={"form_token": "not-a-valid-token"},
        )
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(
            rejected["Location"], reverse("importer:crm_setup_practice")
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_practice_connect_same_token_double_submit_replays_once(self):
        """D3: same practice form token must not start a second connect generation."""
        owner = uuid4()
        browser_session = self.client.session
        browser_session[OWNER_SESSION_KEY] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_wizard_replay"
        start_dispatch_calls = {"n": 0}
        complete_dispatch_calls = {"n": 0}

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                start_dispatch_calls["n"] += 1
                # Exact-replay of a finished mutation must not re-enter create.
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": "https://example.test/oauth",
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                complete_dispatch_calls["n"] += 1
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "connected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self.client.get(reverse("importer:crm_setup_start"))
            pick = self.client.get(reverse("importer:crm_setup_pick"))
            choose_token = self._extract_form_token(pick.content.decode("utf-8"))
            self.client.post(
                reverse("importer:crm_setup_choose"),
                data={"provider_key": "fake", "form_token": choose_token},
            )
            practice = self.client.get(reverse("importer:crm_setup_practice"))
            connect_token = self._extract_form_token(
                practice.content.decode("utf-8")
            )
            setup = ImportSession.objects.get(
                owner_id=owner, product_key="crm.setup"
            )
            claims = decode_form_token(
                connect_token,
                owner_id=owner,
                session=setup,
                action_kind="crm_setup_practice_connect",
            )
            first = self.client.post(
                reverse("importer:crm_setup_practice_connect"),
                data={"form_token": connect_token},
            )
            self.assertEqual(first.status_code, 302)
            self.assertEqual(
                first["Location"], reverse("importer:crm_setup_complete")
            )
            second = self.client.post(
                reverse("importer:crm_setup_practice_connect"),
                data={"form_token": connect_token},
            )
            self.assertEqual(second.status_code, 302)
            self.assertEqual(
                second["Location"], reverse("importer:crm_setup_complete")
            )

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        starts = list(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_start"
            ).order_by("created_at")
        )
        completes = list(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_oauth_complete"
            ).order_by("created_at")
        )
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(completes), 1)
        self.assertEqual(starts[0].form_instance, claims["form_instance"])
        self.assertEqual(starts[0].logical_action_generation, 0)
        self.assertEqual(
            starts[0].logical_action_identity,
            f"crm-connect:fake:django-{owner}",
        )
        self.assertEqual(starts[0].response_json["connection_id"], connection_id)
        self.assertEqual(completes[0].response_json["connection_id"], connection_id)
        # Dispatch may be invoked twice (replay), but only one durable start/complete.
        self.assertGreaterEqual(start_dispatch_calls["n"], 1)
        self.assertGreaterEqual(complete_dispatch_calls["n"], 1)


class CrmConnectionSetupUxPhase3ATests(TestCase):
    """Phase 3A: Salesforce guided registration + connect (network-free)."""

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "form_token missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def _choose_salesforce(self):
        self.client.get(reverse("importer:crm_setup_start"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        token = self._extract_form_token(pick.content.decode("utf-8"))
        return self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "salesforce", "form_token": token},
        )

    def test_salesforce_credentials_page_shows_callback_and_fields(self):
        self._choose_salesforce()
        page = self.client.get(reverse("importer:crm_setup_salesforce"))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Set up Salesforce", body)
        self.assertIn("https://127.0.0.1:8001/crm/oauth/callback/", body)
        self.assertIn('name="login_environment"', body)
        self.assertIn('name="client_id"', body)
        self.assertIn('name="client_secret"', body)
        self.assertIn('type="password"', body)
        self.assertIn(reverse("importer:crm_setup_salesforce_save"), body)
        # No pre-filled secrets on GET.
        self.assertNotIn("super-secret", body)
        self.assertNotIn("access_token", body)

    def test_salesforce_secret_page_requires_loopback(self):
        self._choose_salesforce()
        non_loop = Client(REMOTE_ADDR="203.0.113.9")
        # Share owner session so draft exists for this owner path is separate —
        # loopback gate fails closed before draft checks.
        page = non_loop.get(reverse("importer:crm_setup_salesforce"))
        self.assertEqual(page.status_code, 403)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_salesforce_save_journals_digest_not_plaintext_secret(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        secret = "sf-client-secret-value-never-journal"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_app_registration_salesforce_put":
                raise AssertionError(mutation.mutation_kind)
            body = mutation.request_json or {}
            self.assertIn("client_secret_digest", body)
            self.assertNotIn("client_secret", body)
            self.assertNotIn(secret, str(body))
            self.assertEqual(body.get("login_environment"), "sandbox")
            self.assertEqual(body.get("client_id"), "3MVG_client_id_xyz")
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "salesforce",
                "label": "Dev sandbox",
                "client_secret_configured": True,
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_salesforce()
            page = self.client.get(reverse("importer:crm_setup_salesforce"))
            token = self._extract_form_token(page.content.decode("utf-8"))
            saved = self.client.post(
                reverse("importer:crm_setup_salesforce_save"),
                data={
                    "form_token": token,
                    "label": "Dev sandbox",
                    "login_environment": "sandbox",
                    "client_id": "3MVG_client_id_xyz",
                    "client_secret": secret,
                    "my_domain_host": "",
                    "expected_org_id": "",
                },
            )
            self.assertEqual(saved.status_code, 302)
            self.assertEqual(
                saved["Location"], reverse("importer:crm_setup_salesforce_connect")
            )

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        mut = setup.api_mutations.get(
            mutation_kind="crm_app_registration_salesforce_put"
        )
        self.assertEqual(mut.state, ApiMutation.State.COMPLETED)
        self.assertNotIn(secret, str(mut.request_json))
        self.assertNotIn("client_secret", mut.request_json or {})
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("outcome"), "registration_ready")
        self.assertEqual(draft.get("client_id"), "3MVG_client_id_xyz")
        self.assertNotIn(secret, str(setup.options))

        # Connect step renders without secrets.
        connect_page = self.client.get(reverse("importer:crm_setup_salesforce_connect"))
        self.assertEqual(connect_page.status_code, 200)
        connect_body = connect_page.content.decode("utf-8")
        self.assertIn("Connect Salesforce org", connect_body)
        self.assertNotIn(secret, connect_body)
        self.assertNotIn('name="client_secret"', connect_body)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_salesforce_blank_secret_creates_no_mutation(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch") as mock_dispatch,
        ):
            self._choose_salesforce()
            page = self.client.get(reverse("importer:crm_setup_salesforce"))
            token = self._extract_form_token(page.content.decode("utf-8"))
            rejected = self.client.post(
                reverse("importer:crm_setup_salesforce_save"),
                data={
                    "form_token": token,
                    "label": "Dev",
                    "login_environment": "production",
                    "client_id": "cid",
                    "client_secret": "",
                },
            )
            self.assertEqual(rejected.status_code, 302)
            self.assertEqual(
                rejected["Location"], reverse("importer:crm_setup_salesforce")
            )
            mock_dispatch.assert_not_called()
        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        self.assertEqual(
            setup.api_mutations.filter(
                mutation_kind="crm_app_registration_salesforce_put"
            ).count(),
            0,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_salesforce_save_double_submit_replays_once(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        secret = "same-secret-for-replay"
        dispatch_n = {"n": 0}

        def dispatch(mutation, **kwargs):
            dispatch_n["n"] += 1
            if mutation.state == ApiMutation.State.COMPLETED:
                return MutationDispatchResult(mutation, mutation.response_json)
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "salesforce",
                "label": "Replay",
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_salesforce()
            page = self.client.get(reverse("importer:crm_setup_salesforce"))
            token = self._extract_form_token(page.content.decode("utf-8"))
            data = {
                "form_token": token,
                "label": "Replay",
                "login_environment": "sandbox",
                "client_id": "cid-replay",
                "client_secret": secret,
            }
            first = self.client.post(
                reverse("importer:crm_setup_salesforce_save"), data=data
            )
            second = self.client.post(
                reverse("importer:crm_setup_salesforce_save"), data=data
            )
            self.assertEqual(first.status_code, 302)
            self.assertEqual(second.status_code, 302)

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        puts = setup.api_mutations.filter(
            mutation_kind="crm_app_registration_salesforce_put"
        )
        self.assertEqual(puts.count(), 1)
        claims = decode_form_token(
            token,
            owner_id=owner,
            session=setup,
            action_kind="crm_setup_salesforce_save",
        )
        mut = puts.get()
        self.assertEqual(mut.form_instance, claims["form_instance"])
        self.assertEqual(mut.logical_action_identity, "crm-reg:salesforce:put:wizard")

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_salesforce_connect_form_scoped_synthetic_complete(self):
        """After registration_ready, synthetic connect (no live OAuth) succeeds."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        connection_id = "crm_conn_sf_wizard"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_app_registration_salesforce_put":
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = {"provider_key": "salesforce"}
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, mutation.response_json)
            if mutation.mutation_kind == "crm_connection_start":
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                # Synthetic: no authorization_url so wizard completes in-process.
                body = {
                    "connection_id": connection_id,
                    "provider_key": "salesforce",
                    "provider_label": "Salesforce",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                body = {
                    "connection_id": connection_id,
                    "provider_key": "salesforce",
                    "provider_label": "Salesforce",
                    "status": "connected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(mutation.mutation_kind)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_salesforce()
            page = self.client.get(reverse("importer:crm_setup_salesforce"))
            save_token = self._extract_form_token(page.content.decode("utf-8"))
            self.client.post(
                reverse("importer:crm_setup_salesforce_save"),
                data={
                    "form_token": save_token,
                    "label": "SF",
                    "login_environment": "sandbox",
                    "client_id": "cid",
                    "client_secret": "secret-ok",
                },
            )
            connect_page = self.client.get(
                reverse("importer:crm_setup_salesforce_connect")
            )
            connect_token = self._extract_form_token(
                connect_page.content.decode("utf-8")
            )
            connected = self.client.post(
                reverse("importer:crm_setup_salesforce_connect_submit"),
                data={"form_token": connect_token},
            )
            self.assertEqual(connected.status_code, 302)
            self.assertEqual(
                connected["Location"], reverse("importer:crm_setup_complete")
            )
            complete = self.client.get(reverse("importer:crm_setup_complete"))
            self.assertContains(complete, "Salesforce")
            self.assertContains(complete, connection_id)
            self.assertNotContains(complete, "secret-ok")

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        self.assertEqual(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_start"
            ).count(),
            1,
        )
        start = journal.api_mutations.get(mutation_kind="crm_connection_start")
        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        claims = decode_form_token(
            connect_token,
            owner_id=owner,
            session=setup,
            action_kind="crm_setup_salesforce_connect",
        )
        self.assertEqual(start.form_instance, claims["form_instance"])


class CrmConnectionSetupUxPhase3BTests(TestCase):
    """Phase 3B: HubSpot guided registration + connect (network-free)."""

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "form_token missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def _choose_hubspot(self):
        self.client.get(reverse("importer:crm_setup_start"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        token = self._extract_form_token(pick.content.decode("utf-8"))
        return self.client.post(
            reverse("importer:crm_setup_choose"),
            data={"provider_key": "hubspot", "form_token": token},
        )

    def test_hubspot_credentials_page_shows_private_app_and_oauth_fields(self):
        self._choose_hubspot()
        page = self.client.get(reverse("importer:crm_setup_hubspot"))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Set up HubSpot", body)
        self.assertIn("https://127.0.0.1:8001/crm/oauth/callback/", body)
        self.assertIn('name="auth_mode"', body)
        self.assertIn("private_app", body)
        self.assertIn("oauth", body)
        self.assertIn('name="access_token"', body)
        self.assertIn('name="expected_hub_id"', body)
        self.assertIn('name="client_id"', body)
        self.assertIn('name="client_secret"', body)
        self.assertIn(reverse("importer:crm_setup_hubspot_save"), body)
        self.assertNotIn("super-secret-token", body)

    def test_hubspot_secret_page_requires_loopback(self):
        self._choose_hubspot()
        non_loop = Client(REMOTE_ADDR="203.0.113.9")
        page = non_loop.get(reverse("importer:crm_setup_hubspot"))
        self.assertEqual(page.status_code, 403)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_private_app_save_journals_digest_not_plaintext_token(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        token_value = "pat-hubspot-token-never-journal"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_app_registration_hubspot_put":
                raise AssertionError(mutation.mutation_kind)
            body = mutation.request_json or {}
            self.assertEqual(body.get("auth_mode"), "private_app")
            self.assertIn("access_token_digest", body)
            self.assertNotIn("access_token", body)
            self.assertNotIn(token_value, str(body))
            self.assertEqual(body.get("expected_hub_id"), "12345678")
            self.assertIsNone(body.get("client_id"))
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "hubspot",
                "label": "Work portal",
                "auth_mode": "private_app",
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            form_token = self._extract_form_token(page.content.decode("utf-8"))
            saved = self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": form_token,
                    "label": "Work portal",
                    "auth_mode": "private_app",
                    "expected_hub_id": "12345678",
                    "access_token": token_value,
                },
            )
            self.assertEqual(saved.status_code, 302)
            self.assertEqual(
                saved["Location"], reverse("importer:crm_setup_hubspot_connect")
            )

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        mut = setup.api_mutations.get(
            mutation_kind="crm_app_registration_hubspot_put"
        )
        self.assertEqual(mut.state, ApiMutation.State.COMPLETED)
        self.assertNotIn(token_value, str(mut.request_json))
        self.assertNotIn("access_token", mut.request_json or {})
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("outcome"), "registration_ready")
        self.assertEqual(draft.get("auth_mode"), "private_app")
        self.assertEqual(draft.get("expected_hub_id"), "12345678")
        self.assertNotIn(token_value, str(setup.options))

        connect_page = self.client.get(reverse("importer:crm_setup_hubspot_connect"))
        self.assertEqual(connect_page.status_code, 200)
        connect_body = connect_page.content.decode("utf-8")
        self.assertIn("Connect HubSpot portal", connect_body)
        self.assertNotIn(token_value, connect_body)
        self.assertNotIn('name="access_token"', connect_body)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_oauth_save_journals_client_secret_digest(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        secret = "hs-oauth-client-secret-never-journal"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_app_registration_hubspot_put":
                raise AssertionError(mutation.mutation_kind)
            body = mutation.request_json or {}
            self.assertEqual(body.get("auth_mode"), "oauth")
            self.assertIn("client_secret_digest", body)
            self.assertNotIn("client_secret", body)
            self.assertNotIn(secret, str(body))
            self.assertEqual(body.get("client_id"), "hs-client-id-xyz")
            self.assertEqual(
                body.get("redirect_uri"),
                "https://127.0.0.1:8001/crm/oauth/callback/",
            )
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "hubspot",
                "label": "OAuth app",
                "auth_mode": "oauth",
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            form_token = self._extract_form_token(page.content.decode("utf-8"))
            saved = self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": form_token,
                    "label": "OAuth app",
                    "auth_mode": "oauth",
                    "client_id": "hs-client-id-xyz",
                    "client_secret": secret,
                    "expected_hub_id": "",
                },
            )
            self.assertEqual(saved.status_code, 302)
            self.assertEqual(
                saved["Location"], reverse("importer:crm_setup_hubspot_connect")
            )

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        mut = setup.api_mutations.get(
            mutation_kind="crm_app_registration_hubspot_put"
        )
        self.assertNotIn(secret, str(mut.request_json))
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("auth_mode"), "oauth")
        self.assertEqual(draft.get("client_id"), "hs-client-id-xyz")

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_blank_token_creates_no_mutation(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch") as mock_dispatch,
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            form_token = self._extract_form_token(page.content.decode("utf-8"))
            rejected = self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": form_token,
                    "label": "Work",
                    "auth_mode": "private_app",
                    "expected_hub_id": "999",
                    "access_token": "",
                },
            )
            self.assertEqual(rejected.status_code, 302)
            self.assertEqual(
                rejected["Location"], reverse("importer:crm_setup_hubspot")
            )
            mock_dispatch.assert_not_called()
        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        self.assertEqual(
            setup.api_mutations.filter(
                mutation_kind="crm_app_registration_hubspot_put"
            ).count(),
            0,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_save_double_submit_replays_once(self):
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        token_value = "same-token-for-replay"
        dispatch_n = {"n": 0}

        def dispatch(mutation, **kwargs):
            dispatch_n["n"] += 1
            if mutation.state == ApiMutation.State.COMPLETED:
                return MutationDispatchResult(mutation, mutation.response_json)
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "hubspot",
                "label": "Replay",
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            form_token = self._extract_form_token(page.content.decode("utf-8"))
            data = {
                "form_token": form_token,
                "label": "Replay",
                "auth_mode": "private_app",
                "expected_hub_id": "42",
                "access_token": token_value,
            }
            first = self.client.post(
                reverse("importer:crm_setup_hubspot_save"), data=data
            )
            second = self.client.post(
                reverse("importer:crm_setup_hubspot_save"), data=data
            )
            self.assertEqual(first.status_code, 302)
            self.assertEqual(second.status_code, 302)

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        puts = setup.api_mutations.filter(
            mutation_kind="crm_app_registration_hubspot_put"
        )
        self.assertEqual(puts.count(), 1)
        claims = decode_form_token(
            form_token,
            owner_id=owner,
            session=setup,
            action_kind="crm_setup_hubspot_save",
        )
        mut = puts.get()
        self.assertEqual(mut.form_instance, claims["form_instance"])
        self.assertEqual(mut.logical_action_identity, "crm-reg:hubspot:put:wizard")

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_connect_form_scoped_synthetic_complete(self):
        """After registration_ready, synthetic connect (no live OAuth) succeeds."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        connection_id = "crm_conn_hs_wizard"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_app_registration_hubspot_put":
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = {"provider_key": "hubspot"}
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, mutation.response_json)
            if mutation.mutation_kind == "crm_connection_start":
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                # Synthetic private-app style: no authorization_url.
                body = {
                    "connection_id": connection_id,
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "private_app",
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                body = {
                    "connection_id": connection_id,
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "connected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(mutation.mutation_kind)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            save_token = self._extract_form_token(page.content.decode("utf-8"))
            self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": save_token,
                    "label": "HS",
                    "auth_mode": "private_app",
                    "expected_hub_id": "55",
                    "access_token": "token-ok",
                },
            )
            connect_page = self.client.get(
                reverse("importer:crm_setup_hubspot_connect")
            )
            connect_token = self._extract_form_token(
                connect_page.content.decode("utf-8")
            )
            connected = self.client.post(
                reverse("importer:crm_setup_hubspot_connect_submit"),
                data={"form_token": connect_token},
            )
            self.assertEqual(connected.status_code, 302)
            self.assertEqual(
                connected["Location"], reverse("importer:crm_setup_complete")
            )
            complete = self.client.get(reverse("importer:crm_setup_complete"))
            self.assertContains(complete, "HubSpot")
            self.assertContains(complete, connection_id)
            self.assertNotContains(complete, "token-ok")

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        self.assertEqual(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_start"
            ).count(),
            1,
        )
        start = journal.api_mutations.get(mutation_kind="crm_connection_start")
        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        claims = decode_form_token(
            connect_token,
            owner_id=owner,
            session=setup,
            action_kind="crm_setup_hubspot_connect",
        )
        self.assertEqual(start.form_instance, claims["form_instance"])
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("outcome"), "connected")
        self.assertEqual(draft.get("connection_id"), connection_id)
        self.assertNotIn("token-ok", str(setup.options))

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_stale_form_cannot_overwrite_newer_registration(self):
        """Two forms opened at gen 0: second submit must not advance past claim."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        put_labels: list[str] = []

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_app_registration_hubspot_put":
                raise AssertionError(mutation.mutation_kind)
            if mutation.state == ApiMutation.State.COMPLETED:
                return MutationDispatchResult(mutation, mutation.response_json)
            put_labels.append(str((mutation.request_json or {}).get("label") or ""))
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "hubspot",
                "label": (mutation.request_json or {}).get("label"),
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page_a = self.client.get(reverse("importer:crm_setup_hubspot"))
            token_a = self._extract_form_token(page_a.content.decode("utf-8"))
            page_b = self.client.get(reverse("importer:crm_setup_hubspot"))
            token_b = self._extract_form_token(page_b.content.decode("utf-8"))
            self.assertNotEqual(token_a, token_b)

            first = self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": token_a,
                    "label": "First save wins",
                    "auth_mode": "private_app",
                    "expected_hub_id": "111",
                    "access_token": "token-first",
                },
            )
            self.assertEqual(first.status_code, 302)
            self.assertEqual(
                first["Location"], reverse("importer:crm_setup_hubspot_connect")
            )

            stale = self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": token_b,
                    "label": "Stale overwrite",
                    "auth_mode": "private_app",
                    "expected_hub_id": "222",
                    "access_token": "token-stale",
                },
            )
            self.assertEqual(stale.status_code, 302)
            # Stale form is rejected back to credentials (edit path ok).
            self.assertEqual(
                stale["Location"], reverse("importer:crm_setup_hubspot")
            )

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        puts = list(
            setup.api_mutations.filter(
                mutation_kind="crm_app_registration_hubspot_put"
            ).order_by("logical_action_generation", "created_at")
        )
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0].logical_action_generation, 0)
        self.assertEqual(puts[0].request_json.get("label"), "First save wins")
        self.assertEqual(put_labels, ["First save wins"])
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("label"), "First save wins")
        self.assertNotEqual(draft.get("label"), "Stale overwrite")

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_edit_application_breaks_registration_ready_redirect(self):
        """Connect → Edit application must render credentials, not loop."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_app_registration_hubspot_put":
                raise AssertionError(mutation.mutation_kind)
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {"provider_key": "hubspot", "label": "Work"}
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            token = self._extract_form_token(page.content.decode("utf-8"))
            self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": token,
                    "label": "Work",
                    "auth_mode": "private_app",
                    "expected_hub_id": "99",
                    "access_token": "tok",
                },
            )
            connect = self.client.get(reverse("importer:crm_setup_hubspot_connect"))
            self.assertEqual(connect.status_code, 200)
            connect_body = connect.content.decode("utf-8")
            edit_href = reverse("importer:crm_setup_hubspot") + "?edit=1"
            self.assertIn(edit_href, connect_body)

            # Without edit flag, credentials route continues to connect.
            bounce = self.client.get(reverse("importer:crm_setup_hubspot"))
            self.assertEqual(bounce.status_code, 302)
            self.assertEqual(
                bounce["Location"], reverse("importer:crm_setup_hubspot_connect")
            )

            # With edit=1, operator can correct credentials.
            edit = self.client.get(edit_href)
            self.assertEqual(edit.status_code, 200)
            edit_body = edit.content.decode("utf-8")
            self.assertIn("Edit HubSpot application", edit_body)
            self.assertIn('name="access_token"', edit_body)
            self.assertIn("Work", edit_body)
            # Fresh token is issued at next generation after terminal put.
            setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
            edit_token = self._extract_form_token(edit_body)
            claims = decode_form_token(
                edit_token,
                owner_id=owner,
                session=setup,
                action_kind="crm_setup_hubspot_save",
            )
            self.assertEqual(claims["logical_action_generation"], 1)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_hubspot_oauth_connect_redirects_and_callback_returns_to_wizard(self):
        """OAuth mode connect issues external redirect; callback finishes wizard."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        connection_id = "crm_conn_hs_oauth_wizard"
        auth_url = "https://app.hubspot.test/oauth/authorize?client=xyz"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_app_registration_hubspot_put":
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = {
                    "provider_key": "hubspot",
                    "auth_mode": "oauth",
                }
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, mutation.response_json)
            if mutation.mutation_kind == "crm_connection_start":
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                body = {
                    "connection_id": connection_id,
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": auth_url,
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                if mutation.state == ApiMutation.State.COMPLETED:
                    return MutationDispatchResult(mutation, mutation.response_json)
                body = {
                    "connection_id": connection_id,
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "connected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(mutation.mutation_kind)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self._choose_hubspot()
            page = self.client.get(reverse("importer:crm_setup_hubspot"))
            save_token = self._extract_form_token(page.content.decode("utf-8"))
            self.client.post(
                reverse("importer:crm_setup_hubspot_save"),
                data={
                    "form_token": save_token,
                    "label": "OAuth portal",
                    "auth_mode": "oauth",
                    "client_id": "hs-client",
                    "client_secret": "hs-secret",
                    "expected_hub_id": "",
                },
            )
            connect_page = self.client.get(
                reverse("importer:crm_setup_hubspot_connect")
            )
            connect_token = self._extract_form_token(
                connect_page.content.decode("utf-8")
            )
            redirected = self.client.post(
                reverse("importer:crm_setup_hubspot_connect_submit"),
                data={"form_token": connect_token},
            )
            self.assertEqual(redirected.status_code, 302)
            self.assertEqual(redirected["Location"], auth_url)
            sess = self.client.session
            self.assertEqual(sess.get("crm_setup_oauth_return"), "hubspot")
            pending = sess.get("crm_oauth_pending") or {}
            self.assertEqual(pending.get("connection_id"), connection_id)
            self.assertEqual(pending.get("provider_key"), "hubspot")
            self.assertEqual(pending.get("state"), f"state-{connection_id}")

            # Provider redirects back with code; wizard resumes to complete.
            callback = self.client.get(
                reverse("importer:crm_oauth_callback"),
                data={
                    "code": "one-time-oauth-code",
                    "state": f"state-{connection_id}",
                },
            )
            self.assertEqual(callback.status_code, 302)
            self.assertEqual(
                callback["Location"], reverse("importer:crm_setup_complete")
            )
            complete = self.client.get(reverse("importer:crm_setup_complete"))
            self.assertEqual(complete.status_code, 200)
            body = complete.content.decode("utf-8")
            self.assertIn("HubSpot", body)
            self.assertIn(connection_id, body)
            self.assertNotIn("one-time-oauth-code", body)
            self.assertNotIn("hs-secret", body)

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        draft = (setup.options or {}).get("crm_setup") or {}
        self.assertEqual(draft.get("outcome"), "connected")
        self.assertEqual(draft.get("connection_id"), connection_id)
        self.assertIsNone(self.client.session.get("crm_setup_oauth_return"))


class CrmConnectionSetupUxPhase1Tests(TestCase):
    """Phase 1: Home Connect CTA + Connect CRM hub IA (network-free)."""

    def test_landing_body_cta_order_and_connect_hub_link(self):
        response = self.client.get(reverse("importer:landing"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Start an import", body)
        self.assertIn("Connect CRM", body)
        self.assertIn("Resolve CRM duplicates", body)
        connect_href = reverse("importer:crm_connections")
        resolve_href = reverse("importer:crm_duplicate_journey")
        self.assertIn(connect_href, body)
        self.assertIn(resolve_href, body)
        # D2: body CTA order is Start → Connect CRM → Resolve (home-actions only).
        actions_start = body.find('class="home-actions"')
        self.assertNotEqual(actions_start, -1)
        actions_end = body.find("</div>", actions_start)
        actions = body[actions_start:actions_end if actions_end != -1 else None]
        start_idx = actions.find("Start an import")
        connect_href_idx = actions.find(connect_href)
        connect_label_idx = actions.find("Connect CRM")
        resolve_idx = actions.find("Resolve CRM duplicates")
        self.assertNotEqual(start_idx, -1)
        self.assertNotEqual(connect_href_idx, -1)
        self.assertNotEqual(connect_label_idx, -1)
        self.assertNotEqual(resolve_idx, -1)
        self.assertLess(start_idx, connect_href_idx)
        self.assertLess(connect_href_idx, resolve_idx)
        self.assertLess(connect_label_idx, resolve_idx)

    def test_connect_hub_empty_state_and_setup_deep_link(self):
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Connect CRM", body)
        self.assertIn("Set up new CRM connection", body)
        self.assertIn("Your connections", body)
        self.assertIn("No CRM connections yet", body)
        self.assertIn("Practice CRM", body)
        self.assertIn("Connect practice", body)
        self.assertIn(reverse("importer:crm_setup_start"), body)
        self.assertIn(reverse("importer:local_settings"), body)
        self.assertIn("Device storage &amp; advanced", body)
        self.assertNotIn("access_token", body)
        self.assertNotIn("client_secret", body)

    def test_connect_hub_no_providers_guides_to_setup(self):
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("No connectable CRM providers", body)
        self.assertIn("Set up new CRM connection", body)
        self.assertIn(reverse("importer:crm_setup_start"), body)

    def test_connect_hub_shows_disconnect_for_active_connection(self):
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={
                    "connections": [
                        {
                            "connection_id": "crm_conn_phase1",
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                            "status": "connected",
                            "display_label": "Demo org",
                        }
                    ]
                },
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Practice CRM", body)
        self.assertIn("Demo org", body)
        self.assertIn("connected", body)
        self.assertIn("Disconnect", body)
        self.assertIn("already connected", body)
        disconnect_path = reverse(
            "importer:crm_disconnect",
            kwargs={"connection_id": "crm_conn_phase1"},
        )
        self.assertIn(disconnect_path, body)

    def test_connect_hub_shows_reconnect_for_disconnected(self):
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={
                    "connections": [
                        {
                            "connection_id": "crm_conn_old",
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                            "status": "disconnected",
                            "display_label": "Prior org",
                        }
                    ]
                },
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Reconnect", body)
        self.assertIn(
            reverse(
                "importer:crm_reconnect",
                kwargs={"connection_id": "crm_conn_old"},
            ),
            body,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_fake_connect_and_disconnect_still_work_from_hub(self):
        """Acceptance: hub redesign must not break journaled fake connect path."""
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        connection_id = "crm_conn_phase1_accept"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": "https://example.test/oauth",
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "connected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_disconnect":
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "disconnected",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "disabled",
                        "duplicate_execution": "disabled",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            hub = self.client.get(reverse("importer:crm_connections"))
            self.assertEqual(hub.status_code, 200)
            self.assertContains(hub, "Connect practice")

            connected = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "fake"},
            )
            self.assertEqual(connected.status_code, 302)
            self.assertEqual(
                connected["Location"], reverse("importer:crm_connections")
            )

            disconnected = self.client.post(
                reverse(
                    "importer:crm_disconnect",
                    kwargs={"connection_id": connection_id},
                )
            )
            self.assertEqual(disconnected.status_code, 302)

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        self.assertTrue(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_start", state=ApiMutation.State.COMPLETED
            ).exists()
        )
        self.assertTrue(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_oauth_complete",
                state=ApiMutation.State.COMPLETED,
            ).exists()
        )
        self.assertTrue(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_disconnect",
                state=ApiMutation.State.COMPLETED,
            ).exists()
        )

    def test_connect_hub_surfaces_rejected_oauth_complete_error(self):
        """Regression: a rejected oauth/complete must not report false success.

        _connect_provider_result() must check the dispatched mutation's
        persisted state rather than assuming "ok" whenever the response body
        happens to lack a connection_id. EasyImportsApiClient.dispatch()
        treats a deterministic rejection as terminal-but-non-exceptional (it
        does not raise), so this is the one place that must check it.
        """

        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        connection_id = "crm_conn_phase1_rejected"
        rejection_message = (
            "This CRM account is already connected in this EasyImports "
            "install under another browser session. Disconnect that "
            "connection before reconnecting it."
        )

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = {
                    "connection_id": connection_id,
                    "provider_key": "fake",
                    "provider_label": "Practice CRM",
                    "status": "pending",
                    "supported_entity_families": ["company", "person"],
                    "supported_source_modes": ["candidate_upload", "acquire_all"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": None,
                        "state": f"state-{connection_id}",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                body = {
                    "error": {
                        "code": "invalid_crm_connection",
                        "message": rejection_message,
                        "details": None,
                    }
                }
                mutation.state = ApiMutation.State.REJECTED
                mutation.response_json = body
                mutation.http_status = 422
                mutation.error_code = "invalid_crm_connection"
                mutation.error_message = rejection_message
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "error_code",
                        "error_message",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            connected = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "fake"},
                follow=True,
            )
            self.assertEqual(connected.status_code, 200)
            self.assertContains(connected, rejection_message)
            self.assertNotContains(connected, "CRM connection established.")

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        self.assertTrue(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_oauth_complete",
                state=ApiMutation.State.REJECTED,
            ).exists()
        )

    def test_connect_hub_surfaces_rejected_oauth_complete_on_reconciliation_get(
        self,
    ):
        """Phase 0: first GET after an async-rejected complete must flash.

        The synchronous POST path is covered above. After Prefer:respond-async
        the first complete response is 202; the operator next sees a plain
        Connect CRM GET that reconciles GET /v1/mutations/{key}. That GET
        must show the real rejection, name the blocking connection, and
        must not render Reclaim (handle is not available on this path).
        Later GETs must not repeat the flash.
        """

        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        owner_session = f"django-{owner}"
        connection_id = "crm_conn_async_rejected"
        rejection_message = (
            "This CRM account is already connected in this EasyImports "
            "install under another browser session. Disconnect that "
            "connection before reconnecting it."
        )
        blocking_label = "HubSpot (STANDARD)"
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
            target_provider_id="",
        )
        mutation = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=f"crm-complete:{connection_id}:{owner_session}",
            request_json={
                "authorization_code": "already-consumed",
                "state": "state-async",
                "owner_session": owner_session,
            },
        )
        mutation.state = ApiMutation.State.PENDING
        mutation.http_status = 202
        mutation.save(update_fields=["state", "http_status", "updated_at"])

        envelope = {
            "error": {
                "code": "invalid_crm_connection",
                "message": rejection_message,
                "details": {
                    "conflict": {
                        "reason": "tenant_already_connected_other_session",
                        "reclaim_offer_id": "reclaim_offer_async",
                        "existing_connection_id": "crm_conn_existing_other",
                        "provider_key": "hubspot",
                        "display_label": blocking_label,
                        "expires_at": "2026-08-13T12:00:00Z",
                    }
                },
            }
        }

        def mutation_status(_idempotency_key, **_kwargs):
            return {
                "mutation_id": "mutation-async-reject",
                "mutation_kind": "crm_connection_oauth_complete",
                "status": "completed",
                "http_status": 422,
                "response": envelope,
                "retryable": False,
                "retry_after_seconds": 2,
            }

        hub_kwargs = {
            "crm_providers": {
                "providers": [
                    {"provider_key": "hubspot", "provider_label": "HubSpot"}
                ]
            },
            "crm_connections": {"connections": []},
            "crm_app_registrations": {"registrations": []},
        }

        with (
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                side_effect=mutation_status,
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value=hub_kwargs["crm_providers"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=hub_kwargs["crm_connections"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value=hub_kwargs["crm_app_registrations"],
            ),
        ):
            first = self.client.get(reverse("importer:crm_connections"))

        self.assertEqual(first.status_code, 200)
        self.assertContains(first, rejection_message)
        self.assertContains(first, f"The blocking connection is {blocking_label}.")
        self.assertNotContains(first, "CRM connection established.")
        self.assertNotContains(first, "Reclaim this connection on this browser")
        self.assertContains(first, "No CRM connections yet")

        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.REJECTED)
        self.assertEqual(mutation.error_message, rejection_message)

        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value=hub_kwargs["crm_providers"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=hub_kwargs["crm_connections"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value=hub_kwargs["crm_app_registrations"],
            ),
        ):
            second = self.client.get(reverse("importer:crm_connections"))

        self.assertEqual(second.status_code, 200)
        self.assertNotContains(second, rejection_message)
        self.assertNotContains(second, "The blocking connection is")
        self.assertNotContains(second, "Reclaim this connection on this browser")
        self.assertContains(second, "No CRM connections yet")


class CrmConnectionReclaimPhase2Tests(TestCase):
    """Phase 2: reclaim surface and mutation-state honesty (network-free)."""

    def test_rejected_complete_renders_reclaim_form_not_success(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        existing_id = "crm_conn_existing_e"
        rejection_message = (
            "This CRM account is already connected in this EasyImports "
            "install under another browser session. Disconnect that "
            "connection before reconnecting it."
        )
        handle = "live-reclaim-handle-once"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = {
                    "connection_id": "crm_conn_discarded_start",
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "pending",
                    "supported_entity_families": ["company"],
                    "supported_source_modes": ["acquire_all"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": None,
                        "state": "state-discarded",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=["state", "response_json", "http_status", "updated_at"]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                live = {
                    "error": {
                        "code": "invalid_crm_connection",
                        "message": rejection_message,
                        "details": {
                            "conflict": {
                                "reason": "tenant_already_connected_other_session",
                                "reclaim_handle": handle,
                                "reclaim_offer_id": "reclaim_offer_1",
                                "existing_connection_id": existing_id,
                                "provider_key": "hubspot",
                                "display_label": "Portal",
                                "expires_at": "2026-08-12T12:00:00Z",
                            }
                        },
                    }
                }
                from importer.api_client import scrub_reclaim_handle_from_envelope

                mutation.state = ApiMutation.State.REJECTED
                mutation.response_json = scrub_reclaim_handle_from_envelope(live)
                mutation.http_status = 422
                mutation.error_code = "invalid_crm_connection"
                mutation.error_message = rejection_message
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "error_code",
                        "error_message",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, live)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "hubspot", "provider_label": "HubSpot"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            connected = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "hubspot"},
            )
            self.assertEqual(connected.status_code, 200)
            self.assertContains(connected, rejection_message)
            self.assertContains(connected, "Reclaim this connection on this browser")
            self.assertContains(connected, handle)
            self.assertNotContains(connected, "CRM connection established.")
            self.assertIn(
                "no-store",
                (connected.headers.get("Cache-Control") or "").lower(),
            )

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.REJECTED)
        self.assertNotIn("reclaim_handle", json.dumps(complete.response_json))

    def test_reclaim_post_checks_mutation_state_and_journals_digest(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_existing_e"
        handle = "form-scoped-handle"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_connection_reclaim":
                raise AssertionError(mutation.mutation_kind)
            body = {
                "connection_id": connection_id,
                "provider_key": "hubspot",
                "provider_label": "HubSpot",
                "status": "connected",
                "display_label": "Portal",
                "supported_entity_families": ["company"],
                "supported_source_modes": ["acquire_all"],
                "capabilities": {},
                "capability_profile_digest": "digest",
                "maximum_authorization": {
                    "reference_acquisition": "execute",
                    "duplicate_execution": "execute",
                },
                "last_reclaim": {
                    "reclaimed_at": "2026-08-12T12:00:00Z",
                    "reclaim_offer_id": "reclaim_offer_1",
                    "reclaim_mutation_id": "mutation_abc",
                },
            }
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.http_status = 200
            mutation.save(
                update_fields=["state", "response_json", "http_status", "updated_at"]
            )
            return MutationDispatchResult(mutation, body)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            response = self.client.post(
                reverse("importer:crm_reclaim", args=[connection_id]),
                data={
                    "reclaim_handle": handle,
                    "reclaim_offer_id": "reclaim_offer_1",
                },
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "CRM connection reclaimed on this browser.")
            self.assertNotContains(response, "CRM connection established.")

        journal = ImportSession.objects.get(
            owner_id=owner, product_key="crm.connection"
        )
        mutation = journal.api_mutations.get(mutation_kind="crm_connection_reclaim")
        self.assertNotIn("reclaim_handle", mutation.request_json)
        self.assertIn("reclaim_handle_digest", mutation.request_json)
        self.assertEqual(mutation.request_json["reclaim_offer_id"], "reclaim_offer_1")

    def test_reclaim_post_rejected_does_not_show_success(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        def dispatch(mutation, **kwargs):
            body = {
                "error": {
                    "code": "invalid_crm_connection",
                    "message": "Reclaim handle is expired.",
                }
            }
            mutation.state = ApiMutation.State.REJECTED
            mutation.response_json = body
            mutation.http_status = 422
            mutation.error_code = "invalid_crm_connection"
            mutation.error_message = "Reclaim handle is expired."
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, body)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            response = self.client.post(
                reverse("importer:crm_reclaim", args=["crm_conn_existing_e"]),
                data={
                    "reclaim_handle": "stale-handle",
                    "reclaim_offer_id": "reclaim_offer_1",
                },
                follow=True,
            )
            self.assertContains(response, "Reclaim handle is expired.")
            self.assertNotContains(response, "CRM connection reclaimed")
            self.assertNotContains(response, "CRM connection established.")

    def test_unknown_reclaim_renders_exact_retry_form(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        handle = "unknown-retry-handle"

        def dispatch(mutation, **kwargs):
            mutation.state = ApiMutation.State.UNKNOWN
            mutation.error_message = (
                "The API response is uncertain. Retry this exact action."
            )
            mutation.save(update_fields=["state", "error_message", "updated_at"])
            raise ApiUnavailableError(
                "The API response is uncertain. Retry this exact action."
            )

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            response = self.client.post(
                reverse("importer:crm_reclaim", args=["crm_conn_existing_e"]),
                data={
                    "reclaim_handle": handle,
                    "reclaim_offer_id": "reclaim_offer_1",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Reclaim this connection on this browser")
            self.assertContains(response, handle)
            self.assertContains(response, 'name="explicit_retry"')
            self.assertNotContains(response, "CRM connection reclaimed")
            self.assertIn(
                "no-store",
                (response.headers.get("Cache-Control") or "").lower(),
            )

    def test_hubspot_wizard_surfaces_reclaim_on_private_app_conflict(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        existing_id = "crm_conn_existing_e"
        handle = "wizard-live-handle"
        rejection_message = (
            "This CRM account is already connected in this EasyImports "
            "install under another browser session. Disconnect that "
            "connection before reconnecting it."
        )
        ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM setup wizard",
            product_key="crm.setup",
            target_provider_id="",
            options={
                "crm_setup": {
                    "step": "connect",
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "outcome": "registration_ready",
                    "auth_mode": "private_app",
                }
            },
        )

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = {
                    "connection_id": "crm_conn_discarded_start",
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "pending",
                    "supported_entity_families": ["company"],
                    "supported_source_modes": ["acquire_all"],
                    "capabilities": {},
                    "authorization": {
                        "mode": "oauth_code",
                        "authorization_url": None,
                        "state": "state-discarded",
                    },
                    "capability_profile_digest": "digest",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=["state", "response_json", "http_status", "updated_at"]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                live = {
                    "error": {
                        "code": "invalid_crm_connection",
                        "message": rejection_message,
                        "details": {
                            "conflict": {
                                "reason": "tenant_already_connected_other_session",
                                "reclaim_handle": handle,
                                "reclaim_offer_id": "reclaim_offer_1",
                                "existing_connection_id": existing_id,
                                "provider_key": "hubspot",
                                "display_label": "Portal",
                                "expires_at": "2026-08-12T12:00:00Z",
                            }
                        },
                    }
                }
                from importer.api_client import scrub_reclaim_handle_from_envelope

                mutation.state = ApiMutation.State.REJECTED
                mutation.response_json = scrub_reclaim_handle_from_envelope(live)
                mutation.http_status = 422
                mutation.error_code = "invalid_crm_connection"
                mutation.error_message = rejection_message
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "error_code",
                        "error_message",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, live)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        connect_page = self.client.get(reverse("importer:crm_setup_hubspot_connect"))
        self.assertEqual(connect_page.status_code, 200)
        html = connect_page.content.decode("utf-8")
        marker = 'name="form_token" value="'
        start = html.find(marker) + len(marker)
        token = html[start : html.find('"', start)]

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            response = self.client.post(
                reverse("importer:crm_setup_hubspot_connect_submit"),
                data={"form_token": token},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, rejection_message)
        self.assertContains(response, "Reclaim this connection on this browser")
        self.assertContains(response, handle)
        self.assertNotContains(response, "CRM connection established.")
        self.assertIn(
            "no-store",
            (response.headers.get("Cache-Control") or "").lower(),
        )


class CrmConnectionReclaimAsyncDispatchPhase1Tests(TestCase):
    """Phase 1: reclaim after async oauth/complete 202 → status poll."""

    def test_async_rejected_complete_reconcile_renders_reclaim_end_to_end(
        self,
    ):
        """Defining regression: 202 complete still allows reclaim on GET."""

        from .api_contract_generated import API_VERSION

        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        owner_session = f"django-{owner}"
        connection_id = "crm_conn_async_reclaim"
        existing_id = "crm_conn_existing_other"
        handle = "status-poll-reclaim-handle"
        rejection_message = (
            "This CRM account is already connected in this EasyImports "
            "install under another browser session. Disconnect that "
            "connection before reconnecting it."
        )
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
            target_provider_id="",
        )
        mutation = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=f"crm-complete:{connection_id}:{owner_session}",
            request_json={
                "authorization_code": "already-consumed",
                "state": "state-async",
                "owner_session": owner_session,
            },
        )
        mutation.state = ApiMutation.State.PENDING
        mutation.http_status = 202
        mutation.save(update_fields=["state", "http_status", "updated_at"])

        live = {
            "error": {
                "code": "invalid_crm_connection",
                "message": rejection_message,
                "details": {
                    "conflict": {
                        "reason": "tenant_already_connected_other_session",
                        "reclaim_handle": handle,
                        "reclaim_offer_id": "reclaim_offer_async",
                        "existing_connection_id": existing_id,
                        "provider_key": "hubspot",
                        "display_label": "HubSpot (STANDARD)",
                        "expires_at": "2026-08-13T12:00:00Z",
                    }
                },
            }
        }
        seen_owners: list[str | None] = []

        def mutation_status(_idempotency_key, **kwargs):
            seen_owners.append(kwargs.get("owner_session"))
            return {
                "mutation_id": "mutation-async-reclaim",
                "mutation_kind": "crm_connection_oauth_complete",
                "status": "completed",
                "http_status": 422,
                "response": live,
                "retryable": False,
                "retry_after_seconds": 2,
            }

        def reclaim_dispatch(posted, **kwargs):
            if posted.mutation_kind != "crm_connection_reclaim":
                raise AssertionError(posted.mutation_kind)
            body = {
                "connection_id": existing_id,
                "provider_key": "hubspot",
                "provider_label": "HubSpot",
                "status": "connected",
                "display_label": "HubSpot (STANDARD)",
                "supported_entity_families": ["company"],
                "supported_source_modes": ["acquire_all"],
                "capabilities": {},
                "capability_profile_digest": "digest",
                "maximum_authorization": {
                    "reference_acquisition": "execute",
                    "duplicate_execution": "execute",
                },
                "last_reclaim": {
                    "reclaimed_at": "2026-08-13T12:00:00Z",
                    "reclaim_offer_id": "reclaim_offer_async",
                    "reclaim_mutation_id": "mutation_async_reclaim",
                },
            }
            posted.state = ApiMutation.State.COMPLETED
            posted.response_json = body
            posted.http_status = 200
            posted.save(
                update_fields=["state", "response_json", "http_status", "updated_at"]
            )
            return MutationDispatchResult(posted, body)

        hub_kwargs = {
            "crm_providers": {
                "providers": [
                    {"provider_key": "hubspot", "provider_label": "HubSpot"}
                ]
            },
            "crm_connections": {"connections": []},
            "crm_app_registrations": {"registrations": []},
        }

        with (
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                side_effect=mutation_status,
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value=hub_kwargs["crm_providers"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=hub_kwargs["crm_connections"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value=hub_kwargs["crm_app_registrations"],
            ),
        ):
            first = self.client.get(reverse("importer:crm_connections"))

        self.assertEqual(first.status_code, 200)
        self.assertContains(first, rejection_message)
        self.assertContains(first, "Reclaim this connection on this browser")
        self.assertContains(first, handle)
        self.assertNotContains(first, "CRM connection established.")
        self.assertIn("no-store", (first.headers.get("Cache-Control") or "").lower())
        self.assertEqual(seen_owners, [owner_session])

        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.REJECTED)
        self.assertNotIn("reclaim_handle", json.dumps(mutation.response_json))

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": API_VERSION},
            ),
            patch.object(
                EasyImportsApiClient, "dispatch", side_effect=reclaim_dispatch
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value=hub_kwargs["crm_providers"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=hub_kwargs["crm_connections"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value=hub_kwargs["crm_app_registrations"],
            ),
        ):
            redeemed = self.client.post(
                reverse("importer:crm_reclaim", args=[existing_id]),
                data={
                    "reclaim_handle": handle,
                    "reclaim_offer_id": "reclaim_offer_async",
                },
                follow=True,
            )
        self.assertEqual(redeemed.status_code, 200)
        self.assertContains(redeemed, "CRM connection reclaimed on this browser.")

        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value=hub_kwargs["crm_providers"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=hub_kwargs["crm_connections"],
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value=hub_kwargs["crm_app_registrations"],
            ),
        ):
            later = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(later.status_code, 200)
        self.assertNotContains(later, "Reclaim this connection on this browser")
        self.assertNotContains(later, handle)

    def test_reconcile_without_handle_does_not_render_reclaim(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        owner_session = f"django-{owner}"
        connection_id = "crm_conn_async_lost"
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
            target_provider_id="",
        )
        mutation = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=f"crm-complete:{connection_id}:{owner_session}",
            request_json={
                "authorization_code": "already-consumed",
                "state": "state-lost",
                "owner_session": owner_session,
            },
        )
        mutation.state = ApiMutation.State.PENDING
        mutation.http_status = 202
        mutation.save(update_fields=["state", "http_status", "updated_at"])
        envelope = {
            "error": {
                "code": "invalid_crm_connection",
                "message": "This CRM account is already connected.",
                "details": {
                    "conflict": {
                        "reason": "tenant_already_connected_other_session",
                        "reclaim_offer_id": "reclaim_offer_lost",
                        "existing_connection_id": "crm_conn_existing_other",
                        "provider_key": "hubspot",
                        "display_label": "HubSpot (STANDARD)",
                        "expires_at": "2026-08-13T12:00:00Z",
                    }
                },
            }
        }

        def mutation_status(_idempotency_key, **_kwargs):
            return {
                "mutation_id": "mutation-lost-handle",
                "mutation_kind": "crm_connection_oauth_complete",
                "status": "completed",
                "http_status": 422,
                "response": envelope,
                "retryable": False,
                "retry_after_seconds": 2,
            }

        with (
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                side_effect=mutation_status,
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={"registrations": []},
            ),
        ):
            page = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "This CRM account is already connected.")
        self.assertNotContains(page, "Reclaim this connection on this browser")


class CrmConnectionSetupUxPhase4Tests(TestCase):
    """Phase 4: device reuse polish + Settings demotion (network-free)."""

    def test_settings_demoted_registration_not_primary(self):
        """Settings is device storage; CRM registration forms sit under advanced."""
        with (
            patch.object(
                EasyImportsApiClient,
                "local_storage",
                return_value={
                    "data_root": "C:\\Users\\me\\AppData\\Local\\EasyImports\\data",
                    "bootstrap_path": "C:\\Users\\me\\AppData\\Local\\EasyImports\\bootstrap.json",
                    "is_default_data_root": True,
                    "can_change_location": True,
                    "change_blocked_reason": "",
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={"registrations": []},
            ),
        ):
            response = self.client.get(reverse("importer:local_settings"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Device storage &amp; advanced", body)
        self.assertIn("Open Connect CRM", body)
        self.assertIn(reverse("importer:crm_connections"), body)
        self.assertIn(reverse("importer:crm_setup_start"), body)
        # Primary path called out; registration forms demoted.
        self.assertIn("Advanced CRM app registration (ops)", body)
        self.assertIn("<details", body)
        self.assertIn("guided wizard", body.lower())
        # Storage still first-class.
        self.assertIn("Storage", body)
        self.assertIn("data root", body.lower())
        # Advanced forms may include empty password inputs named access_token;
        # no secret *values* are prefilled on GET.
        self.assertNotIn('value="pat-', body)
        self.assertNotIn('value="sk_', body)
        # Nav label demoted from plain Settings (base template).
        self.assertIn("Device storage", body)

    def test_connect_hub_ready_state_and_manage_app_for_registered(self):
        """Registered SF/HS: device-reuse copy + Connect + Manage app (D5)."""
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"},
                        {
                            "provider_key": "salesforce",
                            "provider_label": "Salesforce",
                        },
                        {"provider_key": "hubspot", "provider_label": "HubSpot"},
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={
                    "registrations": [
                        {"provider_key": "salesforce", "label": "SF app"},
                        {"provider_key": "hubspot", "label": "HS app"},
                    ]
                },
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Ready on this device", body)
        self.assertIn("without pasting secrets again", body)
        self.assertIn("app registered on this device", body)
        self.assertIn("connect without re-entering secrets", body)
        self.assertIn("Manage app", body)
        self.assertIn(
            reverse("importer:crm_setup_salesforce") + "?edit=1", body
        )
        self.assertIn(reverse("importer:crm_setup_hubspot") + "?edit=1", body)
        self.assertIn("Connect practice", body)
        # Practice has no Manage app URL for fake.
        self.assertIn("Practice — no app registration", body)
        self.assertIn("Device storage &amp; advanced", body)
        self.assertNotIn("HubSpot guided steps land next", body)
        self.assertNotIn("access_token", body)
        self.assertNotIn("client_secret", body)

    def test_connect_hub_prebuilt_provider_not_labeled_registered(self):
        """Catalog-only SF/HS (no registration inventory) must not claim device reg."""
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {
                            "provider_key": "salesforce",
                            "provider_label": "Salesforce",
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={"registrations": []},
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Salesforce", body)
        self.assertIn("Connect", body)
        self.assertNotIn("Ready on this device", body)
        self.assertNotIn("app registered on this device", body)
        self.assertNotIn("Manage app", body)
        self.assertNotIn(
            reverse("importer:crm_setup_salesforce") + "?edit=1", body
        )

    def test_connect_hub_reconnect_and_manage_for_disconnected_salesforce(self):
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {
                            "provider_key": "salesforce",
                            "provider_label": "Salesforce",
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={
                    "connections": [
                        {
                            "connection_id": "crm_conn_sf_disc",
                            "provider_key": "salesforce",
                            "provider_label": "Salesforce",
                            "status": "disconnected",
                            "display_label": "Sandbox",
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={
                    "registrations": [
                        {"provider_key": "salesforce", "label": "SF"}
                    ]
                },
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Reconnect", body)
        self.assertIn("Manage app", body)
        self.assertIn(
            reverse("importer:crm_setup_salesforce") + "?edit=1", body
        )

    def test_salesforce_edit_application_from_connect_path(self):
        """Phase 4 Manage app / Edit Connected App uses ?edit=1."""
        self.client.get(reverse("importer:crm_setup_start"))
        self.client.get(reverse("importer:crm_setup_pick"))
        # Draft only; no registration_ready yet — edit=1 still shows form.
        page = self.client.get(
            reverse("importer:crm_setup_salesforce") + "?edit=1"
        )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        # ?edit=1 always enters edit framing (Manage app path).
        self.assertIn("Edit Salesforce Connected App", body)
        self.assertIn('name="client_secret"', body)

    def test_manage_app_works_after_connected_complete_draft(self):
        """?edit=1 must not bounce to success after guided setup completes."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        self.client.get(reverse("importer:crm_setup_start"))
        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        setup.options = {
            "crm_setup": {
                "step": "complete",
                "provider_key": "salesforce",
                "provider_label": "Salesforce",
                "connection_id": "crm_conn_done",
                "outcome": "connected",
                "label": "Done app",
            }
        }
        setup.save(update_fields=["options", "updated_at"])

        bounced = self.client.get(reverse("importer:crm_setup_salesforce"))
        self.assertEqual(bounced.status_code, 302)
        self.assertEqual(
            bounced["Location"], reverse("importer:crm_setup_complete")
        )

        edit = self.client.get(
            reverse("importer:crm_setup_salesforce") + "?edit=1"
        )
        self.assertEqual(edit.status_code, 200)
        body = edit.content.decode("utf-8")
        self.assertIn("Edit Salesforce Connected App", body)
        self.assertIn('name="client_secret"', body)
        self.assertNotIn("crm_conn_done", body)  # not the complete page

        # HubSpot same rule.
        setup.options = {
            "crm_setup": {
                "step": "complete",
                "provider_key": "hubspot",
                "provider_label": "HubSpot",
                "connection_id": "crm_conn_hs_done",
                "outcome": "connected",
                "label": "HS done",
                "auth_mode": "private_app",
            }
        }
        setup.save(update_fields=["options", "updated_at"])
        hs_edit = self.client.get(
            reverse("importer:crm_setup_hubspot") + "?edit=1"
        )
        self.assertEqual(hs_edit.status_code, 200)
        hs_body = hs_edit.content.decode("utf-8")
        self.assertIn("Edit HubSpot application", hs_body)
        self.assertIn('name="access_token"', hs_body)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_salesforce_stale_form_cannot_overwrite_newer_registration(self):
        """Signed generation bind: dual Manage-app forms cannot advance past claim."""
        owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(owner)
        browser.save()
        put_labels: list[str] = []

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind != "crm_app_registration_salesforce_put":
                raise AssertionError(mutation.mutation_kind)
            if mutation.state == ApiMutation.State.COMPLETED:
                return MutationDispatchResult(mutation, mutation.response_json)
            put_labels.append(str((mutation.request_json or {}).get("label") or ""))
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "provider_key": "salesforce",
                "label": (mutation.request_json or {}).get("label"),
            }
            mutation.http_status = 201
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            self.client.get(reverse("importer:crm_setup_start"))
            page_a = self.client.get(reverse("importer:crm_setup_salesforce"))
            token_a = page_a.content.decode("utf-8")
            marker = 'name="form_token" value="'
            start = token_a.find(marker) + len(marker)
            end = token_a.find('"', start)
            form_a = token_a[start:end]
            page_b = self.client.get(reverse("importer:crm_setup_salesforce"))
            token_b = page_b.content.decode("utf-8")
            start = token_b.find(marker) + len(marker)
            end = token_b.find('"', start)
            form_b = token_b[start:end]

            first = self.client.post(
                reverse("importer:crm_setup_salesforce_save"),
                data={
                    "form_token": form_a,
                    "label": "First SF wins",
                    "login_environment": "sandbox",
                    "client_id": "cid-first",
                    "client_secret": "secret-first",
                },
            )
            self.assertEqual(first.status_code, 302)
            stale = self.client.post(
                reverse("importer:crm_setup_salesforce_save"),
                data={
                    "form_token": form_b,
                    "label": "Stale SF overwrite",
                    "login_environment": "sandbox",
                    "client_id": "cid-stale",
                    "client_secret": "secret-stale",
                },
            )
            self.assertEqual(stale.status_code, 302)
            self.assertEqual(
                stale["Location"], reverse("importer:crm_setup_salesforce")
            )

        setup = ImportSession.objects.get(owner_id=owner, product_key="crm.setup")
        puts = list(
            setup.api_mutations.filter(
                mutation_kind="crm_app_registration_salesforce_put"
            ).order_by("logical_action_generation", "created_at")
        )
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0].logical_action_generation, 0)
        self.assertEqual(puts[0].request_json.get("label"), "First SF wins")
        self.assertEqual(put_labels, ["First SF wins"])

    def test_wizard_pick_no_longer_mentions_hubspot_pending(self):
        self.client.get(reverse("importer:crm_setup_start"))
        pick = self.client.get(reverse("importer:crm_setup_pick"))
        self.assertEqual(pick.status_code, 200)
        body = pick.content.decode("utf-8")
        self.assertIn("Salesforce", body)
        self.assertIn("HubSpot", body)
        self.assertNotIn("land next", body.lower())
        self.assertIn("vaulted on this device", body.lower())


class CrmConnectionSetupUxPhase5ConnTests(TransactionTestCase):
    """Phase 5-CONN: HTTP browser → live Django → real API process.

    Spins **two** subprocesses (API + Django runserver). A cookie-bearing
    ``requests.Session`` drives navigation, CSRF, redirects, and forms — not
    Django's in-process test client. Network-free only (fake stack + synthetic
    vaulted HS private-app). Live SF/HS canaries remain out of scope.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory()
        root = Path(cls.state_dir.name)
        cls.api_state = root / "api_state"
        cls.django_db = root / "django.sqlite3"
        cls.api_state.mkdir(parents=True, exist_ok=True)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.api_port = sock.getsockname()[1]
        sock.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.django_port = sock.getsockname()[1]
        sock.close()

        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        cls.django_base = f"http://127.0.0.1:{cls.django_port}"
        cls._start_api()
        cls._start_django()

    @classmethod
    def _api_env(cls) -> dict:
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.api_port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = str(cls.api_state)
        # Durable vault so API restart proves device registration reuse.
        environment["EASYIMPORTS_SECRET_STORE"] = "file_insecure"
        environment["EASYIMPORTS_SF_OAUTH_EXCHANGE"] = "synthetic"
        environment["PYTHONPATH"] = str(cls.repo)
        return environment

    @classmethod
    def _django_env(cls) -> dict:
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "easyimports_web.settings"
        environment["EASYIMPORTS_API_BASE_URL"] = cls.api_base
        environment["EASYIMPORTS_DB_PATH"] = str(cls.django_db)
        environment["EASYIMPORTS_DEBUG"] = "1"
        environment["EASYIMPORTS_ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(cls.repo / "web"), str(cls.repo), environment.get("PYTHONPATH", "")]
        )
        return environment

    @classmethod
    def _start_api(cls) -> None:
        cls.api_process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health = f"{cls.api_base}/health"
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("Phase 5-CONN API process did not start.")

    @classmethod
    def _stop_api(cls) -> None:
        if getattr(cls, "api_process", None) is None:
            return
        cls.api_process.terminate()
        try:
            cls.api_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.api_process.kill()
            cls.api_process.wait(timeout=5)
        cls.api_process = None

    @classmethod
    def _restart_api(cls) -> None:
        """Restart API process against the same durable state root (device reuse)."""

        cls._stop_api()
        cls._start_api()

    @classmethod
    def _start_django(cls) -> None:
        env = cls._django_env()
        migrate = subprocess.run(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "migrate",
                "--run-syncdb",
                "--verbosity",
                "0",
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            text=True,
            capture_output=True,
        )
        if migrate.returncode != 0:
            raise RuntimeError(
                "Phase 5-CONN Django migrate failed:\n"
                f"stdout={migrate.stdout}\nstderr={migrate.stderr}"
            )
        cls.django_process = subprocess.Popen(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "runserver",
                f"127.0.0.1:{cls.django_port}",
                "--noreload",
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        landing = f"{cls.django_base}/"
        for _ in range(80):
            try:
                response = requests.get(landing, timeout=0.25)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.django_process.terminate()
        raise RuntimeError("Phase 5-CONN Django runserver did not start.")

    @classmethod
    def _stop_django(cls) -> None:
        if getattr(cls, "django_process", None) is None:
            return
        cls.django_process.terminate()
        try:
            cls.django_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.django_process.kill()
            cls.django_process.wait(timeout=5)
        cls.django_process = None

    @classmethod
    def tearDownClass(cls):
        cls._stop_django()
        cls._stop_api()
        cls.state_dir.cleanup()
        super().tearDownClass()

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "Phase5ConnBrowser/1.0"})
        return session

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.django_base + path

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "form_token missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def _extract_csrf(self, html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "csrfmiddlewaretoken missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=30, allow_redirects=True)
        self.assertIn(
            response.status_code,
            {200, 302},
            f"GET {path} -> {response.status_code}: {response.text[:300]}",
        )
        return response

    def _post(
        self,
        browser: requests.Session,
        path: str,
        data: dict,
        *,
        allow_redirects: bool = True,
    ) -> requests.Response:
        # Seed CSRF cookie if missing.
        if "csrftoken" not in browser.cookies:
            self._get(browser, path if path.endswith("/") else path.rsplit("/", 1)[0] + "/")
        payload = dict(data)
        if "csrfmiddlewaretoken" not in payload:
            # Prefer form CSRF from a prior GET of the same path when available.
            prior = browser.get(self._url(path), timeout=30, allow_redirects=True)
            if prior.status_code == 200 and "csrfmiddlewaretoken" in prior.text:
                payload["csrfmiddlewaretoken"] = self._extract_csrf(prior.text)
            elif "csrftoken" in browser.cookies:
                payload["csrfmiddlewaretoken"] = browser.cookies["csrftoken"]
        headers = {}
        if "csrftoken" in browser.cookies:
            headers["X-CSRFToken"] = browser.cookies["csrftoken"]
            headers["Referer"] = self._url(path)
        response = browser.post(
            self._url(path),
            data=payload,
            headers=headers,
            timeout=30,
            allow_redirects=allow_redirects,
        )
        return response

    def _assert_no_secrets(self, html: str) -> None:
        self.assertNotIn("pat-xyz-secret", html)
        self.assertNotIn("pat-token-value", html)
        lower = html.lower()
        # Field names may appear; forbid filled secret values from our fixtures.
        self.assertNotIn("super-secret-value", lower)

    def test_browser_practice_wizard_happy_path_and_disconnect(self):
        """Live Django + live API: Home → wizard Practice → complete → disconnect."""
        browser = self._browser()
        home = self._get(browser, "/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Connect CRM", home.text)
        self.assertIn("/crm/connections/", home.text)

        hub = self._get(browser, "/crm/connections/")
        self.assertEqual(hub.status_code, 200)
        self.assertIn("Set up new CRM connection", hub.text)
        self.assertIn("/crm/setup/", hub.text)
        self._assert_no_secrets(hub.text)

        start = browser.get(self._url("/crm/setup/"), timeout=30, allow_redirects=True)
        self.assertEqual(start.status_code, 200)
        self.assertIn("/crm/setup/pick/", start.url)
        pick_html = start.text
        choose_token = self._extract_form_token(pick_html)
        csrf = self._extract_csrf(pick_html)
        chosen = browser.post(
            self._url("/crm/setup/choose/"),
            data={
                "provider_key": "fake",
                "form_token": choose_token,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/pick/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(chosen.status_code, 200)
        self.assertIn("/crm/setup/practice/", chosen.url)

        practice_html = chosen.text
        self._assert_no_secrets(practice_html)
        connect_token = self._extract_form_token(practice_html)
        csrf = self._extract_csrf(practice_html)
        complete = browser.post(
            self._url("/crm/setup/practice/connect/"),
            data={
                "form_token": connect_token,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/practice/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(complete.status_code, 200)
        self.assertIn("/crm/setup/complete/", complete.url)
        self.assertIn("Practice CRM", complete.text)
        self.assertIn("Connected", complete.text)
        self._assert_no_secrets(complete.text)

        # Connection is visible on hub and disconnect works end-to-end.
        hub2 = self._get(browser, "/crm/connections/")
        self.assertIn("connected", hub2.text.lower())
        self.assertIn("Disconnect", hub2.text)
        # Extract connection_id from disconnect form action.
        marker = '/crm/connections/'
        # Prefer disconnect URL pattern.
        disc_marker = '/disconnect/'
        idx = hub2.text.find(disc_marker)
        self.assertNotEqual(idx, -1, "disconnect control missing")
        # Walk back to start of path
        start_idx = hub2.text.rfind(marker, 0, idx)
        self.assertNotEqual(start_idx, -1)
        end_idx = hub2.text.find('"', idx)
        disconnect_path = hub2.text[start_idx:end_idx]
        self.assertIn("/disconnect/", disconnect_path)

        csrf = self._extract_csrf(hub2.text) if "csrfmiddlewaretoken" in hub2.text else browser.cookies.get("csrftoken", "")
        # Hub disconnect forms only have csrf_token in form; fetch a page form.
        # Re-GET hub for a fresh csrf inside a form near disconnect is hard —
        # use cookie CSRF + empty form post via the disconnect action path.
        if not csrf and "csrftoken" in browser.cookies:
            csrf = browser.cookies["csrftoken"]
        # Need a csrf form field: GET hub and extract any csrfmiddlewaretoken.
        if "csrfmiddlewaretoken" not in hub2.text:
            # Inject via cookie-only posts; Django requires the token field usually.
            # Force by GETting landing which includes messages forms rarely —
            # POST with X-CSRFToken header and csrfmiddlewaretoken from cookie.
            csrf = browser.cookies.get("csrftoken", "")
        else:
            csrf = self._extract_csrf(hub2.text)

        disconnected = browser.post(
            self._url(disconnect_path),
            data={"csrfmiddlewaretoken": csrf},
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/connections/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(disconnected.status_code, 200)
        self.assertIn("/crm/connections/", disconnected.url)
        self._assert_no_secrets(disconnected.text)

    def test_browser_hubspot_guided_registration_connect_and_device_reuse(self):
        """Guided HubSpot private-app path + API restart without secret re-entry.

        Exercises 3B registration (vault + dynamic composition) and Phase 4
        ready-state / reconnect after durable API restart (desire #4).
        """
        browser = self._browser()
        secret_token = "pat-xyz-secret-phase5conn"

        # Guided setup → HubSpot credentials → save → connect.
        start = browser.get(self._url("/crm/setup/"), timeout=30, allow_redirects=True)
        self.assertEqual(start.status_code, 200)
        choose_token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)
        chosen = browser.post(
            self._url("/crm/setup/choose/"),
            data={
                "provider_key": "hubspot",
                "form_token": choose_token,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/pick/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(chosen.status_code, 200)
        self.assertIn("/crm/setup/hubspot/", chosen.url)
        self.assertIn("Set up HubSpot", chosen.text)
        self.assertIn('name="access_token"', chosen.text)
        save_token = self._extract_form_token(chosen.text)
        csrf = self._extract_csrf(chosen.text)
        saved = browser.post(
            self._url("/crm/setup/hubspot/save/"),
            data={
                "form_token": save_token,
                "csrfmiddlewaretoken": csrf,
                "label": "Work portal E2E",
                "auth_mode": "private_app",
                "expected_hub_id": "424242",
                "access_token": secret_token,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/hubspot/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn("/crm/setup/hubspot/connect/", saved.url)
        self.assertNotIn(secret_token, saved.text)
        self.assertIn("Connect HubSpot portal", saved.text)

        connect_token = self._extract_form_token(saved.text)
        csrf = self._extract_csrf(saved.text)
        complete = browser.post(
            self._url("/crm/setup/hubspot/connect/submit/"),
            data={
                "form_token": connect_token,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/hubspot/connect/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(complete.status_code, 200, complete.text[:500])
        self.assertIn("/crm/setup/complete/", complete.url)
        self.assertIn("HubSpot", complete.text)
        self.assertIn("Connected", complete.text)
        self.assertNotIn(secret_token, complete.text)

        hub = self._get(browser, "/crm/connections/")
        self.assertIn("HubSpot", hub.text)
        self.assertIn("app registered on this device", hub.text)
        self.assertIn("Manage app", hub.text)
        self.assertIn("/crm/setup/hubspot/?edit=1", hub.text)
        self.assertNotIn(secret_token, hub.text)

        # Disconnect so reconnect path can prove registration reuse.
        disc_marker = "/disconnect/"
        idx = hub.text.find(disc_marker)
        self.assertNotEqual(idx, -1)
        start_idx = hub.text.rfind("/crm/connections/", 0, idx)
        end_idx = hub.text.find('"', idx)
        disconnect_path = hub.text[start_idx:end_idx]
        csrf = self._extract_csrf(hub.text)
        browser.post(
            self._url(disconnect_path),
            data={"csrfmiddlewaretoken": csrf},
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/connections/"),
            },
            timeout=30,
            allow_redirects=True,
        )

        # Device reuse: restart API process (durable vault + state root).
        self._restart_api()

        # Fresh browser session after restart — still no secret re-entry.
        browser2 = self._browser()
        hub2 = self._get(browser2, "/crm/connections/")
        self.assertEqual(hub2.status_code, 200)
        self.assertIn("HubSpot", hub2.text)
        self.assertIn("Ready on this device", hub2.text)
        self.assertIn("app registered on this device", hub2.text)
        self.assertIn("Manage app", hub2.text)
        self.assertNotIn(secret_token, hub2.text)
        # Connect without visiting credentials form.
        self.assertIn('name="provider_key" value="hubspot"', hub2.text)
        csrf = self._extract_csrf(hub2.text)
        reconnected = browser2.post(
            self._url("/crm/connect/"),
            data={
                "provider_key": "hubspot",
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser2.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/connections/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(reconnected.status_code, 200)
        self.assertIn("/crm/connections/", reconnected.url)
        self.assertIn("connected", reconnected.text.lower())
        self.assertNotIn(secret_token, reconnected.text)

        # Registration inventory on live API still lists hubspot (no re-save).
        regs = requests.get(
            f"{self.api_base}/v1/settings/crm-app-registrations",
            timeout=10,
        )
        self.assertEqual(regs.status_code, 200, regs.text)
        keys = {
            row.get("provider_key")
            for row in (regs.json().get("registrations") or [])
        }
        self.assertIn("hubspot", keys)
        self.assertNotIn(secret_token, regs.text)

    def test_browser_csrf_rejected_without_token_on_live_django(self):
        """CSRF middleware on live Django rejects bare choose POST."""
        browser = self._browser()
        start = browser.get(self._url("/crm/setup/"), timeout=30, allow_redirects=True)
        self.assertEqual(start.status_code, 200)
        form_token = self._extract_form_token(start.text)
        denied = browser.post(
            self._url("/crm/setup/choose/"),
            data={"provider_key": "fake", "form_token": form_token},
            timeout=30,
            allow_redirects=False,
        )
        self.assertEqual(denied.status_code, 403)


class CrmConnectionGenerationTests(TestCase):
    """R4: Django CRM connect generation covers full start+complete action."""

    def _owner_session(self, owner) -> str:
        return f"django-{owner}"

    def _start_body(self, connection_id: str) -> dict:
        return {
            "connection_id": connection_id,
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "status": "pending",
            "supported_entity_families": ["company", "person"],
            "supported_source_modes": ["candidate_upload", "acquire_all"],
            "capabilities": {},
            "authorization": {
                "mode": "oauth_code",
                "authorization_url": "https://example.test/oauth",
                "state": f"state-{connection_id}",
            },
            "capability_profile_digest": "digest",
            "maximum_authorization": {
                "reference_acquisition": "execute",
                "duplicate_execution": "execute",
            },
        }

    def _complete_body(self, connection_id: str) -> dict:
        return {
            "connection_id": connection_id,
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "status": "connected",
            "supported_entity_families": ["company", "person"],
            "supported_source_modes": ["candidate_upload", "acquire_all"],
            "capabilities": {},
            "capability_profile_digest": "digest",
            "maximum_authorization": {
                "reference_acquisition": "execute",
                "duplicate_execution": "execute",
            },
        }

    def test_generation_stays_open_after_start_before_complete(self):
        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        identity = f"crm-connect:fake:{owner_session}"
        start = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=identity,
            logical_action_generation=0,
            request_json={"provider_key": "fake", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = self._start_body("crm_conn_open")
        start.save(update_fields=["state", "response_json"])

        # Completed start alone must not advance — complete not created yet.
        self.assertEqual(
            crm_connect_action_generation(
                session, provider_key="fake", owner_session=owner_session
            ),
            0,
        )

    def test_generation_stays_open_when_complete_pending_or_unknown(self):
        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        identity = f"crm-connect:fake:{owner_session}"
        connection_id = "crm_conn_mid"
        start = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=identity,
            logical_action_generation=0,
            request_json={"provider_key": "fake", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = self._start_body(connection_id)
        start.save(update_fields=["state", "response_json"])

        complete = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=f"crm-complete:{connection_id}:{owner_session}",
            request_json={
                "authorization_code": "code",
                "state": f"state-{connection_id}",
                "owner_session": owner_session,
            },
        )
        self.assertEqual(complete.state, ApiMutation.State.PENDING)
        self.assertEqual(
            crm_connect_action_generation(
                session, provider_key="fake", owner_session=owner_session
            ),
            0,
        )

        complete.state = ApiMutation.State.UNKNOWN
        complete.save(update_fields=["state"])
        self.assertEqual(
            crm_connect_action_generation(
                session, provider_key="fake", owner_session=owner_session
            ),
            0,
        )

    def test_generation_advances_only_after_complete_terminal(self):
        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        identity = f"crm-connect:fake:{owner_session}"
        connection_id = "crm_conn_done"
        start = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=identity,
            logical_action_generation=0,
            request_json={"provider_key": "fake", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = self._start_body(connection_id)
        start.save(update_fields=["state", "response_json"])
        complete = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=f"crm-complete:{connection_id}:{owner_session}",
            request_json={
                "authorization_code": "code",
                "state": f"state-{connection_id}",
                "owner_session": owner_session,
            },
        )
        complete.state = ApiMutation.State.COMPLETED
        complete.response_json = self._complete_body(connection_id)
        complete.save(update_fields=["state", "response_json"])
        self.assertEqual(
            crm_connect_action_generation(
                session, provider_key="fake", owner_session=owner_session
            ),
            1,
        )

        # Rejected complete is also terminal for a new intentional Connect.
        complete.state = ApiMutation.State.REJECTED
        complete.save(update_fields=["state"])
        self.assertEqual(
            crm_connect_action_generation(
                session, provider_key="fake", owner_session=owner_session
            ),
            1,
        )

    def test_pending_start_reuses_generation(self):
        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        identity = f"crm-connect:fake:{owner_session}"
        pending = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=identity,
            logical_action_generation=0,
            request_json={
                "provider_key": "fake",
                "owner_session": owner_session,
            },
        )
        self.assertEqual(pending.state, ApiMutation.State.PENDING)
        self.assertEqual(
            crm_connect_action_generation(
                session, provider_key="fake", owner_session=owner_session
            ),
            0,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_reconnect_after_full_connect_uses_new_generation(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        call_index = {"n": 0}

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = self._start_body("crm_conn_gen0")
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_reconnect":
                attempt = "rca_test_1" if call_index["n"] == 0 else "rca_test_2"
                call_index["n"] += 1
                body = {
                    **self._start_body("crm_conn_gen0"),
                    "status": "pending",
                    "reconnect_attempt_id": attempt,
                    "reconnect_epoch": call_index["n"],
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                connection_id = mutation.route.split("/")[3]
                body = self._complete_body(connection_id)
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_disconnect":
                connection_id = mutation.route.split("/")[3]
                body = {
                    **self._complete_body(connection_id),
                    "status": "disconnected",
                    "maximum_authorization": {
                        "reference_acquisition": "disabled",
                        "duplicate_execution": "disabled",
                    },
                }
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_connection",
                return_value=self._complete_body("crm_conn_gen0"),
            ),
        ):
            first = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "fake"},
            )
            self.assertEqual(first.status_code, 302)

            journal = ImportSession.objects.get(product_key="crm.connection")
            self.assertEqual(journal.owner_id, owner)
            start0 = journal.api_mutations.get(
                mutation_kind="crm_connection_start",
                logical_action_generation=0,
            )
            self.assertEqual(start0.state, ApiMutation.State.COMPLETED)
            self.assertEqual(start0.response_json["connection_id"], "crm_conn_gen0")
            complete0 = journal.api_mutations.get(
                logical_action_identity=(
                    f"crm-complete:crm_conn_gen0:{self._owner_session(owner)}"
                )
            )
            self.assertEqual(complete0.state, ApiMutation.State.COMPLETED)

            disconnect = self.client.post(
                reverse(
                    "importer:crm_disconnect",
                    kwargs={"connection_id": "crm_conn_gen0"},
                )
            )
            self.assertEqual(disconnect.status_code, 302)

            second = self.client.post(
                reverse(
                    "importer:crm_reconnect",
                    kwargs={"connection_id": "crm_conn_gen0"},
                )
            )
            self.assertEqual(second.status_code, 302)

            disconnect2 = self.client.post(
                reverse(
                    "importer:crm_disconnect",
                    kwargs={"connection_id": "crm_conn_gen0"},
                )
            )
            self.assertEqual(disconnect2.status_code, 302)

            third = self.client.post(
                reverse(
                    "importer:crm_reconnect",
                    kwargs={"connection_id": "crm_conn_gen0"},
                )
            )
            self.assertEqual(third.status_code, 302)

        reconnects = list(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_reconnect"
            ).order_by("logical_action_generation")
        )
        self.assertEqual(len(reconnects), 2)
        self.assertEqual(reconnects[0].logical_action_generation, 0)
        self.assertEqual(reconnects[1].logical_action_generation, 1)
        self.assertEqual(reconnects[0].response_json["reconnect_attempt_id"], "rca_test_1")
        self.assertEqual(reconnects[1].response_json["reconnect_attempt_id"], "rca_test_2")
        owner_session = self._owner_session(owner)
        complete1 = journal.api_mutations.get(
            logical_action_identity=(
                f"crm-complete:crm_conn_gen0:{owner_session}:rca_test_1"
            )
        )
        complete2 = journal.api_mutations.get(
            logical_action_identity=(
                f"crm-complete:crm_conn_gen0:{owner_session}:rca_test_2"
            )
        )
        self.assertEqual(complete1.state, ApiMutation.State.COMPLETED)
        self.assertEqual(complete1.logical_action_generation, 1)
        self.assertEqual(complete2.state, ApiMutation.State.COMPLETED)
        self.assertEqual(complete2.logical_action_generation, 2)
        self.assertIn("authorization_code_digest", complete1.request_json)
        self.assertNotIn("authorization_code", complete1.request_json)

    def test_disconnect_after_prior_completed_issues_fresh_dispatch(self):
        """A new Disconnect must dispatch, not replay a frozen prior receipt.

        Regression: crm_disconnect used a stable logical_action_identity with
        generation 0. Once one disconnect froze COMPLETED, every later click
        reused that row and dispatch() short-circuited on the frozen response —
        flashing success while never POSTing /disconnect. After the connection
        is live again, the new click must open a new generation and actually
        dispatch.
        """
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        connection_id = "crm_conn_relive"
        identity = f"crm-disconnect:{connection_id}:{owner_session}"

        # A prior disconnect already completed and froze a 200 receipt.
        prior = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_disconnect",
            route=f"/v1/crm/connections/{connection_id}/disconnect",
            logical_action_identity=identity,
            logical_action_generation=0,
            resource_identity=connection_id,
            request_json={"owner_session": owner_session},
        )
        prior.state = ApiMutation.State.COMPLETED
        prior.response_json = {
            **self._complete_body(connection_id),
            "status": "disconnected",
        }
        prior.http_status = 200
        prior.save(update_fields=["state", "response_json", "http_status"])

        dispatched: list[tuple[str, int]] = []

        def dispatch(mutation, **kwargs):
            dispatched.append(
                (mutation.mutation_kind, mutation.logical_action_generation)
            )
            body = {
                **self._complete_body(connection_id),
                "status": "disconnected",
            }
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.http_status = 200
            mutation.save(
                update_fields=[
                    "state",
                    "response_json",
                    "http_status",
                    "updated_at",
                ]
            )
            return MutationDispatchResult(mutation, body)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            resp = self.client.post(
                reverse(
                    "importer:crm_disconnect",
                    kwargs={"connection_id": connection_id},
                )
            )
        self.assertEqual(resp.status_code, 302)

        # The frozen gen-0 receipt was not replayed: a real dispatch ran for a
        # brand-new generation.
        self.assertEqual(dispatched, [("crm_connection_disconnect", 1)])
        rows = list(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_disconnect"
            ).order_by("logical_action_generation")
        )
        self.assertEqual([r.logical_action_generation for r in rows], [0, 1])

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_unknown_reconnect_start_offers_retry_not_remove(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        mutation = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_reconnect",
            route="/v1/crm/connections/crm_conn_u/reconnect",
            logical_action_identity=f"crm-reconnect:crm_conn_u:{owner_session}",
            logical_action_generation=0,
            request_json={"owner_session": owner_session},
        )
        mutation.state = ApiMutation.State.UNKNOWN
        mutation.save(update_fields=["state", "updated_at"])
        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.50.0"},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={
                    "connections": [
                        {
                            "connection_id": "crm_conn_u",
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                            "status": "pending",
                            "reconnect_attempt_id": "rca_unknown",
                            "reconnect_epoch": 1,
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                side_effect=ApiUnavailableError("status unavailable"),
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Retry reconnect", body)
        self.assertIn("still uncertain", body)
        self.assertNotIn(">Remove</button>", body)
        self.assertNotIn(">Reconnect</button>", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_completed_start_offers_continue_reconnect_not_new_start(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        owner_session = self._owner_session(owner)
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_reconnect",
            route="/v1/crm/connections/crm_conn_c/reconnect",
            logical_action_identity=f"crm-reconnect:crm_conn_c:{owner_session}",
            logical_action_generation=0,
            request_json={"owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_c"),
            "status": "pending",
            "reconnect_attempt_id": "rca_continue",
            "reconnect_epoch": 1,
        }
        start.http_status = 200
        start.save(update_fields=["state", "response_json", "http_status", "updated_at"])
        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.50.0"},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={
                    "connections": [
                        {
                            "connection_id": "crm_conn_c",
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                            "status": "pending",
                            "reconnect_attempt_id": "rca_continue",
                            "reconnect_epoch": 1,
                        }
                    ]
                },
            ),
        ):
            page = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode("utf-8")
        continue_url = reverse(
            "importer:crm_reconnect_continue",
            kwargs={"connection_id": "crm_conn_c"},
        )
        self.assertIn("Continue reconnect", html)
        self.assertIn(continue_url, html)
        self.assertNotIn(">Reconnect</button>", html)
        self.assertEqual(self.client.get(continue_url).status_code, 405)

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                body = self._complete_body("crm_conn_c")
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.50.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_connection",
                return_value=self._complete_body("crm_conn_c"),
            ),
        ):
            posted = self.client.post(
                continue_url,
                data={"reconnect_attempt_id": "rca_continue"},
            )
        self.assertEqual(posted.status_code, 302)
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.COMPLETED)
        self.assertEqual(
            complete.logical_action_identity,
            f"crm-complete:crm_conn_c:{owner_session}:rca_continue",
        )
        self.assertIn("authorization_code_digest", complete.request_json)
        self.assertNotIn("authorization_code", complete.request_json)
        self.assertEqual(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_reconnect"
            ).count(),
            1,
        )

    def _dispatch_with_production_unknown_guard(
        self, mutation, *, file_path=None, explicit_retry=False, handler
    ):
        """Mirror EasyImportsApiClient.dispatch UNKNOWN/terminal guards."""

        mutation.refresh_from_db()
        if mutation.state in {
            ApiMutation.State.COMPLETED,
            ApiMutation.State.REJECTED,
        }:
            if not isinstance(mutation.response_json, dict):
                raise AssertionError("finished mutation missing frozen response")
            return MutationDispatchResult(mutation, mutation.response_json)
        if mutation.state == ApiMutation.State.UNKNOWN and not explicit_retry:
            raise MutationExplicitRetryRequired(
                "This outcome is uncertain. Use the explicit exact-retry action."
            )
        return handler(mutation, file_path=file_path, explicit_retry=explicit_retry)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_retry_after_start_before_complete_resumes_same_generation(self):
        """Crash between start complete and OAuth complete reuses generation 0."""
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        owner_session = self._owner_session(owner)
        connection_id = "crm_conn_resume"
        start_calls = {"n": 0}
        complete_calls = {"n": 0}
        complete_explicit_retries = []

        def handler(mutation, *, file_path=None, explicit_retry=False):
            if mutation.mutation_kind == "crm_connection_start":
                start_calls["n"] += 1
                body = self._start_body(connection_id)
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                complete_calls["n"] += 1
                complete_explicit_retries.append(explicit_retry)
                if complete_calls["n"] == 1:
                    # First complete attempt becomes uncertain (process loss).
                    mutation.state = ApiMutation.State.UNKNOWN
                    mutation.error_message = "connection lost"
                    mutation.save(update_fields=["state", "error_message", "updated_at"])
                    raise ApiUnavailableError("The API response is uncertain.")
                body = self._complete_body(connection_id)
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 200
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected mutation kind {mutation.mutation_kind}")

        def dispatch(mutation, *, file_path=None, explicit_retry=False):
            return self._dispatch_with_production_unknown_guard(
                mutation,
                file_path=file_path,
                explicit_retry=explicit_retry,
                handler=handler,
            )

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            first = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "fake"},
            )
            self.assertEqual(first.status_code, 302)

            journal = ImportSession.objects.get(product_key="crm.connection")
            starts = list(
                journal.api_mutations.filter(mutation_kind="crm_connection_start")
            )
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0].logical_action_generation, 0)
            self.assertEqual(starts[0].state, ApiMutation.State.COMPLETED)
            self.assertEqual(
                crm_connect_action_generation(
                    journal, provider_key="fake", owner_session=owner_session
                ),
                0,
            )
            complete = journal.api_mutations.get(
                logical_action_identity=f"crm-complete:{connection_id}:{owner_session}"
            )
            self.assertEqual(complete.state, ApiMutation.State.UNKNOWN)

            # Retry Connect: must not invent generation 1 / second start, and
            # must pass explicit_retry so production UNKNOWN guard allows send.
            second = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "fake"},
            )
            self.assertEqual(second.status_code, 302)

        starts = list(
            journal.api_mutations.filter(mutation_kind="crm_connection_start")
        )
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].logical_action_generation, 0)
        # First start dispatch may be replayed from frozen COMPLETED without a
        # second side-effect call; complete should run twice (unknown then ok).
        self.assertEqual(complete_calls["n"], 2)
        self.assertEqual(complete_explicit_retries, [False, True])
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.COMPLETED)

    def test_unknown_start_dispatch_requires_explicit_retry_flag(self):
        """Helper enables explicit_retry only when the mutation is UNKNOWN."""
        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:fake:django-{owner}",
            request_json={"provider_key": "fake", "owner_session": f"django-{owner}"},
        )
        mutation.state = ApiMutation.State.UNKNOWN
        mutation.save(update_fields=["state"])

        seen = {}

        def dispatch(mutation, *, file_path=None, explicit_retry=False):
            seen["explicit_retry"] = explicit_retry
            if mutation.state == ApiMutation.State.UNKNOWN and not explicit_retry:
                raise MutationExplicitRetryRequired("blocked")
            body = self._start_body("crm_conn_unknown_start")
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.save(update_fields=["state", "response_json"])
            return MutationDispatchResult(mutation, body)

        client = EasyImportsApiClient()
        with patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch):
            result = _dispatch_connection_mutation(client, mutation)
        self.assertTrue(seen["explicit_retry"])
        self.assertEqual(result.response["connection_id"], "crm_conn_unknown_start")

        # Without the helper, production guard would block.
        mutation.state = ApiMutation.State.UNKNOWN
        mutation.save(update_fields=["state"])
        with (
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            self.assertRaises(MutationExplicitRetryRequired),
        ):
            client.dispatch(mutation)

    def test_live_oauth_callback_never_persists_raw_authorization_code(self):
        """Path C: OAuth codes stay process-ephemeral; durable journal holds digest only."""
        from importer.api_client import authorization_code_digest

        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session["crm_oauth_pending"] = {
            "connection_id": "crm_conn_live",
            "provider_key": "hubspot",
            "state": "state-live-1",
            "owner_session": self._owner_session(owner),
            "connect_identity": f"crm-connect:hubspot:{self._owner_session(owner)}",
        }
        browser_session.save()

        raw_code = "super-secret-oauth-code-do-not-store"
        seen_bodies = []

        def dispatch(mutation, *, file_path=None, explicit_retry=False):
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                # Durable request must not contain the raw code.
                stored = mutation.request_json or {}
                self.assertNotIn("authorization_code", stored)
                self.assertEqual(
                    stored.get("authorization_code_digest"),
                    authorization_code_digest(raw_code),
                )
                seen_bodies.append(dict(stored))
                body = self._complete_body("crm_conn_live")
                body["provider_key"] = "hubspot"
                body["provider_label"] = "HubSpot"
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.save(update_fields=["state", "response_json"])
                return MutationDispatchResult(mutation, body)
            raise AssertionError(f"unexpected kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            response = self.client.get(
                reverse("importer:crm_oauth_callback"),
                data={"code": raw_code, "state": "state-live-1"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(seen_bodies), 1)
        journal = ImportSession.objects.get(product_key="crm.connection")
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertNotIn("authorization_code", complete.request_json or {})
        self.assertNotIn(raw_code, json.dumps(complete.request_json or {}))
        self.assertEqual(
            (complete.request_json or {}).get("authorization_code_digest"),
            authorization_code_digest(raw_code),
        )

    def test_live_oauth_callback_pending_redirects_without_replaying_code(self):
        """2-OAUTH: 202 from complete is pending-safe and does not restage."""
        from importer.api_client import authorization_code_digest

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:hubspot:{owner_session}",
            logical_action_generation=0,
            request_json={"provider_key": "hubspot", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        start.save(update_fields=["state", "response_json"])

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session["crm_oauth_pending"] = {
            "connection_id": "crm_conn_live",
            "provider_key": "hubspot",
            "state": "state-live-1",
            "owner_session": owner_session,
            "connect_identity": f"crm-connect:hubspot:{owner_session}",
        }
        browser_session.save()

        raw_code = "single-use-oauth-code-pending"
        dispatch_calls = {"n": 0}

        def dispatch(mutation, *, file_path=None, explicit_retry=False):
            dispatch_calls["n"] += 1
            stored = mutation.request_json or {}
            self.assertNotIn("authorization_code", stored)
            self.assertEqual(
                stored.get("authorization_code_digest"),
                authorization_code_digest(raw_code),
            )
            mutation.state = ApiMutation.State.PENDING
            mutation.http_status = 202
            mutation.lease_token = None
            mutation.lease_expires_at = None
            mutation.save(
                update_fields=[
                    "state",
                    "http_status",
                    "lease_token",
                    "lease_expires_at",
                    "updated_at",
                ]
            )
            raise ApiOperationInProgressError("EasyImports is processing this action.")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value={
                    "mutation_id": "oauth-pending",
                    "mutation_kind": "crm_connection_oauth_complete",
                    "status": "pending",
                    "http_status": None,
                    "response": None,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connection",
                return_value={
                    **self._complete_body("crm_conn_live"),
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "status": "pending",
                },
            ),
        ):
            first = self.client.get(
                reverse("importer:crm_oauth_callback"),
                data={"code": raw_code, "state": "state-live-1"},
            )
            self.assertEqual(first.status_code, 302)
            self.assertEqual(first["Location"], reverse("importer:crm_connections"))
            self.assertEqual(dispatch_calls["n"], 1)
            self.assertIn("crm_oauth_pending", self.client.session)

            second = self.client.get(
                reverse("importer:crm_oauth_callback"),
                data={"code": raw_code, "state": "state-live-1"},
            )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second["Location"], reverse("importer:crm_connections"))
        self.assertEqual(dispatch_calls["n"], 1)
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.PENDING)
        self.assertEqual(complete.http_status, 202)
        self.assertNotIn(raw_code, json.dumps(complete.request_json or {}))

    def test_connect_hub_settles_pending_oauth_without_replaying_code(self):
        """Connect GET polls mutation status and completes the journal."""
        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        complete = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route="/v1/crm/connections/crm_conn_live/oauth/complete",
            logical_action_identity=f"crm-complete:crm_conn_live:{owner_session}",
            request_json={
                "authorization_code_digest": "digest-only",
                "state": "state-live-1",
                "owner_session": owner_session,
            },
        )
        complete.state = ApiMutation.State.PENDING
        complete.http_status = 202
        complete.lease_token = None
        complete.lease_expires_at = None
        complete.save(
            update_fields=[
                "state",
                "http_status",
                "lease_token",
                "lease_expires_at",
                "updated_at",
            ]
        )

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        connected = {
            **self._complete_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
            "status": "connected",
        }
        dispatch_calls = {"n": 0}

        def dispatch(*args, **kwargs):
            dispatch_calls["n"] += 1
            raise AssertionError("refresh must not replay oauth/complete")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "mutation_status",
                return_value={
                    "mutation_id": str(complete.idempotency_key),
                    "mutation_kind": "crm_connection_oauth_complete",
                    "status": "completed",
                    "http_status": 200,
                    "response": connected,
                    "retryable": False,
                    "retry_after_seconds": 2,
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": [connected]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={"registrations": []},
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dispatch_calls["n"], 0)
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.COMPLETED)
        self.assertEqual(complete.response_json["connection_id"], "crm_conn_live")
        self.assertContains(response, "CRM connection established.")

    def test_fake_connect_pending_complete_is_not_an_error(self):
        """Practice complete 202 must not look like a failed connect."""
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_fake_pending"

        def dispatch(mutation, **kwargs):
            if mutation.mutation_kind == "crm_connection_start":
                body = self._start_body(connection_id)
                body["authorization"]["authorization_url"] = None
                mutation.state = ApiMutation.State.COMPLETED
                mutation.response_json = body
                mutation.http_status = 201
                mutation.save(
                    update_fields=[
                        "state",
                        "response_json",
                        "http_status",
                        "updated_at",
                    ]
                )
                return MutationDispatchResult(mutation, body)
            if mutation.mutation_kind == "crm_connection_oauth_complete":
                mutation.state = ApiMutation.State.PENDING
                mutation.http_status = 202
                mutation.lease_token = None
                mutation.lease_expires_at = None
                mutation.save(
                    update_fields=[
                        "state",
                        "http_status",
                        "lease_token",
                        "lease_expires_at",
                        "updated_at",
                    ]
                )
                raise ApiOperationInProgressError(
                    "EasyImports is processing this action."
                )
            raise AssertionError(f"unexpected kind {mutation.mutation_kind}")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
        ):
            response = self.client.post(
                reverse("importer:crm_connect"),
                data={"provider_key": "fake"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("importer:crm_connections"))

    def test_live_oauth_unknown_complete_unlocks_reconnect_generation(self):
        """Uncertain OAuth complete that failed on API unlocks reconnect."""
        from importer.api_client import authorization_code_digest
        from importer.command_service import crm_connect_action_generation

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        # Prior completed start (generation 0).
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:hubspot:{owner_session}",
            logical_action_generation=0,
            request_json={"provider_key": "hubspot", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        start.save(update_fields=["state", "response_json"])

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session["crm_oauth_pending"] = {
            "connection_id": "crm_conn_live",
            "provider_key": "hubspot",
            "state": "state-live-1",
            "owner_session": owner_session,
            "connect_identity": f"crm-connect:hubspot:{owner_session}",
        }
        browser_session.save()

        def dispatch(mutation, *, file_path=None, explicit_retry=False):
            mutation.state = ApiMutation.State.UNKNOWN
            mutation.error_message = "network uncertain"
            mutation.lease_token = None
            mutation.lease_expires_at = None
            mutation.save(
                update_fields=[
                    "state",
                    "error_message",
                    "lease_token",
                    "lease_expires_at",
                ]
            )
            raise ApiUnavailableError("The API response is uncertain.")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient,
                "crm_connection",
                side_effect=ApiRejectedError(
                    "crm_connection_not_found", "Unknown connection."
                ),
            ),
        ):
            response = self.client.get(
                reverse("importer:crm_oauth_callback"),
                data={"code": "one-time-code", "state": "state-live-1"},
            )
        self.assertEqual(response.status_code, 302)
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.REJECTED)
        self.assertEqual(complete.error_code, "oauth_complete_uncertain")
        self.assertNotIn("authorization_code", complete.request_json or {})
        self.assertEqual(
            (complete.request_json or {}).get("authorization_code_digest"),
            authorization_code_digest("one-time-code"),
        )
        # Connect generation advances so a new Connect is a new attempt.
        self.assertEqual(
            crm_connect_action_generation(
                journal, provider_key="hubspot", owner_session=owner_session
            ),
            1,
        )

    def test_live_oauth_lost_response_settles_when_api_already_connected(self):
        """Lost complete response: connection resource may already be connected."""
        from importer.command_service import crm_connect_action_generation

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:hubspot:{owner_session}",
            logical_action_generation=0,
            request_json={"provider_key": "hubspot", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        start.save(update_fields=["state", "response_json"])

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session["crm_oauth_pending"] = {
            "connection_id": "crm_conn_live",
            "provider_key": "hubspot",
            "state": "state-live-1",
            "owner_session": owner_session,
            "connect_identity": f"crm-connect:hubspot:{owner_session}",
        }
        browser_session.save()

        connected = {
            **self._complete_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
            "status": "connected",
        }

        def dispatch(mutation, *, file_path=None, explicit_retry=False):
            mutation.state = ApiMutation.State.UNKNOWN
            mutation.lease_token = None
            mutation.lease_expires_at = None
            mutation.error_message = "lost response"
            mutation.save(
                update_fields=[
                    "state",
                    "lease_token",
                    "lease_expires_at",
                    "error_message",
                ]
            )
            raise ApiUnavailableError("The API response is uncertain.")

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(
                EasyImportsApiClient, "crm_connection", return_value=connected
            ),
        ):
            response = self.client.get(
                reverse("importer:crm_oauth_callback"),
                data={"code": "one-time-code", "state": "state-live-1"},
            )
        self.assertEqual(response.status_code, 302)
        complete = journal.api_mutations.get(
            mutation_kind="crm_connection_oauth_complete"
        )
        self.assertEqual(complete.state, ApiMutation.State.COMPLETED)
        self.assertEqual(complete.response_json["status"], "connected")
        self.assertEqual(complete.http_status, 200)
        self.assertEqual(complete.response_digest, canonical_digest(connected))
        self.assertEqual(complete.error_message, "")
        self.assertEqual(
            crm_connect_action_generation(
                journal, provider_key="hubspot", owner_session=owner_session
            ),
            1,
        )

    def test_live_oauth_duplicate_callback_busy_does_not_reject_in_flight(self):
        """MutationBusyError must not clear an active lease or reject PENDING."""
        from datetime import timedelta

        from django.utils import timezone

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:hubspot:{owner_session}",
            logical_action_generation=0,
            request_json={"provider_key": "hubspot", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        start.save(update_fields=["state", "response_json"])

        # Pre-create complete mutation as if first callback already leased it.
        lease = uuid4()
        complete = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route="/v1/crm/connections/crm_conn_live/oauth/complete",
            logical_action_identity=f"crm-complete:crm_conn_live:{owner_session}",
            request_json={
                "authorization_code_digest": "digest-same",
                "state": "state-live-1",
                "owner_session": owner_session,
            },
        )
        complete.state = ApiMutation.State.PENDING
        complete.lease_token = lease
        complete.lease_expires_at = timezone.now() + timedelta(minutes=2)
        complete.save(update_fields=["state", "lease_token", "lease_expires_at"])

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session["crm_oauth_pending"] = {
            "connection_id": "crm_conn_live",
            "provider_key": "hubspot",
            "state": "state-live-1",
            "owner_session": owner_session,
            "connect_identity": f"crm-connect:hubspot:{owner_session}",
        }
        browser_session.save()

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(
                EasyImportsApiClient,
                "dispatch",
                side_effect=MutationBusyError("This exact action is already being sent."),
            ),
        ):
            # Same digest so create_or_reuse reuses the leased mutation.
            from importer.api_client import authorization_code_digest

            # Force reuse of the existing mutation by matching digest via
            # create path: patch create_or_reuse to return the leased row.
            with patch(
                "importer.connection_views.create_or_reuse_mutation",
                return_value=complete,
            ):
                response = self.client.get(
                    reverse("importer:crm_oauth_callback"),
                    data={"code": "any-code", "state": "state-live-1"},
                )
        self.assertEqual(response.status_code, 302)
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.PENDING)
        self.assertEqual(complete.lease_token, lease)
        self.assertIsNotNone(complete.lease_expires_at)
        # Session pending preserved for refresh.
        self.assertIn("crm_oauth_pending", self.client.session)

    def _aged_unknown_complete(self, journal, *, owner_session: str, connection_id: str):
        from datetime import timedelta

        from importer.connection_views import OAUTH_ABANDON_MIN_AGE_SECONDS

        complete = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=(
                f"crm-complete:{connection_id}:{owner_session}"
            ),
            request_json={
                "authorization_code_digest": "digest",
                "state": "state-live-1",
                "owner_session": owner_session,
            },
        )
        complete.state = ApiMutation.State.UNKNOWN
        complete.lease_token = None
        complete.lease_expires_at = None
        old = timezone.now() - timedelta(seconds=OAUTH_ABANDON_MIN_AGE_SECONDS + 5)
        complete.first_submitted_at = old
        complete.dispatched_at = old
        complete.updated_at = old
        complete.save(
            update_fields=[
                "state",
                "lease_token",
                "lease_expires_at",
                "first_submitted_at",
                "dispatched_at",
                "updated_at",
            ]
        )
        return complete

    def test_abandon_uncertain_oauth_rejects_and_advances_generation(self):
        """Abandon terminalizes only after remote is confirmed gone/disconnected."""
        from importer.command_service import crm_connect_action_generation

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:hubspot:{owner_session}",
            logical_action_generation=0,
            request_json={"provider_key": "hubspot", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        start.save(update_fields=["state", "response_json"])

        complete = self._aged_unknown_complete(
            journal, owner_session=owner_session, connection_id="crm_conn_live"
        )

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session["crm_oauth_pending"] = {
            "connection_id": "crm_conn_live",
            "provider_key": "hubspot",
            "state": "state-live-1",
            "owner_session": owner_session,
        }
        browser_session.save()

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connection",
                side_effect=ApiRejectedError(
                    "crm_connection_not_found", "Unknown connection."
                ),
            ),
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_abandon_uncertain_connection",
                    kwargs={"connection_id": "crm_conn_live"},
                )
            )
        self.assertEqual(response.status_code, 302)
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.REJECTED)
        self.assertEqual(complete.error_code, "oauth_abandoned_by_operator")
        self.assertIsNone(complete.lease_token)
        self.assertNotIn("crm_oauth_pending", self.client.session)
        self.assertEqual(
            crm_connect_action_generation(
                journal, provider_key="hubspot", owner_session=owner_session
            ),
            1,
        )

    def test_abandon_uncertain_disconnect_keeps_generation_closed(self):
        """Uncertain remote disconnect must not claim abandon success."""
        from importer.command_service import crm_connect_action_generation

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        start = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=f"crm-connect:hubspot:{owner_session}",
            logical_action_generation=0,
            request_json={"provider_key": "hubspot", "owner_session": owner_session},
        )
        start.state = ApiMutation.State.COMPLETED
        start.response_json = {
            **self._start_body("crm_conn_live"),
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        start.save(update_fields=["state", "response_json"])
        complete = self._aged_unknown_complete(
            journal, owner_session=owner_session, connection_id="crm_conn_live"
        )

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        pending_body = {
            **self._complete_body("crm_conn_live"),
            "provider_key": "hubspot",
            "status": "pending",
        }

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connection",
                return_value=pending_body,
            ),
            patch.object(
                EasyImportsApiClient,
                "dispatch",
                side_effect=ApiUnavailableError("disconnect uncertain"),
            ),
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_abandon_uncertain_connection",
                    kwargs={"connection_id": "crm_conn_live"},
                )
            )
        self.assertEqual(response.status_code, 302)
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(complete.error_code, "oauth_abandon_pending_disconnect")
        self.assertIsNone(complete.lease_token)
        self.assertEqual(
            crm_connect_action_generation(
                journal, provider_key="hubspot", owner_session=owner_session
            ),
            0,
        )

    def test_abandon_claim_fails_if_callback_holds_lease(self):
        """Atomic claim must not clobber an in-flight OAuth complete lease."""
        from datetime import timedelta

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        complete = self._aged_unknown_complete(
            journal, owner_session=owner_session, connection_id="crm_conn_live"
        )
        # Simulate first callback acquiring the lease after eligibility check.
        worker_lease = uuid4()
        complete.lease_token = worker_lease
        complete.lease_expires_at = timezone.now() + timedelta(minutes=2)
        complete.save(update_fields=["lease_token", "lease_expires_at"])

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        response = self.client.post(
            reverse(
                "importer:crm_abandon_uncertain_connection",
                kwargs={"connection_id": "crm_conn_live"},
            )
        )
        self.assertEqual(response.status_code, 302)
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(complete.lease_token, worker_lease)

    def test_abandon_uncertain_oauth_blocked_while_lease_active(self):
        from datetime import timedelta

        owner = uuid4()
        owner_session = self._owner_session(owner)
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        complete = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route="/v1/crm/connections/crm_conn_live/oauth/complete",
            logical_action_identity=f"crm-complete:crm_conn_live:{owner_session}",
            request_json={
                "authorization_code_digest": "digest",
                "state": "state-live-1",
                "owner_session": owner_session,
            },
        )
        complete.state = ApiMutation.State.UNKNOWN
        complete.lease_token = uuid4()
        complete.lease_expires_at = timezone.now() + timedelta(minutes=2)
        complete.dispatched_at = timezone.now() - timedelta(minutes=5)
        complete.save(
            update_fields=[
                "state",
                "lease_token",
                "lease_expires_at",
                "dispatched_at",
            ]
        )

        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()

        response = self.client.post(
            reverse(
                "importer:crm_abandon_uncertain_connection",
                kwargs={"connection_id": "crm_conn_live"},
            )
        )
        self.assertEqual(response.status_code, 302)
        complete.refresh_from_db()
        self.assertEqual(complete.state, ApiMutation.State.UNKNOWN)
        self.assertIsNotNone(complete.lease_token)

    def test_reject_helper_does_not_reject_unleased_pending(self):
        """PENDING without a lease must not be auto-rejected (use == not is)."""
        from importer.connection_views import _reject_oauth_complete_for_reconnect

        owner = uuid4()
        journal = ImportSession.objects.create(
            owner_id=owner,
            operator_label="CRM connection",
            product_key="crm.connection",
        )
        mutation = create_or_reuse_mutation(
            session=journal,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route="/v1/crm/connections/x/oauth/complete",
            logical_action_identity=f"crm-complete:x:django-{owner}",
            request_json={"owner_session": f"django-{owner}"},
        )
        mutation.state = ApiMutation.State.PENDING
        mutation.lease_token = None
        mutation.lease_expires_at = None
        mutation.save(update_fields=["state", "lease_token", "lease_expires_at"])
        # Reload so state is a plain string as in production.
        mutation = ApiMutation.objects.get(pk=mutation.pk)
        self.assertFalse(_reject_oauth_complete_for_reconnect(mutation))
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)


class CrmDuplicateJourneyUiTests(TestCase):
    """Phase 4B: Django CRM journey forms, routes, and leakage guards."""

    def _start_form_token(self, *, connections=None, providers=None) -> str:
        """GET the redesigned start page and return its signed form_token."""

        import re

        connections = connections or {
            "connections": [
                {
                    "connection_id": "crm_conn_1",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                    "maximum_authorization": {
                        "reference_acquisition": "execute",
                        "duplicate_execution": "execute",
                    },
                }
            ]
        }
        providers = providers or {
            "providers": [{"provider_key": "fake", "provider_label": "Fake CRM"}]
        }
        with (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(
                EasyImportsApiClient, "crm_providers", return_value=providers
            ),
            patch.object(
                EasyImportsApiClient, "crm_connections", return_value=connections
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": []},
            ),
        ):
            response = self.client.get(reverse("importer:crm_duplicate_journey"))
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'name="form_token" value="([^"]+)"',
            response.content.decode("utf-8"),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def test_journey_page_renders_without_secrets_when_api_unavailable(self):
        with patch.object(
            EasyImportsApiClient,
            "assert_compatible",
            side_effect=ApiUnavailableError("API down"),
        ):
            response = self.client.get(reverse("importer:crm_duplicate_journey"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Resolve CRM duplicates", body)
        self.assertIn("Connect a CRM", body)
        self.assertNotIn("access_token", body)
        self.assertNotIn("refresh_token", body)
        self.assertNotIn("client_secret", body)

    def test_landing_and_nav_link_to_crm_journey(self):
        response = self.client.get(reverse("importer:landing"))
        body = response.content.decode("utf-8")
        self.assertIn(reverse("importer:crm_duplicate_journey"), body)
        self.assertIn("Resolve CRM duplicates", body)
        self.assertIn(reverse("importer:crm_connections"), body)

    def test_form_requires_candidate_groups_for_upload_mode(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        self.assertFalse(form.is_valid())
        # Advanced pre-grouped mode still requires its file/JSON input.
        self.assertIn("candidate_groups_file", form.errors)

    def test_form_accepts_valid_candidate_groups(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": json.dumps(
                    [{"group_id": "g1", "member_ids": ["A1", "A2", "A3"]}]
                ),
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["candidate_groups"],
            [{"group_id": "g1", "member_ids": ["A1", "A2", "A3"]}],
        )

    def test_form_fields_are_file_first_for_upload_mode(self):
        """Phase 4A: ordinary population file is primary on the start form."""

        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(connections=self._connected_fake_crm())
        field_names = list(form.fields.keys())
        self.assertIn("population_file", field_names)
        self.assertIn("form_token", field_names)
        self.assertLess(
            field_names.index("population_file"),
            field_names.index("candidate_groups_file"),
        )
        self.assertIn("CSV or Excel", form.fields["population_file"].label)
        self.assertNotIn("group_id", form.fields["population_file"].help_text)
        self.assertNotIn("member_id", form.fields["population_file"].help_text)

    def test_form_accepts_matching_remaining_groups_csv(self):
        from .forms import CrmDuplicateJourneyForm

        content = (
            "export_contract,source_connection_id,source_provider_key,"
            "entity_family,group_id,member_ids\n"
            "easyimports.deferred_duplicate_groups.v1,crm_conn_test,fake,"
            'company,g1,"[""A1"",""A2""]"\n'
        ).encode("utf-8")
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "deferred_duplicate_groups.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["candidate_groups"],
            [{"group_id": "g1", "member_ids": ["A1", "A2"]}],
        )

    def test_form_rejects_remaining_groups_csv_for_another_connection(self):
        from .forms import CrmDuplicateJourneyForm

        content = (
            "export_contract,source_connection_id,source_provider_key,"
            "entity_family,group_id,member_ids\n"
            "easyimports.deferred_duplicate_groups.v1,crm_conn_other,fake,"
            'company,g1,"[""A1"",""A2""]"\n'
        ).encode("utf-8")
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "deferred_duplicate_groups.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)

    def _connected_fake_crm(self):
        return [
            {
                "connection_id": "crm_conn_test",
                "provider_key": "fake",
                "provider_label": "Fake CRM",
                "status": "connected",
                "display_label": "Demo portal",
            }
        ]

    def _expected_candidate_groups(self):
        return [
            {"group_id": "g1", "member_ids": ["A1", "A2", "A3"]},
            {"group_id": "g2", "member_ids": ["B1", "B2"]},
        ]

    def _long_format_csv_bytes(self) -> bytes:
        return (
            "group_id,member_id,group_label\n"
            "g1,A1,Group One\n"
            "g1,A2,Group One\n"
            "g1,A3,Group One\n"
            "g2,B1,\n"
            "g2,B2,\n"
        ).encode("utf-8")

    def _long_format_xlsx_bytes(self) -> bytes:
        import io

        import pandas as pd

        frame = pd.DataFrame(
            [
                {"group_id": "g1", "member_id": "A1", "group_label": "Group One"},
                {"group_id": "g1", "member_id": "A2", "group_label": "Group One"},
                {"group_id": "g1", "member_id": "A3", "group_label": "Group One"},
                {"group_id": "g2", "member_id": "B1", "group_label": ""},
                {"group_id": "g2", "member_id": "B2", "group_label": ""},
            ]
        )
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False, engine="openpyxl")
        return buffer.getvalue()

    def test_form_accepts_long_format_candidate_csv(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    self._long_format_csv_bytes(),
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["candidate_groups"],
            self._expected_candidate_groups(),
        )

    def test_form_accepts_long_format_candidate_xlsx(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.xlsx",
                    self._long_format_xlsx_bytes(),
                    content_type=(
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["candidate_groups"],
            self._expected_candidate_groups(),
        )

    def test_form_csv_and_xlsx_match_equivalent_json(self):
        from .forms import CrmDuplicateJourneyForm

        expected = self._expected_candidate_groups()
        json_form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": json.dumps(expected),
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            connections=self._connected_fake_crm(),
        )
        csv_form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    self._long_format_csv_bytes(),
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        xlsx_form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.xlsx",
                    self._long_format_xlsx_bytes(),
                    content_type=(
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertTrue(json_form.is_valid(), json_form.errors)
        self.assertTrue(csv_form.is_valid(), csv_form.errors)
        self.assertTrue(xlsx_form.is_valid(), xlsx_form.errors)
        self.assertEqual(json_form.cleaned_data["candidate_groups"], expected)
        self.assertEqual(csv_form.cleaned_data["candidate_groups"], expected)
        self.assertEqual(xlsx_form.cleaned_data["candidate_groups"], expected)
        self.assertEqual(
            json.dumps(
                csv_form.cleaned_data["candidate_groups"],
                separators=(",", ":"),
            ),
            json.dumps(
                json_form.cleaned_data["candidate_groups"],
                separators=(",", ":"),
            ),
        )
        self.assertEqual(
            json.dumps(
                xlsx_form.cleaned_data["candidate_groups"],
                separators=(",", ":"),
            ),
            json.dumps(
                json_form.cleaned_data["candidate_groups"],
                separators=(",", ":"),
            ),
        )

    def test_form_rejects_long_format_missing_column(self):
        from .forms import CrmDuplicateJourneyForm

        content = b"group_id,member_label\ng1,A1\ng1,A2\n"
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn("member_id", str(form.errors["candidate_groups_file"]))

    def test_form_rejects_long_format_empty_member_cell(self):
        from .forms import CrmDuplicateJourneyForm

        content = b"group_id,member_id\ng1,A1\ng1,\n"
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn("empty member_id", str(form.errors["candidate_groups_file"]))

    def test_form_rejects_long_format_duplicate_member_in_group(self):
        from .forms import CrmDuplicateJourneyForm

        content = b"group_id,member_id\ng1,A1\ng1,A1\n"
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn("duplicate member ID", str(form.errors["candidate_groups_file"]))

    def test_form_rejects_long_format_duplicate_headers(self):
        from .forms import CrmDuplicateJourneyForm

        content = b"group_id,member_id,member_id\ng1,A1,A2\n"
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn(
            "duplicate column headers",
            str(form.errors["candidate_groups_file"]),
        )

    def test_form_rejects_long_format_duplicate_headers_xlsx(self):
        """Duplicate Excel headers must fail before pandas renames them."""

        import io

        import openpyxl
        from .forms import CrmDuplicateJourneyForm

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        # Two member_id columns — pandas would silently rename the second.
        sheet.append(["group_id", "member_id", "member_id"])
        sheet.append(["g1", "A1", "A2"])
        sheet.append(["g1", "A3", "A4"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.xlsx",
                    buffer.getvalue(),
                    content_type=(
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn(
            "duplicate column headers",
            str(form.errors["candidate_groups_file"]),
        )

    def test_form_accepts_whitespace_padded_xlsx_headers(self):
        """Stripped openpyxl headers must still resolve pandas cell values."""

        import io

        import openpyxl
        from .forms import CrmDuplicateJourneyForm

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append([" group_id ", " member_id ", " group_label "])
        sheet.append(["g1", "A1", "Alpha"])
        sheet.append(["g1", "A2", "Alpha"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.xlsx",
                    buffer.getvalue(),
                    content_type=(
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["candidate_groups"],
            [{"group_id": "g1", "member_ids": ["A1", "A2"]}],
        )

    def test_form_rejects_cross_group_member_reuse_in_spreadsheet(self):
        from .forms import CrmDuplicateJourneyForm

        content = (
            b"group_id,member_id\n"
            b"g1,A1\n"
            b"g1,A2\n"
            b"g2,A1\n"
            b"g2,B2\n"
        )
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn(
            "already appears in group",
            str(form.errors["candidate_groups_file"]),
        )

    def test_form_rejects_cross_group_member_reuse_in_json(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": json.dumps(
                    [
                        {"group_id": "g1", "member_ids": ["A1", "A2"]},
                        {"group_id": "g2", "member_ids": ["A1", "B2"]},
                    ]
                ),
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_json", form.errors)
        self.assertIn("A1", str(form.errors["candidate_groups_json"]))

    def test_form_accepts_group_label_column_without_emitting_it(self):
        """Optional group_label is legal input; Phase 1 does not emit it."""

        from .forms import (
            CANDIDATE_GROUPS_LONG_OPTIONAL_COLUMNS,
            CrmDuplicateJourneyForm,
        )

        self.assertIn("group_label", CANDIDATE_GROUPS_LONG_OPTIONAL_COLUMNS)
        content = (
            b"group_id,member_id,group_label\n"
            b"g1,A1,Alpha\n"
            b"g1,A2,Alpha\n"
        )
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        groups = form.cleaned_data["candidate_groups"]
        self.assertEqual(
            groups,
            [{"group_id": "g1", "member_ids": ["A1", "A2"]}],
        )
        self.assertNotIn("group_label", groups[0])

    def test_form_rejects_long_format_singleton_group(self):
        from .forms import CrmDuplicateJourneyForm

        content = b"group_id,member_id\ng1,A1\n"
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={
                "candidate_groups_file": SimpleUploadedFile(
                    "candidates.csv",
                    content,
                    content_type="text/csv",
                )
            },
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn("at least two", str(form.errors["candidate_groups_file"]))

    def test_form_rejects_oversized_candidate_upload(self):
        from .forms import CANDIDATE_GROUPS_UPLOAD_MAX_BYTES, CrmDuplicateJourneyForm

        # Avoid allocating a huge body: size is what clean() checks first.
        uploaded = SimpleUploadedFile(
            "candidates.csv",
            b"group_id,member_id\ng1,A1\ng1,A2\n",
            content_type="text/csv",
        )
        uploaded.size = CANDIDATE_GROUPS_UPLOAD_MAX_BYTES + 1
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "candidate_upload",
                "candidate_groups_json": "",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            files={"candidate_groups_file": uploaded},
            connections=self._connected_fake_crm(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_groups_file", form.errors)
        self.assertIn("10 MB", str(form.errors["candidate_groups_file"]))

    def test_form_ignores_stale_candidate_groups_when_mode_is_acquire_all(self):
        """Mode switch must not trap operators behind hidden upload validation."""

        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "candidate_groups_json": '[{"group_id": "g1", "member_ids": ["A1", "A2"]}]',
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["candidate_groups"])
        self.assertEqual(form.cleaned_data["selected_ids_list"], [])

    def test_form_accepts_acquire_all_without_upload_fields(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
            },
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["candidate_groups"])
        self.assertEqual(form.cleaned_data["selected_ids_list"], [])

    def test_primary_source_mode_choices_exclude_selected_ids(self):
        from .forms import CrmDuplicateJourneyForm

        form = CrmDuplicateJourneyForm(
            connections=[
                {
                    "connection_id": "crm_conn_test",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                }
            ],
        )
        choice_values = {value for value, _label in form.fields["source_mode"].choices}
        self.assertEqual(choice_values, {"acquire_all", "uploaded_population"})

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_journey_page_progressive_panels_and_find_first_defaults(self):
        connections = {
            "connections": [
                {
                    "connection_id": "crm_conn_1",
                    "provider_key": "fake",
                    "provider_label": "Fake CRM",
                    "status": "connected",
                    "display_label": "Demo portal",
                    "capabilities": {
                        "duplicate_resolution": True,
                        "same_object_merge": True,
                    },
                }
            ]
        }
        with (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value=connections,
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": []},
            ),
        ):
            response = self.client.get(reverse("importer:crm_duplicate_journey"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Where should EasyImports look?", body)
        self.assertIn('data-source-mode-panel="acquire_all"', body)
        self.assertIn('data-source-mode-panel="uploaded_population"', body)
        self.assertIn("No file", body)
        self.assertIn('name="form_token"', body)
        # selected_ids is not a primary radio option
        self.assertNotIn(
            "Selected CRM record IDs (H1-A; discovery after reread)", body
        )
        self.assertIn('value="acquire_all"', body)
        self.assertIn('value="uploaded_population"', body)
        self.assertIn("crm-duplicate-journey-form", body)
        # Phase 4A rem: no group_id/member_id or pre-grouped panels on primary page.
        self.assertIn("Records to analyze (CSV or Excel)", body)
        self.assertNotIn("group_id", body)
        self.assertNotIn("member_id", body)
        self.assertNotIn("Maximum CRM change level", body)
        self.assertNotIn('data-source-mode-panel="candidate_upload"', body)
        self.assertIn('accept=".csv,.xlsx"', body)
        file_pos = body.find('id="id_population_file"')
        self.assertNotEqual(file_pos, -1)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_journey_start_dispatches_api_and_opens_workflow_session(self):
        journey = {
            "journey_id": "crm_journey_abc",
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "entity_family": "company",
            "source_mode": "candidate_upload",
            "status": "workflow_started",
            "run_id": "run-journey-1",
            "target_provider_id": "fake-crm-v1",
            "candidate_group_count": 1,
            "acquired_record_count": 3,
            "acquisition": {
                "mode": "candidate_upload",
                "product_source_mode": "candidate_ids",
                "status": "pending_effect_authorization",
            },
            "next_steps": [],
            "workflow_receipt": None,
            "workflow_status": None,
            "workflow_stage": None,
        }
        workflow = {
            "run_id": "run-journey-1",
            "revision": 2,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "target_provider_id": "fake-crm-v1",
            "status": "awaiting_effect_authorization",
            "stage": "reference_acquisition",
            "decision": None,
            "effect_intent": {
                "intent_id": "intent-1",
                "track": "reference_acquisition",
                "supported_modes": ["execute"],
                "maximum_mode": "execute",
                "summary": {},
            },
            "effect_grants": [],
            "review_handoff": None,
            "terminal_evidence": None,
            "summary": {},
            "error": None,
            "links": {"self": "/v1/workflows/run-journey-1"},
        }
        connection = {
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "status": "connected",
            "display_label": "Demo portal",
            "authorization": None,
            "supported_entity_families": ["company", "person"],
            "supported_source_modes": ["candidate_upload", "acquire_all"],
            "capabilities": {
                "company": {
                    "supported_member_types": ["company"],
                    "same_object_merge": True,
                    "cross_object_resolution": False,
                },
                "person": {
                    "supported_member_types": ["contact"],
                    "same_object_merge": True,
                    "cross_object_resolution": False,
                },
            },
            "capability_profile_digest": "digest",
            "maximum_authorization": {
                "reference_acquisition": "execute",
                "duplicate_execution": "execute",
            },
        }

        def dispatch(mutation, **kwargs):
            self.assertEqual(mutation.mutation_kind, "crm_duplicate_journey_start")
            self.assertEqual(mutation.route, "/v1/crm/duplicate-journeys")
            body = mutation.request_json
            self.assertEqual(body["connection_id"], "crm_conn_1")
            self.assertEqual(body["entity_family"], "company")
            self.assertEqual(body["source_mode"], "candidate_upload")
            self.assertIn("owner_session", body)
            self.assertTrue(str(body["owner_session"]).startswith("django-"))
            return MutationDispatchResult(mutation, journey)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {
                            "provider_key": "fake",
                            "provider_label": "Fake CRM",
                            "connectable": True,
                            "authorization_modes": ["oauth_code"],
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": [connection]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": []},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(EasyImportsApiClient, "workflow", return_value=workflow),
        ):
            token = self._start_form_token(
                connections={"connections": [connection]}
            )
            response = self.client.post(
                reverse("importer:crm_duplicate_journey"),
                data={
                    "connection_id": "crm_conn_1",
                    "entity_family": "company",
                    "source_mode": "candidate_upload",
                    "candidate_groups_json": json.dumps(
                        [{"group_id": "company-group-1", "member_ids": ["A1", "A2", "A3"]}]
                    ),
                    "duplicate_execution_maximum": "dry_run",
                    "form_token": token,
                },
            )
        self.assertEqual(response.status_code, 302, response.content.decode("utf-8")[:500])
        session = ImportSession.objects.filter(
            product_key="easyimports.duplicate_resolution"
        ).latest("created_at")
        self.assertEqual(session.target_provider_id, "fake-crm-v1")
        self.assertEqual(session.options["crm_journey_id"], "crm_journey_abc")
        self.assertEqual(session.options["connection_id"], "crm_conn_1")
        self.assertEqual(session.active_workflow.run_id, "run-journey-1")
        self.assertEqual(
            session.active_workflow.status, "awaiting_effect_authorization"
        )
        self.assertIn(
            reverse("importer:workflow", args=[session.id]), response["Location"]
        )
        # Journal mutation retained owner session and journey kind.
        journal = ImportSession.objects.get(product_key="crm.journey")
        mutation = journal.api_mutations.get(
            mutation_kind="crm_duplicate_journey_start"
        )
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        # After dispatch mock we don't persist COMPLETED unless client does;
        # ensure no secrets leaked into stored request JSON.
        frozen = json.dumps(mutation.request_json)
        self.assertNotIn("access_token", frozen)
        self.assertNotIn("refresh_token", frozen)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_journey_start_from_candidate_csv_upload_dispatches_normalized_groups(self):
        """Phase 2 acceptance: multipart CSV → candidate_groups → workflow redirect."""

        expected_groups = [
            {"group_id": "company-group-1", "member_ids": ["A1", "A2", "A3"]},
        ]
        csv_bytes = (
            "group_id,member_id,group_label\n"
            "company-group-1,A1,Group One\n"
            "company-group-1,A2,Group One\n"
            "company-group-1,A3,Group One\n"
        ).encode("utf-8")
        journey = {
            "journey_id": "crm_journey_csv",
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "entity_family": "company",
            "source_mode": "candidate_upload",
            "status": "workflow_started",
            "run_id": "run-journey-csv",
            "target_provider_id": "fake-crm-v1",
            "candidate_group_count": 1,
            "acquired_record_count": 3,
            "acquisition": {
                "mode": "candidate_upload",
                "product_source_mode": "candidate_ids",
                "status": "pending_effect_authorization",
            },
            "next_steps": [],
            "workflow_receipt": None,
            "workflow_status": None,
            "workflow_stage": None,
        }
        workflow = {
            "run_id": "run-journey-csv",
            "revision": 2,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "target_provider_id": "fake-crm-v1",
            "status": "awaiting_effect_authorization",
            "stage": "reference_acquisition",
            "decision": None,
            "effect_intent": {
                "intent_id": "intent-csv-1",
                "track": "reference_acquisition",
                "supported_modes": ["execute"],
                "maximum_mode": "execute",
                "summary": {},
            },
            "effect_grants": [],
            "review_handoff": None,
            "terminal_evidence": None,
            "summary": {},
            "error": None,
            "links": {"self": "/v1/workflows/run-journey-csv"},
        }
        connection = {
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "status": "connected",
            "display_label": "Demo portal",
            "authorization": None,
            "supported_entity_families": ["company", "person"],
            "supported_source_modes": ["candidate_upload", "acquire_all"],
            "capabilities": {
                "company": {
                    "supported_member_types": ["company"],
                    "same_object_merge": True,
                    "cross_object_resolution": False,
                },
                "person": {
                    "supported_member_types": ["contact"],
                    "same_object_merge": True,
                    "cross_object_resolution": False,
                },
            },
            "capability_profile_digest": "digest",
            "maximum_authorization": {
                "reference_acquisition": "execute",
                "duplicate_execution": "execute",
            },
        }
        captured: dict = {}

        def dispatch(mutation, **kwargs):
            self.assertEqual(mutation.mutation_kind, "crm_duplicate_journey_start")
            self.assertEqual(mutation.route, "/v1/crm/duplicate-journeys")
            body = mutation.request_json
            captured["body"] = body
            self.assertEqual(body["connection_id"], "crm_conn_1")
            self.assertEqual(body["entity_family"], "company")
            self.assertEqual(body["source_mode"], "candidate_upload")
            self.assertEqual(body["candidate_groups"], expected_groups)
            self.assertNotIn("candidate_groups_json", body)
            self.assertIn("owner_session", body)
            self.assertTrue(str(body["owner_session"]).startswith("django-"))
            return MutationDispatchResult(mutation, journey)

        with (
            patch.object(
                EasyImportsApiClient,
                "assert_compatible",
                return_value={"status": "ok", "api_version": "1.14.1"},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {
                            "provider_key": "fake",
                            "provider_label": "Fake CRM",
                            "connectable": True,
                            "authorization_modes": ["oauth_code"],
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": [connection]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": []},
            ),
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch),
            patch.object(EasyImportsApiClient, "workflow", return_value=workflow),
        ):
            token = self._start_form_token(
                connections={"connections": [connection]}
            )
            response = self.client.post(
                reverse("importer:crm_duplicate_journey"),
                data={
                    "connection_id": "crm_conn_1",
                    "entity_family": "company",
                    "source_mode": "candidate_upload",
                    "candidate_groups_json": "",
                    "duplicate_execution_maximum": "dry_run",
                    "form_token": token,
                    "candidate_groups_file": SimpleUploadedFile(
                        "candidates.csv",
                        csv_bytes,
                        content_type="text/csv",
                    ),
                },
            )
        self.assertEqual(response.status_code, 302, response.content.decode("utf-8")[:500])
        self.assertEqual(
            captured["body"]["candidate_groups"],
            expected_groups,
        )
        session = ImportSession.objects.filter(
            product_key="easyimports.duplicate_resolution"
        ).latest("created_at")
        self.assertEqual(session.target_provider_id, "fake-crm-v1")
        self.assertEqual(session.options["crm_journey_id"], "crm_journey_csv")
        self.assertEqual(session.options["connection_id"], "crm_conn_1")
        self.assertEqual(session.active_workflow.run_id, "run-journey-csv")
        self.assertEqual(
            session.active_workflow.status, "awaiting_effect_authorization"
        )
        self.assertIn(
            reverse("importer:workflow", args=[session.id]), response["Location"]
        )
        journal = ImportSession.objects.get(product_key="crm.journey")
        mutation = journal.api_mutations.filter(
            mutation_kind="crm_duplicate_journey_start"
        ).latest("created_at")
        self.assertEqual(mutation.request_json["candidate_groups"], expected_groups)
        frozen = json.dumps(mutation.request_json)
        self.assertNotIn("access_token", frozen)
        self.assertNotIn("refresh_token", frozen)

    @override_settings(EASYIMPORTS_API_BASE_URL="https://api.example.test")
    def test_client_validates_journey_resource_on_dispatch(self):
        session = ImportSession.objects.create(
            owner_id=uuid4(), product_key="crm.journey"
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_duplicate_journey_start",
            route="/v1/crm/duplicate-journeys",
            logical_action_identity="crm-journey-test",
            request_json={
                "connection_id": "crm_conn_1",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "duplicate_execution_maximum": "dry_run",
                "form_token": "token",
                "owner_session": "django-owner",
            },
        )
        journey = {
            "journey_id": "crm_journey_x",
            "connection_id": "crm_conn_1",
            "provider_key": "fake",
            "provider_label": "Fake CRM",
            "entity_family": "company",
            "source_mode": "acquire_all",
            "status": "workflow_started",
            "run_id": "run-x",
            "target_provider_id": "fake-crm-v1",
            "candidate_group_count": 0,
            "acquired_record_count": 0,
            "acquisition": {"mode": "acquire_all"},
            "next_steps": [],
        }
        from .api_contract_generated import API_VERSION as DJANGO_API_VERSION

        http = Mock()
        http.get.return_value = response(
            200, {"status": "ok", "api_version": DJANGO_API_VERSION}
        )
        http.post.return_value = response(201, journey)
        api = EasyImportsApiClient(http=http)
        result = api.dispatch(mutation)
        self.assertEqual(result.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(result.response["journey_id"], "crm_journey_x")
        # Owner session is header-only, not body.
        posted = http.post.call_args
        self.assertEqual(posted.kwargs["headers"]["X-Owner-Session"], "django-owner")
        self.assertNotIn("owner_session", posted.kwargs["json"])
        self.assertNotIn("access_token", json.dumps(result.response))


class FrontendExecuteFeExecTests(TestCase):
    """FE-EXEC: Django authorize execute + terminal report (network-free)."""

    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def _session_workflow(self, value):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=value["workflow_key"],
            target_provider_id=value.get("target_provider_id") or "fake-crm-v1",
            operator_label="FE-EXEC Operator",
            options={"connection_id": "crm_conn_fe_exec", "crm_journey_id": "j1"},
        )
        workflow = ApiWorkflow.objects.create(
            session=session,
            role=ApiWorkflow.Role.PRIMARY,
            run_id=value["run_id"],
            workflow_key=value["workflow_key"],
            workflow_version=value["workflow_version"],
            target_provider_id=value.get("target_provider_id") or "fake-crm-v1",
            status=value["status"],
            stage=value["stage"],
            revision=value["revision"],
            resource_url=f"/v1/workflows/{value['run_id']}",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        session.active_workflow = workflow
        session.save(update_fields=["active_workflow"])
        return session, workflow

    def test_effect_form_offers_execute_and_freezes_selected_mode(self):
        intent = {
            "intent_id": "intent-exec-1",
            "track": "duplicate_execution",
            "gate_phase_id": "gate_duplicate_execution",
            "effect_phase_id": "execute_duplicate_execution",
            "maximum_mode": "execute",
            "supported_modes": ["disabled", "preview", "dry_run", "execute"],
            "target_provider_id": "fake-crm-v1",
            "target_fingerprint": "fp-fe-exec",
            "work_digest": "work-fe-exec",
            "confirmation": "Apply approved duplicate merges",
        }
        value = projection(
            run_id="run-fe-exec-effect",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-crm-v1",
            status="awaiting_effect_authorization",
            stage="duplicate_execution",
            effect_intent=intent,
            summary={"group_count": 1},
        )
        session, workflow = self._session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Apply approved merges in CRM")
        self.assertContains(page, "Test merges without changing CRM")
        self.assertContains(page, "Authorize merge step")
        modes = [m["value"] for m in page.context["effect_presentation"]["mode_options"]]
        self.assertIn("execute", modes)
        self.assertIn("dry_run", modes)

        def complete(mutation, **kwargs):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = {
                "command_id": "cmd-fe-exec",
                "command_kind": "authorize_effect",
                "run_id": workflow.run_id,
                "revision": workflow.revision + 1,
                "workflow_status": "succeeded",
                "stage": "complete",
                "outcome": "accepted",
                "error_code": None,
                "message": None,
                "resource": f"/v1/workflows/{workflow.run_id}",
            }
            mutation.save(update_fields=["state", "response_json"])
            return MutationDispatchResult(mutation, mutation.response_json)

        with (
            patch.object(EasyImportsApiClient, "dispatch", side_effect=complete),
            patch("importer.workflow_views.refresh_workflow", return_value=workflow),
        ):
            self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {
                    "form_token": page.context["tokens"]["effect"],
                    "selected_mode": "execute",
                },
            )
        mutation = session.api_mutations.get(mutation_kind="authorize_effect")
        self.assertEqual(mutation.request_json["selected_mode"], "execute")
        self.assertEqual(mutation.request_json["target_fingerprint"], "fp-fe-exec")
        self.assertEqual(mutation.request_json["work_digest"], "work-fe-exec")
        self.assertEqual(mutation.request_json["intent_id"], "intent-exec-1")
        frozen = json.dumps(mutation.request_json)
        self.assertNotIn("access_token", frozen)
        self.assertNotIn("refresh_token", frozen)

    def test_exact_replay_of_completed_execute_authorization_mutation(self):
        intent = {
            "intent_id": "intent-exec-replay",
            "track": "duplicate_execution",
            "gate_phase_id": "gate",
            "effect_phase_id": "effect",
            "maximum_mode": "execute",
            "supported_modes": ["disabled", "preview", "dry_run", "execute"],
            "target_provider_id": "fake-crm-v1",
            "target_fingerprint": "fp-replay",
            "work_digest": "work-replay",
            "confirmation": "Apply merges",
        }
        value = projection(
            run_id="run-fe-exec-replay",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-crm-v1",
            status="awaiting_effect_authorization",
            stage="duplicate_execution",
            effect_intent=intent,
        )
        session, workflow = self._session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["effect"]
        frozen_response = {
            "command_id": "cmd-replay",
            "command_kind": "authorize_effect",
            "run_id": workflow.run_id,
            "revision": 11,
            "workflow_status": "succeeded",
            "stage": "complete",
            "outcome": "accepted",
            "error_code": None,
            "message": None,
            "resource": f"/v1/workflows/{workflow.run_id}",
        }
        network_posts = {"n": 0}

        def dispatch_once(mutation, **kwargs):
            # Completed mutations replay frozen results without a new network write.
            if mutation.state == ApiMutation.State.COMPLETED:
                return MutationDispatchResult(mutation, mutation.response_json)
            network_posts["n"] += 1
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = frozen_response
            mutation.save(update_fields=["state", "response_json"])
            return MutationDispatchResult(mutation, frozen_response)

        with (
            patch.object(EasyImportsApiClient, "dispatch", side_effect=dispatch_once),
            patch("importer.workflow_views.refresh_workflow", return_value=workflow),
        ):
            first = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "execute"},
            )
            second = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "execute"},
            )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(network_posts["n"], 1)
        self.assertEqual(
            session.api_mutations.filter(mutation_kind="authorize_effect").count(), 1
        )
        mutation = session.api_mutations.get(mutation_kind="authorize_effect")
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.response_json, frozen_response)
        self.assertEqual(mutation.request_json["selected_mode"], "execute")

    def test_terminal_after_execute_shows_crm_changes_and_group_cards(self):
        terminal = {
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "entity": "account",
            "receipts": [
                {
                    "track": "reference_acquisition",
                    "outcome": "succeeded",
                    "authorization": "execute",
                    "capability": "crm_reference_acquisition",
                    "reason": None,
                    "evidence": {},
                },
                {
                    "track": "duplicate_execution",
                    "outcome": "executed",
                    "authorization": "execute",
                    "capability": "account_duplicate_execution",
                    "reason": None,
                    "evidence": {"merged_group_count": 1},
                },
            ],
            "accountability": {
                "dispositions": [
                    {
                        "duplicate_group_id": "g1",
                        "status": "merged",
                        "member_ids": ["A1", "A2", "A3"],
                        "selected_survivor_id": "A1",
                        "action_ids": ["act-1", "act-2"],
                        "verified_action_ids": ["act-1", "act-2"],
                        "error_code": None,
                        "error_message": None,
                    }
                ],
                "reconciles": True,
            },
        }
        value = projection(
            run_id="run-fe-exec-terminal",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-crm-v1",
            status="succeeded",
            stage="complete",
            terminal_evidence=terminal,
        )
        session, workflow = self._session_workflow(value)
        with (
            patch("importer.workflow_views.refresh_workflow", return_value=workflow),
            patch.object(
                EasyImportsApiClient, "artifacts", return_value={"artifacts": []}
            ),
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Groups processed")
        self.assertContains(page, "Completed")
        # Execute receipt on a mutating track must not claim "no CRM changes".
        self.assertNotContains(
            page, "No live CRM changes were authorized for this run."
        )
        presentation = page.context["terminal_presentation"]
        self.assertFalse(presentation["no_crm_changes"])
        self.assertEqual(presentation["reconciles"], True)
        body = page.content.decode("utf-8")
        self.assertNotIn("access_token", body)
        self.assertNotIn("refresh_token", body)
        self.assertNotIn("client_secret", body)

    def test_stale_effect_token_does_not_dispatch_execute(self):
        intent = {
            "intent_id": "intent-stale",
            "track": "duplicate_execution",
            "gate_phase_id": "gate",
            "effect_phase_id": "effect",
            "maximum_mode": "execute",
            "supported_modes": ["disabled", "preview", "dry_run", "execute"],
            "target_provider_id": "fake-crm-v1",
            "target_fingerprint": "fp-stale",
            "work_digest": "work-stale",
            "confirmation": "Apply",
        }
        value = projection(
            run_id="run-fe-exec-stale-token",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-crm-v1",
            status="awaiting_effect_authorization",
            stage="duplicate_execution",
            effect_intent=intent,
        )
        session, workflow = self._session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["effect"]
        workflow.revision = workflow.revision + 1
        workflow.projection_digest = "changed"
        workflow.save(update_fields=["revision", "projection_digest"])
        with patch.object(
            EasyImportsApiClient, "dispatch", side_effect=AssertionError("no dispatch")
        ):
            self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "execute"},
            )
        self.assertFalse(
            session.api_mutations.filter(mutation_kind="authorize_effect").exists()
        )

    def test_paused_unknown_after_execute_surfaces_resume_control(self):
        """FE-EXEC: paused_unknown must offer resume, not a silent terminal success."""

        value = projection(
            run_id="run-fe-exec-paused",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-crm-v1",
            status="paused_unknown",
            stage="duplicate_execution",
            effect_intent=None,
            terminal_evidence=None,
            summary={"paused_reason": "lost_response"},
        )
        session, workflow = self._session_workflow(value)
        with (
            patch("importer.workflow_views.refresh_workflow", return_value=workflow),
            patch.object(
                EasyImportsApiClient, "artifacts", return_value={"artifacts": []}
            ),
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Effect checkpoint paused")
        self.assertContains(page, "Verify and continue")
        self.assertIn("resume", page.context["tokens"])
        # Must not claim a finished CRM execute report while paused.
        self.assertIsNone(page.context.get("terminal") or None)
        self.assertNotContains(page, "Import complete")

    def test_partial_execute_terminal_surfaces_attributable_failure(self):
        """FE-EXEC: partial execute terminal must not look like a clean no-op."""

        terminal = {
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "entity": "account",
            "receipts": [
                {
                    "track": "duplicate_execution",
                    "outcome": "completed_with_failures",
                    "authorization": "execute",
                    "capability": "account_duplicate_execution",
                    "reason": None,
                    "evidence": {"failed_action_count": 1},
                }
            ],
            "accountability": {
                "dispositions": [
                    {
                        "duplicate_group_id": "g1",
                        "status": "completed_with_failures",
                        "member_ids": ["A1", "A2", "A3"],
                        "selected_survivor_id": "A1",
                        "action_ids": ["act-1", "act-2"],
                        "verified_action_ids": ["act-1"],
                        "error_code": "FakeCrmError",
                        "error_message": "Attributable merge failure for 'A2'.",
                    }
                ],
                "reconciles": False,
            },
        }
        value = projection(
            run_id="run-fe-exec-partial",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            target_provider_id="fake-crm-v1",
            status="succeeded",
            stage="complete",
            terminal_evidence=terminal,
        )
        session, workflow = self._session_workflow(value)
        with (
            patch("importer.workflow_views.refresh_workflow", return_value=workflow),
            patch.object(
                EasyImportsApiClient, "artifacts", return_value={"artifacts": []}
            ),
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        presentation = page.context["terminal_presentation"]
        self.assertFalse(presentation["no_crm_changes"])
        self.assertEqual(presentation["reconciles"], False)
        self.assertContains(page, "Completed with issues")
        self.assertContains(page, "Groups processed")
        # Technical details retain attributable failure evidence.
        self.assertContains(page, "FakeCrmError")
        self.assertContains(page, "Attributable merge failure")
