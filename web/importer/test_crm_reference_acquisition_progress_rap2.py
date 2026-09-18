"""RAP-2: CRM-dupe progress page renders the live read count.

Network-free Django render proofs plus a mandatory dual-process HTTP
boundary (real API + real Django; not TestClient). Django does not import
mappings_2.

Authority:
mappings_2/.../crm_reference_acquisition_live_read_progress.md Phase RAP-2.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .api_client import EasyImportsApiClient
from .api_models_generated import WorkflowResource
from .journey_views import (
    _read_progress_collected_count,
    _read_progress_context,
    _read_progress_noun,
)
from .models import ImportSession
from .workflow_state import OWNER_SESSION_KEY

# Keep identical to mappings_2.integrations.crm.provider_test_barrier.
# This module must not import mappings_2.
BARRIER_DIR_ENV = "EASYIMPORTS_PROVIDER_TEST_BARRIER_DIR"
BARRIER_TIMEOUT_ENV = "EASYIMPORTS_PROVIDER_TEST_BARRIER_TIMEOUT_SECONDS"
BLOCK_NAME = "block"
WAITING_NAME = "waiting"
RELEASE_NAME = "release"
PASS_ONCE_NAME = "pass_once"

# Fake CRM defaults: page_size=2, DEFAULT_PAGES_PER_CYCLE=5 → 10 records/cycle.
PAGES_PER_CYCLE = 5
PAIR_COUNT = 13  # 26 companies → cycle 1 = 10, cycle 2 = 20, remainder blocked
PROOF_REPEATS = 3
CONNECTION_OPTION_RE = re.compile(r'<option[^>]+value="(crm_conn_[^"]+)"')
SELECTABLE_CONNECTION_TIMEOUT_SECONDS = 15.0


class Rap2ReadProgressDisplayTests(TestCase):
    """Network-free render: count present vs static copy when absent."""

    def _route_with_projection(self, *, options, projection):
        owner = uuid4()
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(owner)
        browser_session.save()
        draft = ImportSession.objects.create(
            owner_id=owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options=options,
        )
        route = reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": draft.id},
        )
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient, "workflow", return_value=projection
            ):
                return self.client.get(route)

    def test_companies_count_renders_on_read_step(self):
        response = self._route_with_projection(
            options={
                "run_id": "run-rap2-companies",
                "entity_family": "company",
            },
            projection=_running_projection(
                "run-rap2-companies",
                progress={"collected_count": 12400},
            ),
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("12,400 Companies read so far", body)
        self.assertIn("read-progress-lede-count", body)
        self.assertIn("read-progress-status", body)
        self.assertIn("progress-step-read has-live-count", body)
        self.assertIn('data-read-progress-count="12400"', body)
        self.assertIn('data-read-progress-noun="Companies"', body)
        self.assertIn("in progress", body)

    def test_people_noun_from_person_entity(self):
        response = self._route_with_projection(
            options={
                "run_id": "run-rap2-people",
                "entity_family": "person",
            },
            projection=_running_projection(
                "run-rap2-people",
                progress={"collected_count": 3},
            ),
        )
        body = response.content.decode("utf-8")
        self.assertIn("3 People read so far", body)
        self.assertIn("read-progress-lede-count", body)
        self.assertIn("read-progress-status", body)
        self.assertIn('data-read-progress-noun="People"', body)

    def test_null_field_keeps_static_copy(self):
        response = self._route_with_projection(
            options={
                "run_id": "run-rap2-null",
                "entity_family": "company",
            },
            projection=_running_projection("run-rap2-null", progress=None),
        )
        body = response.content.decode("utf-8")
        self.assertIn("Read active CRM records", body)
        self.assertNotIn("read so far", body)
        self.assertNotIn("data-read-progress-count", body)
        self.assertNotIn("read-progress-lede-count", body)
        self.assertNotIn("read-progress-status", body)
        self.assertNotIn("has-live-count", body)
        self.assertRegex(body, r"Read active CRM records</strong>\s+—")

    def test_helpers_fail_closed_on_bad_count_or_entity(self):
        self.assertEqual(_read_progress_noun("company"), "Companies")
        self.assertEqual(_read_progress_noun("person"), "People")
        self.assertIsNone(_read_progress_noun(""))
        self.assertIsNone(
            _read_progress_collected_count(
                {"reference_acquisition_progress": {"collected_count": True}}
            )
        )
        ctx = _read_progress_context(
            {"reference_acquisition_progress": {"collected_count": 9}},
            {"entity_family": ""},
        )
        self.assertIsNone(ctx["read_progress_count"])
        self.assertIsNone(ctx["read_progress_copy"])
        present = _read_progress_context(
            {"reference_acquisition_progress": {"collected_count": 12400}},
            {"entity_family": "company"},
        )
        self.assertEqual(present["read_progress_copy"], "12,400 Companies read so far")


class Rap2DualProcessHttpTests(SimpleTestCase):
    """API serialize + generated-client parse + Django render, two cycles."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_rap2_")
        root = Path(cls.state_dir.name)
        cls.api_state = root / "api_state"
        cls.machine = root / "LocalAppData"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        cls.barrier_dir = root / "provider_barrier"
        cls.api_state.mkdir(parents=True, exist_ok=True)
        cls.machine.mkdir(parents=True, exist_ok=True)
        cls.media_root.mkdir(parents=True, exist_ok=True)
        cls.barrier_dir.mkdir(parents=True, exist_ok=True)

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
        cls._seed_pairs()
        cls._start_api()
        cls._start_django()

    @classmethod
    def _api_env(cls) -> dict:
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.api_port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = str(cls.api_state)
        environment["EASYIMPORTS_SECRET_STORE"] = "file_insecure"
        environment["EASYIMPORTS_SF_OAUTH_EXCHANGE"] = "synthetic"
        environment["LOCALAPPDATA"] = str(cls.machine)
        environment["XDG_DATA_HOME"] = str(cls.machine / "xdg")
        environment[BARRIER_DIR_ENV] = str(cls.barrier_dir)
        environment[BARRIER_TIMEOUT_ENV] = "30"
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
    def _seed_pairs(cls) -> None:
        helper = cls.repo / "web" / "tools" / "seed_fake_crm_org.py"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(cls.repo)
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--state-root",
                str(cls.api_state),
                "--pair-count",
                str(PAIR_COUNT),
                "--clear-injections",
            ],
            cwd=str(cls.repo),
            env=environment,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "seed_fake_crm_org failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )

    @classmethod
    def _start_api(cls) -> None:
        cls.api_process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health = f"{cls.api_base}/health"
        for _ in range(100):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("RAP-2 API process did not start.")

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
                "RAP-2 Django migrate failed:\n"
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
        for _ in range(100):
            try:
                response = requests.get(landing, timeout=0.25)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.django_process.terminate()
        raise RuntimeError("RAP-2 Django runserver did not start.")

    @classmethod
    def _stop_api(cls) -> None:
        process = getattr(cls, "api_process", None)
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        cls.api_process = None

    @classmethod
    def _stop_django(cls) -> None:
        process = getattr(cls, "django_process", None)
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        cls.django_process = None

    @classmethod
    def tearDownClass(cls):
        cls._stop_django()
        cls._stop_api()
        cls.state_dir.cleanup()
        super().tearDownClass()

    def setUp(self):
        self._clear_barrier()

    def tearDown(self):
        self._release_all()

    def _clear_barrier(self) -> None:
        for name in (BLOCK_NAME, WAITING_NAME, RELEASE_NAME, PASS_ONCE_NAME):
            path = self.barrier_dir / name
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

    def _arm_barrier(self) -> None:
        self._clear_barrier()
        (self.barrier_dir / BLOCK_NAME).write_text("1", encoding="utf-8")

    def _release_all(self) -> None:
        (self.barrier_dir / RELEASE_NAME).write_text("1", encoding="utf-8")
        block = self.barrier_dir / BLOCK_NAME
        if block.exists():
            try:
                block.unlink()
            except OSError:
                pass

    def _wait_until_entered(self, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        waiting = self.barrier_dir / WAITING_NAME
        while time.monotonic() < deadline:
            if waiting.is_file():
                return
            time.sleep(0.05)
        self.fail("provider never entered the test barrier")

    def _release_one_page(self) -> None:
        self._wait_until_entered()
        waiting = self.barrier_dir / WAITING_NAME
        if waiting.exists():
            try:
                waiting.unlink()
            except OSError:
                pass
        pass_once = self.barrier_dir / PASS_ONCE_NAME
        pass_once.write_text("1", encoding="utf-8")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not pass_once.is_file():
                return
            time.sleep(0.02)
        self.fail("provider did not consume pass_once")

    def _release_one_cycle(self) -> None:
        for _ in range(PAGES_PER_CYCLE):
            self._release_one_page()

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "RAP2DualProcess/1.0"})
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
        self.assertNotEqual(start, -1, "form_token missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def _extract_csrf(self, html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "csrfmiddlewaretoken missing from page")
        start += len(marker)
        end = html.find('"', start)
        self.assertNotEqual(end, -1)
        return html[start:end]

    def _session_id_from_url(self, url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        self.assertIsNotNone(match, f"session id missing from {url}")
        return match.group(1)

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=60, allow_redirects=True)
        self.assertIn(
            response.status_code,
            {200, 302},
            f"GET {path} -> {response.status_code}: {response.text[:500]}",
        )
        return response

    def _post_form(
        self,
        browser: requests.Session,
        path: str,
        data: dict,
        *,
        allow_redirects: bool = True,
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
            headers=headers,
            timeout=120,
            allow_redirects=allow_redirects,
        )

    def _connect_fake(self, browser: requests.Session) -> None:
        page = self._get(browser, "/crm/connections/")
        csrf = self._extract_csrf(page.text) if "csrfmiddlewaretoken" in page.text else ""
        if not csrf and "csrftoken" in browser.cookies:
            csrf = browser.cookies["csrftoken"]
        response = self._post_form(
            browser,
            "/crm/connect/",
            {
                "provider_key": "fake",
                "csrfmiddlewaretoken": csrf,
            },
        )
        self.assertIn(response.status_code, {200, 302}, response.text[:500])
        deadline = time.monotonic() + SELECTABLE_CONNECTION_TIMEOUT_SECONDS
        last = ""
        while time.monotonic() < deadline:
            connections = self._get(browser, "/crm/connections/")
            last = connections.text
            if "fake" in last.lower():
                return
            time.sleep(0.15)
        self.fail(f"fake connection never appeared on Connect CRM: {last[:800]}")

    def _selectable_connection_id(self, html: str) -> str | None:
        match = CONNECTION_OPTION_RE.search(html)
        return None if match is None else match.group(1)

    def _wait_for_selectable_connection(
        self,
        browser: requests.Session,
        *,
        timeout: float = SELECTABLE_CONNECTION_TIMEOUT_SECONDS,
    ) -> tuple[str, str]:
        """Poll the start page until a connected CRM option is selectable."""

        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            page = self._get(browser, "/crm/duplicate-journeys/")
            last = page.text
            connection_id = self._selectable_connection_id(last)
            if connection_id:
                return connection_id, last
            time.sleep(0.15)
        self.fail(f"no selectable CRM connection on start page: {last[:800]}")

    def _connection_id_from_start_page(self, html: str) -> str:
        connection_id = self._selectable_connection_id(html)
        self.assertIsNotNone(connection_id, "no connected CRM option on start page")
        return str(connection_id)

    def _owner_session_from_browser(self, browser: requests.Session) -> str:
        sessionid = browser.cookies.get("sessionid")
        self.assertIsNotNone(sessionid, "Django session cookie missing")
        conn = sqlite3.connect(str(self.django_db))
        try:
            row = conn.execute(
                "SELECT session_data FROM django_session WHERE session_key = ?",
                (sessionid,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "Django session row missing")
        from django.contrib.sessions.backends.db import SessionStore

        decoded = SessionStore().decode(row[0])
        owner = decoded.get(OWNER_SESSION_KEY)
        self.assertTrue(owner, f"owner id missing from session: {decoded!r}")
        return f"django-{owner}"

    def _run_id_from_html(self, html: str) -> str:
        match = re.search(r"Run <code>([^<]+)</code>", html)
        self.assertIsNotNone(match, "run id missing from progress technical details")
        return match.group(1).strip()

    def _html_count(self, html: str) -> int | None:
        match = re.search(r'data-read-progress-count="(\d+)"', html)
        if match is None:
            return None
        return int(match.group(1))

    def _api_progress(
        self, run_id: str, owner_session: str
    ) -> tuple[WorkflowResource, int | None]:
        response = requests.get(
            f"{self.api_base}/v1/workflows/{run_id}",
            headers={"X-Owner-Session": owner_session},
            timeout=15,
        )
        self.assertEqual(response.status_code, 200, response.text[:500])
        resource = WorkflowResource.model_validate(response.json())
        progress = resource.reference_acquisition_progress
        count = None if progress is None else int(progress.collected_count)
        return resource, count

    def _observe(self, html: str, owner_session: str) -> int | None:
        run_id = self._run_id_from_html(html)
        _resource, api_count = self._api_progress(run_id, owner_session)
        html_count = self._html_count(html)
        self.assertEqual(
            html_count,
            api_count,
            f"Django render {html_count!r} != API/client {api_count!r}",
        )
        if api_count is None:
            self.assertNotIn("read so far", html)
            self.assertNotIn("read-progress-status", html)
            self.assertNotIn("read-progress-lede-count", html)
        else:
            self.assertIn(f"{api_count:,} Companies read so far", html)
            self.assertIn("read-progress-lede-count", html)
            self.assertIn("read-progress-status", html)
            self.assertIn('data-read-progress-noun="Companies"', html)
        return api_count

    def test_count_increases_across_two_dual_process_polls(self):
        for attempt in range(1, PROOF_REPEATS + 1):
            with self.subTest(attempt=attempt):
                self._run_two_cycle_proof()

    def _run_two_cycle_proof(self) -> None:
        browser = self._browser()
        self._connect_fake(browser)
        connection_id, start_html = self._wait_for_selectable_connection(browser)
        self._arm_barrier()

        token = self._extract_form_token(start_html)
        csrf = self._extract_csrf(start_html)
        posted = self._post_form(
            browser,
            "/crm/duplicate-journeys/",
            {
                "csrfmiddlewaretoken": csrf,
                "form_token": token,
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "acquire_all",
            },
        )
        self.assertLess(posted.status_code, 500, posted.text[:500])
        session_id = self._session_id_from_url(posted.url)
        progress_path = f"/sessions/{session_id}/crm-duplicates/progress/"
        owner_session = self._owner_session_from_browser(browser)

        self._wait_until_entered()
        first_html = self._get(browser, progress_path).text
        first_count = self._observe(first_html, owner_session)
        self.assertIsNone(first_count)

        self._release_one_cycle()
        self._wait_until_entered()
        mid_html = self._get(browser, progress_path).text
        mid_count = self._observe(mid_html, owner_session)
        self.assertIsNotNone(mid_count)
        self.assertGreater(mid_count, 0)

        self._release_one_cycle()
        self._wait_until_entered()
        later_html = self._get(browser, progress_path).text
        later_count = self._observe(later_html, owner_session)
        self.assertIsNotNone(later_count)
        self.assertGreater(later_count, mid_count)
        self.assertIn("Companies read so far", later_html)
        self._release_all()
        self._clear_barrier()


def _running_projection(run_id: str, *, progress) -> dict:
    return {
        "run_id": run_id,
        "revision": 3,
        "workflow_key": "easyimports.duplicate_resolution",
        "workflow_version": 4,
        "status": "running",
        "stage": "reading_crm_records",
        "summary": {},
        "decision": None,
        "review_handoff": None,
        "effect_intent": None,
        "effect_grants": [],
        "target_provider_id": "fake",
        "reference_acquisition_progress": progress,
    }
