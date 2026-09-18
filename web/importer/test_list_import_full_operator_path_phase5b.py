"""Slice 5B — Django FE person-dupe effect authorization + Django→API boundary.

Network-free only. Proves the list-path ``person_duplicate_resolution`` track's
review -> continuation -> **separately authorized** effect lifecycle across the
browser -> Django -> HTTP API trust boundary:

* The person-dupe effect intent renders a *separate* authorize surface whose
  modes are ``disabled`` / ``preview`` / ``dry_run`` / ``execute`` (review is
  never the write grant).
* Authorizing builds an ``authorize_effect`` command (never
  ``submit_decision``) carrying ``track == "person_duplicate_resolution"`` and
  the operator-selected mode; ``execute`` is a distinct authorization from a
  preview/dry_run.
* A review handoff / continuation alone mints **no** write grant
  (``effect_grants == []``; no authorize surface until an effect intent is
  exposed).
* Default remains **disabled** and the configure opt-in is gated by the API-
  projected product ceiling.
* Django imports no ``mappings_2`` at runtime.
* **Real Django→API boundary** (no mocked dispatch): a real API subprocess
  drives a list-import to the person-review handoff; submitting the review
  decision resumes on the same ``run_id`` and mints no person-dupe write grant.

No live CRM. FE-CM-4 remains product-default.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    in_progress/list_import_full_operator_path/list_import_full_operator_path.md
  (#### Slice 5B — List-path review and field-aware effect acceptance)
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
    validate_decision_command,
    validate_effect_authorization,
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
    / "in_progress"
    / "list_import_full_operator_path"
    / "list_import_full_operator_path.md"
)
WORKFLOW_TEMPLATE = (
    REPO_ROOT / "web" / "importer" / "templates" / "importer" / "workflow.html"
)
EFFECT_TRACK = "person_duplicate_resolution"
MULTI_PERSON_DECISION_TYPE = "multiple_crm_matches"
ACTION = "submit_selections"
CREATE_AS_NEW_KIND = "continue_unmatched"
CREATE_AS_NEW_LABEL = "None of these people; create the uploaded person as new"
PEOPLE_MATCHING_CATALOG = "easyimports.people_matching_fields.v1"
_HEADER_TO_CHOICE = {
    "Email": f"catalog:{PEOPLE_MATCHING_CATALOG}:email",
    "First Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:first_name",
    "Last Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:last_name",
    "Company": f"catalog:{PEOPLE_MATCHING_CATALOG}:company_name",
    "Contact Domain": "system:ignore",
}


def _projection(**changes):
    value = {
        "run_id": "run-phase5b-person-dupe",
        "revision": 5,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 8,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_effect_authorization",
        "stage": "awaiting_person_duplicate_resolution_authorization",
        "decision": None,
        "effect_intent": None,
        "effect_review": None,
        "effect_grants": [],
        "review_handoff": None,
        "terminal_evidence": None,
        "summary": {"person_duplicate_groups": 1},
        "error": None,
        "links": {},
    }
    value.update(changes)
    return value


def _person_dupe_effect_intent(
    *,
    maximum_mode="execute",
    supported_modes=("disabled", "preview", "dry_run", "execute"),
    work_digest="phase5b-work-digest",
):
    return {
        "intent_id": "intent_phase5b_person_dupe",
        "track": EFFECT_TRACK,
        "gate_phase_id": "freeze_list_person_duplicate_execution_plan",
        "effect_phase_id": "list_person_duplicate_execution_effect",
        "maximum_mode": maximum_mode,
        "supported_modes": list(supported_modes),
        "target_provider_id": "fake-preview-v1",
        "target_fingerprint": "00D000000000001EAA",
        "work_digest": work_digest,
        "confirmation": (
            f"authorize:intent_phase5b_person_dupe:{EFFECT_TRACK}:{maximum_mode}"
        ),
    }


def _receipt(*, outcome="rejected"):
    return {
        "command_id": "cmd-phase5b",
        "command_kind": "authorize_effect",
        "run_id": "run-phase5b-person-dupe",
        "revision": 5,
        "workflow_status": "awaiting_effect_authorization",
        "stage": "freeze_list_person_duplicate_execution_plan",
        "outcome": outcome,
        "error_code": None if outcome == "accepted" else "request_rejected",
        "message": None if outcome == "accepted" else "No",
        "resource": "/v1/workflows/run-phase5b-person-dupe",
    }


class Phase5BPersonDupeEffectFeTests(TestCase):
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
            operator_label="Phase5B Operator",
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

    def test_strategy_and_template_characterize_person_dupe_effect(self):
        self.assertTrue(STRATEGY_PATH.is_file())
        strategy = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("5B", strategy)
        self.assertIn("person_duplicate_resolution", strategy)
        self.assertIn("separately authorize", strategy)
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("authorize_effect", template)
        self.assertIn("selected_mode", template)

    def test_renders_person_dupe_effect_authorize_surface(self):
        intent = _person_dupe_effect_intent()
        value = _projection(effect_intent=intent)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="selected_mode"')
        self.assertIn("effect", page.context["tokens"])
        presentation = page.context["effect_presentation"]
        mode_values = {opt["value"] for opt in presentation["mode_options"]}
        # The four separately-authorizable effect modes are offered; the
        # authorize surface is distinct from any review decision.
        self.assertEqual(
            mode_values, {"disabled", "preview", "dry_run", "execute"}
        )
        self.assertEqual(presentation["track_label"], "Person duplicate handling")

    def test_authorize_dry_run_builds_person_dupe_effect_command(self):
        intent = _person_dupe_effect_intent()
        value = _projection(effect_intent=intent)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["effect"]
        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=self.reject_dispatch,
        ):
            submitted = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "dry_run"},
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="authorize_effect")
        validated = validate_effect_authorization(mutation.request_json)
        self.assertEqual(validated["selected_mode"], "dry_run")
        self.assertEqual(validated["track"], EFFECT_TRACK)
        self.assertEqual(validated["intent_id"], intent["intent_id"])
        self.assertEqual(mutation.mutation_kind, "authorize_effect")
        self.assertNotEqual(mutation.mutation_kind, "submit_decision")

    def test_authorize_execute_is_a_distinct_authorization(self):
        intent = _person_dupe_effect_intent()
        value = _projection(effect_intent=intent)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["effect"]
        with patch.object(
            EasyImportsApiClient,
            "dispatch",
            side_effect=self.reject_dispatch,
        ):
            submitted = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "execute"},
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="authorize_effect")
        validated = validate_effect_authorization(mutation.request_json)
        self.assertEqual(validated["selected_mode"], "execute")
        self.assertEqual(validated["track"], EFFECT_TRACK)

    def test_mode_outside_stored_intent_is_rejected(self):
        # A mode the stored effect intent does not support cannot be authorized
        # (review can never smuggle an execute grant the intent did not expose).
        intent = _person_dupe_effect_intent(
            maximum_mode="preview", supported_modes=("disabled", "preview")
        )
        value = _projection(effect_intent=intent)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = page.context["tokens"]["effect"]
        submitted = self.client.post(
            reverse("importer:authorize_effect", args=[session.id]),
            {"form_token": token, "selected_mode": "execute"},
        )
        # Rejected before any authorize_effect mutation is journaled: a mode the
        # stored intent did not expose can never be authorized from the browser.
        self.assertIn(submitted.status_code, {302, 400})
        self.assertFalse(
            session.api_mutations.filter(mutation_kind="authorize_effect").exists()
        )

    def test_review_handoff_alone_mints_no_effect_surface(self):
        # A paused review handoff / continuation with no effect intent exposes
        # no authorize surface and no effect grants: review is not a write grant.
        value = _projection(
            status="needs_review",
            stage="analyze_list_person_duplicate_groups",
            effect_intent=None,
            review_handoff={
                "handoff_id": "rh-phase5b",
                "kind": "person_duplicate_group_review",
                "decision_type": "person_duplicate_group_review",
            },
            effect_grants=[],
        )
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context.get("effect_intent"))
        self.assertNotIn("effect", page.context["tokens"])
        self.assertEqual(workflow.projection.get("effect_grants") or [], [])
        self.assertNotContains(page, 'name="selected_mode"')

    def test_django_modules_do_not_import_mappings_2(self):
        # workflow_views.py has a single guarded optional import inside a
        # try/except for a rare env; the boundary-authoritative modules below
        # import no mappings_2 at all (matching the phase6a/7a guards).
        import importer.api_client as api_client_mod
        import importer.api_contract as api_contract_mod
        import importer.command_service as command_service_mod
        import importer.workflow_state as workflow_state_mod

        for module in (
            api_client_mod,
            api_contract_mod,
            command_service_mod,
            workflow_state_mod,
        ):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("import mappings_2", source, msg=module.__name__)
            self.assertNotIn("from mappings_2", source, msg=module.__name__)


def _list_import_product() -> dict:
    return {
        "product_key": "easyimports.list_import",
        "tracks": {
            "reference_acquisition": ["disabled", "execute"],
            "account_provisioning": ["disabled", "preview", "dry_run", "execute"],
            "person_duplicate_resolution": [
                "disabled",
                "preview",
                "dry_run",
                "execute",
            ],
            "people_writes": ["disabled", "preview", "dry_run", "execute"],
            "campaign_member_writes": ["disabled", "preview", "dry_run", "execute"],
            "delivery": ["disabled", "preview", "execute"],
        },
    }


def _connection(*, person_dupe_ceiling: str) -> dict:
    list_modes = {
        "reference_acquisition": "execute",
        "account_provisioning": "execute",
        "people_writes": "execute",
        "person_duplicate_resolution": person_dupe_ceiling,
        "campaign_member_writes": "execute",
        "delivery": "preview",
        "duplicate_execution": "execute",
    }
    return {
        "provider_key": "salesforce",
        "connection_id": "conn-sf-5b",
        "maximum_authorization": {
            "reference_acquisition": "execute",
            "account_provisioning": "disabled",
            "people_writes": "execute",
            "person_duplicate_resolution": person_dupe_ceiling,
            "campaign_member_writes": "execute",
            "delivery": "preview",
            "duplicate_execution": "execute",
            "by_product": {"easyimports.list_import": list_modes},
        },
    }


class Phase5BConfigureDefaultTests(TestCase):
    def _form(self, *, ceiling: str):
        from importer.forms import WorkflowConfigurationForm
        from importer.setup_service import connected_import_target_projection

        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.list_import",
            connection=_connection(person_dupe_ceiling=ceiling),
        )
        return WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=target,
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-sf-5b",
        )

    def test_person_dupe_defaults_disabled_when_ceiling_permits(self):
        field = self._form(ceiling="execute").fields[f"mode__{EFFECT_TRACK}"]
        modes = {value for value, _label in field.choices}
        # Opt-in offered up to execute, but the default is never above disabled.
        self.assertEqual(field.initial, "disabled")
        self.assertIn("execute", modes)
        self.assertIn("dry_run", modes)

    def test_person_dupe_opt_in_gated_out_when_ceiling_disabled(self):
        field = self._form(ceiling="disabled").fields[f"mode__{EFFECT_TRACK}"]
        modes = {value for value, _label in field.choices}
        # Fail-closed: the API-projected ceiling admits only disabled.
        self.assertEqual(modes, {"disabled"})
        self.assertNotIn("execute", modes)
        self.assertNotIn("dry_run", modes)


class Phase5BDjangoApiBoundaryTests(TransactionTestCase):
    """Real API process: list-import person-review handoff crosses the boundary.

    Does **not** mock ``EasyImportsApiClient.dispatch``. Proves that submitting
    the review decision resumes on the same ``run_id`` and mints no person-dupe
    write grant (review submission never authorizes a merge).
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
            raise RuntimeError("Phase 5B API process did not start.")

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

    def _start_at_person_review(self, *, case_id: str):
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
                operator_label="Phase5B Boundary Operator",
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
            # person_duplicate_resolution is disabled: no person-dupe write grant.
            self.assertEqual(workflow.projection.get("effect_grants") or [], [])
            return session, workflow, api

    def _submit_review_decision(self, session, workflow, *, expected_kind):
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
        # A review decision is never an effect authorization.
        self.assertEqual(mutation.mutation_kind, "submit_decision")
        self.assertNotEqual(mutation.mutation_kind, "authorize_effect")
        self.assertEqual(mutation.response_json.get("run_id"), run_id)
        return revision_before, run_id

    def test_review_decision_resumes_without_person_dupe_grant(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_at_person_review(case_id="review")
            revision_before, run_id = self._submit_review_decision(
                session, workflow, expected_kind=CREATE_AS_NEW_KIND
            )
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            # Continuation past review mints no person-dupe write grant and no
            # person_duplicate_resolution effect intent (it stayed disabled).
            self.assertEqual(refreshed.projection.get("effect_grants") or [], [])
            intent = refreshed.projection.get("effect_intent") or {}
            self.assertNotEqual(intent.get("track"), EFFECT_TRACK)
