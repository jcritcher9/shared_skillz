"""Phase 7A/7B: import setup defaults + CRM-data picklist + hide target (D6–D8)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from .forms import ProductSelectionForm
from .models import ImportSession
from .workflow_state import OWNER_SESSION_KEY, issue_form_token


def _targets(*ids: str) -> dict:
    return {
        "targets": [
            {"target_provider_id": target_id, "maximum_modes": {}} for target_id in ids
        ]
    }


def _connected(connection_id: str = "conn-1", *, target: str = "fake-crm-v1") -> dict:
    return {
        "connections": [
            {
                "connection_id": connection_id,
                "provider_key": "fake",
                "provider_label": "Practice CRM",
                "display_label": "Practice",
                "status": "connected",
                "execution_target_provider_id": target,
            }
        ]
    }


class CrmConnectionSetupUxPhase7ATests(TestCase):
    """Network-free unit/Django tests for desires #6–#7 and D6 hide."""

    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(self.owner)
        browser.save()

    def test_form_defaults_match_first_and_d8_reference_without_connections(self):
        form = ProductSelectionForm(
            targets=_targets("fake-preview-v1")["targets"],
            connections=[],
        )
        self.assertEqual(
            [value for value, _label in form.fields["operation"].choices],
            ["crm_matching", "clean_only"],
        )
        self.assertEqual(form.fields["operation"].initial, "crm_matching")
        self.assertEqual(
            form.initial.get("operation") or form.fields["operation"].initial,
            "crm_matching",
        )
        labels = dict(form.fields["operation"].choices)
        self.assertEqual(
            labels["clean_only"], "Clean and prepare my file (no matching)"
        )
        self.assertEqual(
            [value for value, _ in form.fields["reference_source"].choices],
            ["uploaded", "connected_crm"],
        )
        self.assertNotIn("", dict(form.fields["reference_source"].choices))
        self.assertEqual(form.initial.get("reference_source"), "uploaded")
        self.assertEqual(form.initial.get("target_provider_id"), "fake-preview-v1")
        self.assertEqual(
            form.fields["target_provider_id"].widget.input_type, "hidden"
        )

    def test_form_d8_defaults_connected_crm_when_connection_present(self):
        form = ProductSelectionForm(
            targets=_targets("fake-preview-v1")["targets"],
            connections=_connected()["connections"],
        )
        self.assertEqual(form.initial.get("reference_source"), "connected_crm")

    def test_clean_only_forces_reference_none_without_not_needed_choice(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "clean_only",
                # Matching default may still be posted while panel is hidden.
                "reference_source": "uploaded",
                "people_output": "",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1")["targets"],
            connections=[],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["reference_source"], "none")
        self.assertEqual(form.cleaned_data["connection_id"], "")
        self.assertEqual(form.cleaned_data["target_provider_id"], "fake-preview-v1")

    def test_matching_requires_real_reference_source(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "accounts",
                "operation": "crm_matching",
                "reference_source": "",
                "people_output": "",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1")["targets"],
            connections=[],
        )
        self.assertFalse(form.is_valid())
        self.assertIn("reference_source", form.errors)

    def test_matching_connected_overwrites_target_from_connection(self):
        form = ProductSelectionForm(
            {
                "form_token": "tok",
                "operator_label": "Op",
                "entity": "people",
                "operation": "crm_matching",
                "reference_source": "connected_crm",
                "connection_id": "conn-1",
                "people_output": "",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": 0,
            },
            targets=_targets("fake-preview-v1")["targets"],
            connections=_connected(target="exec-target-from-conn")["connections"],
        )
        # execution target is not a catalog choice; clean() still freezes it.
        # Expand choices so field validation accepts the catalog POST value only.
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["target_provider_id"], "exec-target-from-conn"
        )
        self.assertEqual(form.cleaned_data["connection_id"], "conn-1")

    def test_product_get_blank_setup_defaults_and_hides_target_label(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        with (
            patch(
                "importer.workflow_views.EasyImportsApiClient.targets",
                return_value=_targets("fake-preview-v1"),
            ),
            patch(
                "importer.workflow_views.EasyImportsApiClient.crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:product", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Match it against my CRM")
        self.assertContains(page, "Clean and prepare my file (no matching)")
        self.assertContains(page, "Where is the CRM data?")
        self.assertNotContains(page, "Not needed for clean-only")
        self.assertNotContains(page, "Where should the results go?")
        self.assertNotContains(page, "Preview files only")
        body = page.content.decode("utf-8")
        # Match is selected by default in the operation control.
        self.assertRegex(
            body,
            r'value="crm_matching"[^>]*selected|selected[^>]*value="crm_matching"',
        )
        # Hidden target still submitted.
        self.assertIn('name="target_provider_id"', body)
        self.assertIn('value="fake-preview-v1"', body)
        # CRM-data panel visible for default matching; uploaded default without conn.
        self.assertIn('data-setup-panel="reference_source"', body)
        ref_idx = body.find('data-setup-panel="reference_source"')
        snippet = body[ref_idx : ref_idx + 120]
        self.assertNotIn("hidden", snippet.split(">")[0])

    def test_product_get_connected_defaults_d8_connected_crm(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        with (
            patch(
                "importer.workflow_views.EasyImportsApiClient.targets",
                return_value=_targets("fake-preview-v1"),
            ),
            patch(
                "importer.workflow_views.EasyImportsApiClient.crm_connections",
                return_value=_connected(),
            ),
        ):
            page = self.client.get(reverse("importer:product", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'value="connected_crm"')
        body = page.content.decode("utf-8")
        self.assertRegex(
            body,
            r'value="connected_crm"[^>]*selected|selected[^>]*value="connected_crm"',
        )

    def test_product_post_clean_only_hides_reference_and_forces_none(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="select_product",
        )
        with (
            patch(
                "importer.workflow_views.EasyImportsApiClient.targets",
                return_value=_targets("fake-preview-v1"),
            ),
            patch(
                "importer.workflow_views.EasyImportsApiClient.crm_connections",
                return_value={"connections": []},
            ),
        ):
            submitted = self.client.post(
                reverse("importer:product", args=[session.id]),
                {
                    "form_token": token,
                    "operator_label": "Phase7A Operator",
                    "entity": "accounts",
                    "operation": "clean_only",
                    "reference_source": "uploaded",
                    "people_output": "",
                    "target_provider_id": "fake-preview-v1",
                    "setup_revision": 0,
                },
            )
        self.assertEqual(submitted.status_code, 302, submitted.content[:400])
        session.refresh_from_db()
        self.assertEqual(session.setup_operation, "clean_only")
        self.assertEqual(session.setup_reference_source, "none")
        self.assertEqual(session.setup_connection_id, "")
        self.assertEqual(session.target_provider_id, "fake-preview-v1")
        self.assertEqual(session.product_key, "")

    def test_product_post_matching_uploaded_defaults_ok(self):
        session = ImportSession.objects.create(owner_id=self.owner)
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="select_product",
        )
        with (
            patch(
                "importer.workflow_views.EasyImportsApiClient.targets",
                return_value=_targets("fake-preview-v1"),
            ),
            patch(
                "importer.workflow_views.EasyImportsApiClient.crm_connections",
                return_value={"connections": []},
            ),
        ):
            submitted = self.client.post(
                reverse("importer:product", args=[session.id]),
                {
                    "form_token": token,
                    "operator_label": "Phase7A Operator",
                    "entity": "people",
                    "operation": "crm_matching",
                    "reference_source": "uploaded",
                    "people_output": "",
                    "target_provider_id": "fake-preview-v1",
                    "setup_revision": 0,
                },
            )
        self.assertEqual(submitted.status_code, 302, submitted.content[:400])
        session.refresh_from_db()
        self.assertEqual(session.setup_operation, "crm_matching")
        self.assertEqual(session.setup_reference_source, "uploaded")
        self.assertEqual(session.setup_entity, "people")
        self.assertEqual(session.target_provider_id, "fake-preview-v1")

    def test_product_post_omitted_target_uses_catalog_default(self):
        """Hidden target may be omitted by odd clients; server defaults (D6)."""

        session = ImportSession.objects.create(owner_id=self.owner)
        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="select_product",
        )
        with (
            patch(
                "importer.workflow_views.EasyImportsApiClient.targets",
                return_value=_targets("fake-preview-v1"),
            ),
            patch(
                "importer.workflow_views.EasyImportsApiClient.crm_connections",
                return_value={"connections": []},
            ),
        ):
            submitted = self.client.post(
                reverse("importer:product", args=[session.id]),
                {
                    "form_token": token,
                    "operator_label": "Phase7A Operator",
                    "entity": "accounts",
                    "operation": "clean_only",
                    "setup_revision": 0,
                },
            )
        self.assertEqual(submitted.status_code, 302, submitted.content[:400])
        session.refresh_from_db()
        self.assertEqual(session.target_provider_id, "fake-preview-v1")
        self.assertEqual(session.setup_reference_source, "none")
