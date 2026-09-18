"""MAP-R4 interactive accessibility proof (Playwright / real Chromium).

Exercises progressive-enhancement contracts that requests.Session cannot:
dialog open/focus, Escape restore, listbox arrow keys, alias search,
fallback-select tab exclusion, and focus restore after Apply under filters.

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
except ImportError as exc:  # pragma: no cover - fail closed for Grade A
    raise ImportError(
        "MAP-R4 interactive a11y tests require Playwright. Install developer "
        "deps with: python -m pip install -r requirements-dev.txt "
        "then: python -m playwright install chromium"
    ) from exc


IMPORTER_ROOT = Path(__file__).resolve().parent
FIXTURE_URL_PATH = "/test_assets/map_r4_a11y/review.html"
_CHROMIUM_BOOTSTRAP_ATTEMPTED = False


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _bootstrap_chromium() -> None:
    """Install Playwright Chromium once if the browser binary is missing."""

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
    """Launch headless Chromium; bootstrap browser binaries if needed."""

    try:
        return pw.chromium.launch(headless=True)
    except PlaywrightError as first:
        msg = str(first).lower()
        if "executable doesn't exist" not in msg and "browserType.launch" not in msg:
            # Unknown launch failure — do not mask with a bootstrap attempt.
            if "chromium" not in msg and "playwright" not in msg:
                raise
        _bootstrap_chromium()
        try:
            return pw.chromium.launch(headless=True)
        except PlaywrightError as second:
            raise RuntimeError(
                "MAP-R4 a11y suite could not launch Chromium after bootstrap. "
                f"Run: {sys.executable} -m playwright install chromium\n"
                f"Original error: {first}\n"
                f"Retry error: {second}"
            ) from second


class MapR4InteractiveA11yTests(SimpleTestCase):
    """Real-browser MAP-R4 progressive-enhancement acceptance (mandatory)."""

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
        # Enhancement marks the root after script runs.
        self.page.wait_for_function(
            "() => document.getElementById('mapping-review')"
            ".classList.contains('map-r4-enhanced')"
        )

    def tearDown(self):
        self.context.close()

    def test_enhanced_selects_are_not_tab_stops(self):
        tabs = self.page.eval_on_selector_all(
            "select.mapping-choice-select",
            "els => els.map(el => el.getAttribute('tabindex'))",
        )
        self.assertTrue(tabs, "expected fallback selects")
        self.assertTrue(all(t == "-1" for t in tabs), tabs)
        # Tab from first Change should not land on a clipped select.
        first_change = self.page.locator('[data-open-picker][data-ordinal="0"]')
        first_change.focus()
        self.page.keyboard.press("Tab")
        active = self.page.evaluate(
            "() => document.activeElement && document.activeElement.tagName"
        )
        self.assertNotEqual(active, "SELECT")

    def test_picker_escape_restores_focus_to_change(self):
        change = self.page.locator('[data-open-picker][data-ordinal="2"]')
        change.click()
        dialog = self.page.locator("#mapping-field-picker")
        self.assertTrue(dialog.evaluate("el => el.open"))
        # Search field should receive initial focus when mapping fields.
        self.page.wait_for_function(
            "() => document.activeElement"
            " && document.activeElement.id === 'mapping-picker-search'"
        )
        self.page.keyboard.press("Escape")
        self.page.wait_for_function(
            "() => !document.getElementById('mapping-field-picker').open"
        )
        active_id = self.page.evaluate(
            "() => document.activeElement"
            " && document.activeElement.getAttribute('data-ordinal')"
        )
        self.assertEqual(active_id, "2")

    def test_listbox_arrow_keys_and_alias_search(self):
        self.page.locator('[data-open-picker][data-ordinal="2"]').click()
        search = self.page.locator("#mapping-picker-search")
        search.fill("homepage")
        # Website is the only non-ignore match for homepage.
        options = self.page.locator("#mapping-picker-list .mapping-picker-option")
        self.assertEqual(options.count(), 1)
        self.assertIn("Website", options.first.inner_text())
        search.press("ArrowDown")
        active = self.page.locator(
            "#mapping-picker-list .mapping-picker-option.is-active"
        )
        self.assertEqual(active.count(), 1)
        self.assertEqual(
            active.first.get_attribute("data-choice-id"),
            "catalog:account:website",
        )
        # Enter selects active option (re-render keeps selection).
        self.page.keyboard.press("Enter")
        self.assertEqual(
            self.page.locator(
                '#mapping-picker-list .mapping-picker-option[aria-selected="true"]'
            ).count(),
            1,
        )

    def test_apply_restores_focus_when_filter_would_hide_row(self):
        # Filter to Needs review so applying a map would hide the row.
        self.page.locator('.mapping-filters [data-filter="needs_review"]').click()
        row2 = self.page.locator("#mapping-row-2")
        self.assertFalse(row2.is_hidden())
        change = self.page.locator('[data-open-picker][data-ordinal="2"]')
        change.click()
        # Pick Ignore via mode radio then Apply.
        self.page.locator('input[name="picker_mode"][value="ignore"]').check()
        self.page.locator("#mapping-picker-apply").click()
        self.page.wait_for_function(
            "() => !document.getElementById('mapping-field-picker').open"
        )
        # Filter should have switched to All so the row stays reachable.
        all_pressed = self.page.locator(
            '.mapping-filters [data-filter="all"][aria-pressed="true"]'
        )
        self.assertEqual(all_pressed.count(), 1)
        self.assertFalse(row2.is_hidden())
        active_ordinal = self.page.evaluate(
            "() => document.activeElement"
            " && document.activeElement.getAttribute('data-ordinal')"
        )
        self.assertEqual(active_ordinal, "2")
        # Staged select value is Ignore.
        self.assertEqual(
            self.page.locator("#choice_2").input_value(),
            "system:ignore",
        )

    def test_dialog_tab_stays_inside_picker(self):
        self.page.locator('[data-open-picker][data-ordinal="0"]').click()
        dialog = self.page.locator("#mapping-field-picker")
        self.assertTrue(dialog.evaluate("el => el.open"))
        # Tab many times; active element must remain inside the dialog.
        for _ in range(12):
            self.page.keyboard.press("Tab")
            inside = self.page.evaluate(
                """() => {
                  const d = document.getElementById('mapping-field-picker');
                  return d && d.contains(document.activeElement);
                }"""
            )
            self.assertTrue(inside, "Tab escaped the field picker dialog")
