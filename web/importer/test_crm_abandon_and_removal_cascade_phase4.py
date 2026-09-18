from __future__ import annotations

"""Phase 4 Django warning-copy closure for confirmed CRM removal."""

from html import unescape
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse


def _three_run_preview(connection_id: str) -> dict:
    statuses = ("awaiting_review", "running", "paused_unknown")
    return {
        "connection_id": connection_id,
        "runs": [
            {
                "run_id": f"job_phase4_{status}",
                "run_status": status,
                "checkpoint_revision": index + 7,
                "remote_outcome": "unknown" if status == "paused_unknown" else "none",
                "dependents": [
                    {
                        "type": "duplicate-resolution journey",
                        "id": f"crm_journey_phase4_{status}",
                    },
                    {"type": "workflow checkpoint", "id": f"job_phase4_{status}"},
                ],
            }
            for index, status in enumerate(statuses)
        ],
        "orphaned_dependents": [],
        "confirmation_digest": "4" * 64,
    }


class CrmAbandonRemovalCascadePhase4Tests(TestCase):
    def setUp(self):
        session = self.client.session
        session["easyimports_owner_id"] = str(uuid4())
        session.save()
        self.connection_id = "crm_conn_phase4_three"

    def test_warn_screen_names_exact_three_run_repro(self):
        preview = _three_run_preview(self.connection_id)
        with patch(
            "importer.connection_views.EasyImportsApiClient.crm_connection_removal_dependents",
            return_value=preview,
        ):
            response = self.client.get(
                reverse(
                    "importer:crm_connection_remove_confirm",
                    kwargs={"connection_id": self.connection_id},
                )
            )

        self.assertEqual(response.status_code, 200)
        body = unescape(response.content.decode("utf-8"))
        for status in ("awaiting_review", "running", "paused_unknown"):
            self.assertIn(f"job_phase4_{status}", body)
            self.assertIn(f"crm_journey_phase4_{status}", body)
            self.assertIn(status, body)
        self.assertNotIn("no loadable analysis checkpoint", body)

    def test_uncertain_removal_copy_never_promises_zero_crm_changes(self):
        preview = _three_run_preview(self.connection_id)
        with patch(
            "importer.connection_views.EasyImportsApiClient.crm_connection_removal_dependents",
            return_value=preview,
        ):
            response = self.client.get(
                reverse(
                    "importer:crm_connection_remove_confirm",
                    kwargs={"connection_id": self.connection_id},
                )
            )

        body = unescape(response.content.decode("utf-8"))
        self.assertIn("cannot recall it", body)
        self.assertIn("remote outcome may be uncertain", body)
        self.assertNotIn("No groups will be merged", body)
        self.assertNotIn("no CRM changes occurred", body)
