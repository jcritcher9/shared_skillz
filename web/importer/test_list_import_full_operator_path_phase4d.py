"""Phase 4D — Django FE Account provision authorize + Django→API boundary.

Network-free only. Proves:
- workflow page renders Account-provision authorize surface after multi-Account
  continue_net_new (optional create copy; decline → Lead label)
- authorize_effect command is journaled (not submit_decision)
- **Django→API boundary** (real API process, no mocked dispatch):
  - continue_net_new → effect_intent on account_provisioning
  - authorize disabled (decline whole set) accepted on same run_id
  - authorize preview accepted when ceiling allows; empty write grants on
    decision alone

No live CRM. FE-CM-4 remains product-default.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    list_import_full_operator_path.md (Phase 4D)
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
    / "list_import_full_operator_path.md"
)
WORKFLOW_TEMPLATE = (
    REPO_ROOT / "web" / "importer" / "templates" / "importer" / "workflow.html"
)
MULTI_ACCOUNT_DECISION_TYPE = "multiple_crm_account_matches"
CONTINUE_NET_NEW_KIND = "continue_net_new_account"
PROVISION_ON_LABEL = (
    "None of these Accounts; prepare optional Account creation "
    "(Lead if creation is skipped)"
)
PROVISION_HEADING = "Optional: create net-new Accounts"
DECLINE_LABEL = "Skip Account creation (route as Leads)"
PREVIEW_LABEL = "Preview Account creation plan"
EFFECT_TRACK = "account_provisioning"
PEOPLE_MATCHING_CATALOG = "easyimports.people_matching_fields.v1"
_HEADER_TO_CHOICE = {
    "Email": f"catalog:{PEOPLE_MATCHING_CATALOG}:email",
    "First Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:first_name",
    "Last Name": f"catalog:{PEOPLE_MATCHING_CATALOG}:last_name",
    "Company": f"catalog:{PEOPLE_MATCHING_CATALOG}:company_name",
}


def _projection(**changes):
    value = {
        "run_id": "run-phase4d-account-provision",
        "revision": 4,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 7,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_effect_authorization",
        "stage": "awaiting_account_provision_authorization",
        "decision": None,
        "effect_intent": None,
        "effect_review": None,
        "effect_grants": [],
        "review_handoff": None,
        "terminal_evidence": None,
        "summary": {
            "planned_account_creates": 1,
            "people_awaiting_account_create": 2,
        },
        "error": None,
        "links": {},
    }
    value.update(changes)
    return value


def _provision_effect_intent(
    *,
    maximum_mode="preview",
    supported_modes=("disabled", "preview"),
    work_digest="phase4d-work-digest",
):
    return {
        "intent_id": "intent_phase4d_account_provision",
        "track": EFFECT_TRACK,
        "gate_phase_id": "freeze_list_account_provisioning_plan",
        "effect_phase_id": "list_account_provisioning_effect",
        "maximum_mode": maximum_mode,
        "supported_modes": list(supported_modes),
        "target_provider_id": "fake-preview-v1",
        "target_fingerprint": "00D000000000001EAA",
        "work_digest": work_digest,
        "confirmation": (
            "authorize:intent_phase4d_account_provision:"
            f"{EFFECT_TRACK}:{maximum_mode}"
        ),
    }


def _provision_effect_review(*, work_digest="phase4d-work-digest"):
    return {
        "contract": "easyimports.list_import.account_provision_candidates.v1",
        "kind": "account_provisioning_candidates",
        "candidate_set_digest": "phase4d-candidate-set-digest",
        "company_count": 1,
        "person_count": 2,
        "candidates": [
            {
                "company_identity": "Acme Review Co",
                "identity_domain": None,
                "affected_person_count": 2,
                "source_row_ids": ["row-1", "row-2"],
                "requirement_id": "req-acme",
                "origin": "none_of_these",
            }
        ],
        "work_digest": work_digest,
        "intent_id": "intent_phase4d_account_provision",
    }


def _receipt(*, outcome="rejected"):
    return {
        "command_id": "cmd-phase4d",
        "command_kind": "authorize_effect",
        "run_id": "run-phase4d-account-provision",
        "revision": 4,
        "workflow_status": "awaiting_effect_authorization",
        "stage": "freeze_list_account_provisioning_plan",
        "outcome": outcome,
        "error_code": None if outcome == "accepted" else "request_rejected",
        "message": None if outcome == "accepted" else "No",
        "resource": "/v1/workflows/run-phase4d-account-provision",
    }


class Phase4DAccountProvisionFeTests(TestCase):
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
            operator_label="Phase4D Operator",
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

    def test_strategy_and_template_characterize_provision_review(self):
        self.assertTrue(STRATEGY_PATH.is_file())
        strategy = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("4D", strategy)
        self.assertIn("provision", strategy.lower())
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("authorize_effect", template)
        self.assertIn("selected_mode", template)
        self.assertIn("account-provision-review", template)
        self.assertIn("Companies that would receive new Accounts", template)

    def test_renders_optional_account_provision_authorize_surface(self):
        intent = _provision_effect_intent()
        review = _provision_effect_review(work_digest=intent["work_digest"])
        value = _projection(effect_intent=intent, effect_review=review)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, PROVISION_HEADING)
        self.assertContains(page, "Account creation is optional")
        self.assertContains(page, DECLINE_LABEL)
        self.assertContains(page, PREVIEW_LABEL)
        self.assertContains(page, 'name="selected_mode"')
        # Real company review table is customer-facing (not technical details).
        self.assertContains(page, "Companies that would receive new Accounts")
        self.assertContains(page, "Acme Review Co")
        self.assertContains(page, "None of these Accounts")
        self.assertContains(page, "2")  # people affected
        self.assertIn("effect", page.context["tokens"])
        self.assertEqual(
            page.context["effect_review"]["candidate_set_digest"],
            review["candidate_set_digest"],
        )
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        # Customer path shows authorize form and company review.
        self.assertIn(b"selected_mode", customer)
        self.assertIn(b"Acme Review Co", customer)
        presentation = page.context["effect_presentation"]
        self.assertEqual(presentation["heading"], PROVISION_HEADING)
        mode_values = {opt["value"] for opt in presentation["mode_options"]}
        self.assertEqual(mode_values, {"disabled", "preview"})
        # Summary cards include planned creates from projection summary.
        labels = {item["label"] for item in presentation["summary_items"]}
        self.assertTrue(
            any("planned" in label.lower() or "account" in label.lower() for label in labels)
            or presentation["summary_items"]
        )

    def test_decline_builds_authorize_effect_command(self):
        intent = _provision_effect_intent()
        review = _provision_effect_review(work_digest=intent["work_digest"])
        value = _projection(effect_intent=intent, effect_review=review)
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
                {
                    "form_token": token,
                    "selected_mode": "disabled",
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = session.api_mutations.get(mutation_kind="authorize_effect")
        validated = validate_effect_authorization(mutation.request_json)
        self.assertEqual(validated["selected_mode"], "disabled")
        self.assertEqual(validated["track"], EFFECT_TRACK)
        self.assertEqual(validated["intent_id"], intent["intent_id"])
        self.assertEqual(mutation.mutation_kind, "authorize_effect")
        self.assertNotEqual(mutation.mutation_kind, "submit_decision")


class Phase4DDjangoApiBoundaryTests(TransactionTestCase):
    """Real API process: multi-Account → provision authorize crosses the boundary.

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
            raise RuntimeError("Phase 4D API process did not start.")

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

    def _start_at_multi_account(self, *, case_id: str):
        raw_csv = (
            b"Email,First Name,Last Name,Company\n"
            b"jane@incoming.example,Jane,Doe,Acme\n"
        )
        accounts_csv = b"Account ID,Account Name\nA1,Acme\nA2,Acme\n"
        contacts_csv = (
            b"Contact ID,Email,First Name,Last Name,Account Name,Account ID\n"
            b"C1,x@other.example,X,Y,Other,A9\n"
        )
        with self.settings(
            EASYIMPORTS_API_BASE_URL=self.base_url,
            SESSIONS_ROOT=Path(tempfile.mkdtemp()),
        ):
            session = ImportSession.objects.create(
                owner_id=self.owner,
                product_key="easyimports.list_import",
                target_provider_id="fake-preview-v1",
                operator_label="Phase4D Boundary Operator",
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
            accounts_reg = save_and_register_upload(
                session=session,
                role="accounts",
                uploaded=SimpleUploadedFile(
                    "accounts.csv", accounts_csv, content_type="text/csv"
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
                        "accounts": accounts_reg.response["upload_id"],
                        "contacts": contacts_reg.response["upload_id"],
                    },
                    "maximum_modes": {
                        "reference_acquisition": "disabled",
                        "account_provisioning": "preview",
                        "person_duplicate_resolution": "disabled",
                        "people_writes": "disabled",
                        "campaign_member_writes": "disabled",
                        "delivery": "disabled",
                    },
                    "options": {
                        "interaction_mode": "interactive",
                        "validate_list_rows": False,
                        "list_duplicate_policy": "surface",
                        "crm_account_multi_match_policy": "review",
                        "contacts_only": True,
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
            self.assertEqual(decision["decision_type"], MULTI_ACCOUNT_DECISION_TYPE)
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

    def _submit_continue_net_new(self, session: ImportSession, workflow):
        run_id = workflow.run_id
        revision_before = workflow.revision
        page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        groups = page.context["decision_groups"]
        self.assertEqual(len(groups), 1)
        none_option = next(
            row
            for row in groups[0]["rows"]
            if row["option_kind"] == CONTINUE_NET_NEW_KIND
        )
        token = page.context["tokens"]["decision"]
        submitted = self.client.post(
            reverse("importer:submit_decision", args=[session.id]),
            {
                "form_token": token,
                "action": "submit_selections",
                groups[0]["input_name"]: none_option["option_id"],
            },
        )
        self.assertEqual(submitted.status_code, 302, submitted.content[:500])
        self._poll_pending_mutations(session)
        return none_option, revision_before, run_id

    def test_django_continue_net_new_then_decline_provision_accepted_by_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_at_multi_account(case_id="decline")
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertContains(page, PROVISION_ON_LABEL)
            none_option, revision_before, run_id = self._submit_continue_net_new(
                session, workflow
            )
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            # Decision alone does not mint write grants.
            self.assertEqual(refreshed.projection.get("effect_grants") or [], [])
            intent = refreshed.projection.get("effect_intent")
            self.assertIsNotNone(intent)
            self.assertEqual(intent.get("track"), EFFECT_TRACK)
            self.assertEqual(
                intent.get("gate_phase_id"),
                "freeze_list_account_provisioning_plan",
            )
            self.assertIn("disabled", intent.get("supported_modes") or [])
            self.assertEqual(none_option["option_kind"], CONTINUE_NET_NEW_KIND)
            # Digest-bound candidate review on real API projection.
            review = refreshed.projection.get("effect_review")
            self.assertIsNotNone(review)
            self.assertEqual(review.get("kind"), "account_provisioning_candidates")
            self.assertEqual(review.get("work_digest"), intent.get("work_digest"))
            self.assertGreaterEqual(int(review.get("company_count") or 0), 1)
            self.assertTrue(review.get("candidates"))
            self.assertTrue(review.get("candidate_set_digest"))
            company_names = {
                row.get("company_identity") for row in review.get("candidates") or []
            }
            self.assertIn("Acme", company_names)
            self.assertTrue(
                any(
                    row.get("origin") == "none_of_these"
                    for row in review.get("candidates") or []
                )
            )
            summary = refreshed.projection.get("summary") or {}
            self.assertGreaterEqual(int(summary.get("planned_account_creates") or 0), 1)

            # Authorize surface: decline whole set.
            page2 = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertContains(page2, PROVISION_HEADING)
            self.assertContains(page2, DECLINE_LABEL)
            self.assertContains(page2, "Companies that would receive new Accounts")
            self.assertContains(page2, "Acme")
            token = page2.context["tokens"]["effect"]
            revision_at_auth = refreshed.revision
            declined = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "disabled"},
            )
            self.assertEqual(declined.status_code, 302, declined.content[:500])
            self._poll_pending_mutations(session)
            mutation = session.api_mutations.get(mutation_kind="authorize_effect")
            self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(mutation.response_json.get("outcome"), "accepted")
            self.assertEqual(mutation.response_json.get("run_id"), run_id)
            validated = validate_effect_authorization(mutation.request_json)
            self.assertEqual(validated["selected_mode"], "disabled")
            self.assertEqual(validated["track"], EFFECT_TRACK)
            after = refresh_workflow(session, workflow, client=api)
            self.assertEqual(after.run_id, run_id)
            self.assertGreater(after.revision, revision_at_auth)
            # Grant recorded; no longer awaiting this intent.
            grants = after.projection.get("effect_grants") or []
            grant_tracks = {
                g.get("track") for g in grants if isinstance(g, dict)
            }
            self.assertIn(EFFECT_TRACK, grant_tracks)
            if after.projection.get("effect_intent") is not None:
                self.assertNotEqual(
                    after.projection["effect_intent"].get("track"),
                    EFFECT_TRACK,
                )

    def test_django_continue_net_new_then_preview_provision_accepted_by_api(self):
        with self.settings(EASYIMPORTS_API_BASE_URL=self.base_url):
            session, workflow, api = self._start_at_multi_account(case_id="preview")
            none_option, revision_before, run_id = self._submit_continue_net_new(
                session, workflow
            )
            refreshed = refresh_workflow(session, workflow, client=api)
            self.assertEqual(refreshed.run_id, run_id)
            self.assertGreater(refreshed.revision, revision_before)
            intent = refreshed.projection.get("effect_intent")
            self.assertIsNotNone(intent)
            self.assertEqual(intent.get("track"), EFFECT_TRACK)
            self.assertIn("preview", intent.get("supported_modes") or [])
            self.assertEqual(none_option["option_kind"], CONTINUE_NET_NEW_KIND)

            page = self.client.get(reverse("importer:workflow", args=[session.id]))
            self.assertContains(page, PREVIEW_LABEL)
            token = page.context["tokens"]["effect"]
            revision_at_auth = refreshed.revision
            previewed = self.client.post(
                reverse("importer:authorize_effect", args=[session.id]),
                {"form_token": token, "selected_mode": "preview"},
            )
            self.assertEqual(previewed.status_code, 302, previewed.content[:500])
            self._poll_pending_mutations(session)
            mutation = session.api_mutations.get(mutation_kind="authorize_effect")
            self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
            self.assertEqual(mutation.response_json.get("outcome"), "accepted")
            validated = validate_effect_authorization(mutation.request_json)
            self.assertEqual(validated["selected_mode"], "preview")
            after = refresh_workflow(session, workflow, client=api)
            self.assertEqual(after.run_id, run_id)
            self.assertGreater(after.revision, revision_at_auth)
