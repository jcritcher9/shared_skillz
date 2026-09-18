"""Slice 9 — Django<->API dual-process full-path boundary acceptance.

Network-free only. This is the dual-process half of the Slice 9 acceptance; the
in-process execute chain (Account create + hydrate + dependent Contact create ->
person-dupe execute -> 5A redirect -> people_writes -> CampaignMember ->
run_output_package.v1) is proven beside this file in
``tests/mappings_2/test_list_import_full_operator_path_phase9.py``.

Here the whole box-aligned path is proven to travel the **real Django -> HTTP
API process boundary** (a real ``mappings_2.api.app`` subprocess, no mocked
dispatch, as in the accepted Slice 5B subprocess test):

* **Review -> continuation carries the full path but mints no write grant.** A
  list-import driven across the boundary to the multi-Person review handoff
  resumes on the same ``run_id`` after a review decision, with **no**
  person-dupe (or any other) write grant: review submission is never an effect
  authorization (``submit_decision`` != ``authorize_effect``).
* **Terminal ``run_output_package.v1`` is reachable through the boundary.** A
  clean list-import with every optional write track disabled runs to
  ``succeeded`` across the boundary and yields a downloadable
  ``run_output_package.v1`` (OUT-2/OUT-3 cleaned member) via the Django package
  routes, with filesystem delivery never minted.
* **Every OUT-6B optional-write control is API-ceiling-gated and default
  disabled.** The configure form offers ``account_provisioning``,
  ``person_duplicate_resolution``, ``people_writes`` and
  ``campaign_member_writes`` opt-in up to the API-projected product ceiling but
  never defaults any of them above ``disabled``; a disabled ceiling fails
  closed to ``disabled`` only. Controls stay grouped (no flat-form fallback).
* Django imports no ``mappings_2`` at runtime.

No live CRM. FE-CM-4 remains product-default.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    in_progress/list_import_full_operator_path/list_import_full_operator_path.md
  (### Phase 9 — Network-free dual-process full-path acceptance)
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from importer.api_client import (
    EasyImportsApiClient,
    canonical_digest,
    create_or_reuse_mutation,
)
from importer.api_contract import (
    validate_decision_command,
    validate_workflow_create,
)
from importer.command_service import materialize_accepted_workflow
from importer.models import ApiMutation, ImportSession
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
EFFECT_TRACK = "person_duplicate_resolution"
OPTIONAL_WRITE_TRACKS = (
    "account_provisioning",
    "person_duplicate_resolution",
    "people_writes",
    "campaign_member_writes",
)
MULTI_PERSON_DECISION_TYPE = "multiple_crm_matches"
ACTION = "submit_selections"
CREATE_AS_NEW_KIND = "continue_unmatched"
PEOPLE_MATCHING_CATALOG = "easyimports.people_matching_fields.v1"
_HEADER_TO_CHOICE = {
    "Email": f"catalog:{PEOPLE_MATCHING_CATALOG}:email",
    "First Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:first_name",
    "Last Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:last_name",
    "Company": f"catalog:{PEOPLE_MATCHING_CATALOG}:company_name",
    "Contact Domain": "system:ignore",
}


# --------------------------------------------------------------------------- #
# Configure: every OUT-6B optional-write control is API-ceiling-gated and
# defaults disabled (grouped; no flat-form fallback).
# --------------------------------------------------------------------------- #
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


def _connection(*, ceiling: str) -> dict:
    list_modes = {
        "reference_acquisition": "execute",
        "account_provisioning": ceiling,
        "people_writes": ceiling,
        "person_duplicate_resolution": ceiling,
        "campaign_member_writes": ceiling,
        "delivery": "preview",
        "duplicate_execution": "execute",
    }
    return {
        "provider_key": "salesforce",
        "connection_id": "conn-sf-9",
        "maximum_authorization": {
            "reference_acquisition": "execute",
            "account_provisioning": "disabled",
            "people_writes": "execute",
            "person_duplicate_resolution": ceiling,
            "campaign_member_writes": "execute",
            "delivery": "preview",
            "duplicate_execution": "execute",
            "by_product": {"easyimports.list_import": list_modes},
        },
    }


class Phase9OptionalWriteConfigureTests(TestCase):
    """All OUT-6B optional-write tracks: default disabled, ceiling-gated."""

    def _form(self, *, ceiling: str):
        from importer.forms import WorkflowConfigurationForm
        from importer.setup_service import connected_import_target_projection

        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.list_import",
            connection=_connection(ceiling=ceiling),
        )
        return WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=target,
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-sf-9",
        )

    def test_strategy_names_dual_process_full_path(self):
        self.assertTrue(STRATEGY_PATH.is_file())
        text = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("Network-free dual-process full-path acceptance", text)
        self.assertIn("run_output_package.v1", text)

    def test_all_optional_write_tracks_default_disabled_when_ceiling_permits(self):
        form = self._form(ceiling="execute")
        for track in OPTIONAL_WRITE_TRACKS:
            field = form.fields[f"mode__{track}"]
            modes = {value for value, _label in field.choices}
            # Opt-in offered up to execute, but the default is never above disabled.
            self.assertEqual(field.initial, "disabled", track)
            self.assertIn("execute", modes, track)
            self.assertIn("dry_run", modes, track)

    def test_all_optional_write_tracks_fail_closed_when_ceiling_disabled(self):
        form = self._form(ceiling="disabled")
        for track in OPTIONAL_WRITE_TRACKS:
            field = form.fields[f"mode__{track}"]
            modes = {value for value, _label in field.choices}
            # Fail closed: the API-projected ceiling admits only disabled.
            self.assertEqual(modes, {"disabled"}, track)
            self.assertNotIn("execute", modes, track)
            self.assertNotIn("dry_run", modes, track)

    def test_django_modules_do_not_import_mappings_2(self):
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


# --------------------------------------------------------------------------- #
# Real Django -> API subprocess boundary: full-path review/continuation carries
# no write grant, and a clean run reaches a terminal package across the boundary.
# --------------------------------------------------------------------------- #
class Phase9DjangoApiBoundaryTests(TransactionTestCase):
    """Real API process (no mocked dispatch), driven from Django over HTTP."""

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
            raise RuntimeError("Phase 9 API process did not start.")

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

    # ---- shared helpers (mirror the accepted Slice 5B boundary test) ---- #
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
                    state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN]
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

    def _create_list_import(
        self,
        *,
        case_id: str,
        raw_csv: bytes,
        contacts_csv: bytes,
    ):
        with self.settings(
            EASYIMPORTS_API_BASE_URL=self.base_url,
            SESSIONS_ROOT=Path(tempfile.mkdtemp()),
        ):
            session = ImportSession.objects.create(
                owner_id=self.owner,
                product_key="easyimports.list_import",
                target_provider_id="fake-preview-v1",
                operator_label="Phase9 Boundary Operator",
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
            # No optional-write grant is minted at create: every write track is
            # disabled across the boundary.
            self.assertEqual(workflow.projection.get("effect_grants") or [], [])
            return session, workflow, api

    # ---- 1. Full-path review -> continuation mints no write grant ---- #
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
        session, workflow, api = self._create_list_import(
            case_id=case_id, raw_csv=raw_csv, contacts_csv=contacts_csv
        )
        self.assertEqual(workflow.status, "needs_decision", workflow.projection)
        return session, workflow, api

    def test_review_continuation_carries_no_write_grant(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_at_person_review(case_id="review")
            run_id = workflow.run_id
            revision_before = workflow.revision
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertEqual(page.status_code, 200)
            groups = page.context["decision_groups"]
            self.assertEqual(len(groups), 1)
            option = next(
                row
                for row in groups[0]["rows"]
                if row["option_kind"] == CREATE_AS_NEW_KIND
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
            validated = validate_decision_command(mutation.request_json)
            self.assertEqual(validated["decision_type"], MULTI_PERSON_DECISION_TYPE)
            # A review decision is never an effect authorization.
            self.assertEqual(mutation.mutation_kind, "submit_decision")
            self.assertNotEqual(mutation.mutation_kind, "authorize_effect")
            self.assertEqual(mutation.response_json.get("run_id"), run_id)

            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            # Continuation past review mints no write grant and no optional-write
            # effect intent (every write track stayed disabled).
            self.assertEqual(refreshed.projection.get("effect_grants") or [], [])
            intent = refreshed.projection.get("effect_intent") or {}
            self.assertNotIn(intent.get("track"), OPTIONAL_WRITE_TRACKS)

    # ---- 2. Terminal package reachable across the boundary ---- #
    def test_clean_run_reaches_terminal_package_through_boundary(self):
        raw_csv = (
            b"Email,Contact Domain,First Name,Last Name,Company\n"
            b"unique-person@example.com,example.com,Solo,Person,Nomatch Co\n"
        )
        # A contact that does NOT share the raw person's email: no CRM match, no
        # multi-Person review -> the disabled-track run advances to terminal.
        contacts_csv = (
            b"Contact ID,Email,Contact Domain,First Name,Last Name,Account Name\n"
            b"C9,other@elsewhere.example,elsewhere.example,Someone,Else,Other Co\n"
        )
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._create_list_import(
                case_id="terminal", raw_csv=raw_csv, contacts_csv=contacts_csv
            )
            workflow = self._drive_to_succeeded(session, workflow, api)
            self.assertEqual(workflow.status, "succeeded", workflow.projection)

            # Package create + download across the Django -> API boundary.
            token_page = self.client.get(
                reverse("importer:workflow", args=[session.id])
            )
            self.assertEqual(token_page.status_code, 200)
            token = token_page.context["tokens"].get("run_output_package")
            self.assertIsNotNone(token, "no run_output_package form token surfaced")
            created = self.client.post(
                reverse("importer:create_run_output_package", args=[session.id]),
                {"form_token": token},
            )
            self.assertEqual(created.status_code, 302, created.content[:500])
            self._poll_pending_mutations(session)
            mutation = session.api_mutations.get(
                mutation_kind="create_run_output_package"
            )
            self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
            package_id = str(mutation.response_json.get("package_id") or "")
            self.assertTrue(package_id, mutation.response_json)

            content = self.client.get(
                reverse(
                    "importer:run_output_package_content",
                    args=[session.id, package_id],
                )
            )
            self.assertEqual(content.status_code, 200)
            payload = (
                b"".join(content.streaming_content)
                if getattr(content, "streaming_content", None)
                else content.content
            )
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
                # The always-on OUT-2/OUT-3 terminal package is present with a
                # cleaned member and an empty (never minted) delivery manifest.
                self.assertIn("MANIFEST.json", names)
                self.assertTrue(
                    any(name.startswith("import/") for name in names), names
                )
                import json as _json

                delivery = _json.loads(
                    archive.read("metadata/delivery_manifest.json").decode("utf-8")
                )
                self.assertEqual(delivery, {})

    def _drive_to_succeeded(self, session, workflow, api, *, timeout: float = 90.0):
        deadline = time.monotonic() + timeout
        current = workflow
        while time.monotonic() < deadline:
            current = refresh_workflow(session, current, client=api)
            status = current.status
            if status == "succeeded":
                return current
            if status in {"failed", "error"}:
                self.fail(f"run failed: {current.projection}")
            if status == "needs_decision":
                page = self.client.get(
                    reverse("importer:workflow", args=[session.id])
                )
                groups = page.context.get("decision_groups") or []
                if not groups:
                    time.sleep(0.2)
                    continue
                token = page.context["tokens"]["decision"]
                data = {"form_token": token, "action": ACTION}
                for group in groups:
                    rows = group["rows"]
                    pick = next(
                        (r for r in rows if r["option_kind"] == CREATE_AS_NEW_KIND),
                        rows[0],
                    )
                    data[group["input_name"]] = pick["option_id"]
                self.client.post(
                    reverse("importer:submit_decision", args=[session.id]), data
                )
                self._poll_pending_mutations(session)
                continue
            time.sleep(0.2)
        self.fail(f"run did not reach terminal in time: status={current.status}")
