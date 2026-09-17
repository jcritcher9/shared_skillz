# Agent Worktree Protocol — cross-runtime specification

A runtime-neutral spec for isolating implementation agents in git
worktrees. Written so Claude Code, Codex, Grok, or any other agent
runtime can implement equivalent skills that interoperate on the same
repository.

**Start at §11.** This document is the entry point for humans and
runtime authors; §11 copies the per-role init read-set from
`read_set.py`. Doers start at their role file. This README is in no
doer read-set. Operating instructions live in the role files; they are
normative. Nothing here is runtime-specific except §9 and the packaging
notes in §11.

---

## 1. Problem

Multiple agents in one checkout share a single `HEAD`, index, and
working tree. A branch alone does not fix this — branches share the
checkout. Only a separate directory does.

## 2. Model

One **scoping document** maps to exactly one **worktree**, one
**branch**, and many **workers** run sequentially, each implementing one
**target** (a slice, a named phase's remaining allowed slices, or a
pass-table row).

```
scoping document  --1:1--  worktree directory
                  --1:1--  branch  agent/<doc-slug>
                  --1:N--  workers (one per target, sequential)
```

Agent granularity is the **normalized target**. Worktree granularity is
the **document**. A phase or slice id must never appear in a worktree
path or branch name.

### Shuttle executable trust root

Shuttle may dispatch, checkpoint, grade, and record deferred acceptance
for a change to `shuttle/**` or the repo-root `shuttle.cmd` only when
the executing `shuttle` package is frozen outside the target document
worktree. The freeze is the single resolved real directory supplied by
the already imported package's `__file__` and `__path__`, never launcher
text, `PYTHONPATH`, editable-install metadata, or cwd. The document
worktree must be distinct from the canonical `--repository-root`; every
component on the package, worktree, and canonical paths must be
non-reparse; missing, multiple, disagreeing, aliased, or unresolvable
origins stop closed. Deferred acceptance is not publication.
`--publish-accepted` for either operator path remains barred until a
separately authorized drained closeout path exists.

The live-read control plane remains unconditionally protected:
`agent_protocol/**` and runtime skill adapters controlling worker,
critic, worktree, readiness, acceptance, landing, publication,
checkpointing, or confinement. The Python dispatcher rejects a scoping
document, path-like target, normative `In scope` contract, or explicit
declared delta that reaches that set before a provider starts and again
before a Python-owned checkpoint. Such changes use a dedicated worktree,
external worker, independent critic, and manual acceptance/landing. The
strategy that installs this exception must itself use that external path.

## 3. Location and naming

Worktrees live **outside the repository**, at a path no runtime owns:

```
<parent-of-repo>/worktrees/<repo-name>/<doc-slug>
```

Operating create-or-attach: `agent_protocol/worktree.md`. `doc-slug` is
the filename stem, punctuation unchanged, unless a `**Branch:**` header
exists — then the slug is that header minus `agent/`. Read only that
line from `origin/main` (fallback: current checkout) before
create-or-attach. Do not rewrite `_` to `-` on document slugs. Never
`.claude/worktrees/`, `.codex/`, `.grok/`, or any runtime-specific
directory.

The path is **derived, never looked up**. The `**Branch:**` line is
identity and may be read from `origin/main` to compute the slug; the
rest of the document may not.

## 4. Scoping document contract

The document is a tracked file, so a copy exists in every checkout.
**The copy inside the worktree is authoritative.**

### 4.1 Document header

Recorded once, by the first worker, below the document-level
`**Status:**`:

```markdown
**Branch:** `agent/<doc-slug>`
**Base commit:** `<sha>` — branched from `main`
**Current state:** `<repo-relative-path>` | none — <reason>
**Testing:** `<repo-relative-path>` | none — <reason>
```

Record the **branch**, never an absolute worktree path.

`**Current state:**` is mandatory: one or more repository-relative paths
to `current_state.md`, or `none` with a one-line reason. Missing both is
a stop. Worker proposes the obvious value and, on confirm, writes the
line in a docs-only commit (pointer pin **P** of implementation **I**
when I already exists). New documents also name `**Testing:**` (sibling
`testing.md`, or `none`). Existing documents without it are
grandfathered; read the sibling of each named `current_state.md`. Critic
first-touch: worker writes P by default; critic writes P only if the
user explicitly authorizes that one commit. Grade tests at I; read the
pointer from P. `HEAD == P` is not a failed exact-SHA gate. See
`agent_protocol/critic.md` (Critic first-touch).

### 4.2 Slice ledger

The durable unit is the **slice**, not the parent phase. A grouped Phase
4 must be able to show `4A` `done`, `4B` awaiting human grading, and
`4C` `not started` at once when those slices have **different** commit
SHAs. Acceptance is atomic by SHA: slices that share one commit must
share one critic outcome. Independently gradeable slices use separate
commits. A same-pass target may still contain multiple commits.
Grandfathered shared SHAs where every slice was accepted remain valid.

The commit table has **one row per slice**, including a **Status** cell
with the vocabulary in §4.3. That cell is the authoritative lifecycle
when present.

**New strategies** use numeric `### Phase N` parent headings. A
multi-slice phase also lists each slice in a Slices table with the same
Status. A single-slice phase uses the phase number as the slice id; that
section's `**Status:**` is the slice.

**Legacy slice sections** (`### Phase 0A`, `### Phase 0B`, and other
already-shipped letter-suffixed headings) remain valid. Treat each
heading as the section for that slice id. Do not rewrite historical ids.

```markdown
**Status:** done (`<sha>`)
**Commit:** `<sha>` — `<commit subject>`
**Implement with:** sequential (alone).
```

Only slice-level Status counts — the commit-table Status cell, a legacy
`### Phase 0A` `**Status:**`, a Slices-table cell, or a single-slice
`### Phase N` `**Status:**`. The document-level `**Status:**` near the
top describes the document itself.

A parent `### Phase 4` `**Status:**` that collapses mixed slice state is
not authoritative. If a phase has more than one slice row and only that
collapsed line, the ledger is defective — closeout must fail, not treat
the parent `done` as closing those slices.

A document with no `### Phase` headings is supporting evidence, not a
scoping document, and does not get a worktree.

### 4.3 Status vocabulary — six states per slice

| Status value | Meaning | Effect on a worker |
|---|---|---|
| `done (<sha>)` | accepted and published to local `main` | this is an ancestor sha |
| `accepted (<sha>) awaiting publication` | critic-accepted on the document branch, not yet published | stop outside an explicit trusted continuation/publication path |
| `checkpointed at <sha> awaiting human grading` | trusted Python created the validated implementation checkpoint | propose `/critic`; not accepted |
| `implemented at <sha> … awaiting human grading` | externally submitted implementation, **not** accepted | stop and report |
| `SUPERSEDED <date> — must not be implemented` | withdrawn | skip as though absent |
| `not started` | no work exists | if it precedes your target, stop and report the gap |

Walk **slices** in document (commit-table) order. Skip `SUPERSEDED`.

- **Earlier** than the target: walk backwards to the newest `done` and
  take its sha. An earlier `awaiting human grading` or `not started`
  slice is a gap: stop, unless the user explicitly authorizes reordering.
  Regression: target 4B with 4A `not started` and no reorder
  authorization → stop.
- **Later unassigned siblings** may remain `not started`. That is not a
  gap.

### 4.4 Who writes what

- **Worker** writes each assigned slice's `**Commit:**` line and sets
  that slice's Status to `implemented at <sha> awaiting human grading`.
  It must **not** set `done`.
- **Grader** writes nothing.
- **Acceptance plus local publication** (`done` per accepted slice) is
  recorded by `accept-and-land` when the user runs it after critic ACCEPT
  (Grade A or A−). A future trusted deferred path may first record
  `accepted (<sha>) awaiting publication`. Record `done` only on slices
  the critic graded ACCEPT whose SHA is not shared with a NOT ACCEPTED
  slice. Worker and critic do not write `done`.

## 5. Worker protocol

Operating instructions: `agent_protocol/worker.md`. Do not restate them
here. Area-only operating instructions: `agent_protocol/worker-area.md`.

## 6. Idempotent create-or-attach

Operating block: `agent_protocol/worktree.md`. Exact-path existence
test, not a search. Four states: reuse if the directory exists; create
if absent; re-attach if the directory is gone but the local branch
survives; fetch and track if only the remote branch exists.

**Never create a second branch for a document that already has one**,
locally or on the remote.

## 7. Grader protocol

Operating instructions: `agent_protocol/critic.md`. Do not restate them
here.

## 8. Landing on `main`

Operating instructions: `agent_protocol/accept-and-land.md`
and `agent_protocol/closeout.md`. Do not restate Path B, ledger
hierarchy, or classify here.

| Skill | When | Retire worktree |
|---|---|---|
| `accept-and-land` | critic Grade A / A− for the tip, and the user asked to land | no |
| `closeout` | every slice `done` or `SUPERSEDED` | yes |

## 9. Runtime adaptation

The protocol needs exactly two abstract operations. Each role file
states them; the runtime-specific row lives in that runtime's adapter.
This section is the human explanation of the mapping, not a doer
read-set. Role files do not name runtime tools.

| Abstract operation | Meaning |
|---|---|
| **Create** | Materialise the document worktree with `git worktree add` as in `worktree.md` / §6. Never the runtime's built-in creator. |
| **Enter** | Make the agent's working directory that path. If the agent is already in that directory, it is entered. |

Runtime-specific Create/Enter rows live only in that runtime's adapter.
Claude-only pinning must not be applied on Grok or Codex. Any runtime
that can run git and set a working directory can implement this.

## 10. Invariants

An implementation is conformant if all of these hold, however it
achieves them:

1. One worktree per scoping document; never one per phase.
2. Worktree paths contain no runtime-specific directory component.
3. The path is computed from the `**Branch:**` header minus the `agent/`
    prefix when it exists, else from the document filename. Never
    searched. The header line may be read from `origin/main` before
    entering; the rest of the document may not.
4. Agents enter the worktree **before** reading the ledger. The
    `**Branch:**` line may be read from `origin/main` solely to compute
    the slug.
5. A worker never creates a second branch for a document that already
    has one.
6. A worker never sets its own slices' Status to `done` during
    implementation. It records `implemented at <sha> awaiting human
    grading` per assigned slice. After Grade A it proposes
    `/accept-and-land` and waits. That skill writes `done` when the user
    runs it.
7. A worker never merges ungraded implementation to `main`. After Grade
    A it does not land. `accept-and-land` lands accepted commits and
    keeps the worktree when the user runs it. Closeout lands a finished
    document and retires. Path B lands by rebase-then-ff
    (`agent_protocol/accept-and-land.md`), not `git merge --no-ff`.
8. Continuity is checked by ancestry, not equality.
9. A grader gates on a clean tree before testing. Tests run at the
    submitted implementation I. `HEAD` may be a proven pointer-only pin
    P of I; that is not a failed exact-SHA gate.
10. Workers do not rebase before pinning by default or rewrite a SHA
    already named in the ledger and off `main` during ordinary submission.
    accept-and-land and closeout Path B may
    rebase unlanded accepted commits only with `range-diff` `=` (or
    matching patch-ids), tests on the landed tree, and a ledger remap
    to the post-rebase SHAs. Path A does not rebase.
11. Only the branch, never an absolute path, is recorded in the
    document. `**Current state:**` records a repository-relative path or
    `none`, not a machine-local path. New documents also record
    `**Testing:**` the same way.
12. Workers on one document run strictly sequentially.
13. After an area has migrated, area-queue files (`open_work.md`,
    `strategy_index.md`) are authored on `agent/<area-slug>-queue` and
    published to `origin/main`; document branches must not modify them.
    The migration commit that lands the area is allowed to create,
    replace, or delete those files. A queue clear after closeout
    fast-forwards the queue branch onto the new `origin/main` before
    committing.
14. A scoping document has `**Current state:**` (path, paths, or `none`)
    before implementation or grading proceeds. Named files get one
    upfront read before proposing, implementing, or grading (`none`
    means there is no file to read). Read `testing.md` from
    `**Testing:**` or the sibling of each named `current_state.md`.
    Later re-reads are not required unless context compacted.

## 11. Initialization — start here

This document is the reasoning and conformance entry point for humans
and runtime authors. The **operating instructions live in the skill
files in this repository**, and they are normative. Doers start at their
role file and read only that role's init set. The machine source is
`agent_protocol/read_set.py`; this table is a human copy and is not
parsed.

| Role | Init read-set |
|---|---|
| Worker | `worker.md` + `worktree.md` + `kernel.md` |
| Critic | `critic.md` + `kernel.md` |
| Area worker | `worker-area.md` + `worktree.md` + `kernel.md` |
| Accept-and-land | `accept-and-land.md` |
| Closeout | `closeout.md` + `accept-and-land.md` |
| Worktree | `worktree.md` |

README is in no role's read-set. Other role files are read only when
their behavior is actually under review (those paths are in the assigned
diff). §10 is for whoever checks conformance or generates a runtime.

These files remain the operating homes for every runtime:

| File | Role it defines | Governs |
|---|---|---|
| `agent_protocol/worker.md` | implementer | target authorization (normalize slice / phase / pass before dispatch; propose-and-wait when omitted), `**Current state:**` / `**Testing:**` pointers, per-slice ledger, Grade A → propose `/accept-and-land` and wait, NOT ACCEPTED in this turn remediates those slices without a further yes, targeted tests with no self-grade, **required handoff format** |
| `agent_protocol/worker-area.md` | area worker | classify from `origin/main`, queue claim, then propose `/worker <doc> <target>` and wait |
| `agent_protocol/kernel.md` | shared kernel | `$py`, targeted lane, Status vocabulary, 1:1 model — opened by worker, critic, and area worker |
| `agent_protocol/critic.md` | grader | lineage and scope proof, `**Current state:**` pointer (first-touch pin P; tests at I), `testing.md` targeted lane, per-slice ACCEPT/NOT ACCEPTED plus an aggregate target grade, acceptance matrix, adversarial probes, severity rules, **required critic report format** |
| `agent_protocol/accept-and-land.md` | accept-and-land | record `done` after Grade A / A− (ledger-only; no functionality change) and land accepted commits on `main` without retiring the worktree |
| `agent_protocol/closeout.md` | closeout | landing a completed document on `main` and retiring the worktree |
| `agent_protocol/worktree.md` | orchestration | worktree creation and assignment |

They carry no runtime packaging — no frontmatter, no runtime-specific
paths. Register them however your runtime registers a skill. The
create/enter mapping lives in the adapter; §9 is the human explanation.

**Adapters, not copies.** A runtime that requires skills in its own
directory gets a thin stub there that points back here:

| Runtime | Adapter path |
|---|---|
| Claude Code | `.claude/skills/<role>/SKILL.md` |
| Grok | `.grok/skills/<role>/SKILL.md` |
| Codex | `.agents/skills/<role>/SKILL.md` |

Codex repository discovery is `.agents/skills`, scanned from the working
directory up to the repository root. Do not put repo-scoped Codex
adapters under `.codex/skills`; that is not a repository skill root.

Each stub is frontmatter plus a pointer to
`../../../agent_protocol/<role>.md` (resolved from the skill directory,
not from the repository root), that runtime's create/enter row, and the
role's read-set line from the table above. Do not send a doer to this
README for an init read-set. Never copy a role file into a runtime
directory — two copies diverge, and the runtimes stop agreeing.

**Registration is separate from the init read-set.** All five registered
roles (`worker`, `critic`, `worktree`, `accept-and-land`, `closeout`)
must be present in each adapter directory. Area binders point at
`worker-area.md`. Narrowing the init read-set does not mean only three
adapters need to exist.

Everything else transfers verbatim. **Do not paraphrase or re-derive
these files.** Reproduce the behavior they specify, especially the two
report formats.

### The report formats are the interface

`Required handoff format` in `worker` and `Required critic report` in
`critic` are the contract between the roles, and they cross runtimes: a
worker running under one runtime may hand off to a grader running under
another. A worker that emits a different handoff shape breaks grading
even if it follows every rule in §1–§8 perfectly. The critic report's
`Slices:` block is required: an aggregate Grade line cannot express “4A
accepted, 4B rejected.” Split per-slice outcomes are valid only across
different SHAs; slices sharing one commit must share one outcome. Treat
both formats as fixed.

### Conformance

An implementation is correct when it satisfies §10 and emits both report
formats unchanged. Check a generated skill against §10 point by point
before using it on real work.
