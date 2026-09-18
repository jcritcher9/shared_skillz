"""R6: web keeps current_state.md only; handoff and backlog are gone."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
CODEX = WEB / "codex_context"
THIN = CODEX / "thin_strategy.md"
STATE = CODEX / "current_state.md"
ARCHIVE_README = (
    CODEX / "archived" / "context_pillars_pre_cleanup_2026-08-19" / "README.md"
)

DELETED = (
    "web/codex_context/handoff_doc.md",
    "web/codex_context/open_work.md",
)

# History, superseded plans, and R5 leftover instruction surfaces may still
# name the retired paths. Active readers must not.
_HISTORY_PREFIXES = (
    "web/codex_context/archived/",
    "web/tests/test_r6_lean_context.py",
    "web/importer/test_r6_lean_context.py",
    "mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/",
    "project_implementations/context_pillars_cleanup_",
    "project_implementations/completed_projects/",
    "CONTEXT_PILLARS.md",
    "how_to_update_handoff_doc.md",
)


def _git_ls_files(*paths: str) -> list[str]:
    listed = subprocess.check_output(
        ["git", "ls-files", *paths],
        cwd=REPO,
        text=True,
        encoding="utf-8",
    )
    return [line.replace("\\", "/") for line in listed.splitlines() if line.strip()]


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


class R6LeanContextTest(unittest.TestCase):
    def test_no_tracked_handoff_or_open_work_under_web(self) -> None:
        listed = _git_ls_files("web")
        leftovers = [
            path
            for path in listed
            if _basename(path) in {"handoff_doc.md", "open_work.md", "strategy_index.md"}
        ]
        self.assertEqual(leftovers, [])
        self.assertFalse((CODEX / "handoff_doc.md").exists())
        self.assertFalse((CODEX / "open_work.md").exists())
        self.assertFalse((CODEX / "strategy_index.md").exists())

    def test_codex_context_living_files_are_state_thin_and_archive(self) -> None:
        listed = [
            path
            for path in _git_ls_files("web/codex_context")
            if not path.startswith("web/codex_context/archived/")
        ]
        self.assertEqual(
            listed,
            [
                "web/codex_context/current_state.md",
                "web/codex_context/thin_strategy.md",
            ],
        )
        on_disk = sorted(p.name for p in CODEX.iterdir())
        self.assertEqual(on_disk, ["archived", "current_state.md", "thin_strategy.md"])

    def test_thin_strategy_routes_to_current_state_alone(self) -> None:
        text = THIN.read_text(encoding="utf-8")
        self.assertIn("current_state.md", text)
        self.assertNotIn("handoff_doc.md", text)
        self.assertNotIn("open_work.md", text)
        self.assertNotIn("strategy_index.md", text)
        numbered = [
            line.strip()
            for line in text.splitlines()
            if re.match(r"^\d+\.", line.strip())
        ]
        self.assertEqual(len(numbered), 1)
        self.assertIn("current_state.md", numbered[0])

    def test_current_state_opens_as_present_tense_behavior(self) -> None:
        text = STATE.read_text(encoding="utf-8")
        first_lines = "\n".join(text.splitlines()[:8])
        self.assertIn("# EasyImports Web — Current State", first_lines)
        self.assertIn("server-rendered Django consumer", first_lines)
        self.assertNotIn("Grade A", first_lines)
        self.assertNotIn("ACCEPT `", first_lines)
        self.assertNotIn("handoff_doc.md", text)
        self.assertNotIn("open_work.md", text)
        self.assertNotIn("## Closeout status", text)
        self.assertNotIn("## Next recommended", text)

    def test_archive_readme_does_not_route_to_deleted_living_files(self) -> None:
        text = ARCHIVE_README.read_text(encoding="utf-8")
        self.assertIn("../../current_state.md", text)
        self.assertNotIn("../../handoff_doc.md", text)
        self.assertNotIn("../../open_work.md", text)

    def test_no_active_reader_of_deleted_web_context_files(self) -> None:
        listed = _git_ls_files()
        hits: list[str] = []
        for relative in listed:
            if relative.endswith((".png", ".jpg", ".zip", ".pyc")):
                continue
            if any(
                relative == prefix or relative.startswith(prefix)
                for prefix in _HISTORY_PREFIXES
            ):
                continue
            try:
                body = (REPO / relative).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(name in body for name in DELETED):
                hits.append(relative)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
