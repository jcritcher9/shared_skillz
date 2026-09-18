"""Phase 7C Grade B rem: deterministic dual-process auto-merge matrix.

Real HTTP against separate API + Django runserver processes. Seeds high-score
(.com domain) and medium-score (name-only) pairs so auto-disposition outcomes
are asserted, not conditional.

Coverage:
  - T=89 rejected on start
  - all-auto (high pairs + T=90) → merge with auto-approved count
  - mixed (high + medium + T=90) → auto + manual review remainder
  - none-auto (no T) → full review
  - upload source with high pairs + T=90
  - exact-retry Continue POST
  - dry_run zero mutation; execute deletes losers
  - high-score advanced-review (patched) still surfaces for manual review
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

from .crm_duplicate_merge_copy import (
    AUTHORIZE_MERGE_STEP_LABEL,
    html_shows_merge_ready,
)


class CrmDuplicateJourneyPhase7cHttpBrowserTests(SimpleTestCase):
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
        cls.api_state.mkdir(parents=True, exist_ok=True)
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
    def _seed(cls, *, high: int, medium: int) -> None:
        args = ["--clear-injections", "--pair-website-mode", "high"]
        if high:
            args.extend(["--pair-count", str(high)])
        if medium:
            args.extend(["--medium-pair-count", str(medium)])
        if not high and not medium:
            args.extend(["--pair-count", "1"])
        cls._run_seed_helper(*args)

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
        raise RuntimeError("Phase 7C API process did not start.")

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
                "Phase 7C Django migrate failed:\n"
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
        raise RuntimeError("Phase 7C Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase7CBrowser/1.0"})
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

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
        self.assertIsNotNone(match, "no connected CRM option on start page")
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
            "active_account_ids": sorted(
                rid
                for rid, rec in records.items()
                if isinstance(rec, dict)
                and rec.get("object_type") == "Account"
                and not rec.get("is_deleted")
            ),
        }

    def _connect_fake(self, browser: requests.Session) -> None:
        page = self._get(browser, "/crm/connections/")
        csrf = (
            self._extract_csrf(page.text)
            if "csrfmiddlewaretoken" in page.text
            else ""
        )
        if not csrf and "csrftoken" in browser.cookies:
            csrf = browser.cookies["csrftoken"]
        self._post_form(
            browser,
            "/crm/connect/",
            {"provider_key": "fake", "csrfmiddlewaretoken": csrf},
        )
        connections = self._get(browser, "/crm/connections/")
        self.assertIn("fake", connections.text.lower(), connections.text[:800])

    def _start_scan(
        self,
        browser: requests.Session,
        *,
        auto_t: int | None,
    ) -> str:
        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)
        data = {
            "csrfmiddlewaretoken": csrf,
            "form_token": token,
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
        token = self._extract_form_token(html)
        csrf = self._extract_csrf(html)
        posted = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/progress/",
            {"csrfmiddlewaretoken": csrf, "form_token": token},
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
            html = post.text
        self.fail("did not reach merge after review")

    def _finalize_and_authorize(
        self,
        browser: requests.Session,
        session_id: str,
        merge_html: str,
        *,
        mode: str,
    ) -> str:
        self.assertIn("Review merge plan", merge_html)
        token = self._extract_form_token(merge_html)
        csrf = self._extract_csrf(merge_html)
        fin = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            {
                "csrfmiddlewaretoken": csrf,
                "form_token": token,
                "merge_action": "finalize",
            },
        )
        merge2 = (
            fin
            if AUTHORIZE_MERGE_STEP_LABEL in fin.text
            else self._get(browser, f"/sessions/{session_id}/crm-duplicates/merge/")
        )
        self.assertIn(AUTHORIZE_MERGE_STEP_LABEL, merge2.text)
        token2 = self._extract_form_token(merge2.text)
        csrf2 = self._extract_csrf(merge2.text)
        auth = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            {
                "csrfmiddlewaretoken": csrf2,
                "form_token": token2,
                "merge_action": "authorize",
                "selected_mode": mode,
            },
        )
        self.assertIn(auth.status_code, {200, 302}, auth.text[:800])
        # Poll workflow briefly for completion.
        deadline = time.monotonic() + 60.0
        html = auth.text
        while time.monotonic() < deadline:
            page = self._get(browser, f"/sessions/{session_id}/workflow/")
            html = page.text
            if "succeeded" in html.lower() or "dry run" in html.lower():
                return html
            if "failed" in html.lower() and "status" in html.lower():
                break
            time.sleep(0.25)
        return html

    def _assert_auto_approved_count(self, html: str, *, minimum: int) -> int:
        """Parse auto-approved count from merge or review copy."""

        patterns = [
            r"Auto-approved\s+(\d+)\s+high-confidence",
            r"Auto-approved \(high confidence\).*?<dd>(\d+)</dd>",
            r"auto-approved\s+(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I | re.S)
            if match:
                count = int(match.group(1))
                self.assertGreaterEqual(count, minimum, html[:1000])
                return count
        self.fail(f"auto-approved count not found (min {minimum}): {html[:1000]}")

    def test_http_rejects_threshold_below_floor(self):
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
        self.assertEqual(posted.status_code, 200)
        self.assertIn("/crm/duplicate-journeys", posted.url)
        self.assertNotIn("/progress/", posted.url)

    def test_http_abandon_journey_through_django_and_api(self):
        """Phase 2: real browser -> Django journal -> API abandon command."""

        self._restart_api_with_seed(high=1, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        self._start_scan(browser, auto_t=None)

        recent = self._get(browser, "/crm/duplicate-journeys/")
        match = re.search(
            r'href="([^"]+/abandon/)"[^>]*>Abandon this journey</a>',
            recent.text,
        )
        self.assertIsNotNone(match, recent.text[:1600])
        abandon_path = match.group(1)
        confirm = self._get(browser, abandon_path)
        self.assertIn("Abandon this journey?", confirm.text)
        self.assertTrue(
            "No groups will be merged" in confirm.text
            or "remote outcome may be uncertain" in confirm.text,
            confirm.text[:1200],
        )

        stopped = self._post_form(
            browser,
            abandon_path,
            {
                "csrfmiddlewaretoken": self._extract_csrf(confirm.text),
                "form_token": self._extract_form_token(confirm.text),
            },
        )
        self.assertIn("This analysis was stopped.", stopped.text)
        self.assertNotIn("Analysis failed: This analysis was stopped.", stopped.text)

    def test_http_all_auto_high_pairs_merge_summary(self):
        self._restart_api_with_seed(high=2, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        self.assertIn("Continue to review", ready.text)
        # GET stays non-mutating.
        again = self._get(
            browser, f"/sessions/{session_id}/crm-duplicates/progress/"
        )
        self.assertIn("Continue to review", again.text)
        html = self._post_continue(browser, session_id, again.text)
        self.assertIn("Review merge plan", html)
        count = self._assert_auto_approved_count(html, minimum=2)
        self.assertGreaterEqual(count, 2)
        self.assertIn("Auto-approved (high confidence)", html)
        before = self._org_snapshot()
        self._finalize_and_authorize(
            browser, session_id, html, mode="dry_run"
        )
        after = self._org_snapshot()
        self.assertEqual(after["mutation_count"], before["mutation_count"])
        self.assertEqual(after["deleted_account_ids"], [])

    def test_http_mixed_auto_and_manual_review(self):
        """High-score pair auto-approves; medium-score pair stays for operator.

        Deterministic dual-process proof that groups below T are not auto-
        approved and still appear in the five-group review UI.
        """

        self._restart_api_with_seed(high=1, medium=1)
        browser = self._browser()
        self._connect_fake(browser)
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        html = self._post_continue(browser, session_id, ready.text)
        # Must still require manual review of the medium group.
        self.assertTrue(
            "Review duplicate groups" in html or "crm-duplicate-review-form" in html,
            html[:800],
        )
        self.assertRegex(html, r"Auto-approved\s+1\s+high-confidence")
        # Manual queue is exactly the medium group (not empty).
        self.assertIn("crm-duplicate-review-form", html)
        merge_html = self._submit_remaining_review_if_needed(
            browser, session_id, html
        )
        self.assertIn("Review merge plan", merge_html)
        auto_count = self._assert_auto_approved_count(merge_html, minimum=1)
        self.assertEqual(auto_count, 1)
        self.assertIn("Approved by operator", merge_html)

    def test_http_none_auto_reviews_all_groups(self):
        self._restart_api_with_seed(high=0, medium=2)
        browser = self._browser()
        self._connect_fake(browser)
        session_id = self._start_scan(browser, auto_t=None)
        ready = self._wait_progress_ready(browser, session_id)
        self.assertNotIn("Continue to review", ready.text)
        html = ready.text
        if "crm-duplicate-review-form" not in html:
            token = self._extract_form_token(html)
            csrf = self._extract_csrf(html)
            posted = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/progress/",
                {"csrfmiddlewaretoken": csrf, "form_token": token},
            )
            html = posted.text
        self.assertIn("crm-duplicate-review-form", html)
        self.assertNotRegex(html, r"Auto-approved\s+[1-9]")

    def test_http_upload_high_pairs_auto_merge(self):
        self._restart_api_with_seed(high=2, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        lines = ["Id,Name"]
        for index in range(2):
            name = f"Holdings{index:03d}"
            lines.append(f"P{index}L,{name}")
            lines.append(f"P{index}R,{name}")
        csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")
        posted = self._post_form(
            browser,
            "/crm/duplicate-journeys/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(start.text),
                "form_token": self._extract_form_token(start.text),
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "uploaded_population",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "90",
            },
            files={"population_file": ("records.csv", csv_bytes, "text/csv")},
        )
        self.assertIn("map-record-id", posted.url, posted.text[:400])
        session_id = self._session_id_from_url(posted.url)
        mapped = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/map-record-id/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(posted.text),
                "form_token": self._extract_form_token(posted.text),
                "source_column": "Id",
            },
        )
        session_id = self._session_id_from_url(mapped.url)
        ready = self._wait_progress_ready(browser, session_id)
        if "Continue to review" in ready.text:
            html = self._post_continue(browser, session_id, ready.text)
        else:
            html = ready.text
        if "Review merge plan" not in html:
            html = self._submit_remaining_review_if_needed(
                browser, session_id, html
            )
        self.assertIn("Review merge plan", html)
        self._assert_auto_approved_count(html, minimum=2)

    def test_http_continue_exact_retry_safe(self):
        self._restart_api_with_seed(high=2, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        token = self._extract_form_token(ready.text)
        csrf = self._extract_csrf(ready.text)
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        first = self._post_form(
            browser,
            path,
            {"csrfmiddlewaretoken": csrf, "form_token": token},
        )
        second = self._post_form(
            browser,
            path,
            {"csrfmiddlewaretoken": csrf, "form_token": token},
        )
        self.assertIn(first.status_code, {200, 302})
        self.assertIn(second.status_code, {200, 302})
        # Both converge on merge (all-auto); no error page.
        for response in (first, second):
            self.assertTrue(
                "Review merge plan" in response.text
                or "merge" in response.url
                or "Auto-approved" in response.text,
                response.text[:600],
            )
        merge = self._get(browser, f"/sessions/{session_id}/crm-duplicates/merge/")
        self._assert_auto_approved_count(merge.text, minimum=2)

    def test_http_execute_after_all_auto(self):
        self._restart_api_with_seed(high=1, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        html = self._post_continue(browser, session_id, ready.text)
        self.assertIn("Review merge plan", html)
        self._assert_auto_approved_count(html, minimum=1)
        before = self._org_snapshot()
        self._finalize_and_authorize(
            browser, session_id, html, mode="execute"
        )
        after = self._org_snapshot()
        # Exactly one loser deleted for the single pair.
        self.assertEqual(len(after["deleted_account_ids"]), 1)
        self.assertGreater(after["mutation_count"], before["mutation_count"])

    def _run_id_from_progress_html(self, html: str) -> str:
        match = re.search(r"Run\s+<code>([^<]+)</code>", html, re.I)
        if not match:
            match = re.search(r"run <code>([^<]+)</code>", html, re.I)
        self.assertIsNotNone(match, "run id missing from progress technical details")
        return match.group(1).strip()

    def _patch_high_score_ineligible(self, run_id: str, *, group_index: int = 0) -> None:
        """Stop the API so the job store is idle, patch, then restart."""

        helper = (
            self.repo / "web" / "tools" / "patch_crm_dupe_high_score_ineligible.py"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.repo)
        # External patch must not race the live API SQLite job store.
        self._stop_api()
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--state-root",
                str(self.api_state),
                "--run-id",
                run_id,
                "--group-index",
                str(group_index),
            ],
            cwd=str(self.repo),
            env=environment,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            self._start_api()
            raise RuntimeError(
                "patch_crm_dupe_high_score_ineligible failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        self._start_api()

    def test_http_high_score_blocked_still_surfaces(self):
        """High-score group with execution_blockers remains manual (not all-auto).

        Patches one of two high-score groups with an execution blocker while
        keeping confidence at 95 so this is not a below-threshold stand-in.
        """

        self._restart_api_with_seed(high=2, medium=0)
        browser = self._browser()
        self._connect_fake(browser)
        session_id = self._start_scan(browser, auto_t=90)
        ready = self._wait_progress_ready(browser, session_id)
        self.assertIn("Continue to review", ready.text)
        run_id = self._run_id_from_progress_html(ready.text)
        self._patch_high_score_ineligible(run_id, group_index=0)
        # After API restart, re-GET progress for a fresh form token, then Continue.
        ready2 = self._get(
            browser, f"/sessions/{session_id}/crm-duplicates/progress/"
        )
        self.assertIn(
            "Continue to review",
            ready2.text,
            ready2.text[:1200],
        )
        html = self._post_continue(browser, session_id, ready2.text)
        # Not all-auto: blocked high-score group must still appear for review.
        self.assertTrue(
            "Review duplicate groups" in html or "crm-duplicate-review-form" in html,
            html[:2000],
        )
        self.assertIn("crm-duplicate-review-form", html)
        # Exactly one high group auto-approved; the blocked group remains manual.
        self.assertRegex(html, r"Auto-approved\s+1\s+high-confidence")
        # Blocker token is rendered under execution blockers on the review card.
        self.assertIn("phase7c_high_score_ineligible_fixture", html)

