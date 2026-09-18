"""SCR-3C real HTTP proof across separate API and Django processes."""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import requests
from django.test import SimpleTestCase


class Scr3cDualProcessHttpTests(SimpleTestCase):
    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_scr3c_")
        root = Path(cls.state_dir.name)
        cls.api_state = root / "api_state"
        cls.machine = root / "LocalAppData"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        for directory in (cls.api_state, cls.machine, cls.media_root):
            directory.mkdir(parents=True, exist_ok=True)

        cls.api_port = cls._free_port()
        cls.django_port = cls._free_port()
        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        cls.django_base = f"http://127.0.0.1:{cls.django_port}"
        cls.owner_id = uuid4()
        cls.owner_session = f"django-{cls.owner_id}"

        cls._seed_fake_records()
        cls.fixture = cls._seed_clearable_run()
        cls._start_api()
        cls._migrate_django()
        cls.web_fixture = cls._seed_web_session()
        cls._start_django()

    @staticmethod
    def _free_port() -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    @classmethod
    def _api_env(cls) -> dict[str, str]:
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.api_port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = str(cls.api_state)
        environment["EASYIMPORTS_SECRET_STORE"] = "file_insecure"
        environment["EASYIMPORTS_SF_OAUTH_EXCHANGE"] = "synthetic"
        environment["LOCALAPPDATA"] = str(cls.machine)
        environment["XDG_DATA_HOME"] = str(cls.machine / "xdg")
        environment["PYTHONPATH"] = str(cls.repo)
        return environment

    @classmethod
    def _django_env(cls) -> dict[str, str]:
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
    def _run_checked(cls, command: list[str], *, env: dict[str, str]):
        completed = subprocess.run(
            command,
            cwd=str(cls.repo),
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"SCR-3C helper failed: {command!r}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        return completed

    @classmethod
    def _seed_fake_records(cls) -> None:
        cls._run_checked(
            [
                sys.executable,
                str(cls.repo / "web" / "tools" / "seed_fake_crm_org.py"),
                "--state-root",
                str(cls.api_state),
                "--pair-count",
                "13",
                "--clear-injections",
            ],
            env=cls._api_env(),
        )

    @classmethod
    def _seed_clearable_run(cls) -> dict:
        completed = cls._run_checked(
            [
                sys.executable,
                str(cls.repo / "web" / "tools" / "seed_scr3c_clearable_run.py"),
                "--state-root",
                str(cls.api_state),
                "--owner-session",
                cls.owner_session,
            ],
            env=cls._api_env(),
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    @classmethod
    def _start_api(cls) -> None:
        cls.api_process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                if (
                    requests.get(f"{cls.api_base}/health", timeout=0.25).status_code
                    == 200
                ):
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls._stop_process("api_process")
        raise RuntimeError("SCR-3C API process did not start.")

    @classmethod
    def _migrate_django(cls) -> None:
        cls._run_checked(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "migrate",
                "--run-syncdb",
                "--verbosity",
                "0",
            ],
            env=cls._django_env(),
        )

    @classmethod
    def _seed_web_session(cls) -> dict:
        script = """
import json
from uuid import UUID
import django
django.setup()
from django.contrib.sessions.backends.db import SessionStore
from importer.models import ImportSession

owner = UUID(%r)
browser = SessionStore()
browser["easyimports_owner_id"] = str(owner)
browser.save()
workflow = ImportSession.objects.create(
    owner_id=owner,
    product_key="easyimports.duplicate_resolution",
    status=ImportSession.Status.RUNNING,
    options={
        "run_id": %r,
        "crm_journey_id": %r,
        "entity_family": "company",
    },
)
print(json.dumps({"session_key": browser.session_key, "workflow_session_id": str(workflow.id)}))
""" % (
            str(cls.owner_id),
            cls.fixture["run_id"],
            cls.fixture["journey_id"],
        )
        completed = cls._run_checked(
            [sys.executable, "-c", script],
            env=cls._django_env(),
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    @classmethod
    def _start_django(cls) -> None:
        cls.django_process = subprocess.Popen(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "runserver",
                f"127.0.0.1:{cls.django_port}",
                "--noreload",
            ],
            cwd=str(cls.repo / "web"),
            env=cls._django_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                response = requests.get(f"{cls.django_base}/", timeout=0.25)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls._stop_process("django_process")
        raise RuntimeError("SCR-3C Django process did not start.")

    @classmethod
    def _stop_process(cls, attribute: str) -> None:
        process = getattr(cls, attribute, None)
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        setattr(cls, attribute, None)

    @classmethod
    def tearDownClass(cls):
        cls._stop_process("django_process")
        cls._stop_process("api_process")
        cls.state_dir.cleanup()
        super().tearDownClass()

    def _browser(self) -> requests.Session:
        browser = requests.Session()
        browser.cookies.set(
            "sessionid",
            self.web_fixture["session_key"],
            domain="127.0.0.1",
            path="/",
        )
        return browser

    @staticmethod
    def _hidden(html: str, name: str) -> str:
        marker = f'name="{name}" value="'
        start = html.find(marker)
        if start < 0:
            raise AssertionError(f"{name} missing from confirmation page")
        start += len(marker)
        end = html.find('"', start)
        if end < 0:
            raise AssertionError(f"{name} value is malformed")
        return html[start:end]

    def _workflow(self) -> dict:
        response = requests.get(
            f"{self.api_base}/v1/workflows/{self.fixture['run_id']}",
            headers={"X-Owner-Session": self.owner_session},
            timeout=30,
        )
        self.assertEqual(response.status_code, 200, response.text[:800])
        return response.json()

    def test_confirmed_action_resumes_same_run_with_fresh_progress(self):
        browser = self._browser()
        session_id = self.web_fixture["workflow_session_id"]
        progress_url = (
            f"{self.django_base}/sessions/{session_id}/crm-duplicates/progress/"
        )
        recovery_url = (
            f"{self.django_base}/sessions/{session_id}/crm-duplicates/"
            "recover-saved-progress/"
        )

        progress = browser.get(progress_url, timeout=30)
        self.assertEqual(progress.status_code, 200, progress.text[:800])
        self.assertIn("Clear stale scan data and retry", progress.text)

        cursor_path = Path(self.fixture["cursor_path"])
        before = cursor_path.read_bytes()
        confirmation = browser.get(recovery_url, timeout=30)
        self.assertEqual(confirmation.status_code, 200, confirmation.text[:800])
        self.assertIn("Clear saved scan data and retry?", confirmation.text)
        self.assertEqual(cursor_path.read_bytes(), before, "GET changed saved state")
        with closing(sqlite3.connect(str(self.django_db))) as connection:
            journal_count = connection.execute(
                "SELECT COUNT(*) FROM importer_apimutation"
            ).fetchone()[0]
        self.assertEqual(journal_count, 0, "GET created a mutation journal row")

        posted = browser.post(
            recovery_url,
            data={
                "csrfmiddlewaretoken": self._hidden(
                    confirmation.text, "csrfmiddlewaretoken"
                ),
                "form_token": self._hidden(confirmation.text, "form_token"),
            },
            headers={
                "Referer": recovery_url,
                "X-CSRFToken": browser.cookies.get("csrftoken", ""),
            },
            timeout=120,
            allow_redirects=True,
        )
        self.assertEqual(posted.status_code, 200, posted.text[:1000])
        self.assertIn(
            "Saved scan data was cleared and the scan was retried.", posted.text
        )

        current = self._workflow()
        self.assertEqual(current["run_id"], self.fixture["run_id"])
        diagnostic = current.get("paused_effect_diagnostic") or {}
        self.assertNotEqual(
            diagnostic.get("code"),
            "reference_acquisition_saved_progress_reset_required",
        )
        progress_state = current.get("reference_acquisition_progress") or {}
        self.assertGreater(int(progress_state.get("collected_count") or 0), 0)

        saved = json.loads(cursor_path.read_text(encoding="utf-8"))
        saved_for_digest = saved.get(self.fixture["work_digest"])
        self.assertTrue(
            saved_for_digest is None or isinstance(saved_for_digest, dict),
            "recovery left the malformed per-digest cursor in place",
        )
        with closing(sqlite3.connect(str(self.django_db))) as connection:
            recovery_mutation_count = connection.execute(
                "SELECT COUNT(*) FROM importer_apimutation "
                "WHERE mutation_kind = 'recover_reference_acquisition'"
            ).fetchone()[0]
            session_count = connection.execute(
                "SELECT COUNT(*) FROM importer_importsession"
            ).fetchone()[0]
        self.assertEqual(recovery_mutation_count, 1)
        self.assertEqual(session_count, 1, "recovery created a second journey")
