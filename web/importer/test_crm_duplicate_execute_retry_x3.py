"""X3 operator runtime: DEBUG off, closed 404/500, decision-set dead-end.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_execute_retry_and_terminal_ux_reliability.md
Phase X3.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from html import unescape
from pathlib import Path
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import include, path, reverse

from importer.models import ImportSession
from importer.workflow_state import (
    DUPLICATE_RESOLUTION_PRODUCT_KEY,
    issue_form_token,
    store_workflow_projection,
)
from importer.workflow_views import _closed_step_unavailable

_REPO = Path(__file__).resolve().parents[2]
_WEB = Path(__file__).resolve().parents[1]
_HTTPS_LAUNCHER = _REPO / "scripts" / "start_local_https.ps1"
_HTTP_LAUNCHER = _REPO / "scripts" / "start_local.ps1"
_RUN_SERVICE = _REPO / "scripts" / "lib" / "run_service.ps1"

_OPERATOR_HOSTS = ["127.0.0.1", "localhost"]
_OPERATOR_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

_FORCED_500_TOKEN = "secret-x3-exception-detail"


def _forced_500(request):
    raise RuntimeError(_FORCED_500_TOKEN)


urlpatterns = [
    path("x3-forced-500/", _forced_500),
    path("", include("importer.urls")),
]
handler404 = "easyimports_web.views.closed_404"
handler500 = "easyimports_web.views.closed_500"


def _operator_override(*, static_root: str, root_urlconf: str | None = None) -> dict:
    values = {
        "DEBUG": False,
        "ALLOWED_HOSTS": list(_OPERATOR_HOSTS),
        "CSRF_COOKIE_SECURE": True,
        "SESSION_COOKIE_SECURE": True,
        "STATIC_ROOT": static_root,
        "STORAGES": _OPERATOR_STORAGES,
    }
    if root_urlconf is not None:
        values["ROOT_URLCONF"] = root_urlconf
    return values


def _assert_closed_body(test: TestCase, body: str) -> None:
    text = unescape(body)
    test.assertIn("data-closed-operator-error", text)
    test.assertNotIn("Django tried these URL patterns", text)
    test.assertNotIn("urlpatterns", text)
    test.assertNotIn("Traceback", text)
    test.assertNotIn("Traceback (most recent call last)", text)
    test.assertNotIn("Review workflow not found.", text)
    test.assertNotIn("Decision-set handoff not found.", text)
    test.assertNotIn(_FORCED_500_TOKEN, text)
    test.assertNotIn("RuntimeError", text)


class CrmDuplicateExecuteRetryX3Tests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._static = tempfile.TemporaryDirectory(prefix="ei-x3-static-")
        cls._settings = override_settings(
            **_operator_override(static_root=cls._static.name)
        )
        cls._settings.enable()
        call_command("collectstatic", interactive=False, verbosity=0, clear=True)

    @classmethod
    def tearDownClass(cls):
        cls._settings.disable()
        cls._static.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
            target_provider_id="fake-preview-v1",
            operator_label="Operator",
            options={"run_id": "run-source-x3"},
        )
        self.continuation = store_workflow_projection(
            self.session,
            {
                "run_id": "run-cont-x3",
                "workflow_key": DUPLICATE_RESOLUTION_PRODUCT_KEY,
                "workflow_version": 5,
                "target_provider_id": "fake-preview-v1",
                "status": "running",
                "stage": "duplicate_execution",
                "revision": 4,
                "summary": {},
                "terminal_evidence": None,
            },
            role="continuation",
        )

    def test_https_launcher_installs_operator_profile(self):
        https = _HTTPS_LAUNCHER.read_text(encoding="utf-8")
        http = _HTTP_LAUNCHER.read_text(encoding="utf-8")
        runner = _RUN_SERVICE.read_text(encoding="utf-8")
        self.assertIn('$env:EASYIMPORTS_DEBUG = "0"', https)
        self.assertIn("collectstatic --noinput", https)
        self.assertIn("localhost,127.0.0.1", https)
        self.assertIn("EASYIMPORTS_HTTPS_LOOPBACK", https)
        self.assertIn('$env:EASYIMPORTS_DEBUG = "0"', runner)
        self.assertIn('$env:EASYIMPORTS_DEBUG = "true"', http)
        self.assertNotIn("collectstatic --noinput", http)
        debug_at = https.find('$env:EASYIMPORTS_DEBUG = "0"')
        collect_at = https.find("collectstatic --noinput")
        ports_at = https.find("Assert-EasyImportsPortsFree")
        self.assertGreater(collect_at, debug_at)
        self.assertGreater(ports_at, collect_at)

    def test_operator_env_resolves_debug_false(self):
        env = os.environ.copy()
        env["EASYIMPORTS_DEBUG"] = "0"
        env["EASYIMPORTS_ALLOWED_HOSTS"] = "localhost,127.0.0.1"
        env["EASYIMPORTS_HTTPS_LOOPBACK"] = "1"
        env["DJANGO_SETTINGS_MODULE"] = "easyimports_web.settings"
        env.pop("RENDER", None)
        env.pop("RENDER_EXTERNAL_HOSTNAME", None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import django; django.setup(); "
                    "from django.conf import settings; "
                    "assert settings.DEBUG is False, settings.DEBUG; "
                    "assert '127.0.0.1' in settings.ALLOWED_HOSTS; "
                    "assert 'localhost' in settings.ALLOWED_HOSTS; "
                    "assert '*' not in settings.ALLOWED_HOSTS; "
                    "assert settings.CSRF_COOKIE_SECURE is True; "
                    "assert settings.SESSION_COOKIE_SECURE is True; "
                    "print('operator-profile-ok')"
                ),
            ],
            cwd=str(_WEB),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("operator-profile-ok", result.stdout)

    def test_operator_landing_renders_200(self):
        from django.conf import settings as django_settings

        self.assertFalse(django_settings.DEBUG)
        page = self.client.get("/", HTTP_HOST="127.0.0.1")
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertIn("EasyImports", body)
        self.assertNotIn("DisallowedHost", body)
        self.assertNotIn("Django tried these URL patterns", body)
        self.assertNotIn("Traceback", body)

    def test_missing_path_uses_closed_404(self):
        page = self.client.get("/x3-no-such-operator-route/", HTTP_HOST="127.0.0.1")
        self.assertEqual(page.status_code, 404)
        body = unescape(page.content.decode("utf-8"))
        _assert_closed_body(self, body)
        self.assertIn("This page isn't available", body)

    def test_decision_set_dead_end_is_closed(self):
        token = issue_form_token(
            owner_id=self.owner,
            session=self.session,
            action_kind="decision_set",
            workflow=self.continuation,
        )
        page = self.client.post(
            reverse("importer:create_decision_set", args=[self.session.id]),
            {"form_token": token},
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(page.status_code, 410)
        body = unescape(page.content.decode("utf-8"))
        _assert_closed_body(self, body)
        self.assertIn("This step isn't available", body)

    def test_continue_source_dead_end_is_closed(self):
        token = issue_form_token(
            owner_id=self.owner,
            session=self.session,
            action_kind="continue",
            workflow=self.continuation,
        )
        page = self.client.post(
            reverse("importer:continue_source", args=[self.session.id]),
            {"form_token": token},
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(page.status_code, 410)
        body = unescape(page.content.decode("utf-8"))
        _assert_closed_body(self, body)
        self.assertIn("This step isn't available", body)

    def test_forced_500_uses_closed_handler(self):
        self.client.raise_request_exception = False
        with override_settings(
            ROOT_URLCONF="importer.test_crm_duplicate_execute_retry_x3"
        ):
            page = self.client.get("/x3-forced-500/", HTTP_HOST="127.0.0.1")
        self.assertEqual(page.status_code, 500)
        body = unescape(page.content.decode("utf-8"))
        _assert_closed_body(self, body)
        self.assertIn("Something went wrong", body)
        self.assertNotIn("{% static", body)
        self.assertNotIn("/static/", body)
        template = (_WEB / "importer" / "templates" / "importer" / "closed_500.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("extends", template)
        self.assertNotIn("static", template)

    def test_closed_helper_stays_django_local(self):
        import inspect

        self.assertNotIn("mappings_2", inspect.getsource(_closed_step_unavailable))
