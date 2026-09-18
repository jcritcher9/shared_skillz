"""HTTPS-2: loopback TLS server, is_secure, Secure+SameSite=Lax session path."""

from __future__ import annotations

import http.cookiejar
import json
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from django.core.wsgi import get_wsgi_application
from django.http import JsonResponse
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path

from importer.https_loopback import (
    LOOPBACK_ORIGIN,
    apply_https_loopback_runtime_settings,
    ensure_loopback_tls_material,
    require_tls_material,
)
from importer.management.commands.runserver_https import (
    SecureWSGIRequestHandler,
    SecureWSGIServer,
)

_TLS_MARKER_KEY = "https2_tls_session_marker"
_TLS_MARKER_VALUE = "tls-session-marker-v1"


def _tls_probe(request):
    return JsonResponse(
        {
            "is_secure": request.is_secure(),
            "scheme": request.scheme,
            "https_meta": request.META.get("HTTPS"),
            "wsgi_url_scheme": request.META.get("wsgi.url_scheme"),
        }
    )


def _tls_session_start(request):
    """Establish a Durable session marker for continuity assertions."""

    request.session[_TLS_MARKER_KEY] = _TLS_MARKER_VALUE
    request.session.save()
    return JsonResponse(
        {
            "marker": request.session.get(_TLS_MARKER_KEY),
            "session_key": request.session.session_key,
            "is_secure": request.is_secure(),
        }
    )


def _tls_session_read(request):
    return JsonResponse(
        {
            "marker": request.session.get(_TLS_MARKER_KEY),
            "session_key": request.session.session_key,
            "is_secure": request.is_secure(),
        }
    )


urlpatterns = [
    path("_tls_probe/", _tls_probe, name="tls_probe"),
    path("_tls_session_start/", _tls_session_start, name="tls_session_start"),
    path("_tls_session_read/", _tls_session_read, name="tls_session_read"),
    # Match product mount: namespace "importer" for reverse() in templates.
    path("", include(("importer.urls", "importer"), namespace="importer")),
]


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """Keep 3xx responses so callback status can be inspected."""

    def http_response(self, request, response):
        return response

    https_response = http_response


class TlsMaterialTests(SimpleTestCase):
    def test_ensure_writes_outside_repo_and_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert = Path(tmp) / "loopback.crt"
            key = Path(tmp) / "loopback.key"
            out_cert, out_key = ensure_loopback_tls_material(
                cert_file=cert, key_file=key
            )
            self.assertEqual(out_cert, cert.resolve())
            self.assertTrue(cert.is_file())
            self.assertTrue(key.is_file())
            # Second call is idempotent.
            again = require_tls_material(
                cert_file=cert, key_file=key, ensure=False
            )
            self.assertEqual(again[0], cert.resolve())

    def test_refuse_keys_inside_repository(self):
        repo_dir = Path(__file__).resolve().parents[1] / "dev_tls"
        with self.assertRaises(RuntimeError):
            ensure_loopback_tls_material(
                cert_file=repo_dir / "must_not_write.crt",
                key_file=repo_dir / "must_not_write.key",
            )

    def test_refuse_when_only_key_is_inside_repository(self):
        """Split paths: outside cert + inside key must still fail closed."""

        repo_dir = Path(__file__).resolve().parents[1] / "dev_tls"
        with tempfile.TemporaryDirectory() as tmp:
            outside_cert = Path(tmp) / "loopback.crt"
            inside_key = repo_dir / "must_not_write_split.key"
            with self.assertRaises(RuntimeError) as ctx:
                ensure_loopback_tls_material(
                    cert_file=outside_cert,
                    key_file=inside_key,
                )
            self.assertIn("private key", str(ctx.exception).lower())
            self.assertFalse(inside_key.exists())
            self.assertFalse(outside_cert.exists())

    def test_refuse_when_only_cert_is_inside_repository(self):
        repo_dir = Path(__file__).resolve().parents[1] / "dev_tls"
        with tempfile.TemporaryDirectory() as tmp:
            inside_cert = repo_dir / "must_not_write_split.crt"
            outside_key = Path(tmp) / "loopback.key"
            with self.assertRaises(RuntimeError) as ctx:
                ensure_loopback_tls_material(
                    cert_file=inside_cert,
                    key_file=outside_key,
                )
            self.assertIn("certificate", str(ctx.exception).lower())
            self.assertFalse(inside_cert.exists())
            self.assertFalse(outside_key.exists())

    def test_missing_without_ensure_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            require_tls_material(
                cert_file=Path("C:/nonexistent/loopback.crt"),
                key_file=Path("C:/nonexistent/loopback.key"),
                ensure=False,
            )


class HttpsLoopbackSettingsTests(SimpleTestCase):
    def test_apply_runtime_settings_secure_and_lax(self):
        from django.conf import settings

        with patch.object(settings, "SESSION_COOKIE_SECURE", False):
            with patch.object(settings, "CSRF_COOKIE_SECURE", False):
                with patch.object(settings, "SESSION_COOKIE_SAMESITE", "Strict"):
                    with patch.object(settings, "CSRF_COOKIE_SAMESITE", "Strict"):
                        with patch.object(settings, "CSRF_TRUSTED_ORIGINS", []):
                            with patch.object(
                                settings, "ALLOWED_HOSTS", ["example.com"]
                            ):
                                apply_https_loopback_runtime_settings()
                                self.assertTrue(settings.SESSION_COOKIE_SECURE)
                                self.assertTrue(settings.CSRF_COOKIE_SECURE)
                                self.assertEqual(
                                    settings.SESSION_COOKIE_SAMESITE, "Lax"
                                )
                                self.assertEqual(
                                    settings.CSRF_COOKIE_SAMESITE, "Lax"
                                )
                                self.assertIn(
                                    LOOPBACK_ORIGIN, settings.CSRF_TRUSTED_ORIGINS
                                )
                                self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)


@override_settings(
    ROOT_URLCONF=__name__,
    ALLOWED_HOSTS=["127.0.0.1", "testserver", "localhost"],
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SECURE=True,
    CSRF_COOKIE_SAMESITE="Lax",
    DEBUG=True,
)
class LiveTlsServerTests(TestCase):
    """Real TLS socket: landing + callback + request.is_secure() + session marker."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdir_obj = tempfile.TemporaryDirectory(prefix="ei-https2-")
        cls._tmpdir = cls._tmpdir_obj.name
        cls.cert = Path(cls._tmpdir) / "loopback.crt"
        cls.key = Path(cls._tmpdir) / "loopback.key"
        ensure_loopback_tls_material(cert_file=cls.cert, key_file=cls.key)

        SecureWSGIServer.certfile = str(cls.cert)
        SecureWSGIServer.keyfile = str(cls.key)
        cls.port = _free_port()
        cls.httpd = SecureWSGIServer(
            ("127.0.0.1", cls.port), SecureWSGIRequestHandler
        )
        cls.httpd.set_app(get_wsgi_application())
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()
        # Brief readiness wait.
        deadline = time.time() + 5
        last_err = None
        while time.time() < deadline:
            try:
                cls._https_get("/_tls_probe/")
                break
            except Exception as exc:  # noqa: BLE001 — readiness probe
                last_err = exc
                time.sleep(0.05)
        else:
            raise RuntimeError(f"TLS server not ready: {last_err}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        finally:
            try:
                cls._tmpdir_obj.cleanup()
            except Exception:  # noqa: BLE001 — best-effort temp cleanup
                pass
            super().tearDownClass()

    @classmethod
    def _opener(cls, *, follow_redirects: bool = True):
        ctx = ssl._create_unverified_context()
        jar = http.cookiejar.CookieJar()
        handlers = [
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(jar),
        ]
        if not follow_redirects:
            handlers.append(_NoRedirect())
        return urllib.request.build_opener(*handlers), jar

    @classmethod
    def _https_get(cls, path: str, opener=None, *, follow_redirects: bool = True):
        if opener is None:
            opener, _ = cls._opener(follow_redirects=follow_redirects)
        url = f"https://127.0.0.1:{cls.port}{path}"
        with opener.open(url, timeout=5) as resp:
            body = resp.read()
            return resp.status, body, resp.headers

    def test_request_is_secure_over_real_tls(self):
        status, body, _ = self._https_get("/_tls_probe/")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["is_secure"])
        self.assertEqual(payload["scheme"], "https")
        self.assertEqual(payload["https_meta"], "on")
        self.assertEqual(payload["wsgi_url_scheme"], "https")

    def test_landing_and_callback_session_continuity_over_tls(self):
        ctx = ssl._create_unverified_context()
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(jar),
        )
        # Shared jar so callback + post-callback read use the same session.
        no_redir = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(jar),
            _NoRedirect(),
        )

        # Product landing over real TLS.
        status_landing, body_landing, _ = self._https_get("/", opener=opener)
        self.assertEqual(status_landing, 200)
        self.assertTrue(body_landing)

        # Establish an explicit session marker (required continuity proof).
        status_start, body_start, _ = self._https_get(
            "/_tls_session_start/", opener=opener
        )
        self.assertEqual(status_start, 200)
        start_payload = json.loads(body_start.decode("utf-8"))
        self.assertEqual(start_payload["marker"], _TLS_MARKER_VALUE)
        self.assertTrue(start_payload["session_key"])
        self.assertTrue(start_payload["is_secure"])

        session_cookies = [c for c in jar if c.name == "sessionid"]
        self.assertTrue(
            session_cookies,
            "sessionid cookie must be issued after session start",
        )
        for cookie in session_cookies:
            self.assertTrue(
                cookie.secure,
                "sessionid cookie must be Secure over HTTPS loopback",
            )

        established_key = start_payload["session_key"]

        # OAuth callback on the same TLS origin with the established session.
        # Do not follow redirect so status is from the callback route itself.
        try:
            status_cb, _, _ = self._https_get(
                "/crm/oauth/callback/", opener=no_redir
            )
        except urllib.error.HTTPError as exc:
            status_cb = exc.code
        self.assertIn(status_cb, {200, 302, 400, 403})

        # Marker must still be present after callback return (same session).
        status_read, body_read, _ = self._https_get(
            "/_tls_session_read/", opener=opener
        )
        self.assertEqual(status_read, 200)
        read_payload = json.loads(body_read.decode("utf-8"))
        self.assertEqual(read_payload["marker"], _TLS_MARKER_VALUE)
        self.assertEqual(read_payload["session_key"], established_key)
        self.assertTrue(read_payload["is_secure"])


class RunserverHttpsCommandImportTests(SimpleTestCase):
    def test_command_module_importable(self):
        from importer.management.commands import runserver_https

        self.assertTrue(hasattr(runserver_https, "Command"))
        self.assertTrue(hasattr(runserver_https, "SecureWSGIServer"))
        self.assertTrue(hasattr(runserver_https, "SecureWSGIRequestHandler"))
