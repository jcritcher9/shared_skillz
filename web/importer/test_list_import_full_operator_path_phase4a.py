"""Phase 4A — Django FE resume UX for validation checkpoint (list-import path).

Network-free only. Proves:
- workflow page renders ``validation_failed`` decision (email edit + actions)
- submit builds strict decision command (submit_edits / exclude remaining)
- validation decision submit is journaled and is not an effect-authorize path
- template + contract characterize validation as a known decision type
- **Django→API boundary** (real API process): create list-import to validation
  stop; Django submit_edits and exclude_remaining are accepted by the API on the
  same workflow; refreshed projection advances; effect_grants stay empty

No live CRM. Does not flip product-default (FE-CM-4 remains product-default).

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    list_import_full_operator_path.md (Phase 4A)
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from importer.api_client import (
    EasyImportsApiClient,
    MutationDispatchResult,
    canonical_digest,
    create_or_reuse_mutation,
)
from importer.api_contract import (
    DECISION_TYPES,
    validate_decision_command,
    validate_workflow_create,
)
from importer.command_service import materialize_accepted_workflow
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.upload_service import save_and_register_upload
from importer.workflow_state import (
    owner_session_for_import_session,
    refresh_workflow,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = (
    REPO_ROOT
    / "mappings_2"
    / "codex_context"
    / "cross_agent_eval"
    / "project_implementations"
    / "list_import_full_operator_path.md"
)
WORKFLOW_TEMPLATE = (
    REPO_ROOT
    / "web"
    / "importer"
    / "templates"
    / "importer"
    / "workflow.html"
)
VALIDATION_DECISION_TYPE = "validation_failed"
VALIDATION_ACTIONS = ("submit_edits", "exclude_remaining_invalid_rows")


def _dataframe(columns, rows):
    return {"columns": columns, "rows": rows}


def _projection(**changes):
    value = {
        "run_id": "run-phase4a-validation",
        "revision": 3,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 7,
        "target_provider_id": "fake-preview-v1",
        "status": "needs_decision",
        "stage": "validate_people_list",
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


def _validation_decision(
    *,
    decision_id="decision-validation-phase4a",
    option_id="option-1",
    email="bad-not-an-email",
):
    row = {
        "option_id": option_id,
        "contact_email_final": email,
        "contact_first_name_final": "Ann",
        "contact_last_name_final": "Able",
        "account_name_final": "Acme",
        "validation_message": "Primary email is missing or is not a valid email address.",
        "validation_severity": "blocking",
        "validation_status": "still_invalid",
    }
    body = {
        "decision_id": decision_id,
        "decision_type": VALIDATION_DECISION_TYPE,
        "title": "Some rows need a valid primary email",
        "message": (
            "Correct the highlighted primary emails. Rows that remain invalid can "
            "be excluded and retained in the validation quarantine report."
        ),
        "rows_df": _dataframe(list(row), [row]),
        "columns": [
            {
                "name": key,
                "label": key,
                "visible": True,
                "editable": key == "contact_email_final",
            }
            for key in row
        ],
        "actions": list(VALIDATION_ACTIONS),
        "option_id_col": "option_id",
        "group_id_col": None,
        "status": "open",
        "resolved_group_ids": [],
        "audit_df": _dataframe(["event"], [{"event": "created"}]),
    }
    return {
        "decision_id": decision_id,
        "decision_type": VALIDATION_DECISION_TYPE,
        "phase_id": "validate_people_list",
        "body": body,
    }


def _receipt(*, outcome="rejected"):
    return {
        "command_id": "cmd-phase4a",
        "command_kind": "submit_decision",
        "run_id": "run-phase4a-validation",
        "revision": 3,
        "workflow_status": "needs_decision",
        "stage": "validate_people_list",
        "outcome": outcome,
        "error_code": None if outcome == "accepted" else "request_rejected",
        "message": None if outcome == "accepted" else "No",
        "resource": "/v1/workflows/run-phase4a-validation",
    }


class Phase4AValidationFeResumeTests(TestCase):
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

    @staticmethod
    def reject_dispatch(mutation, **_kwargs):
        payload = _receipt(outcome="rejected")
        mutation.state = ApiMutation.State.REJECTED
        mutation.response_json = payload
        mutation.response_digest = canonical_digest(payload)
        mutation.error_code = "request_rejected"
        mutation.error_message = "No"
        mutation.save()
        return MutationDispatchResult(mutation, payload)

    def test_strategy_and_template_characterize_validation_resume(self):
        self.assertTrue(STRATEGY_PATH.is_file())
        strategy = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("4A", strategy)
        self.assertIn("validation", strategy.lower())
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('dtype == "validation_failed"', template)
        self.assertIn("email__", template)
        self.assertIn("submit_edits", "\n".join(VALIDATION_ACTIONS))
        self.assertIn(VALIDATION_DECISION_TYPE, DECISION_TYPES)

    def test_renders_validation_failed_decision_with_email_edit(self):
        decision = _validation_decision()
        value = _projection(decision=decision)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, decision["body"]["title"])
        self.assertContains(page, "Email edit")
        self.assertContains(page, 'name="email__option-1"')
        self.assertContains(page, "bad-not-an-email")
        self.assertContains(page, "submit_edits")
        self.assertContains(page, "exclude_remaining_invalid_rows")
        # Effect authorize is a separate surface — not shown as the primary form.
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertNotIn(b"Authorize effect", customer)
        self.assertNotIn(b"people_writes", customer)

    def test_submit_edits_builds_validation_decision_command(self):
        decision = _validation_decision()
        value = _projection(decision=decision)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        token = page.context["tokens"]["decision"]
        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=self.reject_dispatch,
        ):
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": token,
                    "action": "submit_edits",
                    "email__option-1": "ann@example.com",
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="submit_decision")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["decision_type"], VALIDATION_DECISION_TYPE)
        self.assertEqual(validated["decision_id"], decision["decision_id"])
        self.assertEqual(validated["expected_revision"], workflow.revision)
        self.assertEqual(validated["response"]["action"], "submit_edits")
        rows = validated["response"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["option_id"], "option-1")
        self.assertEqual(
            rows[0]["changes"]["contact_email_final"],
            "ann@example.com",
        )
        # Journaled decision submit — not authorize_effect.
        self.assertEqual(mutation.mutation_kind, "submit_decision")
        self.assertNotEqual(mutation.mutation_kind, "authorize_effect")

    def test_exclude_remaining_invalid_builds_quarantine_action_command(self):
        decision = _validation_decision()
        value = _projection(decision=decision)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["decision"]
        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=self.reject_dispatch,
        ):
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": token,
                    "action": "exclude_remaining_invalid_rows",
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="submit_decision")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["decision_type"], VALIDATION_DECISION_TYPE)
        self.assertEqual(
            validated["response"]["action"],
            "exclude_remaining_invalid_rows",
        )
        # Quarantine action may omit row edits; never confuses with write auth.
        self.assertNotIn("effect_mode", validated)
        self.assertNotIn("authorize", str(validated).lower())


PEOPLE_MATCHING_CATALOG = "easyimports.people_matching_fields.v1"
_HEADER_TO_CHOICE = {
    "Email": f"catalog:{PEOPLE_MATCHING_CATALOG}:email",
    "First Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:first_name",
    "Last Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:last_name",
    "Company": f"catalog:{PEOPLE_MATCHING_CATALOG}:company_name",
}


class Phase4ADjangoApiBoundaryTests(TransactionTestCase):
    """Real API process + Django client: validation resume crosses the boundary.

    Does **not** mock EasyImportsApiClient.dispatch. Proves the exact Django
    decision command is accepted by the API and applied to the same run.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        cls.state_dir = tempfile.TemporaryDirectory()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = cls.state_dir.name
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health = f"{cls.base_url}/health"
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            cls.process.terminate()
            raise RuntimeError("Phase 4A API process did not start.")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)
        cls.state_dir.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def _owner_headers(self, session: ImportSession, *, key: str) -> dict[str, str]:
        return {
            "X-Owner-Session": owner_session_for_import_session(session),
            "Idempotency-Key": key,
        }

    def _confirm_people_matching_bind(
        self, session: ImportSession, headers: list[str], *, key_prefix: str
    ) -> dict[str, str]:
        create = requests.post(
            f"{self.base_url}/v1/column-mapping-plans",
            json={
                "source_schema": [
                    {
                        "source_ordinal": index,
                        "source_header": header,
                        "sample_values": [],
                    }
                    for index, header in enumerate(headers)
                ],
                "destination": {
                    "destination_mode": "catalog",
                    "catalog_id": PEOPLE_MATCHING_CATALOG,
                },
                "run_auto_detect": False,
            },
            headers=self._owner_headers(session, key=f"{key_prefix}-create"),
            timeout=15,
        )
        self.assertEqual(create.status_code, 201, create.text)
        plan = create.json()
        plan_id = plan["plan_id"]
        for index, header in enumerate(headers):
            choice = _HEADER_TO_CHOICE.get(header)
            if choice is None:
                choice = "system:ignore"
            patched = requests.post(
                f"{self.base_url}/v1/column-mapping-plans/{plan_id}/rows/{index}",
                json={"mapping_choice_id": choice},
                headers=self._owner_headers(
                    session, key=f"{key_prefix}-row-{index}"
                ),
                timeout=15,
            )
            self.assertEqual(patched.status_code, 200, patched.text)
            plan = patched.json()
        digest = str(plan["plan_content_digest"])
        confirmed = requests.post(
            f"{self.base_url}/v1/column-mapping-plans/{plan_id}/confirm",
            json={"plan_content_digest": digest},
            headers=self._owner_headers(session, key=f"{key_prefix}-confirm"),
            timeout=15,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        plan = confirmed.json()
        digests = plan.get("confirmed_digests") or {}
        return {
            "plan_id": plan["plan_id"],
            "plan_content_digest": str(
                digests.get("plan_content_digest") or plan["plan_content_digest"]
            ),
            "source_schema_digest": str(
                digests.get("source_schema_digest") or plan["source_schema_digest"]
            ),
            "destination_digest": str(
                digests.get("destination_digest") or plan["destination_digest"]
            ),
            "target_contract_digest": str(
                digests.get("target_contract_digest")
                or plan["target_contract_digest"]
            ),
        }

    def _start_list_import_at_validation(
        self,
        *,
        raw_csv: bytes,
        contacts_csv: bytes,
        case_id: str,
    ):
        with self.settings(
            EASYIMPORTS_API_BASE_URL=self.base_url,
            SESSIONS_ROOT=Path(tempfile.mkdtemp()),
        ):
            session = ImportSession.objects.create(
                owner_id=self.owner,
                product_key="easyimports.list_import",
                target_provider_id="fake-preview-v1",
                operator_label="Phase4A Boundary Operator",
                setup_entity="people",
                setup_operation="crm_matching",
                setup_reference_source="uploaded",
            )
            api = EasyImportsApiClient()
            raw_reg = save_and_register_upload(
                session=session,
                role="raw_list",
                uploaded=SimpleUploadedFile(
                    "people.csv", raw_csv, content_type="text/csv"
                ),
                form_instance=uuid4(),
                logical_action_generation=0,
                csv_encoding="utf-8-sig",
                xlsx_sheet_index=0,
                client=api,
            )
            contacts_reg = save_and_register_upload(
                session=session,
                role="contacts",
                uploaded=SimpleUploadedFile(
                    "contacts.csv", contacts_csv, content_type="text/csv"
                ),
                form_instance=uuid4(),
                logical_action_generation=0,
                csv_encoding="utf-8-sig",
                xlsx_sheet_index=0,
                client=api,
            )
            raw_upload = raw_reg.response
            headers = list(raw_upload.get("columns") or [])
            self.assertTrue(headers, "upload must expose columns")
            bind = self._confirm_people_matching_bind(
                session, headers, key_prefix=f"map-{case_id}"
            )
            body = validate_workflow_create(
                {
                    "product_key": "easyimports.list_import",
                    "target_provider_id": "fake-preview-v1",
                    "uploads": {
                        "raw_list": raw_reg.response["upload_id"],
                        "contacts": contacts_reg.response["upload_id"],
                    },
                    "maximum_modes": {
                        "reference_acquisition": "disabled",
                        "account_provisioning": "disabled",
                        "person_duplicate_resolution": "disabled",
                        "people_writes": "disabled",
                        "campaign_member_writes": "disabled",
                        "delivery": "disabled",
                    },
                    "options": {
                        "interaction_mode": "interactive",
                        "validate_list_rows": True,
                        "list_duplicate_policy": "surface",
                    },
                    "column_mapping": bind,
                }
            )
            creation = create_or_reuse_mutation(
                session=session,
                form_instance=uuid4(),
                mutation_kind="create_workflow",
                route="/v1/workflows",
                logical_action_identity=f"session:{session.id}:create_workflow:{case_id}",
                form_payload_digest=canonical_digest(body),
                request_json=body,
            )
            created = api.dispatch(creation)
            self.assertEqual(created.mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(created.response.get("outcome"), "accepted")
            workflow = materialize_accepted_workflow(session, created, client=api)
            self.assertIsNotNone(workflow)
            self.assertEqual(workflow.status, "needs_decision")
            decision = workflow.projection["decision"]
            self.assertEqual(decision["decision_type"], VALIDATION_DECISION_TYPE)
            self.assertEqual(decision["phase_id"], "validate_people_list")
            self.assertEqual(workflow.projection.get("effect_grants") or [], [])
            self.assertIsNone(workflow.projection.get("effect_intent"))
            return session, workflow, api

    def _assert_no_write_authorization(self, projection: dict) -> None:
        grants = projection.get("effect_grants") or []
        self.assertEqual(grants, [])
        self.assertIsNone(projection.get("effect_intent"))
        # Track modes remaining disabled is reflected by no authorize surface.
        summary = projection.get("summary") or {}
        self.assertNotIn("authorized_people_writes", summary)

    def _poll_pending_mutations(
        self, session: ImportSession, *, timeout: float = 60.0
    ) -> None:
        """Drive mutation_status until Prefer:respond-async decisions complete."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            session.refresh_from_db()
            open_mutations = list(
                session.api_mutations.filter(
                    state__in=[
                        ApiMutation.State.PENDING,
                        ApiMutation.State.UNKNOWN,
                    ]
                )
            )
            if not open_mutations:
                return
            for mutation in open_mutations:
                if mutation.state == ApiMutation.State.PENDING:
                    response = self.client.get(
                        reverse(
                            "importer:mutation_status",
                            args=[session.id, mutation.id],
                        )
                    )
                    self.assertEqual(response.status_code, 200, response.content)
                    continue
                api = EasyImportsApiClient()
                try:
                    api.dispatch(mutation, explicit_retry=True)
                except Exception:
                    time.sleep(0.1)
            time.sleep(0.05)
        open_left = list(
            session.api_mutations.filter(
                state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN]
            )
        )
        self.fail(
            "Timed out waiting for pending mutations: "
            + ", ".join(f"{m.mutation_kind}:{m.state}" for m in open_left)
        )

    def test_django_quarantine_resume_accepted_by_api_same_workflow(self):
        raw_csv = (
            b"Email,First Name,Last Name,Company\n"
            b"bad-one,Ann,Able,Acme\n"
            b"also-bad,Ben,Baker,Beta\n"
        )
        contacts_csv = (
            b"Contact ID,Email,First Name,Last Name,Account Name\n"
            b"C1,existing@other.example,Existing,Person,Other Co\n"
        )
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_list_import_at_validation(
                raw_csv=raw_csv,
                contacts_csv=contacts_csv,
                case_id="quarantine",
            )
            run_id = workflow.run_id
            revision_before = workflow.revision
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "valid primary email")
            token = page.context["tokens"]["decision"]
            # Real dispatch — no mock.
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": token,
                    "action": "exclude_remaining_invalid_rows",
                },
            )
            self.assertEqual(submitted.status_code, 302, submitted.content[:500])
            # submit_decision uses Prefer: respond-async → poll to completion.
            self._poll_pending_mutations(session)
            mutation = session.api_mutations.get(mutation_kind="submit_decision")
            self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(mutation.response_json.get("outcome"), "accepted")
            validated = validate_decision_command(mutation.request_json)
            self.assertEqual(validated["decision_type"], VALIDATION_DECISION_TYPE)
            self.assertEqual(
                validated["response"]["action"], "exclude_remaining_invalid_rows"
            )
            self.assertEqual(validated["expected_revision"], revision_before)
            # Same workflow run advanced under the API.
            self.assertEqual(mutation.response_json.get("run_id"), run_id)
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            decision = refreshed.projection.get("decision")
            if refreshed.status == "needs_decision":
                self.assertIsNotNone(decision)
                self.assertNotEqual(
                    decision.get("decision_type"), VALIDATION_DECISION_TYPE
                )
            else:
                self.assertIn(
                    refreshed.status,
                    {"succeeded", "awaiting_effect_authorization", "running"},
                )
            self._assert_no_write_authorization(refreshed.projection)

    def test_django_edit_resume_accepted_by_api_same_workflow(self):
        raw_csv = (
            b"Email,First Name,Last Name,Company\n"
            b"bad-one,Ann,Able,Acme\n"
            b"good@example.com,Ben,Baker,Beta\n"
        )
        contacts_csv = (
            b"Contact ID,Email,First Name,Last Name,Account Name\n"
            b"C1,existing@other.example,Existing,Person,Other Co\n"
        )
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_list_import_at_validation(
                raw_csv=raw_csv,
                contacts_csv=contacts_csv,
                case_id="edit",
            )
            run_id = workflow.run_id
            revision_before = workflow.revision
            decision = workflow.projection["decision"]
            body = decision["body"]
            rows = body["rows_df"]["rows"]
            self.assertGreaterEqual(len(rows), 1)
            option_col = body.get("option_id_col") or "option_id"
            option_id = str(rows[0][option_col])
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertEqual(page.status_code, 200)
            token = page.context["tokens"]["decision"]
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[session.id]),
                {
                    "form_token": token,
                    "action": "submit_edits",
                    f"email__{option_id}": "ann.fixed@example.com",
                },
            )
            self.assertEqual(submitted.status_code, 302, submitted.content[:500])
            self._poll_pending_mutations(session)
            mutation = session.api_mutations.get(mutation_kind="submit_decision")
            self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(mutation.response_json.get("outcome"), "accepted")
            validated = validate_decision_command(mutation.request_json)
            self.assertEqual(validated["decision_type"], VALIDATION_DECISION_TYPE)
            self.assertEqual(validated["response"]["action"], "submit_edits")
            self.assertEqual(validated["expected_revision"], revision_before)
            self.assertEqual(
                validated["response"]["rows"][0]["changes"]["contact_email_final"],
                "ann.fixed@example.com",
            )
            self.assertEqual(mutation.response_json.get("run_id"), run_id)
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            # Mixed frame had one invalid; fixing it clears the validation stop.
            decision_after = refreshed.projection.get("decision")
            if refreshed.status == "needs_decision":
                self.assertNotEqual(
                    (decision_after or {}).get("decision_type"),
                    VALIDATION_DECISION_TYPE,
                )
            self._assert_no_write_authorization(refreshed.projection)
