"""FE-CM-4 — Network-free browser → Django → API CampaignMember E2E.

Residual operator-path cells from the FE-CM-4 obligation matrix. Dual-process
only: a real ``mappings_2.api.app`` subprocess, no mocked dispatch, fake stack,
``campaign_member_writes`` opted in. Does not re-implement landed 7B/9/FE-CM-1/2/3
assertions.

Missing cells:
* Unmocked Django → API CM ``dry_run``; zero mutation
* Authorize → execute ladder through Django ``authorize_effect`` / journal
  after verified Person IDs (matched existing fake Contact)
* Terminal Django ``run_output_package.v1`` after CM execute
* Exact replay of CM execute authorization; no double mutation
* Stale CM plan revision fails closed on Django → API execute
* Disconnect after CM freeze fails closed on execute
* Operator cannot freeze ``too_many_matches`` / truncated name search via
  unmocked Django → API

No live CRM. Django does not import ``mappings_2``.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    in_progress/crm_query_export_schema_and_campaign/
    crm_query_export_schema_and_campaign.md
  (#### Phase FE-CM-4 — Network-free browser → Django → API CM E2E)
"""

from __future__ import annotations

import io
import json
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
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from django.urls import reverse

from importer.api_client import (
    EasyImportsApiClient,
    canonical_digest,
    create_or_reuse_mutation,
)
from importer.api_contract import validate_workflow_create
from importer.campaign_member_setup import load_session_resolutions
from importer.command_service import materialize_accepted_workflow
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.upload_service import save_and_register_upload
from importer.workflow_state import (
    OWNER_SESSION_KEY,
    issue_form_token,
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
    / "crm_query_export_schema_and_campaign"
    / "crm_query_export_schema_and_campaign.md"
)
SEED_HELPER = REPO_ROOT / "web" / "tools" / "seed_fake_crm_org.py"

CAMPAIGN_ID = "701FAKE00000003AAA"
CAMPAIGN_NAME = "Unique Webinar"
FLOOD_NAME = "Flooded Name"
ACCOUNT_NAME = "Nomatch Co"
CONTACT_CASES = {
    "dry": ("cm4-dry@example.test", "Dry"),
    "exec": ("cm4-exec@example.test", "Exec"),
    "replay": ("cm4-replay@example.test", "Replay"),
    "stale": ("cm4-stale@example.test", "Stale"),
    "disc": ("cm4-disc@example.test", "Disc"),
    "pkg": ("cm4-pkg@example.test", "Pkg"),
}
PEOPLE_MATCHING_CATALOG = "easyimports.people_matching_fields.v1"
_HEADER_TO_CHOICE = {
    "Email": f"catalog:{PEOPLE_MATCHING_CATALOG}:email",
    "First Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:first_name",
    "Last Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:last_name",
    "Company": f"catalog:{PEOPLE_MATCHING_CATALOG}:company_name",
    "Contact Domain": "system:ignore",
}
CREATE_AS_NEW_KIND = "continue_unmatched"
ACTION = "submit_selections"
CM_TRACK = "campaign_member_writes"
REF_TRACK = "reference_acquisition"


def _raw_csv(email: str, first_name: str) -> bytes:
    return (
        "Email,Contact Domain,First Name,Last Name,Company\n"
        f"{email},example.com,{first_name},Person,{ACCOUNT_NAME}\n"
    ).encode("utf-8")


def _cm_policy() -> dict:
    return {
        "policy_version": "campaign_member_policy.v2",
        "default_campaign_binding": CAMPAIGN_ID,
        "default_desired_status": "Responded",
        "campaign_match_resolutions": [],
    }


class FeCm4DjangoApiBoundaryTests(TransactionTestCase):
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
        cls.org_path = Path(cls.state_dir.name) / "fake_crm" / "org.json"
        cls._seed_fixture()
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = cls.state_dir.name
        environment["PYTHONPATH"] = str(REPO_ROOT)
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(REPO_ROOT),
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
            raise RuntimeError("FE-CM-4 API process did not start.")

    @classmethod
    def _seed_fixture(cls) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPO_ROOT)
        completed = subprocess.run(
            [
                sys.executable,
                str(SEED_HELPER),
                "--state-root",
                cls.state_dir.name,
                "--fe-cm4",
                "--clear-injections",
            ],
            cwd=str(REPO_ROOT),
            env=environment,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "seed_fake_crm_org --fe-cm4 failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )

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
        browser_session[OWNER_SESSION_KEY] = str(self.owner)
        browser_session.save()

    def _owner_headers(self, session: ImportSession, *, key: str) -> dict[str, str]:
        return {
            "X-Owner-Session": owner_session_for_import_session(session),
            "Idempotency-Key": key,
        }

    def _org_state(self) -> dict:
        return json.loads(self.org_path.read_text(encoding="utf-8"))

    def _mutation_count(self) -> int:
        return int(self._org_state().get("mutation_count") or 0)

    def _campaign_member_ids(self) -> set[str]:
        records = self._org_state().get("records") or {}
        return {
            rid
            for rid, rec in records.items()
            if isinstance(rec, dict) and rec.get("object_type") == "CampaignMember"
        }

    def _connect_fake(self, session: ImportSession, *, key: str) -> dict:
        owner = owner_session_for_import_session(session)
        start = requests.post(
            f"{self.base_url}/v1/crm/connections",
            json={"provider_key": "fake"},
            headers={"X-Owner-Session": owner, "Idempotency-Key": f"{key}-start"},
            timeout=15,
        )
        self.assertEqual(start.status_code, 201, start.text)
        body = start.json()
        connection_id = body["connection_id"]
        complete = requests.post(
            f"{self.base_url}/v1/crm/connections/{connection_id}/oauth/complete",
            json={
                "authorization_code": "code",
                "state": body["authorization"]["state"],
            },
            headers={"X-Owner-Session": owner, "Idempotency-Key": f"{key}-complete"},
            timeout=15,
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        resource = complete.json()
        target = str(
            resource.get("execution_target_provider_id") or "fake-crm-phase0b-v1"
        )
        return {
            "connection_id": connection_id,
            "execution_target_provider_id": target,
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

    def _create_connected_cm_import(
        self,
        *,
        case_id: str,
        cm_mode: str,
    ):
        email, first_name = CONTACT_CASES[case_id]
        with self.settings(
            EASYIMPORTS_API_BASE_URL=self.base_url,
            SESSIONS_ROOT=Path(tempfile.mkdtemp()),
        ):
            session = ImportSession.objects.create(
                owner_id=self.owner,
                product_key="easyimports.list_import",
                target_provider_id="fake-crm-phase0b-v1",
                operator_label="FE-CM-4 Boundary Operator",
                setup_entity="people",
                setup_operation="crm_matching",
                setup_reference_source="connected_crm",
            )
            connected = self._connect_fake(session, key=f"conn-{case_id}")
            session.setup_connection_id = connected["connection_id"]
            session.target_provider_id = connected["execution_target_provider_id"]
            session.save(
                update_fields=[
                    "setup_connection_id",
                    "target_provider_id",
                    "updated_at",
                ]
            )
            api = EasyImportsApiClient()
            raw_reg = save_and_register_upload(
                session=session,
                role="raw_list",
                uploaded=SimpleUploadedFile(
                    "people.csv",
                    _raw_csv(email, first_name),
                    content_type="text/csv",
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
                    "target_provider_id": session.target_provider_id,
                    "connection_id": connected["connection_id"],
                    "uploads": {"raw_list": raw_reg.response["upload_id"]},
                    "maximum_modes": {
                        "reference_acquisition": "execute",
                        "account_provisioning": "disabled",
                        "person_duplicate_resolution": "disabled",
                        "people_writes": "disabled",
                        "campaign_member_writes": cm_mode,
                        "delivery": "disabled",
                    },
                    "options": {
                        "interaction_mode": "interactive",
                        "validate_list_rows": False,
                        "list_duplicate_policy": "surface",
                        "crm_match_duplicate_policy": "drop_repeats",
                        "crm_account_multi_match_policy": "use_recommended",
                    },
                    "column_mapping": bind,
                    "campaign_member_policy": _cm_policy(),
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
            create_body = created.mutation.request_json
            self.assertEqual(
                create_body["campaign_member_policy"]["policy_version"],
                "campaign_member_policy.v2",
            )
            self.assertEqual(
                create_body["maximum_modes"][CM_TRACK],
                cm_mode,
            )
            return session, workflow, api, connected

    def _authorize_effect(self, session: ImportSession, *, selected_mode: str):
        page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200, page.content[:500])
        token = page.context["tokens"].get("effect")
        self.assertIsNotNone(token, "no effect form token surfaced")
        intent = (page.context.get("projection") or {}).get("effect_intent") or {}
        self.assertTrue(intent, "workflow page has no effect_intent")
        posted = self.client.post(
            reverse("importer:authorize_effect", args=[session.id]),
            {"form_token": token, "selected_mode": selected_mode},
        )
        self.assertEqual(posted.status_code, 302, posted.content[:500])
        self._poll_pending_mutations(session)
        return token, intent, posted

    def _submit_any_decision(self, session: ImportSession) -> None:
        page = self.client.get(reverse("importer:workflow", args=[session.id]))
        groups = page.context.get("decision_groups") or []
        if not groups:
            return
        token = page.context["tokens"]["decision"]
        data = {"form_token": token, "action": ACTION}
        for group in groups:
            rows = group["rows"]
            pick = next(
                (row for row in rows if row.get("option_kind") == CREATE_AS_NEW_KIND),
                rows[0],
            )
            data[group["input_name"]] = pick["option_id"]
        self.client.post(
            reverse("importer:submit_decision", args=[session.id]), data
        )
        self._poll_pending_mutations(session)

    def _resume_effect(self, session: ImportSession) -> None:
        page = self.client.get(reverse("importer:workflow", args=[session.id]))
        token = (page.context.get("tokens") or {}).get("resume")
        if not token:
            return
        self.client.post(
            reverse("importer:resume_effect", args=[session.id]),
            {"form_token": token},
        )
        self._poll_pending_mutations(session)

    def _drive(
        self,
        session: ImportSession,
        workflow,
        api,
        *,
        stop_at_cm: bool = False,
        cm_mode: str = "execute",
        timeout: float = 120.0,
    ):
        deadline = time.monotonic() + timeout
        current = workflow
        while time.monotonic() < deadline:
            current = refresh_workflow(session, current, client=api)
            status = current.status
            intent = current.projection.get("effect_intent") or {}
            track = str(intent.get("track") or "")
            if stop_at_cm and status == "awaiting_effect_authorization" and track == CM_TRACK:
                return current
            if status == "succeeded":
                return current
            if status in {"failed", "error"}:
                self.fail(f"run failed: {current.projection}")
            if status == "awaiting_effect_authorization":
                if track == REF_TRACK:
                    self._authorize_effect(session, selected_mode="execute")
                    continue
                if track == CM_TRACK:
                    self._authorize_effect(session, selected_mode=cm_mode)
                    continue
                self.fail(f"unexpected effect track {track!r}: {intent}")
            if status == "needs_decision":
                self._submit_any_decision(session)
                continue
            if status in {"paused_unknown", "paused_verification"}:
                self._resume_effect(session)
                continue
            time.sleep(0.2)
        self.fail(
            f"run did not reach expected gate: status={current.status} "
            f"intent={current.projection.get('effect_intent')}"
        )

    def test_strategy_names_fe_cm4_operator_path(self):
        self.assertTrue(STRATEGY_PATH.is_file())
        text = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("Phase FE-CM-4", text)
        self.assertIn("browser → Django → API", text)

    def test_cm_dry_run_zero_mutation_through_django_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            before = self._mutation_count()
            members_before = self._campaign_member_ids()
            session, workflow, api, _connected = self._create_connected_cm_import(
                case_id="dry", cm_mode="dry_run"
            )
            terminal = self._drive(
                session, workflow, api, stop_at_cm=False, cm_mode="dry_run"
            )
            self.assertEqual(terminal.status, "succeeded", terminal.projection)
            grants = terminal.projection.get("effect_grants") or []
            self.assertTrue(
                any(
                    grant.get("track") == CM_TRACK
                    and grant.get("selected_mode") == "dry_run"
                    for grant in grants
                ),
                grants,
            )
            mutation = (
                session.api_mutations.filter(
                    mutation_kind="authorize_effect"
                )
                .order_by("created_at")
                .last()
            )
            self.assertIsNotNone(mutation)
            self.assertEqual(mutation.request_json.get("track"), CM_TRACK)
            self.assertEqual(mutation.request_json.get("selected_mode"), "dry_run")
            self.assertEqual(self._mutation_count(), before)
            self.assertEqual(self._campaign_member_ids(), members_before)

    def test_cm_execute_after_verified_person_ids_through_django_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            before = self._mutation_count()
            session, workflow, api, _connected = self._create_connected_cm_import(
                case_id="exec", cm_mode="execute"
            )
            at_gate = self._drive(
                session, workflow, api, stop_at_cm=True, cm_mode="execute"
            )
            intent = at_gate.projection.get("effect_intent") or {}
            self.assertEqual(intent.get("track"), CM_TRACK)
            self.assertIn("execute", intent.get("supported_modes") or [])
            terminal = self._drive(
                session, at_gate, api, stop_at_cm=False, cm_mode="execute"
            )
            self.assertEqual(terminal.status, "succeeded", terminal.projection)
            grants = terminal.projection.get("effect_grants") or []
            self.assertTrue(
                any(
                    grant.get("track") == CM_TRACK
                    and grant.get("selected_mode") == "execute"
                    for grant in grants
                ),
                grants,
            )
            mutation = session.api_mutations.filter(
                mutation_kind="authorize_effect",
            ).order_by("created_at")
            cm_auths = [
                row
                for row in mutation
                if row.request_json.get("track") == CM_TRACK
            ]
            self.assertEqual(len(cm_auths), 1, list(mutation))
            self.assertEqual(cm_auths[0].request_json.get("selected_mode"), "execute")
            receipts = (terminal.projection.get("terminal_evidence") or {}).get(
                "receipts"
            ) or []
            cm_receipt = next(
                (row for row in receipts if row.get("track") == CM_TRACK),
                None,
            )
            self.assertIsNotNone(cm_receipt)
            self.assertEqual(cm_receipt.get("outcome"), "succeeded", cm_receipt)
            self.assertGreater(self._mutation_count(), before)
            self.assertTrue(self._campaign_member_ids())

    def test_terminal_package_after_cm_execute(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api, _connected = self._create_connected_cm_import(
                case_id="pkg", cm_mode="execute"
            )
            terminal = self._drive(
                session, workflow, api, stop_at_cm=False, cm_mode="execute"
            )
            self.assertEqual(terminal.status, "succeeded", terminal.projection)
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
                self.assertIn("MANIFEST.json", names)
                receipts_name = next(
                    (
                        name
                        for name in names
                        if name.endswith("track_receipts.json")
                    ),
                    None,
                )
                self.assertIsNotNone(receipts_name, names)
                receipts = json.loads(archive.read(receipts_name).decode("utf-8"))
                by_track = {
                    row["track"]: row
                    for row in receipts
                    if isinstance(row, dict) and "track" in row
                }
                self.assertIn(CM_TRACK, by_track, by_track)
                self.assertEqual(
                    by_track[CM_TRACK].get("outcome"),
                    "succeeded",
                    by_track[CM_TRACK],
                )

    def test_exact_replay_cm_execute_no_double_mutation(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api, _connected = self._create_connected_cm_import(
                case_id="replay", cm_mode="execute"
            )
            at_gate = self._drive(
                session, workflow, api, stop_at_cm=True, cm_mode="execute"
            )
            token, intent, _posted = self._authorize_effect(
                session, selected_mode="execute"
            )
            self.assertEqual(intent.get("track"), CM_TRACK)
            self._poll_pending_mutations(session)
            deadline = time.monotonic() + 60.0
            current = at_gate
            while time.monotonic() < deadline:
                current = refresh_workflow(session, current, client=api)
                if current.status == "succeeded":
                    break
                time.sleep(0.2)
            self.assertEqual(current.status, "succeeded", current.projection)
            after_first = self._mutation_count()
            members_after_first = self._campaign_member_ids()
            replayed = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "execute"},
            )
            self.assertEqual(replayed.status_code, 302, replayed.content[:500])
            self._poll_pending_mutations(session)
            self.assertEqual(self._mutation_count(), after_first)
            self.assertEqual(self._campaign_member_ids(), members_after_first)
            cm_auths = [
                row
                for row in session.api_mutations.filter(
                    mutation_kind="authorize_effect"
                )
                if row.request_json.get("track") == CM_TRACK
            ]
            self.assertEqual(len(cm_auths), 1, cm_auths)
            terminal = refresh_workflow(session, at_gate, client=api)
            self.assertEqual(terminal.status, "succeeded", terminal.projection)

    def test_stale_cm_plan_revision_fails_closed_on_execute(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api, _connected = self._create_connected_cm_import(
                case_id="stale", cm_mode="execute"
            )
            at_gate = self._drive(
                session, workflow, api, stop_at_cm=True, cm_mode="execute"
            )
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            token = page.context["tokens"]["effect"]
            stored = session.active_workflow
            self.assertIsNotNone(stored)
            live_revision = int(stored.revision)
            stored.revision = live_revision + 99
            stored.save(update_fields=["revision", "updated_at"])
            posted = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "execute"},
            )
            self.assertEqual(posted.status_code, 302, posted.content[:500])
            messages = [str(item) for item in get_messages(posted.wsgi_request)]
            self.assertTrue(messages, "stale revision produced no operator error")
            cm_auths = [
                row
                for row in session.api_mutations.filter(
                    mutation_kind="authorize_effect"
                )
                if row.request_json.get("track") == CM_TRACK
            ]
            self.assertEqual(cm_auths, [])
            restored = ApiWorkflow.objects.get(pk=stored.pk)
            restored.revision = live_revision
            restored.save(update_fields=["revision", "updated_at"])
            current = refresh_workflow(session, at_gate, client=api)
            self.assertEqual(current.status, "awaiting_effect_authorization")
            self.assertEqual(
                (current.projection.get("effect_intent") or {}).get("track"),
                CM_TRACK,
            )

            owner = owner_session_for_import_session(session)
            intent = current.projection["effect_intent"]
            stale_api = requests.post(
                f"{self.base_url}/v1/workflows/{current.run_id}/effect-authorizations",
                json={
                    "expected_revision": int(current.revision) + 99,
                    "intent_id": intent["intent_id"],
                    "track": intent["track"],
                    "maximum_mode": intent["maximum_mode"],
                    "selected_mode": "execute",
                    "target_provider_id": intent["target_provider_id"],
                    "target_fingerprint": intent["target_fingerprint"],
                    "work_digest": intent["work_digest"],
                    "confirmation": intent["confirmation"],
                },
                headers={
                    "X-Owner-Session": owner,
                    "Idempotency-Key": f"stale-api-{session.id}",
                },
                timeout=15,
            )
            self.assertIn(stale_api.status_code, {409, 422}, stale_api.text)
            still = refresh_workflow(session, current, client=api)
            self.assertEqual(still.status, "awaiting_effect_authorization")
            self.assertIsNone(still.projection.get("terminal_evidence"))

    def test_disconnect_after_cm_freeze_fails_closed_on_execute(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api, connected = self._create_connected_cm_import(
                case_id="disc", cm_mode="execute"
            )
            at_gate = self._drive(
                session, workflow, api, stop_at_cm=True, cm_mode="execute"
            )
            connection_id = connected["connection_id"]
            disconnected = self.client.post(
                reverse("importer:crm_disconnect", args=[connection_id])
            )
            self.assertEqual(disconnected.status_code, 302, disconnected.content[:500])
            owner = owner_session_for_import_session(session)
            probe = requests.post(
                f"{self.base_url}/v1/crm/connections/{connection_id}/disconnect",
                headers={
                    "X-Owner-Session": owner,
                    "Idempotency-Key": f"disc-api-{session.id}",
                },
                timeout=15,
            )
            self.assertIn(probe.status_code, {200, 409, 422}, probe.text)
            posted = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {
                    "form_token": self.client.get(
                        reverse("importer:workflow", args=[session.id])
                    ).context["tokens"]["effect"],
                    "selected_mode": "execute",
                },
            )
            self.assertEqual(posted.status_code, 302, posted.content[:500])
            self._poll_pending_mutations(session)
            current = refresh_workflow(session, at_gate, client=api)
            self.assertNotEqual(current.status, "succeeded", current.projection)
            self.assertIsNone(current.projection.get("terminal_evidence"))
            messages = [str(item).lower() for item in get_messages(posted.wsgi_request)]
            joined = " ".join(messages)
            mutation = (
                session.api_mutations.filter(mutation_kind="authorize_effect")
                .order_by("created_at")
                .last()
            )
            if mutation is not None and mutation.request_json.get("track") == CM_TRACK:
                self.assertNotEqual(mutation.state, ApiMutation.State.COMPLETED)
            else:
                self.assertTrue(
                    "connect" in joined or "disconnect" in joined or messages,
                    messages,
                )

    def test_too_many_matches_cannot_freeze_via_unmocked_django_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session = ImportSession.objects.create(
                owner_id=self.owner,
                product_key="easyimports.list_import",
                target_provider_id="fake-crm-phase0b-v1",
                operator_label="FE-CM-4 Lookup Operator",
                setup_entity="people",
                setup_operation="crm_matching",
                setup_reference_source="connected_crm",
            )
            connected = self._connect_fake(session, key="lookup-flood")
            session.setup_connection_id = connected["connection_id"]
            session.save(update_fields=["setup_connection_id", "updated_at"])

            search = self.client.get(
                reverse("importer:campaign_lookup", args=[session.id]),
                {"q": FLOOD_NAME, "mode": "name_exact"},
            )
            self.assertEqual(search.status_code, 200, search.content[:500])
            body = search.json()
            self.assertTrue(body.get("too_many_matches") or body.get("truncated"), body)
            self.assertEqual(body.get("campaigns") or [], [])

            unique = self.client.get(
                reverse("importer:campaign_lookup", args=[session.id]),
                {"q": CAMPAIGN_NAME, "mode": "name_exact"},
            )
            self.assertEqual(unique.status_code, 200, unique.content[:500])
            unique_body = unique.json()
            self.assertFalse(unique_body.get("too_many_matches"), unique_body)
            self.assertEqual(unique_body.get("returned"), 1)

            token = issue_form_token(
                owner_id=session.owner_id,
                session=session,
                action_kind="cm_resolution_pick",
                logical_action_identity=f"session:{session.id}:cm_resolution_pick",
                logical_action_generation=0,
            )
            posted = self.client.post(
                reverse("importer:campaign_resolution_pick", args=[session.id]),
                {
                    "form_token": token,
                    "match_key": FLOOD_NAME,
                    "campaign_id": CAMPAIGN_ID,
                    "cm_q": FLOOD_NAME,
                },
            )
            self.assertEqual(posted.status_code, 302, posted.content[:500])
            session.refresh_from_db()
            self.assertEqual(load_session_resolutions(session), [])
            messages = [str(item).lower() for item in get_messages(posted.wsgi_request)]
            joined = " ".join(messages)
            self.assertTrue(
                "too many" in joined or "not saved" in joined or messages,
                messages,
            )
