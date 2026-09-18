"""Phase 4B: five-group CRM duplicate review UI (network-free Django tests)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .api_client import (
    ApiRejectedError,
    EasyImportsApiClient,
    create_or_reuse_mutation,
)
from .api_contract import (
    ApiContractError,
    validate_command_receipt,
    validate_duplicate_review_window_submit_result,
)
from .journey_views import (
    _decisions_from_posted_cards,
    _decisions_from_posted_form,
    _default_group_choice,
    _dispatch_json_mutation,
    _is_review_complete_payload,
    _review_group_cards,
)
from .models import ApiMutation, ImportSession


def _member(record_id: str, *, recommended: bool = False, eligible: bool = True) -> dict:
    return {
        "record_id": record_id,
        "display_fields": {"name": f"Name {record_id}", "domain": f"{record_id}.example"},
        "recommended": recommended,
        "selected": recommended,
        "survivor_eligible": eligible,
        "ranking_evidence": {"score": 10 if recommended else 5},
    }


def _merge_group(group_id: str, left: str, right: str) -> dict:
    return {
        "group_id": group_id,
        "group_revision": f"rev-{group_id}",
        "entity_family": "company",
        "group_status": "ready",
        "review_lane": "standard",
        "confidence_band": "high",
        "confidence_score": 92,
        "advanced_review_required": False,
        "execution_blockers": [],
        "allowed_actions": ["approve", "override_survivor", "decline", "quarantine"],
        "recommended_survivor_id": left,
        "selected_survivor_id": left,
        "members": [
            _member(left, recommended=True),
            _member(right, recommended=False),
        ],
        "conflicts": [],
        "evidence": [{"kind": "name_match"}],
    }


def _quarantine_group(group_id: str) -> dict:
    return {
        "group_id": group_id,
        "group_revision": f"rev-{group_id}",
        "entity_family": "person",
        "group_status": "quarantined",
        "review_lane": "advanced",
        "confidence_band": "low",
        "confidence_score": 20,
        "advanced_review_required": True,
        "execution_blockers": ["person_lane_blocked"],
        "allowed_actions": ["decline", "quarantine"],
        "recommended_survivor_id": None,
        "selected_survivor_id": None,
        "members": [
            _member("P1", recommended=False, eligible=False),
            _member("P2", recommended=False, eligible=False),
        ],
        "conflicts": [{"field": "email"}],
        "evidence": [],
    }


def _window(*, groups: list[dict], start: int = 1, total: int | None = None) -> dict:
    total = total if total is not None else max(start + len(groups) - 1, len(groups))
    remaining = total - (start - 1)
    return {
        "review_contract": "easyimports.crm.duplicate_review_window.v1",
        "window_id": f"win-{start}",
        "window_digest": f"sha256:window-{start}",
        "expected_revision": 3,
        "group_start": start,
        "group_end": start + len(groups) - 1,
        "page_size": 5,
        "total_group_count": total,
        "decided_group_count": start - 1,
        "remaining_group_count": remaining,
        "groups": groups,
        "outcome": "next_window",
    }


def _complete() -> dict:
    return {
        "review_contract": "easyimports.crm.duplicate_review_complete.v1",
        "outcome": "review_complete",
        "total_group_count": 5,
        "decided_group_count": 5,
        "remaining_group_count": 0,
        "analyzed_record_count": 10,
        "no_duplicate_groups_found": False,
        "expected_revision": 4,
        "terminal_message": "Review complete.",
    }


def _accepted_submit_receipt(*, next_window: dict | None = None) -> dict:
    review_window = next_window if next_window is not None else _complete()
    return {
        "command_id": "cmd-review-1",
        "command_kind": "submit_duplicate_review_window",
        "outcome": "accepted",
        "resource": "run-4b",
        "result": {"review_window": review_window},
        "revision": 4,
        "run_id": "run-4b",
        "stage": "awaiting_review",
        "workflow_status": "needs_decision",
        "error_code": None,
        "message": None,
    }


class CrmDuplicateJourneyPhase4bHelperTests(SimpleTestCase):
    def test_default_choice_approve_for_merge_eligible(self):
        action, survivor = _default_group_choice(_merge_group("g1", "A1", "A2"))
        self.assertEqual(action, "approve")
        self.assertEqual(survivor, "A1")

    def test_default_choice_no_survivor_for_quarantine_only(self):
        action, survivor = _default_group_choice(_quarantine_group("q1"))
        self.assertEqual(action, "decline")
        self.assertIsNone(survivor)

    def test_review_cards_hide_survivor_radios_when_forbidden(self):
        window = _window(groups=[_quarantine_group("q1")])
        cards = _review_group_cards(window)
        self.assertEqual(len(cards), 1)
        self.assertFalse(cards[0]["show_survivor_radios"])
        self.assertIn("decline", cards[0]["allowed_actions"])
        self.assertNotIn("approve", cards[0]["allowed_actions"])

    def test_decisions_from_defaults_record_all_five(self):
        groups = [_merge_group(f"g{i}", f"L{i}", f"R{i}") for i in range(5)]
        cards = _review_group_cards(_window(groups=groups, total=6))
        decisions = _decisions_from_posted_cards(cards)
        self.assertEqual(len(decisions), 5)
        for decision, group in zip(decisions, groups, strict=True):
            self.assertEqual(decision["action"], "approve")
            self.assertEqual(
                decision["selected_survivor_id"], group["recommended_survivor_id"]
            )

    def test_override_survivor_promoted_when_different_member_selected(self):
        group = _merge_group("g1", "A1", "A2")
        cards = _review_group_cards(
            _window(groups=[group]),
            posted={"action_g1": "approve", "survivor_g1": "A2"},
        )
        decisions = _decisions_from_posted_cards(cards)
        self.assertEqual(decisions[0]["action"], "override_survivor")
        self.assertEqual(decisions[0]["selected_survivor_id"], "A2")

    def test_complete_payload_detection(self):
        self.assertTrue(_is_review_complete_payload(_complete()))
        self.assertFalse(
            _is_review_complete_payload(_window(groups=[_merge_group("g1", "A", "B")]))
        )

    def test_missing_posted_action_does_not_use_defaults(self):
        groups = [_merge_group("g1", "A1", "A2")]
        cards = _review_group_cards(_window(groups=groups), posted={})
        self.assertIsNone(cards[0]["action"])
        with self.assertRaises(ValueError):
            _decisions_from_posted_cards(cards)

    def test_decisions_from_posted_form_requires_explicit_action(self):
        posted = {
            "group_order": "g1",
            "group_revision_g1": "rev-g1",
            "allowed_g1": "approve,decline",
            "eligible_g1": "A1,A2",
            "recommended_g1": "A1",
            "advanced_g1": "0",
            # action_g1 intentionally omitted
            "survivor_g1": "A1",
        }
        with self.assertRaises(ValueError):
            _decisions_from_posted_form(posted)


class CrmDuplicateJourneyPhase4bPageTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-4b",
                "redesign_phase": "4a",
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        # Bind owner for request session like other importer tests.
        session = self.client.session
        session["easyimports_owner_id"] = str(self.owner)
        session.save()

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_review_page_renders_groups_and_records(self):
        groups = [_merge_group(f"g{i}", f"L{i}", f"R{i}") for i in range(5)]
        window = _window(groups=groups, total=6)
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Review duplicate groups 1–5 of 6", body)
        self.assertIn("Save these 5 and review next 5", body)
        self.assertEqual(body.count("Save these 5 and review next 5"), 2)
        self.assertIn("Show evidence and conflicts", body)
        self.assertIn("<details class=\"review-evidence-block\">", body)
        self.assertNotIn(">End early<", body)
        self.assertIn("Created date", body)
        self.assertIn("Name L0", body)
        self.assertIn("L0.example", body)
        self.assertIn('name="action_g0"', body)
        self.assertIn('name="survivor_g0"', body)
        self.assertIn('name="form_token"', body)
        self.assertNotIn("reference_acquisition", body)
        self.assertIn('class="review-group-card"', body)
        self.assertEqual(body.count('class="review-group-card"'), 5)
        css = (
            Path(__file__).resolve().parent
            / "static"
            / "importer"
            / "css"
            / "app.css"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r"\.review-group-card,\s*\n\.group-card\s*\{[^}]*background:\s*var\(--surface-2\)",
        )
        self.assertRegex(
            css,
            r"\.review-group-card,\s*\n\.group-card\s*\{[^}]*border:\s*2px solid var\(--line-strong\)",
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_quarantine_group_hides_survivor_radios(self):
        window = _window(groups=[_quarantine_group("q1")], total=1)
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        body = response.content.decode("utf-8")
        self.assertIn("Quarantine for manual resolution", body)
        self.assertIn("Do not merge this group", body)
        self.assertNotIn('name="survivor_q1"', body)
        self.assertIn("person_lane_blocked", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_final_window_uses_final_cta(self):
        window = _window(
            groups=[_merge_group("g6", "X1", "X2")],
            start=6,
            total=6,
        )
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        body = response.content.decode("utf-8")
        self.assertIn("Save final groups and review merge plan", body)
        self.assertIn("Review duplicate groups 6–6 of 6", body)
        # Final window still has decided groups from prior windows.
        self.assertIn("Save and exit", body)
        self.assertIn("End early", body)
        self.assertIn("name_match", body)  # evidence content, not only a count

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_end_early_hidden_when_only_auto_approved(self):
        window = _window(
            groups=[_merge_group("g6", "X1", "X2")],
            start=6,
            total=12,
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "auto_approved_group_count": 5,
        }
        self.session.save(update_fields=["options", "updated_at"])
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        self.assertNotContains(response, ">End early<")

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_evidence_and_conflicts_rendered_not_counts_only(self):
        group = _quarantine_group("q1")
        group["evidence"] = [{"signal": "name_overlap", "weight": "0.4"}]
        window = _window(groups=[group], total=1)
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        body = response.content.decode("utf-8")
        self.assertIn("Evidence", body)
        self.assertIn("Conflicts", body)
        self.assertIn("email", body)
        self.assertIn("name_overlap", body)
        self.assertNotIn("Evidence rows:", body)
        # First window (0 decided) has no Save and exit yet.
        self.assertNotIn(">Save and exit<", body)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_submit_defaults_redirects_to_next_window(self):
        groups = [_merge_group(f"g{i}", f"L{i}", f"R{i}") for i in range(5)]
        window = _window(groups=groups, total=6)
        next_window = _window(
            groups=[_merge_group("g5", "L5", "R5")],
            start=6,
            total=6,
        )
        receipt = _accepted_submit_receipt(next_window=next_window)

        def fake_dispatch(**kwargs):
            self.assertEqual(kwargs["mutation_kind"], "submit_duplicate_review_window")
            body = kwargs["body"]
            self.assertEqual(len(body["decisions"]), 5)
            self.assertEqual(body["window_id"], window["window_id"])
            return receipt

        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    get_resp = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
                    token = get_resp.context["form_token"]
                    post_data = {
                        "form_token": token,
                        "window_id": window["window_id"],
                        "window_digest": window["window_digest"],
                        "expected_revision": str(window["expected_revision"]),
                        "group_order": ",".join(g["group_id"] for g in groups),
                    }
                    for group in groups:
                        gid = group["group_id"]
                        post_data[f"action_{gid}"] = "approve"
                        post_data[f"survivor_{gid}"] = group["recommended_survivor_id"]
                        post_data[f"group_revision_{gid}"] = group["group_revision"]
                        post_data[f"allowed_{gid}"] = ",".join(
                            group["allowed_actions"]
                        )
                        post_data[f"recommended_{gid}"] = group[
                            "recommended_survivor_id"
                        ]
                        post_data[f"eligible_{gid}"] = ",".join(
                            m["record_id"] for m in group["members"]
                        )
                        post_data[f"advanced_{gid}"] = "0"
                    with patch(
                        "importer.journey_views._dispatch_json_mutation",
                        side_effect=fake_dispatch,
                    ):
                        post_resp = self.client.post(
                            reverse(
                                "importer:crm_duplicate_journey_review",
                                kwargs={"session_id": self.session.id},
                            ),
                            data=post_data,
                        )
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/crm-duplicates/review/", post_resp["Location"])

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_submit_final_redirects_to_workflow(self):
        window = _window(
            groups=[_merge_group("g6", "X1", "X2")],
            start=6,
            total=6,
        )
        receipt = _accepted_submit_receipt(next_window=_complete())
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    get_resp = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
                    token = get_resp.context["form_token"]
                    with patch(
                        "importer.journey_views._dispatch_json_mutation",
                        return_value=receipt,
                    ):
                        post_resp = self.client.post(
                            reverse(
                                "importer:crm_duplicate_journey_review",
                                kwargs={"session_id": self.session.id},
                            ),
                            data={
                                "form_token": token,
                                "window_id": window["window_id"],
                                "window_digest": window["window_digest"],
                                "expected_revision": str(
                                    window["expected_revision"]
                                ),
                                "group_order": "g6",
                                "action_g6": "approve",
                                "survivor_g6": "X1",
                                "group_revision_g6": "rev-g6",
                                "allowed_g6": "approve,override_survivor,decline,quarantine",
                                "recommended_g6": "X1",
                                "eligible_g6": "X1,X2",
                                "advanced_g6": "0",
                            },
                        )
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn(f"/sessions/{self.session.id}/", post_resp["Location"])
        self.assertNotIn("/crm-duplicates/review/", post_resp["Location"])

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_end_early_posts_without_window_decisions(self):
        groups = [_merge_group("g5", "L5", "R5")]
        window = _window(groups=groups, start=6, total=12)
        self.assertGreaterEqual(window["decided_group_count"], 5)
        receipt = {
            "command_id": "cmd-end-early",
            "command_kind": "submit_duplicate_review_end_early",
            "outcome": "accepted",
            "resource": "run-4b",
            "result": {"review_window": _complete()},
            "revision": 4,
            "run_id": "run-4b",
            "stage": "review_complete",
            "workflow_status": "succeeded",
            "error_code": None,
            "message": None,
        }

        def fake_dispatch(**kwargs):
            self.assertEqual(
                kwargs["mutation_kind"], "submit_duplicate_review_end_early"
            )
            self.assertEqual(
                kwargs["body"],
                {
                    "window_id": window["window_id"],
                    "window_digest": window["window_digest"],
                    "expected_revision": window["expected_revision"],
                },
            )
            return receipt

        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ):
                    get_resp = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": self.session.id},
                        )
                    )
                    self.assertContains(get_resp, "End early")
                    token = get_resp.context["form_token"]
                    with patch(
                        "importer.journey_views._dispatch_json_mutation",
                        side_effect=fake_dispatch,
                    ):
                        post_resp = self.client.post(
                            reverse(
                                "importer:crm_duplicate_journey_review",
                                kwargs={"session_id": self.session.id},
                            ),
                            data={
                                "form_token": token,
                                "window_id": window["window_id"],
                                "window_digest": window["window_digest"],
                                "expected_revision": str(
                                    window["expected_revision"]
                                ),
                                "group_order": "g5",
                                "review_submit": "end_early",
                            },
                        )
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/crm-duplicates/review/", post_resp["Location"])

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_progress_redirects_to_review_when_ready(self):
        projection = {
            "run_id": "run-4b",
            "status": "awaiting_review",
            "stage": "review_ready",
            "summary": {"duplicate_group_count": 6},
            "revision": 2,
            "workflow_key": "easyimports.duplicate_resolution",
            "workflow_version": 4,
            "target_provider_id": "fake",
            "decision": None,
            "effect_intent": None,
            "effect_grants": [],
        }
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "workflow",
                    return_value=projection,
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_progress",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/crm-duplicates/review/", response["Location"])


class CrmDuplicateJourneyPhase4bClientDispatchTests(TestCase):
    """Real client boundary tests (no _dispatch_json_mutation mock)."""

    def setUp(self):
        self.owner = uuid4()
        self.journal = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="crm.journey",
        )

    def _review_body(self) -> dict:
        return {
            "window_id": "win-1",
            "window_digest": "sha256:window-1",
            "expected_revision": 3,
            "decisions": [
                {
                    "group_id": "g1",
                    "group_revision": "rev-g1",
                    "action": "approve",
                    "selected_survivor_id": "A1",
                    "advanced_review": False,
                }
            ],
            "owner_session": f"django-{self.owner}",
        }

    def test_send_strips_owner_session_from_review_submit_json(self):
        client = EasyImportsApiClient()
        http = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.content = b"{}"
        http.post.return_value = response
        client.http = http
        mutation = MagicMock()
        mutation.id = uuid4()
        mutation.mutation_kind = "submit_duplicate_review_window"
        mutation.request_kind = "json"
        mutation.route = "/v1/workflows/run-4b/duplicate-review-window"
        mutation.idempotency_key = "idem-1"
        mutation.request_json = self._review_body()
        mutation.multipart_metadata = None
        mutation.session = MagicMock(owner_id=self.owner)
        client._send(mutation, file_path=None)
        kwargs = http.post.call_args.kwargs
        wire_body = kwargs["json"]
        self.assertNotIn("owner_session", wire_body)
        self.assertEqual(wire_body["window_id"], "win-1")
        self.assertEqual(
            kwargs["headers"].get("X-Owner-Session"), f"django-{self.owner}"
        )

    def test_classify_uses_review_submit_validator_not_generic_receipt(self):
        client = EasyImportsApiClient()
        receipt = _accepted_submit_receipt()
        # Generic command receipt cannot accept result.review_window.
        with self.assertRaises(ApiContractError):
            validate_command_receipt(receipt)
        validated = validate_duplicate_review_window_submit_result(receipt)
        mutation = MagicMock()
        mutation.mutation_kind = "submit_duplicate_review_window"
        out, state, code, message = client._classify_mutation_payload(
            mutation, receipt
        )
        self.assertEqual(state, ApiMutation.State.COMPLETED)
        self.assertEqual(out["outcome"], "accepted")
        self.assertIn("review_window", out["result"])
        self.assertEqual(code, "")
        self.assertEqual(message, "")
        self.assertEqual(validated["command_kind"], out["command_kind"])

    def test_dispatch_rejects_error_envelope_without_success_message_path(self):
        client = EasyImportsApiClient()
        body = self._review_body()
        mutation = create_or_reuse_mutation(
            session=self.journal,
            form_instance=uuid4(),
            mutation_kind="submit_duplicate_review_window",
            route="/v1/workflows/run-4b/duplicate-review-window",
            logical_action_identity="review-reject-test",
            request_json=body,
            resource_identity="run-4b",
        )
        response = MagicMock()
        response.status_code = 422
        response.content = b'{"error":{"code":"invalid_review_window","message":"bad"}}'
        response.json.return_value = {
            "error": {"code": "invalid_review_window", "message": "bad window"}
        }
        with patch.object(client, "assert_compatible", return_value=None):
            with patch.object(client, "_send", return_value=response):
                result = client.dispatch(mutation)
        self.assertEqual(result.mutation.state, ApiMutation.State.REJECTED)
        # View wrapper must raise — not treat the envelope as a save success.
        with self.assertRaises(ApiRejectedError):
            _dispatch_json_mutation(
                journal=self.journal,
                mutation_kind="submit_duplicate_review_window",
                route="/v1/workflows/run-4b/duplicate-review-window",
                body={
                    k: v for k, v in body.items() if k != "owner_session"
                },
                owner_session=f"django-{self.owner}",
                client=client,
                form_instance=mutation.form_instance,
                logical_action_identity="review-reject-test",
                resource_identity="run-4b",
            )

    def test_dispatch_accepts_review_receipt_and_exact_replay_is_frozen(self):
        client = EasyImportsApiClient()
        body = self._review_body()
        form_instance = uuid4()
        receipt = _accepted_submit_receipt(
            next_window=_window(
                groups=[_merge_group("g2", "B1", "B2")], start=2, total=2
            )
        )
        response = MagicMock()
        response.status_code = 200
        response.content = b"{}"
        response.json.return_value = receipt
        with patch.object(client, "assert_compatible", return_value=None):
            with patch.object(client, "_send", return_value=response) as send_mock:
                first = _dispatch_json_mutation(
                    journal=self.journal,
                    mutation_kind="submit_duplicate_review_window",
                    route="/v1/workflows/run-4b/duplicate-review-window",
                    body={
                        k: v for k, v in body.items() if k != "owner_session"
                    },
                    owner_session=f"django-{self.owner}",
                    client=client,
                    form_instance=form_instance,
                    logical_action_identity="review-accept-test",
                    resource_identity="run-4b",
                )
                # Exact replay: same form_instance converges without a second wire call.
                second = _dispatch_json_mutation(
                    journal=self.journal,
                    mutation_kind="submit_duplicate_review_window",
                    route="/v1/workflows/run-4b/duplicate-review-window",
                    body={
                        k: v for k, v in body.items() if k != "owner_session"
                    },
                    owner_session=f"django-{self.owner}",
                    client=client,
                    form_instance=form_instance,
                    logical_action_identity="review-accept-test",
                    resource_identity="run-4b",
                )
        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(first["outcome"], "accepted")
        self.assertEqual(second["outcome"], "accepted")
        self.assertEqual(
            first["result"]["review_window"]["window_id"],
            second["result"]["review_window"]["window_id"],
        )
        # Journal may retain owner_session; wire path must strip it.
        mutation = ApiMutation.objects.get(form_instance=form_instance)
        self.assertIn("owner_session", mutation.request_json or {})
        client2 = EasyImportsApiClient()
        http = MagicMock()
        http.post.return_value = MagicMock(status_code=200, content=b"{}")
        client2.http = http
        client2._send(mutation, file_path=None)
        self.assertNotIn("owner_session", http.post.call_args.kwargs["json"])

    def test_unknown_mutation_exact_retry_uses_explicit_retry(self):
        """Lost-response recovery: UNKNOWN + preserved form must re-dispatch."""

        client = EasyImportsApiClient()
        body = self._review_body()
        form_instance = uuid4()
        receipt = _accepted_submit_receipt()
        # First attempt: create mutation then leave it UNKNOWN (lost response).
        mutation = create_or_reuse_mutation(
            session=self.journal,
            form_instance=form_instance,
            mutation_kind="submit_duplicate_review_window",
            route="/v1/workflows/run-4b/duplicate-review-window",
            logical_action_identity="review-unknown-test",
            request_json=body,
            resource_identity="run-4b",
        )
        ApiMutation.objects.filter(pk=mutation.pk).update(
            state=ApiMutation.State.UNKNOWN,
            lease_token=None,
            lease_expires_at=None,
            error_message="lost response",
        )
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.UNKNOWN)

        # Without explicit_retry, dispatch must refuse UNKNOWN.
        with self.assertRaises(Exception) as refused:
            client.dispatch(mutation, explicit_retry=False)
        self.assertIn("uncertain", str(refused.exception).lower())

        response = MagicMock()
        response.status_code = 200
        response.content = b"{}"
        response.json.return_value = receipt
        with patch.object(client, "assert_compatible", return_value=None):
            with patch.object(client, "_send", return_value=response) as send_mock:
                recovered = _dispatch_json_mutation(
                    journal=self.journal,
                    mutation_kind="submit_duplicate_review_window",
                    route="/v1/workflows/run-4b/duplicate-review-window",
                    body={
                        k: v for k, v in body.items() if k != "owner_session"
                    },
                    owner_session=f"django-{self.owner}",
                    client=client,
                    form_instance=form_instance,
                    logical_action_identity="review-unknown-test",
                    resource_identity="run-4b",
                )
        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(recovered["outcome"], "accepted")
        mutation.refresh_from_db()
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
