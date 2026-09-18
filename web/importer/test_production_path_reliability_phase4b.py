"""Phase 4B rem — Controlled production HubSpot Company canary.

Default CI: network-free allowlist + finally-cleanup proofs (including
fail-after-first-create registration).

Live dual-process (opt-in only; explicit production authorization):
  EASYIMPORTS_CRM_DUPE_PATH_4B_PRODUCTION_CANARY=1
  EASYIMPORTS_CRM_DUPE_PATH_4B_PORTAL_CLASS=standard|production|…
  EASYIMPORTS_CRM_DUPE_PATH_4B_OPERATOR_ACK=I_AUTHORIZE_PRODUCTION_SYNTHETIC_COMPANY_CANARY
  + HubSpot env file (same locations as storage 7B)

Authority:
``mappings_2/.../production_hubspot_crm_duplicate_operator_path_reliability.md``
Phase **4B**.
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
from unittest import mock

import requests
from django.test import SimpleTestCase

from .crm_duplicate_merge_copy import (
    APPROVE_MERGE_PLAN_LABEL,
    AUTHORIZE_MERGE_STEP_LABEL,
    html_shows_unfrozen_merge_plan,
)
from .pending_copy import html_has_pending_mutation_surface
from importer.production_canary_phase4b import (
    LIVE_FLAG,
    MARKER_PREFIX,
    OPERATOR_ACK_FLAG,
    PORTAL_CLASS_FLAG,
    REQUIRED_OPERATOR_ACK,
    CleanupRegistry,
    CleanupRegistryError,
    ProductionCanaryAuthorizationError,
    SeedAllowlistError,
    SeedIdAllowlist,
    assert_evidence_safe,
    count_active_marked_companies,
    count_active_registered_companies,
    digest_ids,
    live_canary_enabled,
    require_production_canary_authorization,
    run_canary_stages,
    sanitized_evidence_template,
    seed_pair_with_immediate_registration,
)


_DEFAULT_HS_ENV_CANDIDATES = (
    Path.home() / "Documents" / "easyimports_hubspot_test" / ".env.txt",
    Path.home() / "Documents" / "easyimports_hubspot_test" / ".env",
)
_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "mappings_2"
    / "codex_context"
    / "cross_agent_eval"
    / "project_implementations"
    / "completed_projects"
    / "production_path_reliability_phase4b_hs_evidence.json"
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


def _csv_for_ids(ids: list[str], *, names: list[str] | None = None) -> bytes:
    lines = ["Id,Name"]
    for i, rid in enumerate(ids):
        name = names[i] if names is not None else rid
        lines.append(f"{rid},{name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_evidence(payload: dict) -> None:
    _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = json.loads(json.dumps(payload))
    assert_evidence_safe(safe)
    text = json.dumps(safe, indent=2, sort_keys=True) + "\n"
    assert_evidence_safe(json.loads(text))
    _EVIDENCE_PATH.write_text(text, encoding="utf-8")


def _build_portal(token: str, hub_id: str):
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
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
    )


class Phase4bAuthorizationTests(SimpleTestCase):
    def test_live_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(LIVE_FLAG, None)
            self.assertFalse(live_canary_enabled())

    def test_refuse_without_opt_in(self):
        with self.assertRaises(ProductionCanaryAuthorizationError):
            require_production_canary_authorization(
                {LIVE_FLAG: "0", OPERATOR_ACK_FLAG: REQUIRED_OPERATOR_ACK}
            )

    def test_refuse_developer_sandbox_reuse(self):
        with self.assertRaises(ProductionCanaryAuthorizationError) as ctx:
            require_production_canary_authorization(
                {
                    LIVE_FLAG: "1",
                    OPERATOR_ACK_FLAG: REQUIRED_OPERATOR_ACK,
                    PORTAL_CLASS_FLAG: "developer_test",
                }
            )
        self.assertIn("7B", str(ctx.exception))

    def test_refuse_missing_ack(self):
        with self.assertRaises(ProductionCanaryAuthorizationError):
            require_production_canary_authorization(
                {
                    LIVE_FLAG: "1",
                    PORTAL_CLASS_FLAG: "standard",
                    OPERATOR_ACK_FLAG: "yes",
                }
            )

    def test_accept_production_standard_attestation(self):
        scope = require_production_canary_authorization(
            {
                LIVE_FLAG: "1",
                OPERATOR_ACK_FLAG: REQUIRED_OPERATOR_ACK,
                PORTAL_CLASS_FLAG: "standard",
            }
        )
        self.assertEqual(scope["portal_class"], "standard")
        self.assertFalse(scope["customer_records_authorized"])
        self.assertEqual(scope["seed_pair_count"], 2)


class Phase4bAllowlistTests(SimpleTestCase):
    def test_requires_exactly_two_ids(self):
        with self.assertRaises(SeedAllowlistError):
            SeedIdAllowlist.from_create_responses(["only-one"])
        with self.assertRaises(SeedAllowlistError):
            SeedIdAllowlist.from_create_responses(["a", "b", "c"])

    def test_equality_at_every_stage(self):
        allow = SeedIdAllowlist.from_create_responses(["1001", "1002"])
        for stage in (
            "mapping",
            "review_projection",
            "reviewed_result",
            "frozen_plan",
            "pre_execute",
        ):
            allow.assert_equals(["1002", "1001"], stage=stage)
        with self.assertRaises(SeedAllowlistError):
            allow.assert_equals(["1001", "9999"], stage="mapping")


class Phase4bCleanupTests(SimpleTestCase):
    def test_finally_cleanup_on_success(self):
        active = {"A", "B"}
        seq = iter(["A", "B"])

        def create_one(_index: int) -> str:
            rid = next(seq)
            active.add(rid)
            return rid

        result = run_canary_stages(
            stages=(
                "mapping",
                "review",
                "freeze",
                "dry_run",
                "execute",
                "verify",
            ),
            create_one=create_one,
            on_stage=lambda stage, allow, reg: allow.assert_equals(
                ["A", "B"], stage=stage
            ),
            archive=lambda rid: active.discard(rid),
            is_active=lambda rid: rid in active,
        )
        self.assertIsNone(result["primary_error"])
        self.assertIsNone(result["cleanup_error"])
        self.assertTrue(result["cleanup"]["cleanup_ok"])
        self.assertEqual(result["cleanup"]["cleanup_attempt_count"], 2)
        self.assertEqual(active, set())

    def test_fail_after_first_create_still_cleans_registered_id(self):
        """Blocking rem: first create must register before second create fails."""

        active: set[str] = set()
        creates = {"count": 0}

        def create_one(index: int) -> str:
            creates["count"] += 1
            rid = f"S{index + 1}"
            active.add(rid)
            return rid

        result = run_canary_stages(
            stages=("mapping",),
            create_one=create_one,
            on_stage=lambda *a, **k: None,
            archive=lambda rid: active.discard(rid),
            is_active=lambda rid: rid in active,
            fail_after_first_create=True,
        )
        self.assertIsNotNone(result["primary_error"])
        self.assertIn("injected_failure_after:first_create", result["primary_error"])
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["cleanup_attempt_count"], 1)
        self.assertEqual(creates["count"], 1)
        self.assertIsNone(result["cleanup_error"])
        self.assertTrue(result["cleanup"]["cleanup_ok"])
        self.assertEqual(active, set())

    def test_finally_cleanup_after_injected_stage_failures(self):
        stages = (
            "upload",
            "review",
            "freeze",
            "dry_run",
            "execute",
            "verify",
        )
        for fail_after in stages:
            active: set[str] = set()
            seq = iter(["S1", "S2"])

            def create_one(_index: int, _seq=seq, _active=active) -> str:
                rid = next(_seq)
                _active.add(rid)
                return rid

            result = run_canary_stages(
                stages=stages,
                create_one=create_one,
                on_stage=lambda stage, allow, reg: allow.assert_equals(
                    ["S1", "S2"], stage=stage
                ),
                archive=lambda rid, _a=active: _a.discard(rid),
                is_active=lambda rid, _a=active: rid in _a,
                fail_after=fail_after,
            )
            self.assertIsNotNone(result["primary_error"])
            self.assertIn(f"injected_failure_after:{fail_after}", result["primary_error"])
            self.assertEqual(result["cleanup_attempt_count"], 2)
            self.assertEqual(active, set())
            self.assertTrue(result["cleanup"]["cleanup_ok"])

    def test_immediate_registration_helper(self):
        registry = CleanupRegistry()
        active: set[str] = set()
        seq = iter(["X", "Y"])

        def create_one(_i: int) -> str:
            rid = next(seq)
            active.add(rid)
            # Simulate failure after first create before second returns:
            return rid

        allow = seed_pair_with_immediate_registration(registry, create_one)
        self.assertEqual(allow.ids, frozenset({"X", "Y"}))
        self.assertEqual(registry.created_ids, ["X", "Y"])

    def test_cleanup_failure_reported_independently(self):
        registry = CleanupRegistry()
        registry.register_created("X")
        registry.register_created("Y")

        def archive(rid: str) -> None:
            if rid == "Y":
                raise RuntimeError("archive_failed")

        with self.assertRaises(CleanupRegistryError):
            registry.run_finally(archive, is_active=lambda rid: rid == "Y")
        self.assertEqual(len(registry.cleanup_errors), 1)

    def test_count_active_registered_fail_closed_on_get(self):
        """Registered recheck fails closed on get errors; empty registry alone fails."""

        class _Rec:
            def __init__(self, archived: bool = False):
                self.archived = archived
                self.properties = {"name": f"x ({MARKER_PREFIX})"}

        store = {"A": _Rec(archived=False), "B": _Rec(archived=True)}

        def get_record(rid: str):
            if rid not in store:
                return None
            return store[rid]

        self.assertEqual(
            count_active_registered_companies(
                ["A", "B"], get_record=get_record, marker=MARKER_PREFIX
            ),
            1,
        )
        store["A"] = _Rec(archived=True)
        self.assertEqual(
            count_active_registered_companies(
                ["A", "B"], get_record=get_record, marker=MARKER_PREFIX
            ),
            0,
        )

        def boom(_rid: str):
            raise ConnectionError("transport_down")

        with self.assertRaises(CleanupRegistryError) as ctx:
            count_active_registered_companies(
                ["A"], get_record=boom, marker=MARKER_PREFIX
            )
        self.assertIn("fail closed", str(ctx.exception).lower())
        # Empty registry is not independent proof of zero marked.
        with self.assertRaises(CleanupRegistryError):
            count_active_registered_companies(
                [], get_record=get_record, marker=MARKER_PREFIX
            )

    def test_count_active_marked_requires_marker_search(self):
        """Independent proof uses marker search; finds unregistered lost creates."""

        class _Rec:
            def __init__(self, rid: str, archived: bool = False):
                self.record_id = rid
                self.archived = archived
                self.properties = {"description": MARKER_PREFIX}

        # Orphan from lost create response (not in registry).
        pages = {None: ([_Rec("ORPHAN")], None)}

        def search_marked(marker: str, cursor: str | None):
            self.assertEqual(marker, MARKER_PREFIX)
            return pages.get(cursor, ([], None))

        self.assertEqual(
            count_active_marked_companies(
                marker=MARKER_PREFIX,
                search_marked_page=search_marked,
                registered_ids=[],
            ),
            1,
        )

        pages[None] = ([], None)
        self.assertEqual(
            count_active_marked_companies(
                marker=MARKER_PREFIX,
                search_marked_page=search_marked,
                registered_ids=[],
            ),
            0,
        )

        def boom_search(_m: str, _c: str | None):
            raise ConnectionError("search_down")

        with self.assertRaises(CleanupRegistryError) as ctx:
            count_active_marked_companies(
                marker=MARKER_PREFIX,
                search_marked_page=boom_search,
                registered_ids=["A"],
                get_record=lambda _rid: None,
            )
        self.assertIn("fail closed", str(ctx.exception).lower())

        # Page-limit with remaining cursor fails closed.
        def endless(marker: str, cursor: str | None):
            return ([], "next-page")

        with self.assertRaises(CleanupRegistryError):
            count_active_marked_companies(
                marker=MARKER_PREFIX,
                search_marked_page=endless,
                max_pages=2,
            )

    def test_evidence_sanitizer_refuses_raw_ids_and_secrets(self):
        auth = require_production_canary_authorization(
            {
                LIVE_FLAG: "1",
                OPERATOR_ACK_FLAG: REQUIRED_OPERATOR_ACK,
                PORTAL_CLASS_FLAG: "production",
            }
        )
        evidence = sanitized_evidence_template(
            authorization=auth,
            allowlist_converged=True,
            dry_run_zero_write=True,
            execute_one_merge=True,
            cleanup={
                "created_count": 2,
                "cleanup_attempt_count": 2,
                "cleanup_success_count": 2,
                "cleanup_error_count": 0,
                "active_remaining_count": 0,
                "cleanup_ok": True,
                "id_digest": "sha256:deadbeefdeadbeef",
            },
        )
        assert_evidence_safe(evidence)
        with self.assertRaises(AssertionError):
            assert_evidence_safe({"access_token": "secret"})
        with self.assertRaises(AssertionError):
            assert_evidence_safe({"seed_ids": ["1", "2"]})


@unittest.skipUnless(
    live_canary_enabled(),
    "Phase 4B live production canary disabled "
    f"(set {LIVE_FLAG}=1 + portal class + operator ack)",
)
class Phase4bLiveProductionCanaryDualProcessTests(SimpleTestCase):
    """Opt-in live dual-process production canary (never default CI).

    Full path: authorize → dual-process stack → FE connect → seed two synthetic
    Companies with immediate cleanup registration → upload allowlist → review →
    freeze → dry_run (unchanged) → execute one merge → portal verify → finally
    cleanup. Evidence is sanitized digests/counts only.
    """

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.authorization = require_production_canary_authorization()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.test_dir = tempfile.TemporaryDirectory(prefix="ei_web_p4b_")
        root = Path(cls.test_dir.name)
        cls.machine = root / "LocalAppData"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        cls.env_file = root / "phase4b.env"
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

        helper = cls.repo / "scripts" / "lib" / "resolve_python_for_tests.ps1"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-RepoRoot",
                str(cls.repo),
            ],
            cwd=str(cls.repo),
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout)
        cls.python_exe = Path(completed.stdout.strip().splitlines()[-1].strip())
        cls._write_env_file()
        cls._start_stack()

    @classmethod
    def _write_env_file(cls) -> None:
        lines = [
            f"EASYIMPORTS_API_BASE_URL={cls.api_base}",
            "EASYIMPORTS_API_HOST=127.0.0.1",
            f"EASYIMPORTS_API_PORT={cls.api_port}",
            f"EASYIMPORTS_DB_PATH={cls.django_db}",
            f"EASYIMPORTS_MEDIA_ROOT={cls.media_root}",
            "EASYIMPORTS_DEBUG=1",
            "EASYIMPORTS_ALLOWED_HOSTS=127.0.0.1,localhost,testserver",
            "EASYIMPORTS_SECRET_STORE=file_insecure",
        ]
        cls.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def _kill_ports(cls) -> None:
        for port in (cls.api_port, cls.django_port):
            try:
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            f"$c = Get-NetTCPConnection -LocalPort {port} "
                            f"-State Listen -ErrorAction SilentlyContinue; "
                            f"if ($c) {{ $c | ForEach-Object {{ "
                            f"Stop-Process -Id $_.OwningProcess -Force "
                            f"-ErrorAction SilentlyContinue }} }}"
                        ),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

    @classmethod
    def _start_stack(cls) -> None:
        launcher = cls.repo / "scripts" / "start_local.ps1"
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(cls.machine)
        environment.pop("EASYIMPORTS_API_STATE_ROOT", None)
        cls._kill_ports()
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-RepoRoot",
                str(cls.repo),
                "-PythonExe",
                str(cls.python_exe),
                "-EnvFile",
                str(cls.env_file),
                "-SkipBrowser",
                "-ApiPort",
                str(cls.api_port),
                "-DjangoPort",
                str(cls.django_port),
                "-ReadyTimeoutSeconds",
                "120",
            ],
            cwd=str(cls.repo),
            env=environment,
            text=True,
            capture_output=True,
            timeout=180,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"start_local failed:\n{completed.stdout}\n{completed.stderr}"
            )
        for _ in range(80):
            try:
                if requests.get(f"{cls.api_base}/health", timeout=0.3).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            raise RuntimeError("API not ready")
        for _ in range(80):
            try:
                r = requests.get(f"{cls.django_base}/", timeout=0.3)
                if r.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        raise RuntimeError("Django not ready")

    @classmethod
    def tearDownClass(cls):
        try:
            cls._kill_ports()
        finally:
            cls.test_dir.cleanup()
            super().tearDownClass()

    def _browser(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": "Phase4BProductionCanary/1.0"})
        return s

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.django_base + path

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1)
        start += len(marker)
        return html[start : html.find('"', start)]

    def _extract_csrf(self, html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1)
        start += len(marker)
        return html[start : html.find('"', start)]

    def _extract_hidden(self, html: str, name: str) -> str:
        pattern = re.compile(
            rf'name="{re.escape(name)}"\s+value="([^"]*)"', re.I
        )
        match = pattern.search(html)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def _session_id_from_url(self, url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        self.assertIsNotNone(match, url)
        return match.group(1)

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        r = browser.get(self._url(path), timeout=90, allow_redirects=True)
        self.assertIn(r.status_code, {200, 302}, r.text[:500])
        return r

    def _post_form(
        self,
        browser: requests.Session,
        path: str,
        data: dict,
        *,
        files: dict | None = None,
        timeout: int = 180,
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
            allow_redirects=True,
        )

    def _register_and_connect_hubspot(
        self, browser: requests.Session, *, token: str, hub_id: str
    ) -> None:
        """Connect wizard: pick HubSpot → private-app registration → connect.

        Token and expected hub id are posted into the isolated stack (same path
        as storage 7B). Never skip registration with unused credentials.
        """

        if not token or not hub_id:
            raise AssertionError("token and hub_id are required for registration")
        pick = self._get(browser, "/crm/setup/")
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
        hs_page = (
            choose
            if "access_token" in choose.text or "Expected hub" in choose.text
            else self._get(browser, "/crm/setup/hubspot/")
        )
        self.assertIn("form_token", hs_page.text)
        self.assertIn("access_token", hs_page.text)
        saved = self._post_form(
            browser,
            "/crm/setup/hubspot/save/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(hs_page.text),
                "form_token": self._extract_form_token(hs_page.text),
                "label": f"4B Production Canary {uuid.uuid4().hex[:6]}",
                "auth_mode": "private_app",
                "expected_hub_id": hub_id,
                "access_token": token,
            },
            timeout=120,
        )
        self.assertNotIn("errorlist", saved.text.lower()[:800])
        if "Connect HubSpot portal" not in saved.text:
            saved = self._get(browser, "/crm/setup/hubspot/connect/")
        self.assertIn("Connect HubSpot portal", saved.text)
        connected = self._post_form(
            browser,
            "/crm/setup/hubspot/connect/submit/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(saved.text),
                "form_token": self._extract_form_token(saved.text),
            },
            timeout=120,
        )
        connections = self._get(browser, "/crm/connections/")
        lower = (connected.text + connections.text).lower()
        self.assertTrue(
            "hubspot" in lower and ("connected" in lower or "disconnect" in lower),
            connections.text[:800],
        )
        start = self._get(browser, "/crm/duplicate-journeys/")
        self.assertIsNotNone(
            re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', start.text),
            "HubSpot connection not visible on CRM Duplicates start page",
        )

    def _extract_projected_record_ids(self, html: str) -> set[str]:
        """Extract CRM Company record IDs from journey UI markup only.

        Does **not** treat every long digit run as an ID (hub id, revisions,
        timestamps, cache busters). Prefer structured review/merge fields and
        ``<code>`` record displays; fall back to map-preview table cells.
        """

        found: set[str] = set()

        def _add_token(raw: str) -> None:
            for part in str(raw or "").split(","):
                token = part.strip()
                if re.fullmatch(r"\d{6,18}", token):
                    found.add(token)

        structured = [
            r'name="eligible_[^"]*"\s+value="([^"]*)"',
            r'value="([^"]*)"\s+name="eligible_[^"]*"',
            r'name="recommended_[^"]*"\s+value="([^"]*)"',
            r'value="([^"]*)"\s+name="recommended_[^"]*"',
            r'name="survivor_[^"]*"\s+value="([^"]*)"',
            r'value="([^"]*)"\s+name="survivor_[^"]*"',
            r"<code>(\d{6,18})</code>",
        ]
        for pattern in structured:
            for match in re.finditer(pattern, html or "", flags=re.IGNORECASE):
                _add_token(match.group(1))
        if found:
            return found
        # Map upload preview: bare table cells with seed IDs only.
        for match in re.finditer(
            r"<td[^>]*>\s*(\d{6,18})\s*</td>", html or "", flags=re.IGNORECASE
        ):
            _add_token(match.group(1))
        return found

    def _ids_visible_on_page(
        self, html: str, allowlist: SeedIdAllowlist
    ) -> tuple[set[str], set[str]]:
        """Return (mentioned_seed_ids, foreign_ids) from projected UI work set."""

        projected = self._extract_projected_record_ids(html)
        mentioned = projected & set(allowlist.ids)
        foreign = projected - set(allowlist.ids)
        return mentioned, foreign

    def _assert_html_ids_match_allowlist(
        self,
        html: str,
        allowlist: SeedIdAllowlist,
        *,
        stage: str,
    ) -> None:
        """Compare projected page work-set IDs to the seed allowlist."""

        mentioned, foreign = self._ids_visible_on_page(html, allowlist)
        # Never put raw IDs in assertion messages (evidence safety).
        self.assertEqual(
            len(foreign),
            0,
            f"{stage}: foreign/extra record ID count={len(foreign)} "
            f"(digests suppressed)",
        )
        self.assertEqual(
            len(mentioned),
            2,
            f"{stage}: expected both allowlist IDs on page; found={len(mentioned)} "
            f"projected_count={len(mentioned) + len(foreign)}",
        )
        allowlist.assert_equals(mentioned, stage=stage)

    def _assert_one_survivor_one_loser(
        self, html: str, allowlist: SeedIdAllowlist, *, stage: str
    ) -> None:
        """Enforce plan shape from merge template markup.

        Template shape (crm_duplicate_journey_merge.html)::

            · survivor <code>{{ selected_survivor_id }}</code>
            · members <code>id</code>, <code>id</code>
        """

        # Match actual template: survivor <code>ID</code>
        survivors = {
            m.strip()
            for m in re.findall(
                r"(?is)survivor\s*<code>\s*([^<]+?)\s*</code>",
                html,
            )
            if m.strip()
        }
        # Members block: · members <code>a</code>, <code>b</code>
        members: set[str] = set()
        for block in re.finditer(
            r"(?is)·\s*members\s*((?:\s*<code>\s*[^<]+?\s*</code>\s*,?)*)",
            html,
        ):
            members.update(
                m.strip()
                for m in re.findall(r"<code>\s*([^<]+?)\s*</code>", block.group(1))
                if m.strip()
            )
        # Also accept allowlist IDs present only as member codes.
        if not members:
            members = {
                rid
                for rid in allowlist.ids
                if re.search(
                    rf"<code>\s*{re.escape(rid)}\s*</code>",
                    html,
                )
            }
        # Constrain to allowlist.
        survivors &= set(allowlist.ids)
        members &= set(allowlist.ids)
        losers = members - survivors
        # If members missing but survivor present, loser is the other allowlist ID.
        if survivors and not members and len(allowlist.ids) == 2:
            losers = set(allowlist.ids) - survivors
            members = set(allowlist.ids)
        self.assertEqual(
            len(survivors),
            1,
            f"{stage}: expected exactly 1 survivor "
            f"(found_count={len(survivors)})",
        )
        self.assertEqual(
            len(losers),
            1,
            f"{stage}: expected exactly 1 loser "
            f"(found_count={len(losers)})",
        )
        self.assertEqual(
            survivors | losers,
            set(allowlist.ids),
            f"{stage}: survivor/loser set must equal allowlist",
        )

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
        self.assertIsNotNone(match, "no connected CRM on start page")
        return match.group(1)

    def _wait_for_review(self, browser: requests.Session, session_id: str) -> str:
        deadline = time.monotonic() + 240.0
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        last = ""
        while time.monotonic() < deadline:
            response = browser.get(self._url(path), timeout=90, allow_redirects=True)
            last = response.text
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                return last
            if "Review merge plan" in last:
                return last
            if "No duplicate groups found" in last:
                self.fail(
                    "unexpected zero groups after mapping (seed pair must share "
                    f"name/domain): {last[:600]}"
                )
            if "Analysis failed" in last or "message error" in last:
                self.fail(f"analysis failed before review: {last[:800]}")
            time.sleep(0.4)
        self.fail(f"timed out for review: {last[:800]}")

    def _submit_review_approve_all(
        self, browser: requests.Session, session_id: str, first_html: str
    ) -> str:
        html = first_html
        for _ in range(8):
            if html_shows_unfrozen_merge_plan(html):
                return html
            if "crm-duplicate-review-form" not in html:
                return self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                ).text
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
                    p.strip()
                    for p in data[f"allowed_{gid}"].split(",")
                    if p.strip()
                }
                if "approve" in allowed and recommended:
                    data[f"action_{gid}"] = "approve"
                    data[f"survivor_{gid}"] = recommended
                elif "decline" in allowed:
                    data[f"action_{gid}"] = "decline"
                else:
                    data[f"action_{gid}"] = next(iter(allowed))
            post = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/review/",
                data,
                timeout=180,
            )
            html = post.text
            if post.url and "merge" in post.url:
                return html
        self.fail("did not reach merge")

    def _poll_mutation_status(self, browser: requests.Session, html: str) -> str:
        """Poll mutation-status endpoint until completed/rejected (as 4A/7B)."""

        deadline = time.monotonic() + 180.0
        current = html
        while time.monotonic() < deadline:
            urls = re.findall(r'data-mutation-status-url="([^"]+)"', current)
            if not urls:
                if not html_has_pending_mutation_surface(current):
                    # Prefer terminal markers when present.
                    if (
                        "Groups processed" in current
                        or "No live CRM changes were authorized" in current
                        or "Run summary" in current
                    ):
                        return current
                    # Keep refreshing workflow briefly for late terminal render.
            for rel in urls:
                try:
                    status = browser.get(self._url(rel), timeout=20)
                    if status.status_code != 200:
                        continue
                    body = status.json()
                    if body.get("status") in {"completed", "rejected"}:
                        redirect = body.get("redirect_url") or ""
                        if redirect:
                            refreshed = browser.get(
                                self._url(redirect),
                                timeout=60,
                                allow_redirects=True,
                            )
                            current = refreshed.text
                except (requests.RequestException, ValueError, KeyError):
                    pass
            time.sleep(0.2)
            match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", current)
            if not match and urls:
                match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", urls[0])
            if match:
                refreshed = browser.get(
                    self._url(f"/sessions/{match.group(1)}/workflow/"),
                    timeout=60,
                    allow_redirects=True,
                )
                current = refreshed.text
                if (
                    "Groups processed" in current
                    or "No live CRM changes were authorized" in current
                ) and not html_has_pending_mutation_surface(current):
                    return current
        return current

    def _finalize_and_authorize(
        self,
        browser: requests.Session,
        session_id: str,
        merge_html: str,
        *,
        mode: str,
    ) -> str:
        """Authorize only when plan is already frozen (Authorize merge step visible).

        Callers that need freeze must finalize first and pass the frozen page.
        """

        page = merge_html
        if AUTHORIZE_MERGE_STEP_LABEL not in page:
            # Backward-compatible: finalize if still on approve control.
            if APPROVE_MERGE_PLAN_LABEL in page:
                fin = self._post_form(
                    browser,
                    f"/sessions/{session_id}/crm-duplicates/merge/",
                    {
                        "csrfmiddlewaretoken": self._extract_csrf(page),
                        "form_token": self._extract_form_token(page),
                        "merge_action": "finalize",
                    },
                    timeout=180,
                )
                page = (
                    fin.text
                    if AUTHORIZE_MERGE_STEP_LABEL in fin.text
                    else self._get(
                        browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                    ).text
                )
        self.assertIn(
            AUTHORIZE_MERGE_STEP_LABEL,
            page,
            "expected approved plan before authorize",
        )
        auth = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(page),
                "form_token": self._extract_form_token(page),
                "merge_action": "authorize",
                "selected_mode": mode,
            },
            timeout=180,
        )
        html = auth.text
        if "/workflow/" not in (auth.url or ""):
            html = self._get(browser, f"/sessions/{session_id}/workflow/").text
        html = self._poll_mutation_status(browser, html)
        # Final reload for stable terminal presentation.
        html = self._get(browser, f"/sessions/{session_id}/workflow/").text
        return html

    def test_live_production_canary_seed_allowlist_dry_run_execute_cleanup(self):
        env_path = _resolve_hs_env_path()
        if env_path is None:
            self.fail(
                "Live 4B enabled but HS env file missing. Set EASYIMPORTS_HS_ENV_FILE "
                f"or place credentials under {_DEFAULT_HS_ENV_CANDIDATES[0]}."
            )
        values = _load_dotenv_keys(env_path)
        # Re-check production authorization (fail closed before seed).
        authorization = require_production_canary_authorization()
        token = (
            values.get("EASYIMPORTS_HUBSPOT_ACCESS_TOKEN")
            or values.get("EASYIMPORTS_HUBSPOT_SERVICE_KEY")
            or ""
        ).strip()
        hub_id = str(values.get("EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID") or "").strip()
        if not token or not hub_id:
            self.fail("Live 4B missing access token and/or expected hub id.")

        portal = _build_portal(token, hub_id)
        registry = CleanupRegistry()
        run_tag = uuid.uuid4().hex[:8]
        marker = f"{MARKER_PREFIX}-{run_tag}"
        evidence: dict[str, Any] = {
            "phase": "4B",
            "mode": "production_canary",
            "authorization": authorization,
            "marker_prefix": MARKER_PREFIX,
            "hub_id_digest": "sha256:"
            + hashlib.sha256(hub_id.encode("utf-8")).hexdigest()[:16],
            "steps": {},
        }
        allowlist: SeedIdAllowlist | None = None

        def archive(rid: str) -> None:
            portal.archive_company(rid)

        def is_active(rid: str) -> bool:
            rec = portal.get(rid)
            return rec is not None and not getattr(rec, "archived", False)

        try:
            browser = self._browser()
            self._register_and_connect_hubspot(browser, token=token, hub_id=hub_id)
            evidence["steps"]["frontend_connected"] = True

            # Seed exactly two Companies that form ONE duplicate pair (same
            # name + domain, as storage 7B). Distinct names/domains yield zero
            # groups and never reach review. Register each immediately.
            pair_name = f"EI 4B Holdings ({marker})"
            pair_domain = f"ei-4b-canary-{run_tag}.example.com"

            def create_one(index: int) -> str:
                created = portal.create_company(
                    {
                        "name": pair_name,
                        "domain": pair_domain,
                        # Exact marker for description EQ search (independent
                        # cleanup proof; also finds lost-response orphans).
                        "description": marker,
                    }
                )
                return str(created.record_id)

            allowlist = seed_pair_with_immediate_registration(registry, create_one)
            self.assertEqual(len(registry.created_ids), 2)
            self._last_registry_ids = list(registry.created_ids)
            evidence["steps"]["seeded"] = {
                "count": 2,
                "id_digest": allowlist.digest(),
            }

            flat_ids = list(registry.created_ids)
            names = [pair_name, pair_name]
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
            self.assertIn("map-record-id", posted.url)
            session_id = self._session_id_from_url(posted.url)
            # Upload CSV stage: only seed IDs were submitted (mapping not confirmed yet).
            self._assert_html_ids_match_allowlist(
                posted.text, allowlist, stage="mapping_upload_preview"
            )
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
            # Confirmed mapping output → progress/review page.
            if "Review duplicate groups" in mapped.text:
                review_html = mapped.text
            else:
                review_html = self._wait_for_review(browser, session_id)
            self._assert_html_ids_match_allowlist(
                review_html, allowlist, stage="mapping_confirmed_and_review"
            )
            merge_html = self._submit_review_approve_all(
                browser, session_id, review_html
            )
            # Reviewed result (pre-freeze merge summary).
            self._assert_html_ids_match_allowlist(
                merge_html, allowlist, stage="reviewed_result"
            )
            self._assert_one_survivor_one_loser(
                merge_html, allowlist, stage="reviewed_result_plan_shape"
            )

            left_id, right_id = flat_ids[0], flat_ids[1]
            self.assertEqual(
                portal.authoritative_state(left_id, right_id),
                "unchanged",
            )
            # Freeze then authorize dry_run — frozen plan checked after finalize.
            freeze = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/merge/",
                {
                    "csrfmiddlewaretoken": self._extract_csrf(merge_html),
                    "form_token": self._extract_form_token(merge_html),
                    "merge_action": "finalize",
                },
                timeout=180,
            )
            freeze_page = (
                freeze
                if AUTHORIZE_MERGE_STEP_LABEL in freeze.text
                else self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
            )
            self._assert_html_ids_match_allowlist(
                freeze_page.text, allowlist, stage="frozen_plan"
            )
            self._assert_one_survivor_one_loser(
                freeze_page.text, allowlist, stage="frozen_plan_shape"
            )
            self._assert_html_ids_match_allowlist(
                freeze_page.text, allowlist, stage="authorization_work_set"
            )
            dry_html = self._finalize_and_authorize(
                browser, session_id, freeze_page.text, mode="dry_run"
            )
            self.assertEqual(
                portal.authoritative_state(left_id, right_id),
                "unchanged",
            )
            evidence["steps"]["dry_run_zero_write"] = True
            self.assertTrue(
                "No live CRM changes were authorized" in dry_html
                or "Groups processed" in dry_html
                or "Run summary" in dry_html,
                f"dry_run workflow not terminal (snippet): {dry_html[:600]}",
            )

            # Separate execute journey.
            session_id2, merge_html2 = self._upload_to_merge_ids(
                browser, flat_ids, names
            )
            freeze2 = self._post_form(
                browser,
                f"/sessions/{session_id2}/crm-duplicates/merge/",
                {
                    "csrfmiddlewaretoken": self._extract_csrf(merge_html2),
                    "form_token": self._extract_form_token(merge_html2),
                    "merge_action": "finalize",
                },
                timeout=180,
            )
            freeze2_page = (
                freeze2
                if AUTHORIZE_MERGE_STEP_LABEL in freeze2.text
                else self._get(
                    browser, f"/sessions/{session_id2}/crm-duplicates/merge/"
                )
            )
            self._assert_html_ids_match_allowlist(
                freeze2_page.text, allowlist, stage="pre_execute_frozen_plan"
            )
            self._assert_one_survivor_one_loser(
                freeze2_page.text, allowlist, stage="pre_execute_plan_shape"
            )
            ex_html = self._finalize_and_authorize(
                browser, session_id2, freeze2_page.text, mode="execute"
            )
            deadline = time.monotonic() + 90.0
            verified = False
            while time.monotonic() < deadline:
                st = portal.authoritative_state(left_id, right_id)
                st2 = portal.authoritative_state(right_id, left_id)
                if st == "verified" or st2 == "verified":
                    verified = True
                    break
                time.sleep(1.0)
            self.assertTrue(verified, "expected authoritative_state=verified")
            evidence["steps"]["execute_one_merge"] = True
            evidence["steps"]["terminal_groups"] = "Groups processed" in ex_html

            self._last_registry_ids = list(registry.created_ids)
            cleanup = registry.run_finally(archive, is_active=is_active)
            # Independent registered-ID re-get: zero active remain (fail-closed).
            marker_active = self._count_active_marked_companies(portal, marker)
            self.assertEqual(
                marker_active,
                0,
                f"active marked companies remain after cleanup count={marker_active}",
            )
            evidence["cleanup"] = {
                **cleanup,
                "marker_active_remaining": marker_active,
                "marker_prefix": MARKER_PREFIX,
            }
            evidence["allowlist_converged"] = True
            evidence["result"] = "passed"
            _write_evidence(evidence)
        except BaseException as primary_exc:
            cleanup_status = None
            cleanup_error = None
            marker_active = -1
            try:
                self._last_registry_ids = list(registry.created_ids)
                cleanup_status = registry.run_finally(archive, is_active=is_active)
                marker_active = self._count_active_marked_companies(portal, marker)
                if marker_active != 0:
                    raise RuntimeError(
                        f"marker_active_remaining={marker_active} after cleanup"
                    )
            except BaseException as cleanup_exc:  # noqa: BLE001
                # Sanitize: never embed raw record IDs in evidence strings.
                cleanup_error = type(cleanup_exc).__name__
                cleanup_status = {
                    "created_count": len(registry.created_ids),
                    "cleanup_attempt_count": len(registry.cleanup_attempts),
                    "cleanup_success_count": len(registry.cleaned_ids),
                    "cleanup_error_count": len(registry.cleanup_errors) + 1,
                    "cleanup_ok": False,
                    "id_digest": digest_ids(registry.created_ids),
                    "marker_active_remaining": marker_active,
                }
            evidence["result"] = "failed"
            evidence["primary_error_class"] = type(primary_exc).__name__
            evidence["cleanup"] = cleanup_status
            evidence["cleanup_error_class"] = cleanup_error
            try:
                _write_evidence(evidence)
            except Exception:
                pass
            if cleanup_error is not None:
                raise RuntimeError(
                    f"Primary failure class={type(primary_exc).__name__}; "
                    f"cleanup also failed class={cleanup_error}"
                ) from primary_exc
            raise

    def _count_active_marked_companies(self, portal, marker: str) -> int:
        """Independently prove zero active marked Companies (fail-closed).

        Uses marker-filtered CRM search (description EQ), not unfiltered
        ``active_population``. Finds lost/transient create orphans that were
        never registered. Transport/search failures raise (never false zero).
        """

        if not marker:
            raise AssertionError("marker required for independent cleanup verify")
        if not hasattr(portal, "search_companies_by_description_marker"):
            raise CleanupRegistryError(
                "portal missing marker search capability; fail closed"
            )
        if not hasattr(portal, "get"):
            raise CleanupRegistryError(
                "portal missing get() capability for marker verify; fail closed"
            )

        def search_marked_page(marker_s: str, cursor: str | None):
            return portal.search_companies_by_description_marker(
                marker_s, cursor=cursor
            )

        registered = list(getattr(self, "_last_registry_ids", []) or [])
        return count_active_marked_companies(
            marker=marker,
            search_marked_page=search_marked_page,
            registered_ids=registered,
            get_record=portal.get,
        )

    def _upload_to_merge_ids(
        self,
        browser: requests.Session,
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
        merge_html = self._submit_review_approve_all(
            browser, session_id, review_html
        )
        return session_id, merge_html
