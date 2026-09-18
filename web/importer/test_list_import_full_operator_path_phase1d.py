"""Phase 1D — operator defaults + fail-closed mode UI (list-import full path).

Network-free Django form wiring only. Proves:
- product-scoped connected ceilings drive form choices (list_import up to execute;
  account_list_import Account provision stays disabled when API says so)
- write/provision track defaults remain disabled (not execute)
- execute is labeled Apply changes
- requested mode above product-scoped ceiling fails closed

No live CRM. Does not flip product-default (FE-CM-4 remains product-default).

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    list_import_full_operator_path.md (Phase 1D)
"""

from __future__ import annotations

from django.test import SimpleTestCase

from .constants import MODE_LABELS, OPT_IN_WRITE_TRACKS
from .forms import CatalogCompatibilityError, WorkflowConfigurationForm
from .setup_service import connected_import_target_projection


def _list_import_product() -> dict:
    return {
        "product_key": "easyimports.list_import",
        "tracks": {
            "reference_acquisition": ["disabled", "execute"],
            "account_provisioning": ["disabled", "preview", "dry_run", "execute"],
            "person_duplicate_resolution": [
                "disabled",
                "preview",
                "dry_run",
                "execute",
            ],
            "people_writes": ["disabled", "preview", "dry_run", "execute"],
            "campaign_member_writes": ["disabled", "preview", "dry_run", "execute"],
            "delivery": ["disabled", "preview", "execute"],
        },
    }


def _account_list_product() -> dict:
    return {
        "product_key": "easyimports.account_list_import",
        "tracks": {
            "reference_acquisition": ["disabled", "execute"],
            "account_provisioning": ["disabled", "preview", "dry_run", "execute"],
            "account_writes": ["disabled"],
            "delivery": ["disabled", "preview", "execute"],
        },
    }


def _sf_list_import_connection() -> dict:
    """Connection projection shape after Phase 1C (product-scoped by_product)."""

    list_modes = {
        "reference_acquisition": "execute",
        "account_provisioning": "execute",
        "people_writes": "execute",
        "person_duplicate_resolution": "execute",
        "campaign_member_writes": "execute",
        "delivery": "preview",
        "duplicate_execution": "execute",
    }
    account_list_modes = {
        **list_modes,
        "account_provisioning": "disabled",
    }
    return {
        "provider_key": "salesforce",
        "connection_id": "conn-sf-1d",
        "maximum_authorization": {
            # Flat = provider-global defaults (Account provision disabled).
            "reference_acquisition": "execute",
            "account_provisioning": "disabled",
            "people_writes": "execute",
            "person_duplicate_resolution": "execute",
            "campaign_member_writes": "execute",
            "delivery": "preview",
            "duplicate_execution": "execute",
            "by_product": {
                "easyimports.list_import": list_modes,
                "easyimports.account_list_import": account_list_modes,
            },
        },
    }


class Phase1DConnectedFormCeilingTests(SimpleTestCase):
    def test_list_import_form_offers_provision_and_people_write_up_to_execute(self):
        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.list_import",
            connection=_sf_list_import_connection(),
        )
        self.assertEqual(target["maximum_modes"]["account_provisioning"], "execute")
        self.assertEqual(target["maximum_modes"]["people_writes"], "execute")

        form = WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=target,
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-sf-1d",
        )
        ap = form.fields["mode__account_provisioning"]
        pw = form.fields["mode__people_writes"]
        ap_modes = [value for value, _label in ap.choices]
        pw_modes = [value for value, _label in pw.choices]
        self.assertEqual(ap.initial, "disabled")
        self.assertEqual(pw.initial, "disabled")
        self.assertIn("execute", ap_modes)
        self.assertIn("execute", pw_modes)
        self.assertIn("dry_run", ap_modes)
        # Apply changes label is explicit for execute.
        ap_labels = dict(ap.choices)
        self.assertEqual(ap_labels["execute"], MODE_LABELS["execute"])
        self.assertEqual(MODE_LABELS["execute"], "Apply changes")
        self.assertEqual(MODE_LABELS["disabled"], "Do not run")

    def test_account_list_form_does_not_offer_account_provision_execute(self):
        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.account_list_import",
            connection=_sf_list_import_connection(),
        )
        self.assertEqual(target["maximum_modes"]["account_provisioning"], "disabled")

        form = WorkflowConfigurationForm(
            product_entry=_account_list_product(),
            target=target,
            uploaded_roles={"raw_list", "accounts"},
            reference_acquisition_requirement="execute",
            connection_id="conn-sf-1d",
        )
        ap = form.fields["mode__account_provisioning"]
        ap_modes = [value for value, _label in ap.choices]
        self.assertEqual(ap.initial, "disabled")
        self.assertEqual(ap_modes, ["disabled"])
        self.assertNotIn("execute", ap_modes)
        self.assertNotIn("dry_run", ap_modes)

    def test_opt_in_write_tracks_default_disabled_when_ceiling_allows_execute(self):
        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.list_import",
            connection=_sf_list_import_connection(),
        )
        form = WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=target,
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
        )
        for track in OPT_IN_WRITE_TRACKS:
            field_name = f"mode__{track}"
            if field_name not in form.fields:
                continue
            field = form.fields[field_name]
            self.assertEqual(
                field.initial,
                "disabled",
                msg=f"{track} must default to disabled (opt-in)",
            )

    def test_mode_above_ceiling_fails_closed(self):
        # Ceiling disabled for account provisioning (account-list product path).
        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.account_list_import",
            connection=_sf_list_import_connection(),
        )
        form = WorkflowConfigurationForm(
            {
                "form_token": "tok",
                "mode__reference_acquisition": "execute",
                "mode__account_provisioning": "execute",
                "mode__account_writes": "disabled",
                "mode__delivery": "preview",
                "list_duplicate_policy": "surface",
                "crm_account_multi_match_policy": "review",
            },
            product_entry=_account_list_product(),
            target=target,
            uploaded_roles={"raw_list", "accounts"},
            reference_acquisition_requirement="execute",
        )
        self.assertFalse(form.is_valid())
        # ChoiceField rejects modes outside the allowed list (fail closed).
        self.assertTrue(form.errors)

    def test_list_import_valid_opt_in_to_apply_changes_when_ceiling_allows(self):
        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.list_import",
            connection=_sf_list_import_connection(),
        )
        form = WorkflowConfigurationForm(
            {
                "form_token": "tok",
                "mode__reference_acquisition": "execute",
                "mode__account_provisioning": "execute",
                "mode__person_duplicate_resolution": "disabled",
                "mode__people_writes": "disabled",
                "mode__campaign_member_writes": "disabled",
                "mode__delivery": "preview",
                "contacts_only": True,
                "list_duplicate_policy": "surface",
                "crm_match_duplicate_policy": "drop_repeats",
                "crm_account_multi_match_policy": "review",
                "batch_validation_policy": "quarantine",
            },
            product_entry=_list_import_product(),
            target=target,
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-sf-1d",
        )
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "upload-raw"},
            target_provider_id="sf-journey",
        )
        self.assertEqual(request["maximum_modes"]["account_provisioning"], "execute")
        self.assertEqual(request["maximum_modes"]["people_writes"], "disabled")

    def test_opt_in_track_without_disabled_fails_closed_on_catalog_drift(self):
        """Non-blocking Grade A- hardening: refuse missing disabled on opt-in tracks."""

        product = {
            "product_key": "easyimports.list_import",
            "tracks": {
                "reference_acquisition": ["disabled", "execute"],
                # Drift: no disabled under a high ceiling.
                "account_provisioning": ["preview", "dry_run", "execute"],
                "person_duplicate_resolution": ["disabled"],
                "people_writes": ["disabled"],
                "campaign_member_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        }
        target = {
            "target_provider_id": "sf-journey",
            "maximum_modes": {
                "reference_acquisition": "execute",
                "account_provisioning": "execute",
                "person_duplicate_resolution": "disabled",
                "people_writes": "disabled",
                "campaign_member_writes": "disabled",
                "delivery": "preview",
            },
        }
        with self.assertRaises(CatalogCompatibilityError) as raised:
            WorkflowConfigurationForm(
                product_entry=product,
                target=target,
                uploaded_roles={"raw_list"},
                reference_acquisition_requirement="execute",
            )
        self.assertIn("disabled", str(raised.exception).lower())

    def test_clean_rejects_mode_above_stored_ceiling_even_if_field_allows(self):
        """Defense-in-depth: clean re-checks product-scoped ceiling."""

        # Build form with a high ceiling so ChoiceField accepts execute, then
        # lower the stored product-scoped ceiling before validation.
        target = connected_import_target_projection(
            target_provider_id="sf-journey",
            product_key="easyimports.list_import",
            connection=_sf_list_import_connection(),
        )
        form = WorkflowConfigurationForm(
            {
                "form_token": "tok",
                "mode__reference_acquisition": "execute",
                "mode__account_provisioning": "execute",
                "mode__person_duplicate_resolution": "disabled",
                "mode__people_writes": "disabled",
                "mode__campaign_member_writes": "disabled",
                "mode__delivery": "preview",
                "contacts_only": True,
                "list_duplicate_policy": "surface",
                "crm_match_duplicate_policy": "drop_repeats",
                "crm_account_multi_match_policy": "review",
                "batch_validation_policy": "quarantine",
            },
            product_entry=_list_import_product(),
            target=target,
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-sf-1d",
        )
        form._target_maximums["account_provisioning"] = "disabled"
        self.assertFalse(form.is_valid())
        self.assertIn("ceiling", str(form.errors).lower())
