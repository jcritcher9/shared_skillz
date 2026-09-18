from __future__ import annotations

"""Phase 2A/2B rem — Start over lease/connectability + technical details."""

from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from importer.api_client import (
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationBusyError,
    MutationReuseError,
    api_error_technical_details,
    create_or_reuse_mutation,
)
from importer.connection_views import _registration_provider_keys, crm_connections
from importer.models import ApiMutation, ImportSession
from importer.settings_views import (
    REGISTRATION_PUT_IDENTITIES,
    _dispatch_registration,
    _registration_generation,
    api_has_provider_registration,
    api_provider_is_connectable,
    mutation_has_active_dispatch_lease,
    rejected_registration_error_from_mutation,
    start_over_registration_put,
    terminalize_unknown_registration_mutation,
)


def _mock_client_empty(*, connectable: bool = False) -> MagicMock:
    client = MagicMock()
    client.crm_app_registrations.return_value = {"registrations": []}
    if connectable:
        client.crm_providers.return_value = {
            "providers": [
                {
                    "provider_key": "hubspot",
                    "provider_label": "HubSpot",
                    "connectable": True,
                }
            ]
        }
    else:
        client.crm_providers.return_value = {"providers": []}
    return client


class ApiErrorTechnicalDetailsPhase2BTests(SimpleTestCase):
    def test_unavailable_includes_route_status_error_id(self):
        exc = ApiUnavailableError(
            "boom",
            route="/v1/settings/crm-app-registrations/hubspot",
            http_status=500,
            error_id="err-abc",
            error_code="internal_error",
        )
        details = api_error_technical_details(exc)
        self.assertEqual(details["route"], "/v1/settings/crm-app-registrations/hubspot")
        self.assertEqual(details["http_status"], 500)
        self.assertEqual(details["error_id"], "err-abc")
        self.assertEqual(details["error_code"], "internal_error")
        self.assertIn("boom", details["message"])

    def test_rejected_includes_error_id(self):
        exc = ApiRejectedError(
            "bad_request",
            "nope",
            route="/v1/crm/connections",
            http_status=422,
            error_id="err-422",
        )
        details = api_error_technical_details(exc)
        self.assertEqual(details["error_id"], "err-422")
        self.assertEqual(details["error_code"], "bad_request")
        self.assertEqual(details["http_status"], 422)

    def test_get_json_500_envelope_surfaces_error_id_on_registration_route(self):
        """Acceptance: force 500 through client on registration inventory route."""

        client = EasyImportsApiClient()
        response = MagicMock()
        response.status_code = 500
        response.content = b'{"error":{"code":"internal","message":"down","error_id":"reg-500-id"}}'
        response.json.return_value = {
            "error": {
                "code": "internal",
                "message": "down",
                "error_id": "reg-500-id",
            }
        }
        with (
            patch.object(client, "assert_compatible"),
            patch.object(client.http, "get", return_value=response),
        ):
            with self.assertRaises(ApiUnavailableError) as ctx:
                client.crm_app_registrations()
        exc = ctx.exception
        self.assertEqual(exc.route, "/v1/settings/crm-app-registrations")
        self.assertEqual(exc.http_status, 500)
        self.assertEqual(exc.error_id, "reg-500-id")
        self.assertEqual(exc.error_code, "internal")
        details = api_error_technical_details(exc)
        self.assertEqual(details["error_id"], "reg-500-id")
        self.assertEqual(details["route"], "/v1/settings/crm-app-registrations")


class RegistrationStartOverPhase2ATests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="setup",
            product_key="crm.setup",
            target_provider_id="",
        )

    def _unknown_put(self, *, identity: str, digest: str = "digest-a") -> ApiMutation:
        return ApiMutation.objects.create(
            session=self.session,
            form_instance=uuid4(),
            idempotency_key=f"web-{uuid4().hex}",
            mutation_kind="crm_app_registration_hubspot_put",
            route="/v1/settings/crm-app-registrations/hubspot",
            resource_identity="hubspot",
            logical_action_identity=identity,
            logical_action_generation=1,
            form_payload_digest=digest,
            request_kind="json",
            request_json={"label": "dev hub"},
            request_digest="req",
            state=ApiMutation.State.UNKNOWN,
            http_status=500,
            error_message="uncertain",
        )

    def test_start_over_clears_unknown_when_api_has_no_registration(self):
        identity = REGISTRATION_PUT_IDENTITIES["hubspot"][0]
        mut = self._unknown_put(identity=identity)
        client = _mock_client_empty(connectable=False)

        result = start_over_registration_put(
            owner_id=self.owner, provider_key="hubspot", client=client
        )
        self.assertEqual(result.outcome, "cleared")
        self.assertEqual(result.terminalized_count, 1)
        mut.refresh_from_db()
        self.assertEqual(mut.state, ApiMutation.State.REJECTED)
        self.assertEqual(mut.error_code, "operator_start_over")

        gen = _registration_generation(
            self.session, identity=identity, form_payload_digest="new"
        )
        self.assertEqual(gen, 2)

    def test_start_over_does_not_terminalize_when_api_has_registration(self):
        identity = REGISTRATION_PUT_IDENTITIES["hubspot"][0]
        mut = self._unknown_put(identity=identity)
        client = MagicMock()
        client.crm_app_registrations.return_value = {
            "registrations": [{"provider_key": "hubspot", "label": "Work"}]
        }
        client.crm_providers.return_value = {"providers": []}

        result = start_over_registration_put(
            owner_id=self.owner, provider_key="hubspot", client=client
        )
        self.assertEqual(result.outcome, "already_registered")
        self.assertEqual(result.terminalized_count, 0)
        mut.refresh_from_db()
        self.assertEqual(mut.state, ApiMutation.State.UNKNOWN)

    def test_start_over_does_not_terminalize_when_provider_connectable_without_inventory(
        self,
    ):
        identity = REGISTRATION_PUT_IDENTITIES["hubspot"][0]
        mut = self._unknown_put(identity=identity)
        client = _mock_client_empty(connectable=True)

        result = start_over_registration_put(
            owner_id=self.owner, provider_key="hubspot", client=client
        )
        self.assertEqual(result.outcome, "already_connectable")
        self.assertEqual(result.terminalized_count, 0)
        mut.refresh_from_db()
        self.assertEqual(mut.state, ApiMutation.State.UNKNOWN)

    def test_start_over_refuses_active_dispatch_lease(self):
        identity = REGISTRATION_PUT_IDENTITIES["hubspot"][0]
        mut = self._unknown_put(identity=identity)
        mut.lease_token = uuid4()
        mut.lease_expires_at = timezone.now() + timedelta(seconds=120)
        mut.save(update_fields=["lease_token", "lease_expires_at"])
        self.assertTrue(mutation_has_active_dispatch_lease(mut))

        client = _mock_client_empty(connectable=False)
        result = start_over_registration_put(
            owner_id=self.owner, provider_key="hubspot", client=client
        )
        self.assertEqual(result.outcome, "busy")
        self.assertEqual(result.terminalized_count, 0)
        mut.refresh_from_db()
        self.assertEqual(mut.state, ApiMutation.State.UNKNOWN)
        self.assertIsNotNone(mut.lease_token)

    def test_terminalize_raises_busy_when_lease_active(self):
        mut = self._unknown_put(identity=REGISTRATION_PUT_IDENTITIES["hubspot"][0])
        mut.lease_token = uuid4()
        mut.lease_expires_at = timezone.now() + timedelta(seconds=60)
        mut.save(update_fields=["lease_token", "lease_expires_at"])
        with self.assertRaises(MutationBusyError):
            terminalize_unknown_registration_mutation(mut, reason="x")

    def test_exact_retry_same_digest_still_reuses_before_start_over(self):
        identity = REGISTRATION_PUT_IDENTITIES["hubspot"][0]
        digest = "same-digest"
        mut = self._unknown_put(identity=identity, digest=digest)
        reused = create_or_reuse_mutation(
            session=self.session,
            form_instance=mut.form_instance,
            mutation_kind="crm_app_registration_hubspot_put",
            route="/v1/settings/crm-app-registrations/hubspot",
            logical_action_identity=identity,
            logical_action_generation=1,
            form_payload_digest=digest,
            request_json={"label": "dev hub"},
            resource_identity="hubspot",
        )
        self.assertEqual(reused.id, mut.id)

        with self.assertRaises(MutationReuseError):
            create_or_reuse_mutation(
                session=self.session,
                form_instance=uuid4(),
                mutation_kind="crm_app_registration_hubspot_put",
                route="/v1/settings/crm-app-registrations/hubspot",
                logical_action_identity=identity,
                logical_action_generation=1,
                form_payload_digest="different-digest",
                request_json={"label": "other"},
                resource_identity="hubspot",
            )

    def test_after_start_over_different_digest_is_accepted_at_next_generation(self):
        identity = REGISTRATION_PUT_IDENTITIES["hubspot"][0]
        mut = self._unknown_put(identity=identity, digest="old")
        client = _mock_client_empty(connectable=False)
        start_over_registration_put(
            owner_id=self.owner, provider_key="hubspot", client=client
        )
        gen = _registration_generation(
            self.session, identity=identity, form_payload_digest="new"
        )
        new_mut = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="crm_app_registration_hubspot_put",
            route="/v1/settings/crm-app-registrations/hubspot",
            logical_action_identity=identity,
            logical_action_generation=gen,
            form_payload_digest="new",
            request_json={"label": "new label"},
            resource_identity="hubspot",
        )
        self.assertNotEqual(new_mut.id, mut.id)
        self.assertEqual(new_mut.logical_action_generation, 2)
        self.assertEqual(new_mut.state, ApiMutation.State.PENDING)

    def test_api_has_provider_registration_helper(self):
        client = MagicMock()
        client.crm_app_registrations.return_value = {
            "registrations": [{"provider_key": "salesforce"}]
        }
        self.assertTrue(api_has_provider_registration(client, "salesforce"))
        self.assertFalse(api_has_provider_registration(client, "hubspot"))

    def test_api_provider_is_connectable_helper(self):
        client = MagicMock()
        client.crm_providers.return_value = {
            "providers": [{"provider_key": "hubspot", "connectable": True}]
        }
        self.assertTrue(api_provider_is_connectable(client, "hubspot"))
        self.assertFalse(api_provider_is_connectable(client, "salesforce"))

    def test_terminalize_rejects_non_unknown(self):
        mut = self._unknown_put(identity=REGISTRATION_PUT_IDENTITIES["hubspot"][0])
        mut.state = ApiMutation.State.PENDING
        mut.save(update_fields=["state"])
        with self.assertRaises(Exception):
            terminalize_unknown_registration_mutation(mut, reason="x")

    def test_rejected_mutation_preserves_route_status_error_id(self):
        mut = self._unknown_put(identity=REGISTRATION_PUT_IDENTITIES["hubspot"][0])
        mut.state = ApiMutation.State.REJECTED
        mut.error_code = "validation_error"
        mut.error_message = "bad hub"
        mut.http_status = 422
        mut.route = "/v1/settings/crm-app-registrations/hubspot"
        mut.response_json = {
            "error": {
                "code": "validation_error",
                "message": "bad hub",
                "error_id": "rej-err-9",
            }
        }
        mut.save()
        exc = rejected_registration_error_from_mutation(mut)
        self.assertEqual(exc.route, "/v1/settings/crm-app-registrations/hubspot")
        self.assertEqual(exc.http_status, 422)
        self.assertEqual(exc.error_id, "rej-err-9")
        self.assertEqual(exc.code, "validation_error")
        details = api_error_technical_details(exc)
        self.assertEqual(details["error_id"], "rej-err-9")
        self.assertEqual(details["route"], mut.route)

    def test_dispatch_registration_surfaces_rejected_technical_fields(self):
        mut = self._unknown_put(identity=REGISTRATION_PUT_IDENTITIES["hubspot"][0])
        mut.state = ApiMutation.State.REJECTED
        mut.error_code = "crm_app_registration_rejected"
        mut.error_message = "nope"
        mut.http_status = 422
        mut.response_json = {
            "error": {
                "code": "crm_app_registration_rejected",
                "message": "nope",
                "error_id": "dispatch-rej-1",
            }
        }
        mut.save()
        client = MagicMock()
        with self.assertRaises(ApiRejectedError) as ctx:
            _dispatch_registration(client, mut)
        exc = ctx.exception
        self.assertEqual(exc.error_id, "dispatch-rej-1")
        self.assertEqual(exc.route, mut.route)
        self.assertEqual(exc.http_status, 422)


class ConnectHubRegistrationLoadPhase2BTests(TestCase):
    def test_registration_inventory_failure_returns_error_not_silent_empty(self):
        client = MagicMock()
        client.crm_app_registrations.side_effect = ApiUnavailableError(
            "reg inventory down",
            route="/v1/settings/crm-app-registrations",
            http_status=500,
            error_id="hub-reg-500",
            error_code="internal",
        )
        keys, err = _registration_provider_keys(client)
        self.assertEqual(keys, set())
        self.assertIsInstance(err, ApiUnavailableError)
        self.assertEqual(err.error_id, "hub-reg-500")
        self.assertEqual(err.route, "/v1/settings/crm-app-registrations")

    def test_crm_connections_surfaces_registration_load_technical_details(self):
        factory = RequestFactory()
        request = factory.get("/crm/connections/")

        class _Sess(dict):
            def pop(self, key, default=None):
                return super().pop(key, default)

        request.session = _Sess()
        request.META["REMOTE_ADDR"] = "127.0.0.1"

        client = MagicMock()
        client.crm_providers.return_value = {"providers": []}
        client.crm_connections.return_value = {"connections": []}
        client.crm_app_registrations.side_effect = ApiUnavailableError(
            "reg inventory down",
            route="/v1/settings/crm-app-registrations",
            http_status=500,
            error_id="hub-reg-500",
            error_code="internal",
        )

        with (
            patch(
                "importer.connection_views.EasyImportsApiClient",
                return_value=client,
            ),
            patch(
                "importer.connection_views.owner_id_for_request",
                return_value=uuid4(),
            ),
            patch(
                "importer.connection_views._connection_session",
            ) as conn_sess,
            patch(
                "importer.connection_views._settings_session",
            ) as settings_sess,
            patch(
                "importer.connection_views.list_stuck_unknown_registration_puts",
                return_value=[],
            ),
            patch("importer.connection_views.messages"),
            patch("importer.connection_views.render") as render_mock,
        ):
            session = ImportSession.objects.create(
                owner_id=uuid4(),
                operator_label="conn",
                product_key="crm.connection",
                target_provider_id="",
            )
            conn_sess.return_value = session
            settings_sess.return_value = session
            render_mock.return_value = MagicMock(status_code=200)
            crm_connections(request)
            self.assertTrue(render_mock.called)
            context = render_mock.call_args[0][2]
            tech = context.get("api_technical")
            self.assertIsNotNone(tech)
            self.assertEqual(tech["error_id"], "hub-reg-500")
            self.assertEqual(tech["route"], "/v1/settings/crm-app-registrations")
            self.assertEqual(tech["http_status"], 500)
