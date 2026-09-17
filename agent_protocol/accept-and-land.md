# Accept and Land

Record critic-ACCEPTED slices as `done` (accepted and published locally) and
land those commits onto local
`main`. Keep the document worktree and `agent/<doc-slug>`. Do not retire
anything. Do not clear queue Current. Push to the remote only when the user
asks.

This is not closeout. `/closeout` lands a **finished** document and removes
the worktree. This skill publishes accepted slices while later slices may
still be `not started`.

`accepted (<sha>) awaiting publication` is a distinct lifecycle state. It does
not mean `done`, does not prove local-main publication, and is not consumed by
this skill without the explicit trusted publication/revalidation authority that
created it.

The implementing worker does not run these steps. The critic does not write
`done`. Follow this file when the user runs `/accept-and-land`, or asks to
land accepted slices. A worker that receives Grade A / A− ACCEPT proposes
this skill and waits; it does not run these steps unless the user asked
to land (`/worker`).

## Preconditions — all must hold

Enter the document worktree. The adapter names the runtime enter mapping.
This role enters an existing tree; it does not create-or-attach. Derive the
path from the filename stem, punctuation unchanged
(`<parent-of-repo>/worktrees/<repo-name>/<doc-slug>`), unless a
`**Branch:**` header exists — then the slug is that header minus `agent/`.

```bash
git rev-parse --abbrev-ref HEAD    # agent/<doc-slug>
git status --porcelain             # empty
```

- Branch matches the document `**Branch:**` header.
- Tree is clean. No worker is mid-target.
- Acceptance evidence is in this turn: a critic report with per-slice
  **ACCEPT** (Grade A or A−), or the user naming the slice ids and stating
  they are accepted. An aggregate Grade line is not enough. A worker
  handoff is not acceptance.
- Record `done` only on slices the critic graded ACCEPT whose SHA is not
  shared with a NOT ACCEPTED slice. Worker and critic do not write `done`.
  Grade B or below, or NOT ACCEPTED, is a stop — do not write `done`, do
  not land. Same-SHA split outcomes are a stop.

If any fails, stop and report.

Already `done` and those shas are ancestors of local `main`: report that
they are already on `main`. Do not empty-commit. If later slices remain,
say so; if every slice is `done` or `SUPERSEDED`, propose `/closeout`.

## 1. Record `done`

For each accepted slice still `awaiting human grading`, set Status to
`done (<sha>)` using that slice's **implementation** sha (critic `I`), not
a later docs pin. Edit the same ledger cells `/worker` would
(commit-table Status and the slice section or Slices-table cell). Do not mark NOT ACCEPTED slices. Do not collapse a
parent phase Status over mixed slices. Do not set document-level Status to
done.

Commit on `agent/<doc-slug>`:

```text
Record <doc-slug> <slice ids> Grade A ACCEPT.
```

Use `A-` in the subject when that was the grade. Porcelain must be empty
after the commit.

Slices already `done (<sha>)` at that same sha: leave them.

This skill does not change functionality. Worker rewrites
`current_state.md` in **I**; critic treats a stale file after a behavior
change in I as a finding. Do not edit `current_state.md`, product code,
or tests in the **done** commit.

After the `done` commit, prove this turn's done commit is ledger-only:

```bash
git diff --name-only HEAD~
git diff HEAD~
```

`git diff --name-only HEAD~` must list only the scoping document.
`git diff HEAD~` must be Status cells (`awaiting human grading` →
`done (<sha>)`). Any other path — including `current_state.md` — or any
non-Status hunk is a stop. Do not land.

If this turn made no `done` commit (already recorded at that sha), skip
the diff; still do not edit those files.

The Path B remap commit in §3 is a different commit. It may edit files
named on `**Current state:**` to replace rebased SHAs. That carve-out
does not apply to the `done` commit.

## 2. Tip may land

Every commit that would reach `main` (not already an ancestor of local
`main`) must belong to critic-ACCEPTED slices: implementation, remediations,
docs / pointer pins, and the `done` commit just made.

```text
main ── accepted ancestor ── ungraded descendant (HEAD)
        may land             blocks land; no cherry-pick
```

| Ancestry | Action |
|---|---|
| Ungraded descendant of an accepted ancestor is on `HEAD`, not `main` | **stop** |
| Cherry-pick the accepted ancestor onto `main` | **no** |

Ungraded means `awaiting human grading` or `not started`. Git cannot
land the accepted ancestor without that descendant; new cherry-pick
SHAs would invalidate the ledger. Remaining work is remediation or a
later target, not a partial land.

```bash
git merge-base --is-ancestor <accepted-sha> HEAD   # must exit 0
```

## 3. Land onto local `main`

Path A then Path B. This file is the operating Path B; `/closeout` follows
it.

Identify the checkout whose branch is `main` from `git worktree list`. Land
**there**. Do **not** `git switch main` inside the document worktree — this
skill must leave that worktree on `agent/<doc-slug>`.

The main checkout must be on `main` and clean. If it is on another branch
or dirty, stop.

**Path A — fast-forward (try first):**

```bash
git merge --ff-only "agent/<doc-slug>"
```

**Path B — rebase, prove, remap, fast-forward** (when Path A says not
possible to fast-forward). Run the rebase in the **document worktree**,
still on `agent/<doc-slug>`. Do **not** merge `main` into the agent
branch to make Path A possible.

```bash
OLD=$(git rev-parse HEAD)
git rebase main
git range-diff main.."$OLD" main..HEAD
```

Every replayed commit must show `=` (equivalent). If `range-diff` is
unavailable, pair `git show <c> | git patch-id --stable` for each commit
in `main..$OLD` with `main..HEAD` in order; every pair must match. The
critic's patch-id for **I** must match the post-rebase **I**.

- **Conflict** that is not trivially mechanical: `git rebase --abort`,
  stop, re-grade. Do not land.
- **Not equivalent** (`range-diff` not `=`, or patch-ids differ): stop.
  Do not land.
- **Tests:** re-run the critic's test selection on `HEAD` (the tree that
  will land). Failure is a stop. If the critic recorded no automated
  tests, say so and continue.

Then remap, in **one** commit, every SHA that the rebase rewrote from the
pre-rebase value to the paired post-rebase SHA.

| Location | Remap? |
|---|---|
| Commit-table Status/Commit cells | yes |
| Slice `**Status:**` / `**Commit:**` lines | yes |
| Backtick SHAs in files named on `**Current state:**` (`none` → no file) | yes |
| Scoping-document prose | no |
| `**Base commit:**` | no |

`done (<sha>)` cells must name the post-rebase commits. Pointer pin **P** is
remapped if it was in the range.

Prove that remap commit:

```bash
git diff --name-only HEAD~
git diff HEAD~
```

`git diff --name-only HEAD~` must be the scoping document and/or the
named `current_state.md` path(s). `git diff HEAD~` must show only SHA
replacements from the rebase pairing (old → paired new). Product code,
tests, any other path, or any non-SHA hunk is a stop. Do not land.

After remap, no SHA remaining in those `current_state.md` files may be
one that was in `main..$OLD` and not remapped. Stop if any is.

Force-push **only** `agent/<doc-slug>` if that branch was already pushed
(`git push --force-with-lease`). Do not force-push `main`.

On the **main** checkout:

```bash
git merge --ff-only "agent/<doc-slug>"
```

After this document's land is on `main`, from the **document worktree**:

```bash
git merge --ff-only main
```

Path A is already up to date. Path B's fast-forward has the agent tip as
an ancestor of `main`, so this is a fast-forward (no new commit). Stop if
it is not. Other document branches are not caught up here. Do **not**
`git merge --no-ff`.

## 4. Verify

Every sha now recorded `done` in this document must be an ancestor of
`main`. Print nothing:

```bash
git merge-base --is-ancestor <done-sha> main
```

Do not grep every backtick sha in the scoping document for this skill —
only the slices just accepted (and any earlier `done` this land also
published). After Path B, also: every backtick SHA in files named on
`**Current state:**` must be an ancestor of `main`. Closeout still
checks the whole ledger when the document finishes.

Leave the document worktree on `agent/<doc-slug>`, clean.

## 5. Push is optional

Default: local `main` only. Do not `git push`.

If the user asked to push:

```bash
git fetch origin
git merge --ff-only origin/main    # on the main checkout, before or as needed so the push is a fast-forward
git push origin main
```

Do **not** delete `origin/agent/<doc-slug>`. Do **not** `git worktree remove`.
If `origin/main` has moved and the land cannot fast-forward to the remote,
stop and report — do not rebase.

## 6. Do not retire

Do not remove the worktree. Do not delete `agent/<doc-slug>`. Do not edit
`open_work.md`. Queue Current stays claimed until closeout.

If every slice is now `done` or `SUPERSEDED`, propose `/closeout` and wait.
Otherwise the next worker continues in this worktree.

## Report

```text
Document:
Branch:
Worktree path:
Accepted slices: <id Grade A|A- sha>
Done commit:
Functionality: unchanged (ledger-only done commit | no done commit this turn)
Land path: A (ff) | B (rebase-ff)
Main tip:
Pushed: no | origin/main <sha>
Worktree: kept on agent/<doc-slug>
Next: propose /closeout | next schedulable target <id>
```
