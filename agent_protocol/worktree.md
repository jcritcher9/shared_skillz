# Worktree isolation

One scoping document has one canonical implementation worktree and branch, reused across targets.
Agents sharing that checkout run sequentially; its critic enters afterward with a clean-tree gate.
An explicitly independent parallel pass requires separate worktrees and branches supplied by the
orchestrator, which owns integration. This create-or-attach flow never invents parallel branches.
A separate branch alone does not isolate a shared HEAD, index, or working directory.

## Derive identity and location

Use this tool-neutral root, outside the repository and any runtime-specific directory:

```text
<parent-of-clone>/worktrees/<clone-name>/
```

Resolve the clone from the parent of `git rev-parse --path-format=absolute --git-common-dir`
(Git >= 2.31, ordinary non-bare clone). This stays stable when invoked inside a linked worktree.
Derive the destination; do not search for a substitute path.

Derive `<doc-slug>` from TARGET's `**Branch:**` minus `agent/`, otherwise its filename stem.
Before entering, read only that identity line from `origin/main` (fallback: current checkout).
Preserve punctuation and existing hyphenation; `_` to `-` normalization applies only to area queues.
Never append a phase or slice id to a document's worktree path or branch.

| Class | Directory / branch suffix | When allowed |
|---|---|---|
| Implementation | `<doc-slug>` / `agent/<doc-slug>` | `/worktree`; reuse across implementation targets. |
| Queue | `<area-slug>-queue` / `agent/<area-slug>-queue` | Only after area classification from `origin/main` returns QUEUE; enter before its document tree. Never for UNMIGRATED or NO_QUEUE. |
| Authoring | `<doc-slug>-authoring` / `agent/<doc-slug>-authoring` | Only through `/shuttle-new-scoping-doc` or `/shuttle-edit-scoping-doc`. |

Authoring uses TARGET's implementation identity to derive its slug. Never write the authoring
branch into TARGET's `**Branch:**`, or author/grade TARGET in the implementation tree.
Follow the invoking authoring skill for acceptance and publication; `/worktree` does not create authoring trees.
Migrated document branches must not edit area queues or indexes; the area's migration commit is exempt.

## Create or attach

Use plain `git worktree add`, never a runtime's built-in creator. Enter via the adapter's
working-directory operation; being at the correct path already counts as entered.
For the requested class, derive `<path>` and `<branch>` above, then apply this order:

1. **Destination exists:** verify that exact path is registered to this repository in
   `git worktree list --porcelain` and has the expected branch. Reuse it; otherwise stop.
2. **Destination absent, local branch exists:** check `git show-ref --verify --quiet refs/heads/<branch>`.
   Reattach with `git worktree add <path> <branch>`. Do not create another branch.
3. **Neither exists:** query `git ls-remote --exit-code --heads origin refs/heads/<branch>`.
   Exit 0 means the remote branch exists: fetch and track it using the commands below.
   Exit 2 means no matching remote branch: only then create from the confirmed base.
   Any other exit is a lookup failure: stop; do not treat it as an absent branch.

For an existing remote branch, populate the tracking ref explicitly before attaching:

```bash
git fetch origin refs/heads/<branch>:refs/remotes/origin/<branch>
git worktree add --track -b <branch> <path> refs/remotes/origin/<branch>
```

For a new branch, confirm the parent checkout's intended base (normally local `main`),
record its HEAD SHA, then run `git worktree add -b <branch> <path> <base-sha>`.
Do not silently substitute `origin/main`; uncommitted parent changes do not carry over.
Create the destination's parent directory if needed. Stop on a failed command rather than
falling through to new-branch creation. If Git reports a stale registration for a hand-deleted
worktree, inspect it and prune stale metadata before retrying; never force past an active checkout.

## Verify, enter, and report

Enter before reading the document ledger. Verify path, branch, and HEAD:

```bash
pwd
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

The branch must match the document header: missing header means record the verified implementation
branch; a mismatch means stop. The document records only the branch, never an absolute path.
For authoring, keep TARGET's implementation header and verify the derived authoring branch separately.

When delegating, give each assignment its worktree path and have the agent enter it; do not let
spawn create another checkout. Preserve unrelated changes and commit assigned edits before finishing.
Report worktree path, branch, and base SHA in the handoff.

## Cleanup

Before a new batch, inspect `git worktree list`. Remove with `git worktree remove <path>` only
inactive trees with no uncommitted changes and all commits reachable from `main` or `origin/main`;
report anything else. `/accept-and-land` keeps the document tree; `/closeout` retires a finished one.
