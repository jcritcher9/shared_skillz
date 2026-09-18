from __future__ import annotations

from contextlib import contextmanager
from html import unescape
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from importer.api_client import MutationDispatchResult
from importer.connection_views import CONNECTION_REMOVAL_BLOCKER_FLASH
from importer.models import ApiMutation, ImportSession

GENERIC_REMOVE_MESSAGE = (
    "This connection is still referenced by other records and cannot be removed."
)


def _rejected_dispatch(
    *,
    blocker: str | None,
    message: str = GENERIC_REMOVE_MESSAGE,
    error_id: str | None = "err-remove-1",
):
    def dispatch(mutation, **kwargs):
        error: dict = {
            "code": "crm_connection_in_use",
            "message": message,
        }
        if error_id is not None:
            error["error_id"] = error_id
        if blocker is not None:
            error["details"] = {"blocker": blocker}
        body = {"error": error}
        mutation.state = ApiMutation.State.REJECTED
        mutation.response_json = body
        mutation.http_status = 409
        mutation.error_code = "crm_connection_in_use"
        mutation.error_message = message
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

    return dispatch


@contextmanager
def _hub_reads():
    with (
        patch(
            "importer.connection_views.EasyImportsApiClient.crm_providers",
            return_value={
                "providers": [
                    {"provider_key": "fake", "provider_label": "Practice CRM"}
                ]
            },
        ),
        patch(
            "importer.connection_views.EasyImportsApiClient.crm_connections",
            return_value={"connections": []},
        ),
        patch(
            "importer.connection_views.EasyImportsApiClient.crm_app_registrations",
            return_value={"registrations": []},
        ),
    ):
        yield


class CrmConnectionRemoveTests(TestCase):
    def test_hub_shows_remove_only_for_disconnected(self):
        with (
            patch(
                "importer.connection_views.EasyImportsApiClient.crm_providers",
                return_value={
                    "providers": [
                        {"provider_key": "fake", "provider_label": "Practice CRM"}
                    ]
                },
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.crm_connections",
                return_value={
                    "connections": [
                        {
                            "connection_id": "crm_conn_old",
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                            "status": "disconnected",
                            "display_label": "Prior org",
                        },
                        {
                            "connection_id": "crm_conn_live",
                            "provider_key": "fake",
                            "provider_label": "Practice CRM",
                            "status": "connected",
                            "display_label": "Live org",
                        },
                    ]
                },
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        remove_old = reverse(
            "importer:crm_connection_remove",
            kwargs={"connection_id": "crm_conn_old"},
        )
        remove_live = reverse(
            "importer:crm_connection_remove",
            kwargs={"connection_id": "crm_conn_live"},
        )
        self.assertIn("Remove", body)
        self.assertIn(remove_old, body)
        self.assertNotIn(remove_live, body)
        self.assertIn("Reconnect", body)
        self.assertIn("Disconnect", body)

    def test_remove_post_checks_mutation_state_before_success(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_remove"

        def dispatch(mutation, **kwargs):
            body = {
                "error": {
                    "code": "crm_connection_in_use",
                    "message": (
                        "This connection is still referenced by other records "
                        "and cannot be removed."
                    ),
                    "details": {"blocker": "duplicate-resolution journey"},
                }
            }
            mutation.state = ApiMutation.State.REJECTED
            mutation.response_json = body
            mutation.http_status = 409
            mutation.error_code = "crm_connection_in_use"
            mutation.error_message = body["error"]["message"]
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
            patch(
                "importer.connection_views.EasyImportsApiClient.dispatch",
                side_effect=dispatch,
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_connection_remove",
                    kwargs={"connection_id": connection_id},
                )
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("importer:crm_connections"))
        journal = ImportSession.objects.get(owner_id=owner, product_key="crm.connection")
        mutation = journal.api_mutations.get(mutation_kind="crm_connection_delete")
        self.assertEqual(mutation.state, ApiMutation.State.REJECTED)
        self.assertEqual(mutation.route, f"/v1/crm/connections/{connection_id}")
        with _hub_reads():
            hub = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(hub.status_code, 200)
        body = unescape(hub.content.decode("utf-8"))
        self.assertIn(
            CONNECTION_REMOVAL_BLOCKER_FLASH["duplicate-resolution journey"],
            body,
        )
        self.assertIn("Technical details", body)
        self.assertIn(f"/v1/crm/connections/{connection_id}", body)
        self.assertIn("409", body)
        self.assertIn("crm_connection_in_use", body)

    def test_remove_post_success_requires_completed_mutation(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_remove_ok"

        def dispatch(mutation, **kwargs):
            body = {"connection_id": connection_id, "status": "deleted"}
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.http_status = 204
            mutation.save(
                update_fields=["state", "response_json", "http_status", "updated_at"]
            )
            return MutationDispatchResult(mutation, body)

        with (
            patch(
                "importer.connection_views.EasyImportsApiClient.dispatch",
                side_effect=dispatch,
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
        ):
            response = self.client.post(
                reverse(
                    "importer:crm_connection_remove",
                    kwargs={"connection_id": connection_id},
                )
            )
        self.assertEqual(response.status_code, 302)
        journal = ImportSession.objects.get(owner_id=owner, product_key="crm.connection")
        mutation = journal.api_mutations.get(mutation_kind="crm_connection_delete")
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.logical_action_identity.split(":")[0], "crm-forget")

    def test_rejected_remove_advances_generation_on_retry(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_remove_retry"
        dispatched: list[str] = []

        def dispatch(mutation, **kwargs):
            dispatched.append(str(mutation.id))
            if mutation.logical_action_generation == 0:
                body = {
                    "error": {
                        "code": "crm_connection_in_use",
                        "message": "still referenced",
                        "details": {"blocker": "CRM query"},
                    }
                }
                mutation.state = ApiMutation.State.REJECTED
                mutation.response_json = body
                mutation.http_status = 409
                mutation.error_code = "crm_connection_in_use"
                mutation.error_message = "still referenced"
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
            body = {"connection_id": connection_id, "status": "deleted"}
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.http_status = 204
            mutation.save(
                update_fields=["state", "response_json", "http_status", "updated_at"]
            )
            return MutationDispatchResult(mutation, body)

        url = reverse(
            "importer:crm_connection_remove",
            kwargs={"connection_id": connection_id},
        )
        with (
            patch(
                "importer.connection_views.EasyImportsApiClient.dispatch",
                side_effect=dispatch,
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
        ):
            first = self.client.post(url)
            second = self.client.post(url)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(len(dispatched), 2)
        journal = ImportSession.objects.get(owner_id=owner, product_key="crm.connection")
        rows = list(
            journal.api_mutations.filter(
                mutation_kind="crm_connection_delete"
            ).order_by("logical_action_generation")
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].logical_action_generation, 0)
        self.assertEqual(rows[0].state, ApiMutation.State.REJECTED)
        self.assertEqual(rows[1].logical_action_generation, 1)
        self.assertEqual(rows[1].state, ApiMutation.State.COMPLETED)

    def test_unknown_remove_reuses_generation_for_exact_retry(self):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        connection_id = "crm_conn_remove_unknown"
        seen_ids: list[str] = []

        def dispatch(mutation, **kwargs):
            seen_ids.append(str(mutation.id))
            if mutation.state != ApiMutation.State.UNKNOWN:
                mutation.state = ApiMutation.State.UNKNOWN
                mutation.error_message = "uncertain"
                mutation.save(update_fields=["state", "error_message", "updated_at"])
                from importer.api_client import MutationExplicitRetryRequired

                raise MutationExplicitRetryRequired("Retry this exact action.")
            body = {"connection_id": connection_id, "status": "deleted"}
            mutation.state = ApiMutation.State.COMPLETED
            mutation.response_json = body
            mutation.http_status = 204
            mutation.save(
                update_fields=["state", "response_json", "http_status", "updated_at"]
            )
            return MutationDispatchResult(mutation, body)

        url = reverse(
            "importer:crm_connection_remove",
            kwargs={"connection_id": connection_id},
        )
        with (
            patch(
                "importer.connection_views.EasyImportsApiClient.dispatch",
                side_effect=dispatch,
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
        ):
            first = self.client.post(url)
            second = self.client.post(url)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(len(set(seen_ids)), 1)
        journal = ImportSession.objects.get(owner_id=owner, product_key="crm.connection")
        self.assertEqual(
            journal.api_mutations.filter(mutation_kind="crm_connection_delete").count(),
            1,
        )
        mutation = journal.api_mutations.get(mutation_kind="crm_connection_delete")
        self.assertEqual(mutation.logical_action_generation, 0)
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)

    def test_blocker_flash_map_copies_phase0_wire_keys_only(self):
        self.assertEqual(
            set(CONNECTION_REMOVAL_BLOCKER_FLASH),
            {
                "duplicate-resolution journey",
                "CRM read grant",
                "workflow checkpoint",
                "CRM query",
                "in-flight dependent",
            },
        )
        self.assertNotIn("CRM population upload", CONNECTION_REMOVAL_BLOCKER_FLASH)
        self.assertNotIn("column-mapping plan", CONNECTION_REMOVAL_BLOCKER_FLASH)

    def _post_rejected_remove(self, *, connection_id: str, blocker: str | None):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        with (
            patch(
                "importer.connection_views.EasyImportsApiClient.dispatch",
                side_effect=_rejected_dispatch(blocker=blocker),
            ),
            patch(
                "importer.connection_views.EasyImportsApiClient.assert_compatible",
                return_value={"status": "ok", "api_version": "1.37.0"},
            ),
        ):
            posted = self.client.post(
                reverse(
                    "importer:crm_connection_remove",
                    kwargs={"connection_id": connection_id},
                )
            )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(posted["Location"], reverse("importer:crm_connections"))
        return owner

    def test_rejected_query_blocker_flash_has_no_delete_instruction(self):
        connection_id = "crm_conn_remove_query"
        self._post_rejected_remove(
            connection_id=connection_id, blocker="CRM query"
        )
        with _hub_reads():
            hub = self.client.get(reverse("importer:crm_connections"))
        body = unescape(hub.content.decode("utf-8"))
        self.assertIn(CONNECTION_REMOVAL_BLOCKER_FLASH["CRM query"], body)
        self.assertNotIn("Delete the query", body)
        self.assertNotIn("delete the query", body.lower())
        self.assertIn("Technical details", body)
        self.assertIn(f"/v1/crm/connections/{connection_id}", body)
        self.assertIn("409", body)
        self.assertIn("crm_connection_in_use", body)

    def test_rejected_upload_or_plan_blocker_uses_generic_flash(self):
        for blocker in ("CRM population upload", "column-mapping plan"):
            with self.subTest(blocker=blocker):
                client = self.client_class()
                owner = uuid4()
                session = client.session
                session["easyimports_owner_id"] = str(owner)
                session.save()
                connection_id = f"crm_conn_{blocker.replace(' ', '_')}"
                with (
                    patch(
                        "importer.connection_views.EasyImportsApiClient.dispatch",
                        side_effect=_rejected_dispatch(blocker=blocker),
                    ),
                    patch(
                        "importer.connection_views.EasyImportsApiClient.assert_compatible",
                        return_value={"status": "ok", "api_version": "1.37.0"},
                    ),
                ):
                    posted = client.post(
                        reverse(
                            "importer:crm_connection_remove",
                            kwargs={"connection_id": connection_id},
                        )
                    )
                self.assertEqual(posted.status_code, 302)
                with _hub_reads():
                    hub = client.get(reverse("importer:crm_connections"))
                body = unescape(hub.content.decode("utf-8"))
                self.assertIn(GENERIC_REMOVE_MESSAGE, body)
                self.assertNotIn("still in progress", body)
                self.assertIn("Technical details", body)

    def test_rejected_unknown_or_missing_blocker_uses_generic_flash(self):
        for blocker in (None, "not-a-known-blocker"):
            with self.subTest(blocker=blocker):
                client = self.client_class()
                owner = uuid4()
                session = client.session
                session["easyimports_owner_id"] = str(owner)
                session.save()
                connection_id = f"crm_conn_unknown_{blocker or 'missing'}"
                with (
                    patch(
                        "importer.connection_views.EasyImportsApiClient.dispatch",
                        side_effect=_rejected_dispatch(blocker=blocker),
                    ),
                    patch(
                        "importer.connection_views.EasyImportsApiClient.assert_compatible",
                        return_value={"status": "ok", "api_version": "1.37.0"},
                    ),
                ):
                    posted = client.post(
                        reverse(
                            "importer:crm_connection_remove",
                            kwargs={"connection_id": connection_id},
                        )
                    )
                self.assertEqual(posted.status_code, 302)
                with _hub_reads():
                    hub = client.get(reverse("importer:crm_connections"))
                body = unescape(hub.content.decode("utf-8"))
                self.assertIn(GENERIC_REMOVE_MESSAGE, body)
                self.assertNotIn("still in progress", body)

    def test_remove_technical_details_session_key_is_consumed(self):
        connection_id = "crm_conn_remove_once"
        self._post_rejected_remove(
            connection_id=connection_id, blocker="workflow checkpoint"
        )
        with _hub_reads():
            first = self.client.get(reverse("importer:crm_connections"))
            second = self.client.get(reverse("importer:crm_connections"))
        first_body = unescape(first.content.decode("utf-8"))
        second_body = unescape(second.content.decode("utf-8"))
        self.assertIn(
            CONNECTION_REMOVAL_BLOCKER_FLASH["workflow checkpoint"], first_body
        )
        self.assertIn("Technical details", first_body)
        self.assertIn(f"/v1/crm/connections/{connection_id}", first_body)
        self.assertNotIn("Technical details", second_body)
        self.assertNotIn(f"/v1/crm/connections/{connection_id}", second_body)
