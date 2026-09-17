# Shared worker/critic kernel

Shared definitions and evidence rules. Role files own dispatch, implementation, and grading consequences.
A **target** is a normalized slice set: a slice, a phase's remaining allowed slices, or a pass-table row.
The document's canonical implementation checkout is reused across targets.

## Slice ledger

| Status value | Meaning |
|---|---|
| `done (<sha>)` | Accepted and published to local `main`. |
| `accepted (<sha>) awaiting publication` | Critic-accepted on the document branch, not yet published. |
| `checkpointed at <sha> awaiting human grading` | Trusted Python created a validated implementation checkpoint; not accepted. |
| `implemented at <sha> … awaiting human grading` | Externally submitted implementation; not accepted. |
| `SUPERSEDED <date> — must not be implemented` | Withdrawn. |
| `not started` | No implementation exists. |

Walk commit-table order, skipping `SUPERSEDED`. The predecessor is the newest earlier `done` SHA.
An earlier unfinished slice is a gap unless reordering is explicitly authorized; later unassigned
siblings may remain unstarted. Unfinished slices before the newest `done` are also gaps.
Role dispatch determines whether a verdict authorizes remediation or landing; status alone does not.

## Context pointers

Before proposing, implementing, or grading, read the scoping document's `**Current state:**`:
repository-relative path(s), or `none` with a reason. Open named files at the role's working revision;
`none` means no current-state read and must be reported. A missing pointer requires repair before proceeding.
Read `**Testing:**` when present; otherwise use each named current-state file's sibling `testing.md`.
Existing documents without `**Testing:**` are grandfathered; new headers include it.

Current-state files describe present-tense implemented behavior; slice history, grades, SHAs, and
landing logs belong in the scoping document. `testing.md` describes selection policy, not slice history.

## Implementation and documentation identity

**I** is the tested implementation: `Remediation commit` when it names a SHA, otherwise `Original implementation`.
`Implementation commit` names I. **P** is its separate documentation pin; `Documentation pin` and
`Current ledger pin` name the same P. Tests run at exact I, never P. A clean HEAD at a proven pin
is not an implementation-identity discrepancy. Classify pins by their diff, not the `Pin type` label:

- **Ledger pin:** records the worker's Status/Commit entries for I. Shuttle may
  additionally record verified first-worker Branch/Base additions and the
  bounded top-level start-status transition described in `critic.md`.
- **Current-state pointer pin:** the complete I-to-P diff changes only the scoping document's
  `**Current state:**` header line, and I is an ancestor of P. New pointer pins are committed directly
  on I. The pointer may be read from P while the named files and tests remain at I.

Acceptance is atomic by commit SHA: slices sharing a SHA must share an outcome; different-SHA
siblings may split outcomes. Previously accepted shared SHAs remain valid.

## Interpreter

Never invoke PATH `python` or `py` first. Resolve `$py` once before testing (Git >= 2.31):

```text
$common = git rev-parse --path-format=absolute --git-common-dir
$clone  = parent of $common
Windows: $py = $clone/.venv/Scripts/python.exe
Unix:    $py = $clone/.venv/bin/python
```

The worktree CWD does not change the interpreter. Missing `$py` is an environment stop, not a red
suite or product finding. Report the clone `.venv`, never its absolute path. Do not activate it
or create a per-worktree environment.

## Targeted lane

Follow the strategy and `testing.md`; otherwise select:

1. New tests for the diff.
2. Regressions for prior grader findings.
3. Existing tests for invariants the diff actually shares.

Run prior-finding regressions first, then deselect those exact node ids from the other selections
to avoid duplicate execution. Node ids are `path::test_name` or `path::Class::test_name`.
The comprehensive suite is not the default during worker/critic ping-pong; broader runs need the
strategy, testing policy, or explicit user/CI/controller instruction. Passing tests is evidence, not acceptance.
