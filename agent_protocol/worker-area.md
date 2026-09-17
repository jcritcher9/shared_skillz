# Area Worker Bootstrap

Area-only assignment: an area name and no scoping-document filename.
Classify, resolve one filename from a migrated queue when present, then
propose `/worker <doc> <target>` and **wait**. Do not implement. Do not
auto-start.

Also read now: `agent_protocol/worktree.md` and `agent_protocol/kernel.md`.
Ledger vocabulary is `kernel.md`.

`/worker <area>` is the generic path; area-binder stubs are optional sugar.

## Classify

Fetch origin. Classify from `origin/main` **before** reading `open_work.md`
as a queue and **before** creating a queue worktree. Context root is
`agent_context/` or `codex_context/`. Inspect **either** name:

```bash
area=<area>
git fetch origin
handoff=0
queue=0
for ctx in agent_context codex_context; do
  git cat-file -e "origin/main:$area/$ctx/handoff_doc.md" 2>/dev/null && handoff=1
  git cat-file -e "origin/main:$area/$ctx/open_work.md" 2>/dev/null && queue=1
done
if [ "$handoff" -eq 1 ]; then echo "UNMIGRATED"
elif [ "$queue" -eq 1 ]; then echo "QUEUE"
else echo "NO_QUEUE"
fi
```

The script is the truth. Do not snapshot today's areas.

| Classify | Then |
|---|---|
| **UNMIGRATED** | `handoff_doc.md` exists. Defect. Do not parse that area's `open_work.md` as Current/Queued. Stop; require a named scoping document. |
| **QUEUE** | Enter the area **queue worktree**. Read `open_work.md` **there**, never from `main`'s dirty checkout and never from a document branch. Current if present, else first Queued. Both empty → stop. That path is the filename. Do not claim yet. A `QUEUE` parse of a file lacking `## Current` / `## Queued` is a defect. |
| **NO_QUEUE** | Valid. Report no queued document. Do not create `open_work.md` or a queue worktree. |

Do not scrape diary prose. Do not auto-start.

## Area queue worktree — authoritative `open_work.md`

The queue is area-level. Authority: `origin/agent/<area-slug>-queue` at
`<parent-of-repo>/worktrees/<repo-name>/<area-slug>-queue`. `<area-slug>`
is the repository-relative path of the directory that contains the context
root, with `/` and `_` replaced by `-`, plus `-queue`
(`metadata_deployer_project` → `metadata-deployer-project-queue`).

Create-or-attach with the document-worktree block (`worktree.md`), **only
after classify returned QUEUE**. Base a new branch on `origin/main`. Do not
create or enter this worktree on UNMIGRATED or NO_QUEUE.

**Read** the queue only from this worktree after `git fetch origin` and
fast-forwarding onto `origin/main` when the queue branch has no unique
production commits. If the queue branch and `origin/main` have diverged on
anything except `open_work.md` / `strategy_index.md`, stop.

Never take Current/Queued from the user's main checkout or a document
worktree.

**Claim** (Queued → Current) only after the user confirms, and only in the
queue worktree: (1) set Current to the confirmed path and remove it from
Queued; (2) commit, touching only `open_work.md` (and `strategy_index.md`
if Active/Historical membership also changed); (3) `git push origin
"agent/<area-slug>-queue"`; (4) fast-forward that commit onto
`origin/main`. If fast-forward is not possible, stop — do not rebase
document history; rebase **only** the queue-only commit onto `origin/main`
and retry. If Current is already the selected document, do not edit the
queue.

Queue claims are the exception to "never merge ungraded implementation to
`main`": queue-only commits on `agent/<area-slug>-queue`, fast-forwarded
onto `origin/main`.

### Migration boundary

Queue-worktree-only ownership of `<area>/<context-root>/open_work.md` and
`strategy_index.md` begins after that area's migration has landed on
`main`. Test: `handoff_doc.md` in either context root on `origin/main`.

- **Still exists** — unmigrated. The authorized migration commit (R1, R2,
  R4, or R6) **must** create, replace, or delete those files in the same
  commit that cuts the handoff. Do **not** stop because those files
  changed. R6 deletes `web/`'s `open_work.md` with no `strategy_index.md`.
- **Gone** — migrated. Document branches must not modify those files. If
  `git diff` against the merge-base with `origin/main` shows them changed,
  stop.

```bash
area=<area>
unmigrated=0
for ctx in agent_context codex_context; do
  git cat-file -e "origin/main:$area/$ctx/handoff_doc.md" 2>/dev/null && unmigrated=1
done
if [ "$unmigrated" -eq 1 ]; then
  echo "UNMIGRATED: queue/index edits are allowed on the migration commit"
else
  git diff --name-only "$(git merge-base origin/main HEAD)" -- \
    "$area/agent_context/open_work.md" \
    "$area/agent_context/strategy_index.md" \
    "$area/codex_context/open_work.md" \
    "$area/codex_context/strategy_index.md"
fi
```

An R1/R2/R4 parent still has `handoff_doc.md` on `origin/main`, so this
check must print `UNMIGRATED` and must not treat the required queue/index
rewrite as a stop. After that phase lands, the same check on a later
document branch must print nothing.

If Current names a document already in flight, a second area-worker
proposes the **next schedulable target of that same document** (and stops
on an `awaiting human grading` gap). It does not skip to Queued. Duplicate
starts of a second document while Current is set are forbidden.

## Resolve the document, then propose and wait

After QUEUE yields a filename (or Current already names one), derive the
**document** worktree and enter it (`worktree.md`). Enter before reading
any other part of the document from any other checkout.

Read strategy, current-state, `testing.md`, and the slice ledger **inside
the document worktree**. Require `**Current state:**` (path, paths, or
`none`). Missing both → propose the pointer and **stop**. Do not implement.

Walk commit-table order, skipping `SUPERSEDED`. Vocabulary: `kernel.md`.
Ordered first-match; first matching row wins.

| When | Then |
|---|---|
| Any `not started` or `awaiting human grading` slice **before** the newest `done` | Ledger gap. Stop. Do not propose. |
| First slice after the newest `done` (or the first slice) is `checkpointed` or `implemented` awaiting human grading | Propose `/critic` for **that slice**. Name the sha. Do not implement. |
| That slice is `accepted` awaiting publication | Stop. Not authority to build or mark `done`. |
| That slice is `done` not on local `main` | Propose `/accept-and-land` and wait. Do not implement. |
| That slice is `not started` and its pass-table grain is `same pass` | Propose `/worker <doc> <pass-id>` (that pass, including other listed slice ids that are also `not started`). A same-pass partner `awaiting human grading` → report that gap. |
| That slice is `not started` and grain is `sequential` / `do not combine` | Propose `/worker <doc> <slice-id>` for that **slice** only. Never the parent phase. |
| Nothing left | Propose `/closeout`. If those `done` shas are not yet on local `main`, `/accept-and-land` first, then `/closeout`. |

Emit the proposal and **wait**. Do not run tests, edit production files, or
implement. Do not open the default worker body.

```text
Proposed: /worker <doc> <target>
Status: not started
Implement with: <grain from the pass table>
Ancestor: Slice <id> done (<sha>)  |  none (first schedulable slice)

Reply yes to authorize this target. I will not start until you do.
```

On yes: if the filename came from **Queued**, **claim it on the queue
worktree** now (Current = that path, drop from Queued, commit, push queue
branch, fast-forward onto `origin/main`) **before** any later
implementation turn. If Current was already that path, do not edit the
queue. Then stop — the assignment is `/worker <doc> <target>`, not this
body.

"Yes" authorizes only the proposed target. A different slice, phase, or
pass in the reply is a new assignment; re-walk the table. Do not scrape
diary prose. Do not auto-start.
