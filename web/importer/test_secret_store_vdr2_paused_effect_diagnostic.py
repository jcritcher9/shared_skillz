from __future__ import annotations

"""VDR-2 Django render: Technical Details consume only the typed diagnostic."""

from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .api_client import canonical_digest
from .models import ApiWorkflow, ImportSession
from .tests import projection

UNPROTECT_OK = (
    "Windows DPAPI unprotect failed "
    "(WinError 13 (0x0000000D): The data is invalid.)."
)
GENERIC = (
    "This step paused because of an unexpected error. "
    "Reconnect the CRM connection if credentials may be stale, or contact support."
)
PLANTED_URL = "https://evil.example/steal?token=SECRET"
PLANTED_PATH = r"C:\Users\vdr2\secret_vault\blob_x.bin"
PLANTED_BEARER = "Bearer eyJ"
PLANTED_HUBSPOT = "https://api.hubapi.com/crm/v3/objects/companies?hapikey=SECRET"


class Vdr2PausedEffectDiagnosticRenderTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()

    def test_allowlisted_unprotect_renders_in_technical_details(self):
        html = self._get_paused_html(
            {
                "code": "secret_store_dpapi_unprotect_failed",
                "error_type": "SecretStoreError",
                "message": UNPROTECT_OK,
                "win32_code": 13,
                "win32_code_hex": "0x0000000D",
            }
        )
        self.assertIn("secret_store_dpapi_unprotect_failed", html)
        self.assertIn("SecretStoreError", html)
        self.assertIn(UNPROTECT_OK, html)
        self.assertIn("0x0000000D", html)
        self.assertNotIn(PLANTED_URL, html)
        self.assertNotIn("prior_evidence", html)

    def test_fallback_hides_planted_payloads(self):
        html = self._get_paused_html(
            {
                "code": "paused_effect_unavailable",
                "error_type": "unavailable",
                "message": GENERIC,
                "win32_code": None,
                "win32_code_hex": None,
            }
        )
        self.assertIn("paused_effect_unavailable", html)
        self.assertIn(GENERIC, html)
        self.assertNotIn(PLANTED_URL, html)
        self.assertNotIn(PLANTED_PATH, html)
        self.assertNotIn(PLANTED_BEARER, html)
        self.assertNotIn(PLANTED_HUBSPOT, html)
        self.assertNotIn("hapikey", html)
        self.assertNotIn("secret_vault", html)

    def test_django_does_not_import_mappings_2_for_this_surface(self):
        from pathlib import Path

        views = Path(__file__).with_name("workflow_views.py").read_text(
            encoding="utf-8"
        )
        template = (
            Path(__file__).parents[0]
            / "templates"
            / "importer"
            / "workflow.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("paused_effect_diagnostic", views)
        self.assertNotIn("safe_secret_store_message", views)
        self.assertNotIn("mappings_2.application", views)
        self.assertNotIn("evidence.message", template)
        self.assertNotIn("prior_evidence", template)
        self.assertIn("paused_effect_diagnostic", template)

    def _get_paused_html(self, diagnostic: dict) -> str:
        value = projection(
            status="paused_unknown",
            stage="paused_unknown",
            workflow_key="easyimports.duplicate_resolution",
            workflow_version=8,
            paused_effect_diagnostic=diagnostic,
        )
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=value["workflow_key"],
            target_provider_id="fake",
            operator_label="VDR-2",
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
        response = self.client.get(
            reverse("importer:workflow", args=[session.id])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode("utf-8")
