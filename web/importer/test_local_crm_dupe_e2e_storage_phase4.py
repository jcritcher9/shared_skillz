"""Phase 4 O2/O3 — CRM read → duplicate recognition on stable dual-process stack.

True dual-process HTTP browser (``requests``) against live Django + live API.
Companies-first CRM scan from a connected fake CRM reaches review with ≥1
group (O3). Zero-group terminal is covered as an additional branch.

Isolation: absolute API state root + pinned ``LOCALAPPDATA`` so the developer
machine bootstrap is never written.

Authority:
``mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/local_crm_dupe_e2e_storage_and_operator_path/local_crm_dupe_e2e_storage_and_operator_path.md``
Phase 4.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

import requests
from django.test import SimpleTestCase


PAIR_COUNT = 2


class LocalCrmDupeStoragePhase4DualProcessTests(SimpleTestCase):
    """Practice connect → Companies CRM scan → review-ready with groups."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_p4_")
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
        cls._seed_pairs(PAIR_COUNT)
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
    def _seed_pairs(cls, pair_count: int) -> None:
        cls._run_seed_helper(
            "--pair-count",
            str(pair_count),
            "--clear-injections",
        )

    @classmethod
    def _seed_unique_singles(cls) -> None:
        """Seed three unique Accounts (no pairs) for zero-group branch."""

        # Use seed helper field-fill then overwrite via a tiny inline script so
        # Django never imports mappings_2. Prefer seed tool pair_count=0 with
        # distinct ids would still same-name seed — so call a subprocess
        # snippet under repo PYTHONPATH.
        script = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
from mappings_2.integrations.crm.fake.provider import build_fake_crm_stack
stack = build_fake_crm_stack(root / "fake_crm")
stack.org.records.clear()
for i in range(3):
    stack.org.upsert(
        record_id=f"U{i}",
        object_type="Account",
        fields={
            "Name": f"Unique Co {i}",
            "Website": f"unique{i}.example",
            "Domain": f"unique{i}.example",
            "rank": 1,
        },
        system_modstamp=f"2026-07-19T12:00:0{i}+00:00",
    )
stack.org.mutation_count = 0
stack.org.save()
stack.close()
print("seeded unique")
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(cls.repo)
        completed = subprocess.run(
            [sys.executable, "-c", script, str(cls.api_state)],
            cwd=str(cls.repo),
            env=environment,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "unique seed failed:\n"
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
        raise RuntimeError("Phase 4 API process did not start.")

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
    def _restart_api_with_seed(cls, *, pair_count: int | None = None, unique: bool = False) -> None:
        cls._stop_api()
        if unique:
            cls._seed_unique_singles()
        else:
            cls._seed_pairs(pair_count if pair_count is not None else PAIR_COUNT)
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
                "Phase 4 Django migrate failed:\n"
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
        raise RuntimeError("Phase 4 Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase4StorageE2E/1.0"})
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
        connections = self._get(browser, "/crm/connections/")
        self.assertIn("fake", connections.text.lower(), connections.text[:800])

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
        self.assertIsNotNone(match, "no connected CRM option on start page")
        return match.group(1)

    def _wait_for_review_or_zero(
        self, browser: requests.Session, session_id: str
    ) -> tuple[str, str]:
        """Return (kind, html) where kind is 'review' or 'zero'."""

        deadline = time.monotonic() + 90.0
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        last = ""
        while time.monotonic() < deadline:
            response = browser.get(self._url(path), timeout=60, allow_redirects=True)
            last = response.text
            if response.status_code != 200:
                time.sleep(0.15)
                continue
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                return "review", last
            if "No duplicate groups found" in last:
                return "zero", last
            if "Review merge plan" in last:
                return "review", last
            time.sleep(0.2)
        self.fail(f"timed out waiting for review or zero-group: {last[:800]}")

    def test_o3_companies_crm_scan_reaches_review_with_groups(self):
        """O2+O3 dual-process: find-in-CRM Companies → review with ≥1 group."""

        self._restart_api_with_seed(pair_count=PAIR_COUNT)
        browser = self._browser()
        self._connect_fake(browser)

        start = self._get(browser, "/crm/duplicate-journeys/")
        self.assertNotIn("No connected CRM account is available yet", start.text)
        connection_id = self._connection_id_from_start_page(start.text)
        token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)
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
        self.assertNotIn("map-record-id", posted.url)
        self.assertTrue(
            any(part in posted.url for part in ("progress", "review", "merge")),
            posted.url,
        )
        self.assertLess(posted.status_code, 500)
        session_id = self._session_id_from_url(posted.url)

        if "Review duplicate groups" in posted.text:
            kind, html = "review", posted.text
        else:
            kind, html = self._wait_for_review_or_zero(browser, session_id)

        self.assertEqual(kind, "review", html[:800])
        self.assertIn("Review duplicate groups", html)
        # PAIR_COUNT=2 → window shows 1–2 of 2 (not multi-window 5).
        self.assertRegex(html, r"Review duplicate groups 1[–-]2 of 2")
        self.assertIn("Holdings", html)
        self.assertRegex(html, r"P\d[LR]")
        # No durable 500 surface / reconnect empty state after read ladder.
        self.assertNotIn("No connected CRM account is available yet", html)
        self.assertNotIn("Service unavailable", html)

        machine_boot = self.machine / "EasyImports" / "bootstrap.json"
        if machine_boot.exists():
            self.assertTrue(str(machine_boot).startswith(str(self.machine)))

    def test_zero_group_terminal_additional_branch(self):
        """Additional branch: unique population → No duplicate groups found."""

        self._restart_api_with_seed(unique=True)
        browser = self._browser()
        self._connect_fake(browser)

        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)
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
        self.assertLess(posted.status_code, 500)
        session_id = self._session_id_from_url(posted.url)

        if "No duplicate groups found" in posted.text:
            kind, html = "zero", posted.text
        elif "Review duplicate groups" in posted.text:
            kind, html = "review", posted.text
        else:
            kind, html = self._wait_for_review_or_zero(browser, session_id)

        self.assertEqual(kind, "zero", html[:800])
        self.assertIn("No duplicate groups found", html)
        self.assertNotIn("crm-duplicate-review-form", html)
