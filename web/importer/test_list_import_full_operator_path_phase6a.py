"""Phase 6A — configure FE option for the public person-write update policy.

Network-free Django form/card wiring only. Django does not import mappings_2.

Proves:
- list-import configure form exposes ``update_existing_contact_information``
  (default unchecked = blank-fill) when people writes are available;
- the control is dropped when people writes are unavailable (ceiling disabled),
  and never appears on clean-only / account-list forms;
- OUT-6B ``configure_form_sections`` places the control in the collapsed optional
  CRM-write card directly beside ``mode__people_writes`` (not on the left "this
  list" column), and hides it for clean-only runs;
- the create body threads the flag (True when checked, False default);
- the generated Django client exposes the field and reports API 1.61.0;
- the touched web modules import no ``mappings_2``.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/
    list_import_full_operator_path/list_import_full_operator_path.md (Slice 6A, D2=A)
"""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from importer.configure_layout import (
    OPTIONAL_WRITE_FIELD_NAMES,
    configure_form_sections,
)
from importer.forms import WorkflowConfigurationForm
from importer.test_list_import_operator_output_out_3 import (
    _list_import_product,
    _single_dataset_product,
    _target,
)

FIELD = "update_existing_contact_information"
_WEB = Path(__file__).resolve().parent


def _people_writes_disabled_product() -> dict:
    product = _list_import_product()
    product["tracks"]["people_writes"] = ["disabled"]
    return product


def _people_writes_disabled_target() -> dict:
    target = _target("easyimports.list_import")
    target["maximum_modes"] = {**target["maximum_modes"], "people_writes": "disabled"}
    return target


class Phase6AConfigureOptionTests(SimpleTestCase):
    def test_field_present_and_defaults_blank_fill_when_people_writes_available(self):
        form = WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=_target("easyimports.list_import"),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
        )
        self.assertIn(FIELD, form.fields)
        # Unchecked default = blank-fill.
        self.assertFalse(bool(form.fields[FIELD].initial))
        self.assertFalse(form.fields[FIELD].required)

    def test_field_dropped_when_people_writes_unavailable(self):
        form = WorkflowConfigurationForm(
            product_entry=_people_writes_disabled_product(),
            target=_people_writes_disabled_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
        )
        # people_writes field still exists (disabled-only) but the update policy
        # control is hidden entirely because writes cannot be opted into.
        self.assertIn("mode__people_writes", form.fields)
        self.assertNotIn(FIELD, form.fields)

    def test_field_absent_on_account_list_and_clean_only(self):
        clean = WorkflowConfigurationForm(
            product_entry=_single_dataset_product(),
            target=_target("easyimports.single_dataset_import"),
            uploaded_roles={"dataset"},
        )
        self.assertNotIn(FIELD, clean.fields)


class Phase6ALayoutPlacementTests(SimpleTestCase):
    def test_control_registered_beside_people_writes(self):
        # Registry order: the update-policy control sits immediately after the
        # people-writes mode in the optional CRM-write card.
        names = list(OPTIONAL_WRITE_FIELD_NAMES)
        self.assertIn(FIELD, names)
        self.assertEqual(
            names[names.index("mode__people_writes") + 1],
            FIELD,
        )

    def test_control_in_optional_card_not_this_list(self):
        form = WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=_target("easyimports.list_import"),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
        )
        sections = configure_form_sections(
            form,
            product_key="easyimports.list_import",
            reference_requirement="execute",
            campaign_ceiling="execute",
        )
        optional = [field.name for field in sections["optional_write_fields"]]
        left = [field.name for field in sections["this_list_fields"]]
        self.assertIn(FIELD, optional)
        self.assertNotIn(FIELD, left)
        # Directly beside people writes in the rendered card.
        self.assertEqual(
            optional[optional.index("mode__people_writes") + 1],
            FIELD,
        )

    def test_clean_only_hides_control(self):
        form = WorkflowConfigurationForm(
            product_entry=_single_dataset_product(),
            target=_target("easyimports.single_dataset_import"),
            uploaded_roles={"dataset"},
        )
        sections = configure_form_sections(
            form,
            product_key="easyimports.single_dataset_import",
            reference_requirement="disabled",
            campaign_ceiling="disabled",
        )
        optional = [field.name for field in sections["optional_write_fields"]]
        self.assertNotIn(FIELD, optional)
        self.assertTrue(sections["is_clean_only"])


class Phase6ABuilderTests(SimpleTestCase):
    def _bound(self, *, update_flag: bool | None) -> WorkflowConfigurationForm:
        data = {
            "form_token": "tok",
            "mode__reference_acquisition": "execute",
            "mode__account_provisioning": "disabled",
            "mode__person_duplicate_resolution": "disabled",
            "mode__people_writes": "execute",
            "mode__campaign_member_writes": "disabled",
            "contacts_only": True,
            "list_duplicate_policy": "surface",
            "crm_match_duplicate_policy": "drop_repeats",
            "crm_account_multi_match_policy": "review",
            "batch_validation_policy": "quarantine",
        }
        if update_flag:
            data[FIELD] = "on"
        return WorkflowConfigurationForm(
            data,
            product_entry=_list_import_product(),
            target=_target("easyimports.list_import"),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
        )

    def test_create_body_threads_allowlisted_overwrite_when_checked(self):
        form = self._bound(update_flag=True)
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "upload-raw"},
            target_provider_id="fake-preview-v1",
        )
        self.assertIs(request[FIELD], True)

    def test_create_body_defaults_blank_fill_when_unchecked(self):
        form = self._bound(update_flag=None)
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "upload-raw"},
            target_provider_id="fake-preview-v1",
        )
        self.assertIs(request[FIELD], False)


class Phase6AGeneratedClientTests(SimpleTestCase):
    def test_generated_client_exposes_field_and_version(self):
        from importer.api_contract_generated import API_VERSION

        self.assertEqual(API_VERSION, "1.61.0")
        models = (_WEB / "api_models_generated.py").read_text(encoding="utf-8")
        self.assertIn(FIELD, models)


class Phase6ABoundaryTests(SimpleTestCase):
    def test_touched_web_modules_do_not_import_mappings_2(self):
        for name in (
            "forms.py",
            "configure_layout.py",
            "api_contract_generated.py",
            "api_models_generated.py",
        ):
            source = (_WEB / name).read_text(encoding="utf-8")
            self.assertNotIn("import mappings_2", source, msg=name)
            self.assertNotIn("from mappings_2", source, msg=name)
