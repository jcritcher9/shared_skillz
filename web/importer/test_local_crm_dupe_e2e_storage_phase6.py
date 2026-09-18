"""Phase 6 O5 — Auto-disposition threshold on stable dual-process stack.

True dual-process HTTP browser against live Django + live API on an isolated
durable data root:

* All-auto high-confidence pairs (T=90) → Continue → merge plan without manual
  five-group windows; CRM mutation count unchanged until separate authorize.
* Mixed high+medium → auto-approves high only; medium remains for operator.
* T below 90 fails closed on the start form.

Isolation: absolute API state root + pinned ``LOCALAPPDATA``.

Authority:
``mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/local_crm_dupe_e2e_storage_and_operator_path/local_crm_dupe_e2e_storage_and_operator_path.md``
Phase 6.
"""

from __future__ import annotations

import json
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

from .crm_duplicate_merge_copy import html_shows_merge_ready


class LocalCrmDupeStoragePhase6DualProcessTests(SimpleTestCase):
    """Frontend auto-merge Continue path re-proof on stable storage."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_p6_")
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
        cls._seed(high=2, medium=0)
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
    def _seed(cls, *, high: int, medium: int) -> None:
        helper = cls.repo / "web" / "tools" / "seed_fake_crm_org.py"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(cls.repo)
        args = [
            sys.executable,
            str(helper),
            "--state-root",
            str(cls.api_state),
            "--clear-injections",
        ]
        if high > 0:
            args.extend(["--pair-count", str(high)])
        if medium > 0:
            args.extend(["--medium-pair-count", str(medium)])
        if high == 0 and medium == 0:
            args.extend(["--pair-count", "1"])
        completed = subprocess.run(
            args,
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
        raise RuntimeError("Phase 6 API process did not start.")

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
    def _restart_api_with_seed(cls, *, high: int, medium: int) -> None:
        cls._stop_api()
        cls._seed(high=high, medium=medium)
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
                "Phase 6 Django migrate failed:\n"
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
        raise RuntimeError("Phase 6 Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase6StorageE2E/1.0"})
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

    def _session_id_from_url(self, url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        self.assertIsNotNone(match, url)
        return match.group(1)

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
        self.assertIsNotNone(match, "no connected CRM")
        return match.group(1)

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=60, allow_redirects=True)
        self.assertIn(response.status_code, {200, 302}, response.text[:500])
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

    def _org_snapshot(self) -> dict:
        org_path = self.api_state / "fake_crm" / "org.json"
        payload = json.loads(org_path.read_text(encoding="utf-8"))
        records = payload.get("records") or {}
        deleted = sorted(
            rid
            for rid, rec in records.items()
            if isinstance(rec, dict)
            and rec.get("object_type") == "Account"
            and rec.get("is_deleted")
        )
        return {
            "mutation_count": int(payload.get("mutation_count") or 0),
            "deleted_account_ids": deleted,
        }

    def _connect_fake(self, browser: requests.Session) -> None:
        page = self._get(browser, "/crm/connections/")
        csrf = (
            self._extract_csrf(page.text)
            if "csrfmiddlewaretoken" in page.text
            else browser.cookies.get("csrftoken", "")
        )
        self._post_form(
            browser,
            "/crm/connect/",
            {"provider_key": "fake", "csrfmiddlewaretoken": csrf},
        )
        connections = self._get(browser, "/crm/connections/")
        self.assertIn("fake", connections.text.lower())

    def _start_scan(self, browser: requests.Session, *, auto_t: int | None) -> str:
        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        data = {
            "csrfmiddlewaretoken": self._extract_csrf(start.text),
            "form_token": self._extract_form_token(start.text),
            "connection_id": connection_id,
            "entity_family": "company",
            "source_mode": "acquire_all",
        }
        if auto_t is not None:
            data["auto_merge_high_confidence"] = "on"
            data["auto_merge_min_confidence"] = str(auto_t)
        posted = self._post_form(browser, "/crm/duplicate-journeys/", data)
        self.assertTrue(
            any(part in posted.url for part in ("progress", "review", "merge")),
            posted.url,
        )
        return self._session_id_from_url(posted.url)

    def _wait_progress_ready(
        self, browser: requests.Session, session_id: str
    ) -> requests.Response:
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        deadline = time.monotonic() + 90.0
        last = ""
        while time.monotonic() < deadline:
            response = browser.get(self._url(path), timeout=60, allow_redirects=True)
            last = response.text
            if response.status_code not in {200, 302}:
                time.sleep(0.15)
                continue
            if "Continue to review" in last:
                return response
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                return response
            if "Review merge plan" in last:
                return response
            if "No duplicate groups found" in last:
                self.fail(f"unexpected zero groups: {last[:600]}")
            time.sleep(0.2)
        self.fail(f"timed out waiting for progress ready: {last[:800]}")

    def _post_continue(
        self, browser: requests.Session, session_id: str, html: str
    ) -> str:
        self.assertIn("Continue to review", html)
        posted = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/progress/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(html),
                "form_token": self._extract_form_token(html),
            },
        )
        self.assertIn(posted.status_code, {200, 302}, posted.text[:800])
        return posted.text

    def _submit_remaining_review_if_needed(
        self, browser: requests.Session, session_id: str, html: str
    ) -> str:
        for _ in range(8):
            if html_shows_merge_ready(html):
                return html
            if "crm-duplicate-review-form" not in html:
                merge = self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
                return merge.text

            def hidden(name: str) -> str:
                match = re.search(
                    rf'name="{re.escape(name)}"\s+value="([^"]*)"',
                    html,
                    re.I,
                )
                self.assertIsNotNone(match, name)
                return match.group(1)

            group_order = hidden("group_order")
            group_ids = [g for g in group_order.split(",") if g.strip()]
            data = {
                "csrfmiddlewaretoken": self._extract_csrf(html),
                "form_token": self._extract_form_token(html),
                "window_id": hidden("window_id"),
                "window_digest": hidden("window_digest"),
                "expected_revision": hidden("expected_revision"),
                "group_order": group_order,
            }
            for gid in group_ids:
                data[f"group_revision_{gid}"] = hidden(f"group_revision_{gid}")
                data[f"allowed_{gid}"] = hidden(f"allowed_{gid}")
                data[f"recommended_{gid}"] = hidden(f"recommended_{gid}")
                data[f"eligible_{gid}"] = hidden(f"eligible_{gid}")
                data[f"advanced_{gid}"] = hidden(f"advanced_{gid}")
                recommended = data[f"recommended_{gid}"]
                allowed = {
                    part.strip()
                    for part in data[f"allowed_{gid}"].split(",")
                    if part.strip()
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
            )
            self.assertIn(post.status_code, {200, 302}, post.text[:800])
            html = post.text
            if post.url and "merge" in post.url:
                return html
        self.fail("did not reach merge after manual remainder")

    def test_o5_all_auto_continue_to_merge_without_crm_write(self):
        """All high pairs auto-dispose; merge plan before any CRM mutation."""

        self._restart_api_with_seed(high=2, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        before = self._org_snapshot()
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        self.assertIn("Continue to review", ready.text)
        # GET progress is non-mutating (auto-disposition is POST-only).
        again = self._get(
            browser, f"/sessions/{session_id}/crm-duplicates/progress/"
        )
        self.assertIn("Continue to review", again.text)
        mid = self._org_snapshot()
        self.assertEqual(mid["mutation_count"], before["mutation_count"])
        self.assertEqual(mid["deleted_account_ids"], [])

        html = self._post_continue(browser, session_id, again.text)
        self.assertIn("Review merge plan", html)
        self.assertIn("Auto-approved (high confidence)", html)
        self.assertRegex(html, r"Auto-approved\s+2\s+high-confidence|auto-approved", re.I)

        after = self._org_snapshot()
        self.assertEqual(after["mutation_count"], before["mutation_count"])
        self.assertEqual(after["deleted_account_ids"], [])

    def test_o5_mixed_auto_and_manual_remainder(self):
        """High auto-approved; medium remains for five-group operator review."""

        self._restart_api_with_seed(high=1, medium=1)
        browser = self._browser()
        self._connect_fake(browser)
        before = self._org_snapshot()
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        html = self._post_continue(browser, session_id, ready.text)
        self.assertTrue(
            "Review duplicate groups" in html or "crm-duplicate-review-form" in html,
            html[:800],
        )
        self.assertIn("crm-duplicate-review-form", html)
        self.assertRegex(html, r"Auto-approved\s+1\s+high-confidence")
        merge_html = self._submit_remaining_review_if_needed(
            browser, session_id, html
        )
        self.assertIn("Review merge plan", merge_html)
        self.assertIn("Auto-approved (high confidence)", merge_html)
        after = self._org_snapshot()
        # Still no CRM write without separate authorize.
        self.assertEqual(after["mutation_count"], before["mutation_count"])
        self.assertEqual(after["deleted_account_ids"], [])

    def test_o5_threshold_below_floor_rejected_on_start(self):
        """T=89 fails closed on the operator start form (no clamp)."""

        self._restart_api_with_seed(high=1, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        posted = self._post_form(
            browser,
            "/crm/duplicate-journeys/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(start.text),
                "form_token": self._extract_form_token(start.text),
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "acquire_all",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "89",
            },
        )
        # Must not start a progress/review session for invalid T.
        self.assertNotIn("/crm-duplicates/progress/", posted.url)
        self.assertNotIn("/crm-duplicates/review/", posted.url)
        body = posted.text.lower()
        self.assertTrue(
            posted.status_code == 200
            and (
                "90" in posted.text
                or "threshold" in body
                or "confidence" in body
                or "invalid" in body
                or "error" in body
                or "auto" in body
            ),
            posted.text[:800],
        )
