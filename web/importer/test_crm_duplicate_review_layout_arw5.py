"""ARW-5: skip/quarantine sit below the member comparison table.

Layout only. The controls keep name=\"duplicate_choice\" and the same
skip/quarantine values. Django still does not import mappings_2.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_review_window_integrity_and_identity_cost.md
Phase ARW-5.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from importer.api_client import EasyImportsApiClient, MutationDispatchResult
from importer.api_contract import validate_decision_command
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import (
    DUPLICATE_REVIEW_QUARANTINE_VALUE,
    DUPLICATE_REVIEW_SKIP_VALUE,
)


WORKFLOW_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "importer" / "workflow.html"
)
JOURNEY_REVIEW_TEMPLATE = (
    Path(__file__).resolve().parent
    / "templates"
    / "importer"
    / "crm_duplicate_journey_review.html"
)


def _duplicate_review_branch(source: str) -> str:
    start = source.index(
        '{% elif dtype == "account_duplicate_group_review" or dtype == "person_duplicate_group_review" %}'
    )
    end = source.index(
        'This decision type is not supported by this web version.',
        start,
    )
    return source[start:end]


class Arw5TemplatePlacementTests(SimpleTestCase):
    def test_workflow_skip_quarantine_render_after_comparison_table(self):
        branch = _duplicate_review_branch(
            WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        )
        table_close = branch.index("</table>")
        action_loop = branch.index(
            "{% for row in duplicate_choice_rows %}",
            table_close,
        )
        self.assertGreater(action_loop, table_close)
        table_source = branch[:table_close]
        self.assertIn("{% if not row.synthetic %}", table_source)
        self.assertIn('name="duplicate_choice"', table_source)
        self.assertNotIn("Skip this group", table_source)
        self.assertNotIn("Quarantine this group", table_source)
        after_table = branch[table_close:]
        self.assertIn("{% if row.synthetic %}", after_table)
        self.assertIn('name="duplicate_choice"', after_table)
        self.assertIn("{{ row.choice_value }}", after_table)
        self.assertIn("{{ row.label }}", after_table)

    def test_journey_review_outcomes_already_follow_the_member_table(self):
        source = JOURNEY_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        table_close = source.index("</table>")
        self.assertGreater(source.index('name="action_{{ card.group_id }}"'), table_close)
        self.assertGreater(source.index('value="decline"'), table_close)
        self.assertGreater(source.index('value="quarantine"'), table_close)
        self.assertNotIn("Skip this group", source[source.find("<table") : table_close])
        self.assertNotIn(
            "Quarantine this group", source[source.find("<table") : table_close]
        )


def _source_projection() -> dict:
    return {
        "run_id": "run-source-arw5",
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_review",
        "stage": "review",
        "revision": 2,
        "summary": {},
        "review_handoff": {
            "handoff_id": "handoff-arw5",
            "entity": "Account",
            "group_count": 1,
        },
    }


def _child_projection() -> dict:
    return {
        "run_id": "run-review-arw5",
        "workflow_key": "easyimports.duplicate_resolution.account_review",
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "needs_decision",
        "stage": "review_account_duplicate_groups",
        "revision": 3,
        "summary": {},
        "decision": {
            "decision_id": "dec-arw5",
            "decision_type": "account_duplicate_group_review",
            "body": {
                "title": "Review this group",
                "message": "Choose a winner.",
                "duplicate_group_id": "account_dupe_arw5",
                "group_revision": "rev-arw5",
                "confidence_score": 80,
                "confidence_band": "medium",
                "allowed_actions": [
                    "approve",
                    "decline",
                    "quarantine",
                    "override_survivor",
                ],
                "recommended_survivor_id": "001-survivor",
                "selected_survivor_id": "001-survivor",
                "advanced_review_required": False,
                "execution_blockers": [],
                "group_members_df": {
                    "columns": ["account_id"],
                    "rows": [
                        {"account_id": "001-survivor"},
                        {"account_id": "001-other"},
                    ],
                },
                "ranking_df": {"columns": [], "rows": []},
                "accounts_df": {"columns": [], "rows": []},
            },
        },
    }


def _reject_dispatch(mutation, **_kwargs):
    payload = {
        "outcome": "rejected",
        "error": {"message": "No", "code": "request_rejected"},
    }
    mutation.state = ApiMutation.State.REJECTED
    mutation.response_json = payload
    mutation.error_code = "request_rejected"
    mutation.error_message = "No"
    mutation.save()
    return MutationDispatchResult(mutation, payload)


class Arw5RenderedControlsTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            target_provider_id="fake-preview-v1",
            operator_label="Operator",
        )
        self.primary = store_workflow_projection(
            self.session,
            _source_projection(),
            role=ApiWorkflow.Role.PRIMARY,
        )
        self.review = store_workflow_projection(
            self.session,
            _child_projection(),
            role=ApiWorkflow.Role.REVIEW,
            source_workflow=self.primary,
            make_active=False,
        )
        ApiMutation.objects.create(
            session=self.session,
            workflow=self.primary,
            form_instance=uuid4(),
            idempotency_key="idem-start-review-arw5",
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source-arw5/review-workflows",
            logical_action_identity="start-review-arw5",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            resource_identity="run-source-arw5",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-review-arw5",
                "resource": "/v1/workflows/run-review-arw5",
                "revision": 3,
            },
        )

    def _get(self):
        def _workflow(run_id, *, owner_session=None):
            if run_id == "run-review-arw5":
                return _child_projection()
            if run_id == "run-source-arw5":
                return _source_projection()
            raise AssertionError(run_id)

        with (
            patch.object(EasyImportsApiClient, "workflow", side_effect=_workflow),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            return self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )

    def test_page_renders_skip_and_quarantine_outside_the_table(self):
        page = self._get()
        self.assertEqual(page.status_code, 200)
        html = page.content.decode("utf-8")
        table = html.split("<table", 1)[1].split("</table>", 1)
        table_html, after_table = table[0], table[1]
        self.assertIn("001-survivor", table_html)
        self.assertNotIn("Skip this group", table_html)
        self.assertNotIn("Quarantine this group", table_html)
        self.assertNotIn(f'value="{DUPLICATE_REVIEW_SKIP_VALUE}"', table_html)
        self.assertNotIn(f'value="{DUPLICATE_REVIEW_QUARANTINE_VALUE}"', table_html)
        after_table = after_table.split("</form>", 1)[0]
        self.assertIn("Skip this group", after_table)
        self.assertIn("Quarantine this group", after_table)
        self.assertIn('name="duplicate_choice"', after_table)
        self.assertIn(f'value="{DUPLICATE_REVIEW_SKIP_VALUE}"', after_table)
        self.assertIn(f'value="{DUPLICATE_REVIEW_QUARANTINE_VALUE}"', after_table)

    def test_skip_still_submits_decline(self):
        page = self._get()
        token = page.context["tokens"]["decision"]
        with patch.object(
            EasyImportsApiClient, "dispatch", side_effect=_reject_dispatch
        ):
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[self.session.id]),
                {
                    "form_token": token,
                    "duplicate_choice": DUPLICATE_REVIEW_SKIP_VALUE,
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = self.session.api_mutations.get(mutation_kind="submit_decision")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["response"]["action"], "decline")
        self.assertEqual(validated["response"]["duplicate_group_id"], "account_dupe_arw5")
