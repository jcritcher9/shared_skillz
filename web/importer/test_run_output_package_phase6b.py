"""Phase 6B network-free tests: terminal package CTA + Django content proxy."""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.test import Client, TestCase
from django.urls import reverse

from .api_client import (
    ApiConsistencyError,
    ApiOperationInProgressError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationDispatchResult,
    canonical_digest,
    create_or_reuse_mutation,
)
from .api_contract import validate_run_output_package_resource
from .api_contract_generated import API_VERSION
from .models import ApiMutation, ApiWorkflow, ImportSession
from .tests import projection, response
from .workflow_state import OWNER_SESSION_KEY
from .workflow_views import (
    _resolve_run_output_package,
    run_output_package_create_generation,
)


def _package_resource(**changes) -> dict:
    content = b"PK\x03\x04fake-zip-bytes"
    digest = sha256(content).hexdigest()
    value = {
        "package_id": "rop_testpackage001",
        "run_id": "run-package-term",
        "run_revision": 3,
        "workflow_key": "easyimports.list_import",
        "layout_version": "run_output_package.v1",
        "content_digest": digest,
        "byte_count": len(content),
        "created_at": "2026-08-02T12:00:00Z",
        "expires_at": "2026-09-01T12:00:00Z",
        "included_files": [
            {
                "path": "MANIFEST.json",
                "content_digest": sha256(b"{}").hexdigest(),
                "byte_count": 2,
            }
        ],
        "status": "available",
        "download_url": "/v1/run-output-packages/rop_testpackage001/content",
    }
    value.update(changes)
    return value, content


class _PackageStream:
    def __init__(self, content: bytes, *, etag: str, content_type: str = "application/zip"):
        self._content = content
        self.headers = {
            "ETag": etag,
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
        }
        self.status_code = 200

    def iter_content(self, chunk_size=64 * 1024):
        yield self._content

    def close(self):
        return None


class CrmConnectionSetupUxPhase6BTests(TestCase):
    """Terminal always-download UI + integrity proxy (desire #5 / Phase 6B)."""

    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session[OWNER_SESSION_KEY] = str(self.owner)
        browser_session.save()
        self.package, self.zip_bytes = _package_resource()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="Package Operator",
            product_key="easyimports.list_import",
        )
        value = projection(
            run_id=self.package["run_id"],
            revision=self.package["run_revision"],
            workflow_key="easyimports.list_import",
            status="succeeded",
            stage="complete",
            terminal_evidence={
                "workflow_key": "easyimports.list_import",
                "workflow_version": 7,
                "receipts": [],
                "accountability": {
                    "original_row_ids": ["r1"],
                    "output_row_ids": ["r1"],
                    "excluded_row_count": 0,
                    "failed_row_ids": [],
                    "reconciles": True,
                },
                "delivery_manifest": None,
            },
        )
        self.workflow = ApiWorkflow.objects.create(
            session=self.session,
            run_id=value["run_id"],
            workflow_key=value["workflow_key"],
            workflow_version=value["workflow_version"],
            target_provider_id="fake-preview-v1",
            status=value["status"],
            stage=value["stage"],
            revision=value["revision"],
            resource_url=f"/v1/workflows/{value['run_id']}",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        self.session.active_workflow = self.workflow
        self.session.save(update_fields=["active_workflow"])

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        # Prefer the package form token near run-output-package action.
        package_marker = "create_run_output_package"
        # Find form that posts to create package.
        form_start = html.find('action="')
        token = None
        idx = 0
        while True:
            pos = html.find(marker, idx)
            if pos == -1:
                break
            start = pos + len(marker)
            end = html.find('"', start)
            candidate = html[start:end]
            # Take the last form_token in the package section when present.
            section = html[max(0, pos - 400) : pos + 50]
            if "run-output-package" in section or "Download results package" in section:
                token = candidate
            idx = end + 1
        if token is None:
            # Fallback: first token on page.
            start = html.find(marker)
            self.assertNotEqual(start, -1, "form_token missing")
            start += len(marker)
            end = html.find('"', start)
            token = html[start:end]
        return token

    def test_validate_package_resource_contract(self):
        validated = validate_run_output_package_resource(self.package)
        self.assertEqual(validated["package_id"], self.package["package_id"])
        self.assertEqual(validated["layout_version"], "run_output_package.v1")

    def test_terminal_shows_package_cta_for_succeeded_eligible_run(self):
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch(
            "importer.workflow_views._resolve_run_output_package", return_value=None
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Download results package", body)
        self.assertIn(
            reverse("importer:create_run_output_package", args=[self.session.id]),
            body,
        )
        self.assertIn('name="form_token"', body)
        # Per-file section demoted when package track is primary.
        self.assertNotIn("Download results</h2>", body)

    def test_running_workflow_hides_package_cta(self):
        self.workflow.status = "running"
        self.workflow.projection = projection(
            run_id=self.workflow.run_id,
            revision=self.workflow.revision,
            status="running",
        )
        self.workflow.projection_digest = canonical_digest(self.workflow.projection)
        self.workflow.save()
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Download results package")

    def test_create_package_post_dispatches_and_redirects_to_content(self):
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch(
            "importer.workflow_views._resolve_run_output_package", return_value=None
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        token = self._extract_form_token(page.content.decode("utf-8"))

        mutation_holder: dict = {}

        def fake_dispatch(mutation):
            mutation_holder["mutation"] = mutation
            mutation.state = ApiMutation.State.COMPLETED
            mutation.http_status = 201
            mutation.response_json = self.package
            mutation.response_digest = canonical_digest(self.package)
            mutation.error_code = ""
            mutation.error_message = ""
            mutation.save()
            return MutationDispatchResult(mutation, self.package)

        with patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            created = self.client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                data={"form_token": token},
            )
        self.assertEqual(created.status_code, 302)
        self.assertEqual(
            created["Location"],
            reverse(
                "importer:run_output_package_content",
                args=[self.session.id, self.package["package_id"]],
            ),
        )
        mutation = mutation_holder["mutation"]
        self.assertEqual(mutation.mutation_kind, "create_run_output_package")
        self.assertEqual(
            mutation.route,
            f"/v1/workflows/{self.workflow.run_id}/run-output-packages",
        )
        self.assertEqual(
            mutation.request_json.get("layout_version"), "run_output_package.v1"
        )
        self.assertIn("owner_session", mutation.request_json)
        # Exact-retry reuses same mutation (same form token / generation while live).
        with patch.object(
            EasyImportsApiClient, "run_output_package", return_value=self.package
        ), patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            again = self.client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                data={"form_token": token},
            )
        self.assertEqual(again.status_code, 302)
        self.assertEqual(
            ApiMutation.objects.filter(
                mutation_kind="create_run_output_package"
            ).count(),
            1,
        )

    def test_create_package_pending_shows_in_progress_not_prep_error(self):
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch(
            "importer.workflow_views._resolve_run_output_package", return_value=None
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        token = self._extract_form_token(page.content.decode("utf-8"))

        def fake_dispatch(mutation):
            mutation.state = ApiMutation.State.PENDING
            mutation.http_status = 202
            mutation.lease_token = None
            mutation.lease_expires_at = None
            mutation.error_message = ""
            mutation.save(
                update_fields=[
                    "state",
                    "http_status",
                    "lease_token",
                    "lease_expires_at",
                    "error_message",
                    "updated_at",
                ]
            )
            raise ApiOperationInProgressError("EasyImports is processing this action.")

        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch(
            "importer.workflow_views._resolve_run_output_package", return_value=None
        ), patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            created = self.client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                data={"form_token": token},
                follow=True,
            )
        self.assertEqual(created.status_code, 200)
        self.assertContains(created, "Preparing your results")
        self.assertContains(created, "does not change CRM records")
        self.assertNotContains(created, "cleaning and importing")
        self.assertNotContains(created, "The results package could not be prepared.")
        mutation = ApiMutation.objects.get(mutation_kind="create_run_output_package")
        self.assertEqual(mutation.state, ApiMutation.State.PENDING)
        self.assertTrue(mutation.operation_in_progress)
        self.assertContains(
            created,
            reverse("importer:mutation_status", args=[self.session.id, mutation.id]),
        )

    def test_create_package_requires_form_token(self):
        denied = self.client.post(
            reverse("importer:create_run_output_package", args=[self.session.id]),
            data={},
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(
            denied["Location"],
            reverse("importer:workflow", args=[self.session.id]),
        )
        self.assertEqual(
            ApiMutation.objects.filter(
                mutation_kind="create_run_output_package"
            ).count(),
            0,
        )

    def test_create_package_enforces_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        session = csrf_client.session
        session[OWNER_SESSION_KEY] = str(self.owner)
        session.save()
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch(
            "importer.workflow_views._resolve_run_output_package", return_value=None
        ):
            page = csrf_client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        body = page.content.decode("utf-8")
        token = self._extract_form_token(body)
        csrf_marker = 'name="csrfmiddlewaretoken" value="'
        start = body.find(csrf_marker) + len(csrf_marker)
        end = body.find('"', start)
        csrf = body[start:end]
        denied = csrf_client.post(
            reverse("importer:create_run_output_package", args=[self.session.id]),
            data={"form_token": token},
        )
        self.assertEqual(denied.status_code, 403)

        def fake_dispatch(mutation):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.http_status = 201
            mutation.response_json = self.package
            mutation.response_digest = canonical_digest(self.package)
            mutation.save()
            return MutationDispatchResult(mutation, self.package)

        with patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            allowed = csrf_client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                data={
                    "form_token": token,
                    "csrfmiddlewaretoken": csrf,
                },
            )
        self.assertEqual(allowed.status_code, 302)

    def _complete_package_mutation(self, *, logical_action_identity: str | None = None):
        identity = logical_action_identity or (
            f"run:{self.workflow.run_id}:revision:{self.workflow.revision}:"
            f"create_run_output_package:{self.workflow.run_id}:{self.workflow.revision}:group:"
        )
        create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_run_output_package",
            route=f"/v1/workflows/{self.workflow.run_id}/run-output-packages",
            logical_action_identity=identity,
            request_json={
                "layout_version": "run_output_package.v1",
                "owner_session": f"django-{self.owner}",
            },
            workflow=self.workflow,
            resource_identity=self.workflow.run_id,
        )
        mutation = ApiMutation.objects.filter(
            mutation_kind="create_run_output_package",
            logical_action_identity=identity,
        ).latest("created_at")
        mutation.state = ApiMutation.State.COMPLETED
        mutation.http_status = 201
        mutation.response_json = self.package
        mutation.response_digest = canonical_digest(self.package)
        mutation.save()
        return mutation

    def test_content_proxy_streams_with_integrity_checks(self):
        self._complete_package_mutation()
        stream = _PackageStream(
            self.zip_bytes,
            etag=f'"sha256:{self.package["content_digest"]}"',
        )
        with patch.object(
            EasyImportsApiClient, "run_output_package", return_value=self.package
        ), patch.object(
            EasyImportsApiClient, "stream_run_output_package", return_value=stream
        ):
            download = self.client.get(
                reverse(
                    "importer:run_output_package_content",
                    args=[self.session.id, self.package["package_id"]],
                )
            )
        self.assertEqual(download.status_code, 200)
        streamed = b"".join(download.streaming_content)
        self.assertEqual(streamed, self.zip_bytes)
        self.assertEqual(
            download["ETag"], f'"sha256:{self.package["content_digest"]}"'
        )
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertIn(self.package["run_id"], download["Content-Disposition"])

    def test_content_proxy_rejects_digest_mismatch(self):
        self._complete_package_mutation(logical_action_identity="pkg-bad-digest")
        stream = _PackageStream(
            b"tampered-bytes",
            etag=f'"sha256:{self.package["content_digest"]}"',
        )
        with patch.object(
            EasyImportsApiClient, "run_output_package", return_value=self.package
        ), patch.object(
            EasyImportsApiClient, "stream_run_output_package", return_value=stream
        ):
            download = self.client.get(
                reverse(
                    "importer:run_output_package_content",
                    args=[self.session.id, self.package["package_id"]],
                )
            )
        self.assertEqual(download.status_code, 404)

    def test_stale_completed_receipt_does_not_surface_dead_download_link(self):
        """Expired/deleted package opens a new create generation (no permanent dead-end)."""

        self._complete_package_mutation()
        self.assertEqual(
            run_output_package_create_generation(
                self.session,
                self.workflow,
                client=Mock(
                    run_output_package=Mock(
                        side_effect=ApiRejectedError(
                            "run_output_package_expired", "expired"
                        )
                    )
                ),
            ),
            1,
        )
        missing_client = Mock()
        missing_client.run_output_package.side_effect = ApiRejectedError(
            "run_output_package_not_found", "gone"
        )
        missing_client.run_output_package_for_run.side_effect = ApiRejectedError(
            "run_output_package_not_found", "missing"
        )
        resolved = _resolve_run_output_package(
            self.session, self.workflow, client=missing_client
        )
        self.assertIsNone(resolved)

        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package",
            side_effect=ApiRejectedError(
                "run_output_package_expired", "expired"
            ),
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package_for_run",
            side_effect=ApiRejectedError(
                "run_output_package_not_found", "missing"
            ),
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        body = page.content.decode("utf-8")
        self.assertIn("Download results package", body)
        self.assertIn(
            reverse("importer:create_run_output_package", args=[self.session.id]),
            body,
        )
        self.assertNotIn(self.package["package_id"], body)

    def test_client_rejects_mismatched_package_identity(self):
        http = Mock()
        http.get.side_effect = [
            response(200, {"status": "ok", "api_version": API_VERSION}),
            response(
                200,
                {
                    **self.package,
                    "package_id": "rop_other",
                    "run_id": "run-other",
                },
            ),
        ]
        api = EasyImportsApiClient(http=http)
        with self.assertRaises(ApiConsistencyError):
            api.run_output_package(
                self.package["package_id"],
                owner_session=f"django-{self.owner}",
            )

        http2 = Mock()
        http2.get.side_effect = [
            response(200, {"status": "ok", "api_version": API_VERSION}),
            response(200, {**self.package, "run_id": "run-other"}),
        ]
        api2 = EasyImportsApiClient(http=http2)
        with self.assertRaises(ApiConsistencyError):
            api2.run_output_package_for_run(
                self.package["run_id"],
                owner_session=f"django-{self.owner}",
            )

    def test_create_dispatch_rejects_wrong_run_binding(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_run_output_package",
            route=f"/v1/workflows/{self.workflow.run_id}/run-output-packages",
            logical_action_identity="pkg-bad-bind",
            request_json={
                "layout_version": "run_output_package.v1",
                "owner_session": f"django-{self.owner}",
            },
            workflow=self.workflow,
            resource_identity=self.workflow.run_id,
        )
        bad = {**self.package, "run_id": "run-not-this-workflow", "run_revision": 99}
        http = Mock()
        http.get.return_value = response(
            200, {"status": "ok", "api_version": API_VERSION}
        )
        http.post.return_value = response(201, bad)
        api = EasyImportsApiClient(http=http)
        with self.assertRaises(ApiConsistencyError):
            api.dispatch(mutation)

    def test_get_discovered_package_download_without_local_mutation(self):
        """Freeze §5.3: CTA from read-only GET must proxy without a local create."""

        stream = _PackageStream(
            self.zip_bytes,
            etag=f'"sha256:{self.package["content_digest"]}"',
        )
        self.assertFalse(
            ApiMutation.objects.filter(
                mutation_kind="create_run_output_package"
            ).exists()
        )
        with patch.object(
            EasyImportsApiClient, "run_output_package", return_value=self.package
        ), patch.object(
            EasyImportsApiClient, "stream_run_output_package", return_value=stream
        ):
            download = self.client.get(
                reverse(
                    "importer:run_output_package_content",
                    args=[self.session.id, self.package["package_id"]],
                )
            )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), self.zip_bytes)

    def test_get_discovered_package_shown_on_terminal(self):
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package_for_run",
            return_value=self.package,
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package",
            side_effect=ApiRejectedError(
                "run_output_package_not_found", "no create receipt"
            ),
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        body = page.content.decode("utf-8")
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.package["package_id"], body)
        self.assertIn(
            reverse(
                "importer:run_output_package_content",
                args=[self.session.id, self.package["package_id"]],
            ),
            body,
        )

    def test_transient_metadata_failure_does_not_crash_terminal(self):
        """Completed receipt + API outage during generation must still render."""

        self._complete_package_mutation()
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package",
            side_effect=ApiUnavailableError("API down"),
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package_for_run",
            side_effect=ApiUnavailableError("API down"),
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Import complete")

    def test_transient_metadata_failure_on_create_post_redirects_safely(self):
        with patch(
            "importer.workflow_views.refresh_workflow", return_value=self.workflow
        ), patch(
            "importer.workflow_views._resolve_run_output_package", return_value=None
        ), patch.object(
            EasyImportsApiClient,
            "run_output_package",
            side_effect=ApiUnavailableError("API down"),
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        token = self._extract_form_token(page.content.decode("utf-8"))
        with patch(
            "importer.workflow_views.run_output_package_create_generation",
            side_effect=ApiUnavailableError("API down"),
        ):
            posted = self.client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                data={"form_token": token},
            )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(
            posted["Location"],
            reverse("importer:workflow", args=[self.session.id]),
        )

    def test_content_proxy_other_session_forbidden(self):
        other_owner = uuid4()
        other = ImportSession.objects.create(
            owner_id=other_owner, product_key="easyimports.list_import"
        )
        other_client = Client()
        sess = other_client.session
        sess[OWNER_SESSION_KEY] = str(other_owner)
        sess.save()
        download = other_client.get(
            reverse(
                "importer:run_output_package_content",
                args=[self.session.id, self.package["package_id"]],
            )
        )
        self.assertEqual(download.status_code, 404)

    def test_client_strips_owner_session_from_package_create_body(self):
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="create_run_output_package",
            route=f"/v1/workflows/{self.workflow.run_id}/run-output-packages",
            logical_action_identity="pkg-wire-body",
            request_json={
                "layout_version": "run_output_package.v1",
                "owner_session": f"django-{self.owner}",
            },
            workflow=self.workflow,
            resource_identity=self.workflow.run_id,
        )
        http = Mock()
        http.get.return_value = response(
            200, {"status": "ok", "api_version": API_VERSION}
        )
        http.post.return_value = response(201, self.package)
        api = EasyImportsApiClient(http=http)
        api.dispatch(mutation)
        _args, kwargs = http.post.call_args
        body = kwargs.get("json") or {}
        self.assertEqual(body.get("layout_version"), "run_output_package.v1")
        self.assertNotIn("owner_session", body)
        headers = kwargs.get("headers") or {}
        self.assertEqual(headers.get("X-Owner-Session"), f"django-{self.owner}")
        self.assertTrue(headers.get("Idempotency-Key"))
