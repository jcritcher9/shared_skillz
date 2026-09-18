from __future__ import annotations

import re
from html import unescape
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .api_client import MutationDispatchResult
from .models import ApiMutation, ImportSession


def _preview(connection_id: str):
    return {
        "connection_id": connection_id,
        "runs": [
            {
                "run_id": "job_shared",
                "run_status": "paused_unknown",
                "checkpoint_revision": 9,
                "remote_outcome": "unknown",
                "dependents": [
                    {"type": "CRM query", "id": "crm_query_1"},
                    {"type": "workflow checkpoint", "id": "job_shared"},
                ],
            }
        ],
        "orphaned_dependents": [
            {
                "type": "duplicate-resolution journey",
                "id": "crm_journey_orphan",
                "reason": "missing_run_id",
            }
        ],
        "confirmation_digest": "d" * 64,
    }


class CrmConnectionRemovalCascadePhase3Tests(TestCase):
    def setUp(self):
        owner = uuid4()
        session = self.client.session
        session["easyimports_owner_id"] = str(owner)
        session.save()
        self.owner = owner
        self.connection_id = "crm_conn_cascade"

    def test_confirmation_names_every_dependent_and_unknown_remote_outcome(self):
        with patch(
            "importer.connection_views.EasyImportsApiClient.crm_connection_removal_dependents",
            return_value=_preview(self.connection_id),
        ):
            response = self.client.get(
                reverse(
                    "importer:crm_connection_remove_confirm",
                    kwargs={"connection_id": self.connection_id},
                )
            )
        self.assertEqual(response.status_code, 200)
        body = unescape(response.content.decode("utf-8"))
        self.assertIn("job_shared", body)
        self.assertIn("CRM query: crm_query_1", body)
        self.assertIn("workflow checkpoint: job_shared", body)
        self.assertIn("crm_journey_orphan", body)
        self.assertIn("remote outcome may be uncertain", body)
        self.assertIn("Stop analyses and remove connection", body)

    def test_loadable_runs_do_not_show_unloadable_checkpoint_warning(self):
        preview = _preview(self.connection_id)
        preview["runs"][0]["run_status"] = "running"
        preview["runs"][0]["remote_outcome"] = "none"
        preview["orphaned_dependents"] = []
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
        self.assertNotIn("no loadable analysis checkpoint", body)

    def test_confirm_uses_distinct_mutation_and_completes(self):
        preview = _preview(self.connection_id)
        with patch(
            "importer.connection_views.EasyImportsApiClient.crm_connection_removal_dependents",
            return_value=preview,
        ):
            page = self.client.get(
                reverse(
                    "importer:crm_connection_remove_confirm",
                    kwargs={"connection_id": self.connection_id},
                )
            )
        token = re.search(r'name="form_token" value="([^"]+)"', page.content.decode())[
            1
        ]

        def dispatch(mutation, **kwargs):
            body = {
                "completed": [{"run_id": "job_shared", "result": "force_terminalized"}],
                "pending": [],
                "quarantined": [],
            }
            mutation.state = ApiMutation.State.COMPLETED
            mutation.http_status = 200
            mutation.response_json = body
            mutation.save(
                update_fields=["state", "http_status", "response_json", "updated_at"]
            )
            return MutationDispatchResult(mutation, body)

        with patch(
            "importer.connection_views.EasyImportsApiClient.dispatch",
            side_effect=dispatch,
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_connection_remove_with_dependents",
                    kwargs={"connection_id": self.connection_id},
                ),
                {
                    "form_token": token,
                    "confirmation_digest": preview["confirmation_digest"],
                },
            )
        self.assertEqual(response.status_code, 302)
        journal = ImportSession.objects.get(
            owner_id=self.owner, product_key="crm.connection"
        )
        mutation = journal.api_mutations.get(
            mutation_kind="crm_connection_remove_with_dependents"
        )
        self.assertNotEqual(mutation.mutation_kind, "crm_connection_delete")
        self.assertEqual(
            mutation.route,
            f"/v1/crm/connections/{self.connection_id}/remove-with-dependents",
        )
        self.assertEqual(
            mutation.request_json["confirmation_digest"],
            preview["confirmation_digest"],
        )

    def test_ordinary_blocked_remove_redirects_to_confirmation(self):
        def rejected(mutation, **kwargs):
            body = {
                "error": {
                    "code": "crm_connection_in_use",
                    "message": "still referenced",
                    "details": {"blocker": "CRM query"},
                }
            }
            mutation.state = ApiMutation.State.REJECTED
            mutation.http_status = 409
            mutation.response_json = body
            mutation.error_code = "crm_connection_in_use"
            mutation.error_message = "still referenced"
            mutation.save()
            return MutationDispatchResult(mutation, body)

        with (
            patch(
                "importer.connection_views.EasyImportsApiClient.dispatch",
                side_effect=rejected,
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.crm_connection_removal_dependents",
                return_value=_preview(self.connection_id),
            ),
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_connection_remove",
                    kwargs={"connection_id": self.connection_id},
                )
            )
        self.assertRedirects(
            response,
            reverse(
                "importer:crm_connection_remove_confirm",
                kwargs={"connection_id": self.connection_id},
            ),
            fetch_redirect_response=False,
        )
