# Implementation notes strategy

Last updated: 2026-08-22

This is the **repository convention** for multi-phase implementation strategies:
how to write them, how agents should update them, and a worked example from
EasyImports CRM-neutral foundation work.

Deep product design for that initiative remains in:

```text
mappings_2/codex_context/cross_agent_eval/implementation_notes.md
```

Agents implementing phased work should treat **this root doc** as the process
template and the **package-local `implementation_notes.md`** (or equivalent)
as the living product strategy.

---

## Why this document exists

Large initiatives fail when agents:

- treat a multi-phase plan as one unbounded PR;
- lose track of which commit closed which phase;
- alternate between competing “next task” definitions across context documents;
- mix contracts, fake adapters, public HTTP, provider adapters, and live gates
  in one change set;
- over-serialize tightly coupled work into many tiny PRs that reopen the same
  unfinished contract.

A good strategy doc is **phased**, **commit-linked**, **explicit about what
is not allowed in each phase**, and **explicit about which slices are one
implementation pass**.

### Phase vs slice vs pass

These are three different things. Do not use one word for all three.

| Word | Meaning |
|---|---|
| **Phase** | A grouping with a numeric `### Phase N` narrative section (new strategies) or, in already-shipped docs, letter-suffixed `### Phase 0A` **legacy slice sections**. A numeric parent can contain one or more **slices**. A phase is done only when every one of its slices is done. Naming the phase in an assignment authorizes those slices (below), except `do not combine`. |
| **Slice** | The individual part. It always has its own commit-table row. `0A` and `4B` are slices. A phase with a single slice uses the phase number as that slice's id (`1` is the sole slice of Phase 1). |
| **Pass** | How slices are delivered: one slice, several slices together (`same pass`), or globally isolated (`do not combine`). |

Letter suffixes (`0A`, `4A`, `5B`) are **slice** ids. They are not a second kind of phase. The parent phase is the numeric prefix (`0`, `4`, `5`) matching the `### Phase N` heading.

**Heading representations**

- **New strategies** use a numeric `### Phase N` parent heading, one commit-table row per slice, and a Slices table when the phase has more than one slice. Slice ids stay letter-suffixed (`4A`) or equal the phase number (`1`).
- **Already-shipped `### Phase 0A`-style headings and ids remain valid.** They are **legacy slice sections**: the letter-suffixed heading *is* the slice, not a parent phase. Workers read that section's `**Status:**` as that slice's lifecycle. Do not rewrite historical ids (`0A` stays `0A`; do not retitle the heading to `### Phase 0`).
- The worker supports both representations in the same document.

**Worker assignment** — normalize into one target before dispatch (`agent_protocol/worker.md`):

- A **slice** id (`4A`) → that slice only.
- A **phase** id (`Phase 4`) → remaining unfinished slices of that phase **except** slices whose pass grain is `do not combine`. Those isolated slices must be named (`5A`, not `Phase 5`).
- A **pass** id (`P1`) → exactly the slice ids on that pass-table row.
- **No target** → propose and wait. Confirmation is the assignment.

If a named phase expands to **zero** slices because every remaining slice is `do not combine`, **reject**. `Phase 5` when 5A and 5B remain is this case: the parent is not an authorizable target.

A worker may implement a whole phase in one turn when the user names the phase. That is allowed even if those slices are listed as `sequential`; naming the parent is the authorization to take them together. `do not combine` still wins: the parent name does not pull those slices in.

Adopting a pass table approves its **grain**. It does not start work. The user must still name or confirm the pass (or a slice or phase).

### Current state pointer (mandatory on every scoping document)

Every scoping document names its associated `current_state.md` near the top,
below the document-level `**Status:**` (and next to `**Branch:**` when that
header exists). New documents also name `testing.md` on a `**Testing:**`
line in that same header region.

```markdown
**Current state:** `mappings_2/codex_context/current_state.md`
```

Several paths, when the strategy spans areas:

```markdown
**Current state:** `mappings_2/codex_context/current_state.md`; `archon/codex_context/current_state.md`
```

Or an explicit none:

```markdown
**Current state:** none — protocol-convention document; no area `current_state.md`
```

Missing **both** a path and `none` is a stop. Do not implement or grade.
Propose the obvious value; do not write it until the user confirms:

- Document lives under `<area>/` and `<area>/agent_context/current_state.md`
  or `<area>/codex_context/current_state.md` exists → propose that path
  (`agent_context` if both). See `CONTEXT_PILLARS.md` (Context root).
- Repo-root, `agent_protocol/`, or a strategy whose scope names several
  areas → propose `none` or the list of those areas' files.
- Confirmation is required. Do not infer silently.

On confirm, the **worker** writes that one line and makes a docs-only commit
on the document branch. Do not mix it with implementation. Existing
strategies are grandfathered by first touch — do not mass-rewrite.

The **critic** pauses and asks the same way. Default: it does **not**
commit; the worker records pointer pin **P** as a docs-only descendant
of implementation **I**. If the user explicitly authorizes the critic to
write P, that one commit is allowed. Then the critic reads the pointer
from P, reads named `current_state.md` at I, and runs tests at I.
`HEAD == P` is expected and is not a failed exact-SHA gate. Detached I
lacking the pointer is not a stop once P is proved. See
`agent_protocol/critic.md` (Critic first-touch).

See `agent_protocol/worker.md` and `agent_protocol/critic.md`.

### Testing pointer (mandatory on new scoping documents)

New scoping documents name the associated `testing.md` next to
`**Current state:**`:

```markdown
**Testing:** `mappings_2/codex_context/testing.md`
```

Several paths, when current state names several areas:

```markdown
**Testing:** `mappings_2/codex_context/testing.md`; `archon/codex_context/testing.md`
```

Or explicit none, matching a `none` current-state pointer:

```markdown
**Testing:** none — protocol-convention document; no area `testing.md`
```

Default: the `testing.md` sibling of each named `current_state.md`.
`**Current state:** none` → `**Testing:** none` with the same reason.
A named `current_state.md` path with `**Testing:** none` is a stop; none
cannot skip a required testing pillar. Each path must end in
`testing.md`. See `CONTEXT_PILLARS.md` (`testing.md`).

Existing documents without this header are grandfathered. Do not
mass-rewrite. Read the sibling of each named `current_state.md` instead
(`none` → skip). Worker first-header commits on a new document include
this line. Missing `testing.md` on disk is filled by
`/create-context-pillars` or `/make-shuttle-ready`, not by inventing
commands in the strategy.

### Document terminator and human review prerequisite (optional)

Optional closed headers in the same header region as `**Current state:**`,
`**Testing:**`, and `**Shuttle plan:**`. `/make-shuttle-ready` and
`/check-shuttle-ready` parse them. Omitted headers preserve existing plans
that have no last-pass `terminator` and no sister-plan
`human_review_requirement`. Present headers are exact syntax; empty,
duplicated, extra-token, or prose-only lines fail before ready.
Generate-check-regenerate preserves the declared identity. A later
make-ready must not silently erase a matching sister-plan field.

Publication terminator (no artifacts key):

```markdown
**Document terminator:** kind `publication`
```

Human-review terminator (nonempty ordered distinct repository-relative
POSIX artifact paths):

```markdown
**Document terminator:** kind `human_review`; artifacts `review_artifacts/report.json`, `review_artifacts/bundle/index.json`
```

Concrete successor prerequisite (predecessor path, predecessor pass, gate,
SHA-256 request digest):

```markdown
**Human review prerequisite:** predecessor `area/pred.md`; pass `P3`; gate `H2`; request `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
```

The terminator attaches to the last pass. The prerequisite is a sister-plan
root field. Publication forbids `artifacts`. `human_review` requires them.
The request digest is 64 lowercase hex. At most one of each header.

---

## Agent rules (mandatory)

### When designing a scoping / strategy doc

1. Keep distinct goals as **distinct slice IDs** even if they should land
   together. Combining is a delivery decision, not a reason to collapse two
   goals into one slice or one phase.
2. After naming phases, decide the **implementation pass grain** and record it
   in the **Recommended implementation passes** table: which IDs are `same
   pass`, which are `sequential`, and which are `do not combine`.
3. **Suggest `same pass` where it earns it** (see criteria below). Silence is
   sequential-only, not implementer discretion. `do not combine` means that
   one ID is globally isolated, not “forbidden with unnamed neighbors.”
4. Write a one-line **why** on every `same pass` row and every `do not
   combine` row.
5. Do not recommend combining across risk classes or live-gate authorization.
6. If a tempting combination would exceed a reviewable change set, **slice
   first**, then combine only the slices that still meet the criteria.
7. Put a `**Current state:**` line near the top: a repository-relative path
   (or several), or `none` with a one-line reason. Put a `**Testing:**`
   line next to it for the sibling `testing.md` (or `none` with the same
   reason). See **Current state pointer** and **Testing pointer** above.
8. Write a bounded `## Lifecycle sketch` before detailed phase prose. State
   whether operational transitions apply. When they do, include a typed source
   map and trace accepted work through publication, binding, dispatch, evidence
   ingest, and the next gate. Name the existing public command/API and the
   repository, document, worktree/ref, plan/pass, operator authority, binding,
   and evidence identities at each transition; explain inapplicable coordinates.
   A proposed capability names its implementing slice and acceptance proof and
   cannot generate the proof that authorizes itself. Include retry/crash and
   negative cases at the skeleton stage. When Shuttle authors the strategy, its
   first critic approves this milestone before detailed expansion.

### When implementing or closing a phase or slice

1. **Read** the package strategy (`implementation_notes.md` or named strategy)
   and the phase section you are authorized for. If `**Current state:**` is
   missing both a path and `none`, stop and ask (`agent_protocol/worker.md`).
   Read `testing.md` from `**Testing:**` or the sibling of each named
   `current_state.md`. Follow its targeted-selection policy; do not
   self-grade.
   Authorization comes from the
   user naming a bounded task, a **phase**, a **slice**, or a pass, or from a
   structured task queue — never from prose being first, open, or `Not
   started`. A written phase is not a self-authorizing task. See the
   authorization rule in `CONTEXT_PILLARS.md` and **Worker assignment** above.
2. **Implement the assignment:** one slice, one named phase (all of its
   allowed remaining slices), or one strategy-named combined pass. Do not
   invent combinations the pass table does not list, and do not pull in
   `do not combine` slices via a parent phase name.
3. **Commit locally** when that assignment is done (or when the user asks),
   with a subject that names the slice ids (and the phase, if the assignment
   was the whole phase).
4. **Update the slice commit table** in the strategy notes, **per assigned
   slice**:
   - slice id and short name;
   - Status: `implemented at <sha> awaiting human grading` (never `done` —
     acceptance is recorded after grading);
   - short git SHA (`git rev-parse --short HEAD` of the code commit);
   - commit subject / one-line scope.
   A phase/pass assignment may cover several slices; each row stays
   independently recordable. Do not collapse them into one parent-phase row.
5. Prefer a **follow-up docs commit** if the SHA must appear inside the same
   notes file (a commit cannot contain its own full hash).
6. **Do not invent live CRM/customer access** or production authorization from
   strategy adoption alone.
7. When strategy **supersedes** a prior “next task,” record the supersession in
   the strategy that owned it, and update any surviving living context document
   or local priority pointer in that area, so agents do not thrash. The
   strategy's own supersession record is the whole obligation, and a
   now-closed strategy's path moves to **Historical** in that area's
   `strategy_index.md`, and is removed from Current/Queued in that area's
   `open_work.md` if it was listed. Path and membership edits only, never a
   diary note. An area with no area-local scoping documents has neither file,
   and then the supersession record alone is the whole obligation. See
   `CONTEXT_PILLARS.md`.
8. Treat the package strategy as the **sole human-readable normative authority**
   for scope, phase order, authorization, and acceptance. Do not create a
   second prose freeze that agents must discover separately.
9. **Every additional documentation file created for an initiative MUST be
   referenced by repository-relative path from its main package strategy.** The
   reference must state why the file exists and whether it is normative or
   non-normative. An unreferenced auxiliary document is not an authority.
10. Put closed machine-enforceable decisions in executable freeze artifacts
    and prove them with tests; keep their human-readable meaning and ownership
    in the main strategy.

### Compatibility policy — pre-launch hard cut (mandatory)

Unless a phase **explicitly** requires compatibility, contract, manifest,
checkpoint, receipt, database, and API revisions are **hard cuts**. Old
invocations, journals, checkpoints, and local development state may become
unreadable and may require reset/reseed. **“Versioned” identifies the current
contract; it does not imply dual-read, dual-write, migration, or replay
support.** Agents and graders must **not** request or implement backward
compatibility without explicit phase authorization language.

### Documentation authority and freeze artifacts (mandatory)

Use this hierarchy for every multi-phase initiative:

```text
Main package strategy
  → sole human-readable normative scope, phase order, authorization, acceptance
Freeze module + structured fixtures
  → executable representation of closed decisions
Freeze tests
  → stability proofs for those executable decisions
Auxiliary documentation
  → optional, explicitly referenced non-normative explanation only
```

If freeze modules, fixtures, tests, decision records, appendices, or other
supporting documents are added, the main strategy **MUST** name their exact
repository-relative paths and explain their place in this hierarchy. Auxiliary
documentation must not introduce or override scope, sequencing, authorization,
or acceptance decisions that are absent from the main strategy. If a supporting
document reveals a new decision, update the main strategy in the same change.

### Phase / commit table (required shape)

Keep this table near the top of every multi-phase `implementation_notes`
strategy. Update it as phases land on `main`.

```markdown
## Phase implementation commits

Recorded against `main` as slices land. A single commit may cover more than
one slice when they shipped together. Preferred combinations are named at
design time in **Recommended implementation passes**, not invented at
implementation time. **Status is per slice.** A grouped Phase 4 can show
4A `done`, 4B awaiting human grading, and 4C `not started` at once.

| Slice | Status | Commit | Subject / scope |
|---|---|---|---|
| **0A** — short name | done (`abcdef12`) | `abcdef12` | `Commit subject line.` |
| **0B** — short name | done (`abcdef12`) | `abcdef12` | Same commit as 0A if combined. |
| **1** — short name | done (`34567890`) | `34567890` | Sole slice of Phase 1. |
| **4A** — short name | done (`456789ab`) | `456789ab` | `Commit subject line.` |
| **4B** — short name | implemented at `567890bc` awaiting human grading | `567890bc` | `Commit subject line.` |
| **4C** — short name | not started | — | Not started. |
| **5A** — Salesforce live gates | not started | — | Not started. |
| **5B** — HubSpot live gates | not started | — | Not started. |

```text
abcdef12  Phase 0A + 0B (one-line summary)
34567890  Phase 1 (one-line summary)
456789ab  Phase 4A (one-line summary)
567890bc  Phase 4B (awaiting human grading)
```
```

**Rules for the table**

| Rule | Detail |
|---|---|
| Slice column | Stable **slice** id (`0A`, `0B`, `1`, `4A`…) plus a human short name. `1` is the sole slice of Phase 1. |
| Status column | Authoritative per-slice lifecycle: `not started` \| `implemented at <sha> awaiting human grading` \| `checkpointed at <sha> awaiting human grading` \| `accepted (<sha>) awaiting publication` \| `done (<sha>)` \| `SUPERSEDED <date> — must not be implemented`. `done` means accepted and published to local `main`; a SHA in Commit is not acceptance. |
| Commit column | Short SHA of the **code** commit that closed the slice; `—` if not started |
| Subject / scope | Exact or near-exact git subject; note combined deliveries |
| Combined slices | Allowed when the pass table, a **phase** assignment, or the user authorizes them. List the same SHA only when those slices are one Git commit. Acceptance is atomic by SHA: every slice sharing a SHA gets the same critic outcome. Independently gradeable slices use separate commits (a same-pass target may still contain multiple commits). Grandfathered shared SHAs where every slice is already `done` remain valid. |
| Self-referential SHA | Do not amend forever to embed the hash of the notes edit; use a small docs commit after the phase code commit |
| **One row per slice (mandatory)** | If a phase has more than one slice, **do not** keep a single rolled-up phase row. Expand the top table **immediately** with **one row per slice** (e.g. `4A`, `4B`, `4C`). Never mark a parent phase “done” while open slices remain unlisted or unfinished. |
| **Independent authorization (mandatory)** | Per-provider live gates, per-tenant, per-environment units are slices with `do not combine`. Do not authorize the parent (`Phase 5` does not mean 5A+5B). A worker given `Phase 5` when every remaining slice is `do not combine` **rejects**. Add a new slice row before authorizing a new unit. |
| When to introduce slices | As soon as the work is known to be multi-slice. Independent authorization boundaries must be sliced at **design time, before any authorization**. Other slices: name them before or with the first slice commit, not only at closeout. |
| Slice ids | Parent phase number + letter (`4A`, `4B`) or a lone phase number when there is only one slice (`1`). Keep ids stable so agents can say “implement 4B only” or “implement Phase 4.” Historical letter-suffixed headings (`### Phase 0A`) keep those ids. |
| Parent phase section | The narrative `### Phase N` section describes the whole phase; the **top table Status column** is the authority for each slice's lifecycle. A worker given **Phase N** implements that phase's remaining allowed slices. A worker given **NA** implements only that slice. |

### Recommended implementation passes (mandatory at design time)

When writing or revising a scoping/strategy doc, the designing agent **must**
propose the **implementation pass grain**, not only a phase list and a
delivery order. Sequential one-slice passes remain the default. Combining is
an explicit recommendation, never implementer improvisation.

**Keep distinct goals as distinct slice IDs** even when they should land
together. Combining is not the same thing as slicing: slices are the parts of
a phase; a combined pass is two or more slice IDs in one authorized
implementation pass. Naming a **phase** is a third way to take more than one
slice at once (every remaining allowed slice of that phase).

#### Required table

Place this immediately after the phase/commit table, still near the top. Fill
it when the strategy is written, **before** the first implementation pass.
Update it if later planning changes the grain.

```markdown
## Recommended implementation passes

Default is one slice per authorized pass. A row with more than one ID is a
designer-proposed combined pass. Adopting the strategy adopts that **grain**
— it does not start work. The user must still name or confirm the pass (or a
slice or phase) before a worker implements it.

| Pass | Slice IDs | Grain | Why |
|---|---|---|---|
| **P1** | 0A + 0B | same pass | Contract freeze is untestable without the proving consumer; same risk class. |
| **P2** | 1 | sequential | Public HTTP is a different risk class than 0A/0B. |
| **P3** | 2 | sequential | Salesforce adaptation; do not wait for or combine with HubSpot. |
| **P4** | 4 | sequential | Review journey may precede HubSpot; keep independently revertible. |
| **P5** | 3 | sequential | Second provider against a frozen contract; do not expand to include Phase 2. |
| **P6** | 5A | do not combine | Salesforce live gate is globally isolated; never implied; does not authorize 5B. |
| **P7** | 5B | do not combine | HubSpot live gate is globally isolated; 5A does not authorize it. |
```

**Grain values**

| Grain | Meaning |
|---|---|
| `same pass` | Implement the listed IDs together in one authorized pass. That pass may be one commit or several commits that still ship in the same turn. Grade each listed slice; slices that share a SHA must share one outcome. Use separate commits when mixed accept/reject must be possible. |
| `sequential` | Implement exactly one listed slice, unless the user names the **parent phase** (then all remaining allowed slices of that phase in this turn) or explicitly expands the pass. They may not pull in any ID whose own row is `do not combine`. |
| `do not combine` | **Global isolation of this one slice.** The IDs cell contains exactly one slice. It must not share a pass with any other ID, and it is not implied by naming the parent phase, unless the user explicitly overrides this row. |

**Rules for the pass table**

| Rule | Detail |
|---|---|
| Completeness | Every slice ID in the commit table appears in exactly one pass row |
| Sliced parents | List the slice IDs (`4A`, `4B`, `5A`, `5B`) in the pass table, not only a parent phase number |
| Isolated IDs | A `do not combine` row lists exactly one ID. Isolation is global: that ID cannot share a pass with any other ID |
| Why column | Required for `same pass` and `do not combine`; one line is enough |
| Adoption | Adopting the strategy adopts the pass-table grain. That is not authorization to start. The user must still name or confirm a pass, slice, or phase. Implementers do not invent combinations. |
| Later change | If planning splits or joins passes, update this table in the same docs change that authorizes the new grain |
| Landed record | When a `same pass` lands, the commit table still has one row per ID and repeats the SHA |

#### When to recommend `same pass`

Recommend combining only when **all** of the hard tests hold **and** at least
one coupling reason holds.

**Hard tests (all required):**

- **Same risk class and authorization.** All IDs are network-free, or all are
  the same already-authorized live gate — never a mix. Live gates do not
  combine with foundation work, and one provider's live gate does not
  authorize another's.
- **Still bounded.** The combined diff remains one reviewable change set
  with one named subject. If it would not fit, slice first; combine only the
  slices that still meet these tests.
- **Same test envelope.** Same network-free suite, or the same explicit live
  gate — not “land the fake and the public HTTP so we can click around.”

**Coupling reasons (need one):**

- **Acceptance of one is weak or unstable without the other.** Typical:
  contract freeze + first proving consumer; type change + the only call site
  that makes the new type executable. Useful question: *if we shipped only
  A, would we immediately have to reopen A to make B true?* If yes, prefer
  `same pass`.
- **Shared blast radius.** Sequential PRs would edit the same contracts,
  fixtures, or review surface and churn each other.
- **Shared production path, cheaper as one review.** Two small, independent
  fixes on the same path where a combined review is cheaper than two
  sequential ones. “They're small” alone is not a coupling reason.

#### When not to combine

Do **not** recommend `same pass` when any of the following hold:

- Different risk classes (internal contracts vs public HTTP vs Django
  journey vs real-provider adapter vs live CRM).
- One ID may be reordered, skipped, or superseded without the other
  (HubSpot must not block Salesforce; live must not ride along).
- The later ID should consume a **frozen** output of the earlier ID.
- The IDs need independent revertibility or independent review audiences.
- Combining would recreate an unbounded PR — the failure mode this
  document exists to prevent.
- The only argument is “they're small” or “we're already in the files.”
  Small and independent is `sequential` (or a later user-authorized
  combo), not an implicit combine.
- Independently authorized live units. Those are `do not combine` slices
  (`5A`, `5B`), not a rolled-up sequential Phase 5.

Over-serializing tightly coupled work is also a failure mode: many tiny PRs
that rewrite the same unfinished contract. Name a combined pass instead of
hoping the implementer notices.

#### Slices vs combined passes

| Device | Use when | Table treatment |
|---|---|---|
| **Slice** (`4A`, `4B`, `5A`, `5B`, or `1`) | The individual part of a phase; the row you land and grade | One commit-table row per slice; pass table lists each slice |
| **Phase assignment** (`Phase 4`) | Worker should take every remaining allowed slice of that phase in one turn; reject if that set is empty (`Phase 5` with only `do not combine` slices remaining) | Narrative `### Phase N` section; commit table still one row per slice, each with its own Status |
| **Combined pass** (`0A + 0B`) | Two or more slice IDs should land together even if they are different phases | Separate commit-table rows; one pass-table row; same SHA when they land |
| **`do not combine`** (`5A`, `5B`) | This one slice must never share a pass with any other ID and is not implied by the parent phase | One pass-table row; IDs cell has exactly that slice |
| **Do not** | Collapse two goals into one slice just to get one PR, keep a rolled-up live parent, or mark a parent “done” while slices remain | — |

---

## How to structure a strategy doc

Recommended sections for a package-local strategy:

1. **Status** — proposed / adopted; which **slices** are done. Include
   `**Current state:**` (path, paths, or `none`) next to `**Branch:**`.
2. **Phase implementation commits** — table above (always).
3. **Recommended implementation passes** — `same pass` / `sequential` /
   `do not combine` (always; fill at design time).
4. **Recommendation** — one-paragraph thesis and operator journey sketch.
5. **Lifecycle sketch** — operational applicability, typed source map, and
   concrete transitions with authority, retry/failure, and proof; reviewed
   before detailed expansion in Shuttle authoring.
6. **Why now / precedence** — what this supersedes and which context documents
   to update.
7. **Architectural principles** — ownership and fail-closed rules.
8. **Contracts / vocabulary** — only what later phases need.
9. **Phases** — one section per phase (template below). Each phase names
   `Implement with`.
10. **Safety / live gates** — explicit non-authorization of live work.
11. **Adoption sequence** — docs sweep before first code if needed.

### Per-phase section template

```markdown
### Phase N — Title

**Goal:** one sentence.

**Implement with:** same pass as 0B | sequential (alone) | do not combine
(global isolation of this one ID)

**In scope:**
- …

**Out of scope (explicit):**
- …

**Slices (when the phase has more than one slice):**
| Slice | Goal | Status |
|---|---|---|
| **NA** — … | … | not started \| implemented at `SHA` awaiting human grading \| checkpointed at `SHA` awaiting human grading \| accepted (`SHA`) awaiting publication \| done (`SHA`) \| SUPERSEDED |

**Status:** not started | implemented at `SHA` awaiting human grading | checkpointed at `SHA` awaiting human grading | accepted (`SHA`) awaiting publication | done (`SHA`) | SUPERSEDED
<!-- Single-slice phase only, where slice id = N. Do not collapse a
     multi-slice phase into one parent Status. -->

**Deliverables:**
- code / packages / routes / tests

**Acceptance (network-free unless noted):**
- …

**Commit:** `SHA` — `subject`  <!-- one line per slice; omit on a parent that has a Slices table -->
```

If the phase is sliced, the **top commit table must list every slice** even
when some are still `not started`, and each row carries its own Status.
Narrative detail lives in the Phase N section. Agents read the commit-table
Status column for lifecycle, the pass table for default grain, and the
normalized assignment (slice vs phase vs pass) for what this worker turn
covers. A parent `**Status:**` must not replace per-slice Status.

---

## Worked example: EasyImports CRM-neutral foundation

**Current state:** `mappings_2/codex_context/current_state.md`

**Authoritative design:**  
`mappings_2/codex_context/cross_agent_eval/implementation_notes.md`

**Thesis:** product owns survivor/loser intent and authorization; each CRM
integration owns native mechanism. Build neutral contracts and a fake consumer
path before Salesforce-shaped frontend merge UI; HubSpot must not block a
usable Salesforce operator path.

**Operator journey (product goal, after foundation):**

```text
Connect a CRM
-> choose provider
-> verify opaque connected account
-> choose Companies or People
-> upload candidates or find duplicates
-> review every group
-> freeze provider-neutral merge intent
-> authorize dry-run or execute
-> provider compiles and executes native operations
-> verify and report neutral outcomes
```

### Phase implementation commits (example)

| Slice | Status | Commit | Subject / scope |
|---|---|---|---|
| **0A** — Lock neutral contracts | done (`0e2056f6`) | `0e2056f6` | `Implement CRM-neutral Phase 0A contracts and Phase 0B fake CRM.` |
| **0B** — Fake CRM vertical slice | done (`0e2056f6`) | `0e2056f6` | Same commit as 0A (combined delivery). |
| **1** — API + Django connection lifecycle (fake-only) | done (`c6092039`) | `c6092039` | `Implement CRM-neutral Phase 1 connection HTTP and Django journey.` |
| **2** — Adapt Salesforce | not started | — | Not started. |
| **3** — Add HubSpot | not started | — | Not started. |
| **4** — Source branches + full review journey | not started | — | Not started. |
| **5A** — Salesforce live gates | not started | — | Not started. Provider slice required before any Phase 5 authorization. |
| **5B** — HubSpot live gates | not started | — | Not started. Provider slice required before any Phase 5 authorization. |

### Recommended implementation passes (example)

Designed up front, not invented when 0A and 0B happened to land together.
Phase 5 is sliced by provider at design time; there is no authorizable
rolled-up Phase 5 row.

| Pass | Slice IDs | Grain | Why |
|---|---|---|---|
| **P1** | 0A + 0B | same pass | Neutral contracts without a proving consumer will churn; fake CRM is the executable freeze. Same network-free risk class. |
| **P2** | 1 | sequential | Public HTTP + Django is a different risk class; do not mix with the contract freeze. |
| **P3** | 2 | sequential | Salesforce adaptation; do not wait for or combine with HubSpot. |
| **P4** | 4 | sequential | Review journey may precede HubSpot under delivery pressure; keep independently revertible. |
| **P5** | 3 | sequential | Second provider against the already frozen 0B contract. |
| **P6** | 5A | do not combine | Salesforce live gate is globally isolated; never implied by earlier phases; does not authorize 5B. |
| **P7** | 5B | do not combine | HubSpot live gate is globally isolated; 5A does not authorize it. |

```text
0e2056f6  Phase 0A + Phase 0B
c6092039  Phase 1
76b1a14d  docs: record Phase 1 SHA in strategy notes
```

Delivery order used by this initiative:

```text
0A contracts
-> 0B fake consumer contract
-> 1 fake API/Django connection journey
-> 2 Salesforce adaptation and regression
-> 4 upload/acquire-all and review (may precede HubSpot)
-> 3 HubSpot against frozen contract
-> 5A Salesforce live gates (separately authorized)
-> 5B HubSpot live gates (separately authorized; 5A does not authorize 5B)
```

---

### Legacy slice headings in this example

This worked example shipped as `### Phase 0A` and `### Phase 0B` headings.
Those ids and headings remain valid. They are **grandfathered legacy slice
sections**: a letter-suffixed `### Phase <id>` heading *is* the slice, not a
parent phase. A worker reads each heading's `**Status:**` as that slice's
lifecycle and does **not** rewrite the ids to a numeric `### Phase 0` parent.

New strategies use numeric `### Phase N` headings, one commit-table row per
slice, and a Slices table when the phase has more than one slice. Phase 5
below is the new form (numeric parent, 5A/5B as slice rows). Phases 0A and
0B are the grandfathered form. Phases 1–4 are single-slice numeric parents
(slice id equals the phase number).

---

### Phase 0A — Lock neutral contracts

**Status:** done (`0e2056f6`)

**Goal:** freeze CRM-neutral intent and connection/capability ownership without
public HTTP or live CRM access.

**Implement with:** same pass as 0B.

**In scope:**

- internal provider/connection protocol types and capability descriptors;
- private connection/tenant binding and credential-store boundaries;
- connection as durable parent of derived execution targets;
- survivor/loser intent (no product-level `native_merge` / `lead_conversion`);
- minimal neutral redirect and provider-evidence envelopes;
- workflow/checkpoint inventory and deliberate version bumps;
- focused contract tests; Salesforce characterization that native plans still
  compile after action typing moves to the integration.

**Out of scope:**

- Django screens;
- public `/v1/crm/*` or OpenAPI connection resources;
- fake provider implementation (that is 0B);
- live OAuth or mutation.

**Deliverables (example locations):**

- `mappings_2/integrations/crm/contracts.py`
- neutral plan/completion contract advances in product effects
- list-import / duplicate-resolution version bumps
- `tests/mappings_2/test_crm_phase0a_contracts.py`

**Commit:** `0e2056f6` — combined with Phase 0B.

---

### Phase 0B — Fake CRM vertical slice

**Status:** done (`0e2056f6`)

**Goal:** one network-free fake CRM that freezes the executable consumer
contract both real providers must satisfy.

**Implement with:** same pass as 0A.

**In scope:**

- connect / status / disconnect;
- company and person reference acquisition (candidate-ID and acquire-all);
- paginated acquire-all with restart;
- multi-loser pairwise execution;
- stale preflight, attributable failure, partial group failure;
- wrong-tenant rejection, lost-response / `paused_unknown`, reconcile, verify;
- dry-run zero mutation;
- real application / capability boundary (not a happy-path stub).

**Out of scope:**

- Django journey (Phase 1);
- separate fake Salesforce vs fake HubSpot unless testing a divergence;
- live CRM.

**Deliverables (example locations):**

- `mappings_2/integrations/crm/fake/`
- `tests/mappings_2/test_fake_crm_phase0b.py`

**Commit:** `0e2056f6` — combined with Phase 0A.

---

### Phase 1 — CRM-neutral API and Django connection lifecycle

**Status:** done (`c6092039`)

**Goal:** public connection HTTP + Django UI on the **fake provider only**.

**Implement with:** sequential. Do not combine with 0A/0B (different risk
class) or with Phase 2 (real adapter).

**In scope:**

- `GET /v1/crm/providers` (connectable catalog only);
- connection start / OAuth complete / status / disconnect;
- owner/session binding (`X-Owner-Session`);
- API-global mutation journal for connection mutations;
- Django connection page and server-to-server complete of the fake OAuth loop;
- OpenAPI + generated Django contracts (API `1.3.0`);
- leakage / replay / restart tests.

**Out of scope:**

- Salesforce or HubSpot in the public connectable catalog;
- dead “Connect” buttons for unconfigured providers;
- live OAuth to real tenants.

**Deliverables (example locations):**

- `mappings_2/api/connection_registry.py`, CRM routes in `mappings_2/api/app.py`
- `web/importer/connection_views.py`, `/crm/connections/`
- `tests/mappings_2/test_crm_phase1_connections.py`
- regenerated `web/importer/api_*_generated.py`

**Commit:** `c6092039` — `Implement CRM-neutral Phase 1 connection HTTP and Django journey.`

---

### Phase 2 — Adapt Salesforce

**Status:** not started

**Goal:** recompose existing Salesforce OAuth, target verification, reference
acquisition, and Account/Person merge engines behind the neutral contracts.

**Implement with:** sequential. Do not combine with Phase 3.

**In scope:**

- move remaining Salesforce-native decision out of neutral product code;
- regression proof: same safety behavior as existing synthetic tests;
- appear in connectable catalog only when adapter + config are available.

**Out of scope:**

- waiting for HubSpot;
- live production or customer data;
- live gates without separate authorization.

---

### Phase 3 — Add HubSpot

**Status:** not started

**Goal:** `mappings_2/integrations/hubspot/` against the **already frozen**
consumer contract from 0B.

**Implement with:** sequential. Do not combine with Phase 2; it should consume
the frozen 0B contract, not reopen it.

**In scope (first scope):**

- OAuth, immutable hub/account binding, safe status;
- Company and Contact acquisition and same-object merge;
- durable ledger, uncertainty, verification, neutral completion evidence.

**Out of scope (first scope):**

- HubSpot Lead semantics as Salesforce Lead;
- cross-object Person resolution unless separately designed;
- redefining the Phase 0B contract.

---

### Phase 4 — Source branches and complete review journey

**Status:** not started

**Goal:** upload candidate groups and acquire-all paths share one product
review and authorization state machine for every connected provider.

**Implement with:** sequential. May precede Phase 3. Cannot pull in 5A or
5B (`do not combine` isolation).

**In scope:**

- candidate upload contract and acquire-all progress/resume;
- survivor / change / skip;
- frozen plan, dry-run vs execute, terminal report;
- reuse existing review UI/engine rather than rebuilding it.

**Ordering note:** may follow Phase 2 before Phase 3 under delivery pressure,
without weakening the frozen contract.

---

### Phase 5 — Separately authorized live gates

Do not authorize a rolled-up Phase 5. Lifecycle is per slice in the table
below and in the commit-table Status column — not a collapsed parent
`**Status:**`. Naming `Phase 5` is a reject while 5A and 5B remain (`do not
combine`). Add a new provider slice row before authorizing any additional
provider. Never implied by earlier phases.

**Goal:** per-provider live acceptance with synthetic fixtures only. Each
provider is its own independently authorized slice. Within a slice, run in
order:

1. connection and exact tenant identity;
2. synthetic read;
3. synthetic reversible write canary;
4. synthetic duplicate dry-run;
5. synthetic company merge;
6. synthetic person merge where supported;
7. cleanup and independent verification;
8. only then, an explicitly approved operator run.

**Slices:**

| Slice | Goal | Status |
|---|---|---|
| **5A** — Salesforce live gates | The eight-step live sequence against a Salesforce synthetic tenant | not started |
| **5B** — HubSpot live gates | The eight-step live sequence against a HubSpot synthetic tenant | not started |

**Implement with:** do not combine (global isolation of each slice). Passing
5A does not authorize 5B. Do not attach either slice to any earlier phase.

Passing one provider does not authorize another. Production remains a separate
reviewed decision.

---

## Checklist for starting a new multi-phase initiative

- [ ] Write or adopt a package-local strategy with phased sections.
- [ ] Put `**Current state:**` near the top: a repository-relative path (or
      several), or `none` with a one-line reason.
- [ ] Put the **Phase implementation commits** table at the top.
- [ ] After naming phases, fill **Recommended implementation passes**
      (`same pass` / `sequential` / `do not combine`) with a one-line why
      on every combine or isolation. `do not combine` is global isolation of
      exactly one ID.
- [ ] Keep distinct goals as distinct IDs even when recommending one pass.
- [ ] If any phase will land as slices, list **every slice row** (`NA`, `NB`…)
      in that table with Status `not started` and Commit `—` until submitted —
      never a single fuzzy “phase 4 done” row while slices remain. Status is
      per slice (`not started` / awaiting grading / `done` / `SUPERSEDED`).
- [ ] If a phase has independent authorization boundaries (per-provider live
      gates, per-tenant, per-environment), slice them at design time. Do not
      authorize a rolled-up parent. Add a slice row before authorizing a new
      unit.
- [ ] Record supersede rules in the strategy that owned the superseded task,
      plus any surviving living context document in that area. A migrated area
      has no living diary to update; move a closed strategy's path to
      **Historical** in that area's `strategy_index.md`, and drop it from the
      `open_work.md` queue if it was listed.
- [ ] New initiative in a migrated area: add its strategy path to **Active**
      in that area's `strategy_index.md`. Path and one-line purpose only. Add
      it to **Queued** (or **Current**) in `open_work.md` only if it is in
      line to be worked. An area with no scoping documents keeps neither file.
- [ ] Phase 0-style work: contracts and fakes before public HTTP when the risk
      is identity or provider leakage. Prefer `same pass` for a contract freeze
      plus its first proving consumer.
- [ ] One slice, one named **phase** (all remaining allowed slices), **or**
      a strategy-named combined pass per authorized implementation turn unless
      the user expands or splits scope. `do not combine` slices are never
      implied by a parent phase name. Reject `Phase 5` (and any parent whose
      remaining slices are all `do not combine`). Adopting the pass table
      does not start work; the user must still name or confirm the pass.
- [ ] After each phase/slice/pass: commit, update each assigned slice's
      Status to awaiting human grading and its Commit cell (docs commit OK
      for SHA). Do not mark `done`.
- [ ] Network-free tests for every non-live phase; live gates only with explicit
      user authorization of that provider slice. Never recommend combining a
      live-gate slice with earlier work or with another provider's slice.

---

## Related paths

| Path | Role |
|---|---|
| `implementation_notes_strategy.md` (this file) | Repo-wide process and worked example |
| `mappings_2/codex_context/cross_agent_eval/implementation_notes.md` | Full CRM-neutral product strategy |
| `mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_agnostic_imports/phase0a_inventory.md` | Affected contracts / packages inventory |
| `CONTEXT_PILLARS.md` | Which context model an area uses, and the authorization rule for picking a task |
| `mappings_2/codex_context/reference/DUPLICATE_RESOLUTION_FRONTEND_LIFECYCLE.md` | Operator journey (after foundation) |
| `archon/implement_bootstrap.md` | Agent bootstrap: read state + strategy, confirm, then implement |
