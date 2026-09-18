"""Phase 1B-UI: field-decision presentation on CRM duplicate review (network-free)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .api_client import canonical_digest
from .field_merge_display import FIELD_FILL_EMPTY_SENTENCE, field_merge_display
from .journey_views import _review_group_cards
from .models import ApiWorkflow, ImportSession
from .tests import dataframe, projection


def _sample_field_merge_plan(*, survivor: str = "A1") -> dict:
    return {
        "contract": "easyimports.field_merge_public_plan.v1",
        "group_id": "g1",
        "group_revision": "rev-g1",
        "survivor_id": survivor,
        "entity_family": "company",
        "field_projection_digest": "sha256:projection",
        "policy_version": "naive_account.v1",
        "field_decisions": [
            {
                "logical_field_key": "account_linkedin_url",
                "value": "https://linkedin.com/company/acme",
                "merge_rule": "empty_fill_from_ranked_loser",
                "overwrites_existing_value": False,
                "source_member_id": "A2",
                "source_field_key": "account_linkedin_url",
            }
        ],
        "populate_field_keys": ["account_linkedin_url"],
        "merge_conflicts": [{"field_name": "account_type", "reason": "disagreement"}],
        "planned_sequence": {
            "sequence_contract": "easyimports.crm_field_merge_sequence.v1",
            "sequence_digest": "sha256:seq",
            "projection_digest": "sha256:projection",
            "steps": [
                {"step_kind": "populate_survivor", "ordinal": 1},
                {"step_kind": "verify_survivor_fields", "ordinal": 2},
                {
                    "step_kind": "consolidate_record",
                    "ordinal": 3,
                    "source_id": "A2",
                    "target_id": survivor,
                },
            ],
            "consolidate_pairs": [{"source_id": "A2", "target_id": survivor}],
        },
        "execution_intent": "populate_then_consolidate",
        "execution_intent_summary": (
            "Execute would populate survivor fields from the frozen projection, "
            "verify them, then consolidate each loser into the survivor."
        ),
    }


def _merge_group_with_plan() -> dict:
    return {
        "group_id": "g1",
        "group_revision": "rev-g1",
        "entity_family": "company",
        "group_status": "ready",
        "review_lane": "standard",
        "confidence_band": "high",
        "confidence_score": 92,
        "advanced_review_required": False,
        "execution_blockers": [],
        "allowed_actions": ["approve", "override_survivor", "decline", "quarantine"],
        "recommended_survivor_id": "A1",
        "selected_survivor_id": "A1",
        "members": [
            {
                "record_id": "A1",
                "display_fields": {"Name": "Acme"},
                "recommended": True,
                "selected": True,
                "survivor_eligible": True,
                "ranking_evidence": {},
            },
            {
                "record_id": "A2",
                "display_fields": {"Name": "Acme Co"},
                "recommended": False,
                "selected": False,
                "survivor_eligible": True,
                "ranking_evidence": {},
            },
        ],
        "conflicts": [],
        "evidence": [],
        "field_merge_plan": _sample_field_merge_plan(),
    }


class FieldMergeDisplayHelperTests(SimpleTestCase):
    def test_maps_public_plan_frame(self):
        display = field_merge_display(_sample_field_merge_plan())
        self.assertTrue(display["present"])
        self.assertEqual(display["survivor_id"], "A1")
        self.assertEqual(display["execution_intent"], "populate_then_consolidate")
        self.assertIn("populate", display["execution_intent_summary"].lower())
        self.assertEqual(len(display["field_decisions"]), 1)
        self.assertEqual(
            display["field_decisions"][0]["logical_field_key"], "account_linkedin_url"
        )
        self.assertFalse(display["field_decisions"][0]["overwrites_existing_value"])
        self.assertEqual(len(display["merge_conflicts"]), 1)
        self.assertEqual(display["consolidate_pairs"][0]["source_id"], "A2")

    def test_absent_plan_is_not_present(self):
        self.assertFalse(field_merge_display(None)["present"])
        self.assertFalse(field_merge_display("x")["present"])


class ReviewCardFieldMergeTests(SimpleTestCase):
    def test_review_cards_carry_field_merge_display(self):
        window = {
            "review_contract": "easyimports.crm.duplicate_review_window.v1",
            "window_id": "win-1",
            "window_digest": "sha256:w",
            "expected_revision": 1,
            "group_start": 1,
            "group_end": 1,
            "page_size": 5,
            "total_group_count": 1,
            "decided_group_count": 0,
            "remaining_group_count": 1,
            "groups": [_merge_group_with_plan()],
            "outcome": "next_window",
        }
        cards = _review_group_cards(window)
        self.assertEqual(len(cards), 1)
        field_merge = cards[0]["field_merge"]
        self.assertTrue(field_merge["present"])
        self.assertEqual(
            field_merge["field_decisions"][0]["logical_field_key"],
            "account_linkedin_url",
        )


class FieldMergeTemplateRenderTests(SimpleTestCase):
    def test_partial_renders_decisions_conflicts_and_intent(self):
        html = render_to_string(
            "importer/_field_merge_plan.html",
            {"field_merge": field_merge_display(_sample_field_merge_plan())},
        )
        self.assertIn("data-field-merge-plan", html)
        self.assertIn("account_linkedin_url", html)
        self.assertIn("https://linkedin.com/company/acme", html)
        self.assertIn("populate_then_consolidate", html)
        self.assertIn(FIELD_FILL_EMPTY_SENTENCE, html)
        self.assertIn("Fill blank survivor field", html)
        self.assertIn("data-field-merge-conflicts", html)
        self.assertIn("account_type", html)
        # No mutation controls in the partial.
        self.assertNotIn("<form", html)
        self.assertNotIn('type="submit"', html)

    def test_review_template_includes_field_merge_section(self):
        window = {
            "window_id": "win-1",
            "window_digest": "sha256:w",
            "expected_revision": 1,
        }
        cards = _review_group_cards(
            {
                "groups": [_merge_group_with_plan()],
                "decided_group_count": 0,
                "remaining_group_count": 1,
            }
        )
        html = render_to_string(
            "importer/crm_duplicate_journey_review.html",
            {
                "heading": "Review duplicate groups 1–1 of 1",
                "decided_group_count": 0,
                "remaining_group_count": 1,
                "auto_approved_group_count": 0,
                "form_errors": [],
                "form_token": "token",
                "window": window,
                "group_order": "g1",
                "cards": cards,
                "cta_label": "Save",
                "show_save_and_exit": False,
                "run_id": "run-1",
                "start_url": "/crm/duplicate-journeys/",
                "progress_url": "/progress/",
                "workflow_url": "/workflow/",
            },
        )
        self.assertIn("Field decisions for survivor", html)
        self.assertIn("account_linkedin_url", html)
        self.assertIn("populate_then_consolidate", html)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
)
class WorkflowAccountPersonFieldMergeRenderTests(TestCase):
    """Render Account decision body through workflow.html (not handcrafted partial-only)."""

    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def _make_session_workflow(self, value):
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

    def _account_decision_with_plan(self) -> dict:
        member_id = "001-survivor"
        plan = _sample_field_merge_plan(survivor=member_id)
        body = {
            "object_type": "Account",
            "duplicate_group_id": "group-1",
            "group_revision": "revision-1",
            "confidence_score": 92,
            "confidence_band": "high",
            "review_lane": "standard",
            "advanced_review_required": False,
            "recommended_survivor_id": member_id,
            "selected_survivor_id": member_id,
            "allowed_actions": [
                "approve",
                "decline",
                "quarantine",
                "override_survivor",
            ],
            "title": "Duplicate group review",
            "message": (
                "Review members, evidence, conflicts, and survivor projection. "
                "Execute would populate then consolidate."
            ),
            "field_projection_digest": plan["field_projection_digest"],
            "field_merge_execution_intent": plan["execution_intent"],
            "field_merge_execution_intent_summary": plan["execution_intent_summary"],
            "field_merge_plan": plan,
            "execution_blockers": [],
        }
        table_names = [
            "group_df",
            "group_members_df",
            "accounts_df",
            "evidence_df",
            "conflicts_df",
            "edge_decisions_df",
            "ranking_df",
            "recommended_survivor_df",
            "selected_survivor_df",
            "projected_survivor_df",
            "field_recommendations_df",
            "merge_conflicts_df",
            "revision_df",
        ]
        for name in table_names:
            body[name] = dataframe(
                ["account_id", "evidence"],
                [{"account_id": member_id, "evidence": name}],
            )
        return {
            "decision_id": "decision-account_duplicate_group_review",
            "decision_type": "account_duplicate_group_review",
            "phase_id": "duplicate_review",
            "body": body,
        }

    def test_workflow_account_review_renders_field_merge_from_decision_body(self):
        decision = self._account_decision_with_plan()
        value = projection(
            run_id="run-1bui-workflow-account",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=4,
            status="needs_decision",
            stage="review",
            decision=decision,
            review_progress={
                "entity": "account",
                "decided_group_count": 0,
                "remaining_group_count": 1,
                "finish_for_now_available": False,
                "finished_for_now": False,
                "deferred_group_count": 0,
                "remaining_groups_exported": False,
            },
        )
        session, workflow = self._make_session_workflow(value)
        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        # Decision body path (workflow.html Account branch), not partial-only harness.
        self.assertContains(page, "Default winner is pre-selected")
        self.assertContains(page, "Confirm winner and continue")
        self.assertContains(page, "Field decisions for survivor")
        self.assertContains(page, "account_linkedin_url")
        self.assertContains(page, "populate_then_consolidate")
        self.assertContains(page, "https://linkedin.com/company/acme")
        display = page.context["field_merge_display"]
        self.assertTrue(display["present"])
        self.assertEqual(
            display["field_decisions"][0]["logical_field_key"],
            "account_linkedin_url",
        )
