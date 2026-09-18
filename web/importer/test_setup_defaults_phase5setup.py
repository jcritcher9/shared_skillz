"""Phase 5-SETUP: network-free browser accept for import setup defaults (7A/7B).

HTTP browser → live Django → live API: prove match-first default, D7/D8 CRM-data
picklist, clean-only hides CRM-data + forces none, and destination field hidden.
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
from django.test import TransactionTestCase


class CrmConnectionSetupUxPhase5SetupTests(TransactionTestCase):
    """Phase 5-SETUP dual-process browser accept (desires #6–#7 / D6–D8)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory()
        root = Path(cls.state_dir.name)
        cls.api_state = root / "api_state"
        cls.django_db = root / "django.sqlite3"
        cls.api_state.mkdir(parents=True, exist_ok=True)

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
        environment["PYTHONPATH"] = str(cls.repo)
        return environment

    @classmethod
    def _django_env(cls) -> dict:
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "easyimports_web.settings"
        environment["EASYIMPORTS_API_BASE_URL"] = cls.api_base
        environment["EASYIMPORTS_DB_PATH"] = str(cls.django_db)
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
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("Phase 5-SETUP API process did not start.")

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
                "Phase 5-SETUP Django migrate failed:\n"
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
        raise RuntimeError("Phase 5-SETUP Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase5SetupBrowser/1.0"})
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
        self.assertIsNotNone(match, f"hidden field {name!r} missing")
        return match.group(1)

    def _session_id_from_url(self, url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        self.assertIsNotNone(match, f"session id missing from {url}")
        return match.group(1)

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=60, allow_redirects=True)
        self.assertIn(
            response.status_code,
            {200, 302},
            f"GET {path} -> {response.status_code}: {response.text[:400]}",
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
            timeout=60,
            allow_redirects=allow_redirects,
        )

    def _start_import_setup(self, browser: requests.Session) -> tuple[str, str]:
        """Home → Start an import → product setup page. Returns (session_id, html)."""

        home = self._get(browser, "/")
        self.assertIn("Start an import", home.text)
        csrf = self._extract_csrf(home.text)
        started = self._post_form(
            browser,
            "/sessions/new/",
            {"csrfmiddlewaretoken": csrf},
        )
        self.assertEqual(started.status_code, 200, started.text[:400])
        self.assertIn("/product/", started.url)
        return self._session_id_from_url(started.url), started.text

    def _assert_setup_defaults_chrome(self, html: str) -> None:
        """Shared blank-setup product surface checks (7A/7B)."""

        self.assertIn("Match it against my CRM", html)
        self.assertIn("Clean and prepare my file (no matching)", html)
        self.assertNotIn("Not needed for clean-only", html)
        self.assertNotIn("Where should the results go?", html)
        self.assertNotIn("Preview files only", html)
        self.assertRegex(
            html,
            r'value="crm_matching"[^>]*selected|selected[^>]*value="crm_matching"',
        )
        # Hidden target still present for internal default.
        self.assertIn('name="target_provider_id"', html)
        self.assertIn('value="fake-preview-v1"', html)
        # CRM-data panel not hidden when matching is default.
        ref_idx = html.find('data-setup-panel="reference_source"')
        self.assertNotEqual(ref_idx, -1)
        snippet = html[ref_idx : ref_idx + 140]
        self.assertNotIn("hidden", snippet.split(">")[0])

    def test_browser_blank_setup_defaults_match_and_hides_destination(self):
        """Live stack: blank setup defaults to match; destination not operator UI."""

        browser = self._browser()
        _session_id, html = self._start_import_setup(browser)
        self._assert_setup_defaults_chrome(html)
        # No connected CRM on a fresh device → D8 uploaded.
        self.assertRegex(
            html,
            r'value="uploaded"[^>]*selected|selected[^>]*value="uploaded"',
        )
        self.assertIn("Where is the CRM data?", html)
        self.assertIn("Upload CRM exports", html)
        self.assertIn("Use a connected CRM", html)

    def test_browser_clean_only_hides_crm_data_and_forces_none(self):
        """Live stack: clean-only submission stores reference_source=none."""

        browser = self._browser()
        session_id, html = self._start_import_setup(browser)
        form_token = self._extract_form_token(html)
        csrf = self._extract_csrf(html)
        setup_revision = self._extract_hidden(html, "setup_revision")
        target = self._extract_hidden(html, "target_provider_id")
        self.assertEqual(target, "fake-preview-v1")

        submitted = self._post_form(
            browser,
            f"/sessions/{session_id}/product/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "operator_label": "Phase5Setup Operator",
                "entity": "accounts",
                "operation": "clean_only",
                # Matching default may still be posted while panel is hidden.
                "reference_source": "uploaded",
                "connection_id": "",
                "people_output": "",
                "target_provider_id": target,
                "setup_revision": setup_revision,
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text[:500])
        self.assertIn("/upload/", submitted.url)
        # Intent frozen on session (upload technical details: entity / op / ref).
        body = submitted.text
        self.assertRegex(
            body,
            r"accounts\s*/\s*clean_only\s*/\s*none",
        )
        self.assertIn("Accounts · clean and prepare", body)
        # Destination remains technical-only.
        self.assertNotIn("Where should the results go?", body)
        self.assertNotIn("Not needed for clean-only", body)

        # Re-open setup: clean-only draft; CRM-data panel starts hidden.
        product = self._get(browser, f"/sessions/{session_id}/product/")
        self.assertEqual(product.status_code, 200)
        self.assertRegex(
            product.text,
            r'value="clean_only"[^>]*selected|selected[^>]*value="clean_only"',
        )
        ref_idx = product.text.find('data-setup-panel="reference_source"')
        self.assertNotEqual(ref_idx, -1)
        snippet = product.text[ref_idx : ref_idx + 140]
        self.assertIn("hidden", snippet.split(">")[0])
        # Target still not an operator-facing destination label.
        self.assertNotIn("Where should the results go?", product.text)
        self.assertNotIn("Preview files only", product.text)

    def test_browser_matching_uploaded_submit_and_no_destination_field(self):
        """Live stack: explicit match + uploaded freezes draft without destination UI."""

        browser = self._browser()
        session_id, html = self._start_import_setup(browser)
        form_token = self._extract_form_token(html)
        csrf = self._extract_csrf(html)
        setup_revision = self._extract_hidden(html, "setup_revision")
        target = self._extract_hidden(html, "target_provider_id")

        submitted = self._post_form(
            browser,
            f"/sessions/{session_id}/product/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "operator_label": "Phase5Setup Match Operator",
                "entity": "people",
                "operation": "crm_matching",
                "reference_source": "uploaded",
                "connection_id": "",
                "people_output": "",
                "target_provider_id": target,
                "setup_revision": setup_revision,
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text[:500])
        self.assertIn("/upload/", submitted.url)
        body = submitted.text
        self.assertRegex(
            body,
            r"people\s*/\s*crm_matching\s*/\s*uploaded",
        )
        self.assertNotIn("Where should the results go?", body)

    def test_browser_practice_connect_then_d8_connected_crm_default(self):
        """D8: after a Practice connection exists, blank setup defaults connected_crm."""

        browser = self._browser()
        # Guided practice connect (same path as Phase 5-CONN happy path).
        start = browser.get(self._url("/crm/setup/"), timeout=30, allow_redirects=True)
        self.assertEqual(start.status_code, 200)
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
        self.assertEqual(chosen.status_code, 200)
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
        self.assertEqual(complete.status_code, 200, complete.text[:400])
        self.assertIn("/crm/setup/complete/", complete.url)

        # New import setup on same browser/owner session.
        _session_id, html = self._start_import_setup(browser)
        self._assert_setup_defaults_chrome(html)
        self.assertRegex(
            html,
            r'value="connected_crm"[^>]*selected|selected[^>]*value="connected_crm"',
        )
        # Connection picker panel visible for connected_crm default.
        conn_idx = html.find('data-setup-panel="connection_id"')
        self.assertNotEqual(conn_idx, -1)
        conn_snippet = html[conn_idx : conn_idx + 140]
        self.assertNotIn("hidden", conn_snippet.split(">")[0])
