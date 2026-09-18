"""DRC-2: comparison columns + winner sentence (network-free)."""

from __future__ import annotations

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .crm_duplicate_review_display import (
    CONTACT_OVER_LEAD,
    CONTACT_WITH_ACCOUNT,
    EMPTY_CELL,
    HIGHEST_RANKED,
    MORE_COMPLETE,
    MORE_RECENT,
    MORE_RECENT_AND_MORE_COMPLETE,
    NO_RECOMMENDATION,
    NOT_MERGEABLE,
    NOT_MERGEABLE_PERSON_CONFLICT,
    ONLY_CUSTOMER,
    review_field_columns,
    winner_reason,
)
from .journey_views import _review_group_cards


def _member(
    record_id: str,
    *,
    recommended: bool = False,
    eligible: bool = True,
    display_fields: dict | None = None,
    ranking_evidence: dict | None = None,
) -> dict:
    return {
        "record_id": record_id,
        "display_fields": display_fields
        if display_fields is not None
        else {"name": f"Name {record_id}"},
        "recommended": recommended,
        "selected": recommended,
        "survivor_eligible": eligible,
        "ranking_evidence": ranking_evidence
        if ranking_evidence is not None
        else {"survivor_eligible": eligible, "rank": 1 if recommended else 2},
    }


def _account_group(*, members: list[dict], blockers: list[str] | None = None) -> dict:
    recommended = next((m["record_id"] for m in members if m.get("recommended")), None)
    return {
        "group_id": "g1",
        "group_revision": "rev-g1",
        "entity_family": "company",
        "group_status": "ready",
        "review_lane": "standard",
        "confidence_band": "medium",
        "confidence_score": 70,
        "advanced_review_required": False,
        "execution_blockers": list(blockers or []),
        "allowed_actions": ["approve", "override_survivor", "decline", "quarantine"],
        "recommended_survivor_id": recommended,
        "selected_survivor_id": recommended,
        "members": members,
        "conflicts": [],
        "evidence": [],
    }


def _person_group(*, members: list[dict], blockers: list[str] | None = None) -> dict:
    group = _account_group(members=members, blockers=blockers)
    group["entity_family"] = "person"
    return group


class DisplayColumnTests(SimpleTestCase):
    def test_name_only_account_shows_locked_labels_and_em_dash(self):
        group = _account_group(
            members=[
                _member("A1", recommended=True, display_fields={"name": "Acme"}),
                _member("A2", display_fields={"name": "Acme Co"}),
            ]
        )
        cards = _review_group_cards({"groups": [group]})
        labels = [col["operator_label"] for col in cards[0]["field_columns"]]
        keys = [col["key"] for col in cards[0]["field_columns"]]
        self.assertEqual(
            labels,
            [
                "Name",
                "Created date",
                "Domain",
                "Website",
                "Location",
                "Type",
                "Owner",
                "Last activity",
            ],
        )
        self.assertNotIn("account_type", labels)
        self.assertNotIn("record_id", keys)
        html = render_to_string(
            "importer/crm_duplicate_journey_review.html",
            {
                "heading": "Review",
                "decided_group_count": 0,
                "remaining_group_count": 1,
                "auto_approved_group_count": 0,
                "form_errors": [],
                "form_token": "token",
                "window": {"window_id": "w", "window_digest": "d", "expected_revision": 1},
                "group_order": "g1",
                "cards": cards,
                "cta_label": "Save",
                "show_save_and_exit": False,
                "run_id": "run-1",
                "start_url": "/",
                "progress_url": "/",
                "workflow_url": "/",
            },
        )
        self.assertIn(">Domain<", html)
        self.assertIn(">Website<", html)
        self.assertIn(">Location<", html)
        self.assertIn(">Record ID<", html)
        self.assertIn(">Notes<", html)
        self.assertIn(">Survivor<", html)
        self.assertNotIn(">account_type<", html)
        self.assertIn(EMPTY_CELL, cards[0]["members"][0]["cells"])
        self.assertIn(">Created date<", html)
        name_at = html.index(">Name<")
        created_at = html.index(">Created date<")
        domain_at = html.index(">Domain<")
        self.assertLess(name_at, created_at)
        self.assertLess(created_at, domain_at)
        self.assertIn(HIGHEST_RANKED, html)

    def test_created_date_prefers_display_fields_then_ranking_evidence(self):
        from importer.crm_duplicate_review_display import review_cell_value

        column = {"key": "created_date", "operator_label": "Created date"}
        self.assertEqual(
            review_cell_value(
                {
                    "display_fields": {"created_date": "2021-03-04"},
                    "ranking_evidence": {"created_date_raw": "2019-01-01"},
                },
                column,
            ),
            "2021-03-04",
        )
        self.assertEqual(
            review_cell_value(
                {
                    "display_fields": {},
                    "ranking_evidence": {"created_date_raw": "2019-01-01"},
                },
                column,
            ),
            "2019-01-01",
        )
        self.assertEqual(
            review_cell_value(
                {
                    "display_fields": {},
                    "ranking_evidence": {
                        "created_at": "2018-05-06T12:30:00.000+0000"
                    },
                },
                column,
            ),
            "2018-05-06",
        )
        self.assertEqual(
            review_cell_value(
                {"display_fields": {"CreatedDate": "2017-11-02T00:00:00Z"}},
                column,
            ),
            "2017-11-02",
        )
        self.assertEqual(review_cell_value({"display_fields": {}}, column), EMPTY_CELL)

    def test_person_columns_use_operator_labels(self):
        group = _person_group(
            members=[
                _member(
                    "P1",
                    recommended=True,
                    display_fields={"name": "Pat", "email": "pat@x.com"},
                ),
                _member("P2", display_fields={"name": "Pat Q"}),
            ]
        )
        columns = review_field_columns(group)
        labels = [col["operator_label"] for col in columns]
        keys = [col["key"] for col in columns]
        self.assertEqual(
            labels,
            ["Name", "Created date", "Email", "Account", "Title", "Type", "Owner"],
        )
        self.assertNotIn("company_account", labels)
        self.assertNotIn("record_id", keys)
        cards = _review_group_cards({"groups": [group]})
        html = render_to_string(
            "importer/crm_duplicate_journey_review.html",
            {
                "heading": "Review",
                "decided_group_count": 0,
                "remaining_group_count": 1,
                "auto_approved_group_count": 0,
                "form_errors": [],
                "form_token": "token",
                "window": {"window_id": "w", "window_digest": "d", "expected_revision": 1},
                "group_order": "g1",
                "cards": cards,
                "cta_label": "Save",
                "show_save_and_exit": False,
                "run_id": "run-1",
                "start_url": "/",
                "progress_url": "/",
                "workflow_url": "/",
            },
        )
        self.assertIn(">Email<", html)
        self.assertIn(">Account<", html)
        self.assertIn(">Title<", html)
        self.assertNotIn(">company_account<", html)

    def test_opportunity_columns_appear_when_ranking_key_present(self):
        group = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={
                        "survivor_eligible": True,
                        "open_opportunity_count": 2,
                        "closed_won_opportunity_count": 1,
                    },
                ),
                _member("A2", ranking_evidence={"survivor_eligible": True}),
            ]
        )
        labels = [col["operator_label"] for col in review_field_columns(group)]
        self.assertIn("Open opportunities", labels)
        self.assertIn("Won opportunities", labels)


class WinnerReasonTests(SimpleTestCase):
    def test_person_type_conflict_uses_sentence_one(self):
        group = _person_group(
            members=[_member("P1", recommended=True), _member("P2")],
            blockers=["person_type_conflict"],
        )
        self.assertEqual(
            winner_reason(group),
            ("not_mergeable_person_conflict", NOT_MERGEABLE_PERSON_CONFLICT),
        )

    def test_other_blocker_uses_sentence_two(self):
        group = _account_group(
            members=[_member("A1", recommended=True), _member("A2")],
            blockers=["hierarchy_veto"],
        )
        self.assertEqual(winner_reason(group), ("not_mergeable", NOT_MERGEABLE))

    def test_only_customer_does_not_fire_when_two_customers(self):
        group = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={"is_customer": True, "survivor_eligible": True},
                ),
                _member(
                    "A2",
                    ranking_evidence={"is_customer": True, "survivor_eligible": True},
                ),
            ]
        )
        reason_id, sentence = winner_reason(group)
        self.assertNotEqual(reason_id, "only_customer")
        self.assertNotEqual(sentence, ONLY_CUSTOMER)

    def test_only_customer_fires_for_single_customer(self):
        group = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={"is_customer": True, "survivor_eligible": True},
                ),
                _member(
                    "A2",
                    ranking_evidence={"is_customer": False, "survivor_eligible": True},
                ),
            ]
        )
        self.assertEqual(winner_reason(group), ("only_customer", ONLY_CUSTOMER))

    def test_people_never_emit_customer_copy(self):
        group = _person_group(
            members=[
                _member(
                    "P1",
                    recommended=True,
                    ranking_evidence={"is_customer": True, "survivor_eligible": True},
                ),
                _member("P2"),
            ]
        )
        reason_id, sentence = winner_reason(group)
        self.assertNotEqual(reason_id, "only_customer")
        self.assertNotEqual(sentence, ONLY_CUSTOMER)

    def test_accounts_never_emit_contact_copy(self):
        group = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={
                        "person_type": "Contact",
                        "has_account": True,
                        "contact_priority_applied": True,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "A2",
                    ranking_evidence={"person_type": "Lead", "survivor_eligible": True},
                ),
            ]
        )
        reason_id, sentence = winner_reason(group)
        self.assertNotIn(reason_id, {"contact_with_account", "contact_over_lead"})
        self.assertNotEqual(sentence, CONTACT_WITH_ACCOUNT)
        self.assertNotEqual(sentence, CONTACT_OVER_LEAD)

    def test_contact_with_account_and_contact_over_lead(self):
        with_account = _person_group(
            members=[
                _member(
                    "P1",
                    recommended=True,
                    ranking_evidence={
                        "person_type": "Contact",
                        "has_account": True,
                        "contact_priority_applied": True,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "P2",
                    ranking_evidence={"person_type": "Lead", "survivor_eligible": True},
                ),
            ]
        )
        self.assertEqual(
            winner_reason(with_account),
            ("contact_with_account", CONTACT_WITH_ACCOUNT),
        )
        over_lead = _person_group(
            members=[
                _member(
                    "P1",
                    recommended=True,
                    ranking_evidence={
                        "person_type": "Contact",
                        "has_account": False,
                        "contact_priority_applied": True,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "P2",
                    ranking_evidence={"person_type": "Lead", "survivor_eligible": True},
                ),
            ]
        )
        self.assertEqual(
            winner_reason(over_lead),
            ("contact_over_lead", CONTACT_OVER_LEAD),
        )

    def test_more_recent_and_more_complete_requires_both(self):
        only_recent = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={
                        "last_activity_age_days": 3,
                        "completeness_score": 40,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "A2",
                    ranking_evidence={
                        "last_activity_age_days": 30,
                        "completeness_score": 80,
                        "survivor_eligible": True,
                    },
                ),
            ]
        )
        self.assertEqual(winner_reason(only_recent), ("more_recent", MORE_RECENT))
        only_complete = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={
                        "last_activity_age_days": 30,
                        "completeness_score": 80,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "A2",
                    ranking_evidence={
                        "last_activity_age_days": 3,
                        "completeness_score": 40,
                        "survivor_eligible": True,
                    },
                ),
            ]
        )
        self.assertEqual(winner_reason(only_complete), ("more_complete", MORE_COMPLETE))
        both = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={
                        "last_activity_age_days": 3,
                        "completeness_score": 80,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "A2",
                    ranking_evidence={
                        "last_activity_age_days": 30,
                        "completeness_score": 40,
                        "survivor_eligible": True,
                    },
                ),
            ]
        )
        self.assertEqual(
            winner_reason(both),
            ("more_recent_and_more_complete", MORE_RECENT_AND_MORE_COMPLETE),
        )

    def test_last_activity_tie_does_not_claim_more_recent(self):
        group = _account_group(
            members=[
                _member(
                    "A1",
                    recommended=True,
                    ranking_evidence={
                        "last_activity_age_days": 10,
                        "completeness_score": 40,
                        "survivor_eligible": True,
                    },
                ),
                _member(
                    "A2",
                    ranking_evidence={
                        "last_activity_age_days": 10,
                        "completeness_score": 40,
                        "survivor_eligible": True,
                    },
                ),
            ]
        )
        reason_id, sentence = winner_reason(group)
        self.assertNotEqual(reason_id, "more_recent")
        self.assertNotEqual(reason_id, "more_recent_and_more_complete")
        self.assertNotIn("more recent", sentence)

    def test_no_recommendation(self):
        group = _account_group(
            members=[
                _member("A1", recommended=False),
                _member("A2", recommended=False),
            ]
        )
        group["recommended_survivor_id"] = None
        self.assertEqual(winner_reason(group), ("no_recommendation", NO_RECOMMENDATION))

    def test_cards_carry_exactly_one_winner_sentence(self):
        cards = _review_group_cards(
            {
                "groups": [
                    _account_group(
                        members=[_member("A1", recommended=True), _member("A2")]
                    )
                ]
            }
        )
        self.assertEqual(cards[0]["winner_sentence"], HIGHEST_RANKED)
        self.assertEqual(cards[0]["winner_reason_id"], "highest_ranked")
