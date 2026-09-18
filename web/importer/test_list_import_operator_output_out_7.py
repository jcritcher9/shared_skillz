"""OUT-7 Django surface — review tables use review-core plus extras.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from importer.models import ApiWorkflow, ImportSession
from importer.review_display import (
    DOWNLOAD_CORE_NOT_REVIEW_PEOPLE,
    REVIEW_CORE_ACCOUNT_ROLES,
    REVIEW_CORE_PEOPLE_ROLES,
    resolve_review_entity_family,
    select_existing_review_columns,
)
from importer.workflow_views import (
    GROUPED_SELECTION_MAX_COLUMNS,
    _decision_presentation,
    _grouped_display_columns,
    canonical_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = (
    REPO_ROOT
    / "mappings_2"
    / "codex_context"
    / "cross_agent_eval"
    / "project_implementations"
    / "list_import_operator_output_and_settings_clarity.md"
)
REVIEW_DISPLAY_PATH = Path(__file__).with_name("review_display.py")
WORKFLOW_VIEWS_PATH = Path(__file__).with_name("workflow_views.py")
WORKFLOW_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "importer" / "workflow.html"
)
APP_CSS = (
    Path(__file__).resolve().parent / "static" / "importer" / "css" / "app.css"
)

UNUSED_ACCOUNT_LIST_CANON = (
    "account_parent_id",
    "account_owner",
    "account_industry",
    "account_record_type",
    "campaign_id",
    "member_status",
)
INTERNAL_DUMP_COLUMNS = (
    "_canon_row_id",
    "contact_email_final_source_field",
    "account_id_provenance",
    "contact_id_provenance",
)


def _dataframe(columns, rows):
    return {"columns": columns, "rows": rows}


def _projection(**changes):
    value = {
        "run_id": "run-out7-review",
        "revision": 3,
        "workflow_key": "easyimports.list_import",
        "workflow_version": 7,
        "target_provider_id": "fake-preview-v1",
        "status": "needs_decision",
        "stage": "validate_people_list",
        "decision": None,
        "effect_intent": None,
        "effect_grants": [],
        "review_handoff": None,
        "terminal_evidence": None,
        "summary": {},
        "error": None,
        "links": {},
    }
    value.update(changes)
    return value


def _wide_people_row(*, option_id="option-1", email="bad-not-an-email"):
    row = {
        "option_id": option_id,
        "contact_first_name_final": "Ann",
        "contact_last_name_final": "Able",
        "contact_email_final": email,
        "account_name_final": "Acme",
        "contact_title": "Buyer",
        "contact_work_phone_final": "555-0100",
        "validation_message": "Primary email is missing or is not a valid email address.",
        "validation_severity": "blocking",
        "validation_status": "still_invalid",
        "contact_mobile": "555-0199",
        "contact_mailing_street": "1 Dump St",
        "_canon_row_id": "canon-row-zzz",
        "contact_email_final_source_field": "Email",
        "account_id_provenance": "provided_unverified",
        "contact_id_provenance": "provided_unverified",
        "account_parent_id": "001PARENT",
        "account_owner": "Owner Dump",
        "account_industry": "Dump Industry",
        "account_record_type": "Dump Type",
        "campaign_id": "701DUMP",
        "member_status": "Sent",
    }
    return row


def _validation_decision(*, wide=True):
    row = _wide_people_row() if wide else {
        "option_id": "option-1",
        "contact_email_final": "bad-not-an-email",
        "contact_first_name_final": "Ann",
        "contact_last_name_final": "Able",
        "account_name_final": "Acme",
        "validation_message": "Primary email is missing or is not a valid email address.",
    }
    return {
        "decision_id": "decision-out7-validation",
        "decision_type": "validation_failed",
        "phase_id": "validate_people_list",
        "body": {
            "decision_id": "decision-out7-validation",
            "decision_type": "validation_failed",
            "title": "Some rows need a valid primary email",
            "message": "Correct the highlighted primary emails.",
            "rows_df": _dataframe(list(row), [row]),
            "columns": [
                {
                    "name": key,
                    "label": key,
                    "visible": True,
                    "editable": key == "contact_email_final",
                }
                for key in row
            ],
            "actions": ("submit_edits", "exclude_remaining_invalid_rows"),
            "option_id_col": "option_id",
            "group_id_col": None,
            "status": "open",
            "resolved_group_ids": [],
            "audit_df": _dataframe(["event"], [{"event": "created"}]),
        },
    }


def _list_duplicates_decision(*, wide=True):
    base_keep = {
        "option_id": "option-keep-a",
        "group_id": "list_dupe_1",
        "option_kind": "uploaded_row",
        "option_label": "Keep this uploaded row",
        "selected": True,
        "is_recommended": True,
        "contact_first_name_final": "Ann",
        "contact_last_name_final": "Able",
        "contact_email_final": "same@example.com",
        "account_name_final": "Acme",
        "contact_title": "Buyer",
        "contact_work_phone_final": "555-0100",
        "list_duplicate_rules": "same_email",
        "list_duplicate_evidence_rules": "email",
    }
    if wide:
        base_keep.update(
            {
                "_canon_row_id": "canon-row-zzz",
                "account_id_provenance": "provided_unverified",
                "contact_email_final_source_field": "Email",
                "account_owner": "Owner Dump",
                "account_industry": "Dump Industry",
                "campaign_id": "701DUMP",
                "contact_mobile": "555-0199",
                "contact_mailing_street": "1 Dump St",
            }
        )
    exclude = {
        key: None if key not in {
            "option_id",
            "group_id",
            "option_kind",
            "option_label",
            "selected",
            "is_recommended",
        }
        else value
        for key, value in base_keep.items()
    }
    exclude.update(
        {
            "option_id": "option-exclude",
            "option_kind": "exclude_group",
            "option_label": "Exclude every row in this duplicate group",
            "selected": False,
            "is_recommended": False,
        }
    )
    keep_b = dict(base_keep)
    keep_b["option_id"] = "option-keep-b"
    keep_b["selected"] = False
    keep_b["is_recommended"] = False
    keep_b["contact_first_name_final"] = "Ann2"
    rows = [base_keep, keep_b, exclude]
    columns = list(base_keep)
    return {
        "decision_id": "decision-out7-list-dupes",
        "decision_type": "list_duplicates",
        "phase_id": "resolve_people_list_duplicates",
        "body": {
            "decision_id": "decision-out7-list-dupes",
            "decision_type": "list_duplicates",
            "title": "Choose one uploaded-list row per duplicate group",
            "message": "The recommended row starts selected.",
            "rows_df": _dataframe(columns, rows),
            "columns": [
                {
                    "name": key,
                    "label": key,
                    "visible": True,
                    "editable": False,
                }
                for key in columns
            ],
            "actions": ["submit_selections"],
            "option_id_col": "option_id",
            "group_id_col": "group_id",
            "status": "open",
            "resolved_group_ids": [],
            "audit_df": _dataframe(["event"], [{"event": "created"}]),
        },
    }


def _customer_html(content: bytes) -> bytes:
    return content.split(b'<details class="card run-details technical-details">')[0]


class Out7ReviewDisplayContractTests(SimpleTestCase):
    def test_django_mirror_matches_strategy_review_core(self) -> None:
        text = STRATEGY_PATH.read_text(encoding="utf-8")
        self.assertIn("REVIEW_CORE_PEOPLE_ROLES", text)
        self.assertIn("REVIEW_CORE_ACCOUNT_ROLES", text)
        for role in REVIEW_CORE_PEOPLE_ROLES:
            self.assertIn(role, text)
        for role in REVIEW_CORE_ACCOUNT_ROLES:
            self.assertIn(role, text)

    def test_review_display_does_not_import_mappings_2(self) -> None:
        source = REVIEW_DISPLAY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import mappings_2", source)
        self.assertNotIn("from mappings_2", source)
        views = WORKFLOW_VIEWS_PATH.read_text(encoding="utf-8")
        self.assertIn("select_existing_review_columns", views)
        self.assertIn("review-core", views)

    def test_select_people_validation_is_review_core_plus_reason(self) -> None:
        available = [
            *REVIEW_CORE_PEOPLE_ROLES,
            "contact_email_final",
            "validation_message",
            "account_industry",
            "_canon_row_id",
            "contact_email_final_source_field",
            *DOWNLOAD_CORE_NOT_REVIEW_PEOPLE,
        ]
        selected = select_existing_review_columns(
            decision_type="validation_failed",
            available=available,
            product_key="easyimports.list_import",
            setup_entity="people",
        )
        self.assertEqual(
            selected,
            [
                "contact_first_name",
                "contact_last_name",
                "contact_email",
                "account_name",
                "contact_title",
                "contact_work_phone",
                "validation_message",
            ],
        )
        self.assertNotIn("contact_email_final", selected)
        self.assertNotIn("account_industry", selected)
        self.assertNotIn("_canon_row_id", selected)
        self.assertNotIn("contact_mobile", selected)

    def test_select_does_not_backfill_or_cap_at_seven(self) -> None:
        available = [
            "contact_first_name_final",
            "contact_last_name_final",
            "contact_email_final",
            "account_name_final",
            "contact_title",
            "contact_work_phone_final",
            "list_duplicate_rules",
            "list_duplicate_evidence_rules",
            "account_industry",
            "account_owner",
            "campaign_id",
        ]
        selected = select_existing_review_columns(
            decision_type="list_duplicates",
            available=available,
            product_key="easyimports.list_import",
        )
        self.assertGreater(len(selected), GROUPED_SELECTION_MAX_COLUMNS)
        self.assertEqual(
            selected,
            [
                "contact_first_name_final",
                "contact_last_name_final",
                "contact_email_final",
                "account_name_final",
                "contact_title",
                "contact_work_phone_final",
                "list_duplicate_rules",
                "list_duplicate_evidence_rules",
            ],
        )

    def test_crm_match_extras_and_account_family(self) -> None:
        people_selected = select_existing_review_columns(
            decision_type="multiple_crm_matches",
            available=(
                "candidate_person_full_name",
                "candidate_person_email",
                "candidate_person_type",
                "match_score",
                "account_industry",
            ),
            product_key="easyimports.list_import",
        )
        self.assertEqual(
            people_selected,
            [
                "candidate_person_full_name",
                "candidate_person_email",
                "candidate_person_type",
                "match_score",
            ],
        )
        account_selected = select_existing_review_columns(
            decision_type="multiple_crm_account_matches",
            available=(
                "account_name_final",
                "account_domain_final",
                "account_website",
                "account_billing_city",
                "account_billing_state",
                "candidate_acct_name",
                "candidate_acct_domain",
                "match_score",
                "account_owner",
            ),
            product_key="easyimports.account_list_import",
        )
        self.assertEqual(
            account_selected,
            [
                "account_name_final",
                "account_domain_final",
                "account_website",
                "account_billing_city",
                "account_billing_state",
                "candidate_acct_name",
                "candidate_acct_domain",
                "match_score",
            ],
        )

    def test_entity_family_resolution(self) -> None:
        self.assertEqual(
            resolve_review_entity_family(setup_entity="accounts"),
            "accounts",
        )
        self.assertEqual(
            resolve_review_entity_family(product_key="easyimports.list_import"),
            "people",
        )
        self.assertEqual(
            resolve_review_entity_family(
                decision_type="multiple_crm_account_matches"
            ),
            "accounts",
        )

    def test_narrow_viewport_css_scrolls_review_tables(self) -> None:
        css = APP_CSS.read_text(encoding="utf-8")
        self.assertIn(".decision-review-scroll", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("width: max-content", css)
        template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("decision-review-scroll", template)
        self.assertIn("Decision frame columns", template)
        self.assertLess(
            template.index("decision-review-scroll"),
            template.index("Decision frame columns"),
        )

    def test_grouped_display_does_not_append_leftover_canon(self) -> None:
        columns = [
            "option_id",
            "group_id",
            "contact_first_name_final",
            "contact_last_name_final",
            "contact_email_final",
            "account_name_final",
            "contact_title",
            "contact_work_phone_final",
            "list_duplicate_rules",
            "account_industry",
            "account_owner",
            "campaign_id",
            "_canon_row_id",
        ]
        metadata = {name: {"name": name, "visible": True} for name in columns}
        grouped = _grouped_display_columns(
            decision_type="list_duplicates",
            columns=columns,
            column_metadata=metadata,
            option_column="option_id",
            group_column="group_id",
            product_key="easyimports.list_import",
            setup_entity="people",
        )
        names = [item["name"] for item in grouped]
        self.assertEqual(
            names,
            [
                "contact_first_name_final",
                "contact_last_name_final",
                "contact_email_final",
                "account_name_final",
                "contact_title",
                "contact_work_phone_final",
                "list_duplicate_rules",
            ],
        )


class Out7ReviewTableRenderTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def make_session_workflow(self, value, *, setup_entity="people"):
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=value["workflow_key"],
            target_provider_id="fake-preview-v1",
            operator_label="Alice Operator",
            setup_entity=setup_entity,
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

    def test_people_validation_shows_review_core_not_dump(self) -> None:
        decision = _validation_decision(wide=True)
        value = _projection(decision=decision)
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        customer = _customer_html(page.content)
        for expected in (
            b"Ann",
            b"Able",
            b"bad-not-an-email",
            b"Acme",
            b"Buyer",
            b"555-0100",
            b"Email edit",
            b"Primary email is missing",
        ):
            self.assertIn(expected, customer)
        for forbidden in (
            b"_canon_row_id",
            b"contact_email_final_source_field",
            b"account_id_provenance",
            b"account_parent_id",
            b"account_owner",
            b"account_industry",
            b"campaign_id",
            b"contact_mailing_street",
            b"1 Dump St",
            b"Dump Industry",
            b"Owner Dump",
        ):
            self.assertNotIn(forbidden, customer)
        self.assertEqual(
            list(page.context["decision_columns"]),
            [
                "contact_first_name_final",
                "contact_last_name_final",
                "contact_email_final",
                "account_name_final",
                "contact_title",
                "contact_work_phone_final",
                "validation_message",
            ],
        )
        self.assertIn("_canon_row_id", page.context["decision_source_columns"])
        self.assertContains(page, "_canon_row_id")
        self.assertContains(page, "Decision frame columns")

    def test_list_duplicates_shows_review_core_and_keep_exclude(self) -> None:
        decision = _list_duplicates_decision(wide=True)
        value = _projection(
            decision=decision,
            stage="resolve_people_list_duplicates",
        )
        session, workflow = self.make_session_workflow(value)
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        customer = _customer_html(page.content)
        self.assertIn(b"Keep this uploaded row", customer)
        self.assertIn(b"Exclude every row in this duplicate group", customer)
        self.assertIn(b"Ann", customer)
        self.assertIn(b"same@example.com", customer)
        self.assertIn(b"Same email", customer)
        for forbidden in (
            b"_canon_row_id",
            b"account_id_provenance",
            b"contact_email_final_source_field",
            b"account_industry",
            b"Owner Dump",
            b"701DUMP",
            b"1 Dump St",
        ):
            self.assertNotIn(forbidden, customer)
        group_names = [
            column["name"] for column in page.context["decision_group_columns"]
        ]
        self.assertEqual(
            group_names,
            [
                "contact_first_name_final",
                "contact_last_name_final",
                "contact_email_final",
                "account_name_final",
                "contact_title",
                "contact_work_phone_final",
                "list_duplicate_rules",
                "list_duplicate_evidence_rules",
            ],
        )
        self.assertIn("_canon_row_id", page.context["decision_source_columns"])

    def test_accounts_validation_uses_account_review_core(self) -> None:
        row = {
            "option_id": "option-1",
            "account_name_final": "Acme",
            "account_domain_final": "acme.test",
            "account_website": "https://acme.test",
            "account_billing_city": "Austin",
            "account_billing_state": "TX",
            "validation_message": "Website looks incomplete.",
            "account_industry": "Dump Industry",
            "account_owner": "Owner Dump",
            "_canon_row_id": "canon-row-zzz",
        }
        decision = {
            "decision_id": "decision-out7-account-validation",
            "decision_type": "validation_failed",
            "phase_id": "validate_account_list",
            "body": {
                "decision_id": "decision-out7-account-validation",
                "decision_type": "validation_failed",
                "title": "Some Account rows need attention",
                "message": "Review the highlighted fields.",
                "rows_df": _dataframe(list(row), [row]),
                "columns": [
                    {
                        "name": key,
                        "label": key,
                        "visible": True,
                        "editable": False,
                    }
                    for key in row
                ],
                "actions": ("submit_edits", "exclude_remaining_invalid_rows"),
                "option_id_col": "option_id",
                "group_id_col": None,
                "status": "open",
                "resolved_group_ids": [],
                "audit_df": _dataframe(["event"], [{"event": "created"}]),
            },
        }
        value = _projection(
            workflow_key="easyimports.account_list_import",
            stage="validate_account_list",
            decision=decision,
        )
        session, workflow = self.make_session_workflow(
            value, setup_entity="accounts"
        )
        with patch(
            "importer.workflow_views.refresh_workflow",
            return_value=workflow,
        ):
            page = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            list(page.context["decision_columns"]),
            [
                "account_name_final",
                "account_domain_final",
                "account_website",
                "account_billing_city",
                "account_billing_state",
                "validation_message",
            ],
        )
        customer = _customer_html(page.content)
        self.assertIn(b"Acme", customer)
        self.assertIn(b"Austin", customer)
        self.assertNotIn(b"Dump Industry", customer)
        self.assertNotIn(b"_canon_row_id", customer)

    def test_presentation_keeps_full_source_columns(self) -> None:
        decision = _validation_decision(wide=True)
        presentation = _decision_presentation(
            decision,
            product_key="easyimports.list_import",
            setup_entity="people",
        )
        self.assertIn("_canon_row_id", presentation["source_columns"])
        self.assertNotIn("_canon_row_id", presentation["columns"])
        for leftover in UNUSED_ACCOUNT_LIST_CANON:
            self.assertNotIn(leftover, presentation["columns"])
        for internal in INTERNAL_DUMP_COLUMNS:
            self.assertNotIn(internal, presentation["columns"])
