from __future__ import annotations

"""HTTPS loopback runserver for EasyImports (HTTPS-2 / HSR-1).

Default bind: ``127.0.0.1:8001`` with **per-machine** TLS material
(``%LOCALAPPDATA%\\EasyImports\\tls`` or ``EASYIMPORTS_HTTPS_CERT_DIR``).
Sets session cookies to Secure + SameSite=Lax and reports ``request.is_secure()``.

TLS handshakes run **per connection** in the worker thread (never inside the
accept loop), so a stalled or speculative client cannot wedge other browsers.
"""

import logging
import os
import socket
import ssl
import sys
from datetime import datetime

from django.core.management.commands.runserver import Command as RunserverCommand
from django.core.servers.basehttp import WSGIRequestHandler, WSGIServer

from importer.https_loopback import (
    HTTPS_LOOPBACK_ENV,
    LOOPBACK_HOST,
    LOOPBACK_ORIGIN,
    LOOPBACK_PORT,
    apply_https_loopback_runtime_settings,
    require_tls_material,
)

logger = logging.getLogger("django.server")

# Flat connection timeout for the dev loopback server (handshake + idle reads).
# Acceptable for local OAuth/UI; not a production TLS terminator.
_CONNECTION_TIMEOUT_S = 30


class SecureWSGIRequestHandler(WSGIRequestHandler):
    """Force HTTPS WSGI environ so Django ``request.is_secure()`` is true."""

    def get_environ(self):
        env = super().get_environ()
        env["wsgi.url_scheme"] = "https"
        env["HTTPS"] = "on"
        return env


class SecureWSGIServer(WSGIServer):
    """WSGI server with per-connection TLS handshake off the accept loop.

    Extend plain ``WSGIServer`` (not ``ThreadedWSGIServer``) so Django can still
    compose ``ThreadingMixIn`` when ``use_threading`` is true. Accept plain TCP;
    wrap each accepted socket and let the worker thread complete the handshake
    on first read (``do_handshake_on_connect=False``).
    """

    certfile: str = ""
    keyfile: str = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Django's run() always passes WSGIRequestHandler; replace after init.
        self.RequestHandlerClass = SecureWSGIRequestHandler
        # Fail closed if cert/key were not set by Command.handle before the
        # server was constructed.
        if not self.certfile or not self.keyfile:
            raise ValueError("SecureWSGIServer requires certfile and keyfile")
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ssl_context.load_cert_chain(self.certfile, self.keyfile)

    def get_request(self):
        # Plain TCP accept — never blocks on a client TLS handshake.
        sock, addr = self.socket.accept()
        # Bounds stalled handshakes (and idle keep-alive reads) for this peer.
        sock.settimeout(_CONNECTION_TIMEOUT_S)
        ssock = self._ssl_context.wrap_socket(
            sock,
            server_side=True,
            do_handshake_on_connect=False,
        )
        return ssock, addr

    def handle_error(self, request, client_address):
        """Label known-benign connection closures distinctly; traceback real bugs.

        All three labeled cases below are pre-existing, already-swallowed
        behavior (no change in what is caught or how loud it is) -- this only
        splits one generic message into three so the common "browser threw
        away a spare connection" case reads differently from an idle
        keep-alive timing out or a peer resetting the socket.
        """

        exc_type, exc_value, _exc_tb = sys.exc_info()
        if exc_type is None:
            super().handle_error(request, client_address)
            return

        if issubclass(exc_type, ssl.SSLError):
            # The client (browser) sent a TLS alert (or dropped the socket
            # outright) before completing the handshake. This is the normal
            # signature of a browser discarding a speculative/extra
            # connection it opened but did not end up needing -- expected,
            # not a failure of this server or its certificate.
            logger.info(
                "HTTPS loopback: browser discarded an unused connection from "
                "%s (%s) -- expected, not an error.",
                client_address,
                exc_value,
            )
            return

        if issubclass(exc_type, (TimeoutError, socket.timeout)):
            # No bytes arrived within the bounded window -- an idle
            # keep-alive connection or a stalled handshake, either way
            # released deliberately by _CONNECTION_TIMEOUT_S, not a hang.
            logger.info(
                "HTTPS loopback: idle connection from %s timed out after "
                "%ss -- expected bound, not an error.",
                client_address,
                _CONNECTION_TIMEOUT_S,
            )
            return

        if issubclass(
            exc_type,
            (ConnectionResetError, ConnectionAbortedError, BrokenPipeError),
        ):
            # The peer (browser tab closed/navigated away, or OS torn down
            # the socket) reset the connection from its side.
            logger.info(
                "HTTPS loopback: peer reset connection from %s (%s) -- "
                "expected on tab close/navigation, not an error.",
                client_address,
                exc_type.__name__,
            )
            return

        super().handle_error(request, client_address)

    def shutdown_request(self, request):
        try:
            # SSL and half-open peers can raise on shutdown; never traceback here.
            request.shutdown(socket.SHUT_WR)
        except (OSError, ssl.SSLError):
            pass
        self.close_request(request)


class Command(RunserverCommand):
    help = (
        "Run Django with TLS on https://127.0.0.1:8001 for External Client App "
        "OAuth callbacks (HTTPS-2 loopback). Does not flip product redirect default."
    )
    protocol = "https"

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--cert-file",
            dest="cert_file",
            default=None,
            help="TLS certificate PEM (default: per-machine EasyImports/tls).",
        )
        parser.add_argument(
            "--key-file",
            dest="key_file",
            default=None,
            help="TLS private key PEM (default: per-machine EasyImports/tls).",
        )

    def handle(self, *args, **options):
        os.environ.setdefault(HTTPS_LOOPBACK_ENV, "1")
        apply_https_loopback_runtime_settings()

        cert, key = require_tls_material(
            cert_file=options.get("cert_file"),
            key_file=options.get("key_file"),
            ensure=True,
        )
        SecureWSGIServer.certfile = str(cert)
        SecureWSGIServer.keyfile = str(key)
        self.server_cls = SecureWSGIServer

        if not options.get("addrport"):
            options["addrport"] = f"{LOOPBACK_HOST}:{LOOPBACK_PORT}"

        self.stdout.write(
            self.style.SUCCESS(
                f"EasyImports HTTPS loopback: {LOOPBACK_ORIGIN}/ "
                f"(cert={cert})"
            )
        )
        self.stdout.write(
            "Session cookies: Secure + SameSite=Lax. "
            "Open the entire Connect UI on this origin (no tunnel mix)."
        )
        self.stdout.write(
            "TLS keys are per-machine only; do not commit or share private keys."
        )
        super().handle(*args, **options)

    def on_bind(self, server_port):
        quit_command = "CTRL-BREAK" if os.name == "nt" else "CONTROL-C"
        now = datetime.now().strftime("%B %d, %Y - %X")
        self.stdout.write(now)
        self.stdout.write(
            (
                "Django version {version}, using settings {settings!r}\n"
                "Starting HTTPS development server at {origin}/\n"
                "Quit the server with {quit}."
            ).format(
                version=__import__("django").get_version(),
                settings=os.environ.get("DJANGO_SETTINGS_MODULE"),
                origin=LOOPBACK_ORIGIN,
                quit=quit_command,
            )
        )
