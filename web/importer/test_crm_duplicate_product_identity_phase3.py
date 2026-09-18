"""Phase 3 — base product identity across CRM-dupe review/continuation.

Authority:
``production_hubspot_crm_duplicate_operator_path_reliability.md`` Phase **3**.
Network-free only.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from importer.api_client import EasyImportsApiClient, MutationDispatchResult
from importer.command_service import materialize_accepted_workflow, materialize_stored_receipt
from importer.models import (
    ApiMutation,
    ApiWorkflow,
    CrmDuplicateMergePlanLease,
    ImportSession,
)
from importer.setup_service import SetupValidationError, freeze_product_key
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_INTERNAL_WORKFLOW_KEYS,
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    resolve_session_product_key_for_projection,
    store_workflow_projection,
)


def _projection(
    *,
    run_id: str,
    workflow_key: str,
    status: str = "awaiting_effect_authorization",
    revision: int = 2,
) -> dict:
    return {
        "run_id": run_id,
        "workflow_key": workflow_key,
        "workflow_version": 4 if workflow_key == DUPLICATE_RESOLUTION_PRODUCT_KEY else 6,
        "target_provider_id": "fake-preview-v1",
        "status": status,
        "stage": "effect",
        "revision": revision,
        "summary": {},
        "effect_intent": {
            "intent_id": "intent-1",
            "track": "duplicate_execution",
            "maximum_mode": "execute",
            "supported_modes": ["dry_run", "execute"],
            "target_provider_id": "fake-preview-v1",
            "target_fingerprint": "fp",
            "work_digest": "digest",
            "confirmation": "confirm",
        },
    }


class ResolveSessionProductKeyTests(TestCase):
    def test_closed_internal_boxes_map_to_base_product(self):
        for key in sorted(DUPLICATE_RESOLUTION_INTERNAL_WORKFLOW_KEYS):
            self.assertEqual(
                resolve_session_product_key_for_projection(
                    role=ApiWorkflow.Role.PRIMARY,
                    workflow_key=key,
                ),
                DUPLICATE_RESOLUTION_PRODUCT_KEY,
            )
            self.assertEqual(
                resolve_session_product_key_for_projection(
                    role=ApiWorkflow.Role.REVIEW,
                    workflow_key=key,
                ),
                DUPLICATE_RESOLUTION_PRODUCT_KEY,
            )

    def test_continuation_base_product_stays_base(self):
        self.assertEqual(
            resolve_session_product_key_for_projection(
                role=ApiWorkflow.Role.CONTINUATION,
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            ),
            DUPLICATE_RESOLUTION_PRODUCT_KEY,
        )

    def test_unrelated_primary_product_passes_through(self):
        self.assertEqual(
            resolve_session_product_key_for_projection(
                role=ApiWorkflow.Role.PRIMARY,
                workflow_key="easyimports.list_import",
            ),
            "easyimports.list_import",
        )

    def test_prefix_guessing_is_not_accepted(self):
        # Unknown suffix must not map via prefix; only closed boxes map.
        self.assertEqual(
            resolve_session_product_key_for_projection(
                role=ApiWorkflow.Role.PRIMARY,
                workflow_key="easyimports.duplicate_resolution.unknown_box",
            ),
            "easyimports.duplicate_resolution.unknown_box",
        )
        self.assertEqual(
            resolve_session_product_key_for_projection(
                role=ApiWorkflow.Role.CONTINUATION,
                workflow_key="easyimports.duplicate_resolution.unknown_box",
            ),
            "easyimports.duplicate_resolution.unknown_box",
        )

    def test_foreign_continuation_key_not_rebranded_by_source_lineage(self):
        """list_import continuation must not become duplicate product via source."""

        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            product_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            target_provider_id="fake-preview-v1",
        )
        source = store_workflow_projection(
            session,
            _projection(
                run_id="run-dupe-source",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="running",
                revision=1,
            ),
            role=ApiWorkflow.Role.PRIMARY,
        )
        resolved = resolve_session_product_key_for_projection(
            role=ApiWorkflow.Role.CONTINUATION,
            workflow_key="easyimports.list_import",
            source_workflow=source,
        )
        self.assertEqual(resolved, "easyimports.list_import")
        with self.assertRaises(SetupValidationError):
            freeze_product_key(session, resolved)
        session.refresh_from_db()
        self.assertEqual(session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)


class ProductIdentityMaterializeTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            target_provider_id="fake-preview-v1",
            operator_label="Operator",
        )
        self.primary = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-source",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_review",
                revision=1,
            ),
            role=ApiWorkflow.Role.PRIMARY,
        )

    def test_account_review_materialize_does_not_conflict(self):
        mutation = ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source/review-workflows",
            logical_action_identity="start-review",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            resource_identity="run-source",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-account-review",
                "resource": "/v1/workflows/run-account-review",
                "revision": 1,
            },
        )
        projection = _projection(
            run_id="run-account-review",
            workflow_key="easyimports.duplicate_resolution.account_review",
            status="running",
            revision=1,
        )
        with patch.object(EasyImportsApiClient, "workflow", return_value=projection):
            workflow = materialize_stored_receipt(self.session, mutation)
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.role, ApiWorkflow.Role.REVIEW)
        self.assertEqual(workflow.source_workflow_id, self.primary.id)
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)
        # Review materialization must preserve the active primary.
        self.assertEqual(self.session.active_workflow_id, self.primary.id)

    def test_person_review_materialize_does_not_conflict(self):
        mutation = ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source/review-workflows",
            logical_action_identity="start-review-person",
            logical_action_generation=0,
            form_payload_digest="e" * 64,
            resource_identity="run-source",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-person-review",
                "resource": "/v1/workflows/run-person-review",
                "revision": 1,
            },
        )
        projection = _projection(
            run_id="run-person-review",
            workflow_key="easyimports.duplicate_resolution.person_review",
            status="running",
            revision=1,
        )
        with patch.object(EasyImportsApiClient, "workflow", return_value=projection):
            workflow = materialize_stored_receipt(self.session, mutation)
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.role, ApiWorkflow.Role.REVIEW)
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)

    def test_mislabelled_primary_account_review_still_resolves_base_product(self):
        """Recovery safety: even wrong role cannot corrupt session product."""

        projection = _projection(
            run_id="run-bad-primary-review",
            workflow_key="easyimports.duplicate_resolution.account_review",
            status="succeeded",
            revision=3,
        )
        mutation = ApiMutation.objects.create(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="authorize_effect",
            route="/v1/workflows/run-bad-primary-review/effect-authorizations",
            logical_action_identity="authorize",
            logical_action_generation=0,
            form_payload_digest="f" * 64,
            resource_identity="run-bad-primary-review",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-bad-primary-review",
                "resource": "/v1/workflows/run-bad-primary-review",
                "revision": 3,
            },
        )
        with patch.object(EasyImportsApiClient, "workflow", return_value=projection):
            materialize_accepted_workflow(
                self.session,
                MutationDispatchResult(mutation, mutation.response_json),
                role=ApiWorkflow.Role.PRIMARY,
            )
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)

    def test_different_base_product_still_fails_closed(self):
        with self.assertRaises(SetupValidationError):
            freeze_product_key(self.session, "easyimports.list_import")
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)

    def test_continuation_store_preserves_role_and_source_on_reload(self):
        cont = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-cont",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="succeeded",
                revision=4,
            ),
            role=ApiWorkflow.Role.CONTINUATION,
            source_workflow=self.primary,
            make_active=False,
        )
        reloaded = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-cont",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="succeeded",
                revision=5,
            ),
            role=ApiWorkflow.Role.CONTINUATION,
            source_workflow=self.primary,
            make_active=False,
        )
        self.assertEqual(reloaded.id, cont.id)
        self.assertEqual(reloaded.role, ApiWorkflow.Role.CONTINUATION)
        self.assertEqual(reloaded.source_workflow_id, self.primary.id)
        self.assertEqual(reloaded.revision, 5)
        # Primary analysis workflow remains active; session product unchanged.
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.role, ApiWorkflow.Role.PRIMARY)
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)
        self.assertEqual(self.session.active_workflow_id, self.primary.id)


class JourneyViewsProjectionInventoryTests(TestCase):
    def test_continuation_and_review_writes_are_role_explicit(self):
        """Static inventory: continuation/review stores set role, never default primary."""

        path = Path(__file__).resolve().parent / "journey_views.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        store_calls: list[ast.Call] = []

        class Visitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in {
                    "store_workflow_projection",
                    "_store_continuation_projection",
                    "_store_review_projection",
                }:
                    store_calls.append(node)
                self.generic_visit(node)

        Visitor().visit(tree)
        self.assertGreaterEqual(len(store_calls), 4)

        bare_store = [
            call
            for call in store_calls
            if isinstance(call.func, ast.Name)
            and call.func.id == "store_workflow_projection"
        ]
        for call in bare_store:
            keywords = {kw.arg for kw in call.keywords if kw.arg}
            # Every direct store must pass role=... (no accidental primary default).
            self.assertIn(
                "role",
                keywords,
                msg=f"store_workflow_projection missing role= at line {call.lineno}",
            )

        # Helpers for review/continuation must exist and be used.
        helper_names = {
            call.func.id
            for call in store_calls
            if isinstance(call.func, ast.Name)
        }
        self.assertIn("_store_continuation_projection", helper_names)
        self.assertIn("_store_review_projection", helper_names)

        # Helpers default make_active=False (read-only reloads). Authorize may
        # pass make_active=True for terminal visibility while keeping role.
        helper_defs = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name
            in {"_store_continuation_projection", "_store_review_projection"}
        }
        for name, fn in helper_defs.items():
            defaults_false = False
            # Function signature default make_active: bool = False
            for arg, default in zip(
                reversed(fn.args.args), reversed(fn.args.defaults)
            ):
                if arg.arg == "make_active" and isinstance(default, ast.Constant):
                    if default.value is False:
                        defaults_false = True
            # Or explicit keyword in body store call.
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "make_active":
                        if isinstance(kw.value, ast.Name) and kw.value.id == "make_active":
                            defaults_false = True
                        if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                            defaults_false = True
            self.assertTrue(
                defaults_false,
                msg=f"{name} must default make_active=False for read-only stores",
            )


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class MergeSummaryContinuationIdentityTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            target_provider_id="fake-preview-v1",
            operator_label="Operator",
            options={
                "run_id": "run-source",
                "source_run_id": "run-source",
                "review_run_id": "run-review",
            },
        )
        CrmDuplicateMergePlanLease.objects.create(
            session=self.session,
            epoch=0,
            continuation_run_id="run-cont",
            decision_set_content_digest="sha256:x",
        )
        self.primary = store_workflow_projection(
            self.session,
            _projection(
                run_id="run-source",
                workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
                status="awaiting_review",
                revision=1,
            ),
            role=ApiWorkflow.Role.PRIMARY,
        )

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    def test_merge_summary_get_stores_continuation_role_not_primary(self):
        cont_projection = _projection(
            run_id="run-cont",
            workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            status="awaiting_effect_authorization",
            revision=2,
        )
        reviewed = {
            "review_contract": "easyimports.crm.duplicate_reviewed_result.v1",
            "entity": "account",
            "total_group_count": 1,
            "decided_group_count": 1,
            "approved_merge_group_count": 1,
            "declined_group_count": 0,
            "quarantined_group_count": 0,
            "survivor_count": 1,
            "loser_count": 1,
            "analyzed_record_count": 2,
            "complete": True,
            "merge_plan_frozen": True,
            "decision_set_content_digest": "sha256:x",
            "dispositions": [],
            "expected_revision": 1,
        }
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=reviewed,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=cont_projection,
                    ):
                        resp = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": self.session.id},
                            )
                        )
        self.assertEqual(resp.status_code, 200)
        cont = ApiWorkflow.objects.get(run_id="run-cont")
        self.assertEqual(cont.role, ApiWorkflow.Role.CONTINUATION)
        self.assertEqual(cont.source_workflow_id, self.primary.id)
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)
        # Read-only merge GET must keep the analysis primary active.
        self.assertEqual(self.session.active_workflow_id, self.primary.id)
        # Repeated GET remains idempotent for role/source/product/active.
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=reviewed,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value={**cont_projection, "revision": 3},
                    ):
                        resp2 = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": self.session.id},
                            )
                        )
        self.assertEqual(resp2.status_code, 200)
        cont.refresh_from_db()
        self.assertEqual(cont.role, ApiWorkflow.Role.CONTINUATION)
        self.assertEqual(cont.source_workflow_id, self.primary.id)
        self.assertEqual(cont.revision, 3)
        self.session.refresh_from_db()
        self.assertEqual(self.session.product_key, DUPLICATE_RESOLUTION_PRODUCT_KEY)
        self.assertEqual(self.session.active_workflow_id, self.primary.id)
        # Primary analysis row remains primary and active.
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.role, ApiWorkflow.Role.PRIMARY)
