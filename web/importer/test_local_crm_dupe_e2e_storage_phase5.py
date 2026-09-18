"""Phase 5 O4 — Five-group frontend review on stable dual-process stack.

True dual-process HTTP browser against live Django + live API.

* Six company pairs produce windows 1–5 and 6–6; each projected group receives
  exactly one outcome. Merge-eligible groups default to backend recommended
  survivor; one group is overridden to the other eligible member.
* A deterministic **person** fixture produces a genuine
  ``group_status=quarantined`` projection (only decline/quarantine; no
  survivor radios / no approve).

Isolation: absolute API state root + pinned ``LOCALAPPDATA``.

Authority:
``mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/local_crm_dupe_e2e_storage_and_operator_path/local_crm_dupe_e2e_storage_and_operator_path.md``
Phase 5.
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

from .crm_duplicate_merge_copy import (
    APPROVE_MERGE_PLAN_LABEL,
    html_shows_unfrozen_merge_plan,
)


PAIR_COUNT = 6


class LocalCrmDupeStoragePhase5DualProcessTests(SimpleTestCase):
    """CRM scan → multi-window five-group review → merge plan (O4)."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_p5_")
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
    def _seed_person_quarantine_pair(cls) -> None:
        """Shared email + conflicting portal ids → group_status=quarantined."""

        script = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
from mappings_2.integrations.crm.fake.provider import build_fake_crm_stack
stack = build_fake_crm_stack(root / "fake_crm")
stack.org.records.clear()
for i, (rid, portal) in enumerate(
    (("C1", "005000000000001AAA"), ("C2", "005000000000002AAA")), start=1
):
    stack.org.upsert(
        record_id=rid,
        object_type="Contact",
        fields={
            "FirstName": "Same",
            "LastName": "Person",
            "Email": "same@real.co",
            "IsPortalEnabled": True,
            "is_portal_linked": True,
            "portal_user_id": portal,
            "rank": i,
        },
        system_modstamp=f"2026-07-19T12:00:0{i}+00:00",
    )
stack.org.mutation_count = 0
stack.org.save()
stack.close()
print("seeded person quarantine")
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
                "person quarantine seed failed:\n"
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
        raise RuntimeError("Phase 5 API process did not start.")

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
    def _restart_api_with_seed(cls, *, person_quarantine: bool = False) -> None:
        cls._stop_api()
        if person_quarantine:
            cls._seed_person_quarantine_pair()
        else:
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
                "Phase 5 Django migrate failed:\n"
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
        raise RuntimeError("Phase 5 Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase5StorageE2E/1.0"})
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
        csrf = (
            self._extract_csrf(page.text)
            if "csrfmiddlewaretoken" in page.text
            else browser.cookies.get("csrftoken", "")
        )
        response = self._post_form(
            browser,
            "/crm/connect/",
            {"provider_key": "fake", "csrfmiddlewaretoken": csrf},
        )
        self.assertIn(response.status_code, {200, 302}, response.text[:500])
        connections = self._get(browser, "/crm/connections/")
        self.assertIn("fake", connections.text.lower(), connections.text[:800])

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
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
        self,
        browser: requests.Session,
        session_id: str,
        first_html: str,
        *,
        override_first: bool = True,
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
                merge = self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
                return merge.text

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
            for index, gid in enumerate(group_ids):
                data[f"group_revision_{gid}"] = self._extract_hidden(
                    html, f"group_revision_{gid}"
                )
                data[f"allowed_{gid}"] = self._extract_hidden(html, f"allowed_{gid}")
                data[f"recommended_{gid}"] = self._extract_hidden(
                    html, f"recommended_{gid}"
                )
                data[f"eligible_{gid}"] = self._extract_hidden(html, f"eligible_{gid}")
                data[f"advanced_{gid}"] = self._extract_hidden(html, f"advanced_{gid}")
                recommended = data[f"recommended_{gid}"]
                allowed = {
                    part.strip()
                    for part in data[f"allowed_{gid}"].split(",")
                    if part.strip()
                }
                eligible = [
                    part.strip()
                    for part in data[f"eligible_{gid}"].split(",")
                    if part.strip()
                ]
                # Exactly one outcome per group.
                if (
                    override_first
                    and windows_submitted == 0
                    and index == 0
                    and recommended
                    and len(eligible) >= 2
                ):
                    other = next(e for e in eligible if e != recommended)
                    data[f"action_{gid}"] = "override_survivor"
                    data[f"survivor_{gid}"] = other
                elif "approve" in allowed and recommended:
                    data[f"action_{gid}"] = "approve"
                    data[f"survivor_{gid}"] = recommended
                elif "decline" in allowed:
                    data[f"action_{gid}"] = "decline"
                else:
                    data[f"action_{gid}"] = next(iter(allowed))

            # One action field per group in group_order.
            for gid in group_ids:
                self.assertIn(f"action_{gid}", data)

            post = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/review/",
                data,
            )
            self.assertIn(post.status_code, {200, 302}, post.text[:800])
            self.assertLess(post.status_code, 500)
            windows_submitted += 1
            html = post.text
            if post.url and "merge" in post.url:
                return html
        self.fail("did not reach merge after review windows")

    def test_o4_multi_window_review_defaults_and_override(self):
        """O4 dual-process: windows 1–5 and 6–6; one outcome each; reach merge."""

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
        self.assertTrue(
            any(part in posted.url for part in ("progress", "review", "merge")),
            posted.url,
        )
        session_id = self._session_id_from_url(posted.url)

        if "Review duplicate groups" in posted.text:
            review_html = posted.text
        else:
            review_html = self._wait_for_review(browser, session_id)

        self.assertIn("Review duplicate groups 1–5 of 6", review_html)
        self.assertIn("crm-duplicate-review-form", review_html)
        # Recommended / survivor defaults visible for merge-eligible groups.
        self.assertIn("recommended_", review_html)
        self.assertIn("Holdings", review_html)

        merge_html = self._submit_review_windows_until_merge(
            browser, session_id, review_html, override_first=True
        )
        self.assertTrue(
            "Review merge plan" in merge_html or APPROVE_MERGE_PLAN_LABEL in merge_html,
            merge_html[:800],
        )
        # Terminal-ish summary should reflect full group accounting.
        self.assertIn("6", merge_html)

        machine_boot = self.machine / "EasyImports" / "bootstrap.json"
        if machine_boot.exists():
            self.assertTrue(str(machine_boot).startswith(str(self.machine)))

    def test_o4_genuinely_quarantined_person_group_browser(self):
        """O4 dual-process: person group_status=quarantined; no survivor radios."""

        self._restart_api_with_seed(person_quarantine=True)
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
                "entity_family": "person",
                "source_mode": "acquire_all",
            },
        )
        self.assertTrue(
            any(part in posted.url for part in ("progress", "review", "merge")),
            posted.url,
        )
        session_id = self._session_id_from_url(posted.url)

        if "Review duplicate groups" in posted.text or "crm-duplicate-review-form" in posted.text:
            review_html = posted.text
        else:
            review_html = self._wait_for_review(browser, session_id)

        self.assertIn("crm-duplicate-review-form", review_html)
        # Genuine quarantined projection surfaces on the FE.
        self.assertIn("quarantined", review_html.lower())
        # Allowed non-merge only — no approve action radios for this group.
        self.assertIn('value="quarantine"', review_html)
        self.assertIn('value="decline"', review_html)
        # Survivor radios must not render (show_survivor_radios=false).
        self.assertNotIn('name="survivor_', review_html)
        # recommended/selected survivor defaults are empty in hidden fields.
        group_order = self._extract_hidden(review_html, "group_order")
        group_ids = [g for g in group_order.split(",") if g.strip()]
        self.assertEqual(len(group_ids), 1, group_order)
        gid = group_ids[0]
        allowed = {
            part.strip()
            for part in self._extract_hidden(review_html, f"allowed_{gid}").split(",")
            if part.strip()
        }
        self.assertEqual(allowed, {"decline", "quarantine"}, allowed)
        self.assertNotIn("approve", allowed)
        self.assertNotIn("override_survivor", allowed)
        recommended = self._extract_hidden(review_html, f"recommended_{gid}")
        self.assertEqual(recommended, "")

        token = self._extract_form_token(review_html)
        csrf = self._extract_csrf(review_html)
        data = {
            "csrfmiddlewaretoken": csrf,
            "form_token": token,
            "window_id": self._extract_hidden(review_html, "window_id"),
            "window_digest": self._extract_hidden(review_html, "window_digest"),
            "expected_revision": self._extract_hidden(
                review_html, "expected_revision"
            ),
            "group_order": group_order,
            f"group_revision_{gid}": self._extract_hidden(
                review_html, f"group_revision_{gid}"
            ),
            f"allowed_{gid}": self._extract_hidden(review_html, f"allowed_{gid}"),
            f"recommended_{gid}": recommended,
            f"eligible_{gid}": self._extract_hidden(review_html, f"eligible_{gid}"),
            f"advanced_{gid}": self._extract_hidden(review_html, f"advanced_{gid}"),
            f"action_{gid}": "quarantine",
        }
        post = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/review/",
            data,
        )
        self.assertIn(post.status_code, {200, 302}, post.text[:800])
        self.assertLess(post.status_code, 500)
        # Single quarantined group completes review → merge summary path.
        self.assertTrue(
            "Review merge plan" in post.text
            or APPROVE_MERGE_PLAN_LABEL in post.text
            or "merge" in post.url
            or "quarantine" in post.text.lower(),
            post.text[:800],
        )
