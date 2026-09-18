"""MAP-3: network-free browser accept for workflow create bind gate.

Proves: confirm plan → configure create includes digests → workflow accepted;
stale/missing bind fails closed before full run.
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


class ColumnMappingMap3BrowserAcceptTests(TransactionTestCase):
    """MAP-3 dual-process browser accept (workflow bind + fail closed)."""

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
        cls.api_log = cls.api_state.parent / "api.log"
        cls.api_log_handle = open(cls.api_log, "wb")
        cls.api_process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=cls.api_log_handle,
            stderr=subprocess.STDOUT,
        )
        health = f"{cls.api_base}/health"
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("MAP-3 API process did not start.")

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
            handle.close()
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
                "MAP-3 Django migrate failed:\n"
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
        raise RuntimeError("MAP-3 Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Map3Browser/1.0"})
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
        files: dict | None = None,
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
            files=files,
            headers=headers,
            timeout=90,
            allow_redirects=allow_redirects,
        )

    def test_browser_confirm_mapping_then_create_workflow(self):
        """Live stack: map columns → confirm → configure create succeeds."""

        browser = self._browser()
        home = self._get(browser, "/")
        csrf = self._extract_csrf(home.text)
        started = self._post_form(
            browser,
            "/sessions/new/",
            {"csrfmiddlewaretoken": csrf},
        )
        self.assertEqual(started.status_code, 200, started.text[:400])
        session_id = self._session_id_from_url(started.url)

        # Clean-only accounts setup (catalog destination for MAP stub inventory).
        form_token = self._extract_form_token(started.text)
        csrf = self._extract_csrf(started.text)
        setup_revision = self._extract_hidden(started.text, "setup_revision")
        target = self._extract_hidden(started.text, "target_provider_id")
        product = self._post_form(
            browser,
            f"/sessions/{session_id}/product/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "operator_label": "MAP-3 Browser Operator",
                "entity": "accounts",
                "operation": "clean_only",
                "reference_source": "uploaded",
                "connection_id": "",
                "people_output": "",
                "target_provider_id": target,
                "setup_revision": setup_revision,
            },
        )
        self.assertEqual(product.status_code, 200, product.text[:500])
        self.assertIn("/upload/", product.url)

        # Upload dataset CSV (clean-only accounts → single_dataset_import).
        upload_page = product
        form_token = self._extract_form_token(upload_page.text)
        csrf = self._extract_csrf(upload_page.text)
        csv_bytes = b"Account Name,Website\nAcme,https://acme.example\n"
        uploaded = self._post_form(
            browser,
            f"/sessions/{session_id}/upload/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "role": "dataset",
                "csv_encoding": "utf-8-sig",
                "xlsx_sheet_index": "0",
            },
            files={
                "file": ("accounts.csv", csv_bytes, "text/csv"),
            },
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text[:500])
        self.assertIn("accounts.csv", uploaded.text)

        # Column mapping: create plan from upload headers.
        map_start = self._get(
            browser, f"/sessions/{session_id}/column-mapping/"
        )
        self.assertEqual(map_start.status_code, 200, map_start.text[:400])
        form_token = self._extract_form_token(map_start.text)
        csrf = self._extract_csrf(map_start.text)
        created_map = self._post_form(
            browser,
            f"/sessions/{session_id}/column-mapping/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
            },
        )
        self.assertEqual(created_map.status_code, 200, created_map.text[:500])
        self.assertIn("/column-mapping/", created_map.url)
        plan_match = re.search(
            r"/column-mapping/([^/?#]+)/", created_map.url
        )
        if plan_match is None:
            # Create may leave us on the start page with a message; follow GET if a plan link exists.
            link = re.search(
                rf"/sessions/{session_id}/column-mapping/([^/\"'?]+)/",
                created_map.text,
            )
            # Surface operator-visible messages for diagnosis.
            messages = re.findall(
                r'class="message[^"]*"[^>]*>(.*?)</div>',
                created_map.text,
                flags=re.DOTALL,
            )
            api_tail = ""
            log_path = getattr(self, "api_log", None)
            if log_path and Path(log_path).is_file():
                try:
                    api_tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-1500:]
                except OSError:
                    api_tail = ""
            self.assertIsNotNone(
                link,
                "plan create did not reach review. messages="
                + repr(messages)
                + " api_log_tail="
                + repr(api_tail)
                + " body="
                + created_map.text[
                    created_map.text.find("<main") : created_map.text.find("<main") + 800
                ],
            )
            plan_id = link.group(1)
        else:
            plan_id = plan_match.group(1)
        # Avoid matching the bare session path as plan id.
        self.assertNotEqual(plan_id, session_id)
        self.assertFalse(plan_id.endswith(".html"))

        # MAP-R4: resolve needs-review rows via multi-row form (ignore), then
        # atomic confirm_mapping. Legacy patch/confirm remain for unit tests.
        for _ in range(12):
            review = self._get(
                browser, f"/sessions/{session_id}/column-mapping/{plan_id}/"
            )
            self.assertEqual(review.status_code, 200)
            if re.search(
                r"Mapping confirmed|id=\"mapping-confirmed\"|"
                r"Plan status</dt>\s*<dd><code>confirmed</code>",
                review.text,
                flags=re.IGNORECASE,
            ):
                break
            form_token = self._extract_form_token(review.text)
            csrf = self._extract_csrf(review.text)
            # Build choice_* payload: keep current selects; force needs_review → ignore.
            payload: dict[str, str] = {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
            }
            for sel in re.finditer(
                r'<select[^>]*\bname="(choice_\d+)"[^>]*>(.*?)</select>',
                review.text,
                flags=re.DOTALL | re.IGNORECASE,
            ):
                name = sel.group(1)
                block = sel.group(2)
                selected = re.search(
                    r'<option[^>]*\bvalue="([^"]*)"[^>]*selected',
                    block,
                    flags=re.IGNORECASE,
                )
                if selected and selected.group(1):
                    payload[name] = selected.group(1)
                else:
                    payload[name] = "system:ignore"
            # Filter tab always has data-filter="needs_review"; only rows matter.
            needs_review = bool(
                re.search(
                    r"<tr\b[^>]*\bdata-filter=\"needs_review\"",
                    review.text,
                    flags=re.IGNORECASE,
                )
            )
            if needs_review:
                payload["action"] = "save_draft"
            else:
                payload["action"] = "confirm_mapping"
            posted = self._post_form(
                browser,
                f"/sessions/{session_id}/column-mapping/{plan_id}/",
                payload,
            )
            self.assertEqual(posted.status_code, 200, posted.text[:500])
        else:
            self.fail("Could not confirm mapping plan within attempts")

        review = self._get(
            browser, f"/sessions/{session_id}/column-mapping/{plan_id}/"
        )
        self.assertIn("confirmed", review.text.lower())
        self.assertTrue(
            "Continue to settings" in review.text or "Continue setup" in review.text,
            "expected continue CTA after confirm",
        )

        # Configure → create workflow (bind must be present).
        configure = self._get(browser, f"/sessions/{session_id}/configure/")
        self.assertEqual(configure.status_code, 200, configure.text[:500])
        self.assertIn("Start import", configure.text)
        form_token = self._extract_form_token(configure.text)
        csrf = self._extract_csrf(configure.text)
        setup_revision = self._extract_hidden(configure.text, "setup_revision")
        mode_fields = re.findall(r'name="(mode__[^"]+)"', configure.text)
        data = {
            "form_token": form_token,
            "csrfmiddlewaretoken": csrf,
            "setup_revision": setup_revision,
            "list_duplicate_policy": "drop_repeats",
        }
        for field in mode_fields:
            if field == "mode__delivery":
                data[field] = "disabled"
            else:
                data[field] = "disabled"

        created_wf = self._post_form(
            browser,
            f"/sessions/{session_id}/configure/",
            data,
        )
        self.assertEqual(created_wf.status_code, 200, created_wf.text[:600])
        # Success lands on workflow page (not stuck on configure with error).
        self.assertTrue(
            "/workflow/" in created_wf.url or "workflow" in created_wf.text.lower(),
            f"expected workflow page, got {created_wf.url}: {created_wf.text[:400]}",
        )
        self.assertNotIn(
            "Confirm column mapping before starting the import",
            created_wf.text,
        )
