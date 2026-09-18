"""Phase 5B rem: real-process browser → Django → API acceptance.

Starts **both** API and Django as separate processes. The browser is an HTTP
``requests.Session`` against live Django (no ``self.client``, no ORM/context
assertions for product outcomes). Seed uses ``web/tools/seed_fake_crm_org.py``
subprocess so Django never imports ``mappings_2``.

Authority: completed_projects/crm_duplicate_operator_journey_redesign.md Phase 5B.
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
    APPROVE_MERGE_PLAN_LABEL,
    AUTHORIZE_MERGE_STEP_LABEL,
    html_shows_unfrozen_merge_plan,
)
from .pending_copy import html_has_pending_mutation_surface


PAIR_COUNT = 6


class CrmDuplicateJourneyPhase5bHttpBrowserTests(SimpleTestCase):
    """True dual-process HTTP browser acceptance (not Django TestClient)."""

    # Django DB is in a separate runserver process; no shared test DB.
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
        raise RuntimeError("Phase 5B API process did not start.")

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
    def _restart_api_with_seed(cls) -> None:
        cls._stop_api()
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
                "Phase 5B Django migrate failed:\n"
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
        raise RuntimeError("Phase 5B Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase5BBrowser/1.0"})
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
            "account_ids": sorted(
                rid
                for rid, rec in records.items()
                if isinstance(rec, dict) and rec.get("object_type") == "Account"
            ),
        }

    def _connect_fake(self, browser: requests.Session) -> None:
        page = self._get(browser, "/crm/connections/")
        # Prefer direct connect endpoint used by FE-EXEC.
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
        self.assertIn(
            response.status_code,
            {200, 302},
            response.text[:500],
        )
        # Land somewhere with a connected fake account visible.
        connections = self._get(browser, "/crm/connections/")
        self.assertIn(
            "fake",
            connections.text.lower(),
            connections.text[:800],
        )

    def _pair_csv_bytes(self) -> bytes:
        lines = ["Id,Name"]
        for index in range(PAIR_COUNT):
            name = f"Holdings{index:03d}"
            lines.append(f"P{index}L,{name}")
            lines.append(f"P{index}R,{name}")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _connection_id_from_start_page(self, html: str) -> str:
        # First connection option value after connect.
        match = re.search(
            r'<option[^>]+value="(crm_conn_[^"]+)"',
            html,
        )
        self.assertIsNotNone(match, "no connected CRM option on start page")
        return match.group(1)

    def _wait_for_review(self, browser: requests.Session, session_id: str) -> str:
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
                return last
            if "Review merge plan" in last:
                return last
            if "No duplicate groups found" in last:
                self.fail(f"unexpected zero groups: {last[:600]}")
            time.sleep(0.2)
        self.fail(f"timed out waiting for review page: {last[:800]}")

    def _submit_review_windows_until_merge(
        self, browser: requests.Session, session_id: str, first_html: str
    ) -> str:
        html = first_html
        windows_submitted = 0
        for _step in range(12):
            if html_shows_unfrozen_merge_plan(html):
                self.assertGreaterEqual(
                    windows_submitted, 2, "expected multi-window before merge"
                )
                return html
            if "crm-duplicate-review-form" not in html:
                # Summary page with link to merge.
                merge = self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
                return merge.text
            # Assert multi-window shape on first window.
            if windows_submitted == 0:
                self.assertIn("Review duplicate groups 1–5 of 6", html)
                self.assertIn("Save these 5 and review next 5", html)
            elif windows_submitted == 1:
                self.assertIn("Review duplicate groups 6–6 of 6", html)
                self.assertIn("Save final groups and review merge plan", html)

            token = self._extract_form_token(html)
            csrf = self._extract_csrf(html)
            window_id = self._extract_hidden(html, "window_id")
            window_digest = self._extract_hidden(html, "window_digest")
            expected_revision = self._extract_hidden(html, "expected_revision")
            group_order = self._extract_hidden(html, "group_order")
            group_ids = [g for g in group_order.split(",") if g.strip()]
            self.assertTrue(group_ids, "group_order empty")
            if windows_submitted == 0:
                self.assertEqual(len(group_ids), 5)
            if windows_submitted == 1:
                self.assertEqual(len(group_ids), 1)

            data = {
                "csrfmiddlewaretoken": csrf,
                "form_token": token,
                "window_id": window_id,
                "window_digest": window_digest,
                "expected_revision": expected_revision,
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
                # Defaults: approve recommended survivor when present.
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
            windows_submitted += 1
            html = post.text
            if post.url and "merge" in post.url:
                return html
        self.fail("did not reach merge after review windows")

    def _poll_mutation_status(self, browser: requests.Session, html: str) -> str:
        deadline = time.monotonic() + 90.0
        current = html
        while time.monotonic() < deadline:
            urls = re.findall(r'data-mutation-status-url="([^"]+)"', current)
            if not urls:
                if not html_has_pending_mutation_surface(current):
                    return current
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
                except (requests.RequestException, ValueError):
                    pass
            time.sleep(0.15)
            # Reload workflow if we have a session path in any status URL.
            if urls:
                match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", urls[0])
                if match:
                    refreshed = browser.get(
                        self._url(f"/sessions/{match.group(1)}/workflow/"),
                        timeout=60,
                        allow_redirects=True,
                    )
                    current = refreshed.text
        return current

    def _finalize_and_authorize(
        self,
        browser: requests.Session,
        session_id: str,
        merge_html: str,
        *,
        mode: str,
    ) -> str:
        self.assertIn("Review merge plan", merge_html)
        self.assertIn(APPROVE_MERGE_PLAN_LABEL, merge_html)
        # Multi-group counts visible on summary.
        self.assertRegex(merge_html, r"Duplicate groups found")
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
        self.assertIn(fin.status_code, {200, 302}, fin.text[:800])
        merge2 = fin if AUTHORIZE_MERGE_STEP_LABEL in fin.text else self._get(
            browser, f"/sessions/{session_id}/crm-duplicates/merge/"
        )
        self.assertIn(AUTHORIZE_MERGE_STEP_LABEL, merge2.text)
        self.assertIn(mode, merge2.text)
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
        # Should land on workflow for terminal / in-progress poll.
        html = auth.text
        if "/workflow/" not in auth.url:
            html = self._get(
                browser, f"/sessions/{session_id}/workflow/"
            ).text
        html = self._poll_mutation_status(browser, html)
        # Final reload.
        final = self._get(browser, f"/sessions/{session_id}/workflow/")
        return final.text

    def test_http_browser_upload_branch_multi_window_dry_run(self):
        """HTTP browser: upload → map → 5+1 windows → freeze → dry_run terminal."""

        self._restart_api_with_seed()
        browser = self._browser()
        self._connect_fake(browser)

        start = self._get(browser, "/crm/duplicate-journeys/")
        self.assertIn("Resolve CRM duplicates", start.text)
        connection_id = self._connection_id_from_start_page(start.text)
        token = self._extract_form_token(start.text)
        csrf = self._extract_csrf(start.text)
        files = {
            "population_file": ("records.csv", self._pair_csv_bytes(), "text/csv"),
        }
        posted = self._post_form(
            browser,
            "/crm/duplicate-journeys/",
            {
                "csrfmiddlewaretoken": csrf,
                "form_token": token,
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "uploaded_population",
            },
            files=files,
        )
        self.assertIn("map-record-id", posted.url, posted.text[:400])
        session_id = self._session_id_from_url(posted.url)
        map_html = posted.text
        self.assertIn("Record ID", map_html)
        map_token = self._extract_form_token(map_html)
        map_csrf = self._extract_csrf(map_html)
        # Prefer Id column.
        mapped = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/map-record-id/",
            {
                "csrfmiddlewaretoken": map_csrf,
                "form_token": map_token,
                "source_column": "Id",
            },
        )
        # Progress may auto-redirect into review when analysis is already ready.
        self.assertTrue(
            any(part in mapped.url for part in ("progress", "review", "merge")),
            mapped.url,
        )

        before = self._org_snapshot()
        if "Review duplicate groups" in mapped.text:
            review_html = mapped.text
        else:
            review_html = self._wait_for_review(browser, session_id)
        self.assertIn("Review duplicate groups 1–5 of 6", review_html)
        # Record IDs from the multi-pair seed appear (order is backend-owned).
        self.assertRegex(review_html, r"P\d[LR]")
        self.assertIn("Holdings", review_html)
        self.assertNotIn("reference_acquisition", review_html)
        merge_html = self._submit_review_windows_until_merge(
            browser, session_id, review_html
        )
        workflow_html = self._finalize_and_authorize(
            browser, session_id, merge_html, mode="dry_run"
        )
        after = self._org_snapshot()
        self.assertEqual(after["mutation_count"], before["mutation_count"])
        self.assertEqual(after["deleted_account_ids"], [])
        # Terminal: dry_run is not live CRM mutation; exact group total.
        self.assertIn(
            "No live CRM changes were authorized for this run.",
            workflow_html,
        )
        self.assertIn("Groups processed", workflow_html)
        self.assertRegex(
            workflow_html,
            rf'<div class="stat-value">\s*{PAIR_COUNT}\s*</div>\s*'
            r'<div class="stat-label">Groups processed</div>',
        )
        self.assertNotIn("access_token", workflow_html)
        self.assertNotIn("client_secret", workflow_html)

    def test_http_browser_crm_scan_branch_multi_window_execute(self):
        """HTTP browser: CRM scan → 5+1 windows → freeze → execute terminal."""

        self._restart_api_with_seed()
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
        self.assertNotIn("map-record-id", posted.url)
        self.assertTrue(
            any(part in posted.url for part in ("progress", "review", "merge")),
            posted.url,
        )
        session_id = self._session_id_from_url(posted.url)

        before = self._org_snapshot()
        if "Review duplicate groups" in posted.text:
            review_html = posted.text
        else:
            review_html = self._wait_for_review(browser, session_id)
        self.assertIn("Review duplicate groups 1–5 of 6", review_html)
        merge_html = self._submit_review_windows_until_merge(
            browser, session_id, review_html
        )
        workflow_html = self._finalize_and_authorize(
            browser, session_id, merge_html, mode="execute"
        )
        after = self._org_snapshot()
        # Six pairs → six losers deleted under default approve-recommended.
        self.assertEqual(len(after["deleted_account_ids"]), PAIR_COUNT)
        self.assertGreater(after["mutation_count"], before["mutation_count"])
        # Survivors remain.
        remaining = set(after["account_ids"]) - set(after["deleted_account_ids"])
        self.assertEqual(len(remaining), PAIR_COUNT)
        self.assertNotIn("access_token", workflow_html)
        self.assertNotIn(
            "No live CRM changes were authorized for this run.",
            workflow_html,
        )
        self.assertIn("Groups processed", workflow_html)
        self.assertRegex(
            workflow_html,
            rf'<div class="stat-value">\s*{PAIR_COUNT}\s*</div>\s*'
            r'<div class="stat-label">Groups processed</div>',
        )
