"""HTTPS-1: Django OAuth redirect resolve + validation (network-free)."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings
from django.test.client import RequestFactory

from importer.oauth_redirect import (
    ENV_LEGACY_HUBSPOT_REDIRECT_URI,
    ENV_UNIFIED_OAUTH_REDIRECT_URI,
    HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
    LEGACY_HTTP_OAUTH_CALLBACK_URI,
    InvalidOAuthRedirectUri,
    resolve_hubspot_oauth_redirect_uri,
    resolve_oauth_redirect_uri,
    resolve_salesforce_oauth_redirect_uri,
    validate_oauth_redirect_uri,
)


_VALID_HTTPS = "https://127.0.0.1:8001/crm/oauth/callback/"
_TUNNEL = "https://abc.ngrok-free.app/crm/oauth/callback/"
_HS_ONLY = "https://hs-only.example.com/crm/oauth/callback/"


class OAuthRedirectValidateTests(SimpleTestCase):
    def test_accepts_valid_https(self):
        self.assertEqual(
            validate_oauth_redirect_uri(_VALID_HTTPS),
            _VALID_HTTPS,
        )

    def test_rejects_http_override(self):
        with self.assertRaises(InvalidOAuthRedirectUri):
            validate_oauth_redirect_uri(
                "http://127.0.0.1:8001/crm/oauth/callback/",
                allow_legacy_http_builtin=False,
            )

    def test_allows_exact_legacy_when_flag_set(self):
        self.assertEqual(
            validate_oauth_redirect_uri(
                LEGACY_HTTP_OAUTH_CALLBACK_URI,
                allow_legacy_http_builtin=True,
            ),
            LEGACY_HTTP_OAUTH_CALLBACK_URI,
        )

    def test_rejects_credentials_query_fragment_wrong_path(self):
        cases = [
            "https://user:pass@example.com/crm/oauth/callback/",
            "https://example.com/crm/oauth/callback/?a=1",
            "https://example.com/crm/oauth/callback/#x",
            "https://example.com/crm/oauth/callback/;x",
            "https://example.com:/crm/oauth/callback/",
            "https://example.com:abc/crm/oauth/callback/",
            "https://example.com/other/",
            "https://10.0.0.5/crm/oauth/callback/",
            "https://0177.0.0.1/crm/oauth/callback/",
            "https://127.0.0.01/crm/oauth/callback/",
            "https://127.1/crm/oauth/callback/",
            "https://127.0.1/crm/oauth/callback/",
            "https://0x7f.0.0.1/crm/oauth/callback/",
            "https://0x7f000001/crm/oauth/callback/",
            "https://2130706433/crm/oauth/callback/",
        ]
        for uri in cases:
            with self.subTest(uri=uri):
                with self.assertRaises(InvalidOAuthRedirectUri):
                    validate_oauth_redirect_uri(uri)

    def test_rejects_over_max_length(self):
        host = "x" * 480 + ".example.com"
        uri = f"https://{host}/crm/oauth/callback/"
        self.assertGreater(len(uri), 512)
        with self.assertRaises(InvalidOAuthRedirectUri):
            validate_oauth_redirect_uri(uri)


class OAuthRedirectResolveTests(SimpleTestCase):
    def setUp(self):
        for key in (
            ENV_UNIFIED_OAUTH_REDIRECT_URI,
            ENV_LEGACY_HUBSPOT_REDIRECT_URI,
        ):
            os.environ.pop(key, None)

    def tearDown(self):
        for key in (
            ENV_UNIFIED_OAUTH_REDIRECT_URI,
            ENV_LEGACY_HUBSPOT_REDIRECT_URI,
        ):
            os.environ.pop(key, None)

    def test_builtin_https_when_no_env_https3(self):
        self.assertEqual(
            resolve_oauth_redirect_uri(provider="salesforce"),
            HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
        )
        self.assertEqual(
            resolve_oauth_redirect_uri(provider="hubspot"),
            HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
        )

    def test_provider_required_rejects_unknown(self):
        with self.assertRaises(InvalidOAuthRedirectUri):
            resolve_oauth_redirect_uri(provider="fake")

    def test_only_legacy_hs_env_affects_hubspot_not_salesforce(self):
        os.environ[ENV_LEGACY_HUBSPOT_REDIRECT_URI] = _HS_ONLY
        self.assertEqual(resolve_hubspot_oauth_redirect_uri(), _HS_ONLY)
        self.assertEqual(
            resolve_salesforce_oauth_redirect_uri(),
            HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
        )

    def test_unified_env_wins_for_both_over_legacy_hs(self):
        os.environ[ENV_UNIFIED_OAUTH_REDIRECT_URI] = _TUNNEL
        os.environ[ENV_LEGACY_HUBSPOT_REDIRECT_URI] = _HS_ONLY
        self.assertEqual(resolve_salesforce_oauth_redirect_uri(), _TUNNEL)
        self.assertEqual(resolve_hubspot_oauth_redirect_uri(), _TUNNEL)

    def test_invalid_unified_env_fails_closed(self):
        os.environ[ENV_UNIFIED_OAUTH_REDIRECT_URI] = (
            "http://127.0.0.1:8001/crm/oauth/callback/"
        )
        with self.assertRaises(InvalidOAuthRedirectUri):
            resolve_salesforce_oauth_redirect_uri()
        with self.assertRaises(InvalidOAuthRedirectUri):
            resolve_hubspot_oauth_redirect_uri()

    def test_invalid_legacy_hs_env_fails_closed_for_hubspot(self):
        os.environ[ENV_LEGACY_HUBSPOT_REDIRECT_URI] = (
            "http://127.0.0.1:8001/crm/oauth/callback/"
        )
        with self.assertRaises(InvalidOAuthRedirectUri):
            resolve_hubspot_oauth_redirect_uri()
        # SF still uses builtin when only invalid HS env is present — wait:
        # freeze says invalid override fails closed for that path only.
        # SF path does not read legacy_hs, so SF still gets builtin.
        self.assertEqual(
            resolve_salesforce_oauth_redirect_uri(),
            HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
        )


class SettingsRegistrationMaterialTests(SimpleTestCase):
    def setUp(self):
        for key in (
            ENV_UNIFIED_OAUTH_REDIRECT_URI,
            ENV_LEGACY_HUBSPOT_REDIRECT_URI,
        ):
            os.environ.pop(key, None)
        self.factory = RequestFactory()

    def tearDown(self):
        for key in (
            ENV_UNIFIED_OAUTH_REDIRECT_URI,
            ENV_LEGACY_HUBSPOT_REDIRECT_URI,
        ):
            os.environ.pop(key, None)

    def _loopback_post(self, path: str, data: dict):
        request = self.factory.post(path, data)
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        return request

    def _loopback_get(self, path: str):
        request = self.factory.get(path)
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        return request

    def test_settings_page_shows_provider_specific_callbacks(self):
        from importer import settings_views

        os.environ[ENV_LEGACY_HUBSPOT_REDIRECT_URI] = _HS_ONLY
        request = self._loopback_get("/settings/")
        mock_client = Mock()
        mock_client.local_storage.return_value = {"data_root": "x"}
        mock_client.crm_app_registrations.return_value = {"registrations": []}
        captured: dict = {}

        def _capture_render(request, template, context):
            captured.update(context)
            return context

        with (
            patch.object(settings_views, "_client", return_value=mock_client),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(
                settings_views,
                "_issue_reg_form_token",
                return_value="token",
            ),
            patch.object(settings_views, "render", side_effect=_capture_render),
        ):
            settings_views.local_settings(request)
        self.assertEqual(
            captured["salesforce_redirect_uri"],
            HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
        )
        self.assertEqual(captured["hubspot_redirect_uri"], _HS_ONLY)
        self.assertNotEqual(
            captured["salesforce_redirect_uri"],
            captured["hubspot_redirect_uri"],
        )
        self.assertIn(_HS_ONLY, captured["hubspot_redirect_uri"])

    def test_salesforce_settings_put_uses_resolved_redirect(self):
        from importer import settings_views

        os.environ[ENV_UNIFIED_OAUTH_REDIRECT_URI] = _TUNNEL
        mock_mutation = Mock()
        mock_mutation.id = "mut-sf"
        request = self._loopback_post(
            "/settings/crm-app-registrations/salesforce/",
            {
                "label": "SF",
                "login_environment": "sandbox",
                "client_id": "CID",
                "client_secret": "secret",
            },
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(
                settings_views,
                "_registration_mutation_slot",
                return_value=(uuid4(), 0),
            ),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets"),
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"provider_key": "salesforce"},
            ),
            patch.object(settings_views, "messages"),
        ):
            response = settings_views.local_settings_salesforce_registration(request)
        self.assertEqual(response.status_code, 302)
        journal = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(journal["redirect_uri"], _TUNNEL)

    def test_hubspot_settings_oauth_put_uses_hs_legacy_env(self):
        from importer import settings_views

        os.environ[ENV_LEGACY_HUBSPOT_REDIRECT_URI] = _HS_ONLY
        mock_mutation = Mock()
        mock_mutation.id = "mut-hs"
        request = self._loopback_post(
            "/settings/crm-app-registrations/hubspot/",
            {
                "label": "HS",
                "auth_mode": "oauth",
                "client_id": "HSCLIENT",
                "client_secret": "oauth-secret",
            },
        )
        with (
            patch.object(settings_views, "_client", return_value=Mock()),
            patch.object(settings_views, "_settings_session", return_value=Mock()),
            patch.object(
                settings_views,
                "_registration_mutation_slot",
                return_value=(uuid4(), 0),
            ),
            patch.object(
                settings_views,
                "create_or_reuse_mutation",
                return_value=mock_mutation,
            ) as mock_create,
            patch.object(settings_views, "stage_ephemeral_registration_secrets"),
            patch.object(
                settings_views,
                "_dispatch_registration",
                return_value={"provider_key": "hubspot"},
            ),
            patch.object(settings_views, "messages"),
        ):
            response = settings_views.local_settings_hubspot_registration(request)
        self.assertEqual(response.status_code, 302)
        journal = mock_create.call_args.kwargs["request_json"]
        self.assertEqual(journal["redirect_uri"], _HS_ONLY)
