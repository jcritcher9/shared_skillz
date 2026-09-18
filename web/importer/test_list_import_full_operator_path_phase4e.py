"""Phase 4E — Django FE multi-Person review + Django→API boundary.

Network-free only. Proves:
- workflow page renders multi-Person choices (candidate / create-as-new / quarantine)
- submit builds grouped submit_selections command (journaled; not effect authorize)
- **Django→API boundary** (real API process, no mocked dispatch):
  - create-as-new accepted on same run_id; revision advances; empty write grants
  - quarantine accepted on same run_id; revision advances; empty write grants

No live CRM. FE-CM-4 remains product-default.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    list_import_full_operator_path.md (Phase 4E)
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
    REPO_ROOT / "web" / "importer" / "templates" / "importer" / "workflow.html"
)
MULTI_PERSON_DECISION_TYPE = "multiple_crm_matches"
ACTION = "submit_selections"
CANDIDATE_KIND = "candidate"
CREATE_AS_NEW_KIND = "continue_unmatched"
QUARANTINE_KIND = "quarantine"
CANDIDATE_LABEL = "Use this CRM person"
CREATE_AS_NEW_LABEL = "None of these people; create the uploaded person as new"
QUARANTINE_LABEL = "Set this uploaded row aside"
PEOPLE_MATCHING_CATALOG = "easyimports.people_matching_fields.v1"
_HEADER_TO_CHOICE = {
    "Email": f"catalog:{PEOPLE_MATCHING_CATALOG}:email",
    "First Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:first_name",
    "Last Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:last_name",
    "Company": f"catalog:{PEOPLE_MATCHING_CATALOG}:company_name",
    "Contact Domain": "system:ignore",
}


def _dataframe(columns, rows):
    return {"columns": columns, "rows": rows}


def _projection(**changes):
    value = {
        "run_id": "run-phase4e-multi-person",
        "revision": 3,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 7,
        "target_provider_id": "fake-preview-v1",
        "status": "needs_decision",
        "stage": "review_crm_people_matches",
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


def _multi_person_decision(*, decision_id="decision-multi-person-phase4e"):
    rows = [
        {
            "option_id": "opt-p1",
            "group_id": "group-1",
            "option_kind": CANDIDATE_KIND,
            "option_label": CANDIDATE_LABEL,
            "selected": True,
            "is_recommended": True,
            "candidate_person_full_name": "First Candidate",
            "candidate_person_email": "shared@example.com",
            "incoming_contact_full_name": "Uploaded Person",
            "incoming_contact_email": "shared@example.com",
        },
        {
            "option_id": "opt-p2",
            "group_id": "group-1",
            "option_kind": CANDIDATE_KIND,
            "option_label": CANDIDATE_LABEL,
            "selected": False,
            "is_recommended": False,
            "candidate_person_full_name": "Second Candidate",
            "candidate_person_email": "shared@example.com",
            "incoming_contact_full_name": "Uploaded Person",
            "incoming_contact_email": "shared@example.com",
        },
        {
            "option_id": "opt-create",
            "group_id": "group-1",
            "option_kind": CREATE_AS_NEW_KIND,
            "option_label": CREATE_AS_NEW_LABEL,
            "selected": False,
            "is_recommended": False,
            "candidate_person_full_name": None,
            "candidate_person_email": None,
            "incoming_contact_full_name": "Uploaded Person",
            "incoming_contact_email": "shared@example.com",
        },
        {
            "option_id": "opt-quarantine",
            "group_id": "group-1",
            "option_kind": QUARANTINE_KIND,
            "option_label": QUARANTINE_LABEL,
            "selected": False,
            "is_recommended": False,
            "candidate_person_full_name": None,
            "candidate_person_email": None,
            "incoming_contact_full_name": "Uploaded Person",
            "incoming_contact_email": "shared@example.com",
        },
    ]
    columns = list(rows[0].keys())
    body = {
        "decision_id": decision_id,
        "decision_type": MULTI_PERSON_DECISION_TYPE,
        "title": "Choose the CRM match for each uploaded row",
        "message": (
            "The system recommendation starts selected. Keep it, select a "
            "different candidate, create the uploaded person as new, or "
            "quarantine the uploaded row."
        ),
        "rows_df": _dataframe(columns, rows),
        "columns": [
            {"name": key, "label": key, "visible": True, "editable": False}
            for key in columns
        ],
        "actions": [ACTION],
        "option_id_col": "option_id",
        "group_id_col": "group_id",
        "status": "open",
        "resolved_group_ids": [],
        "audit_df": _dataframe(["event"], [{"event": "created"}]),
    }
    return {
        "decision_id": decision_id,
        "decision_type": MULTI_PERSON_DECISION_TYPE,
        "phase_id": "review_crm_people_matches",
        "body": body,
    }


def _receipt(*, outcome="rejected"):
    return {
        "command_id": "cmd-phase4e",
        "command_kind": "submit_decision",
        "run_id": "run-phase4e-multi-person",
        "revision": 3,
        "workflow_status": "needs_decision",
        "stage": "review_crm_people_matches",
        "outcome": outcome,
        "error_code": None if outcome == "accepted" else "request_rejected",
        "message": None if outcome == "accepted" else "No",
        "resource": "/v1/workflows/run-phase4e-multi-person",
    }


class Phase4EMultiPersonFeTests(TestCase):
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

    def test_strategy_and_template_characterize_multi_person(self):
        self.assertTrue(STRATEGY_PATH.is_file())
        strategy = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("4E", strategy)
        self.assertIn("multi-person", strategy.lower().replace(" ", "-"))
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("multiple_crm_matches", template)
        self.assertIn(MULTI_PERSON_DECISION_TYPE, DECISION_TYPES)

    def test_renders_candidate_create_as_new_and_quarantine(self):
        decision = _multi_person_decision()
        value = _projection(decision=decision)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, decision["body"]["title"])
        self.assertContains(page, CANDIDATE_LABEL)
        self.assertContains(page, CREATE_AS_NEW_LABEL)
        self.assertContains(page, QUARANTINE_LABEL)
        self.assertContains(page, 'name="selected__0"')
        groups = page.context["decision_groups"]
        kinds = {row["option_kind"] for row in groups[0]["rows"]}
        self.assertEqual(
            kinds,
            {CANDIDATE_KIND, CREATE_AS_NEW_KIND, QUARANTINE_KIND},
        )
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertNotIn(b"Authorize effect", customer)

    def test_create_as_new_builds_decision_command(self):
        decision = _multi_person_decision()
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
                    "action": ACTION,
                    "selected__0": "opt-create",
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="submit_decision")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["decision_type"], MULTI_PERSON_DECISION_TYPE)
        self.assertEqual(validated["response"]["action"], ACTION)
        self.assertEqual(
            validated["response"]["groups"],
            [{"group_id": "group-1", "selected_option_id": "opt-create"}],
        )
        self.assertEqual(mutation.mutation_kind, "submit_decision")
        self.assertNotEqual(mutation.mutation_kind, "authorize_effect")


class Phase4EDjangoApiBoundaryTests(TransactionTestCase):
    """Real API process: multi-Person create-as-new / quarantine resume.

    Does **not** mock EasyImportsApiClient.dispatch.
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
            raise RuntimeError("Phase 4E API process did not start.")

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
            choice = _HEADER_TO_CHOICE.get(header, "system:ignore")
            patched = requests.post(
                f"{self.base_url}/v1/column-mapping-plans/{plan_id}/rows/{index}",
                json={"mapping_choice_id": choice},
                headers=self._owner_headers(session, key=f"{key_prefix}-row-{index}"),
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

    def _start_at_multi_person(self, *, case_id: str):
        raw_csv = (
            b"Email,Contact Domain,First Name,Last Name,Company\n"
            b"shared@example.com,example.com,Uploaded,Person,Acme Unique Co\n"
        )
        contacts_csv = (
            b"Contact ID,Email,Contact Domain,First Name,Last Name,Account Name\n"
            b"C1,shared@example.com,example.com,First,Candidate,Acme Unique Co\n"
            b"C2,shared@example.com,example.com,Second,Candidate,Acme Unique Co\n"
        )
        with self.settings(
            EASYIMPORTS_API_BASE_URL=self.base_url,
            SESSIONS_ROOT=Path(tempfile.mkdtemp()),
        ):
            session = ImportSession.objects.create(
                owner_id=self.owner,
                product_key="easyimports.list_import",
                target_provider_id="fake-preview-v1",
                operator_label="Phase4E Boundary Operator",
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
            headers = list(raw_reg.response.get("columns") or [])
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
                        "validate_list_rows": False,
                        "list_duplicate_policy": "surface",
                        "crm_match_duplicate_policy": "surface",
                        "crm_account_multi_match_policy": "use_recommended",
                    },
                    "column_mapping": bind,
                }
            )
            creation = create_or_reuse_mutation(
                session=session,
                form_instance=uuid4(),
                mutation_kind="create_workflow",
                route="/v1/workflows",
                logical_action_identity=(
                    f"session:{session.id}:create_workflow:{case_id}"
                ),
                form_payload_digest=canonical_digest(body),
                request_json=body,
            )
            created = api.dispatch(creation)
            self.assertEqual(created.mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(created.response.get("outcome"), "accepted")
            workflow = materialize_accepted_workflow(session, created, client=api)
            self.assertIsNotNone(workflow)
            self.assertEqual(workflow.status, "needs_decision", workflow.projection)
            decision = workflow.projection["decision"]
            self.assertEqual(decision["decision_type"], MULTI_PERSON_DECISION_TYPE)
            self.assertEqual(decision["phase_id"], "review_crm_people_matches")
            self.assertEqual(workflow.projection.get("effect_grants") or [], [])
            return session, workflow, api

    def _poll_pending_mutations(
        self, session: ImportSession, *, timeout: float = 60.0
    ) -> None:
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
                try:
                    EasyImportsApiClient().dispatch(mutation, explicit_retry=True)
                except Exception:
                    time.sleep(0.1)
            time.sleep(0.05)
        self.fail("Timed out waiting for pending mutations")

    def _submit_kind(
        self,
        session: ImportSession,
        workflow,
        *,
        expected_kind: str,
    ):
        run_id = workflow.run_id
        revision_before = workflow.revision
        page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        groups = page.context["decision_groups"]
        self.assertEqual(len(groups), 1)
        option = next(
            row for row in groups[0]["rows"] if row["option_kind"] == expected_kind
        )
        token = page.context["tokens"]["decision"]
        submitted = self.client.post(
            reverse("importer:submit_decision", args=[session.id]),
            {
                "form_token": token,
                "action": ACTION,
                groups[0]["input_name"]: option["option_id"],
            },
        )
        self.assertEqual(submitted.status_code, 302, submitted.content[:500])
        self._poll_pending_mutations(session)
        mutation = session.api_mutations.get(mutation_kind="submit_decision")
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.response_json.get("outcome"), "accepted")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["decision_type"], MULTI_PERSON_DECISION_TYPE)
        self.assertEqual(validated["response"]["action"], ACTION)
        self.assertEqual(validated["expected_revision"], revision_before)
        self.assertEqual(
            validated["response"]["groups"][0]["selected_option_id"],
            option["option_id"],
        )
        self.assertEqual(mutation.response_json.get("run_id"), run_id)
        return option, revision_before, run_id

    def test_django_create_as_new_accepted_by_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_at_multi_person(case_id="create-new")
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertContains(page, CREATE_AS_NEW_LABEL)
            self.assertContains(page, QUARANTINE_LABEL)
            option, revision_before, run_id = self._submit_kind(
                session, workflow, expected_kind=CREATE_AS_NEW_KIND
            )
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            if refreshed.status == "needs_decision":
                self.assertNotEqual(
                    (refreshed.projection.get("decision") or {}).get("decision_type"),
                    MULTI_PERSON_DECISION_TYPE,
                )
            self.assertEqual(refreshed.projection.get("effect_grants") or [], [])
            self.assertIsNone(refreshed.projection.get("effect_intent"))
            self.assertEqual(option["option_kind"], CREATE_AS_NEW_KIND)

    def test_django_quarantine_accepted_by_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_at_multi_person(case_id="quarantine")
            option, revision_before, run_id = self._submit_kind(
                session, workflow, expected_kind=QUARANTINE_KIND
            )
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            if refreshed.status == "needs_decision":
                self.assertNotEqual(
                    (refreshed.projection.get("decision") or {}).get("decision_type"),
                    MULTI_PERSON_DECISION_TYPE,
                )
            self.assertEqual(refreshed.projection.get("effect_grants") or [], [])
            self.assertIsNone(refreshed.projection.get("effect_intent"))
            self.assertEqual(option["option_kind"], QUARANTINE_KIND)
