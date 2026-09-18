from __future__ import annotations

"""SCR-2: render typed paused diagnostics on the CRM-dupe progress page."""

from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from .api_client import EasyImportsApiClient
from .models import ImportSession

DIAGNOSTICS = (
    {
        "code": "reference_acquisition_saved_progress_reset_required",
        "error_type": "ReferenceAcquisitionError",
        "message": (
            "This scan's saved read progress is incompatible or damaged and "
            "cannot be resumed safely. Clear the saved scan data and retry "
            "from the beginning."
        ),
        "win32_code": None,
        "win32_code_hex": None,
    },
    {
        "code": "reference_acquisition_state_store_unavailable",
        "error_type": "ReferenceAcquisitionError",
        "message": (
            "EasyImports cannot read the scan-progress store. Clearing one "
            "scan is not safe. Contact support; reconnecting the CRM will not "
            "fix this."
        ),
        "win32_code": None,
        "win32_code_hex": None,
    },
    {
        "code": "reference_acquisition_adapter_contract_failed",
        "error_type": "ReferenceAcquisitionError",
        "message": (
            "New CRM records could not be stored safely for resumable "
            "scanning. Retrying or clearing saved scan data may repeat the "
            "problem. Contact support."
        ),
        "win32_code": None,
        "win32_code_hex": None,
    },
)


class Scr2CrmDuplicateProgressDiagnosticTests(TestCase):
    def test_each_typed_diagnostic_message_keeps_exact_scr3c_action_gating(self):
        for index, diagnostic in enumerate(DIAGNOSTICS):
            with self.subTest(code=diagnostic["code"]):
                response = self._get_progress(
                    run_id=f"run-scr2-{index}",
                    diagnostic=diagnostic,
                )
                body = response.content.decode("utf-8")

                self.assertEqual(response.status_code, 200)
                self.assertIn(escape(diagnostic["message"]), body)
                self.assertIn("paused-effect-diagnostic", body)
                self.assertIn(
                    "Workflow status: <code>paused_unknown</code>",
                    body,
                )
                self.assertIn("Connected CRM confirmed", body)
                self.assertIn("Read active CRM records", body)
                self.assertIn("Discover duplicate groups", body)
                self.assertIn("Open review", body)
                if diagnostic["code"] == (
                    "reference_acquisition_saved_progress_reset_required"
                ):
                    self.assertIn("Clear stale scan data and retry", body)
                    self.assertIn("recover-saved-progress", body)
                else:
                    self.assertNotIn("Clear stale scan data and retry", body)
                    self.assertNotIn("recover-saved-progress", body)
                self.assertNotIn("reference-acquisition-recoveries", body)
                self.assertNotIn(diagnostic["code"], body)

    def test_no_diagnostic_preserves_existing_status_and_step_list(self):
        response = self._get_progress(
            run_id="run-scr2-none",
            diagnostic=None,
        )
        body = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("paused-effect-diagnostic", body)
        self.assertIn("Workflow status: <code>paused_unknown</code>", body)
        self.assertIn("Connected CRM confirmed", body)
        self.assertIn("Read active CRM records", body)
        self.assertIn("Discover duplicate groups", body)
        self.assertIn("Open review", body)

    def _get_progress(self, *, run_id: str, diagnostic: dict | None):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={"run_id": run_id, "entity_family": "company"},
        )
        projection = {
            "run_id": run_id,
            "revision": 3,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 8,
            "status": "paused_unknown",
            "stage": "paused_unknown",
            "summary": {},
            "decision": None,
            "review_handoff": None,
            "effect_intent": None,
            "effect_grants": [],
            "target_provider_id": "fake",
            "paused_effect_diagnostic": diagnostic,
            "reference_acquisition_progress": None,
        }
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with (
            patch.object(EasyImportsApiClient, "assert_compatible", return_value=None),
            patch.object(EasyImportsApiClient, "workflow", return_value=projection),
        ):
            return self.client.get(route)
