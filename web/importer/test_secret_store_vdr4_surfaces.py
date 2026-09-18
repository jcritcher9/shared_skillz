from __future__ import annotations

"""VDR-4 Django status and aggregate vault-health rendering."""

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .api_client import EasyImportsApiClient


class Vdr4DjangoSurfaceTests(TestCase):
    def setUp(self) -> None:
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(uuid4())
        browser_session["easyimports_crm_owner_session"] = "vdr4-owner"
        browser_session.save()

    def test_settings_renders_secret_free_vault_health_counts(self) -> None:
        storage = {
            "application_root": r"C:\EasyImports",
            "bootstrap_path": r"C:\EasyImports\bootstrap.json",
            "data_root": r"C:\EasyImports\data",
            "is_default_data_root": True,
            "source": "default",
            "can_change_location": False,
            "change_blocked_reason": "Storage is frozen.",
            "vault_health": {
                "indexed_key_count": 3,
                "decrypt_failed_count": 1,
            },
        }
        with (
            patch.object(EasyImportsApiClient, "local_storage", return_value=storage),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={"registrations": []},
            ),
        ):
            response = self.client.get(reverse("importer:local_settings"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Secret vault health", html)
        self.assertIn("Indexed secrets</dt><dd>3", html)
        self.assertIn("Secrets that could not be decrypted</dt><dd>1", html)
        self.assertIn("Counts only", html)
        self.assertNotIn("connection_credential:", html)
        self.assertNotIn("Windows DPAPI", html)

    def test_connect_crm_renders_reconnect_for_unavailable_status(self) -> None:
        connection = {
            "connection_id": "crm_conn_vdr4",
            "provider_key": "salesforce",
            "provider_label": "Salesforce",
            "status": "credential_unavailable",
            "display_label": "Example org",
            "last_reclaim": None,
        }
        with (
            patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={
                    "providers": [
                        {
                            "provider_key": "salesforce",
                            "provider_label": "Salesforce",
                        }
                    ]
                },
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": [connection]},
            ),
            patch.object(
                EasyImportsApiClient,
                "crm_app_registrations",
                return_value={
                    "registrations": [{"provider_key": "salesforce"}]
                },
            ),
        ):
            response = self.client.get(reverse("importer:crm_connections"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Credentials unavailable", html)
        self.assertIn("Reconnect this CRM connection", html)
        self.assertIn(">Reconnect</button>", html)
        self.assertNotIn("secret_vault", html)
        self.assertNotIn("access_token", html)

    def test_django_surface_does_not_import_mappings_2(self) -> None:
        root = Path(__file__).parent
        for relative in (
            "connection_views.py",
            "settings_views.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            self.assertNotIn("from mappings_2", source)
            self.assertNotIn("import mappings_2", source)
