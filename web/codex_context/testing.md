# Testing

This file is the area testing pillar. It is not a test inventory. It is
not architecture and not a scoping-document queue. See
`CONTEXT_PILLARS.md` (`testing.md`).

## Interpreter

Never PATH `python` or `py`. Resolve `$py` from the clone `.venv`
(Git ≥ 2.31). Worktree CWD does not change the interpreter:

```text
$common = git rev-parse --path-format=absolute --git-common-dir
$clone  = parent of $common
Windows: $py = $clone/.venv/Scripts/python.exe
Unix:    $py = $clone/.venv/bin/python
```

If `$py` is missing, stop as an environment failure: do not treat it as
a red suite. Do not activate the venv. Do not record the resolved
absolute path.

## Default selection

Write the smallest tests that prove the assigned change.
Run the smallest command that can disprove it.

Targeted (implementer and first critic):
1. new tests for this diff
2. exact tests for prior grader findings
3. existing tests for invariants this diff actually shares

The implementer may run that targeted selection. Red results are
remediation, not a grade.

Do not run this area's comprehensive suite during implementer/critic
ping-pong. Parent comparison uses only the failing selector.

Typical targeted commands:

```text
<clone>/.venv/Scripts/python.exe web/manage.py test importer.tests
<clone>/.venv/Scripts/python.exe web/manage.py test importer.test_<module>
```

Unix: `<clone>/.venv/bin/python web/manage.py test …`. After resolving
`$py`, `$py web/manage.py test importer.test_<module>` is the same
invocation.

## Comprehensive

Reserved for a later Shuttle critic lane after a landable targeted
Grade A/A- ACCEPT with empty finding arrays, for CI, or when the
governing strategy names this envelope.

```text
<clone>/.venv/Scripts/python.exe web/manage.py test importer
<clone>/.venv/Scripts/python.exe web/manage.py check
```

Unix: `<clone>/.venv/bin/python` in place of
`<clone>/.venv/Scripts/python.exe`.
