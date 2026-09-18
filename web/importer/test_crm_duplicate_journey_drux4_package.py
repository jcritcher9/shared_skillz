"""DRUX-4: Django terminal package CTA binds to the merge continuation."""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .api_client import EasyImportsApiClient, MutationDispatchResult, canonical_digest
from .models import (
    ApiMutation,
    ApiWorkflow,
    CrmDuplicateMergePlanLease,
    ImportSession,
)
from .tests import projection
from .workflow_state import OWNER_SESSION_KEY
from .workflow_views import (
    RUN_OUTPUT_PACKAGE_LAYOUT,
    RUN_OUTPUT_PACKAGE_LAYOUT_V2,
    _package_matches_workflow,
    _resolve_run_output_package,
)


def _package_resource(**changes) -> dict:
    content = b"PK\x03\x04fake-zip-bytes"
    digest = sha256(content).hexdigest()
    value = {
        "package_id": "rop_duperesults001",
        "run_id": "run-cont-drux4",
        "run_revision": 4,
        "workflow_key": "easyimports.duplicate_resolution",
        "layout_version": RUN_OUTPUT_PACKAGE_LAYOUT_V2,
        "content_digest": digest,
        "byte_count": len(content),
        "created_at": "2026-08-12T12:00:00Z",
        "expires_at": "2026-09-11T12:00:00Z",
        "included_files": [
            {
                "path": "duplicates/groups.csv",
                "content_digest": sha256(b"g").hexdigest(),
                "byte_count": 1,
            }
        ],
        "status": "available",
        "download_url": "/v1/run-output-packages/rop_duperesults001/content",
    }
    value.update(changes)
    return value


class CrmDuplicateJourneyDrux4PackageTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session[OWNER_SESSION_KEY] = str(self.owner)
        browser_session.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="DRUX-4 package",
            product_key="easyimports.duplicate_resolution",
        )
        primary_value = projection(
            run_id="run-analysis-drux4",
            revision=2,
            workflow_key="easyimports.duplicate_resolution",
            status="succeeded",
            stage="complete",
        )
        self.primary = ApiWorkflow.objects.create(
            session=self.session,
            run_id=primary_value["run_id"],
            role=ApiWorkflow.Role.PRIMARY,
            workflow_key=primary_value["workflow_key"],
            workflow_version=primary_value["workflow_version"],
            target_provider_id="fake",
            status=primary_value["status"],
            stage=primary_value["stage"],
            revision=primary_value["revision"],
            resource_url=f"/v1/workflows/{primary_value['run_id']}",
            projection=primary_value,
            projection_digest=canonical_digest(primary_value),
        )
        cont_value = projection(
            run_id="run-cont-drux4",
            revision=4,
            workflow_key="easyimports.duplicate_resolution",
            status="succeeded",
            stage="complete",
            terminal_evidence={
                "workflow_key": "easyimports.duplicate_resolution",
                "workflow_version": 4,
                "receipts": [],
                "accountability": {"dispositions": []},
                "delivery_manifest": None,
            },
        )
        self.continuation = ApiWorkflow.objects.create(
            session=self.session,
            run_id=cont_value["run_id"],
            role=ApiWorkflow.Role.CONTINUATION,
            source_workflow=self.primary,
            workflow_key=cont_value["workflow_key"],
            workflow_version=cont_value["workflow_version"],
            target_provider_id="fake",
            status=cont_value["status"],
            stage=cont_value["stage"],
            revision=cont_value["revision"],
            resource_url=f"/v1/workflows/{cont_value['run_id']}",
            projection=cont_value,
            projection_digest=canonical_digest(cont_value),
        )
        self.session.active_workflow = self.primary
        self.session.save(update_fields=["active_workflow"])
        CrmDuplicateMergePlanLease.objects.create(
            session=self.session,
            epoch=0,
            continuation_run_id=self.continuation.run_id,
            decision_set_content_digest="sha256:deadbeef",
        )
        self.package = _package_resource()

    def test_terminal_copy_and_cta_use_continuation(self):
        with patch(
            "importer.workflow_views.refresh_workflow",
            side_effect=lambda session, workflow: workflow,
        ), patch(
            "importer.workflow_views._resolve_run_output_package",
            return_value=None,
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("Download results package", body)
        self.assertIn(
            "A single downloadable package of duplicate groups, chosen survivors,",
            body,
        )
        self.assertNotIn("prepared import files", body)
        self.assertIn(
            reverse("importer:create_run_output_package", args=[self.session.id]),
            body,
        )

    def test_missing_continuation_hides_cta(self):
        CrmDuplicateMergePlanLease.objects.filter(session=self.session).update(
            continuation_run_id=""
        )
        with patch(
            "importer.workflow_views.refresh_workflow",
            side_effect=lambda session, workflow: workflow,
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        body = page.content.decode("utf-8")
        self.assertNotIn("Download results package", body)
        self.assertNotIn("prepared import files", body)

    def test_create_posts_v2_against_continuation(self):
        with patch(
            "importer.workflow_views.refresh_workflow",
            side_effect=lambda session, workflow: workflow,
        ), patch(
            "importer.workflow_views._resolve_run_output_package",
            return_value=None,
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        html = page.content.decode("utf-8")
        marker = 'name="form_token" value="'
        token_start = html.find(marker)
        self.assertNotEqual(token_start, -1)
        token = html[token_start + len(marker) :].split('"', 1)[0]

        def fake_dispatch(mutation):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.http_status = 201
            mutation.response_json = self.package
            mutation.response_digest = canonical_digest(self.package)
            mutation.error_code = ""
            mutation.error_message = ""
            mutation.save()
            return MutationDispatchResult(mutation, self.package)

        with patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            posted = self.client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(posted.status_code, 302)
        mutation = ApiMutation.objects.get(mutation_kind="create_run_output_package")
        self.assertEqual(
            mutation.route,
            f"/v1/workflows/{self.continuation.run_id}/run-output-packages",
        )
        self.assertEqual(
            mutation.request_json.get("layout_version"), RUN_OUTPUT_PACKAGE_LAYOUT_V2
        )
        self.assertNotIn("run-analysis-drux4", mutation.route)

    def test_package_matches_workflow_rejects_duplicate_v1(self):
        v1 = _package_resource(layout_version=RUN_OUTPUT_PACKAGE_LAYOUT)
        self.assertTrue(_package_matches_workflow(v1, self.continuation))
        self.assertFalse(
            _package_matches_workflow(
                v1,
                self.continuation,
                expected_layout=RUN_OUTPUT_PACKAGE_LAYOUT_V2,
            )
        )
        self.assertTrue(
            _package_matches_workflow(
                self.package,
                self.continuation,
                expected_layout=RUN_OUTPUT_PACKAGE_LAYOUT_V2,
            )
        )

    def test_resolve_ignores_legacy_duplicate_v1(self):
        from .api_client import ApiRejectedError

        v1 = _package_resource(layout_version=RUN_OUTPUT_PACKAGE_LAYOUT)
        api = Mock()
        api.run_output_package_for_run.return_value = v1
        api.run_output_package.side_effect = ApiRejectedError(
            "run_output_package_not_found", "missing"
        )
        resolved = _resolve_run_output_package(
            self.session, self.continuation, client=api
        )
        self.assertIsNone(resolved)

    def test_create_rejects_v1_response_on_duplicate_session(self):
        with patch(
            "importer.workflow_views.refresh_workflow",
            side_effect=lambda session, workflow: workflow,
        ), patch(
            "importer.workflow_views._resolve_run_output_package",
            return_value=None,
        ):
            page = self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )
        html = page.content.decode("utf-8")
        marker = 'name="form_token" value="'
        token = html[html.find(marker) + len(marker) :].split('"', 1)[0]
        v1 = _package_resource(layout_version=RUN_OUTPUT_PACKAGE_LAYOUT)

        def fake_dispatch(mutation):
            mutation.state = ApiMutation.State.COMPLETED
            mutation.http_status = 201
            mutation.response_json = v1
            mutation.response_digest = canonical_digest(v1)
            mutation.error_code = ""
            mutation.error_message = ""
            mutation.save()
            return MutationDispatchResult(mutation, v1)

        with patch.object(EasyImportsApiClient, "dispatch", side_effect=fake_dispatch):
            posted = self.client.post(
                reverse("importer:create_run_output_package", args=[self.session.id]),
                {"form_token": token},
            )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(
            posted["Location"],
            reverse("importer:workflow", args=[self.session.id]),
        )
        self.assertNotIn("run_output_package_content", posted["Location"])
