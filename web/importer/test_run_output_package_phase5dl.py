"""Phase 5-DL: network-free browser accept for always-download package.

HTTP browser → live Django → live API: complete a synthetic single-dataset
run, create/download the run-output package, verify ZIP layout and integrity.
"""

from __future__ import annotations

from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

import requests
from django.test import TransactionTestCase

from importer.pending_copy import html_has_pending_mutation_surface


class CrmConnectionSetupUxPhase5DlTests(TransactionTestCase):
    """Phase 5-DL dual-process browser accept (desire #5 / freeze §5)."""

    PEOPLE_CSV = (
        b"First Name,Last Name,Email\n"
        b"Example,Person,example.person@example.com\n"
        b"Second,Contact,second.contact@example.com\n"
    )

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
        raise RuntimeError("Phase 5-DL API process did not start.")

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
                "Phase 5-DL Django migrate failed:\n"
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
        raise RuntimeError("Phase 5-DL Django runserver did not start.")

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
        session.headers.update({"User-Agent": "Phase5DlBrowser/1.0"})
        return session

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.django_base + path

    def _extract_form_token(self, html: str, *, near: str | None = None) -> str:
        marker = 'name="form_token" value="'
        if near:
            idx = 0
            chosen = None
            while True:
                pos = html.find(marker, idx)
                if pos == -1:
                    break
                start = pos + len(marker)
                end = html.find('"', start)
                candidate = html[start:end]
                window = html[max(0, pos - 500) : pos + 80]
                if near in window:
                    chosen = candidate
                idx = end + 1
            if chosen is not None:
                return chosen
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
        # Prefer exact value= form.
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
        match = re.search(
            r"/sessions/([0-9a-fA-F-]{36})/",
            url,
        )
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
            timeout=120,
            allow_redirects=allow_redirects,
        )

    def _poll_pending_mutations(self, browser: requests.Session, html: str) -> str:
        """Drive mutation_status / reload until no in-progress cards remain."""

        deadline = time.monotonic() + 45
        current = html
        while time.monotonic() < deadline:
            urls = re.findall(
                r'data-mutation-status-url="([^"]+)"',
                current,
            )
            if not urls:
                if not html_has_pending_mutation_surface(current):
                    return current
                # Unknown without status URL: try retry if available.
                if "Retry saved action" in current:
                    retry_match = re.search(
                        r'action="(/sessions/[^"]+/mutations/[^"]+/retry/)"',
                        current,
                    )
                    if retry_match is not None:
                        token = self._extract_form_token(current)
                        csrf = self._extract_csrf(current)
                        self._post_form(
                            browser,
                            retry_match.group(1),
                            {
                                "form_token": token,
                                "csrfmiddlewaretoken": csrf,
                            },
                        )
                path = browser.get(self._url("/"), timeout=10).url  # keep session warm
                del path
                # Reload last workflow from recent path in current URL isn't stored;
                # caller should re-GET. Fall through to sleep + require caller loop.
            for rel in urls:
                try:
                    status = browser.get(self._url(rel), timeout=15)
                    if status.status_code == 200:
                        try:
                            body = status.json()
                        except ValueError:
                            body = {}
                        if body.get("status") in {"completed", "rejected"}:
                            redirect = body.get("redirect_url") or ""
                            if redirect:
                                refreshed = browser.get(
                                    self._url(redirect),
                                    timeout=60,
                                    allow_redirects=True,
                                )
                                current = refreshed.text
                                continue
                except requests.RequestException:
                    pass
            time.sleep(0.15)
            # Caller provides workflow path via re-get; keep current if no urls.
            if not urls:
                break
        return current

    def _wait_workflow_for(
        self,
        browser: requests.Session,
        session_id: str,
        *,
        needle: str,
        timeout: float = 60,
    ) -> requests.Response:
        deadline = time.monotonic() + timeout
        last = None
        path = f"/sessions/{session_id}/workflow/"
        while time.monotonic() < deadline:
            last = browser.get(self._url(path), timeout=60, allow_redirects=True)
            self.assertEqual(last.status_code, 200, last.text[:500])
            html = self._poll_pending_mutations(browser, last.text)
            # Re-fetch after polling mutations.
            last = browser.get(self._url(path), timeout=60, allow_redirects=True)
            self.assertEqual(last.status_code, 200, last.text[:500])
            if needle in last.text:
                return last
            if html_has_pending_mutation_surface(last.text):
                self._poll_pending_mutations(browser, last.text)
            time.sleep(0.2)
        self.fail(
            f"Timed out waiting for {needle!r} on workflow.\n"
            f"Last body (trunc): {(last.text if last is not None else '')[:800]}"
        )

    def test_browser_completed_run_downloads_package_layout_and_digest(self):
        """E2E: synthetic clean-only people run → package ZIP layout + integrity."""

        browser = self._browser()

        # Home → Start an import
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
        session_id = self._session_id_from_url(started.url)

        # Intent: people + clean-only + contacts + preview target
        product_html = started.text
        form_token = self._extract_form_token(product_html)
        csrf = self._extract_csrf(product_html)
        setup_revision = self._extract_hidden(product_html, "setup_revision")
        product_post = self._post_form(
            browser,
            f"/sessions/{session_id}/product/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "operator_label": "Phase5DL Operator",
                "entity": "people",
                "operation": "clean_only",
                "reference_source": "",
                "connection_id": "",
                "people_output": "contact",
                "target_provider_id": "fake-preview-v1",
                "setup_revision": setup_revision,
            },
        )
        self.assertEqual(product_post.status_code, 200, product_post.text[:500])
        self.assertIn("/upload/", product_post.url)

        # Upload dataset CSV
        upload_html = product_post.text
        upload_token = self._extract_form_token(upload_html)
        csrf = self._extract_csrf(upload_html)
        uploaded = self._post_form(
            browser,
            f"/sessions/{session_id}/upload/",
            {
                "form_token": upload_token,
                "csrfmiddlewaretoken": csrf,
                "role": "dataset",
                "csv_encoding": "utf-8-sig",
                "xlsx_sheet_index": "0",
            },
            files={
                "file": ("people.csv", self.PEOPLE_CSV, "text/csv"),
            },
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text[:500])
        self.assertIn("people.csv", uploaded.text)
        # MAP-R5: Map columns is the primary next action after required upload.
        self.assertIn("Map columns", uploaded.text)
        self.assertIn("Review settings", uploaded.text)

        # MAP-R5 / MAP-3: confirm column mapping before Start import is active.
        map_start = self._get(browser, f"/sessions/{session_id}/column-mapping/")
        self.assertEqual(map_start.status_code, 200, map_start.text[:400])
        map_token = self._extract_form_token(map_start.text)
        csrf = self._extract_csrf(map_start.text)
        created_map = self._post_form(
            browser,
            f"/sessions/{session_id}/column-mapping/",
            {
                "form_token": map_token,
                "csrfmiddlewaretoken": csrf,
            },
        )
        self.assertEqual(created_map.status_code, 200, created_map.text[:500])
        plan_match = re.search(r"/column-mapping/([^/?#]+)/", created_map.url)
        if plan_match is None:
            link = re.search(
                rf"/sessions/{session_id}/column-mapping/([^/\"'?]+)/",
                created_map.text,
            )
            self.assertIsNotNone(link, "column mapping plan create failed")
            plan_id = link.group(1)
        else:
            plan_id = plan_match.group(1)
        for _ in range(12):
            review = self._get(
                browser, f"/sessions/{session_id}/column-mapping/{plan_id}/"
            )
            self.assertEqual(review.status_code, 200)
            if "/configure/" in review.url or "Review your import settings" in review.text:
                break
            if re.search(
                r"Mapping confirmed|id=\"mapping-confirmed\"",
                review.text,
                flags=re.IGNORECASE,
            ):
                break
            form_token = self._extract_form_token(review.text)
            csrf = self._extract_csrf(review.text)
            payload = {
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
            needs_review = bool(
                re.search(
                    r"<tr\b[^>]*\bdata-filter=\"needs_review\"",
                    review.text,
                    flags=re.IGNORECASE,
                )
            )
            payload["action"] = "save_draft" if needs_review else "confirm_mapping"
            posted = self._post_form(
                browser,
                f"/sessions/{session_id}/column-mapping/{plan_id}/",
                payload,
            )
            self.assertEqual(posted.status_code, 200, posted.text[:500])
            if "/configure/" in posted.url or "Review your import settings" in posted.text:
                break
        else:
            self.fail("Could not confirm column mapping for package-download accept")

        # Configure → start import (delivery preview default for single dataset)
        configure = self._get(browser, f"/sessions/{session_id}/configure/")
        self.assertEqual(configure.status_code, 200, configure.text[:500])
        self.assertIn("Start import", configure.text)
        cfg_token = self._extract_form_token(configure.text)
        csrf = self._extract_csrf(configure.text)
        setup_revision = self._extract_hidden(configure.text, "setup_revision")
        # Collect mode fields present on the form.
        mode_fields = re.findall(r'name="(mode__[^"]+)"', configure.text)
        configure_data = {
            "form_token": cfg_token,
            "csrfmiddlewaretoken": csrf,
            "setup_revision": setup_revision,
            "list_duplicate_policy": "drop_repeats",
        }
        for field in mode_fields:
            if field == "mode__delivery":
                configure_data[field] = "preview"
            else:
                configure_data[field] = "disabled"
        created = self._post_form(
            browser,
            f"/sessions/{session_id}/configure/",
            configure_data,
        )
        self.assertEqual(created.status_code, 200, created.text[:600])
        self.assertIn(
            f"/sessions/{session_id}/workflow/",
            created.url,
            created.url,
        )

        # Wait for effect authorization (delivery) or already-terminal package CTA.
        page = browser.get(
            self._url(f"/sessions/{session_id}/workflow/"),
            timeout=60,
            allow_redirects=True,
        )
        self.assertEqual(page.status_code, 200, page.text[:500])
        if "Download results package" not in page.text:
            page = self._wait_workflow_for(
                browser,
                session_id,
                needle='name="selected_mode"',
                timeout=60,
            )
        if "Download results package" not in page.text:
            # Authorize delivery preview.
            effect_form = re.search(
                r'action="(/sessions/[^"]+/effects/authorize/)"(.*?</form>)',
                page.text,
                re.DOTALL,
            )
            self.assertIsNotNone(effect_form, "effect authorize form missing")
            form_chunk = effect_form.group(0)
            effect_token = self._extract_form_token(form_chunk)
            csrf = self._extract_csrf(page.text)
            # Prefer preview when offered.
            mode = "preview"
            if 'value="preview"' not in form_chunk:
                mode_match = re.search(
                    r'<option value="([^"]+)"',
                    form_chunk,
                )
                self.assertIsNotNone(mode_match)
                mode = mode_match.group(1)
            authorized = self._post_form(
                browser,
                f"/sessions/{session_id}/effects/authorize/",
                {
                    "form_token": effect_token,
                    "csrfmiddlewaretoken": csrf,
                    "selected_mode": mode,
                },
            )
            self.assertEqual(authorized.status_code, 200, authorized.text[:500])
            page = self._wait_workflow_for(
                browser,
                session_id,
                needle="Download results package",
                timeout=60,
            )
        else:
            page = self._wait_workflow_for(
                browser,
                session_id,
                needle="Download results package",
                timeout=60,
            )

        self.assertIn("Download results package", page.text)
        self.assertIn("data-run-output-package", page.text)
        self.assertIn("Results package", page.text)

        # Create package (journaled POST) → content proxy streams ZIP.
        create_form = re.search(
            r'action="(/sessions/[^"]+/run-output-package/)"(.*?</form>)',
            page.text,
            re.DOTALL,
        )
        package_id_from_link = None
        if create_form is None:
            # Package already resolved (GET discovery) — download link present.
            link = re.search(
                r'href="(/sessions/[^"]+/run-output-packages/([^"/]+)/)"',
                page.text,
            )
            self.assertIsNotNone(link, "package create form or download link missing")
            download_path = link.group(1)
            package_id_from_link = link.group(2)
            downloaded = browser.get(
                self._url(download_path),
                timeout=120,
                allow_redirects=True,
            )
        else:
            form_chunk = create_form.group(0)
            create_token = self._extract_form_token(form_chunk)
            csrf = self._extract_csrf(page.text)
            downloaded = self._post_form(
                browser,
                f"/sessions/{session_id}/run-output-package/",
                {
                    "form_token": create_token,
                    "csrfmiddlewaretoken": csrf,
                },
            )

        self.assertEqual(
            downloaded.status_code,
            200,
            f"package download failed: {downloaded.status_code} {downloaded.text[:400]}",
        )
        content_type = downloaded.headers.get("Content-Type", "")
        self.assertTrue(
            "zip" in content_type or "octet-stream" in content_type,
            content_type,
        )
        zip_bytes = downloaded.content
        self.assertGreater(len(zip_bytes), 32)
        content_digest = sha256(zip_bytes).hexdigest()

        # Layout + MANIFEST (freeze §5.7)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names), names)
            self.assertIn("MANIFEST.json", names)
            self.assertTrue(
                any(n.startswith("debug/") for n in names),
                names,
            )
            self.assertIn("debug/package_build.json", names)
            # Membership may vary; require at least MANIFEST + debug build.
            # Import/metadata present for a successful clean-only people run.
            self.assertTrue(
                any(n.startswith("import/") for n in names)
                or any(n.startswith("metadata/") for n in names),
                names,
            )
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), info.filename)
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED, info.filename)

            manifest = json.loads(archive.read("MANIFEST.json").decode("utf-8"))
            self.assertEqual(manifest.get("layout_version"), "run_output_package.v1")
            self.assertNotIn("content_digest", manifest)
            files = manifest.get("files") or []
            self.assertIsInstance(files, list)
            paths = [item.get("path") for item in files]
            self.assertEqual(paths, sorted(paths))
            self.assertNotIn("MANIFEST.json", paths)
            # Per-file digests in MANIFEST must match archive bytes.
            for item in files:
                path = item["path"]
                expected = item["content_digest"]
                actual = sha256(archive.read(path)).hexdigest()
                self.assertEqual(actual, expected, path)

            build = json.loads(
                archive.read("debug/package_build.json").decode("utf-8")
            )
            self.assertNotIn("created_at", build)
            self.assertNotIn("package_id", build)
            self.assertIn("api_version", build)
            self.assertIn("run_id", build)

        # Terminal re-load: package ready link + same identity available.
        terminal = self._get(browser, f"/sessions/{session_id}/workflow/")
        self.assertIn("Download results package", terminal.text)
        self.assertIn("Package ready", terminal.text)
        link = re.search(
            r'href="(/sessions/[^"]+/run-output-packages/([^"/]+)/)"',
            terminal.text,
        )
        self.assertIsNotNone(link, "ready package download link missing after create")
        package_id = link.group(2)
        if package_id_from_link:
            self.assertEqual(package_id, package_id_from_link)

        # Re-download via GET content proxy — same bytes / digest (determinism).
        again = browser.get(self._url(link.group(1)), timeout=120, allow_redirects=True)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(sha256(again.content).hexdigest(), content_digest)
        self.assertEqual(again.content, zip_bytes)

        # No directory-path leakage in customer body.
        lower = terminal.text.lower()
        self.assertNotIn("localappdata", lower)
        self.assertNotIn(str(self.api_state).lower(), lower)
