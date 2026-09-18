"""OUT-3 Django surface — delivery is not an operator choice.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from django import forms
from django.test import SimpleTestCase

from importer.constants import ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS
from importer.forms import WorkflowConfigurationForm


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


def _single_dataset_product() -> dict:
    return {
        "product_key": "easyimports.single_dataset_import",
        "tracks": {
            "dataset_writes": ["disabled"],
            "delivery": ["disabled", "preview", "execute"],
        },
    }


def _target(product_key: str) -> dict:
    if product_key == "easyimports.list_import":
        modes = {
            "reference_acquisition": "execute",
            "account_provisioning": "execute",
            "person_duplicate_resolution": "execute",
            "people_writes": "execute",
            "campaign_member_writes": "execute",
            "delivery": "preview",
        }
    elif product_key == "easyimports.account_list_import":
        modes = {
            "reference_acquisition": "execute",
            "account_provisioning": "disabled",
            "account_writes": "disabled",
            "delivery": "preview",
        }
    else:
        modes = {
            "dataset_writes": "disabled",
            "delivery": "preview",
        }
    return {
        "target_provider_id": "fake-preview-v1",
        "maximum_modes": modes,
    }


class Out3ConfigureDeliveryHiddenTests(SimpleTestCase):
    def _form(self, product: dict, **kwargs) -> WorkflowConfigurationForm:
        roles = (
            {"dataset"}
            if product["product_key"] == "easyimports.single_dataset_import"
            else {"raw_list"}
        )
        extra = {}
        if product["product_key"] == "easyimports.list_import":
            extra["reference_acquisition_requirement"] = "execute"
        elif product["product_key"] == "easyimports.account_list_import":
            extra["reference_acquisition_requirement"] = "execute"
            extra["uploaded_roles"] = {"raw_list", "accounts"}
        return WorkflowConfigurationForm(
            product_entry=product,
            target=_target(product["product_key"]),
            uploaded_roles=kwargs.pop("uploaded_roles", extra.pop("uploaded_roles", roles)),
            **extra,
            **kwargs,
        )

    def test_hidden_products_are_the_list_import_family(self) -> None:
        self.assertEqual(
            ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS,
            {
                "easyimports.list_import",
                "easyimports.account_list_import",
                "easyimports.single_dataset_import",
            },
        )

    def test_configure_form_hides_delivery_and_defaults_disabled(self) -> None:
        cases = (
            _list_import_product(),
            _account_list_product(),
            _single_dataset_product(),
        )
        for product in cases:
            with self.subTest(product=product["product_key"]):
                form = self._form(product)
                field = form.fields["mode__delivery"]
                self.assertEqual(field.initial, "disabled")
                self.assertIsInstance(field.widget, forms.HiddenInput)
                visible = {item.name for item in form.visible_fields()}
                self.assertNotIn("mode__delivery", visible)
                html = form.as_p()
                self.assertNotIn(
                    "Choose whether downloadable result files should be created",
                    html,
                )
                self.assertNotIn("Output files", html)

    def test_list_import_request_defaults_delivery_disabled(self) -> None:
        form = WorkflowConfigurationForm(
            {
                "form_token": "tok",
                "mode__reference_acquisition": "execute",
                "mode__account_provisioning": "disabled",
                "mode__person_duplicate_resolution": "disabled",
                "mode__people_writes": "disabled",
                "mode__campaign_member_writes": "disabled",
                "contacts_only": True,
                "list_duplicate_policy": "surface",
                "crm_match_duplicate_policy": "drop_repeats",
                "crm_account_multi_match_policy": "review",
                "batch_validation_policy": "quarantine",
            },
            product_entry=_list_import_product(),
            target=_target("easyimports.list_import"),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
        )
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "upload-raw"},
            target_provider_id="fake-preview-v1",
        )
        self.assertEqual(request["maximum_modes"]["delivery"], "disabled")
        self.assertEqual(request["maximum_modes"]["people_writes"], "disabled")
