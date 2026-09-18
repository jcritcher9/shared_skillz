"""OUT-6A Django surface — Contacts-only help tells the truth about Leads.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from importer.forms import WorkflowConfigurationForm
from importer.vocabulary_intent import (
    CONTACTS_ONLY_HUBSPOT_HELP,
    CONTACTS_ONLY_LEAD_FALLBACK_HELP,
    contacts_only_help_text,
)


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


class Out6aContactsOnlyHelpTests(SimpleTestCase):
    def test_helper_locks_provider_sentences(self) -> None:
        self.assertEqual(
            contacts_only_help_text(vocabulary="salesforce"),
            CONTACTS_ONLY_LEAD_FALLBACK_HELP,
        )
        self.assertEqual(
            contacts_only_help_text(vocabulary="product"),
            CONTACTS_ONLY_LEAD_FALLBACK_HELP,
        )
        self.assertEqual(
            contacts_only_help_text(vocabulary="fake"),
            CONTACTS_ONLY_LEAD_FALLBACK_HELP,
        )
        self.assertEqual(
            contacts_only_help_text(vocabulary="hubspot"),
            CONTACTS_ONLY_HUBSPOT_HELP,
        )
        self.assertEqual(
            contacts_only_help_text(
                vocabulary="product", provider_key="hubspot"
            ),
            CONTACTS_ONLY_HUBSPOT_HELP,
        )
        self.assertIn("HubSpot has no Lead", CONTACTS_ONLY_HUBSPOT_HELP)
        self.assertIn("Lead instead", CONTACTS_ONLY_LEAD_FALLBACK_HELP)

    def _people_form(self, **kwargs) -> WorkflowConfigurationForm:
        return WorkflowConfigurationForm(
            product_entry=_list_import_product(),
            target=_target("easyimports.list_import"),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            **kwargs,
        )

    def test_salesforce_people_configure_uses_lead_fallback_sentence(self) -> None:
        form = self._people_form(vocabulary="salesforce")
        help_text = form.fields["contacts_only"].help_text
        self.assertEqual(help_text, CONTACTS_ONLY_LEAD_FALLBACK_HELP)
        html = form.as_p()
        self.assertIn("prepare that person as a Lead instead", html)
        self.assertIn('class="helptext"', html)
        self.assertNotIn("HubSpot has no Lead", html)

    def test_product_and_fake_people_use_lead_fallback_sentence(self) -> None:
        for vocabulary in ("product", "fake", ""):
            with self.subTest(vocabulary=vocabulary):
                form = self._people_form(vocabulary=vocabulary)
                self.assertEqual(
                    form.fields["contacts_only"].help_text,
                    CONTACTS_ONLY_LEAD_FALLBACK_HELP,
                )

    def test_hubspot_people_configure_does_not_promise_leads(self) -> None:
        form = self._people_form(vocabulary="hubspot")
        help_text = form.fields["contacts_only"].help_text
        self.assertEqual(help_text, CONTACTS_ONLY_HUBSPOT_HELP)
        html = form.as_p()
        self.assertIn("HubSpot has no Lead", html)
        self.assertIn("stay Contacts in the HubSpot Contact file", html)
        self.assertNotIn("prepare that person as a Lead instead", html)

    def test_hubspot_connection_wins_over_product_vocabulary(self) -> None:
        form = self._people_form(
            vocabulary="product",
            provider_key="hubspot",
        )
        self.assertEqual(
            form.fields["contacts_only"].help_text,
            CONTACTS_ONLY_HUBSPOT_HELP,
        )

    def test_accounts_configure_does_not_show_lead_sentence(self) -> None:
        form = WorkflowConfigurationForm(
            product_entry=_account_list_product(),
            target=_target("easyimports.account_list_import"),
            uploaded_roles={"raw_list", "accounts"},
            reference_acquisition_requirement="execute",
            vocabulary="salesforce",
        )
        self.assertNotIn("contacts_only", form.fields)
        html = form.as_p()
        self.assertNotIn(CONTACTS_ONLY_LEAD_FALLBACK_HELP, html)
        self.assertNotIn("prepare that person as a Lead instead", html)

    def test_clean_only_configure_does_not_show_lead_sentence(self) -> None:
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
            vocabulary="salesforce",
        )
        self.assertNotIn("contacts_only", form.fields)
        html = form.as_p()
        self.assertNotIn(CONTACTS_ONLY_LEAD_FALLBACK_HELP, html)
        self.assertNotIn("prepare that person as a Lead instead", html)
