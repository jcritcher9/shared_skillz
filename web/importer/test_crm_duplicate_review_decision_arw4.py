"""ARW-4: Confirm winner posts the displayed child decision.

GFC-6 renders the review child's sequential card without replacing
``session.active_workflow``. The Confirm winner form therefore has no
``action`` field; it posts ``duplicate_choice=survivor:<id>``. Before this
phase, ``submit_decision`` read the source (no decision) and rejected with
"This decision type is not supported by this web version."

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_review_window_integrity_and_identity_cost.md
Phase ARW-4.
"""

from __future__ import annotations

import inspect
from html.parser import HTMLParser
from unittest.mock import patch
from uuid import uuid4

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from importer.api_client import EasyImportsApiClient, MutationDispatchResult
from importer.api_contract import validate_decision_command
from importer.models import ApiMutation, ApiWorkflow, ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    store_workflow_projection,
)
from importer.workflow_views import (
    DUPLICATE_REVIEW_SURVIVOR_PREFIX,
    _auto_merge_configured,
    _workflow_for_displayed_decision,
    submit_decision,
    workflow,
)


UNSUPPORTED_DECISION_COPY = "This decision type is not supported by this web version."


def _source_projection(*, run_id: str = "run-source-arw4", revision: int = 2) -> dict:
    return {
        "run_id": run_id,
        "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": "awaiting_review",
        "stage": "review",
        "revision": revision,
        "summary": {},
        "review_handoff": {
            "handoff_id": "handoff-arw4",
            "entity": "Account",
            "group_count": 2,
        },
        "duplicate_analysis_progress": {
            "stage": "complete",
            "completed_count": 2,
            "total_count": 2,
            "count_unit": "groups",
            "review_ready": True,
            "review_window_ready": True,
            "review_groups_ready": 2,
            "review_groups_total": 2,
        },
    }


def _child_decision(
    *,
    recommended: str = "001-survivor",
    selected: str = "001-survivor",
    other: str = "001-other",
    allowed_actions: list[str] | None = None,
) -> dict:
    return {
        "decision_id": "dec-arw4",
        "decision_type": "account_duplicate_group_review",
        "body": {
            "title": "Review this group",
            "message": "Choose a winner.",
            "duplicate_group_id": "account_dupe_arw4",
            "group_revision": "rev-arw4",
            "confidence_score": 80,
            "confidence_band": "medium",
            "allowed_actions": allowed_actions
            or ["approve", "decline", "quarantine", "override_survivor"],
            "recommended_survivor_id": recommended,
            "selected_survivor_id": selected,
            "advanced_review_required": False,
            "execution_blockers": [],
            "group_members_df": {
                "columns": ["account_id"],
                "rows": [
                    {"account_id": recommended},
                    {"account_id": other},
                ],
            },
            "ranking_df": {"columns": [], "rows": []},
            "accounts_df": {"columns": [], "rows": []},
        },
    }


def _child_projection(
    *,
    run_id: str = "run-review-arw4",
    status: str = "needs_decision",
    revision: int = 3,
    decision: dict | None = None,
    decided_group_count: int = 0,
    remaining_group_count: int = 2,
) -> dict:
    body = {
        "run_id": run_id,
        "workflow_key": "easyimports.duplicate_resolution.account_review",
        "workflow_version": 5,
        "target_provider_id": "fake-preview-v1",
        "status": status,
        "stage": "review_account_duplicate_groups",
        "revision": revision,
        "summary": {},
    }
    if status == "needs_decision":
        body["decision"] = decision or _child_decision()
        body["review_progress"] = {
            "entity": "account",
            "decided_group_count": decided_group_count,
            "remaining_group_count": remaining_group_count,
            "finish_for_now_available": False,
        }
    return body


def _next_child_decision() -> dict:
    decision = _child_decision()
    decision["decision_id"] = "dec-arw4-next"
    decision["body"] = dict(decision["body"])
    decision["body"]["duplicate_group_id"] = "account_dupe_arw4_next"
    return decision


def _advanced_child_projection() -> dict:
    return _child_projection(
        revision=4,
        decision=_next_child_decision(),
        decided_group_count=1,
        remaining_group_count=1,
    )


class _DecisionFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_decision_form = False
        self._form_depth = 0
        self.fields: dict[str, list[str]] = {}
        self.checked: dict[str, str] = {}
        self.submit_labels: list[str] = []
        self.submit_names: list[str] = []
        self._capture_submit = False
        self._submit_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "form":
            action = attrs_d.get("action") or ""
            if self._in_decision_form:
                self._form_depth += 1
            elif "/decisions" in action:
                self._in_decision_form = True
                self._form_depth = 1
            return
        if not self._in_decision_form:
            return
        if tag == "input":
            name = attrs_d.get("name")
            if not name:
                return
            value = attrs_d.get("value", "")
            self.fields.setdefault(name, []).append(value)
            if "checked" in attrs_d:
                self.checked[name] = value
        if tag == "button" and attrs_d.get("type") == "submit":
            self.submit_names.append(attrs_d.get("name") or "")
            self._capture_submit = True
            self._submit_buf = []

    def handle_endtag(self, tag):
        if tag == "form" and self._in_decision_form:
            self._form_depth -= 1
            if self._form_depth <= 0:
                self._in_decision_form = False
        if tag == "button" and self._capture_submit:
            self.submit_labels.append("".join(self._submit_buf).strip())
            self._capture_submit = False

    def handle_data(self, data):
        if self._capture_submit:
            self._submit_buf.append(data)


def _parse_decision_form(html: str) -> _DecisionFormParser:
    parser = _DecisionFormParser()
    parser.feed(html)
    return parser


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


def _accept_dispatch(mutation, **_kwargs):
    command = mutation.request_json if isinstance(mutation.request_json, dict) else {}
    response = command.get("response") if isinstance(command.get("response"), dict) else {}
    payload = {
        "command_id": "cmd-arw4-approve",
        "command_kind": "submit_decision",
        "run_id": "run-review-arw4",
        "revision": 4,
        "workflow_status": "needs_decision",
        "stage": "review_account_duplicate_groups",
        "outcome": "accepted",
        "error_code": None,
        "message": None,
        "resource": "/v1/workflows/run-review-arw4",
        "result": {
            "action": response.get("action"),
            "selected_survivor_id": response.get("selected_survivor_id"),
            "duplicate_group_id": response.get("duplicate_group_id"),
        },
    }
    mutation.state = ApiMutation.State.COMPLETED
    mutation.http_status = 200
    mutation.response_json = payload
    mutation.error_code = ""
    mutation.error_message = ""
    mutation.save()
    return MutationDispatchResult(mutation, payload)


def _api_workflows(*, child: dict):
    def _workflow(run_id, *, owner_session=None):
        if run_id == "run-review-arw4":
            return child
        if run_id == "run-source-arw4":
            return _source_projection()
        raise AssertionError(run_id)

    return _workflow


class Arw4ConfirmWinnerChildDispatchTests(TestCase):
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
            idempotency_key="idem-start-review-arw4",
            mutation_kind="start_review_workflow",
            route="/v1/workflows/run-source-arw4/review-workflows",
            logical_action_identity="start-review-arw4",
            logical_action_generation=0,
            form_payload_digest="d" * 64,
            resource_identity="run-source-arw4",
            state=ApiMutation.State.COMPLETED,
            response_json={
                "outcome": "accepted",
                "run_id": "run-review-arw4",
                "resource": "/v1/workflows/run-review-arw4",
                "revision": 3,
            },
        )

    def _get(self, *, child: dict | None = None):
        with (
            patch.object(
                EasyImportsApiClient,
                "workflow",
                side_effect=_api_workflows(child=child or _child_projection()),
            ),
            patch(
                "importer.workflow_views.dispatch_command",
                side_effect=AssertionError("GET must not dispatch"),
            ),
        ):
            return self.client.get(
                reverse("importer:workflow", args=[self.session.id])
            )

    def test_capture_confirm_winner_posts_survivor_choice_without_action(self):
        page = self._get()
        self.assertEqual(page.status_code, 200)
        html = page.content.decode("utf-8")
        self.assertIn("Confirm winner and continue", html)
        form = _parse_decision_form(html)
        self.assertIn("Confirm winner and continue", form.submit_labels)
        self.assertEqual(form.submit_names, [""])
        self.assertNotIn("action", form.fields)
        checked = form.checked.get("duplicate_choice", "")
        self.assertEqual(checked, f"{DUPLICATE_REVIEW_SURVIVOR_PREFIX}001-survivor")
        allowed = (_child_decision()["body"]["allowed_actions"])
        self.assertEqual(
            allowed,
            ["approve", "decline", "quarantine", "override_survivor"],
        )
        source_decision = (self.primary.projection or {}).get("decision")
        self.assertFalse(source_decision)
        child_type = ((self.review.projection or {}).get("decision") or {}).get(
            "decision_type"
        )
        self.assertEqual(child_type, "account_duplicate_group_review")

    def test_confirm_winner_is_accepted_and_recorded(self):
        page = self._get()
        form = _parse_decision_form(page.content.decode("utf-8"))
        post = {
            "form_token": form.fields["form_token"][0],
            "duplicate_choice": form.checked["duplicate_choice"],
        }
        self.assertNotIn("action", post)
        advanced = _advanced_child_projection()
        with (
            patch.object(EasyImportsApiClient, "dispatch", side_effect=_accept_dispatch),
            patch.object(
                EasyImportsApiClient,
                "workflow",
                side_effect=_api_workflows(child=advanced),
            ),
        ):
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[self.session.id]),
                post,
            )
        self.assertEqual(submitted.status_code, 302)
        messages = [str(item) for item in get_messages(submitted.wsgi_request)]
        self.assertFalse(any(UNSUPPORTED_DECISION_COPY in item for item in messages))
        self.assertTrue(any("completed" in item.lower() for item in messages))
        mutation = self.session.api_mutations.get(mutation_kind="submit_decision")
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.workflow_id, self.review.id)
        self.assertEqual(mutation.route, "/v1/workflows/run-review-arw4/decisions")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["decision_type"], "account_duplicate_group_review")
        self.assertEqual(validated["response"]["action"], "approve")
        self.assertEqual(validated["response"]["selected_survivor_id"], "001-survivor")
        self.assertEqual(validated["response"]["duplicate_group_id"], "account_dupe_arw4")
        self.assertEqual(mutation.response_json["outcome"], "accepted")
        self.assertEqual(mutation.response_json["result"]["action"], "approve")
        self.assertEqual(
            mutation.response_json["result"]["selected_survivor_id"], "001-survivor"
        )
        self.assertEqual(
            mutation.response_json["result"]["duplicate_group_id"], "account_dupe_arw4"
        )
        self.review.refresh_from_db()
        self.assertEqual(self.review.revision, 4)
        advanced_decision = (self.review.projection or {}).get("decision") or {}
        self.assertEqual(advanced_decision.get("decision_id"), "dec-arw4-next")
        self.assertEqual(
            (advanced_decision.get("body") or {}).get("duplicate_group_id"),
            "account_dupe_arw4_next",
        )
        self.assertEqual(
            (self.review.projection or {}).get("review_progress", {}).get(
                "decided_group_count"
            ),
            1,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_workflow_id, self.primary.id)

        follow = self._get(child=advanced)
        self.assertEqual(follow.status_code, 200)
        follow_html = follow.content.decode("utf-8")
        self.assertNotIn(UNSUPPORTED_DECISION_COPY, follow_html)
        self.assertIn("account_dupe_arw4_next", follow_html)
        self.assertIn("Confirm winner and continue", follow_html)

    def test_other_survivor_maps_to_override_survivor(self):
        page = self._get()
        form = _parse_decision_form(page.content.decode("utf-8"))
        other = f"{DUPLICATE_REVIEW_SURVIVOR_PREFIX}001-other"
        self.assertIn(other, form.fields.get("duplicate_choice") or [])
        with patch.object(
            EasyImportsApiClient, "dispatch", side_effect=_reject_dispatch
        ):
            submitted = self.client.post(
                reverse("importer:submit_decision", args=[self.session.id]),
                {
                    "form_token": form.fields["form_token"][0],
                    "duplicate_choice": other,
                },
            )
        self.assertEqual(submitted.status_code, 302)
        mutation = self.session.api_mutations.get(mutation_kind="submit_decision")
        validated = validate_decision_command(mutation.request_json)
        self.assertEqual(validated["response"]["action"], "override_survivor")
        self.assertEqual(validated["response"]["selected_survivor_id"], "001-other")
        self.assertIn("override_survivor", _child_decision()["body"]["allowed_actions"])

    def test_helper_follows_gfc6_display_rules(self):
        self.assertFalse(_auto_merge_configured(self.session))
        self.assertEqual(
            _workflow_for_displayed_decision(self.session, self.primary).id,
            self.review.id,
        )
        options = dict(self.session.options or {})
        options["auto_merge_min_confidence"] = 90
        self.session.options = options
        self.session.save(update_fields=["options", "updated_at"])
        self.assertEqual(
            _workflow_for_displayed_decision(self.session, self.primary).id,
            self.primary.id,
        )

    def test_submit_decision_binds_displayed_child(self):
        source = inspect.getsource(submit_decision)
        self.assertIn("_workflow_for_displayed_decision", source)
        self.assertIn("validate_decision_command", source)
        view_source = inspect.getsource(workflow)
        self.assertNotIn("dispatch_command(", view_source)

    def test_url_uses_submit_decision(self):
        html = self._get().content.decode("utf-8")
        self.assertRegex(html, r'<form[^>]+action="[^"]*/decisions/[^"]*"')
