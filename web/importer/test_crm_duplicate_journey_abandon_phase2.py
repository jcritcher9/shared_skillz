from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .api_client import EasyImportsApiClient
from .journey_views import (
    _ABANDON_SAFE_CONFIRMATION,
    _ABANDON_UNCERTAIN_CONFIRMATION,
    _CONNECTION_REMOVAL_QUARANTINE_COPY,
    _progress_message,
)
from .models import ApiWorkflow, CrmDuplicateMergePlanLease, ImportSession
from .workflow_state import projection_digest


def _journey(
    *,
    journey_id: str = "crm_journey_phase2",
    run_id: str = "run_phase2",
    workflow_status: str = "running",
) -> dict:
    return {
        "journey_id": journey_id,
        "run_id": run_id,
        "provider_key": "fake",
        "provider_label": "Practice CRM",
        "connection_id": "crm_conn_phase2",
        "entity_family": "company",
        "source_mode": "acquire_all",
        "status": "workflow_started",
        "workflow_status": workflow_status,
    }


def _projection(
    *,
    run_id: str = "run_phase2",
    revision: int = 7,
    status: str = "running",
    remote_outcome: str | None = "none",
    error: dict | None = None,
    effect_intent: dict | None = None,
    effect_grants: list[dict] | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "revision": revision,
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 4,
        "status": status,
        "stage": status,
        "summary": {},
        "decision": None,
        "review_handoff": None,
        "effect_intent": effect_intent,
        "effect_grants": [] if effect_grants is None else effect_grants,
        "target_provider_id": "fake",
        "remote_outcome": remote_outcome,
        "error": error,
    }


class CrmDuplicateJourneyAbandonPhase2Tests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()
        self.local = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            options={
                "crm_journey_id": "crm_journey_phase2",
                "run_id": "run_phase2",
                "source_run_id": "run_phase2",
            },
        )

    def _confirmation_get(self, projection: dict):
        route = reverse(
            "importer:crm_duplicate_journey_abandon",
            kwargs={"journey_id": "crm_journey_phase2"},
        )
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journey",
                return_value=_journey(),
            ),
            patch.object(EasyImportsApiClient, "workflow", return_value=projection),
        ):
            return self.client.get(route)

    def test_safe_confirmation_requires_proven_none_and_source_run(self):
        response = self._confirmation_get(_projection(remote_outcome="none"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _ABANDON_SAFE_CONFIRMATION)
        self.assertNotContains(response, _ABANDON_UNCERTAIN_CONFIRMATION)
        self.assertContains(response, "Abandon this journey")
        self.assertContains(response, "Keep this journey")

    def test_unknown_or_write_authorized_run_uses_uncertain_copy(self):
        unknown = self._confirmation_get(_projection(remote_outcome="unknown"))
        self.assertEqual(unknown.status_code, 200)
        self.assertContains(unknown, _ABANDON_UNCERTAIN_CONFIRMATION)
        self.assertNotContains(unknown, "No groups will be merged")

        write_intent = {
            "track": "duplicate_execution",
            "intent_id": "intent-1",
        }
        write = self._confirmation_get(
            _projection(remote_outcome="none", effect_intent=write_intent)
        )
        self.assertContains(write, _ABANDON_UNCERTAIN_CONFIRMATION)
        self.assertNotContains(write, "No groups will be merged")

        unreadable_grant = self._confirmation_get(
            _projection(remote_outcome="none", effect_grants=[{}])
        )
        self.assertContains(unreadable_grant, _ABANDON_UNCERTAIN_CONFIRMATION)
        self.assertNotContains(unreadable_grant, "No groups will be merged")

        CrmDuplicateMergePlanLease.objects.create(
            session=self.local,
            continuation_run_id="run_phase2",
        )
        continuation = self._confirmation_get(_projection(remote_outcome="none"))
        self.assertContains(continuation, _ABANDON_UNCERTAIN_CONFIRMATION)
        self.assertNotContains(continuation, "No groups will be merged")

    def test_running_review_and_uncertain_statuses_are_abandonable(self):
        for status in ("running", "awaiting_review", "paused_unknown"):
            with self.subTest(status=status):
                response = self._confirmation_get(
                    _projection(status=status, remote_outcome="unknown")
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Abandon this journey")
                self.assertContains(response, _ABANDON_UNCERTAIN_CONFIRMATION)

    def test_post_dispatches_revision_fenced_abandon_command(self):
        get_response = self._confirmation_get(_projection(revision=11))
        token = get_response.context["form_token"]
        route = reverse(
            "importer:crm_duplicate_journey_abandon",
            kwargs={"journey_id": "crm_journey_phase2"},
        )
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journey",
                return_value=_journey(),
            ),
            patch(
                "importer.journey_views._dispatch_json_mutation",
                return_value={
                    "outcome": "accepted",
                    "command_kind": "abandon_run",
                    "run_id": "run_phase2",
                },
            ) as dispatch,
        ):
            response = self.client.post(route, {"form_token": token})

        self.assertRedirects(
            response,
            reverse("importer:crm_duplicate_journey"),
            fetch_redirect_response=False,
        )
        call = dispatch.call_args.kwargs
        self.assertEqual(call["mutation_kind"], "abandon_run")
        self.assertEqual(call["route"], "/v1/workflows/run_phase2/abandon")
        self.assertEqual(call["body"], {"expected_revision": 11})
        self.assertEqual(call["resource_identity"], "run_phase2")

    def test_list_and_progress_present_operator_stop_reasons_distinctly(self):
        abandoned = _journey(
            journey_id="journey-abandoned",
            run_id="run-abandoned",
            workflow_status="failed",
        )
        removed = _journey(
            journey_id="journey-removed",
            run_id="run-removed",
            workflow_status="failed",
        )
        quarantined = _journey(
            journey_id="journey-quarantined",
            run_id="run-quarantined",
            workflow_status="unavailable",
        )
        quarantined["connection_removal_quarantine"] = True
        abandoned_projection = _projection(
            run_id="run-abandoned",
            status="failed",
            error={
                "code": "abandoned_by_operator",
                "message": "This analysis was stopped.",
            },
        )
        removed_projection = _projection(
            run_id="run-removed",
            status="failed",
            error={
                "code": "crm_connection_removed_by_operator",
                "message": "Stopped because its CRM connection was removed.",
            },
        )
        with (
            patch.object(
                EasyImportsApiClient, "crm_providers", return_value={"providers": []}
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_duplicate_journeys",
                return_value={"journeys": [abandoned, removed, quarantined]},
            ),
            patch.object(
                EasyImportsApiClient,
                "workflow",
                side_effect=[abandoned_projection, removed_projection],
            ),
        ):
            response = self.client.get(reverse("importer:crm_duplicate_journey"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This analysis was stopped.")
        self.assertContains(response, "Stopped because its CRM connection was removed.")
        self.assertContains(response, _CONNECTION_REMOVAL_QUARANTINE_COPY)
        self.assertEqual(
            _progress_message(abandoned_projection), "This analysis was stopped."
        )
        self.assertEqual(
            _progress_message(removed_projection),
            "Stopped because its CRM connection was removed.",
        )
        self.assertEqual(
            _progress_message({"connection_removal_quarantine": True}),
            _CONNECTION_REMOVAL_QUARANTINE_COPY,
        )

    def test_clear_from_list_does_not_change_workflow_status(self):
        projection = _projection(status="running")
        workflow = ApiWorkflow.objects.create(
            session=self.local,
            run_id="run_phase2",
            workflow_key=projection["workflow_key"],
            workflow_version=projection["workflow_version"],
            target_provider_id="fake",
            status="running",
            stage="running",
            revision=projection["revision"],
            resource_url="/v1/workflows/run_phase2",
            projection=projection,
            projection_digest=projection_digest(projection),
        )

        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=AssertionError("Clear must not dispatch an API command."),
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_duplicate_journey_clear",
                    kwargs={"journey_id": "crm_journey_phase2"},
                )
            )

        self.assertEqual(response.status_code, 302)
        workflow.refresh_from_db()
        self.assertEqual(workflow.status, "running")
        self.assertEqual(workflow.projection["status"], "running")
