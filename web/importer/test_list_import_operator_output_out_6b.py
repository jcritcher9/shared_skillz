"""OUT-6B Django surface — thin two-column configure page.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from importer.configure_layout import configure_form_sections
from importer.forms import WorkflowConfigurationForm
from importer.test_list_import_operator_output_out_3 import (
    _account_list_product,
    _list_import_product,
    _single_dataset_product,
    _target,
)


def _people_form(**kwargs) -> WorkflowConfigurationForm:
    return WorkflowConfigurationForm(
        product_entry=_list_import_product(),
        target=_target("easyimports.list_import"),
        uploaded_roles={"raw_list"},
        reference_acquisition_requirement=kwargs.pop(
            "reference_acquisition_requirement", "disabled"
        ),
        **kwargs,
    )


def _render_configure(sections: dict, *, cm_ceiling: str = "execute") -> str:
    session = SimpleNamespace(
        id=uuid4(),
        product_key="",
        setup_entity="people",
        setup_operation="crm_matching",
        setup_reference_source="uploaded",
        setup_revision=1,
        setup_connection_id="",
        target_provider_id="fake-preview-v1",
        active_workflow_id=None,
    )
    request = RequestFactory().get("/sessions/x/configure/")
    context = {
        "session": session,
        "form": sections.get("_form"),
        "product_label": "People · match against CRM (uploaded)",
        "target_label": "Preview files only",
        "uploaded_rows": [],
        "mapping_gate": "confirmed",
        "mapping_required": True,
        "mapping_confirmed": True,
        "outstanding": None,
        "rejected": None,
        "awaiting_projection": False,
        "cm_enabled_for_product": True,
        "cm_connection_id": "",
        "cm_ceiling": cm_ceiling,
        "cm_search_q": "",
        "cm_search_result": None,
        "cm_search_error": "",
        "cm_status_result": None,
        "cm_resolution_rows": [],
        "cm_pick_token": "",
        "cm_clear_token": "",
        "setup_intent": None,
        "derived_product_key": "easyimports.list_import",
        "step": 3,
        **{key: value for key, value in sections.items() if key != "_form"},
    }
    return render_to_string(
        "importer/api_configure.html",
        context,
        request=request,
    )


class Out6bConfigureLayoutTests(SimpleTestCase):
    def test_people_match_groups_this_list_and_optional_writes(self) -> None:
        form = _people_form()
        sections = configure_form_sections(
            form,
            product_key="easyimports.list_import",
            reference_requirement="disabled",
            campaign_ceiling="execute",
        )
        left = [field.name for field in sections["this_list_fields"]]
        optional = [field.name for field in sections["optional_write_fields"]]
        self.assertIn("contacts_only", left)
        self.assertIn("match_speed", left)
        self.assertIn("list_duplicate_policy", left)
        self.assertIn("batch_validation_policy", left)
        self.assertIn("mode__account_provisioning", optional)
        self.assertIn("mode__people_writes", optional)
        self.assertIn("mode__person_duplicate_resolution", optional)
        self.assertEqual(
            sections["reference_in_optional"].name,
            "mode__reference_acquisition",
        )
        self.assertTrue(sections["show_optional_crm"])
        self.assertTrue(sections["show_campaign_block"])
        self.assertEqual(
            sections["campaign_track_field"].name,
            "mode__campaign_member_writes",
        )
        self.assertNotIn("mode__campaign_member_writes", left)
        self.assertNotIn("mode__campaign_member_writes", optional)
        self.assertNotIn("mode__delivery", left + optional)

    def test_required_reference_is_submitted_hidden(self) -> None:
        form = _people_form(reference_acquisition_requirement="execute")
        sections = configure_form_sections(
            form,
            product_key="easyimports.list_import",
            reference_requirement="execute",
            campaign_ceiling="disabled",
        )
        left = [field.name for field in sections["this_list_fields"]]
        self.assertNotIn("mode__reference_acquisition", left)
        self.assertIsNone(sections["reference_in_optional"])
        self.assertIsNone(sections["reference_on_left"])
        self.assertFalse(sections["show_campaign_block"])
        hidden = [field.name for field in sections["hidden_section_fields"]]
        self.assertIn("mode__reference_acquisition", hidden)
        self.assertIn("mode__campaign_member_writes", hidden)
        self.assertIn("mode__reference_acquisition", form.fields)

    def test_clean_only_hides_write_campaign_and_reference(self) -> None:
        form = WorkflowConfigurationForm(
            product_entry=_single_dataset_product(),
            target=_target("easyimports.single_dataset_import"),
            uploaded_roles={"dataset"},
            route_defaults={
                "target_object": "contact",
                "content_type": "people_and_accounts",
                "canon_profile": "new_list",
                "person_kind": "contact",
            },
        )
        sections = configure_form_sections(
            form,
            product_key="easyimports.single_dataset_import",
            reference_requirement="prohibited",
            campaign_ceiling="disabled",
        )
        visible = [field.name for field in sections["this_list_fields"]]
        visible.extend(field.name for field in sections["optional_write_fields"])
        self.assertFalse(sections["show_optional_crm"])
        self.assertFalse(sections["show_campaign_block"])
        self.assertNotIn("mode__account_provisioning", visible)
        self.assertNotIn("mode__people_writes", visible)
        self.assertNotIn("mode__person_duplicate_resolution", visible)
        self.assertNotIn("mode__campaign_member_writes", visible)
        hidden = [field.name for field in sections["hidden_section_fields"]]
        self.assertIn("mode__dataset_writes", hidden)
        self.assertIn("target_object", hidden)
        self.assertIn("content_type", hidden)
        self.assertIn("canon_profile", hidden)
        self.assertNotIn("target_object", visible)
        self.assertNotIn("content_type", visible)
        self.assertNotIn("canon_profile", visible)
        self.assertNotIn("person_kind", visible)

    def test_accounts_match_has_no_contacts_only_or_people_writes(self) -> None:
        form = WorkflowConfigurationForm(
            product_entry=_account_list_product(),
            target=_target("easyimports.account_list_import"),
            uploaded_roles={"raw_list", "accounts"},
            reference_acquisition_requirement="execute",
        )
        sections = configure_form_sections(
            form,
            product_key="easyimports.account_list_import",
            reference_requirement="execute",
            campaign_ceiling="disabled",
        )
        left = [field.name for field in sections["this_list_fields"]]
        optional = [field.name for field in sections["optional_write_fields"]]
        self.assertIn("list_duplicate_policy", left)
        self.assertNotIn("contacts_only", left)
        self.assertNotIn("mode__reference_acquisition", left)
        self.assertNotIn("mode__people_writes", optional)
        self.assertFalse(sections["show_campaign_block"])
        hidden = [field.name for field in sections["hidden_section_fields"]]
        self.assertIn("mode__reference_acquisition", hidden)

    def test_people_match_html_has_one_campaign_heading(self) -> None:
        form = _people_form()
        sections = configure_form_sections(
            form,
            product_key="easyimports.list_import",
            reference_requirement="disabled",
            campaign_ceiling="execute",
        )
        sections["_form"] = form
        html = _render_configure(sections, cm_ceiling="execute")
        self.assertEqual(html.count("<legend>Campaign membership</legend>"), 1)
        self.assertIn('class="config-form-grid"', html)
        self.assertIn("Optional CRM writes", html)
        self.assertIn("Create Contacts only", html)
        self.assertNotIn(
            "Choose whether downloadable result files should be created",
            html,
        )
        self.assertIn('id="config-optional-crm"', html)

    def test_clean_only_html_omits_write_and_campaign_copy(self) -> None:
        form = WorkflowConfigurationForm(
            product_entry=_single_dataset_product(),
            target=_target("easyimports.single_dataset_import"),
            uploaded_roles={"dataset"},
            route_defaults={
                "target_object": "account",
                "content_type": "accounts",
                "canon_profile": "accounts",
                "person_kind": None,
            },
        )
        sections = configure_form_sections(
            form,
            product_key="easyimports.single_dataset_import",
            reference_requirement="prohibited",
            campaign_ceiling="disabled",
        )
        sections["_form"] = form
        html = _render_configure(sections, cm_ceiling="disabled")
        self.assertNotIn("<legend>Campaign membership</legend>", html)
        self.assertNotIn("New Account creation", html)
        self.assertNotIn("Contact and Lead changes", html)
        self.assertNotIn("Person duplicate handling", html)
        self.assertNotIn("Optional CRM writes", html)
        self.assertNotIn("Dataset changes", html)
        self.assertNotIn("Prepare output for", html)
        self.assertNotIn("What does the file contain?", html)
        self.assertNotIn("How is the file organized?", html)
        self.assertNotIn("Type of people", html)

    def test_duplicate_resolution_keeps_entity_date_and_execution(self) -> None:
        product = {
            "product_key": "easyimports.duplicate_resolution",
            "tracks": {
                "reference_acquisition": ["disabled", "execute"],
                "duplicate_execution": ["disabled", "preview", "dry_run", "execute"],
                "delivery": ["disabled", "preview"],
            },
        }
        target = {
            "target_provider_id": "fake-preview-v1",
            "maximum_modes": {
                "reference_acquisition": "disabled",
                "duplicate_execution": "execute",
                "delivery": "preview",
            },
        }
        form = WorkflowConfigurationForm(
            product_entry=product,
            target=target,
            uploaded_roles={"canonical_records"},
        )
        sections = configure_form_sections(
            form,
            product_key="easyimports.duplicate_resolution",
            reference_requirement=None,
            campaign_ceiling="disabled",
        )
        left = [field.name for field in sections["this_list_fields"]]
        hidden = [field.name for field in sections["hidden_section_fields"]]
        self.assertIn("entity", left)
        self.assertIn("analysis_as_of_date", left)
        self.assertIn("mode__duplicate_execution", left)
        self.assertIn("mode__delivery", left)
        self.assertNotIn("entity", hidden)
        self.assertFalse(sections["show_optional_crm"])
        self.assertFalse(sections["show_campaign_block"])

        posted = WorkflowConfigurationForm(
            {
                "form_token": "tok",
                "entity": "account",
                "analysis_as_of_date": "2026-07-20",
                "mode__reference_acquisition": "disabled",
                "mode__duplicate_execution": "disabled",
                "mode__delivery": "preview",
            },
            product_entry=product,
            target=target,
            uploaded_roles={"canonical_records"},
        )
        self.assertTrue(posted.is_valid(), posted.errors)
        sections["_form"] = form
        html = _render_configure(sections, cm_ceiling="disabled")
        self.assertIn("Record type", html)
        self.assertIn("Review records as of", html)
        self.assertIn("Duplicate changes", html)

    def test_css_splits_form_on_desktop_and_stacks_narrow(self) -> None:
        css = (
            Path(__file__).resolve().parent
            / "static"
            / "importer"
            / "css"
            / "app.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".config-form-grid", css)
        self.assertIn(
            ".config-form-grid { display: grid; grid-template-columns: 1fr 1fr;",
            css,
        )
        self.assertIn(
            ".config-form-grid { grid-template-columns: 1fr; }",
            css,
        )
        self.assertIn("@media (max-width: 760px)", css)
