"""HSR-0: HTTPS loopback TLS accept-loop wedge characterization / regression.

Network-free regression for
``mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/https_loopback_tls_handshake_reliability/https_loopback_tls_handshake_reliability.md``.

- a healthy single client completes ``GET /`` with 200 (harness soundness);
- a TCP connection that never completes the TLS handshake must **not** block a
  second normal HTTPS client (failed pre-HSR-1; must pass after HSR-1 deferred
  handshake).
"""

from __future__ import annotations

import socket
import socketserver
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.core.wsgi import get_wsgi_application
from django.test import TestCase, override_settings

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
    """Match Django runserver: ``ThreadingMixIn`` composed over server_cls."""

    httpd_cls = type(
        "ThreadedSecureWSGIServer",
        (socketserver.ThreadingMixIn, SecureWSGIServer),
        {"daemon_threads": True},
    )
    return httpd_cls(server_address, SecureWSGIRequestHandler)


@override_settings(
    ALLOWED_HOSTS=["127.0.0.1", "testserver", "localhost"],
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SECURE=True,
    CSRF_COOKIE_SAMESITE="Lax",
    DEBUG=True,
)
class Hsr0HandshakeReliabilityTests(TestCase):
    """Real TLS + plain-TCP stall against the production SecureWSGIServer path."""

    # Second-client budget: long enough for a healthy handshake on loopback,
    # short enough to treat accept-loop wedge as failure (not flake).
    _SECOND_CLIENT_TIMEOUT_S = 2.0
    # Give the server thread time to enter accept()/TLS handshake on the stall.
    _STALL_SETTLE_S = 0.25

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdir_obj = tempfile.TemporaryDirectory(prefix="ei-hsr0-")
        cls._tmpdir = cls._tmpdir_obj.name
        cls.cert = Path(cls._tmpdir) / "loopback.crt"
        cls.key = Path(cls._tmpdir) / "loopback.key"
        ensure_loopback_tls_material(cert_file=cls.cert, key_file=cls.key)

    @classmethod
    def tearDownClass(cls):
        try:
            cls._tmpdir_obj.cleanup()
        except Exception:  # noqa: BLE001 — best-effort temp cleanup
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
        except Exception:  # noqa: BLE001 — best-effort server stop
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
            except Exception as exc:  # noqa: BLE001 — readiness probe
                last_err = exc
                time.sleep(0.05)
        raise RuntimeError(f"HSR-0 TLS server not ready: {last_err}")

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

    def test_healthy_single_client_get_root_200(self):
        """Harness soundness: one client completes GET / with 200 over TLS."""

        status, body = self._https_get("/", timeout=5.0)
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_stalled_handshake_does_not_block_second_client(self):
        """Stalled TCP must not wedge accept/TLS (green after HSR-1).

        Opens a plain TCP connection and never sends a TLS ClientHello, then
        asserts a second normal HTTPS client still completes GET / with 200
        within a short timeout. Pre-HSR-1 this failed (handshake inside accept).
        """

        stalled = socket.create_connection(
            ("127.0.0.1", self.port), timeout=2.0
        )
        try:
            # Do not write a ClientHello; leave the connection idle so the
            # server-side handshake (today: inside accept) waits forever.
            time.sleep(self._STALL_SETTLE_S)

            try:
                status, body = self._https_get(
                    "/", timeout=self._SECOND_CLIENT_TIMEOUT_S
                )
            except (TimeoutError, OSError, urllib.error.URLError) as exc:
                # Expected against pre-HSR-1 code: accept-loop wedge surfaces
                # as client handshake timeout (same class as operator
                # ERR_TIMED_OUT / "_ssl.c: … handshake operation timed out").
                self.fail(
                    "second HTTPS client could not complete while a stalled "
                    "TCP peer held an incomplete handshake (accept-loop must "
                    f"not block): {exc}"
                )

            self.assertEqual(
                status,
                200,
                "second HTTPS client must complete while a stalled TCP peer "
                "holds an incomplete handshake (accept-loop must not block)",
            )
            self.assertTrue(body)
        finally:
            try:
                stalled.close()
            except OSError:
                pass
