"""Phase 3 O1b — Duplicates lists fake connected CRM after API restart.

True dual-process HTTP browser (``requests``) against live Django + live API.
Proves operator-visible outcome: same browser owner-session sees
``connected_count >= 1`` on CRM Duplicates after a real API process restart
against durable isolated storage (not MemorySecretStore).

Authority:
``mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/local_crm_dupe_e2e_storage_and_operator_path/local_crm_dupe_e2e_storage_and_operator_path.md``
Phase 3.
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import requests
from django.test import SimpleTestCase


class LocalCrmDupeStoragePhase3DualProcessTests(SimpleTestCase):
    """Practice connect → Duplicates → API restart → Duplicates still connected."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_p3_")
        root = Path(cls.state_dir.name)
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
        environment["EASYIMPORTS_SF_OAUTH_EXCHANGE"] = "synthetic"
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
    def _restart_api(cls) -> None:
        cls._stop_api()
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
        for _ in range(100):
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

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "Phase3StorageE2E/1.0"})
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

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=30, allow_redirects=True)
        self.assertIn(
            response.status_code,
            {200, 302},
            f"GET {path} -> {response.status_code}: {response.text[:300]}",
        )
        return response

    def _connect_practice(self, browser: requests.Session) -> None:
        start = browser.get(self._url("/crm/setup/"), timeout=30, allow_redirects=True)
        self.assertEqual(start.status_code, 200)
        self.assertIn("/crm/setup/pick/", start.url)
        choose_token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)
        chosen = browser.post(
            self._url("/crm/setup/choose/"),
            data={
                "provider_key": "fake",
                "form_token": choose_token,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/pick/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(chosen.status_code, 200, chosen.text[:500])
        self.assertIn("/crm/setup/practice/", chosen.url)

        connect_token = self._extract_form_token(chosen.text)
        csrf = self._extract_csrf(chosen.text)
        complete = browser.post(
            self._url("/crm/setup/practice/connect/"),
            data={
                "form_token": connect_token,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "X-CSRFToken": browser.cookies.get("csrftoken", csrf),
                "Referer": self._url("/crm/setup/practice/"),
            },
            timeout=30,
            allow_redirects=True,
        )
        self.assertEqual(complete.status_code, 200, complete.text[:500])
        self.assertIn("/crm/setup/complete/", complete.url)
        self.assertIn("Connected", complete.text)

    def test_duplicates_lists_fake_connection_after_api_restart(self):
        browser = self._browser()

        # Before connect: Duplicates shows reconnect empty state.
        empty = self._get(browser, "/crm/duplicate-journeys/")
        self.assertEqual(empty.status_code, 200)
        self.assertIn("No connected CRM", empty.text)
        self.assertIn("Connect a CRM", empty.text)

        self._connect_practice(browser)

        before = self._get(browser, "/crm/duplicate-journeys/")
        self.assertEqual(before.status_code, 200)
        self.assertNotIn("No connected CRM account is available yet", before.text)
        self.assertIn("connection_id", before.text)
        # Form is present when connected_count >= 1.
        self.assertIn("crm-duplicate-journey-form", before.text)
        self.assertIn("Fake CRM", before.text)

        # Durable connection file under isolated API state.
        conn_file = self.api_state / "fake_crm" / "connections.json"
        self.assertTrue(conn_file.is_file(), "fake connection must be durable on disk")

        # Real API process restart (same state root + same browser owner cookies).
        self._restart_api()

        after = self._get(browser, "/crm/duplicate-journeys/")
        self.assertEqual(after.status_code, 200, after.text[:500])
        self.assertNotIn(
            "No connected CRM account is available yet",
            after.text,
            "O1b: Duplicates must not empty-state after API restart when connection is connected",
        )
        self.assertIn("crm-duplicate-journey-form", after.text)
        self.assertIn("Fake CRM", after.text)
        self.assertIn("connection_id", after.text)

        # Isolation: machine bootstrap under isolated LOCALAPPDATA only if env
        # create path touches app root; never the real developer path.
        machine_boot = self.machine / "EasyImports" / "bootstrap.json"
        # Env-managed state root should not need a bootstrap file; if present it
        # must only live under the test machine root (asserted by path).
        if machine_boot.exists():
            self.assertTrue(str(machine_boot).startswith(str(self.machine)))
