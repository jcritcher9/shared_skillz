# Implementer Worker Bootstrap

Read `agent_protocol/worktree.md` and `agent_protocol/kernel.md` for worktree mechanics, ledger vocabulary, interpreter, and targeted test selection. The scoping document, current state, and authorized target govern implementation. Preserve unrelated changes.

Shuttle self-operation must stop before changing `agent_protocol/**` or controlling runtime skill adapters; use the external worker/critic/manual-land path. Changes to `shuttle/**` or `shuttle.cmd` are allowed only when Shuttle's Python gate proves the imported operator is frozen outside a distinct non-reparse document worktree and the invocation is not publication. External worker assignments remain valid.

## Resolve the assignment

A **phase** groups slices; a **slice** owns a commit-table row; a **pass** specifies delivery grain. Numeric slice ids represent single-slice phases. Preserve legacy `### Phase 4A` slice sections and historical ids.

| Assignment | Target |
|---|---|
| Slice `4A` | That slice only. |
| `Phase 4` | Its remaining unfinished slices, excluding `do not combine`. Reject an empty expansion; require a named slice or pass. |
| Pass `P1` | Exactly that pass-table row's slices. |
| Document only | Propose the next schedulable slice or pass and wait. |

Pass-table adoption approves grain, not implementation. A named phase may combine sequential slices; `do not combine` still wins. Use the normalized slice set throughout implementation, ledger updates, and handoff.

## Enter and read

Follow `worktree.md` to enter and verify the document checkout; its copy is authoritative.

Read context pointers per `kernel.md` from this worktree. If the current-state pointer is missing, propose the area's existing context file(s), or `none` with a reason, and wait. On confirmation, commit and push only the header repair, including testing for a new header.

For a fresh document, confirm this is the first schedulable target and add `**Branch:**`, `**Base commit:**`, `**Current state:**`, and `**Testing:**` using the verified worktree identity and base. Propose unknown pointers before implementation.

In a Python-checkpointed Shuttle turn, missing Branch/Base headers may be added
using the current branch and full pre-checkpoint HEAD SHA; existing identity
headers stay unchanged. The top Status may change only its terminal
`implementation not started` to `implementation in progress`, preserving any
prefix. Testing must already be established during readiness. Other document
edits remain limited to the Current state pointer and assigned ledger fields.
Python proves this document pin before publishing the checkpoint; a rejected
pin leaves the working files and provisional handoff available for correction.

Resolve each slice's status from the first available source:

1. Commit-table Status cell.
2. Legacy `### Phase <slice-id>` section's `**Status:**`.
3. Parent phase's Slices-table cell.
4. Parent `**Status:**` only for its sole numeric slice.

Ignore document-level status. Multiple slices with only a collapsed parent status are a ledger defect: stop. A document without `### Phase` headings is not a scoping document and gets no document worktree.

## Dispatch

Apply the first matching row after rejecting an empty phase expansion:

| State in this turn | Action |
|---|---|
| NOT ACCEPTED / Grade B+ or below for submitted slices awaiting grading | Remediate those slices now; the report authorizes remediation. Preserve the original assignment and all open findings. Accepted siblings remain unlanded this turn. |
| Grade A / A− ACCEPT, with no rejected slices in that report | If already asked to land, run `/accept-and-land`; otherwise propose it and wait. Acceptance alone does not authorize landing or a new target. |
| Submitted slices await grading, with no applicable verdict | Propose `/critic`, naming the slices and SHAs; do not implement. |
| `accepted` awaiting publication | Stop outside an explicit trusted continuation/publication path. |
| After `/accept-and-land` | Propose the next schedulable target, or `/closeout` if all slices are `done` or `SUPERSEDED`, and wait. |
| No target named | Follow the proposal rule below. |
| Authorized unfinished target | Prove continuity, then implement exactly that target. |

For a proposal, use kernel ledger order and stop on gaps. Route the first unfinished slice through Dispatch. Verify `done` SHAs are on local `main`; if not, propose `/accept-and-land` and run it only if already authorized.

For `not started`, propose its pass when grain is `same pass`, including its other unstarted partners; a submitted partner is a gap. Otherwise propose that slice, never its parent phase. State target/title, status, grain, and previous `done` SHA (or no ancestor). Wait without edits, tests, or commits. Confirmation authorizes only that proposal; normalize a different assignment. If nothing remains, propose `/closeout` after any required landing.

## Prove continuity

Before editing, require a clean tree. Do not stash, reset, or commit unrelated changes.

Target slices must be `not started` or authorized NOT ACCEPTED remediation. Stop on unapproved kernel ledger gaps. When a predecessor exists, prove it is on local `main` and an ancestor of `HEAD` with `git merge-base --is-ancestor`. Intervening changes must be documentation only, apart from this target's submitted implementation/remediation chain. Stop on failed ancestry or unexplained production changes.

If `HEAD` is behind and an ancestor of local `main`, fast-forward with `git merge --ff-only main`. A divergent `main` does not itself require integration. **Do not rebase before the ledger pin by default.** Keep the implementation's existing history; rebase only under a separate explicit integration instruction or the authorized landing workflow. Never rewrite an off-main SHA already recorded in the ledger during ordinary worker submission or remediation. Landing Path B belongs to `/accept-and-land` or `/closeout`.

## Implement, test, and pin

Implement only the target and update affected context descriptions under the kernel's documentation rules.

Read the trusted cumulative findings projection, not only the latest report. Address every open finding ID with repair evidence and a regression test; omission from a newer report does not erase an obligation. Test real failure boundaries and competing operations, including failure injection where atomicity or exact retry is required. Replace obsolete tests when the contract makes a hard cut.

Commit implementation before recording I. Independently gradeable slices need separate commits under the kernel's shared-SHA rule; a same-pass target may contain multiple commits.

Run the kernel's targeted lane on I. Red results require remediation, a new implementation commit, and another targeted run before handoff.

After tests pass, record each assigned slice as `implemented at <I> awaiting human grading` in both its commit-table Status/Commit cells and its section or Slices-table representation. Leave other slices untouched. Commit these ledger changes separately as documentation pin **P**. Never mark your own work `done`.

If only the current-state pointer is missing after submission, create the kernel's pointer-only P without reopening implementation.

Verify the pin names the tested implementation, all work is committed, and `git status --porcelain` is empty. Push the document branch with `git push -u origin "agent/<doc-slug>"`. Never publish ungraded implementation to `main`. Hand off without a self-grade, acceptance matrix, or acceptance disposition; the critic decides whether findings are resolved.

## Required handoff format

Keep these identity fields and use repository-relative document/test paths.

```text
Scoping document: <repo-relative-path>
Target: <slice 4A | Phase 4 → 4A,4B | Pass P1 → 0A,0B>
Slices:
Current state: <path | paths | none — reason>
Testing: <path | paths | none — reason | sibling of current_state>
Worktree path:
Branch:
Base commit:
Original implementation:
Remediation commit: <sha | none>
Prior ledger pin: <sha | none>
Current ledger pin:
Pin type: ledger pin | current-state pointer pin
Implementation commit:
Documentation pin:

Production files changed:
Generated files changed:
Shared invariants touched:

Tests added:
1. <test path>::<test name> — <boundary it proves>
2. <test path>::<test name> — <boundary it proves>

Prior finding -> regression test:
1. <finding> -> <test path>::<test name>
2. <finding> -> <test path>::<test name>

Targeted selection:
<path>::<name> — passed | failed then remediated

Authorization boundary:
<next slice remains unstarted and unauthorized, or exact authorization given>

Unrelated workspace changes:
<confirmation that they were preserved>
```

Use kernel identity definitions and exact pytest node ids in test fields, keyed by finding ID for regressions. These are evidence claims, not commands the critic must repeat. State when a field has no applicable entries.
