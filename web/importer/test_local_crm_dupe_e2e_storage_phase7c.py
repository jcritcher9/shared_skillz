"""Phase 7C — Salesforce Account live merge via true frontend dual-process path.

Network-free: gate defaults off + org attestation + recursive evidence safety.

Live (opt-in): browser → Django → API (refresh-token live launcher) on isolated
durable root:

  SF Connected App register (Connect wizard)
    → Connect org (opaque complete via refresh-token exchange)
    → seed uniquely marked synthetic Accounts (independent REST)
    → CRM Duplicates / Companies upload + Record ID map
    → frontend review (approve 1, decline rest)
    → freeze → dry_run (zero Account deletes; modstamps unchanged)
    → execute one merge → independent SOQL verify
    → cleanup remaining synthetic Accounts
    → sanitized evidence

Live flags:
  EASYIMPORTS_CRM_DUPE_STORAGE_7C_LIVE=1
  EASYIMPORTS_CRM_DUPE_7C_SF_LIVE=1

Required attestation before seed:
  EASYIMPORTS_CRM_DUPE_STORAGE_7C_ORG_CLASS=developer_edition|sandbox|developer
  + remote Organization SOQL DE/sandbox gate

Env file: EASYIMPORTS_SF_ENV_FILE (default sf_integration\\.env.txt)

Authority: local_crm_dupe_e2e_storage_and_operator_path.md Phase 7C
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from typing import Any

import requests
from django.test import SimpleTestCase

from .crm_duplicate_merge_copy import (
    APPROVE_MERGE_PLAN_LABEL,
    AUTHORIZE_MERGE_STEP_LABEL,
    html_shows_unfrozen_merge_plan,
)
from .pending_copy import html_has_pending_mutation_surface


MARKER = "ei-crmdupe-7c-storage-synthetic-01"
_LIVE_FLAG = "EASYIMPORTS_CRM_DUPE_STORAGE_7C_LIVE"
_LIVE_SF = "EASYIMPORTS_CRM_DUPE_7C_SF_LIVE"
_ORG_CLASS_KEYS = (
    "EASYIMPORTS_CRM_DUPE_STORAGE_7C_ORG_CLASS",
    "EASYIMPORTS_SF_ORG_CLASS",
)
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
PAIR_COUNT = 2
MERGE_PAIR_INDEX = 0

_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "mappings_2"
    / "codex_context"
    / "cross_agent_eval"
    / "project_implementations"
    / "completed_projects"
    / "local_crm_dupe_e2e_storage_phase7c_sf_evidence.json"
)

_FORBIDDEN_EVIDENCE_KEYS_EXACT = frozenset(
    {
        "access_token",
        "token",
        "refresh_token",
        "client_secret",
        "client_id",
        "authorization_code",
        "api_key",
        "password",
        "secret",
        "org_id",
        "organization_id",
        "instance_url",
        "hub_id",
        "portal_id",
        "bearer",
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
)


def _live_enabled() -> bool:
    return any(
        os.environ.get(flag, "").strip() == "1" for flag in (_LIVE_FLAG, _LIVE_SF)
    )


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


def _digest_ids(ids: list[str]) -> str:
    material = "|".join(sorted(str(i) for i in ids))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _pair_name(index: int) -> str:
    return f"EI 7C Holdings{index:03d} ({MARKER})"


def _pair_website(index: int) -> str:
    return f"https://ei-7c-p{index:03d}.example.com"


def _csv_for_ids(ids: list[str], *, names: list[str] | None = None) -> bytes:
    lines = ["Id,Name"]
    for i, rid in enumerate(ids):
        name = names[i] if names is not None else rid
        lines.append(f"{rid},{name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _resolve_org_class(values: dict[str, str]) -> str:
    for key in _ORG_CLASS_KEYS:
        raw = str(values.get(key) or os.environ.get(key) or "").strip().lower()
        if raw:
            return raw.replace(" ", "_").replace("-", "_")
    return ""


def _build_authorization_scope(*, org_class: str) -> dict[str, Any]:
    return {
        "live_gate_flags": sorted([_LIVE_FLAG, _LIVE_SF]),
        "org_class": org_class,
        "org_attestation": "operator_declared_non_production",
        "production_customer_orgs": False,
        "scope": {
            "entity_family": "company",
            "object": "Account",
            "modes": ["dry_run", "execute"],
            "synthetic_marker_only": True,
            "marker_prefix": "ei-crmdupe-7c-storage",
            "person_merge_execute": False,
            "path": "frontend_dual_process",
        },
    }


class LiveGateAuthorizationError(RuntimeError):
    pass


def require_live_org_authorization(values: dict[str, str]) -> dict[str, Any]:
    org_class = _resolve_org_class(values)
    if not org_class:
        raise LiveGateAuthorizationError(
            "Live 7C refused: missing non-production org attestation. "
            "Set EASYIMPORTS_CRM_DUPE_STORAGE_7C_ORG_CLASS to one of "
            f"{sorted(_ALLOWED_ORG_CLASSES)}."
        )
    if org_class in _REFUSED_ORG_CLASSES:
        raise LiveGateAuthorizationError(
            f"Live 7C refused org_class={org_class!r}: production/customer orgs "
            "are not authorized."
        )
    if org_class not in _ALLOWED_ORG_CLASSES:
        raise LiveGateAuthorizationError(
            f"Live 7C refused org_class={org_class!r}: must be one of "
            f"{sorted(_ALLOWED_ORG_CLASSES)}."
        )
    return _build_authorization_scope(org_class=org_class)


def assert_remote_org_is_de_or_sandbox(*, org_type: str, is_sandbox: bool) -> None:
    if is_sandbox or org_type in {"Developer Edition", "Sandbox"}:
        return
    raise LiveGateAuthorizationError(
        f"Live 7C refused production org type={org_type!r} "
        f"IsSandbox={is_sandbox}."
    )


def _forbidden_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    if not lowered:
        return False
    if lowered in _FORBIDDEN_EVIDENCE_KEYS_EXACT:
        return True
    if lowered.endswith("_digest"):
        return False
    return any(fragment in lowered for fragment in _FORBIDDEN_EVIDENCE_KEY_FRAGMENTS)


def _forbidden_string_value(value: str) -> str | None:
    text = str(value)
    if not text:
        return None
    for pattern in _FORBIDDEN_VALUE_PATTERNS:
        if pattern.search(text):
            return f"matches forbidden pattern {pattern.pattern!r}"
    if text.startswith("sha256:") or text.startswith("ei-"):
        return None
    if len(text) >= 40 and re.fullmatch(r"[A-Za-z0-9+/=_-]{40,}", text):
        if "_" not in text and "-" not in text[4:]:
            return "looks like an opaque secret/token value"
    return None


def assert_evidence_safe(obj: Any, *, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = f"{path}.{key}"
            if _forbidden_key(str(key)):
                raise AssertionError(f"Evidence refuses forbidden key at {key_path}")
            assert_evidence_safe(value, path=key_path)
        return
    if isinstance(obj, list):
        for index, item in enumerate(obj):
            assert_evidence_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(obj, str):
        reason = _forbidden_string_value(obj)
        if reason is not None:
            raise AssertionError(
                f"Evidence refuses forbidden value at {path}: {reason}"
            )


def _write_evidence(payload: dict) -> None:
    _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = json.loads(json.dumps(payload))
    assert_evidence_safe(safe)
    text = json.dumps(safe, indent=2, sort_keys=True) + "\n"
    assert_evidence_safe(json.loads(text))
    _EVIDENCE_PATH.write_text(text, encoding="utf-8")


def _ensure_repo_on_path() -> Path:
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return repo


def _build_rest_client(values: dict[str, str]):
    _ensure_repo_on_path()
    from mappings_2.integrations.salesforce.connection.auth import (
        SalesforceOAuthConfig,
        refresh_access_token,
    )
    from mappings_2.integrations.salesforce.reference.client import (
        SalesforceRestClient,
    )

    redirect_uri = (
        values.get("SF_REDIRECT_URI")
        or "https://127.0.0.1:8001/crm/oauth/callback/"
    )
    oauth = SalesforceOAuthConfig(
        client_id=values["SF_CLIENT_ID"],
        client_secret=values["SF_CLIENT_SECRET"],
        redirect_uri=redirect_uri,
        login_url=values.get("SF_LOGIN_URL") or "https://login.salesforce.com",
        api_version=values.get("SF_API_VERSION") or "v61.0",
    )
    token = refresh_access_token(oauth, values["SF_REFRESH_TOKEN"])
    return SalesforceRestClient(
        token=token, api_version=oauth.api_version
    ), oauth


def _create_account(client, fields: dict[str, Any]) -> str:
    url = (
        f"{client.token.instance_url}/services/data/"
        f"{client.api_version}/sobjects/Account"
    )
    response = requests.post(
        url, json=fields, headers=client.headers, timeout=60
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            f"Account create HTTP {response.status_code}: {response.text[:300]}"
        )
    payload = response.json()
    rid = str(payload.get("id") or "")
    if not rid:
        raise RuntimeError(f"Account create missing id: {payload!r}")
    return rid


def _delete_account(client, record_id: str) -> None:
    url = (
        f"{client.token.instance_url}/services/data/"
        f"{client.api_version}/sobjects/Account/{record_id}"
    )
    response = requests.delete(url, headers=client.headers, timeout=60)
    if response.status_code not in {200, 204, 404}:
        raise RuntimeError(
            f"Account delete HTTP {response.status_code}: {response.text[:200]}"
        )


def _read_account_headers(client, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    # SOQL IN clause with quoted ids.
    quoted = ",".join(f"'{rid}'" for rid in ids)
    soql = (
        "SELECT Id, IsDeleted, MasterRecordId, SystemModstamp "
        f"FROM Account WHERE Id IN ({quoted})"
    )
    rows = client.query_all_including_deleted(soql)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = str(row.get("Id") or "")
        if not rid:
            continue
        out[rid] = {
            "is_deleted": bool(row.get("IsDeleted")),
            "master_record_id": str(row.get("MasterRecordId") or "") or None,
            "system_modstamp": str(row.get("SystemModstamp") or ""),
        }
    return out


class LocalCrmDupeStoragePhase7cNetworkFreeTests(SimpleTestCase):
    databases = set()

    def test_live_gate_defaults_off(self):
        self.assertFalse(_live_enabled())
        self.assertTrue(MARKER.startswith("ei-crmdupe-7c-storage"))

    def test_evidence_path_is_repo_local(self):
        self.assertEqual(
            _EVIDENCE_PATH.name,
            "local_crm_dupe_e2e_storage_phase7c_sf_evidence.json",
        )
        self.assertIn("completed_projects", _EVIDENCE_PATH.parts)

    def test_org_authorization_requires_attestation(self):
        with self.assertRaises(LiveGateAuthorizationError):
            require_live_org_authorization({})
        with self.assertRaises(LiveGateAuthorizationError):
            require_live_org_authorization(
                {"EASYIMPORTS_CRM_DUPE_STORAGE_7C_ORG_CLASS": "production"}
            )

    def test_org_authorization_accepts_de_sandbox(self):
        for org_class in ("developer_edition", "sandbox", "developer"):
            auth = require_live_org_authorization(
                {"EASYIMPORTS_CRM_DUPE_STORAGE_7C_ORG_CLASS": org_class}
            )
            self.assertEqual(auth["org_class"], org_class)
            self.assertFalse(auth["production_customer_orgs"])

    def test_evidence_safe_rejects_nested_secrets(self):
        with self.assertRaises(AssertionError):
            assert_evidence_safe({"steps": {"nested": {"refresh_token": "x"}}})
        with self.assertRaises(AssertionError):
            assert_evidence_safe({"org_id": "00Dxx0000000001"})
        assert_evidence_safe(
            {
                "org_id_digest": "sha256:deadbeefcafef00d",
                "authorization": _build_authorization_scope(
                    org_class="developer_edition"
                ),
            }
        )


@unittest.skipUnless(
    _live_enabled(),
    f"Live CRM-dupe storage 7C SF gate disabled "
    f"(set {_LIVE_FLAG}=1 or {_LIVE_SF}=1).",
)
class LocalCrmDupeStoragePhase7cLiveDualProcessTests(SimpleTestCase):
    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.tmp = tempfile.TemporaryDirectory(prefix="ei_web_p7c_")
        root = Path(cls.tmp.name)
        cls.api_state = root / "api_state"
        cls.machine = root / "LocalAppData"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        cls.api_state.mkdir(parents=True, exist_ok=True)
        cls.machine.mkdir(parents=True, exist_ok=True)
        cls.media_root.mkdir(parents=True, exist_ok=True)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.api_port = sock.getsockname()[1]
        sock.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.django_port = sock.getsockname()[1]
        sock.close()

        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        cls.django_base = f"http://127.0.0.1:{cls.django_port}"
        cls._start_api()
        cls._start_django()

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
        env_path = _resolve_sf_env_path()
        if env_path is not None:
            environment["EASYIMPORTS_SF_ENV_FILE"] = str(env_path)
            # Load SF keys into API process env for the launcher.
            for key, value in _load_dotenv_keys(env_path).items():
                environment.setdefault(key, value)
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
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(cls.repo / "web"), str(cls.repo), environment.get("PYTHONPATH", "")]
        )
        return environment

    @classmethod
    def _start_api(cls) -> None:
        cls.api_log = Path(cls.tmp.name) / "api_process.log"
        cls.api_log_handle = open(cls.api_log, "w", encoding="utf-8")
        launcher = cls.repo / "web" / "tools" / "run_api_sf_live_refresh_gate.py"
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
        raise RuntimeError(f"Phase 7C API process did not start.\n{tail}")

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
    def _start_django(cls) -> None:
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
                "Phase 7C Django migrate failed:\n"
                f"stdout={migrate.stdout}\nstderr={migrate.stderr}"
            )
        cls.django_process = subprocess.Popen(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "runserver",
                f"127.0.0.1:{cls.django_port}",
                "--noreload",
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        landing = f"{cls.django_base}/"
        for _ in range(120):
            try:
                response = requests.get(landing, timeout=0.25)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.django_process.terminate()
        raise RuntimeError("Phase 7C Django runserver did not start.")

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

    @classmethod
    def tearDownClass(cls):
        cls._stop_django()
        cls._stop_api()
        cls.tmp.cleanup()
        super().tearDownClass()

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "Phase7CStorageE2E/1.0"})
        return session

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.django_base + path

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "form_token missing")
        start += len(marker)
        end = html.find('"', start)
        return html[start:end]

    def _extract_csrf(self, html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "csrf missing")
        start += len(marker)
        end = html.find('"', start)
        return html[start:end]

    def _extract_hidden(self, html: str, name: str) -> str:
        pattern = re.compile(
            rf'name="{re.escape(name)}"\s+value="([^"]*)"',
            re.IGNORECASE,
        )
        match = pattern.search(html)
        if match:
            return match.group(1)
        pattern = re.compile(
            rf'value="([^"]*)"\s+name="{re.escape(name)}"',
            re.IGNORECASE,
        )
        match = pattern.search(html)
        self.assertIsNotNone(match, f"hidden {name!r} missing")
        return match.group(1)

    def _session_id_from_url(self, url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        self.assertIsNotNone(match, url)
        return match.group(1)

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
        self.assertIsNotNone(match, "no connected CRM on duplicates start page")
        return match.group(1)

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=90, allow_redirects=True)
        self.assertIn(response.status_code, {200, 302}, response.text[:500])
        return response

    def _post_form(
        self,
        browser: requests.Session,
        path: str,
        data: dict,
        *,
        files: dict | None = None,
        allow_redirects: bool = True,
        timeout: float = 180,
    ) -> requests.Response:
        payload = dict(data)
        if "csrfmiddlewaretoken" not in payload:
            prior = browser.get(self._url(path), timeout=30, allow_redirects=True)
            if prior.status_code == 200 and "csrfmiddlewaretoken" in prior.text:
                payload["csrfmiddlewaretoken"] = self._extract_csrf(prior.text)
            elif "csrftoken" in browser.cookies:
                payload["csrfmiddlewaretoken"] = browser.cookies["csrftoken"]
        headers = {}
        if "csrftoken" in browser.cookies:
            headers["X-CSRFToken"] = browser.cookies["csrftoken"]
            headers["Referer"] = self._url(path)
        return browser.post(
            self._url(path),
            data=payload,
            files=files,
            headers=headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )

    def _register_and_connect_salesforce(
        self,
        browser: requests.Session,
        *,
        values: dict[str, str],
        org_id: str,
    ) -> None:
        pick = self._get(browser, "/crm/setup/")
        if "crm/setup/pick" not in pick.url and "Choose a CRM" not in pick.text:
            pick = self._get(browser, "/crm/setup/pick/")
        choose = self._post_form(
            browser,
            "/crm/setup/choose/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(pick.text),
                "form_token": self._extract_form_token(pick.text),
                "provider_key": "salesforce",
            },
        )
        sf_page = (
            choose
            if "client_id" in choose.text or "Consumer Key" in choose.text
            else self._get(browser, "/crm/setup/salesforce/")
        )
        login_url = values.get("SF_LOGIN_URL") or ""
        login_environment = (
            "sandbox" if "test.salesforce" in login_url else "production"
        )
        # login_environment "production" login.salesforce.com is still OK for DE;
        # remote org SOQL gate enforces DE/sandbox.
        saved = self._post_form(
            browser,
            "/crm/setup/salesforce/save/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(sf_page.text),
                "form_token": self._extract_form_token(sf_page.text),
                "label": f"7C Storage Live {uuid.uuid4().hex[:6]}",
                "login_environment": login_environment,
                "client_id": values["SF_CLIENT_ID"],
                "client_secret": values["SF_CLIENT_SECRET"],
                "my_domain_host": "",
                "expected_org_id": org_id,
            },
            timeout=120,
        )
        if "Connect Salesforce" not in saved.text:
            saved = self._get(browser, "/crm/setup/salesforce/connect/")
        self.assertIn("form_token", saved.text)
        connected = self._post_form(
            browser,
            "/crm/setup/salesforce/connect/submit/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(saved.text),
                "form_token": self._extract_form_token(saved.text),
            },
            timeout=120,
        )
        # Must not bounce to external Salesforce authorize (refresh shim).
        self.assertNotIn("salesforce.com/services/oauth2", connected.url)
        connections = self._get(browser, "/crm/connections/")
        lower = (connected.text + connections.text).lower()
        self.assertTrue(
            "salesforce" in lower
            and ("connected" in lower or "disconnect" in lower),
            connections.text[:800],
        )
        start = self._get(browser, "/crm/duplicate-journeys/")
        self.assertIsNotNone(
            re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', start.text),
            "Salesforce connection not visible on CRM Duplicates start page",
        )

    def _wait_for_review(
        self, browser: requests.Session, session_id: str, *, timeout_s: float = 360.0
    ) -> str:
        deadline = time.monotonic() + timeout_s
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        last = ""
        while time.monotonic() < deadline:
            response = browser.get(self._url(path), timeout=90, allow_redirects=True)
            last = response.text
            if response.status_code != 200:
                time.sleep(0.4)
                continue
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                return last
            if "Review merge plan" in last:
                return last
            if "No duplicate groups found" in last:
                self.fail(f"unexpected zero groups: {last[:600]}")
            status_match = re.search(
                r"Workflow status:\s*<code>([^<]+)</code>", last, re.I
            )
            status = status_match.group(1).strip() if status_match else ""
            if status in {"failed", "unavailable"} or "Analysis failed" in last:
                self.fail(f"analysis failed status={status!r} html={last[:1200]}")
            if "form_token" in last and "Refresh status" in last:
                try:
                    self._post_form(
                        browser,
                        path,
                        {
                            "csrfmiddlewaretoken": self._extract_csrf(last)
                            if "csrfmiddlewaretoken" in last
                            else browser.cookies.get("csrftoken", ""),
                            "form_token": self._extract_form_token(last),
                        },
                        timeout=120,
                    )
                except Exception:
                    pass
            time.sleep(0.5)
        self.fail(f"timed out waiting for review: {last[:1000]}")

    def _submit_review_windows_approve_one_decline_rest(
        self,
        browser: requests.Session,
        session_id: str,
        first_html: str,
    ) -> str:
        html = first_html
        approved_once = False
        for _ in range(12):
            if html_shows_unfrozen_merge_plan(html):
                self.assertTrue(approved_once)
                return html
            if "crm-duplicate-review-form" not in html:
                merge = self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
                return merge.text

            group_order = self._extract_hidden(html, "group_order")
            group_ids = [g for g in group_order.split(",") if g.strip()]
            data = {
                "csrfmiddlewaretoken": self._extract_csrf(html),
                "form_token": self._extract_form_token(html),
                "window_id": self._extract_hidden(html, "window_id"),
                "window_digest": self._extract_hidden(html, "window_digest"),
                "expected_revision": self._extract_hidden(html, "expected_revision"),
                "group_order": group_order,
            }
            for gid in group_ids:
                data[f"group_revision_{gid}"] = self._extract_hidden(
                    html, f"group_revision_{gid}"
                )
                data[f"allowed_{gid}"] = self._extract_hidden(html, f"allowed_{gid}")
                data[f"recommended_{gid}"] = self._extract_hidden(
                    html, f"recommended_{gid}"
                )
                data[f"eligible_{gid}"] = self._extract_hidden(html, f"eligible_{gid}")
                data[f"advanced_{gid}"] = self._extract_hidden(html, f"advanced_{gid}")
                recommended = data[f"recommended_{gid}"]
                allowed = {
                    part.strip()
                    for part in data[f"allowed_{gid}"].split(",")
                    if part.strip()
                }
                if not approved_once and "approve" in allowed and recommended:
                    data[f"action_{gid}"] = "approve"
                    data[f"survivor_{gid}"] = recommended
                    approved_once = True
                elif "decline" in allowed:
                    data[f"action_{gid}"] = "decline"
                elif "quarantine" in allowed:
                    data[f"action_{gid}"] = "quarantine"
                else:
                    data[f"action_{gid}"] = next(iter(allowed))

            post = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/review/",
                data,
                timeout=180,
            )
            self.assertIn(post.status_code, {200, 302}, post.text[:800])
            html = post.text
            if post.url and "merge" in post.url:
                self.assertTrue(approved_once)
                return html
        self.fail(f"did not reach merge approved_once={approved_once}")

    def _poll_mutation_status(
        self, browser: requests.Session, html: str, *, timeout_s: float = 240.0
    ) -> str:
        deadline = time.monotonic() + timeout_s
        current = html
        while time.monotonic() < deadline:
            urls = re.findall(r'data-mutation-status-url="([^"]+)"', current)
            if not urls:
                if not html_has_pending_mutation_surface(current):
                    return current
            for rel in urls:
                try:
                    status = browser.get(self._url(rel), timeout=30)
                    if status.status_code != 200:
                        continue
                    body = status.json()
                    if body.get("status") in {"completed", "rejected"}:
                        redirect = body.get("redirect_url") or ""
                        if redirect:
                            refreshed = browser.get(
                                self._url(redirect),
                                timeout=90,
                                allow_redirects=True,
                            )
                            current = refreshed.text
                except (requests.RequestException, ValueError):
                    pass
            time.sleep(0.25)
            if urls:
                match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", urls[0])
                if match:
                    refreshed = browser.get(
                        self._url(f"/sessions/{match.group(1)}/workflow/"),
                        timeout=90,
                        allow_redirects=True,
                    )
                    current = refreshed.text
        return current

    def _finalize_and_authorize(
        self,
        browser: requests.Session,
        session_id: str,
        merge_html: str,
        *,
        mode: str,
    ) -> str:
        self.assertIn("Review merge plan", merge_html)
        self.assertIn(APPROVE_MERGE_PLAN_LABEL, merge_html)
        fin = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(merge_html),
                "form_token": self._extract_form_token(merge_html),
                "merge_action": "finalize",
            },
            timeout=180,
        )
        merge2 = (
            fin
            if AUTHORIZE_MERGE_STEP_LABEL in fin.text
            else self._get(browser, f"/sessions/{session_id}/crm-duplicates/merge/")
        )
        self.assertIn(AUTHORIZE_MERGE_STEP_LABEL, merge2.text)
        self.assertIn(mode, merge2.text)
        auth = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(merge2.text),
                "form_token": self._extract_form_token(merge2.text),
                "merge_action": "authorize",
                "selected_mode": mode,
            },
            timeout=180,
        )
        html = auth.text
        if "/workflow/" not in auth.url:
            html = self._get(browser, f"/sessions/{session_id}/workflow/").text
        html = self._poll_mutation_status(browser, html)
        return self._get(browser, f"/sessions/{session_id}/workflow/").text

    def _upload_to_merge(
        self,
        browser: requests.Session,
        *,
        flat_ids: list[str],
        names: list[str],
    ) -> tuple[str, str]:
        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        files = {
            "population_file": (
                "pop.csv",
                _csv_for_ids(flat_ids, names=names),
                "text/csv",
            ),
        }
        posted = self._post_form(
            browser,
            "/crm/duplicate-journeys/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(start.text),
                "form_token": self._extract_form_token(start.text),
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "uploaded_population",
            },
            files=files,
            timeout=180,
        )
        self.assertIn("map-record-id", posted.url, posted.text[:600])
        session_id = self._session_id_from_url(posted.url)
        mapped = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/map-record-id/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(posted.text),
                "form_token": self._extract_form_token(posted.text),
                "source_column": "Id",
            },
            timeout=180,
        )
        if "Review duplicate groups" in mapped.text:
            review_html = mapped.text
        else:
            review_html = self._wait_for_review(browser, session_id)
        merge_html = self._submit_review_windows_approve_one_decline_rest(
            browser, session_id, review_html
        )
        return session_id, merge_html

    def test_live_fe_salesforce_account_dry_run_execute_verify_cleanup(self):
        env_path = _resolve_sf_env_path()
        if env_path is None:
            self.fail(
                "Live 7C SF enabled but env file missing. Set "
                "EASYIMPORTS_SF_ENV_FILE or place credentials under "
                f"{_DEFAULT_SF_ENV}."
            )
        values = _load_dotenv_keys(env_path)
        try:
            authorization = require_live_org_authorization(values)
        except LiveGateAuthorizationError as exc:
            self.fail(str(exc))

        required = ("SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_REFRESH_TOKEN")
        missing = [k for k in required if not values.get(k)]
        if missing:
            self.fail(f"Live 7C SF env missing keys: {missing}.")

        rest, _oauth = _build_rest_client(values)
        org_rows = rest.query_all(
            "SELECT Id, IsSandbox, OrganizationType FROM Organization LIMIT 1"
        )
        self.assertEqual(len(org_rows), 1)
        org_id = str(org_rows[0]["Id"])
        org_type = str(org_rows[0].get("OrganizationType") or "")
        is_sandbox = bool(org_rows[0].get("IsSandbox"))
        try:
            assert_remote_org_is_de_or_sandbox(
                org_type=org_type, is_sandbox=is_sandbox
            )
        except LiveGateAuthorizationError as exc:
            self.fail(str(exc))

        org_id_digest = "sha256:" + hashlib.sha256(org_id.encode("utf-8")).hexdigest()[
            :16
        ]
        run_tag = uuid.uuid4().hex[:8]
        evidence: dict = {
            "phase": "7C",
            "track": "local_crm_dupe_e2e_storage",
            "provider": "salesforce",
            "marker": MARKER,
            "org_id_digest": org_id_digest,
            "org_type": org_type,
            "is_sandbox": is_sandbox,
            "pair_count": PAIR_COUNT,
            "merge_pair_index": MERGE_PAIR_INDEX,
            "path": "frontend_dual_process",
            "stable_root": True,
            "org_class": authorization["org_class"],
            "authorization": authorization,
            "steps": {},
        }

        created_account_ids: list[str] = []
        pair_ids: list[tuple[str, str]] = []

        try:
            browser = self._browser()
            self._register_and_connect_salesforce(
                browser, values=values, org_id=org_id
            )
            evidence["steps"]["tenant_verified"] = True
            evidence["steps"]["frontend_connected"] = True
            evidence["steps"]["org_attested"] = {
                "org_class": authorization["org_class"],
                "attestation": authorization["org_attestation"],
                "remote_org_type": org_type,
                "remote_is_sandbox": is_sandbox,
                "before_seed": True,
            }
            print(
                f"[7C-SF LIVE] connected org_digest={org_id_digest} "
                f"org_class={authorization['org_class']} type={org_type!r} "
                f"sandbox={is_sandbox}"
            )

            for index in range(PAIR_COUNT):
                name = _pair_name(index)
                website = _pair_website(index)
                left = _create_account(
                    rest,
                    {
                        "Name": name,
                        "Website": website,
                        "Description": (
                            f"{MARKER} group=p{index:03d} member=1/2 run={run_tag}"
                        ),
                    },
                )
                right = _create_account(
                    rest,
                    {
                        "Name": name,
                        "Website": website,
                        "Description": (
                            f"{MARKER} group=p{index:03d} member=2/2 run={run_tag}"
                        ),
                    },
                )
                created_account_ids.extend([left, right])
                pair_ids.append((left, right))
            self.assertEqual(len(created_account_ids), PAIR_COUNT * 2)
            evidence["steps"]["seeded_accounts"] = len(created_account_ids)
            evidence["seeded_id_digest"] = _digest_ids(created_account_ids)
            print(
                f"[7C-SF LIVE] seeded accounts count={len(created_account_ids)} "
                f"marker={MARKER}"
            )

            flat_ids = [rid for pair in pair_ids for rid in pair]
            names = [_pair_name(i // 2) for i in range(len(flat_ids))]

            headers_before = _read_account_headers(rest, created_account_ids)
            self.assertEqual(len(headers_before), len(created_account_ids))
            self.assertTrue(all(not h["is_deleted"] for h in headers_before.values()))

            session_id, merge_html = self._upload_to_merge(
                browser, flat_ids=flat_ids, names=names
            )
            evidence["steps"]["upload_path_review_ready"] = True
            dry_html = self._finalize_and_authorize(
                browser, session_id, merge_html, mode="dry_run"
            )
            headers_after_dry = _read_account_headers(rest, created_account_ids)
            self.assertTrue(
                all(not h["is_deleted"] for h in headers_after_dry.values())
            )
            for rid, before in headers_before.items():
                after = headers_after_dry[rid]
                self.assertEqual(after["system_modstamp"], before["system_modstamp"], rid)
                self.assertEqual(
                    after["master_record_id"], before["master_record_id"], rid
                )
            evidence["steps"]["dry_run_zero_writes"] = {
                "all_seeded_accounts_active": True,
                "system_modstamps_unchanged": True,
                "terminal_copy_present": (
                    "No live CRM changes were authorized for this run." in dry_html
                    or "Groups processed" in dry_html
                ),
            }
            print("[7C-SF LIVE] dry_run verified zero Account deletes")

            session_id2, merge_html2 = self._upload_to_merge(
                browser, flat_ids=flat_ids, names=names
            )
            ex_html = self._finalize_and_authorize(
                browser, session_id2, merge_html2, mode="execute"
            )

            # Poll SOQL for durable merge (may lag briefly).
            deleted: list[str] = []
            active: list[str] = []
            survivor = None
            loser = None
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                headers_after = _read_account_headers(rest, created_account_ids)
                deleted = [
                    rid for rid, h in headers_after.items() if h["is_deleted"]
                ]
                active = [
                    rid for rid, h in headers_after.items() if not h["is_deleted"]
                ]
                if len(deleted) == 1 and len(active) == PAIR_COUNT * 2 - 1:
                    loser = deleted[0]
                    # MasterRecordId points at survivor when available.
                    master = headers_after[loser].get("master_record_id")
                    if master and master in active:
                        survivor = master
                    else:
                        # Fallback: survivor is the other member of the deleted pair.
                        for left, right in pair_ids:
                            if loser == left:
                                survivor = right
                                break
                            if loser == right:
                                survivor = left
                                break
                    if survivor in active:
                        break
                time.sleep(1.0)

            self.assertEqual(
                len(deleted),
                1,
                f"expected one deleted loser; deleted={deleted} active={active} "
                f"terminal={ex_html[:600]!r}",
            )
            self.assertIsNotNone(survivor)
            self.assertIn(survivor, active)
            evidence["steps"]["execute_merge"] = {
                "loser_deleted": True,
                "survivor_active": True,
                "deleted_count": len(deleted),
                "active_seeded_count": len(active),
                "survivor_id_digest": _digest_ids([str(survivor)]),
                "loser_id_digest": _digest_ids([str(loser)]),
                "terminal_groups_processed": "Groups processed" in ex_html,
            }
            print("[7C-SF LIVE] execute verified loser_deleted survivor_active")
            created_account_ids = list(active)

            machine_boot = self.machine / "EasyImports" / "bootstrap.json"
            if machine_boot.exists():
                self.assertTrue(str(machine_boot).startswith(str(self.machine)))
            evidence["steps"]["bootstrap_isolated"] = True
            evidence["steps"]["person_merge_execute"] = {
                "attempted": False,
                "reason": "phase_7c_account_only",
            }

            archived = 0
            for rid in list(dict.fromkeys(created_account_ids)):
                try:
                    _delete_account(rest, rid)
                    archived += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[7C-SF LIVE] delete failed {rid}: {exc}")
            leftover = _read_account_headers(rest, created_account_ids)
            still = [rid for rid, h in leftover.items() if not h["is_deleted"]]
            self.assertEqual(still, [], f"active seeded remain: {still}")
            evidence["steps"]["cleanup"] = {
                "accounts_deleted": archived,
                "active_seeded_accounts_remaining": 0,
            }
            evidence["result"] = "passed"
            _write_evidence(evidence)
            print(f"[7C-SF LIVE] cleanup deleted={archived}; evidence written")
        except Exception:
            for rid in list(dict.fromkeys(created_account_ids)):
                try:
                    _delete_account(rest, rid)
                except Exception:
                    pass
            raise
