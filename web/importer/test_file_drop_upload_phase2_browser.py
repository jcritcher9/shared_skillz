"""Phase 2 browser proof: CRM-dupe drop after default acquire_all.

The production start page disables ``population_file`` on acquire_all before
the deferred drop script runs. This module proves the enhancer still binds
and that switching to uploaded_population then dropping a file populates
the field without submitting the journey form.

Network-free: serves a local static fixture over loopback HTTP only.

**Required (not optional):**
- ``playwright`` from ``requirements-dev.txt``
- Chromium browser binary (auto-installed once via
  ``python -m playwright install chromium`` on first suite setup if missing)

A clean environment that lacks Playwright must **fail** this module, not skip.
"""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import sys
import threading

from django.test import SimpleTestCase

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover - fail closed
    raise ImportError(
        "Phase 2 CRM-dupe drop tests require Playwright. Install developer "
        "deps with: python -m pip install -r requirements-dev.txt "
        "then: python -m playwright install chromium"
    ) from exc


IMPORTER_ROOT = Path(__file__).resolve().parent
FIXTURE_URL_PATH = "/test_assets/file_drop_phase2/crm_dupe_start.html"
_CHROMIUM_BOOTSTRAP_ATTEMPTED = False


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _bootstrap_chromium() -> None:
    global _CHROMIUM_BOOTSTRAP_ATTEMPTED
    if _CHROMIUM_BOOTSTRAP_ATTEMPTED:
        return
    _CHROMIUM_BOOTSTRAP_ATTEMPTED = True
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "Failed to bootstrap Playwright Chromium. Run manually:\n"
            f"  {sys.executable} -m playwright install chromium\n"
            f"{detail}"
        )


def _launch_chromium(pw):
    try:
        return pw.chromium.launch(headless=True)
    except PlaywrightError as first:
        msg = str(first).lower()
        if "executable doesn't exist" not in msg and "browserType.launch" not in msg:
            if "chromium" not in msg and "playwright" not in msg:
                raise
        _bootstrap_chromium()
        try:
            return pw.chromium.launch(headless=True)
        except PlaywrightError as second:
            raise RuntimeError(
                "Phase 2 drop suite could not launch Chromium after bootstrap. "
                f"Run: {sys.executable} -m playwright install chromium\n"
                f"Original error: {first}\n"
                f"Retry error: {second}"
            ) from second


class CrmDupeDropPopulateBrowserTests(SimpleTestCase):
    """Real-browser CRM-dupe populate-only drop after mode switch."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.port = _free_port()
        handler = partial(SimpleHTTPRequestHandler, directory=str(IMPORTER_ROOT))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.pw = sync_playwright().start()
        try:
            cls.browser = _launch_chromium(cls.pw)
        except Exception:
            cls.pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls.pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()
            super().tearDownClass()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(self.base + FIXTURE_URL_PATH, wait_until="load")
        self.page.wait_for_function(
            "() => document.querySelector('[data-file-drop=\"populate\"]')"
            ".getAttribute('data-file-drop-enhanced') === '1'"
        )

    def tearDown(self):
        self.context.close()

    def test_default_acquire_all_then_upload_mode_drop_populates_without_submit(self):
        before = self.page.evaluate(
            """() => {
              const input = document.querySelector('input[name="population_file"]');
              const zone = document.querySelector('[data-file-drop="populate"]');
              const acquire = document.querySelector(
                'input[name="source_mode"][value="acquire_all"]'
              );
              return {
                acquire_checked: !!(acquire && acquire.checked),
                input_disabled: !!(input && input.disabled),
                enhanced: zone && zone.getAttribute("data-file-drop-enhanced"),
                file_count: input && input.files ? input.files.length : -1,
              };
            }"""
        )
        self.assertTrue(before["acquire_checked"], before)
        self.assertTrue(before["input_disabled"], before)
        self.assertEqual(before["enhanced"], "1", before)
        self.assertEqual(before["file_count"], 0, before)

        self.page.locator(
            'input[name="source_mode"][value="uploaded_population"]'
        ).check()
        self.page.wait_for_function(
            """() => {
              const input = document.querySelector('input[name="population_file"]');
              return input && !input.disabled;
            }"""
        )

        after = self.page.evaluate(
            """() => {
              const zone = document.querySelector('[data-file-drop="populate"]');
              const input = zone.querySelector('input[type="file"]');
              const dt = new DataTransfer();
              dt.items.add(
                new File(["Id,Name\\nA1,One\\n"], "records.csv", {
                  type: "text/csv",
                })
              );
              function fire(type) {
                var ev;
                try {
                  ev = new DragEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    dataTransfer: dt,
                  });
                } catch (err) {
                  ev = new Event(type, { bubbles: true, cancelable: true });
                }
                if (!ev.dataTransfer) {
                  Object.defineProperty(ev, "dataTransfer", { value: dt });
                }
                zone.dispatchEvent(ev);
              }
              fire("dragenter");
              fire("dragover");
              var highlighted = zone.classList.contains("is-dragover");
              fire("drop");
              return {
                highlighted: highlighted,
                filename:
                  input.files && input.files[0] ? input.files[0].name : "",
                submitted: !!window.__crmDupeSubmitted,
                file_count: input.files ? input.files.length : 0,
              };
            }"""
        )
        self.assertTrue(after["highlighted"], after)
        self.assertEqual(after["filename"], "records.csv", after)
        self.assertEqual(after["file_count"], 1, after)
        self.assertFalse(after["submitted"], after)
