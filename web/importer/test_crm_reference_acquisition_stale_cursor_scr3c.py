from __future__ import annotations

"""SCR-3C: confirmed, exact-retry UI for clearable acquisition state."""

from unittest.mock import patch
from uuid import uuid4

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .api_client import (
    ApiOperationInProgressError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationDispatchResult,
)
from .journey_views import (
    REFERENCE_ACQUISITION_RECOVERY_KIND,
    REFERENCE_ACQUISITION_RECOVERY_SUCCESS,
    REFERENCE_ACQUISITION_RESET_REQUIRED,
)
from .models import ApiMutation, ImportSession


def _diagnostic(code: str) -> dict:
    return {
        "code": code,
        "error_type": "ReferenceAcquisitionError",
        "message": f"Diagnostic for {code}.",
        "win32_code": None,
        "win32_code_hex": None,
    }


def _projection(
    *,
    revision: int = 9,
    status: str = "paused_unknown",
    diagnostic_code: str | None = REFERENCE_ACQUISITION_RESET_REQUIRED,
) -> dict:
    return {
        "run_id": "run-scr3c",
        "revision": revision,
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 8,
        "status": status,
        "stage": status,
        "summary": {},
        "decision": None,
        "review_handoff": None,
        "effect_intent": None,
        "effect_grants": [],
        "target_provider_id": "fake",
        "paused_effect_diagnostic": (
            None if diagnostic_code is None else _diagnostic(diagnostic_code)
        ),
        "reference_acquisition_progress": None,
    }


def _receipt() -> dict:
    return {
        "command_id": "cmd-scr3c",
        "command_kind": REFERENCE_ACQUISITION_RECOVERY_KIND,
        "run_id": "run-scr3c",
        "revision": 12,
        "workflow_status": "running",
        "stage": "reference_acquisition",
        "outcome": "accepted",
        "error_code": None,
        "message": None,
        "resource": "/v1/workflows/run-scr3c",
        "result": {
            "recovery_contract": "easyimports.reference_acquisition.recovery.v1",
            "state_reset": True,
            "resume_dispatched": True,
        },
        "remote_outcome": None,
    }


class Scr3cCrmDuplicateRecoveryTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()
        self.workflow_session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-scr3c",
                "crm_journey_id": "journey-scr3c",
                "entity_family": "company",
            },
        )
        self.progress_route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": self.workflow_session.id},
        )
        self.recovery_route = reverse(
            "importer:crm_duplicate_reference_acquisition_recovery",
            kwargs={"session_id": self.workflow_session.id},
        )

    def _progress(self, projection: dict):
        with (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(EasyImportsApiClient, "workflow", return_value=projection),
        ):
            return self.client.get(self.progress_route)

    def _confirmation(self, projection: dict):
        with patch.object(
            EasyImportsApiClient,
            "workflow",
            return_value=projection,
        ):
            return self.client.get(self.recovery_route)

    def test_progress_shows_action_only_for_exact_clearable_diagnostic(self):
        clearable = self._progress(_projection())
        self.assertEqual(clearable.status_code, 200)
        self.assertContains(clearable, "Clear stale scan data and retry")
        self.assertContains(clearable, self.recovery_route)
        self.assertNotContains(clearable, "reference-acquisition-recoveries")

        for code, status in (
            ("reference_acquisition_state_store_unavailable", "paused_unknown"),
            ("reference_acquisition_adapter_contract_failed", "paused_unknown"),
            ("generic_paused_effect", "paused_unknown"),
            (None, "running"),
        ):
            with self.subTest(code=code, status=status):
                response = self._progress(
                    _projection(status=status, diagnostic_code=code)
                )
                self.assertNotContains(response, "Clear stale scan data and retry")
                self.assertNotContains(response, "recover-saved-progress")

    def test_confirmation_get_is_read_only_and_rechecks_clearable_code(self):
        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=AssertionError("GET must not dispatch a mutation."),
        ):
            response = self._confirmation(_projection(revision=11))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clear saved scan data and retry?")
        self.assertContains(response, "CRM records are not changed")
        self.assertEqual(ApiMutation.objects.count(), 0)

        changed = self._confirmation(
            _projection(
                revision=12,
                diagnostic_code="reference_acquisition_state_store_unavailable",
            )
        )
        self.assertRedirects(
            changed,
            self.progress_route,
            fetch_redirect_response=False,
        )
        self.assertEqual(ApiMutation.objects.count(), 0)

    def test_confirmed_post_dispatches_only_combined_recovery_command(self):
        confirmation = self._confirmation(_projection(revision=13))
        token = confirmation.context["form_token"]
        with patch(
            "importer.journey_views._dispatch_json_mutation",
            return_value=_receipt(),
        ) as dispatch:
            response = self.client.post(
                self.recovery_route,
                {"form_token": token},
            )

        call = dispatch.call_args.kwargs
        self.assertEqual(
            call["route"],
            "/v1/workflows/run-scr3c/reference-acquisition-recoveries",
        )
        self.assertEqual(call["mutation_kind"], REFERENCE_ACQUISITION_RECOVERY_KIND)
        self.assertEqual(call["body"], {"expected_revision": 13})
        self.assertEqual(call["resource_identity"], "run-scr3c")
        self.assertNotIn("effect-resumptions", call["route"])
        self.assertRedirects(
            response,
            self.progress_route,
            fetch_redirect_response=False,
        )
        self.assertIn(
            REFERENCE_ACQUISITION_RECOVERY_SUCCESS,
            [str(message) for message in get_messages(response.wsgi_request)],
        )

    def test_stale_revision_rejection_fails_closed_without_success_copy(self):
        confirmation = self._confirmation(_projection(revision=14))
        token = confirmation.context["form_token"]
        with patch(
            "importer.journey_views._dispatch_json_mutation",
            side_effect=ApiRejectedError(
                "reference_acquisition_recovery_not_current",
                "This recovery is no longer current.",
            ),
        ):
            response = self.client.post(
                self.recovery_route,
                {"form_token": token},
            )

        self.assertRedirects(
            response,
            self.recovery_route,
            fetch_redirect_response=False,
        )
        self.assertEqual(ApiMutation.objects.count(), 0)

    def test_unknown_exact_retry_reuses_one_saved_mutation(self):
        confirmation = self._confirmation(_projection(revision=15))
        first_token = confirmation.context["form_token"]
        mutation_ids = []
        explicit_retry_values = []

        def uncertain_then_complete(mutation, *, explicit_retry=False, **_kwargs):
            mutation_ids.append(mutation.id)
            explicit_retry_values.append(explicit_retry)
            if len(mutation_ids) == 1:
                mutation.state = ApiMutation.State.UNKNOWN
                mutation.lease_token = None
                mutation.lease_expires_at = None
                mutation.save(
                    update_fields=[
                        "state",
                        "lease_token",
                        "lease_expires_at",
                        "updated_at",
                    ]
                )
                raise ApiUnavailableError("The API response is uncertain.")
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = _receipt()
            mutation.save(update_fields=["state", "response_json", "updated_at"])
            return MutationDispatchResult(mutation, _receipt())

        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=uncertain_then_complete,
        ):
            first = self.client.post(
                self.recovery_route,
                {"form_token": first_token},
            )
            self.assertRedirects(
                first,
                self.recovery_route,
                fetch_redirect_response=False,
            )
            retry_confirmation = self.client.get(self.recovery_route)
            self.assertEqual(retry_confirmation.status_code, 200)
            self.assertContains(retry_confirmation, "exact action")
            retry_token = retry_confirmation.context["form_token"]
            retried = self.client.post(
                self.recovery_route,
                {"form_token": retry_token},
            )

        self.assertEqual(mutation_ids[0], mutation_ids[1])
        self.assertEqual(explicit_retry_values, [False, True])
        self.assertEqual(ApiMutation.objects.count(), 1)
        mutation = ApiMutation.objects.get()
        self.assertEqual(mutation.idempotency_key, f"web-{mutation.id.hex}")
        self.assertEqual(mutation.request_json["expected_revision"], 15)
        self.assertRedirects(
            retried,
            self.progress_route,
            fetch_redirect_response=False,
        )
        self.assertIn(
            REFERENCE_ACQUISITION_RECOVERY_SUCCESS,
            [str(message) for message in get_messages(retried.wsgi_request)],
        )

    def test_pending_exact_retry_reuses_one_saved_mutation(self):
        confirmation = self._confirmation(_projection(revision=16))
        first_token = confirmation.context["form_token"]
        mutation_ids = []
        explicit_retry_values = []

        def pending_then_complete(mutation, *, explicit_retry=False, **_kwargs):
            mutation_ids.append(mutation.id)
            explicit_retry_values.append(explicit_retry)
            if len(mutation_ids) == 1:
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
                    "The saved-progress recovery is still processing."
                )
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = _receipt()
            mutation.save(update_fields=["state", "response_json", "updated_at"])
            return MutationDispatchResult(mutation, _receipt())

        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=pending_then_complete,
        ):
            first = self.client.post(
                self.recovery_route,
                {"form_token": first_token},
            )
            self.assertRedirects(
                first,
                self.recovery_route,
                fetch_redirect_response=False,
            )
            retry_confirmation = self.client.get(self.recovery_route)
            self.assertEqual(retry_confirmation.status_code, 200)
            self.assertContains(retry_confirmation, "exact action")
            retry_token = retry_confirmation.context["form_token"]
            retried = self.client.post(
                self.recovery_route,
                {"form_token": retry_token},
            )

        self.assertEqual(mutation_ids[0], mutation_ids[1])
        self.assertEqual(explicit_retry_values, [False, False])
        self.assertEqual(ApiMutation.objects.count(), 1)
        mutation = ApiMutation.objects.get()
        self.assertEqual(mutation.idempotency_key, f"web-{mutation.id.hex}")
        self.assertEqual(mutation.request_json["expected_revision"], 16)
        self.assertRedirects(
            retried,
            self.progress_route,
            fetch_redirect_response=False,
        )
        self.assertIn(
            REFERENCE_ACQUISITION_RECOVERY_SUCCESS,
            [str(message) for message in get_messages(retried.wsgi_request)],
        )
