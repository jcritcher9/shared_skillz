"""HSR-1: deferred per-connection TLS handshake for SecureWSGIServer.

Proves the accept-loop fix from
``https_loopback_tls_handshake_reliability.md`` (HSR-1):

- empty cert/key fails closed at construction;
- stalled/aborted handshake is closed without a full traceback;
- HSR-0 regression stays green (imported suite still authoritative for wedge).
"""

from __future__ import annotations

import io
import socket
import socketserver
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path

from django.core.wsgi import get_wsgi_application
from django.test import SimpleTestCase, TestCase, override_settings

from importer.https_loopback import ensure_loopback_tls_material
from importer.management.commands.runserver_https import (
    SecureWSGIRequestHandler,
    SecureWSGIServer,
)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _threaded_secure_server(
    server_address: tuple[str, int],
) -> socketserver.BaseServer:
    httpd_cls = type(
        "ThreadedSecureWSGIServer",
        (socketserver.ThreadingMixIn, SecureWSGIServer),
        {"daemon_threads": True},
    )
    return httpd_cls(server_address, SecureWSGIRequestHandler)


class SecureWSGIServerConstructionTests(SimpleTestCase):
    def test_empty_certfile_keyfile_fails_closed(self):
        prev_cert, prev_key = SecureWSGIServer.certfile, SecureWSGIServer.keyfile
        try:
            SecureWSGIServer.certfile = ""
            SecureWSGIServer.keyfile = ""
            with self.assertRaises(ValueError) as ctx:
                SecureWSGIServer(("127.0.0.1", 0), SecureWSGIRequestHandler)
            self.assertIn("certfile", str(ctx.exception).lower())
        finally:
            SecureWSGIServer.certfile = prev_cert
            SecureWSGIServer.keyfile = prev_key

    def test_empty_keyfile_only_fails_closed(self):
        prev_cert, prev_key = SecureWSGIServer.certfile, SecureWSGIServer.keyfile
        try:
            SecureWSGIServer.certfile = "C:/nonexistent/loopback.crt"
            SecureWSGIServer.keyfile = ""
            with self.assertRaises(ValueError) as ctx:
                SecureWSGIServer(("127.0.0.1", 0), SecureWSGIRequestHandler)
            self.assertIn("keyfile", str(ctx.exception).lower())
        finally:
            SecureWSGIServer.certfile = prev_cert
            SecureWSGIServer.keyfile = prev_key


@override_settings(
    ALLOWED_HOSTS=["127.0.0.1", "testserver", "localhost"],
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SECURE=True,
    CSRF_COOKIE_SAMESITE="Lax",
    DEBUG=True,
)
class Hsr1DeferredHandshakeTests(TestCase):
    """Real TLS server: quiet close on stalled handshake; second client OK."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdir_obj = tempfile.TemporaryDirectory(prefix="ei-hsr1-")
        cls.cert = Path(cls._tmpdir_obj.name) / "loopback.crt"
        cls.key = Path(cls._tmpdir_obj.name) / "loopback.key"
        ensure_loopback_tls_material(cert_file=cls.cert, key_file=cls.key)

    @classmethod
    def tearDownClass(cls):
        try:
            cls._tmpdir_obj.cleanup()
        except Exception:  # noqa: BLE001
            pass
        super().tearDownClass()

    def setUp(self):
        SecureWSGIServer.certfile = str(self.cert)
        SecureWSGIServer.keyfile = str(self.key)
        self.port = _free_port()
        self.httpd = _threaded_secure_server(("127.0.0.1", self.port))
        self.httpd.set_app(get_wsgi_application())
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self._thread.start()
        self._wait_ready()

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _wait_ready(self) -> None:
        deadline = time.time() + 5.0
        last_err: BaseException | None = None
        while time.time() < deadline:
            try:
                status, _ = self._https_get("/", timeout=2.0)
                if status == 200:
                    return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.05)
        raise RuntimeError(f"HSR-1 TLS server not ready: {last_err}")

    def _https_get(self, path: str, *, timeout: float) -> tuple[int, bytes]:
        ctx = ssl._create_unverified_context()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        )
        url = f"https://127.0.0.1:{self.port}{path}"
        try:
            with opener.open(url, timeout=timeout) as resp:
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            return int(exc.code), body

    def test_stalled_handshake_closed_without_traceback(self):
        """Aborted peer: no full traceback on stderr; server still serves."""

        stderr = io.StringIO()
        stalled = socket.create_connection(
            ("127.0.0.1", self.port), timeout=2.0
        )
        try:
            time.sleep(0.15)
            with redirect_stderr(stderr):
                # Abort before ClientHello completes — worker hits SSLError.
                stalled.close()
                stalled = None
                time.sleep(0.35)
                status, body = self._https_get("/", timeout=3.0)
        finally:
            if stalled is not None:
                try:
                    stalled.close()
                except OSError:
                    pass

        self.assertEqual(status, 200)
        self.assertTrue(body)
        err_text = stderr.getvalue()
        self.assertNotIn(
            "Traceback (most recent call last)",
            err_text,
            f"stalled handshake must not dump full traceback; got:\n{err_text}",
        )

    def test_listening_socket_is_not_ssl_wrapped(self):
        """Accept path is plain TCP; handshake deferred to the wrapped peer."""

        listen = self.httpd.socket
        self.assertFalse(
            isinstance(listen, ssl.SSLSocket),
            "listening socket must remain plain TCP after HSR-1",
        )
        self.assertTrue(hasattr(self.httpd, "_ssl_context"))
