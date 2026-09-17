# Create a scoping document

Write a new strategy markdown file that satisfies
`implementation_notes_strategy.md`. This turn designs and writes the document.
It does not implement a slice and does not create a worktree.

## Authority — read, do not copy

Read these before asking questions. Follow them. Do not restate their tables,
templates, or status vocabulary in this file or in the document's prose.

- `implementation_notes_strategy.md` — designer rules, current-state and
  testing pointers, commit table, pass table, section list, per-phase
  template, compatibility, documentation hierarchy
- `CONTEXT_PILLARS.md` — file home, what is indexable, queue vs catalog,
  authorization (writing a strategy does not start work)
- `agent_protocol/README.md` §4 — a scoping document has `### Phase` headings;
  `**Branch:**` and `**Base commit:**` are first-worker fields
- `agent_protocol/worktree.md` — filename slug (no phase id in the name)

## 1. Gather — one question at a time

Ask as ordinary conversation. Do not use structured option prompts for
free-text. Skip any question the user already answered.

1. **Home.** An area that has `agent_context/` or `codex_context/`, or
   repository-root `project_implementations/` for a repo-wide strategy.
   New areas use `agent_context/` (`agent_protocol/create-context-pillars.md`).
2. **Path.** Propose a repository-relative `*.md` path:
   - area-local:
     `<area>/<context-root>/cross_agent_eval/project_implementations/<slug>.md`
     (`agent_context` by default; existing `codex_context` if that area
     has no `agent_context/`)
   - repo-wide: `project_implementations/<slug>.md`
   The slug is the filename minus `.md` and must be a valid worktree slug.
   If that path already exists, stop and ask. Do not overwrite.
3. **Initiative.** Goal, in/out of scope, and what this supersedes. If the
   user already pasted a draft, use it.
4. **Current state and testing pointers.** Propose `**Current state:**`
   path(s) or `none` using `implementation_notes_strategy.md` (Current
   state pointer). Propose `**Testing:**` in the same turn: the
   `testing.md` sibling of each named `current_state.md`, or `none` with
   the same reason when current state is `none`. Wait for confirmation.
   Do not write either line until confirmed.

Then read the named `current_state.md` and `testing.md` (if any) and nearby
live strategies so phases describe present truth, not a parallel plan. If
`testing.md` is missing next to a named `current_state.md`, say so; do not
create it here (`agent_protocol/create-context-pillars.md`).

Build a compact source map before detailed prose: original requirements, known
concerns, relevant source owners, existing public commands/APIs, and actual
prerequisite or competing strategies. The map guides inspection and does not
replace independent source reading.

## 2. Propose the ledger — wait

Design per `implementation_notes_strategy.md` (When designing a scoping /
strategy doc). New documents use numeric `### Phase N` headings, not legacy
`### Phase 0A` slice headings.

Show, then wait for approval or edits:

- the path
- the confirmed `**Current state:**` line
- the confirmed `**Testing:**` line
- the commit table (one row per slice, all `not started`)
- the pass table (every slice id in exactly one row; `why` on `same pass`
  and `do not combine`)
- one-line goal per phase/slice

Do not write the file until the user approves that ledger.

## 3. Write the document

Create the approved path.

Take required shape from `implementation_notes_strategy.md` (How to structure
a strategy doc, Phase / commit table, Recommended implementation passes,
Per-phase section template):

- document-level `**Status:**` `proposed` unless the user already adopted it
- confirmed `**Current state:**` and `**Testing:**` near the top
- both top tables, filled at design time
- a `### Phase N` section for every parent phase
- a related-paths table naming `implementation_notes_strategy.md`, the
  current-state files, testing files, superseded docs, and any auxiliary
  file this document creates, each with why it exists and whether it is
  normative

Before expanding detailed phase prose, write a bounded `## Lifecycle sketch`
per `implementation_notes_strategy.md`. Operational strategies trace accepted
work through publication, binding, dispatch, evidence ingest, and the next gate;
non-operational strategies record why those transitions do not apply. A future
capability names its implementing slice and acceptance proof and cannot supply
the evidence that authorizes itself. When Shuttle authors the document, its
first critic grades this sketch milestone before the expansion worker proceeds.

Do not write `**Branch:**` or `**Base commit:**`. The first worker records
those (`agent_protocol/README.md` §4.1).

If this document supersedes a prior next task, record that here and update the
surviving living context files named by `implementation_notes_strategy.md`
(When implementing or closing, item 7) and `CONTEXT_PILLARS.md`.

## 4. Register

Follow `CONTEXT_PILLARS.md` (What counts as an indexable scoping document, the
two-file contract).

- **Indexable (area-local):** add the path under **Active** in that area's
  `strategy_index.md`. Create the catalog and queue with this first strategy
  if they do not exist. Default queue placement is **Queued**, not **Current**,
  unless the user says this document is in flight. After the area has
  migrated, make those edits on the area queue worktree, not on a document
  branch (`agent_protocol/worker.md`).
- **Not indexable (repo-wide, or an area with no area-local strategies):** do
  not add it to any area catalog. Do not create `open_work.md` or
  `strategy_index.md` speculatively.
- Do not create, restore, or update `handoff_doc.md`. A tracked handoff is a
  defect. Queue membership is Current/Queued only; listing does not
  authorize work.

## 5. Confirm

Read back the path, `**Current state:**` line, `**Testing:**` line, slice
ids, pass grain, and every index or queue file touched. Adopting the pass
table is not authorization to implement (`CONTEXT_PILLARS.md`,
Authorization rule).
