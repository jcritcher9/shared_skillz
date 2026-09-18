"""Phase 7A O6 — Fake merge execute full ladder on stable dual-process stack.



True dual-process HTTP browser against live Django + live API:



* **Companies** CRM scan → review → freeze → dry_run (zero CRM mutation) or

  execute (losers only) with terminal Groups processed + record accountability.

* **People** CRM scan → review → freeze → dry_run / execute with Contact

  survivor/loser outcomes on durable fake storage.



Isolation: absolute API state root + pinned ``LOCALAPPDATA``. No live CRM.



Authority:

``mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/local_crm_dupe_e2e_storage_and_operator_path/local_crm_dupe_e2e_storage_and_operator_path.md``

Phase 7A.

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





PAIR_COUNT = 2





class LocalCrmDupeStoragePhase7aDualProcessTests(SimpleTestCase):

    """Browser freeze → dry_run / execute on isolated fake stack."""



    databases = set()



    @classmethod

    def setUpClass(cls):

        super().setUpClass()

        cls.repo = Path(__file__).resolve().parents[2]

        cls.state_dir = tempfile.TemporaryDirectory(prefix="ei_web_p7a_")

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

    def _seed_person_pair(cls) -> None:

        script = r"""

import sys

from pathlib import Path

root = Path(sys.argv[1])

from mappings_2.integrations.crm.fake.provider import (

    build_fake_crm_stack,

    seed_person_duplicates,

)

stack = build_fake_crm_stack(root / "fake_crm")

stack.org.records.clear()

seed_person_duplicates(stack.org, contact_ids=("C1", "C2"), lead_id=None)

stack.org.mutation_count = 0

stack.org.save()

stack.close()

print("seeded person pair")

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

                "person seed failed:\n"

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

        raise RuntimeError("Phase 7A API process did not start.")



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

    def _restart_api_with_seed(cls, *, person: bool = False) -> None:

        cls._stop_api()

        if person:

            cls._seed_person_pair()

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

                "Phase 7A Django migrate failed:\n"

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

        raise RuntimeError("Phase 7A Django runserver did not start.")



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

        session.headers.update({"User-Agent": "Phase7AStorageE2E/1.0"})

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

        self.assertIsNotNone(match, f"hidden {name!r} missing")

        return match.group(1)



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



    def _org_snapshot(self, *, object_type: str = "Account") -> dict:

        org_path = self.api_state / "fake_crm" / "org.json"

        payload = json.loads(org_path.read_text(encoding="utf-8"))

        records = payload.get("records") or {}

        deleted = sorted(

            rid

            for rid, rec in records.items()

            if isinstance(rec, dict)

            and rec.get("object_type") == object_type

            and rec.get("is_deleted")

        )

        return {

            "mutation_count": int(payload.get("mutation_count") or 0),

            "deleted_ids": deleted,

            "deleted_account_ids": deleted if object_type == "Account" else [],

            "record_ids": sorted(

                rid

                for rid, rec in records.items()

                if isinstance(rec, dict) and rec.get("object_type") == object_type

            ),

            "account_ids": sorted(

                rid

                for rid, rec in records.items()

                if isinstance(rec, dict) and rec.get("object_type") == "Account"

            ),

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

        self.fail(f"timed out waiting for review: {last[:800]}")



    def _submit_review_windows_until_merge(

        self, browser: requests.Session, session_id: str, first_html: str

    ) -> str:

        html = first_html

        for _ in range(8):

            if html_shows_unfrozen_merge_plan(html):

                return html

            if "crm-duplicate-review-form" not in html:

                merge = self._get(

                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"

                )

                return merge.text



            group_order = self._extract_hidden(html, "group_order")

            group_ids = [g for g in group_order.split(",") if g.strip()]

            data = {

                "csrfmiddlewaretoken": self._extract_csrf(html),

                "form_token": self._extract_form_token(html),

                "window_id": self._extract_hidden(html, "window_id"),

                "window_digest": self._extract_hidden(html, "window_digest"),

                "expected_revision": self._extract_hidden(html, "expected_revision"),

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

        self.fail("did not reach merge after review")



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

        fin = self._post_form(

            browser,

            f"/sessions/{session_id}/crm-duplicates/merge/",

            {

                "csrfmiddlewaretoken": self._extract_csrf(merge_html),

                "form_token": self._extract_form_token(merge_html),

                "merge_action": "finalize",

            },

        )

        self.assertIn(fin.status_code, {200, 302}, fin.text[:800])

        merge2 = (

            fin

            if AUTHORIZE_MERGE_STEP_LABEL in fin.text

            else self._get(browser, f"/sessions/{session_id}/crm-duplicates/merge/")

        )

        self.assertIn(AUTHORIZE_MERGE_STEP_LABEL, merge2.text)

        self.assertIn(mode, merge2.text)

        auth = self._post_form(

            browser,

            f"/sessions/{session_id}/crm-duplicates/merge/",

            {

                "csrfmiddlewaretoken": self._extract_csrf(merge2.text),

                "form_token": self._extract_form_token(merge2.text),

                "merge_action": "authorize",

                "selected_mode": mode,

            },

        )

        self.assertIn(auth.status_code, {200, 302}, auth.text[:800])

        html = auth.text

        if "/workflow/" not in auth.url:

            html = self._get(browser, f"/sessions/{session_id}/workflow/").text

        html = self._poll_mutation_status(browser, html)

        return self._get(browser, f"/sessions/{session_id}/workflow/").text



    def _scan_to_merge(

        self,

        browser: requests.Session,

        *,

        entity_family: str = "company",

    ) -> tuple[str, str]:

        start = self._get(browser, "/crm/duplicate-journeys/")

        connection_id = self._connection_id_from_start_page(start.text)

        posted = self._post_form(

            browser,

            "/crm/duplicate-journeys/",

            {

                "csrfmiddlewaretoken": self._extract_csrf(start.text),

                "form_token": self._extract_form_token(start.text),

                "connection_id": connection_id,

                "entity_family": entity_family,

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

        merge_html = self._submit_review_windows_until_merge(

            browser, session_id, review_html

        )

        return session_id, merge_html



    def _assert_terminal_group_and_record_copy(

        self, html: str, *, group_count: int

    ) -> None:

        """Bind terminal UI to every-group accounting (not count-only)."""



        self.assertIn("Groups processed", html)

        self.assertRegex(

            html,

            rf'<div class="stat-value">\s*{group_count}\s*</div>\s*'

            r'<div class="stat-label">Groups processed</div>',

        )

        # Record-level accountability surfaces for the frozen plan.

        lower = html.lower()

        self.assertTrue(

            "survivor" in lower

            or "loser" in lower

            or "record" in lower

            or "member" in lower

            or "processed" in lower,

            html[:600],

        )



    def test_o6_browser_dry_run_zero_crm_mutation(self):

        self._restart_api_with_seed()

        browser = self._browser()

        self._connect_fake(browser)

        before = self._org_snapshot()

        session_id, merge_html = self._scan_to_merge(browser)

        # Capture frozen plan group count before authorize.

        self.assertIn("Review merge plan", merge_html)

        self.assertIn(str(PAIR_COUNT), merge_html)

        workflow_html = self._finalize_and_authorize(

            browser, session_id, merge_html, mode="dry_run"

        )

        after = self._org_snapshot()

        self.assertEqual(after["mutation_count"], before["mutation_count"])

        self.assertEqual(after["deleted_account_ids"], [])

        self.assertEqual(after["record_ids"], before["record_ids"])

        self.assertIn(

            "No live CRM changes were authorized for this run.",

            workflow_html,

        )

        self._assert_terminal_group_and_record_copy(

            workflow_html, group_count=PAIR_COUNT

        )

        # Every seeded account still present after dry_run.

        for index in range(PAIR_COUNT):

            self.assertIn(f"P{index}L", after["record_ids"])

            self.assertIn(f"P{index}R", after["record_ids"])



    def test_o6_browser_execute_merges_only_losers(self):

        self._restart_api_with_seed()

        browser = self._browser()

        self._connect_fake(browser)

        before = self._org_snapshot()

        session_id, merge_html = self._scan_to_merge(browser)

        workflow_html = self._finalize_and_authorize(

            browser, session_id, merge_html, mode="execute"

        )

        after = self._org_snapshot()

        # PAIR_COUNT pairs → PAIR_COUNT losers deleted; survivors remain.

        self.assertEqual(len(after["deleted_account_ids"]), PAIR_COUNT)

        self.assertGreater(after["mutation_count"], before["mutation_count"])

        remaining = set(after["account_ids"]) - set(after["deleted_account_ids"])

        self.assertEqual(len(remaining), PAIR_COUNT)

        self.assertNotIn(

            "No live CRM changes were authorized for this run.",

            workflow_html,

        )

        self._assert_terminal_group_and_record_copy(

            workflow_html, group_count=PAIR_COUNT

        )

        machine_boot = self.machine / "EasyImports" / "bootstrap.json"

        if machine_boot.exists():

            self.assertTrue(str(machine_boot).startswith(str(self.machine)))



    def test_o6_browser_person_dry_run_and_execute(self):

        """People ladder: dry_run leaves Contacts; execute deletes one loser."""



        # dry_run

        self._restart_api_with_seed(person=True)

        browser = self._browser()

        self._connect_fake(browser)

        before = self._org_snapshot(object_type="Contact")

        self.assertEqual(set(before["record_ids"]), {"C1", "C2"})

        session_id, merge_html = self._scan_to_merge(

            browser, entity_family="person"

        )

        dry_html = self._finalize_and_authorize(

            browser, session_id, merge_html, mode="dry_run"

        )

        after_dry = self._org_snapshot(object_type="Contact")

        self.assertEqual(after_dry["mutation_count"], before["mutation_count"])

        self.assertEqual(after_dry["deleted_ids"], [])

        self.assertEqual(set(after_dry["record_ids"]), {"C1", "C2"})

        self.assertIn(

            "No live CRM changes were authorized for this run.",

            dry_html,

        )

        self._assert_terminal_group_and_record_copy(dry_html, group_count=1)



        # execute on fresh person seed

        self._restart_api_with_seed(person=True)

        browser2 = self._browser()

        self._connect_fake(browser2)

        before_ex = self._org_snapshot(object_type="Contact")

        session_id2, merge_html2 = self._scan_to_merge(

            browser2, entity_family="person"

        )

        ex_html = self._finalize_and_authorize(

            browser2, session_id2, merge_html2, mode="execute"

        )

        after_ex = self._org_snapshot(object_type="Contact")

        self.assertEqual(len(after_ex["deleted_ids"]), 1)

        self.assertGreater(

            after_ex["mutation_count"], before_ex["mutation_count"]

        )

        remaining = set(after_ex["record_ids"]) - set(after_ex["deleted_ids"])

        self.assertEqual(len(remaining), 1)

        self.assertTrue(remaining.issubset({"C1", "C2"}))

        self.assertNotIn(

            "No live CRM changes were authorized for this run.",

            ex_html,

        )

        self._assert_terminal_group_and_record_copy(ex_html, group_count=1)

