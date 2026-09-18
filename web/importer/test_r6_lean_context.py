"""R6 Django-suite entry. Canonical tests live in web/tests/."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_STANDALONE = (
    Path(__file__).resolve().parents[1] / "tests" / "test_r6_lean_context.py"
)
_spec = importlib.util.spec_from_file_location(
    "web_tests_test_r6_lean_context", _STANDALONE
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
R6LeanContextTest = _mod.R6LeanContextTest
