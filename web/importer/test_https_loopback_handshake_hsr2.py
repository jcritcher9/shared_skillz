"""HSR-2: concurrency / stall matrix + dual readiness for HTTPS loopback.

Network-free proofs for
``https_loopback_tls_handshake_reliability.md`` phase HSR-2:

- N concurrent clients (healthy + stalled) resolve without wedging accept;
- stalled peers are closed by the **server connection timeout** (not the test
  client), and their worker threads complete;
- broader failure matrix (truncated ClientHello, mid-handshake abort,
  post-handshake reset) leaves the server serving;
- dual sequential HTTPS GETs mirror launcher readiness + second-tab load
  (the 2026-08-12 operator failure mode).
"""

from __future__ import annotations

import io
import socket
import socketserver
import ssl
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from django.core.wsgi import get_wsgi_application
from django.test import TestCase, override_settings

import importer.management.commands.runserver_https as runserver_https
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
    *,
    worker_threads: list[threading.Thread] | None = None,
) -> socketserver.BaseServer:
    """Match Django runserver threading; optionally track worker threads."""

    tracked = worker_threads

    class TrackingThreadedSecureWSGIServer(
        socketserver.ThreadingMixIn, SecureWSGIServer
    ):
        daemon_threads = True

        def process_request(self, request, client_address):
            if tracked is None:
                return super().process_request(request, client_address)
            # Mirror ThreadingMixIn.process_request so tests can join workers.
            t = threading.Thread(
                target=self.process_request_thread,
                args=(request, client_address),
            )
            t.daemon = self.daemon_threads
            tracked.append(t)
            t.start()

    return TrackingThreadedSecureWSGIServer(
        server_address, SecureWSGIRequestHandler
    )


def _assert_peer_closed_by_server(sock: socket.socket, *, deadline: float) -> None:
    """Wait until the remote (server) closes ``sock`` without local close().

    Fails if the peer stays open past ``deadline`` — that means the server
    timeout (or equivalent close path) did not run.
    """

    sock.settimeout(0.2)
    while time.time() < deadline:
        try:
            data = sock.recv(64)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return  # hard close from server — sufficient
        except (TimeoutError, socket.timeout, OSError):
            # Still open / no data yet; keep waiting within deadline.
            continue
        if data == b"":
            return  # orderly EOF from server shutdown
        # Unexpected application data on a stalled non-TLS peer.
        raise AssertionError(
            f"stalled peer received unexpected data before server close: {data!r}"
        )
    raise AssertionError(
        "stalled peer still open after server timeout budget; "
        "server did not close the connection (timeout path unproven)"
    )


@override_settings(
    ALLOWED_HOSTS=["127.0.0.1", "testserver", "localhost"],
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SECURE=True,
    CSRF_COOKIE_SAMESITE="Lax",
    DEBUG=True,
)
class Hsr2ConcurrencyAndStallTests(TestCase):
    """Mixed concurrency + failure matrix against fixed SecureWSGIServer."""

    _HEALTHY_CLIENTS = 6
    _STALLED_CLIENTS = 4
    _CLIENT_TIMEOUT_S = 5.0
    # Short enough for network-free CI; long enough for accept/dispatch.
    _SHORT_SERVER_TIMEOUT_S = 0.5

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdir_obj = tempfile.TemporaryDirectory(prefix="ei-hsr2-")
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
        self._worker_threads: list[threading.Thread] = []
        self.httpd = _threaded_secure_server(
            ("127.0.0.1", self.port),
            worker_threads=self._worker_threads,
        )
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
        raise RuntimeError(f"HSR-2 TLS server not ready: {last_err}")

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

    def _join_workers(self, *, timeout_s: float) -> None:
        deadline = time.time() + timeout_s
        for t in list(self._worker_threads):
            remaining = max(0.05, deadline - time.time())
            t.join(timeout=remaining)
            self.assertFalse(
                t.is_alive(),
                "worker thread still alive after timeout budget "
                "(orphaned handler / missing server-side close)",
            )

    def test_mixed_concurrent_healthy_and_stalled_clients(self):
        """Healthy 200s under stall load; server timeout closes stalls + workers."""

        short_to = self._SHORT_SERVER_TIMEOUT_S
        stalled: list[socket.socket] = []
        stderr = io.StringIO()
        # Snapshot workers from readiness so we can reason about new ones.
        workers_before = len(self._worker_threads)

        try:
            # Patch the production timeout so the test proves *server* close,
            # not client-side cleanup (default 30s would make this too slow).
            with patch.object(
                runserver_https, "_CONNECTION_TIMEOUT_S", short_to
            ):
                for _ in range(self._STALLED_CLIENTS):
                    s = socket.create_connection(
                        ("127.0.0.1", self.port), timeout=2.0
                    )
                    stalled.append(s)
                # Let accept + worker dispatch bind each stall to a handler.
                time.sleep(0.2)
                workers_after_stalls = len(self._worker_threads)
                self.assertGreaterEqual(
                    workers_after_stalls - workers_before,
                    self._STALLED_CLIENTS,
                    "each stalled TCP peer must be handed to a worker thread",
                )

                def _one_healthy(_: int) -> int:
                    status, body = self._https_get(
                        "/", timeout=self._CLIENT_TIMEOUT_S
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(body)
                    return status

                with redirect_stderr(stderr):
                    with ThreadPoolExecutor(
                        max_workers=self._HEALTHY_CLIENTS
                    ) as pool:
                        futures = [
                            pool.submit(_one_healthy, i)
                            for i in range(self._HEALTHY_CLIENTS)
                        ]
                        results = [
                            f.result() for f in as_completed(futures)
                        ]

                self.assertEqual(results.count(200), self._HEALTHY_CLIENTS)
                err_text = stderr.getvalue()
                self.assertNotIn("Traceback (most recent call last)", err_text)

                # Critical: do **not** close stalled clients here. Wait for the
                # server connection timeout to drop them, then prove EOF/reset.
                close_deadline = time.time() + short_to + 1.5
                for s in stalled:
                    _assert_peer_closed_by_server(s, deadline=close_deadline)

                # Stall workers (and healthy workers) must finish — no orphans.
                self._join_workers(timeout_s=short_to + 2.0)
        finally:
            # Safety net only after assertions (or on failure paths).
            for s in stalled:
                try:
                    s.close()
                except OSError:
                    pass

        # Server still serves after server-driven stall cleanup.
        status, body = self._https_get("/", timeout=3.0)
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_truncated_clienthello_then_server_still_serves(self):
        """Partial TLS record is closed; subsequent GET / is 200."""

        peer = socket.create_connection(("127.0.0.1", self.port), timeout=2.0)
        try:
            # TLS handshake ContentType=handshake (0x16), version, truncated.
            peer.sendall(b"\x16\x03\x01\x00\x20\x01\x00")
            time.sleep(0.1)
            peer.close()
            peer = None
            time.sleep(0.25)
        finally:
            if peer is not None:
                try:
                    peer.close()
                except OSError:
                    pass

        status, body = self._https_get("/", timeout=3.0)
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_mid_handshake_abort_then_server_still_serves(self):
        """Deterministic mid-handshake abort: WantRead then close; no full wrap.

        Drives a real TLS ClientHello with ``do_handshake_on_connect=False``
        and non-blocking I/O until ``SSLWantReadError`` (ClientHello sent,
        ServerHello not yet consumed). Closing at that point is a true
        mid-handshake abort. Completing the handshake fails the test.
        """

        peer = socket.create_connection(("127.0.0.1", self.port), timeout=2.0)
        ssock: ssl.SSLSocket | None = None
        try:
            ctx = ssl._create_unverified_context()
            ssock = ctx.wrap_socket(
                peer,
                server_hostname="127.0.0.1",
                do_handshake_on_connect=False,
            )
            peer = None  # owned by ssock
            ssock.setblocking(False)

            want_read_seen = False
            for _ in range(20):
                try:
                    ssock.do_handshake()
                except ssl.SSLWantWriteError:
                    continue
                except ssl.SSLWantReadError:
                    want_read_seen = True
                    break
                else:
                    self.fail(
                        "TLS handshake completed fully before abort; "
                        "mid-handshake branch was not exercised"
                    )
            self.assertTrue(
                want_read_seen,
                "expected SSLWantReadError after ClientHello "
                "(handshake incomplete) before aborting",
            )

            # Abort while handshake is incomplete — do not finish wrap.
            try:
                ssock.close()
            except OSError:
                pass
            ssock = None
            time.sleep(0.25)
        finally:
            if ssock is not None:
                try:
                    ssock.close()
                except OSError:
                    pass
            if peer is not None:
                try:
                    peer.close()
                except OSError:
                    pass

        status, body = self._https_get("/", timeout=3.0)
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_post_handshake_reset_then_server_still_serves(self):
        """Successful request, then raw connect+RST-style close; server lives."""

        status, body = self._https_get("/", timeout=3.0)
        self.assertEqual(status, 200)
        self.assertTrue(body)

        peer = socket.create_connection(("127.0.0.1", self.port), timeout=2.0)
        try:
            ctx = ssl._create_unverified_context()
            ssock = ctx.wrap_socket(peer, server_hostname="127.0.0.1")
            peer = None  # ownership transferred
            try:
                ssock.sendall(
                    b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Connection: close\r\n\r\n"
                )
                ssock.recv(64)
            finally:
                try:
                    ssock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_LINGER,
                        struct.pack("ii", 1, 0),
                    )
                except OSError:
                    pass
                try:
                    ssock.close()
                except OSError:
                    pass
        finally:
            if peer is not None:
                try:
                    peer.close()
                except OSError:
                    pass

        status2, body2 = self._https_get("/", timeout=3.0)
        self.assertEqual(status2, 200)
        self.assertTrue(body2)

    def test_dual_sequential_https_readiness_and_reload(self):
        """Launcher-shaped proof: readiness GET then second-tab/reload GET.

        Mirrors ``Wait-EasyImportsHttpsReady`` (HTTPS 2xx) followed by a second
        load of ``/`` — the operator failure from 2026-08-12 where the first
        request could succeed and later tabs timed out on the accept-loop wedge.
        """

        status1, body1 = self._https_get("/", timeout=3.0)
        self.assertEqual(status1, 200)
        self.assertTrue(body1)

        status2, body2 = self._https_get("/", timeout=3.0)
        self.assertEqual(status2, 200)
        self.assertTrue(body2)

        time.sleep(0.15)
        status3, body3 = self._https_get("/", timeout=3.0)
        self.assertEqual(status3, 200)
        self.assertTrue(body3)
