"""CMX-1: clean-only CRM vocabulary intent on import setup (network-free)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from .forms import ProductSelectionForm
from .models import ImportSession
from .setup_service import save_setup_draft
from .vocabulary_intent import DEFAULT_VOCABULARY, VOCABULARY_UI_COPY
from .workflow_state import OWNER_SESSION_KEY, issue_form_token


def _targets(*ids: str) -> list[dict]:
    return [
        {"target_provider_id": target_id, "maximum_modes": {}} for target_id in ids
    ]


def _connected(
    connection_id: str = "conn-1",
    *,
    provider_key: str = "salesforce",
    target: str = "salesforce",
) -> list[dict]:
    return [
        {
            "connection_id": connection_id,
            "provider_key": provider_key,
            "provider_label": provider_key.title(),
            "display_label": "Connected",
            "status": "connected",
            "execution_target_provider_id": target,
        }
    ]


class Cmx1VocabularyFormTests(TestCase):
    def test_form_defaults_product_vocabulary_and_export_copy(self):
        form = ProductSelectionForm(
            targets=_targets("fake-preview-v1"),
            connections=[],
        )
        self.assertEqual(
            form.initial.get("vocabulary") or form.fields["vocabulary"].initial,
            DEFAULT_VOCABULARY,
        )
        self.assertIn(
            "does not change your download format",
            form.fields["vocabulary"].help_text,
        )
        self.assertEqual(form.fields["vocabulary"].help_text, VOCABULARY_UI_COPY)

    def test_clean_only_salesforce_vocabulary_no_connection(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "clean_only",
                "reference_source": "uploaded",
                "people_output": "",
                "vocabulary": "salesforce",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1"),
            connections=[],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["vocabulary"], "salesforce")
        self.assertEqual(form.cleaned_data["connection_id"], "")
        self.assertEqual(form.cleaned_data["reference_source"], "none")

    def test_match_without_connection_still_invalid(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "crm_matching",
                "reference_source": "connected_crm",
                "connection_id": "",
                "people_output": "",
                "vocabulary": "product",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1"),
            connections=[],
        )
        self.assertFalse(form.is_valid())
        self.assertIn("connection_id", form.errors)

    def test_match_provider_vocabulary_mismatch_fails_closed(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "crm_matching",
                "reference_source": "connected_crm",
                "connection_id": "conn-1",
                "people_output": "",
                "vocabulary": "hubspot",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1"),
            connections=_connected(provider_key="salesforce", target="salesforce"),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vocabulary", form.errors)

    def test_match_provider_vocabulary_agreement_ok(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "crm_matching",
                "reference_source": "connected_crm",
                "connection_id": "conn-1",
                "people_output": "",
                "vocabulary": "salesforce",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1"),
            connections=_connected(provider_key="salesforce", target="salesforce"),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["vocabulary"], "salesforce")
        self.assertEqual(form.cleaned_data["connection_id"], "conn-1")


class Cmx1VocabularyPersistTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(owner_id=self.owner)

    def test_save_setup_draft_persists_clean_only_salesforce_vocabulary(self):
        locked, notices = save_setup_draft(
            self.session,
            entity="accounts",
            operation="clean_only",
            reference_source="none",
            people_output="",
            target_provider_id="fake-preview-v1",
            operator_label="Op",
            expected_revision=0,
            connection_id="",
            vocabulary="salesforce",
        )
        self.assertEqual(notices, [])
        locked.refresh_from_db()
        self.assertEqual(locked.setup_operation, "clean_only")
        self.assertEqual(locked.setup_vocabulary, "salesforce")
        self.assertEqual(locked.setup_connection_id, "")
        self.assertEqual(locked.setup_revision, 1)

    def test_vocabulary_change_bumps_revision(self):
        save_setup_draft(
            self.session,
            entity="accounts",
            operation="clean_only",
            reference_source="none",
            people_output="",
            target_provider_id="fake-preview-v1",
            operator_label="Op",
            expected_revision=0,
            vocabulary="product",
        )
        self.session.refresh_from_db()
        rev = self.session.setup_revision
        save_setup_draft(
            self.session,
            entity="accounts",
            operation="clean_only",
            reference_source="none",
            people_output="",
            target_provider_id="fake-preview-v1",
            operator_label="Op",
            expected_revision=rev,
            vocabulary="hubspot",
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.setup_vocabulary, "hubspot")
        self.assertEqual(self.session.setup_revision, rev + 1)


class Cmx2VocabularyDestinationRoutingTests(TestCase):
    """CMX-2: vocabulary selects ingest catalog destination (network-free)."""

    def test_clean_only_sf_vocabulary_destination_is_ingest_catalog(self):
        from .column_mapping_views import _destination_for_session
        from .vocabulary_intent import resolve_catalog_id_for_setup_vocabulary

        self.assertEqual(
            resolve_catalog_id_for_setup_vocabulary(
                entity="people",
                operation="clean_only",
                people_output="contact",
                vocabulary="salesforce",
            ),
            "easyimports.ingest.salesforce.contact.v1",
        )
        owner = uuid4()
        session = ImportSession.objects.create(
            owner_id=owner,
            setup_entity="people",
            setup_operation="clean_only",
            setup_reference_source="none",
            setup_people_output="contact",
            setup_vocabulary="salesforce",
            setup_revision=1,
        )
        dest = _destination_for_session(
            session, owner_session=f"django-{owner}"
        )
        self.assertEqual(dest["destination_mode"], "catalog")
        self.assertEqual(
            dest["catalog_id"], "easyimports.ingest.salesforce.contact.v1"
        )

    def test_product_vocabulary_still_map_r1(self):
        from .vocabulary_intent import resolve_catalog_id_for_setup_vocabulary

        self.assertEqual(
            resolve_catalog_id_for_setup_vocabulary(
                entity="accounts",
                operation="clean_only",
                people_output=None,
                vocabulary="product",
            ),
            "easyimports.account_fields.v1",
        )


class Cmx1VocabularyProductViewTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(owner_id=self.owner)

    @patch("importer.workflow_views.EasyImportsApiClient")
    def test_product_post_persists_vocabulary_and_shows_export_copy(self, mock_api):
        mock_api.return_value.targets.return_value = {
            "targets": [{"target_provider_id": "fake-preview-v1", "maximum_modes": {}}]
        }
        mock_api.return_value.crm_connections.return_value = {"connections": []}

        url = reverse("importer:product", kwargs={"session_id": self.session.id})
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        body = get_resp.content.decode("utf-8")
        self.assertIn("Field names for mapping", body)
        self.assertIn("does not change your download format", body)
        self.assertIn('name="vocabulary"', body)

        token = issue_form_token(
            owner_id=self.owner,
            session=self.session,
            action_kind="select_product",
        )
        post = self.client.post(
            url,
            {
                "form_token": token,
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "clean_only",
                "reference_source": "uploaded",
                "connection_id": "",
                "people_output": "",
                "vocabulary": "salesforce",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
        )
        self.assertEqual(post.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.setup_vocabulary, "salesforce")
        self.assertEqual(self.session.setup_operation, "clean_only")
        self.assertEqual(self.session.setup_connection_id, "")
