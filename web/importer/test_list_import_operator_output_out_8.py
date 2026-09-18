"""OUT-8 Django surface — mutation-kind pending copy.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from importer.api_client import create_or_reuse_mutation
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.pending_copy import (
    GENERIC_HEADLINE,
    MERGE_HEADLINE,
    PACKAGE_BODY,
    PACKAGE_HEADLINE,
    SETUP_BODY,
    SETUP_HEADLINE,
    UNKNOWN_HEADLINE,
    attach_pending_copy,
    contains_cleaning_sentence,
    html_has_pending_mutation_surface,
    in_progress_flash_message,
    pending_copy_for_mutation,
)
from importer.workflow_views import canonical_digest

WORKFLOW_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "importer" / "workflow.html"
)
PENDING_COPY_PATH = Path(__file__).with_name("pending_copy.py")
API_CLIENT_PATH = Path(__file__).with_name("api_client.py")


def _projection(**changes):
    value = {
        "run_id": "run-out8-pending",
        "revision": 3,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 7,
        "target_provider_id": "fake-preview-v1",
        "status": "running",
        "stage": "prepare",
        "decision": None,
        "effect_intent": None,
        "effect_grants": [],
        "review_handoff": None,
        "terminal_evidence": None,
        "summary": {},
        "error": None,
        "links": {},
    }
    value.update(changes)
    return value


class Out8PendingCopyContractTests(SimpleTestCase):
    def test_pending_copy_does_not_import_mappings_2(self) -> None:
        source = PENDING_COPY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import mappings_2", source)
        self.assertNotIn("from mappings_2", source)
        client = API_CLIENT_PATH.read_text(encoding="utf-8")
        self.assertIn("in_progress_flash_message", client)
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("data-mutation-kind", template)
        self.assertIn("pending_copy.headline", template)

    def test_create_workflow_uses_setup_copy(self) -> None:
        copy = pending_copy_for_mutation(
            SimpleNamespace(state="pending", mutation_kind="create_workflow")
        )
        self.assertEqual(copy["headline"], SETUP_HEADLINE)
        self.assertIn("Setup is all done", copy["headline"])
        self.assertEqual(copy["body"], SETUP_BODY)
        self.assertNotEqual(copy["headline"], GENERIC_HEADLINE)

    def test_write_and_package_never_use_cleaning_sentence(self) -> None:
        package = pending_copy_for_mutation(
            SimpleNamespace(
                state="pending", mutation_kind="create_run_output_package"
            )
        )
        self.assertEqual(package["headline"], PACKAGE_HEADLINE)
        self.assertEqual(package["body"], PACKAGE_BODY)
        self.assertFalse(contains_cleaning_sentence(package["headline"]))
        self.assertFalse(contains_cleaning_sentence(package["body"]))
        merge = pending_copy_for_mutation(
            SimpleNamespace(
                state="pending",
                mutation_kind="authorize_effect",
                request_json={"track": "duplicate_execution"},
            )
        )
        self.assertEqual(merge["headline"], MERGE_HEADLINE)
        self.assertFalse(contains_cleaning_sentence(merge["headline"]))
        self.assertFalse(contains_cleaning_sentence(merge["body"]))
        writes = pending_copy_for_mutation(
            SimpleNamespace(
                state="pending",
                mutation_kind="authorize_effect",
                request_json={"track": "people_writes"},
            )
        )
        self.assertEqual(writes["headline"], GENERIC_HEADLINE)
        self.assertFalse(contains_cleaning_sentence(writes["body"]))
        self.assertFalse(
            contains_cleaning_sentence(
                in_progress_flash_message(
                    SimpleNamespace(
                        state="pending",
                        mutation_kind="create_run_output_package",
                    )
                )
            )
        )

    def test_unknown_state_keeps_exact_retry_copy(self) -> None:
        copy = pending_copy_for_mutation(
            SimpleNamespace(state="unknown", mutation_kind="create_workflow")
        )
        self.assertEqual(copy["headline"], UNKNOWN_HEADLINE)
        self.assertIn("uncertain", copy["body"])

    def test_dual_process_probe_uses_hooks_not_retired_headline(self) -> None:
        spinning = (
            '<section data-mutation-kind="create_workflow" data-mutation-pending>'
            "<h2>Setup is all done!</h2></section>"
        )
        self.assertTrue(html_has_pending_mutation_surface(spinning))
        self.assertFalse(
            html_has_pending_mutation_surface(
                "<h1>Review required</h1><p>No pending mutation.</p>"
            )
        )
        self.assertFalse(
            html_has_pending_mutation_surface("<p>Action in progress</p>")
        )


class Out8PendingCardRenderTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def make_session_workflow(self, value):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=value["workflow_key"],
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
        )
        workflow = ApiWorkflow.objects.create(
            session=session,
            role=ApiWorkflow.Role.PRIMARY,
            run_id=value["run_id"],
            workflow_key=value["workflow_key"],
            workflow_version=value["workflow_version"],
            target_provider_id=value.get("target_provider_id") or "",
            status=value["status"],
            stage=value["stage"],
            revision=value["revision"],
            resource_url=f"/v1/workflows/{value['run_id']}",
            projection=value,
            projection_digest=canonical_digest(value),
        )
        session.active_workflow = workflow
        session.save(update_fields=["active_workflow"])
        return session, workflow

    def _pending(
        self,
        session,
        workflow,
        *,
        kind: str,
        state: str = ApiMutation.State.PENDING,
        request_json: dict | None = None,
        http_status: int | None = 202,
    ) -> ApiMutation:
        mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind=kind,
            route=f"/v1/workflows/{workflow.run_id}/{kind}",
            logical_action_identity=f"{kind}:{uuid4()}",
            request_json=request_json or {"expected_revision": workflow.revision},
        )
        mutation.state = state
        mutation.http_status = http_status
        mutation.save(update_fields=["state", "http_status", "updated_at"])
        return mutation

    def test_create_workflow_card_says_setup_is_all_done(self) -> None:
        value = _projection()
        session, workflow = self.make_session_workflow(value)
        self._pending(session, workflow, kind="create_workflow")
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Setup is all done", customer)
        self.assertIn(b"cleaning and importing", customer)
        self.assertIn(b'data-mutation-kind="create_workflow"', customer)
        self.assertNotIn(b"<h2>Action in progress</h2>", customer)

    def test_duplicate_execution_authorize_does_not_use_cleaning_copy(self) -> None:
        value = _projection()
        session, workflow = self.make_session_workflow(value)
        self._pending(
            session,
            workflow,
            kind="authorize_effect",
            request_json={
                "expected_revision": workflow.revision,
                "track": "duplicate_execution",
                "selected_mode": "dry_run",
            },
        )
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Authorizing CRM merges", customer)
        self.assertNotIn(b"cleaning and importing", customer)
        self.assertNotIn(b"Setup is all done", customer)

    def test_package_create_says_preparing_results(self) -> None:
        value = _projection(status="succeeded", stage="complete")
        session, workflow = self.make_session_workflow(value)
        self._pending(session, workflow, kind="create_run_output_package")
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Preparing your results", customer)
        self.assertIn(b"does not change CRM records", customer)
        self.assertNotIn(b"cleaning and importing", customer)
        self.assertNotIn(b"Setup is all done", customer)

    def test_unknown_offers_retry_saved_action(self) -> None:
        value = _projection()
        session, workflow = self.make_session_workflow(value)
        self._pending(
            session,
            workflow,
            kind="create_workflow",
            state=ApiMutation.State.UNKNOWN,
            http_status=None,
        )
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Action needs confirmation", customer)
        self.assertIn(b"uncertain", customer)
        self.assertIn(b"Retry saved action", customer)
        self.assertNotIn(b"Setup is all done", customer)

    def test_poller_reuses_kind_specific_body(self) -> None:
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("card.dataset.pendingBody", template)
        self.assertIn("data-mutation-kind", template)
        attach_pending_copy(
            SimpleNamespace(state="pending", mutation_kind="create_workflow")
        )
        self.assertIn(
            "in_progress_flash_message",
            API_CLIENT_PATH.read_text(encoding="utf-8"),
        )
