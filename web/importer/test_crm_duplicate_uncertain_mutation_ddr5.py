"""DDR-5 timeout-specific uncertain-mutation diagnostics.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer.api_client import (
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationExplicitRetryRequired,
    canonical_digest,
    create_or_reuse_mutation,
)
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.pending_copy import UNKNOWN_BODY, UNKNOWN_HEADLINE, pending_copy_for_mutation
from importer.uncertain_mutation_copy import (
    GENERIC_UNCERTAIN_MESSAGE,
    classify_dispatch_exception,
    connect_timeout_message,
    generic_uncertain_message,
    is_safe_uncertain_diagnostic,
    read_timeout_message,
    uncertain_mutation_message,
)



COPY_PATH = Path(__file__).with_name("uncertain_mutation_copy.py")
CLIENT_PATH = Path(__file__).with_name("api_client.py")


def _response(status: int, payload) -> requests.Response:
    value = requests.Response()
    value.status_code = status
    value._content = (
        payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    )
    value.raw = Mock()
    value.headers["Content-Type"] = "application/json"
    return value


def _projection(**changes):
    value = {
        "run_id": "run-ddr5",
        "revision": 1,
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 8,
        "target_provider_id": "fake",
        "status": "awaiting_review",
        "stage": "review_handoff",
        "decision": None,
        "effect_intent": None,
        "effect_grants": [],
        "review_handoff": {
            "handoff_id": "review-handoff-ddr5",
            "entity": "account",
            "group_count": 2,
            "binding_digest": "binding-ddr5",
        },
        "terminal_evidence": None,
        "summary": {},
        "error": None,
        "paused_effect_diagnostic": None,
        "reference_acquisition_progress": None,
        "duplicate_analysis_progress": None,
        "links": {},
    }
    value.update(changes)
    return value


class UncertainMutationCopyContractTests(SimpleTestCase):
    def test_module_does_not_import_mappings_2(self) -> None:
        source = COPY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import mappings_2", source)
        self.assertNotIn("from mappings_2", source)
        client = CLIENT_PATH.read_text(encoding="utf-8")
        self.assertIn("uncertain_mutation_message", client)
        self.assertIn("time.monotonic()", client)
        self.assertIn("requests.ReadTimeout", client)
        self.assertIn("requests.ConnectTimeout", client)

    def test_review_start_read_timeout_names_action_and_measured_wait(self) -> None:
        message = read_timeout_message(
            "start_review_workflow", elapsed_seconds=180
        )
        self.assertEqual(
            message,
            "Starting your review is taking longer than 180 seconds. "
            "Retry this saved action; it uses the same idempotency key and "
            "will not create a second review.",
        )
        self.assertNotIn("start_review_workflow", message)
        self.assertNotIn("/v1/", message)
        immediate = read_timeout_message(
            "start_review_workflow", elapsed_seconds=0
        )
        self.assertEqual(
            immediate,
            "Starting your review is taking longer than 0 seconds. "
            "Retry this saved action; it uses the same idempotency key and "
            "will not create a second review.",
        )
        self.assertNotIn("180", immediate)

    def test_other_kind_is_parameterized_not_review_hardcoded(self) -> None:
        message = read_timeout_message("create_workflow", elapsed_seconds=180)
        self.assertIn("Starting your import is taking longer than 180 seconds.", message)
        self.assertIn("will not create a second import.", message)
        self.assertNotIn("Starting your review", message)
        self.assertNotIn("create_workflow", message)

    def test_unknown_kind_never_echoes_raw_kind_or_route(self) -> None:
        message = read_timeout_message(
            "totally_unknown_kind", elapsed_seconds=180
        )
        self.assertTrue(message.startswith("This saved action is taking longer than"))
        self.assertNotIn("totally_unknown_kind", message)
        self.assertNotIn("/secret", message)

    def test_connect_timeout_uses_connect_wait_never_read_timeout(self) -> None:
        message = connect_timeout_message(
            "start_review_workflow", connect_timeout=3
        )
        self.assertIn("could not connect to EasyImports within 3 seconds", message)
        self.assertIn(GENERIC_UNCERTAIN_MESSAGE, message)
        self.assertNotIn("180", message)
        self.assertNotIn("taking longer than", message)
        self.assertNotIn("processing", message.lower())

    def test_generic_uncertain_names_action_without_timeout_claim(self) -> None:
        message = generic_uncertain_message("start_review_workflow")
        self.assertIn("Starting your review", message)
        self.assertIn(GENERIC_UNCERTAIN_MESSAGE, message)
        self.assertNotIn("taking longer than", message)
        self.assertNotIn("180", message)
        self.assertNotIn("3 seconds", message)

    def test_builder_classifies_exception_types_exactly(self) -> None:
        self.assertEqual(
            classify_dispatch_exception(requests.ReadTimeout()), "read_timeout"
        )
        self.assertEqual(
            classify_dispatch_exception(requests.ConnectTimeout()),
            "connect_timeout",
        )
        self.assertEqual(
            classify_dispatch_exception(requests.ConnectionError("down")),
            "uncertain",
        )
        self.assertEqual(
            classify_dispatch_exception(requests.Timeout()), "uncertain"
        )
        read = uncertain_mutation_message(
            mutation_kind="start_review_workflow",
            exc=requests.ReadTimeout(),
            elapsed_seconds=181.2,
            connect_timeout=3,
            read_timeout=180,
        )
        self.assertIn("taking longer than 181 seconds", read)
        self.assertNotIn("taking longer than 180 seconds", read)
        immediate = uncertain_mutation_message(
            mutation_kind="start_review_workflow",
            exc=requests.ReadTimeout(),
            elapsed_seconds=0.0,
            connect_timeout=3,
            read_timeout=180,
        )
        self.assertIn("taking longer than 0 seconds", immediate)
        self.assertNotIn("180", immediate)
        connect = uncertain_mutation_message(
            mutation_kind="start_review_workflow",
            exc=requests.ConnectTimeout(),
            elapsed_seconds=3.1,
            connect_timeout=3,
            read_timeout=180,
        )
        self.assertIn("within 3 seconds", connect)
        self.assertNotIn("180", connect)
        other = uncertain_mutation_message(
            mutation_kind="start_review_workflow",
            exc=requests.ConnectionError("proxy"),
            elapsed_seconds=12,
            connect_timeout=3,
            read_timeout=180,
        )
        self.assertNotIn("taking longer than", other)
        self.assertNotIn("180", other)

    def test_raw_exception_text_is_not_a_safe_diagnostic(self) -> None:
        self.assertFalse(
            is_safe_uncertain_diagnostic(
                "HTTPConnectionPool(host='127.0.0.1', port=8000): Read timed out."
            )
        )
        self.assertFalse(
            is_safe_uncertain_diagnostic(
                "Starting your review at /v1/workflows/run/review-workflows"
            )
        )
        exact = read_timeout_message("start_review_workflow", elapsed_seconds=180)
        self.assertTrue(is_safe_uncertain_diagnostic(exact))
        self.assertTrue(
            is_safe_uncertain_diagnostic(
                exact, mutation_kind="start_review_workflow"
            )
        )
        self.assertFalse(
            is_safe_uncertain_diagnostic(
                "Starting your review is taking longer than SECRET seconds. "
                "Retry this saved action; it uses the same idempotency key and "
                "will not create a second review."
            )
        )
        self.assertFalse(is_safe_uncertain_diagnostic(f"{exact} token"))
        self.assertFalse(
            is_safe_uncertain_diagnostic(
                exact, mutation_kind="create_workflow"
            )
        )


class UncertainMutationDispatchTests(TestCase):
    def setUp(self):
        self.session = ImportSession.objects.create(owner_id=uuid4())

    def _mutation(self, *, kind: str, route: str) -> ApiMutation:
        return create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind=kind,
            route=route,
            logical_action_identity=f"ddr5:{kind}:{uuid4()}",
            request_json={"owner_session": f"django-{self.session.owner_id}"},
        )

    def _api(self, post_side_effect) -> tuple[EasyImportsApiClient, Mock]:
        from importer.api_contract_generated import API_VERSION as DJANGO_API_VERSION

        http = Mock()
        http.get.return_value = _response(
            200, {"status": "ok", "api_version": DJANGO_API_VERSION}
        )
        http.post.side_effect = post_side_effect
        return EasyImportsApiClient(http=http), http

    def _raise_read_timeout(
        self,
        api: EasyImportsApiClient,
        mutation: ApiMutation,
        *,
        start: float,
        end: float,
        explicit_retry: bool = False,
    ) -> str:
        ticks = iter([start, end])
        with patch(
            "importer.api_client.time.monotonic", side_effect=lambda: next(ticks)
        ):
            with self.assertRaises(ApiUnavailableError) as ctx:
                api.dispatch(mutation, explicit_retry=explicit_retry)
        return str(ctx.exception)

    @override_settings(
        EASYIMPORTS_API_CONNECT_TIMEOUT=3,
        EASYIMPORTS_API_MUTATION_READ_TIMEOUT=180,
    )
    def test_read_timeout_on_start_review_persists_action_and_duration(self) -> None:
        mutation = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5/review-workflows",
        )
        api, http = self._api(requests.ReadTimeout("read timed out"))
        expected = read_timeout_message(
            "start_review_workflow", elapsed_seconds=180
        )
        message = self._raise_read_timeout(api, mutation, start=10.0, end=190.0)
        self.assertEqual(message, expected)
        self.assertNotIn(mutation.route, message)
        self.assertNotIn(mutation.mutation_kind, message)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(mutation.error_message, message)
        self.assertEqual(mutation.idempotency_key, f"web-{mutation.id.hex}")
        with self.assertRaises(MutationExplicitRetryRequired):
            api.dispatch(mutation)
        http.post.side_effect = requests.ReadTimeout("read timed out")
        retry_message = self._raise_read_timeout(
            api, mutation, start=10.0, end=190.0, explicit_retry=True
        )
        self.assertEqual(retry_message, expected)
        self.assertEqual(http.post.call_count, 2)

    @override_settings(
        EASYIMPORTS_API_CONNECT_TIMEOUT=3,
        EASYIMPORTS_API_MUTATION_READ_TIMEOUT=180,
    )
    def test_immediate_read_timeout_uses_measured_zero_not_configured_budget(
        self,
    ) -> None:
        mutation = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5/review-workflows",
        )
        api, _http = self._api(requests.ReadTimeout("read timed out"))
        message = self._raise_read_timeout(api, mutation, start=10.0, end=10.0)
        self.assertEqual(
            message,
            read_timeout_message("start_review_workflow", elapsed_seconds=0),
        )
        self.assertNotIn("180", message)
        mutation.refresh_from_db()
        self.assertEqual(mutation.error_message, message)

    @override_settings(
        EASYIMPORTS_API_CONNECT_TIMEOUT=3,
        EASYIMPORTS_API_MUTATION_READ_TIMEOUT=180,
    )
    def test_read_timeout_on_create_workflow_is_parameterized(self) -> None:
        mutation = self._mutation(
            kind="create_workflow",
            route="/v1/workflows",
        )
        api, _http = self._api(requests.ReadTimeout())
        message = self._raise_read_timeout(api, mutation, start=4.0, end=184.0)
        self.assertIn("Starting your import is taking longer than 180 seconds.", message)
        self.assertIn("will not create a second import.", message)
        self.assertNotIn("Starting your review", message)
        mutation.refresh_from_db()
        self.assertEqual(mutation.error_message, message)
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)

    @override_settings(
        EASYIMPORTS_API_CONNECT_TIMEOUT=3,
        EASYIMPORTS_API_MUTATION_READ_TIMEOUT=180,
    )
    def test_connect_timeout_never_uses_read_timeout_wording(self) -> None:
        mutation = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5/review-workflows",
        )
        api, _http = self._api(requests.ConnectTimeout("connect timed out"))
        with self.assertRaises(ApiUnavailableError) as ctx:
            api.dispatch(mutation)
        message = str(ctx.exception)
        self.assertIn("could not connect to EasyImports within 3 seconds", message)
        self.assertIn(GENERIC_UNCERTAIN_MESSAGE, message)
        self.assertNotIn("180", message)
        self.assertNotIn("taking longer than", message)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(mutation.error_message, message)

    @override_settings(
        EASYIMPORTS_API_CONNECT_TIMEOUT=3,
        EASYIMPORTS_API_MUTATION_READ_TIMEOUT=180,
    )
    def test_connection_error_malformed_and_5xx_are_not_read_timeouts(self) -> None:
        mutation = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5/review-workflows",
        )
        api, http = self._api(requests.ConnectionError("connection reset"))
        with self.assertRaises(ApiUnavailableError) as ctx:
            api.dispatch(mutation)
        connection_message = str(ctx.exception)
        self.assertIn(GENERIC_UNCERTAIN_MESSAGE, connection_message)
        self.assertNotIn("taking longer than", connection_message)
        self.assertNotIn("180", connection_message)
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(mutation.error_message, connection_message)

        malformed = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5-malformed/review-workflows",
        )
        http.post.side_effect = None
        http.post.return_value = _response(200, {"not": "a receipt"})
        with self.assertRaises(ApiUnavailableError) as malformed_ctx:
            api.dispatch(malformed)
        self.assertEqual(str(malformed_ctx.exception), GENERIC_UNCERTAIN_MESSAGE)
        self.assertNotIn("taking longer than", str(malformed_ctx.exception))
        malformed.refresh_from_db()
        self.assertEqual(malformed.state, ApiMutation.State.UNKNOWN)
        with self.assertRaises(MutationExplicitRetryRequired):
            api.dispatch(malformed)

        failed = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5-500/review-workflows",
        )
        http.post.return_value = _response(500, {"error": {"message": "boom"}})
        with self.assertRaises(ApiUnavailableError) as failed_ctx:
            api.dispatch(failed)
        failed_message = str(failed_ctx.exception)
        self.assertEqual(failed_message, GENERIC_UNCERTAIN_MESSAGE)
        self.assertNotIn("boom", failed_message)
        self.assertNotIn("taking longer than", failed_message)
        self.assertNotIn("180 seconds", failed_message)
        failed.refresh_from_db()
        self.assertEqual(failed.state, ApiMutation.State.UNKNOWN)
        self.assertEqual(failed.error_message, GENERIC_UNCERTAIN_MESSAGE)

    def test_dispatch_measures_elapsed_with_monotonic_clock(self) -> None:
        mutation = self._mutation(
            kind="start_review_workflow",
            route="/v1/workflows/run-ddr5/review-workflows",
        )
        api, _http = self._api(requests.ReadTimeout())
        ticks = iter([10.0, 190.4])
        with (
            patch("importer.api_client.time.monotonic", side_effect=lambda: next(ticks)),
            patch(
                "importer.api_client.uncertain_mutation_message",
                wraps=uncertain_mutation_message,
            ) as builder,
        ):
            with self.assertRaises(ApiUnavailableError):
                api.dispatch(mutation)
        kwargs = builder.call_args.kwargs
        self.assertAlmostEqual(kwargs["elapsed_seconds"], 180.4)
        self.assertEqual(kwargs["read_timeout"], 180)
        self.assertEqual(kwargs["connect_timeout"], 3)
        self.assertEqual(kwargs["mutation_kind"], "start_review_workflow")
        self.assertIsInstance(kwargs["exc"], requests.ReadTimeout)
        mutation.refresh_from_db()
        self.assertEqual(
            mutation.error_message,
            read_timeout_message("start_review_workflow", elapsed_seconds=180.4),
        )


class UncertainMutationPresentationTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def test_unknown_retry_card_surfaces_persisted_diagnostic(self) -> None:
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            target_provider_id="fake",
            operator_label="Alice Operator",
        )
        value = _projection()
        workflow = ApiWorkflow.objects.create(
            session=session,
            role=ApiWorkflow.Role.PRIMARY,
            run_id=value["run_id"],
            workflow_key=value["workflow_key"],
            workflow_version=value["workflow_version"],
            target_provider_id="fake",
            status=value["status"],
            stage=value["stage"],
            revision=value["revision"],
            resource_url=f"/v1/workflows/{value['run_id']}",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        session.active_workflow = workflow
        session.save(update_fields=["active_workflow"])
        diagnostic = read_timeout_message(
            "start_review_workflow", elapsed_seconds=180
        )
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="start_review_workflow",
            route=f"/v1/workflows/{workflow.run_id}/review-workflows",
            logical_action_identity=f"start-review:{uuid4()}",
            request_json={"review_handoff_id": "review-handoff-ddr5"},
        )
        mutation.state = ApiMutation.State.UNKNOWN
        mutation.error_message = diagnostic
        mutation.save(update_fields=["state", "error_message", "updated_at"])

        copy = pending_copy_for_mutation(mutation)
        self.assertEqual(copy["headline"], UNKNOWN_HEADLINE)
        self.assertEqual(copy["body"], diagnostic)
        self.assertNotEqual(copy["body"], UNKNOWN_BODY)

        raw = pending_copy_for_mutation(
            SimpleNamespace(
                state="unknown",
                mutation_kind="start_review_workflow",
                error_message=(
                    "HTTPConnectionPool(host='127.0.0.1', port=8000): "
                    "Read timed out. "
                    f"{mutation.route}"
                ),
            )
        )
        self.assertEqual(raw["body"], UNKNOWN_BODY)
        forged_duration = pending_copy_for_mutation(
            SimpleNamespace(
                state="unknown",
                mutation_kind="start_review_workflow",
                error_message=(
                    "Starting your review is taking longer than SECRET seconds. "
                    "Retry this saved action; it uses the same idempotency key and "
                    "will not create a second review."
                ),
            )
        )
        self.assertEqual(forged_duration["body"], UNKNOWN_BODY)
        trailing = pending_copy_for_mutation(
            SimpleNamespace(
                state="unknown",
                mutation_kind="start_review_workflow",
                error_message=f"{diagnostic} token",
            )
        )
        self.assertEqual(trailing["body"], UNKNOWN_BODY)

        with patch(
            "importer.workflow_views.refresh_workflow", return_value=workflow
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Starting your review is taking longer than 180 seconds.")
        self.assertContains(page, "Retry saved action")
        self.assertContains(page, "Action needs confirmation")
        self.assertNotContains(page, mutation.route)
