"""Phase 3: dual-process barrier proof for CRM-dupe first-batch hang.

True separate API + Django processes (not Django TestClient, not FastAPI
TestClient). A file barrier holds fake provider work until the test has
observed HTTP 202 and the Django redirect, then release lets progress
reconcile the completed first batch.

Authority:
project_implementations/completed_projects/crm_duplicate_first_batch_synchronous_authorization_hang.md
Phase 3.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import requests
from django.test import SimpleTestCase

# Keep these string-identical to mappings_2.integrations.crm.provider_test_barrier.
# This module must not import mappings_2.
BARRIER_DIR_ENV = "EASYIMPORTS_PROVIDER_TEST_BARRIER_DIR"
BARRIER_TIMEOUT_ENV = "EASYIMPORTS_PROVIDER_TEST_BARRIER_TIMEOUT_SECONDS"
BLOCK_NAME = "block"
WAITING_NAME = "waiting"
RELEASE_NAME = "release"


PAIR_COUNT = 2
APPLY_KIND = "crm_duplicate_implied_reference_authorization"
OAUTH_KIND = "crm_connection_oauth_complete"


class CrmDuplicateFirstBatchHangPhase3Tests(SimpleTestCase):
    """Network-free dual-process HTTP proof (not TestClient)."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory()
        root = Path(cls.state_dir.name)
        cls.api_state = root / "api_state"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        cls.barrier_dir = root / "provider_barrier"
        cls.api_state.mkdir(parents=True, exist_ok=True)
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
    def _run_seed_helper(cls, *extra: str) -> None:
        helper = cls.repo / "web" / "tools" / "seed_fake_crm_org.py"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(cls.repo)
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--state-root",
                str(cls.api_state),
                *extra,
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
    def _seed_pairs(cls) -> None:
        cls._run_seed_helper(
            "--pair-count",
            str(PAIR_COUNT),
            "--clear-injections",
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
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("Phase 3 API process did not start.")

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

    @classmethod
    def _reset_api(cls) -> None:
        cls._stop_api()
        if cls.api_state.exists():
            shutil.rmtree(cls.api_state)
        cls.api_state.mkdir(parents=True, exist_ok=True)
        cls._seed_pairs()
        cls._start_api()

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
                "Phase 3 Django migrate failed:\n"
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
        for _ in range(80):
            try:
                response = requests.get(landing, timeout=0.25)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.django_process.terminate()
        raise RuntimeError("Phase 3 Django runserver did not start.")

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
        cls.state_dir.cleanup()
        super().tearDownClass()

    def setUp(self):
        self._clear_barrier()

    def tearDown(self):
        self._release_barrier()

    def _clear_barrier(self) -> None:
        for name in (BLOCK_NAME, WAITING_NAME, RELEASE_NAME):
            path = self.barrier_dir / name
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

    def _arm_barrier(self) -> None:
        self._clear_barrier()
        (self.barrier_dir / BLOCK_NAME).write_text("1", encoding="utf-8")

    def _release_barrier(self) -> None:
        (self.barrier_dir / RELEASE_NAME).write_text("1", encoding="utf-8")
        block = self.barrier_dir / BLOCK_NAME
        if block.exists():
            try:
                block.unlink()
            except OSError:
                pass

    def _wait_until_entered(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        waiting = self.barrier_dir / WAITING_NAME
        while time.monotonic() < deadline:
            if waiting.is_file():
                return
            time.sleep(0.05)
        self.fail("provider never entered the test barrier")

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "Phase3HangBrowser/1.0"})
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
        response = browser.get(self._url(path), timeout=30, allow_redirects=True)
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
        timeout: float = 30,
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
            timeout=timeout,
            allow_redirects=allow_redirects,
        )

    def _sqlite(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        last_error = None
        for _ in range(25):
            try:
                conn = sqlite3.connect(str(self.django_db), timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(sql, params).fetchall()
                finally:
                    conn.close()
                return rows
            except sqlite3.OperationalError as exc:
                last_error = exc
                time.sleep(0.05)
        raise AssertionError(f"django sqlite query failed: {last_error}")

    def _mutations(self, kind: str) -> list[sqlite3.Row]:
        return self._sqlite(
            "SELECT mutation_kind, state, http_status, idempotency_key, "
            "logical_action_identity, session_id "
            "FROM importer_apimutation WHERE mutation_kind = ? "
            "ORDER BY created_at, id",
            (kind,),
        )

    def _connect_fake(self, browser: requests.Session) -> None:
        page = self._get(browser, "/crm/connections/")
        csrf = (
            self._extract_csrf(page.text)
            if "csrfmiddlewaretoken" in page.text
            else ""
        )
        if not csrf and "csrftoken" in browser.cookies:
            csrf = browser.cookies["csrftoken"]
        response = self._post_form(
            browser,
            "/crm/connect/",
            {"provider_key": "fake", "csrfmiddlewaretoken": csrf},
        )
        self.assertIn(response.status_code, {200, 302}, response.text[:500])
        connections = self._get(browser, "/crm/connections/")
        self.assertIn("fake", connections.text.lower(), connections.text[:800])

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(
            r'<option[^>]+value="(crm_conn_[^"]+)"',
            html,
        )
        self.assertIsNotNone(match, "no connected CRM option on start page")
        return match.group(1)

    def test_first_batch_implied_ra_returns_before_barrier_release(self):
        self._reset_api()
        browser = self._browser()
        self._connect_fake(browser)

        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)

        self._arm_barrier()
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
            allow_redirects=False,
        )
        messages = re.findall(
            r'<li class="[^"]*">([^<]+)</li>|<ul class="form-errors">\s*<li>([^<]+)',
            posted.text,
        )
        flash = " | ".join(
            part for group in messages for part in group if part
        )
        self.assertEqual(
            posted.status_code,
            302,
            f"start POST {posted.status_code} flash={flash!r} "
            f"mutations={self._mutations(APPLY_KIND)} body={posted.text[800:2400]}",
        )
        location = posted.headers.get("Location") or ""
        self.assertIn("crm-duplicates/progress", location, location)
        session_id = self._session_id_from_url(location)

        apply_rows = self._mutations(APPLY_KIND)
        self.assertEqual(len(apply_rows), 1, apply_rows)
        self.assertEqual(apply_rows[0]["state"], "pending")
        self.assertEqual(apply_rows[0]["http_status"], 202)
        idempotency_key = apply_rows[0]["idempotency_key"]

        self._wait_until_entered()
        status = requests.get(
            f"{self.api_base}/v1/mutations/{idempotency_key}",
            timeout=10,
        )
        self.assertEqual(status.status_code, 200, status.text[:400])
        body = status.json()
        self.assertEqual(body.get("mutation_kind"), APPLY_KIND)
        self.assertEqual(body.get("status"), "pending")

        progress = self._get(
            browser, f"/sessions/{session_id}/crm-duplicates/progress/"
        )
        self.assertIn("crm-duplicates/progress", progress.url)
        self.assertIn("Reading and analyzing records", progress.text)
        self.assertIn("in progress", progress.text)
        self.assertNotIn("Review duplicate groups", progress.text)
        self.assertIn(
            "reading records from the connected crm in the background",
            progress.text.lower(),
        )

        progress_token = self._extract_form_token(progress.text)
        progress_csrf = self._extract_csrf(progress.text)
        retried = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/progress/",
            {
                "csrfmiddlewaretoken": progress_csrf,
                "form_token": progress_token,
            },
            allow_redirects=True,
        )
        self.assertIn("crm-duplicates/progress", retried.url)
        self.assertEqual(len(self._mutations(APPLY_KIND)), 1)

        journal_opts = self._sqlite(
            "SELECT options FROM importer_importsession "
            "WHERE lower(replace(id, '-', '')) = lower(replace(?, '-', ''))",
            (session_id,),
        )
        self.assertTrue(journal_opts)
        options_text = journal_opts[0]["options"] or ""
        self.assertIn("mutation_journal_id", options_text)
        self.assertIn("apply_root_form_instance", options_text)
        self.assertIn("read_grant_id", options_text)

        self._release_barrier()
        deadline = time.monotonic() + 30.0
        last = retried.text
        while time.monotonic() < deadline:
            page = browser.get(
                self._url(f"/sessions/{session_id}/crm-duplicates/progress/"),
                timeout=30,
                allow_redirects=True,
            )
            last = page.text
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                break
            if "awaiting_review" in last or "No duplicate groups found" in last:
                break
            time.sleep(0.2)
        else:
            self.fail(f"progress never left first-batch pending: {last[:800]}")

        finished = self._mutations(APPLY_KIND)
        self.assertEqual(len(finished), 1)
        api_done = requests.get(
            f"{self.api_base}/v1/mutations/{idempotency_key}",
            timeout=10,
        )
        self.assertEqual(api_done.status_code, 200, api_done.text[:400])
        self.assertEqual(api_done.json().get("status"), "completed")
        # Progress may land on review from the workflow projection before
        # Django persists the frozen 200 onto the apply journal row.
        self.assertIn(finished[0]["state"], {"pending", "completed"})

    def test_oauth_complete_returns_before_barrier_release(self):
        self._reset_api()
        browser = self._browser()
        page = self._get(browser, "/crm/connections/")
        csrf = (
            self._extract_csrf(page.text)
            if "csrfmiddlewaretoken" in page.text
            else browser.cookies.get("csrftoken", "")
        )
        self._arm_barrier()
        posted = self._post_form(
            browser,
            "/crm/connect/",
            {"provider_key": "fake", "csrfmiddlewaretoken": csrf},
            allow_redirects=False,
        )
        self.assertEqual(posted.status_code, 302, posted.text[:800])
        location = posted.headers.get("Location") or ""
        self.assertTrue(
            location.endswith("/crm/connections/")
            or location.rstrip("/").endswith("/crm/connections"),
            location,
        )

        oauth_rows = self._mutations(OAUTH_KIND)
        self.assertGreaterEqual(len(oauth_rows), 1, oauth_rows)
        latest = oauth_rows[-1]
        self.assertEqual(latest["state"], "pending")
        self.assertEqual(latest["http_status"], 202)

        self._wait_until_entered()
        status = requests.get(
            f"{self.api_base}/v1/mutations/{latest['idempotency_key']}",
            timeout=10,
        )
        self.assertEqual(status.status_code, 200, status.text[:400])
        self.assertEqual(status.json().get("status"), "pending")

        hub = self._get(browser, "/crm/connections/")
        self.assertIn("CRM authorization is in progress", hub.text)
        self.assertNotIn("CRM connection established.", hub.text)

        self._release_barrier()
        deadline = time.monotonic() + 30.0
        last = hub.text
        while time.monotonic() < deadline:
            page = self._get(browser, "/crm/connections/")
            last = page.text
            rows = self._mutations(OAUTH_KIND)
            if rows and rows[-1]["state"] == "completed" and "fake" in last.lower():
                if "CRM authorization is in progress" not in last:
                    break
            time.sleep(0.2)
        else:
            self.fail(f"oauth complete never settled: {last[:800]}")
        self.assertEqual(self._mutations(OAUTH_KIND)[-1]["state"], "completed")
        self.assertEqual(len(self._mutations(OAUTH_KIND)), len(oauth_rows))
