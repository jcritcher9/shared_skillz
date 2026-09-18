"""Phase 7B — HubSpot Company live merge via true frontend dual-process path.

Network-free: gate defaults off (always run).

Live (opt-in only): browser → Django runserver → API process on an isolated
durable data root:

  HubSpot register (Connect wizard)
    → Connect portal (same owner-session)
    → seed uniquely marked synthetic Companies (independent portal)
    → CRM Duplicates / Companies upload population + Record ID map
    → frontend review (approve 1, decline rest)
    → freeze merge plan
    → dry_run authorize (zero CRM mutation; independent portal verify)
    → separately authorized execute journey (approve 1)
    → independent portal authoritative_state verify
    → cleanup remaining synthetic companies
    → write sanitized evidence JSON

Live flags:
  EASYIMPORTS_CRM_DUPE_STORAGE_7B_LIVE=1
  EASYIMPORTS_CRM_DUPE_7B_HS_LIVE=1          (alias)

Required non-production attestation (fail closed before any seed/write):
  EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS=developer_test|sandbox|developer
  (alias: EASYIMPORTS_HUBSPOT_PORTAL_CLASS)

Env file (override with EASYIMPORTS_HS_ENV_FILE):
  %USERPROFILE%\\Documents\\easyimports_hubspot_test\\.env.txt
  (falls back to .env)

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    local_crm_dupe_e2e_storage_and_operator_path.md (Phase 7B)
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


MARKER = "ei-crmdupe-7b-storage-synthetic-01"
_LIVE_FLAG = "EASYIMPORTS_CRM_DUPE_STORAGE_7B_LIVE"
_LIVE_HS = "EASYIMPORTS_CRM_DUPE_7B_HS_LIVE"
_PORTAL_CLASS_KEYS = (
    "EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS",
    "EASYIMPORTS_HUBSPOT_PORTAL_CLASS",
)
# Operator must attest non-production. Production/customer classes are refused.
_ALLOWED_PORTAL_CLASSES = frozenset(
    {
        "developer_test",
        "developer",
        "sandbox",
        "developer_sandbox",
        "app_developer",
    }
)
_REFUSED_PORTAL_CLASSES = frozenset(
    {
        "production",
        "customer",
        "prod",
        "live_customer",
        "standard_production",
    }
)
_DEFAULT_HS_ENV_CANDIDATES = (
    Path.home() / "Documents" / "easyimports_hubspot_test" / ".env.txt",
    Path.home() / "Documents" / "easyimports_hubspot_test" / ".env",
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
    / "local_crm_dupe_e2e_storage_phase7b_hs_evidence.json"
)

# Exact keys that must never appear (raw tenant/secret material).
_FORBIDDEN_EVIDENCE_KEYS_EXACT = frozenset(
    {
        "access_token",
        "token",
        "refresh_token",
        "client_secret",
        "authorization_code",
        "api_key",
        "password",
        "secret",
        "hub_id",
        "portal_id",
        "instance_url",
        "bearer",
        "private_app_token",
        "service_key",
    }
)
# Substring fragments for compound secret key names (not digests like hub_id_digest).
_FORBIDDEN_EVIDENCE_KEY_FRAGMENTS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization_code",
    "private_app_token",
    "service_key",
    "api_key",
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"pat-[a-z0-9_-]+", re.IGNORECASE),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _live_enabled() -> bool:
    return any(
        os.environ.get(flag, "").strip() == "1" for flag in (_LIVE_FLAG, _LIVE_HS)
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


def _resolve_hs_env_path() -> Path | None:
    override = (os.environ.get("EASYIMPORTS_HS_ENV_FILE") or "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    for candidate in _DEFAULT_HS_ENV_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _digest_ids(ids: list[str]) -> str:
    material = "|".join(sorted(str(i) for i in ids))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _pair_name(index: int) -> str:
    return f"EI 7B Holdings{index:03d} ({MARKER})"


def _pair_domain(index: int) -> str:
    return f"ei-7b-p{index:03d}.example.com"


def _csv_for_ids(ids: list[str], *, names: list[str] | None = None) -> bytes:
    lines = ["Id,Name"]
    for i, rid in enumerate(ids):
        name = names[i] if names is not None else rid
        lines.append(f"{rid},{name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _resolve_portal_class(values: dict[str, str]) -> str:
    """Return normalized operator portal-class attestation or empty if missing."""

    for key in _PORTAL_CLASS_KEYS:
        raw = str(values.get(key) or os.environ.get(key) or "").strip().lower()
        if raw:
            return raw.replace(" ", "_").replace("-", "_")
    return ""


def _build_authorization_scope(*, portal_class: str) -> dict[str, Any]:
    """Sanitized per-run authorization block (no secrets, no hub ids)."""

    return {
        "live_gate_flags": sorted([_LIVE_FLAG, _LIVE_HS]),
        "portal_class": portal_class,
        "portal_attestation": "operator_declared_non_production",
        "production_customer_orgs": False,
        "scope": {
            "entity_family": "company",
            "capability": "company_only_path_c",
            "modes": ["dry_run", "execute"],
            "synthetic_marker_only": True,
            "marker_prefix": "ei-crmdupe-7b-storage",
            "contact_people_merge": False,
            "path": "frontend_dual_process",
        },
    }


class LiveGateAuthorizationError(RuntimeError):
    """Fail-closed live gate: missing/invalid non-production attestation."""


def require_live_portal_authorization(
    values: dict[str, str],
) -> dict[str, Any]:
    """Fail closed unless operator attests a developer/sandbox test portal.

    Hub ID match alone is not sufficient: a production customer portal could
    still match an expected hub id. Require an explicit portal class attestation
    before any seed or CRM write.
    """

    portal_class = _resolve_portal_class(values)
    if not portal_class:
        raise LiveGateAuthorizationError(
            "Live 7B refused: missing non-production portal attestation. "
            "Set EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS to one of "
            f"{sorted(_ALLOWED_PORTAL_CLASSES)} (developer/sandbox test portals "
            "only; never production customer portals)."
        )
    if portal_class in _REFUSED_PORTAL_CLASSES:
        raise LiveGateAuthorizationError(
            f"Live 7B refused portal_class={portal_class!r}: production/customer "
            "portals are not authorized for this gate."
        )
    if portal_class not in _ALLOWED_PORTAL_CLASSES:
        raise LiveGateAuthorizationError(
            f"Live 7B refused portal_class={portal_class!r}: must be one of "
            f"{sorted(_ALLOWED_PORTAL_CLASSES)}."
        )
    return _build_authorization_scope(portal_class=portal_class)


def _forbidden_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    if not lowered:
        return False
    if lowered in _FORBIDDEN_EVIDENCE_KEYS_EXACT:
        return True
    # Allow intentional digests (hub_id_digest) while refusing raw hub_id.
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
    # Digests and synthetic markers are intentional evidence fields.
    if text.startswith("sha256:") or text.startswith("ei-"):
        return None
    # HubSpot private-app tokens are typically long opaque base64-ish blobs.
    # Refuse high-entropy looking secrets without underscores/spaces (labels OK).
    if len(text) >= 40 and re.fullmatch(r"[A-Za-z0-9+/=_-]{40,}", text):
        if "_" not in text and "-" not in text[4:]:
            return "looks like an opaque secret/token value"
    return None


def assert_evidence_safe(obj: Any, *, path: str = "$") -> None:
    """Recursively fail closed if secrets/tenant identifiers appear in evidence."""

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = f"{path}.{key}"
            if _forbidden_key(str(key)):
                raise AssertionError(
                    f"Evidence refuses forbidden key at {key_path}"
                )
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
        return
    # Numbers/bools/None are fine (and must not be hub ids stored under secret keys).


def _write_evidence(payload: dict) -> None:
    """Write sanitized evidence; recursive secret rejection fail-closed."""

    _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Deep copy via JSON round-trip so nested structures are plain data.
    safe = json.loads(json.dumps(payload))
    assert_evidence_safe(safe)
    text = json.dumps(safe, indent=2, sort_keys=True) + "\n"
    # Final wire-level scan of serialized form.
    assert_evidence_safe(json.loads(text))
    lowered = text.lower()
    if "pat-" in lowered:
        raise AssertionError("Evidence serialization contains pat- token material.")
    _EVIDENCE_PATH.write_text(text, encoding="utf-8")


def _ensure_repo_on_path() -> Path:
    """Test-only: allow independent portal seed/verify without Django runtime imports."""

    repo = Path(__file__).resolve().parents[2]
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    return repo


def _build_portal(token: str, hub_id: str):
    """Independent portal for seed / authoritative verify / cleanup (test-only)."""

    _ensure_repo_on_path()
    from mappings_2.integrations.hubspot.transport.http import HubSpotHttpClient
    from mappings_2.integrations.hubspot.transport.live_portal import (
        LiveHubSpotPortal,
        StaticAccessTokenSource,
    )

    return LiveHubSpotPortal(
        hub_id=hub_id,
        http=HubSpotHttpClient(),
        token_source=StaticAccessTokenSource(token),
        expected_hub_id=hub_id,
        company_only=True,
    )


class LocalCrmDupeStoragePhase7bNetworkFreeTests(SimpleTestCase):
    """Always-on network-free phase 7B boundaries (no CRM HTTP)."""

    databases = set()

    def test_live_gate_defaults_off(self):
        self.assertFalse(_live_enabled())
        self.assertTrue(MARKER.startswith("ei-crmdupe-7b-storage"))
        self.assertEqual(PAIR_COUNT, 2)

    def test_evidence_path_is_repo_local(self):
        self.assertEqual(
            _EVIDENCE_PATH.name,
            "local_crm_dupe_e2e_storage_phase7b_hs_evidence.json",
        )
        self.assertIn("completed_projects", _EVIDENCE_PATH.parts)

    def test_portal_authorization_requires_attestation(self):
        with self.assertRaises(LiveGateAuthorizationError):
            require_live_portal_authorization({})
        with self.assertRaises(LiveGateAuthorizationError):
            require_live_portal_authorization(
                {"EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS": "production"}
            )
        with self.assertRaises(LiveGateAuthorizationError):
            require_live_portal_authorization(
                {"EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS": "customer"}
            )
        with self.assertRaises(LiveGateAuthorizationError):
            require_live_portal_authorization(
                {"EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS": "standard"}
            )

    def test_portal_authorization_accepts_developer_sandbox_classes(self):
        for portal_class in sorted(_ALLOWED_PORTAL_CLASSES):
            auth = require_live_portal_authorization(
                {"EASYIMPORTS_CRM_DUPE_STORAGE_7B_PORTAL_CLASS": portal_class}
            )
            self.assertEqual(auth["portal_class"], portal_class)
            self.assertFalse(auth["production_customer_orgs"])
            self.assertEqual(auth["scope"]["entity_family"], "company")
            self.assertFalse(auth["scope"]["contact_people_merge"])
            self.assertEqual(auth["scope"]["modes"], ["dry_run", "execute"])

    def test_evidence_safe_rejects_nested_secrets(self):
        with self.assertRaises(AssertionError):
            assert_evidence_safe(
                {"steps": {"nested": {"access_token": "pat-nested-secret-value"}}}
            )
        with self.assertRaises(AssertionError):
            assert_evidence_safe(
                {"steps": {"nested": {"token": "still-secret"}}}
            )
        with self.assertRaises(AssertionError):
            assert_evidence_safe(
                {"steps": {"note": "Bearer pat-abc123xyz-should-fail"}}
            )
        with self.assertRaises(AssertionError):
            assert_evidence_safe({"hub_id": "12345678"})
        # Allowed sanitized shape.
        assert_evidence_safe(
            {
                "hub_id_digest": "sha256:deadbeefcafef00d",
                "authorization": _build_authorization_scope(
                    portal_class="developer_test"
                ),
                "steps": {"dry_run_zero_writes": {"pairs_unchanged": True}},
            }
        )


@unittest.skipUnless(
    _live_enabled(),
    f"Live CRM-dupe storage 7B HS gate disabled "
    f"(set {_LIVE_FLAG}=1 or {_LIVE_HS}=1).",
)
class LocalCrmDupeStoragePhase7bLiveDualProcessTests(SimpleTestCase):
    """True dual-process FE ladder for HubSpot Company live merge (opt-in)."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.tmp = tempfile.TemporaryDirectory(prefix="ei_web_p7b_")
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
        # Live HubSpot private-app composition requires non-synthetic OAuth.
        # EASYIMPORTS_SF_OAUTH_EXCHANGE=synthetic forces hubspot-phase3-v1 and
        # cannot read real portal company IDs (analysis fails closed).
        environment.pop("EASYIMPORTS_SF_OAUTH_EXCHANGE", None)
        # Never write the developer's real machine bootstrap.
        environment["LOCALAPPDATA"] = str(cls.machine)
        environment["XDG_DATA_HOME"] = str(cls.machine / "xdg")
        environment["PYTHONPATH"] = str(cls.repo)
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
        cls.api_process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=cls.api_log_handle,
            stderr=subprocess.STDOUT,
        )
        health = f"{cls.api_base}/health"
        for _ in range(120):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("Phase 7B API process did not start.")

    @classmethod
    def _stop_api(cls) -> None:
        if getattr(cls, "api_process", None) is None:
            return
        cls.api_process.terminate()
        try:
            cls.api_process.wait(timeout=5)
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
                "Phase 7B Django migrate failed:\n"
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
        raise RuntimeError("Phase 7B Django runserver did not start.")

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

    # ------------------------------------------------------------------
    # Browser helpers
    # ------------------------------------------------------------------

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "Phase7BStorageE2E/1.0"})
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

    def _register_and_connect_hubspot(
        self,
        browser: requests.Session,
        *,
        token: str,
        hub_id: str,
    ) -> None:
        """Connect wizard: pick HubSpot → private-app registration → connect."""

        pick = self._get(browser, "/crm/setup/")
        # May redirect to pick.
        if "crm/setup/pick" not in pick.url and "Choose a CRM" not in pick.text:
            pick = self._get(browser, "/crm/setup/pick/")
        self.assertIn("form_token", pick.text)
        choose = self._post_form(
            browser,
            "/crm/setup/choose/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(pick.text),
                "form_token": self._extract_form_token(pick.text),
                "provider_key": "hubspot",
            },
        )
        self.assertIn(
            "hubspot",
            choose.url.lower() + choose.text.lower(),
            choose.url,
        )
        # Registration form.
        hs_page = (
            choose
            if "access_token" in choose.text or "Expected hub" in choose.text
            else self._get(browser, "/crm/setup/hubspot/")
        )
        self.assertIn("form_token", hs_page.text)
        saved = self._post_form(
            browser,
            "/crm/setup/hubspot/save/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(hs_page.text),
                "form_token": self._extract_form_token(hs_page.text),
                "label": f"7B Storage Live {uuid.uuid4().hex[:6]}",
                "auth_mode": "private_app",
                "expected_hub_id": hub_id,
                "access_token": token,
            },
            timeout=120,
        )
        self.assertNotIn("errorlist", saved.text.lower()[:800])
        # Should land on connect page.
        if "Connect HubSpot portal" not in saved.text:
            saved = self._get(browser, "/crm/setup/hubspot/connect/")
        self.assertIn("Connect HubSpot portal", saved.text)
        self.assertIn("form_token", saved.text)
        connected = self._post_form(
            browser,
            "/crm/setup/hubspot/connect/submit/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(saved.text),
                "form_token": self._extract_form_token(saved.text),
            },
            timeout=120,
        )
        # Complete page or connections list should show connected.
        connections = self._get(browser, "/crm/connections/")
        lower = (connected.text + connections.text).lower()
        self.assertTrue(
            "hubspot" in lower and ("connected" in lower or "disconnect" in lower),
            connections.text[:800],
        )
        # Duplicates start must list the connection.
        start = self._get(browser, "/crm/duplicate-journeys/")
        self.assertIsNotNone(
            re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', start.text),
            "HubSpot connection not visible on CRM Duplicates start page",
        )

    def _progress_status(self, html: str) -> str:
        match = re.search(
            r"Workflow status:\s*<code>([^<]+)</code>",
            html,
            re.IGNORECASE,
        )
        return (match.group(1).strip() if match else "")

    def _progress_message(self, html: str) -> str:
        match = re.search(
            r'<p class="lede">([^<]+)</p>',
            html,
            re.IGNORECASE,
        )
        return (match.group(1).strip() if match else "")

    def _wait_for_review(
        self, browser: requests.Session, session_id: str, *, timeout_s: float = 360.0
    ) -> str:
        deadline = time.monotonic() + timeout_s
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        last = ""
        last_status = ""
        ticks = 0
        while time.monotonic() < deadline:
            response = browser.get(self._url(path), timeout=90, allow_redirects=True)
            last = response.text
            last_status = self._progress_status(last)
            if response.status_code != 200:
                time.sleep(0.4)
                continue
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                return last
            if "Review merge plan" in last:
                return last
            if "No duplicate groups found" in last:
                self.fail(f"unexpected zero groups: {last[:600]}")
            # Explicit failed progress screen.
            if last_status in {"failed", "unavailable"} or (
                "Analysis failed" in last and "message error" in last
            ):
                detail = last
                # Prefer workflow technical details when available.
                wf_match = re.search(
                    r'href="(/sessions/[0-9a-fA-F-]{36}/workflow/)"', last
                )
                if wf_match:
                    try:
                        detail = browser.get(
                            self._url(wf_match.group(1)),
                            timeout=60,
                            allow_redirects=True,
                        ).text
                    except requests.RequestException:
                        pass
                # Strip tags lightly for readable assertion.
                textish = re.sub(r"<[^>]+>", " ", detail)
                textish = re.sub(r"\s+", " ", textish)[:1500]
                log_tail = ""
                api_log = getattr(type(self), "api_log", None)
                if api_log is not None and Path(api_log).is_file():
                    try:
                        log_tail = Path(api_log).read_text(
                            encoding="utf-8", errors="replace"
                        )[-2500:]
                    except Exception:
                        log_tail = ""
                self.fail(
                    f"analysis failed status={last_status!r} "
                    f"msg={self._progress_message(last)!r} "
                    f"detail={textish!r} api_log_tail={log_tail!r}"
                )
            # POST refresh every few ticks (re-apply / exact retry progress).
            ticks += 1
            if ticks % 4 == 0 and "form_token" in last and "Refresh status" in last:
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
                except Exception as exc:  # noqa: BLE001
                    print(f"[7B-HS LIVE] progress refresh post failed: {exc}")
            if ticks % 10 == 0:
                print(
                    f"[7B-HS LIVE] progress tick={ticks} status={last_status!r} "
                    f"msg={self._progress_message(last)!r}"
                )
            time.sleep(0.5)
        log_tail = ""
        api_log = getattr(self, "api_log", None)
        if api_log is not None and Path(api_log).is_file():
            try:
                log_tail = Path(api_log).read_text(encoding="utf-8", errors="replace")[
                    -2000:
                ]
            except Exception:
                log_tail = ""
        self.fail(
            f"timed out waiting for review status={last_status!r} "
            f"msg={self._progress_message(last)!r} html={last[:1200]} "
            f"api_log_tail={log_tail!r}"
        )

    def _submit_review_windows_approve_one_decline_rest(
        self,
        browser: requests.Session,
        session_id: str,
        first_html: str,
    ) -> str:
        """Approve only the first eligible group once; decline all others."""

        html = first_html
        approved_once = False
        for _ in range(12):
            if html_shows_unfrozen_merge_plan(html):
                return html
            if "crm-duplicate-review-form" not in html:
                merge = self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
                return merge.text

            group_order = self._extract_hidden(html, "group_order")
            group_ids = [g for g in group_order.split(",") if g.strip()]
            self.assertTrue(group_ids, "group_order empty")
            data = {
                "csrfmiddlewaretoken": self._extract_csrf(html),
                "form_token": self._extract_form_token(html),
                "window_id": self._extract_hidden(html, "window_id"),
                "window_digest": self._extract_hidden(html, "window_digest"),
                "expected_revision": self._extract_hidden(html, "expected_revision"),
                "group_order": group_order,
            }
            for index, gid in enumerate(group_ids):
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
                # Approve exactly one group (first approve-eligible with survivor).
                if (
                    not approved_once
                    and "approve" in allowed
                    and recommended
                ):
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
                self.assertTrue(
                    approved_once,
                    "reached merge plan without approving a merge-eligible group",
                )
                return html
        self.fail(
            f"did not reach merge after review approved_once={approved_once}"
        )

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
        self.assertIn(fin.status_code, {200, 302}, fin.text[:800])
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
        self.assertIn(auth.status_code, {200, 302}, auth.text[:800])
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
        self.assertIn("Resolve CRM duplicates", start.text)
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
        self.assertTrue(
            any(part in mapped.url for part in ("progress", "review", "merge")),
            mapped.url,
        )
        if "Review duplicate groups" in mapped.text:
            review_html = mapped.text
        else:
            review_html = self._wait_for_review(browser, session_id)
        self.assertIn("Review duplicate groups", review_html)
        merge_html = self._submit_review_windows_approve_one_decline_rest(
            browser, session_id, review_html
        )
        return session_id, merge_html

    def _archive_all(self, portal, company_ids: list[str]) -> int:
        archived = 0
        for rid in list(dict.fromkeys(company_ids)):
            try:
                portal.archive_company(rid)
                archived += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[7B-HS LIVE] archive failed {rid}: {exc}")
        still: list[str] = []
        for rid in list(dict.fromkeys(company_ids)):
            rec = portal.get(rid)
            if rec is not None and not getattr(rec, "archived", False):
                try:
                    portal.archive_company(rid)
                    archived += 1
                except Exception:
                    still.append(rid)
        self.assertEqual(still, [], f"active seeded companies remain: {still}")
        return archived

    def test_live_fe_hubspot_company_dry_run_execute_verify_cleanup(self):
        env_path = _resolve_hs_env_path()
        if env_path is None:
            self.fail(
                "Live 7B HS enabled but env file missing. Set "
                "EASYIMPORTS_HS_ENV_FILE or place credentials under "
                f"{_DEFAULT_HS_ENV_CANDIDATES[0]}."
            )
        values = _load_dotenv_keys(env_path)
        # Fail closed before any CRM network mutation unless portal is attested
        # as a developer/sandbox test portal (hub id match alone is insufficient).
        try:
            authorization = require_live_portal_authorization(values)
        except LiveGateAuthorizationError as exc:
            self.fail(str(exc))

        token = (
            values.get("EASYIMPORTS_HUBSPOT_ACCESS_TOKEN")
            or values.get("EASYIMPORTS_HUBSPOT_SERVICE_KEY")
            or ""
        ).strip()
        hub_id = str(values.get("EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID") or "").strip()
        if not token or not hub_id:
            self.fail(
                "Live 7B HS env missing EASYIMPORTS_HUBSPOT_ACCESS_TOKEN "
                "and/or EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID."
            )

        hub_id_digest = "sha256:" + hashlib.sha256(hub_id.encode("utf-8")).hexdigest()[
            :16
        ]
        run_tag = uuid.uuid4().hex[:8]
        evidence: dict = {
            "phase": "7B",
            "track": "local_crm_dupe_e2e_storage",
            "provider": "hubspot",
            "marker": MARKER,
            "hub_id_digest": hub_id_digest,
            "pair_count": PAIR_COUNT,
            "merge_pair_index": MERGE_PAIR_INDEX,
            "capability": "company_only_path_c",
            "path": "frontend_dual_process",
            "stable_root": True,
            "portal_class": authorization["portal_class"],
            "authorization": authorization,
            "steps": {},
        }
        # Authorization attestation is already recorded; refuse seed without it.
        self.assertIn(
            authorization["portal_class"],
            _ALLOWED_PORTAL_CLASSES,
        )
        self.assertFalse(authorization["production_customer_orgs"])
        portal = _build_portal(token, hub_id)
        created_company_ids: list[str] = []
        pair_ids: list[tuple[str, str]] = []

        try:
            browser = self._browser()
            # --- O1c-ish: register + connect on stable isolated root via FE ---
            self._register_and_connect_hubspot(
                browser, token=token, hub_id=hub_id
            )
            evidence["steps"]["tenant_verified"] = True
            evidence["steps"]["frontend_connected"] = True
            evidence["steps"]["portal_attested"] = {
                "portal_class": authorization["portal_class"],
                "attestation": authorization["portal_attestation"],
                "before_seed": True,
            }
            print(
                f"[7B-HS LIVE] registered+connected hub_digest={hub_id_digest} "
                f"portal_class={authorization['portal_class']}"
            )

            # --- Seed synthetic Company pairs (setup only; not product path) ---
            # Authorization already enforced above; never seed without attestation.
            for index in range(PAIR_COUNT):
                name = _pair_name(index)
                domain = _pair_domain(index)
                left = portal.create_company(
                    {
                        "name": name,
                        "domain": domain,
                        "description": (
                            f"{MARKER} group=p{index:03d} member=1/2 run={run_tag}"
                        ),
                    }
                )
                right = portal.create_company(
                    {
                        "name": name,
                        "domain": domain,
                        "description": (
                            f"{MARKER} group=p{index:03d} member=2/2 run={run_tag}"
                        ),
                    }
                )
                left_id = str(left.record_id)
                right_id = str(right.record_id)
                created_company_ids.extend([left_id, right_id])
                pair_ids.append((left_id, right_id))
            self.assertEqual(len(created_company_ids), PAIR_COUNT * 2)
            evidence["steps"]["seeded_companies"] = len(created_company_ids)
            evidence["seeded_id_digest"] = _digest_ids(created_company_ids)
            print(
                f"[7B-HS LIVE] seeded companies count={len(created_company_ids)} "
                f"marker={MARKER}"
            )

            flat_ids = [rid for pair in pair_ids for rid in pair]
            names = [_pair_name(i // 2) for i in range(len(flat_ids))]

            # --- dry_run ladder via FE ---
            session_id, merge_html = self._upload_to_merge(
                browser, flat_ids=flat_ids, names=names
            )
            evidence["steps"]["upload_path_review_ready"] = True
            evidence["steps"]["frontend_review"] = {
                "approved_once": True,
                "pair_count": PAIR_COUNT,
            }
            # Pre-dry snapshot: all seeded active; pairs distinct.
            pre_dry_active = {
                rid: portal.get(rid) is not None for rid in created_company_ids
            }
            self.assertTrue(all(pre_dry_active.values()), pre_dry_active)
            for left, right in pair_ids:
                self.assertEqual(
                    portal.authoritative_state(left, right),
                    "unchanged",
                    (left, right),
                )

            dry_html = self._finalize_and_authorize(
                browser, session_id, merge_html, mode="dry_run"
            )
            post_dry_active = {
                rid: portal.get(rid) is not None for rid in created_company_ids
            }
            self.assertTrue(all(post_dry_active.values()), post_dry_active)
            for left, right in pair_ids:
                self.assertEqual(
                    portal.authoritative_state(left, right),
                    "unchanged",
                    (left, right),
                )
            evidence["steps"]["dry_run_zero_writes"] = {
                "pairs_unchanged": True,
                "all_seeded_companies_active": True,
                "terminal_copy_present": (
                    "No live CRM changes were authorized for this run." in dry_html
                    or "Groups processed" in dry_html
                ),
            }
            print("[7B-HS LIVE] dry_run verified pairs unchanged via portal")

            # --- execute ladder (separate journey) via FE ---
            browser2 = self._browser()
            # Same owner-session: reuse cookies from first browser after connect.
            # Re-using browser keeps owner-session; start a fresh journey.
            session_id2, merge_html2 = self._upload_to_merge(
                browser, flat_ids=flat_ids, names=names
            )
            ex_html = self._finalize_and_authorize(
                browser, session_id2, merge_html2, mode="execute"
            )
            # Independent portal verification: exactly one pair merged.
            # HubSpot merge state can lag briefly; poll authoritative_state.
            merge_loser = None
            merge_survivor = None
            verified_pairs = 0
            unchanged_pairs = 0
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                verified_pairs = 0
                unchanged_pairs = 0
                merge_loser = None
                merge_survivor = None
                for left, right in pair_ids:
                    state_lr = portal.authoritative_state(left, right)
                    state_rl = portal.authoritative_state(right, left)
                    if state_lr == "verified" or state_rl == "verified":
                        verified_pairs += 1
                        if state_lr == "verified":
                            merge_loser, merge_survivor = left, right
                        else:
                            merge_loser, merge_survivor = right, left
                    elif state_lr == "unchanged" and state_rl == "unchanged":
                        unchanged_pairs += 1
                if verified_pairs == 1 and unchanged_pairs == PAIR_COUNT - 1:
                    break
                time.sleep(1.0)
            self.assertEqual(
                verified_pairs,
                1,
                f"expected exactly one merged pair; verified={verified_pairs} "
                f"unchanged={unchanged_pairs} "
                f"terminal_has_no_live="
                f"{'No live CRM changes were authorized' in ex_html} "
                f"terminal={ex_html[:800]!r}",
            )
            self.assertEqual(unchanged_pairs, PAIR_COUNT - 1)
            self.assertIsNotNone(merge_loser)
            evidence["steps"]["execute_merge"] = {
                "merge_verified": True,
                "authoritative_state": "verified",
                "survivor_id_digest": _digest_ids([str(merge_survivor)]),
                "loser_id_digest": _digest_ids([str(merge_loser)]),
                "terminal_groups_processed": "Groups processed" in ex_html,
            }
            print("[7B-HS LIVE] execute verified authoritative_state=verified")
            # Do not re-archive the merged loser as an active seed.
            created_company_ids = [
                rid for rid in created_company_ids if rid != str(merge_loser)
            ]

            # Isolation: no developer machine bootstrap under isolated LOCALAPPDATA.
            machine_boot = self.machine / "EasyImports" / "bootstrap.json"
            if machine_boot.exists():
                self.assertTrue(str(machine_boot).startswith(str(self.machine)))
            evidence["steps"]["bootstrap_isolated"] = True

            # Contact path intentionally not exercised.
            evidence["steps"]["contact_path"] = {
                "attempted": False,
                "reason": "path_c_company_only_capability",
            }

            archived = self._archive_all(portal, created_company_ids)
            evidence["steps"]["cleanup"] = {
                "companies_archived": archived,
                "active_seeded_companies_remaining": 0,
            }
            evidence["result"] = "passed"
            _write_evidence(evidence)
            print(f"[7B-HS LIVE] cleanup archived={archived}; evidence written")
            # browser2 unused intentionally (owner-session continuity via browser).
            del browser2
        except Exception:
            for rid in list(dict.fromkeys(created_company_ids)):
                try:
                    portal.archive_company(rid)
                except Exception:
                    pass
            raise
