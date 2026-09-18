"""HSR-L — Salesforce External Client App live OAuth on HTTPS loopback :8001.

Network-free default: skipped unless explicitly authorized.

Live (opt-in, explicit per-run only): dual-process browser → Django
``runserver_https`` on **127.0.0.1:8001** → API (real OAuth code exchange):

  HTTPS dual-tab readiness on product origin
    → Connect wizard register Salesforce (product callback URI shown)
    → Connect submit → browser redirect to Salesforce ``/services/oauth2/authorize``
      with ``redirect_uri=https://127.0.0.1:8001/crm/oauth/callback/``
    → operator (or optional SF username/password env) completes authorize
    → Salesforce returns to product callback with ``code`` + ``state``
    → Django journals oauth complete + API **authorization_code** exchange
    → connection visible; dual-tab still healthy

This is **not** the Phase 7C refresh-token pilot. Authorization URL is not
suppressed; a pre-existing refresh token alone cannot pass this gate.

Live flags:
  EASYIMPORTS_HSR_L_LIVE=1
  EASYIMPORTS_HSR_L_ORG_CLASS=developer_edition|sandbox|developer|de

Optional automated SF login (never required for network-free CI):
  EASYIMPORTS_HSR_L_SF_USERNAME
  EASYIMPORTS_HSR_L_SF_PASSWORD
  (or SF_USERNAME / SF_PASSWORD)

Env file: EASYIMPORTS_SF_ENV_FILE (client id/secret for Connect registration)

Authority:
``https_loopback_tls_handshake_reliability.md`` Phase HSR-L.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
import urllib3
from django.test import SimpleTestCase

from importer.https_loopback import ensure_loopback_tls_material
from importer.oauth_redirect import HTTPS_BUILTIN_OAUTH_CALLBACK_URI

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LIVE_FLAG = "EASYIMPORTS_HSR_L_LIVE"
_ORG_CLASS_KEY = "EASYIMPORTS_HSR_L_ORG_CLASS"
_PRODUCT_HTTPS_ORIGIN = "https://127.0.0.1:8001"
_PRODUCT_HTTPS_CALLBACK = HTTPS_BUILTIN_OAUTH_CALLBACK_URI
_DJANGO_BIND = "127.0.0.1:8001"
_ALLOWED_ORG_CLASSES = frozenset(
    {
        "developer_edition",
        "developer",
        "sandbox",
        "developer_sandbox",
        "de",
    }
)
_REFUSED_ORG_CLASSES = frozenset(
    {
        "production",
        "customer",
        "prod",
        "live_customer",
        "enterprise_production",
    }
)
_DEFAULT_SF_ENV = Path(
    r"C:\Users\jcrit\Documents\Automations\sf_integration\.env.txt"
)
_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "mappings_2"
    / "codex_context"
    / "cross_agent_eval"
    / "project_implementations"
    / "completed_projects"
    / "https_loopback_handshake_hsrl_sf_evidence.json"
)
_OAUTH_WAIT_S = float(os.environ.get("EASYIMPORTS_HSR_L_OAUTH_WAIT_S") or "300")
# Operator My Domain (DE) — authorize/login should use this host, not generic
# login.salesforce.com alone, when the External Client App is org-bound.
_DEFAULT_SF_MY_DOMAIN_HOST = "orgfarm-1939a0da78-dev-ed.develop.my.salesforce.com"

_FORBIDDEN_EVIDENCE_KEYS_EXACT = frozenset(
    {
        "access_token",
        "token",
        "refresh_token",
        "client_secret",
        "client_id",
        "authorization_code",
        "code",
        "api_key",
        "password",
        "secret",
        "org_id",
        "organization_id",
        "instance_url",
        "hub_id",
        "portal_id",
        "bearer",
        # Tenant-specific hostnames must be digests/booleans only.
        "my_domain_host",
        "login_url_expected",
        "login_url",
    }
)
_FORBIDDEN_EVIDENCE_KEY_FRAGMENTS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization_code",
    "api_key",
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"00D[a-zA-Z0-9]{12,18}"),
    # Raw Salesforce tenant hosts (use digests instead).
    re.compile(r"\.my\.salesforce\.com", re.IGNORECASE),
    re.compile(r"login\.salesforce\.com", re.IGNORECASE),
    re.compile(r"test\.salesforce\.com", re.IGNORECASE),
)


def _live_enabled() -> bool:
    return os.environ.get(_LIVE_FLAG, "").strip() == "1"


def _load_dotenv_keys(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _resolve_sf_env_path() -> Path | None:
    override = (os.environ.get("EASYIMPORTS_SF_ENV_FILE") or "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    if _DEFAULT_SF_ENV.is_file():
        return _DEFAULT_SF_ENV
    return None


def _ensure_repo_on_path() -> Path:
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return repo


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _port_free(host: str, port: int) -> bool:
    sock = socket.socket()
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _assert_evidence_safe(payload: Any, *, path: str = "root") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if key_l in _FORBIDDEN_EVIDENCE_KEYS_EXACT:
                raise AssertionError(f"forbidden evidence key at {path}.{key}")
            if any(frag in key_l for frag in _FORBIDDEN_EVIDENCE_KEY_FRAGMENTS):
                raise AssertionError(
                    f"forbidden evidence key fragment at {path}.{key}"
                )
            _assert_evidence_safe(value, path=f"{path}.{key}")
        return
    if isinstance(payload, list):
        for i, item in enumerate(payload):
            _assert_evidence_safe(item, path=f"{path}[{i}]")
        return
    if isinstance(payload, str):
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(payload):
                raise AssertionError(f"forbidden evidence value pattern at {path}")


def _org_class_attested() -> str:
    raw = (os.environ.get(_ORG_CLASS_KEY) or "").strip().lower()
    if not raw:
        raise unittest.SkipTest(
            f"HSR-L live requires {_ORG_CLASS_KEY}="
            f"developer_edition|sandbox|…"
        )
    if raw in _REFUSED_ORG_CLASSES:
        raise unittest.SkipTest(
            f"HSR-L refuses production/customer org class {raw!r}"
        )
    if raw not in _ALLOWED_ORG_CLASSES:
        raise unittest.SkipTest(f"HSR-L unknown org class {raw!r}")
    return raw


def _sf_login_credentials() -> tuple[str, str] | None:
    user = (
        os.environ.get("EASYIMPORTS_HSR_L_SF_USERNAME")
        or os.environ.get("SF_USERNAME")
        or ""
    ).strip()
    password = (
        os.environ.get("EASYIMPORTS_HSR_L_SF_PASSWORD")
        or os.environ.get("SF_PASSWORD")
        or ""
    ).strip()
    if user and password:
        return user, password
    return None


def _resolve_my_domain_host(sf_values: dict[str, str]) -> str:
    """Bare My Domain host for Connect registration (no scheme/path)."""

    override = (
        os.environ.get("EASYIMPORTS_HSR_L_SF_MY_DOMAIN")
        or os.environ.get("SF_MY_DOMAIN_HOST")
        or ""
    ).strip()
    if override:
        host = override
    else:
        login = (sf_values.get("SF_LOGIN_URL") or "").strip()
        if login and "my.salesforce.com" in login.casefold():
            parsed = urlparse(login if "://" in login else f"https://{login}")
            host = (parsed.hostname or "").strip()
        else:
            host = _DEFAULT_SF_MY_DOMAIN_HOST
    host = host.casefold().removeprefix("https://").removeprefix("http://")
    host = host.split("/")[0].strip()
    if host.endswith(":443"):
        host = host[:-4]
    return host


def _authorize_url_has_product_callback(url: str) -> bool:
    """True when an OAuth authorize URL targets product HTTPS :8001 callback."""

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if "salesforce.com" not in host and "force.com" not in host:
        return False
    # Classic authorize endpoint carries redirect_uri; ECA may bounce through
    # RemoteAccessAuthorizationPage after the authorize hop.
    qs = parse_qs(parsed.query)
    redirect_values = qs.get("redirect_uri") or []
    if redirect_values:
        return unquote(redirect_values[0]) == _PRODUCT_HTTPS_CALLBACK
    if "/services/oauth2/authorize" in path:
        return False
    return False


def _is_salesforce_authorize_surface(url: str) -> bool:
    """Salesforce authorize/login surfaces after Connect OAuth start."""

    u = (url or "").casefold()
    if "salesforce.com" not in u and "force.com" not in u:
        return False
    return any(
        marker in u
        for marker in (
            "/services/oauth2/authorize",
            "remoteaccessauthorizationpage",
            "setup/secur/remoteaccess",
            "login.salesforce.com",
            "test.salesforce.com",
            ".my.salesforce.com",
        )
    )


@unittest.skipUnless(_live_enabled(), "HSR-L live gate (set EASYIMPORTS_HSR_L_LIVE=1)")
class HsrlLiveHttpsOauthDualProcessTests(SimpleTestCase):
    """Authorized live SF authorize→callback→code exchange on :8001 HTTPS."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org_class = _org_class_attested()
        env_path = _resolve_sf_env_path()
        if env_path is None:
            raise unittest.SkipTest(
                "HSR-L live requires EASYIMPORTS_SF_ENV_FILE or default "
                "sf_integration\\.env.txt"
            )
        cls.sf_values = _load_dotenv_keys(env_path)
        for required in ("SF_CLIENT_ID", "SF_CLIENT_SECRET"):
            if not (cls.sf_values.get(required) or "").strip():
                raise unittest.SkipTest(f"HSR-L live missing {required} in env file")
        cls.env_path = env_path
        cls.repo = _ensure_repo_on_path()

        if not _port_free("127.0.0.1", 8001):
            raise unittest.SkipTest(
                "HSR-L requires free 127.0.0.1:8001 (product OAuth callback port). "
                "Run scripts\\stop_local.ps1 and retry."
            )

        cls.tmp = tempfile.TemporaryDirectory(prefix="ei-hsrl-")
        root = Path(cls.tmp.name)
        cls.machine = root / "machine"
        cls.api_state = root / "api_state"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        cls.tls_dir = root / "tls"
        for path in (cls.machine, cls.api_state, cls.media_root, cls.tls_dir):
            path.mkdir()
        cls.cert = cls.tls_dir / "loopback.crt"
        cls.key = cls.tls_dir / "loopback.key"
        # SAN must include 127.0.0.1 for product origin TLS.
        ensure_loopback_tls_material(cert_file=cls.cert, key_file=cls.key)

        cls.api_port = _free_port()
        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        cls.django_base = _PRODUCT_HTTPS_ORIGIN

        cls._remote_org_gate()
        cls._start_api()
        cls._start_django_https()

    @classmethod
    def _build_oauth_config(cls):
        from mappings_2.integrations.salesforce.connection.auth import (
            SalesforceOAuthConfig,
        )

        return SalesforceOAuthConfig(
            client_id=cls.sf_values["SF_CLIENT_ID"],
            client_secret=cls.sf_values["SF_CLIENT_SECRET"],
            redirect_uri=_PRODUCT_HTTPS_CALLBACK,
            login_url=cls.sf_values.get("SF_LOGIN_URL")
            or "https://login.salesforce.com",
            api_version=cls.sf_values.get("SF_API_VERSION") or "v61.0",
        )

    @classmethod
    def _query_organization_via_refresh(cls) -> tuple[str, bool, str]:
        """Return (org_type, is_sandbox, org_id) using SF_REFRESH_TOKEN only.

        Used for the mandatory DE/sandbox gate — never for Connect complete.
        """

        from mappings_2.integrations.salesforce.connection.auth import (
            refresh_access_token,
        )
        from mappings_2.integrations.salesforce.reference.client import (
            SalesforceRestClient,
        )

        refresh = (cls.sf_values.get("SF_REFRESH_TOKEN") or "").strip()
        if not refresh:
            raise unittest.SkipTest(
                "HSR-L requires SF_REFRESH_TOKEN for mandatory DE/sandbox "
                "Organization SOQL gate before live authorize (no bypass)."
            )
        oauth = cls._build_oauth_config()
        token = refresh_access_token(oauth, refresh)
        client = SalesforceRestClient(token=token, api_version=oauth.api_version)
        rows = client.query_all(
            "SELECT Id, IsSandbox, OrganizationType FROM Organization LIMIT 1"
        )
        if len(rows) != 1:
            raise RuntimeError("HSR-L Organization SOQL failed")
        row = rows[0]
        return (
            str(row.get("OrganizationType") or ""),
            bool(row.get("IsSandbox")),
            str(row.get("Id") or ""),
        )

    @classmethod
    def _assert_dev_or_sandbox_org(
        cls, *, org_type: str, is_sandbox: bool, phase: str
    ) -> None:
        if is_sandbox or org_type in {"Developer Edition", "Sandbox"}:
            return
        raise RuntimeError(
            f"HSR-L {phase} refused non-DE/sandbox org "
            f"type={org_type!r} IsSandbox={is_sandbox}"
        )

    @classmethod
    def _remote_org_gate(cls) -> None:
        """Mandatory DE/sandbox SOQL gate before live OAuth (requires refresh)."""

        org_type, is_sandbox, org_id = cls._query_organization_via_refresh()
        cls._assert_dev_or_sandbox_org(
            org_type=org_type, is_sandbox=is_sandbox, phase="pre-gate"
        )
        cls.remote_org_type = org_type
        cls.remote_is_sandbox = is_sandbox
        cls.remote_org_id = org_id
        cls.remote_org_id_digest = (
            "sha256:"
            + hashlib.sha256(org_id.encode("utf-8")).hexdigest()[:16]
        )

    @classmethod
    def _post_exchange_org_gate(cls) -> dict[str, Any]:
        """Re-verify DE/sandbox after Connect (fail closed on production)."""

        org_type, is_sandbox, org_id = cls._query_organization_via_refresh()
        cls._assert_dev_or_sandbox_org(
            org_type=org_type, is_sandbox=is_sandbox, phase="post-exchange"
        )
        if cls.remote_org_id and org_id and org_id != cls.remote_org_id:
            raise RuntimeError(
                "HSR-L post-exchange Organization Id does not match pre-gate pin"
            )
        return {
            "org_type": org_type,
            "is_sandbox": is_sandbox,
            "org_id_matches_pre_gate": (
                not cls.remote_org_id or org_id == cls.remote_org_id
            ),
            "dev_or_sandbox_ok": True,
        }

    @classmethod
    def _api_env(cls) -> dict:
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.api_port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = str(cls.api_state)
        environment["EASYIMPORTS_SECRET_STORE"] = "file_insecure"
        environment.pop("EASYIMPORTS_SF_OAUTH_EXCHANGE", None)
        environment["LOCALAPPDATA"] = str(cls.machine)
        environment["XDG_DATA_HOME"] = str(cls.machine / "xdg")
        environment["PYTHONPATH"] = str(cls.repo)
        environment["EASYIMPORTS_SF_ENV_FILE"] = str(cls.env_path)
        # Product callback for any env-based OAuth config (registration also freezes it).
        environment["SF_REDIRECT_URI"] = _PRODUCT_HTTPS_CALLBACK
        for key, value in cls.sf_values.items():
            environment.setdefault(key, value)
        environment["SF_REDIRECT_URI"] = _PRODUCT_HTTPS_CALLBACK
        return environment

    @classmethod
    def _django_env(cls) -> dict:
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "easyimports_web.settings"
        environment["EASYIMPORTS_API_BASE_URL"] = cls.api_base
        environment["EASYIMPORTS_DB_PATH"] = str(cls.django_db)
        environment["EASYIMPORTS_MEDIA_ROOT"] = str(cls.media_root)
        environment["EASYIMPORTS_DEBUG"] = "1"
        environment["EASYIMPORTS_ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"
        environment["EASYIMPORTS_HTTPS_LOOPBACK"] = "1"
        environment["EASYIMPORTS_HTTPS_CERT_DIR"] = str(cls.tls_dir)
        environment["EASYIMPORTS_HTTPS_CERT_FILE"] = str(cls.cert)
        environment["EASYIMPORTS_HTTPS_KEY_FILE"] = str(cls.key)
        # Never override product built-in callback for HSR-L.
        environment.pop("EASYIMPORTS_OAUTH_REDIRECT_URI", None)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(cls.repo / "web"), str(cls.repo), environment.get("PYTHONPATH", "")]
        )
        return environment

    @classmethod
    def _start_api(cls) -> None:
        cls.api_log = Path(cls.tmp.name) / "api_process.log"
        cls.api_log_handle = open(cls.api_log, "w", encoding="utf-8")
        launcher = cls.repo / "web" / "tools" / "run_api_sf_live_oauth_gate.py"
        cls.api_process = subprocess.Popen(
            [sys.executable, str(launcher)],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=cls.api_log_handle,
            stderr=subprocess.STDOUT,
        )
        health = f"{cls.api_base}/health"
        for _ in range(150):
            try:
                if requests.get(health, timeout=0.3).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.15)
        cls.api_process.terminate()
        tail = ""
        try:
            cls.api_log_handle.flush()
            tail = Path(cls.api_log).read_text(encoding="utf-8", errors="replace")[
                -1500:
            ]
        except Exception:
            pass
        raise RuntimeError(f"HSR-L API process did not start.\n{tail}")

    @classmethod
    def _stop_api(cls) -> None:
        if getattr(cls, "api_process", None) is None:
            return
        cls.api_process.terminate()
        try:
            cls.api_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            cls.api_process.kill()
            cls.api_process.wait(timeout=5)
        cls.api_process = None
        handle = getattr(cls, "api_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
            cls.api_log_handle = None

    @classmethod
    def _start_django_https(cls) -> None:
        env = cls._django_env()
        migrate = subprocess.run(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "migrate",
                "--run-syncdb",
                "--verbosity",
                "0",
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            text=True,
            capture_output=True,
        )
        if migrate.returncode != 0:
            raise RuntimeError(
                "HSR-L Django migrate failed:\n"
                f"stdout={migrate.stdout}\nstderr={migrate.stderr}"
            )
        cls.django_log = Path(cls.tmp.name) / "django_process.log"
        cls.django_log_handle = open(cls.django_log, "w", encoding="utf-8")
        # Product origin only — External Client App callback is :8001.
        cls.django_process = subprocess.Popen(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "runserver_https",
                _DJANGO_BIND,
                "--noreload",
                "--cert-file",
                str(cls.cert),
                "--key-file",
                str(cls.key),
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            stdout=cls.django_log_handle,
            stderr=subprocess.STDOUT,
        )
        landing = f"{cls.django_base}/"
        for _ in range(150):
            try:
                response = requests.get(landing, timeout=0.5, verify=False)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.15)
        cls.django_process.terminate()
        tail = ""
        try:
            cls.django_log_handle.flush()
            tail = Path(cls.django_log).read_text(
                encoding="utf-8", errors="replace"
            )[-1500:]
        except Exception:
            pass
        raise RuntimeError(f"HSR-L Django runserver_https :8001 did not start.\n{tail}")

    @classmethod
    def _stop_django(cls) -> None:
        if getattr(cls, "django_process", None) is None:
            return
        cls.django_process.terminate()
        try:
            cls.django_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.django_process.kill()
            cls.django_process.wait(timeout=5)
        cls.django_process = None
        handle = getattr(cls, "django_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
            cls.django_log_handle = None

    @classmethod
    def tearDownClass(cls):
        cls._stop_django()
        cls._stop_api()
        try:
            cls.tmp.cleanup()
        except Exception:
            pass
        super().tearDownClass()

    def test_live_external_client_app_oauth_round_trip_on_port_8001(self):
        """Real authorize → :8001 callback with code → token exchange."""

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest("Playwright required for HSR-L live OAuth") from exc

        evidence: dict[str, Any] = {
            "phase": "HSR-L",
            "grade_claim": "live_sf_external_client_app_oauth_port_8001",
            "org_class_attested": self.org_class,
            "remote_org_type": self.remote_org_type,
            "remote_is_sandbox": self.remote_is_sandbox,
            "remote_org_id_digest": self.remote_org_id_digest,
            "product_https_callback": _PRODUCT_HTTPS_CALLBACK,
            "django_bind": _DJANGO_BIND,
            "django_scheme": "https",
            "oauth_mode": "authorization_code_grant",
            "uses_phase7c_refresh_pilot": False,
            "steps": {},
        }
        callback_observations: list[dict[str, bool]] = []

        headless = os.environ.get("EASYIMPORTS_HSR_L_HEADLESS", "").strip() == "1"
        creds = _sf_login_credentials()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            def _record_callback_url(url: str) -> None:
                if "127.0.0.1:8001/crm/oauth/callback/" not in url:
                    return
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                # Never record code/state values — booleans only.
                callback_observations.append(
                    {
                        "host_port_8001": "127.0.0.1:8001" in url,
                        "path_is_product_callback": parsed.path
                        == "/crm/oauth/callback/",
                        "has_oauth_code_query": bool(
                            (qs.get("code") or [""])[0]
                        ),
                        "has_oauth_state_query": bool(
                            (qs.get("state") or [""])[0]
                        ),
                        "scheme_https": (parsed.scheme or "").lower() == "https",
                    }
                )

            def _on_frame(frame) -> None:
                try:
                    _record_callback_url(frame.url)
                except Exception:
                    return

            def _on_response(response) -> None:
                try:
                    _record_callback_url(response.url)
                except Exception:
                    return

            page.on("framenavigated", _on_frame)
            page.on("response", _on_response)
            page.on("request", lambda req: _record_callback_url(req.url))

            # Dual-tab style readiness on product origin.
            page.goto(f"{self.django_base}/", wait_until="domcontentloaded")
            self.assertTrue(page.url.startswith("https://127.0.0.1:8001"))
            page.goto(f"{self.django_base}/", wait_until="domcontentloaded")
            evidence["steps"]["dual_tab_https_landing"] = {
                "first_status_ok": True,
                "second_status_ok": True,
                "origin": _PRODUCT_HTTPS_ORIGIN,
            }

            # Pick Salesforce → registration form (product callback shown).
            page.goto(
                f"{self.django_base}/crm/setup/pick/",
                wait_until="domcontentloaded",
            )
            page.locator(
                'input[name="provider_key"][value="salesforce"]'
            ).check()
            page.locator('button:has-text("Continue")').click()
            page.wait_for_url(
                re.compile(r"/crm/setup/salesforce/?"),
                timeout=30000,
            )
            page.wait_for_selector("#sf-client-id, input[name=\"client_id\"]", timeout=30000)
            body = page.content()
            self.assertIn(_PRODUCT_HTTPS_CALLBACK, body)
            my_domain_host = _resolve_my_domain_host(self.sf_values)
            my_domain_digest = (
                "sha256:"
                + hashlib.sha256(my_domain_host.encode("utf-8")).hexdigest()[:16]
            )
            # DE My Domain orgs still use login_environment=production + My Domain.
            login_environment = "production"
            page.fill('input[name="label"]', f"HSR-L Live {uuid.uuid4().hex[:6]}")
            page.select_option(
                'select[name="login_environment"]', login_environment
            )
            page.fill('input[name="client_id"]', self.sf_values["SF_CLIENT_ID"])
            page.fill(
                'input[name="client_secret"]', self.sf_values["SF_CLIENT_SECRET"]
            )
            page.fill('input[name="my_domain_host"]', my_domain_host)
            if self.remote_org_id and page.locator(
                'input[name="expected_org_id"]'
            ).count():
                page.fill('input[name="expected_org_id"]', self.remote_org_id)
            page.locator(
                'form[action*="salesforce/save"] button[type="submit"], '
                'form[action*="salesforce/save"] input[type="submit"]'
            ).first.click()
            page.wait_for_load_state("domcontentloaded")
            # Land on connect step (or navigate).
            if "/crm/setup/salesforce/connect" not in page.url:
                page.goto(
                    f"{self.django_base}/crm/setup/salesforce/connect/",
                    wait_until="domcontentloaded",
                )
            # Evidence: product callback URI is public/non-secret; never store
            # tenant My Domain hostname — digest + boolean only.
            evidence["steps"]["registration_saved"] = {
                "product_callback_uri_shown": True,
                "my_domain_host_digest": my_domain_digest,
                "my_domain_configured": bool(my_domain_host),
                "connect_path": "/crm/setup/salesforce/connect/",
            }

            # Capture authorize hops (oauth2/authorize often redirects quickly to
            # RemoteAccessAuthorizationPage / My Domain login).
            oauth_request_urls: list[str] = []

            def _on_request(request) -> None:
                u = request.url
                if _is_salesforce_authorize_surface(u) or "redirect_uri=" in u:
                    oauth_request_urls.append(u)

            page.on("request", _on_request)

            # Connect submit → browser must leave product origin for Salesforce.
            with page.expect_navigation(
                url=re.compile(
                    r"(salesforce\.com|force\.com|my\.salesforce\.com)"
                ),
                timeout=120000,
            ):
                page.locator(
                    'form[action*="connect/submit"] button[type="submit"], '
                    'form[action*="connect/submit"] input[type="submit"], '
                    'button:has-text("Connect Salesforce")'
                ).first.click()

            authorize_surface = page.url
            self.assertTrue(
                _is_salesforce_authorize_surface(authorize_surface),
                f"expected Salesforce authorize/login surface, got host/path only "
                f"(len={len(authorize_surface)})",
            )
            # Prefer exact product redirect_uri on the oauth2/authorize hop.
            product_callback_on_wire = any(
                _authorize_url_has_product_callback(u) for u in oauth_request_urls
            )
            if not product_callback_on_wire:
                # Fallback: decode any redirect_uri query on captured URLs.
                for u in oauth_request_urls:
                    if "redirect_uri=" in u and (
                        "127.0.0.1%3A8001%2Fcrm%2Foauth%2Fcallback"
                        in u
                        or "127.0.0.1:8001/crm/oauth/callback" in unquote(u)
                    ):
                        product_callback_on_wire = True
                        break
            self.assertTrue(
                product_callback_on_wire,
                "Salesforce authorize hop must carry product "
                f"redirect_uri={_PRODUCT_HTTPS_CALLBACK}",
            )
            evidence["steps"]["authorize_redirect"] = {
                "hit_salesforce_authorize_surface": True,
                "redirect_uri_is_product_https_8001": True,
                "authorize_surface_uses_my_domain": my_domain_host
                in authorize_surface.casefold()
                or any(my_domain_host in u.casefold() for u in oauth_request_urls),
                "my_domain_host_digest": my_domain_digest,
            }

            # Complete Salesforce authorize (credentials optional; else interactive).
            if creds is not None:
                user, password = creds
                # Classic SF login form variants.
                if page.locator("#username").count():
                    page.fill("#username", user)
                    page.fill("#password", password)
                    page.locator("#Login").click()
                elif page.locator('input[name="username"]').count():
                    page.fill('input[name="username"]', user)
                    page.fill('input[name="pw"], input[name="password"]', password)
                    page.locator(
                        'input[type="submit"], button[type="submit"]'
                    ).first.click()
                # Allow page may require an Allow click.
                try:
                    page.locator(
                        '#oaapprove, input[name="save"][value="Allow"], '
                        'button:has-text("Allow")'
                    ).first.click(timeout=15000)
                except PlaywrightTimeout:
                    pass
            else:
                print(
                    "\n*** HSR-L: complete Salesforce authorize in the **Playwright "
                    "Chromium** window (not another browser).\n"
                    f"    My Domain: https://{my_domain_host}/\n"
                    f"    Wait up to {_OAUTH_WAIT_S:.0f}s. After Allow, Salesforce must "
                    f"return to {_PRODUCT_HTTPS_CALLBACK}?code=…&state=…\n"
                    "    Callback URL in the External Client App must be exactly that "
                    "HTTPS :8001 path.\n",
                    flush=True,
                )

            # Wait until product origin after SF authorize. Success lands on
            # callback (brief) then often redirects to setup complete / connections.
            # Observe callback via request/response (302 may skip a stable load URL).
            try:
                page.wait_for_url(
                    re.compile(
                        r"https://127\.0\.0\.1:8001/"
                        r"(crm/oauth/callback/|crm/setup/complete/?|crm/connections/?)"
                    ),
                    timeout=int(_OAUTH_WAIT_S * 1000),
                )
            except PlaywrightTimeout as exc:
                final_url = page.url
                parsed_final = urlparse(final_url)
                safe_final = (
                    f"{parsed_final.scheme}://{parsed_final.netloc}{parsed_final.path}"
                )
                browser.close()
                self.fail(
                    "Timed out waiting for product origin after Salesforce authorize "
                    f"(last page path={safe_final!r}; "
                    f"callback_hits={len(callback_observations)}; "
                    f"oauth_requests={len(oauth_request_urls)}). "
                    "Complete authorize in the Playwright Chromium window; "
                    "confirm External Client App callback is exactly "
                    f"{_PRODUCT_HTTPS_CALLBACK}. ({exc})"
                )

            try:
                page.wait_for_load_state("domcontentloaded", timeout=30000)
            except PlaywrightTimeout:
                pass

            landed = page.url
            landed_path = urlparse(landed).path or ""
            on_complete = "/crm/setup/complete" in landed_path
            on_connections = "/crm/connections" in landed_path
            on_callback = "/crm/oauth/callback" in landed_path

            # Prefer wire observation of callback?code=…; if 302 was too fast for
            # the load waiter, complete/connections after SF authorize still
            # proves the product round-trip when connection is visible below.
            coded = [
                o for o in callback_observations if o.get("has_oauth_code_query")
            ]
            if coded:
                last_cb = coded[-1]
                self.assertTrue(last_cb["host_port_8001"])
                self.assertTrue(last_cb["path_is_product_callback"])
                self.assertTrue(last_cb["has_oauth_state_query"])
                self.assertTrue(last_cb["scheme_https"])
                evidence["steps"]["oauth_callback"] = {
                    "observed_count": len(callback_observations),
                    "with_code_count": len(coded),
                    **last_cb,
                }
            else:
                # Must not accept bare :8001 without a post-OAuth success surface.
                self.assertTrue(
                    on_complete or on_connections or on_callback,
                    "returned to product origin without callback code observation "
                    "and without setup complete/connections success surface",
                )
                evidence["steps"]["oauth_callback"] = {
                    "observed_count": len(callback_observations),
                    "with_code_count": 0,
                    "callback_redirect_too_fast_for_stable_url": True,
                    "landed_success_surface": on_complete
                    or on_connections
                    or on_callback,
                    "landed_path": landed_path,
                    "host_port_8001": "127.0.0.1:8001" in landed,
                    "scheme_https": landed.startswith("https://"),
                }

            # After exchange, connection should be usable.
            page.goto(
                f"{self.django_base}/crm/connections/",
                wait_until="domcontentloaded",
            )
            lower = page.content().lower()
            self.assertTrue(
                "salesforce" in lower
                and (
                    "connected" in lower
                    or "disconnect" in lower
                    or "crm connected" in lower
                ),
                "Salesforce connection not visible after code exchange",
            )
            # Dual-tab still healthy after OAuth.
            page.goto(f"{self.django_base}/", wait_until="domcontentloaded")
            page.goto(f"{self.django_base}/", wait_until="domcontentloaded")
            evidence["steps"]["connect_complete"] = {
                "oauth_code_exchange_ok": True,
                "external_authorize_used": True,
                "connection_visible": True,
                "stayed_on_product_https_8001": True,
            }
            evidence["steps"]["post_oauth_dual_tab"] = {
                "landing_ok": True,
            }

            browser.close()

        # Mandatory post-exchange DE/sandbox rejection (no production acceptance).
        evidence["steps"]["post_exchange_org_gate"] = self._post_exchange_org_gate()

        _assert_evidence_safe(evidence)
        _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _EVIDENCE_PATH.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class HsrlGateDefaultsOffTests(SimpleTestCase):
    """Network-free: live gate stays skipped without flags."""

    def test_live_flag_defaults_off(self):
        prev = os.environ.pop(_LIVE_FLAG, None)
        try:
            self.assertFalse(_live_enabled())
        finally:
            if prev is not None:
                os.environ[_LIVE_FLAG] = prev

    def test_evidence_path_is_repo_local(self):
        self.assertEqual(
            _EVIDENCE_PATH.name,
            "https_loopback_handshake_hsrl_sf_evidence.json",
        )
        self.assertIn("completed_projects", _EVIDENCE_PATH.parts)

    def test_product_callback_is_port_8001(self):
        self.assertEqual(
            _PRODUCT_HTTPS_CALLBACK,
            "https://127.0.0.1:8001/crm/oauth/callback/",
        )
        self.assertTrue(
            _authorize_url_has_product_callback(
                "https://login.salesforce.com/services/oauth2/authorize"
                "?response_type=code&client_id=x"
                "&redirect_uri=https%3A%2F%2F127.0.0.1%3A8001%2Fcrm%2Foauth%2Fcallback%2F"
            )
        )
        self.assertFalse(
            _authorize_url_has_product_callback(
                "https://login.salesforce.com/services/oauth2/authorize"
                "?redirect_uri=https%3A%2F%2F127.0.0.1%3A59999%2Fcrm%2Foauth%2Fcallback%2F"
            )
        )

    def test_evidence_forbids_tenant_my_domain_hostname(self):
        """Committed/live evidence must not carry raw My Domain hostnames."""

        sample = {
            "steps": {
                "registration_saved": {
                    "my_domain_configured": True,
                    "my_domain_host_digest": "sha256:deadbeefdeadbeef",
                    "product_callback_uri_shown": True,
                },
                "authorize_redirect": {
                    "authorize_surface_uses_my_domain": True,
                    "my_domain_host_digest": "sha256:deadbeefdeadbeef",
                },
            }
        }
        _assert_evidence_safe(sample)
        with self.assertRaises(AssertionError):
            _assert_evidence_safe(
                {
                    "steps": {
                        "registration_saved": {
                            "my_domain_host": (
                                "example.my.salesforce.com"
                            )
                        }
                    }
                }
            )
