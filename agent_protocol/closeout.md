# Close Out a Scoping Document

Workers push to `agent/<doc-slug>` and do not merge ungraded implementation
to `main`. Accepted slices may already be on `main` from
`agent_protocol/accept-and-land.md`; that skill keeps the worktree. This
skill lands a **finished** document and retires the branch and worktree.
Prefer a fast-forward, which keeps `main` linear; if `main` has moved, land
with Path B in `agent_protocol/accept-and-land.md` (rebase, prove
equivalence, remap ledger SHAs, fast-forward). Do not `git merge --no-ff`.

## Preconditions — all must hold

Run from inside the document's worktree:

```bash
git rev-parse --abbrev-ref HEAD    # agent/<doc-slug>
git status --porcelain             # empty
git fetch origin
```

- Every **slice** in the document is `done (<sha>)`, or explicitly
  `SUPERSEDED`. Read the slice ledger: per-slice Status cells, not a
  collapsed parent-phase Status. A slice still `awaiting human grading` or
  `not started` means this document is not finished — stop and report which.
  A parent `### Phase 4` marked `done` while 4C is `not started` is not
  finished.
- `accepted (<sha>) awaiting publication` is not closeable. Publish it through
  its explicit trusted publication path before closeout.
- The tree is clean and the branch is pushed.
- No worker is mid-target on this branch.

If any fails, stop. Do not land partial work.

## Path A — fast-forward (preferred)

If `main` has not moved since the branch diverged, the branch lands with no
rewriting. Shas are preserved, so the ledger stays valid and nothing else is
needed:

```bash
git switch main
git merge --ff-only "agent/<doc-slug>"
```

Try this first, always — it is the default, and when it works `main` stays
linear. If it fails with "not possible to fast-forward", use Path B.

## Path B — rebase then fast-forward

`main` has moved, so the branch cannot fast-forward. Follow Path B in
`agent_protocol/accept-and-land.md` §3 (rebase onto `main`, `range-diff`
`=` or matching patch-ids, re-run the critic's tests, remap ledger SHAs
in one commit, `git merge --ff-only`). Do not `git merge --no-ff`.

Closeout may `git switch main` after that land because it then removes
the worktree. The ledger after Path B names the post-rebase SHAs;
ancestry checks below read those.

## Verify before pushing

Both paths end here. All three must pass:

```bash
# 1. Every graded commit is on main, in order
git log --oneline origin/main..main

# 2. Every SHA in commit-table Status/Commit cells and slice
#    **Status:** / **Commit:** lines is an ancestor of main
#    must be checked. Also check every
#    backtick SHA in files named on **Current state:** (`none` →
#    skip). Do not scrape scoping-document prose or **Base commit:**.
#    For each such sha:
#      git merge-base --is-ancestor "$sha" main
#    Print ORPHANED on failure.

# 3. Every resolved slice Status is done or SUPERSEDED.
#    The helper is the check. Do **not** grep
#    '^\*\*Status:\*\*' — that hits document-level and collapsed
#    parent-phase lines and misses table cells.
python agent_protocol/closeout_ledger.py "<scoping-doc>"
```

Command 2 must print no `ORPHANED` lines. After Path B the ledger names
post-rebase SHAs, which must be ancestors of `main`. An orphan is a
failed remap or a rebase of the branch behind this skill — stop. Do not
push until it is clean. Do not require ancestry for historical SHAs that
appear only in prose.

Command 3 must print nothing and exit 0. Stop on any `NOT CLOSED`,
`LEDGER DEFECT`, or `NO SLICE STATUSES` line. Do **not** fall back to
`grep '^\*\*Status:\*\*'`. The helper is the check; do not restate its
resolution order here. Ignore document-level `**Status:**` (`adopted`, …).

Rejection fixtures for command 3 live in
`shuttle/tests/test_closeout_ledger.py` (Status-column mixed; legacy
all-done; legacy one awaiting; collapsed parent). Those tests are the
spec. Do not keep a second markdown copy.

## Push and retire

```bash
git push origin main
git push origin --delete "agent/<doc-slug>"     # if it was pushed
```

Then remove the worktree, from the main checkout, not from inside it:

```bash
git worktree remove "<worktree path>"
git branch -d "agent/<doc-slug>"                # -d, never -D
```

`git branch -d` refuses if the branch is not fully merged. If it refuses, the
land did not complete — investigate rather than forcing.

If this document was **Current** in the area's `open_work.md` and every slice
is now `done` or `SUPERSEDED`, clear Current on the **queue worktree** only
**after** the land is on `origin/main`. The graph is: queue claim → document
commits → land onto `main` → queue clear as a descendant of that land. Do
not commit the clear on the pre-land queue tip; that tip is behind the land
and cannot fast-forward.

From the queue worktree, after `git push origin main` of the land:

```bash
git fetch origin
git merge --ff-only origin/main    # queue branch must now sit on the land
# Re-read open_work.md. Current must still name this document; if it does
# not, stop and report — do not clear someone else's claim.
# Then clear Current only. Do not auto-promote Queued into Current.
git add <area>/<context-root>/open_work.md   # agent_context or codex_context; CONTEXT_PILLARS.md
git commit -m "Clear Current after landing <doc-slug>"
git push origin "agent/<area-slug>-queue"
git push origin HEAD:main          # ff-only descendant of the land
```

If `merge --ff-only origin/main` fails, stop. If Current is not this
document, stop. Do not edit the queue on the document branch before land.

## Report

State: which path was taken, the commits landed, the three verification results,
and that the worktree and branch are gone.
