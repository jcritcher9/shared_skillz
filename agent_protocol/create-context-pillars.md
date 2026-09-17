# Create context pillars

Write or align the lean living documents from `CONTEXT_PILLARS.md` in a
directory the user names, under the default area schema below. This turn
only creates missing living files and checks existing ones. It does not
overwrite a present file, implement a slice, create a worktree, or write a
scoping document (`agent_protocol/create-scoping-doc.md`).

The skill is **idempotent**. A context root that already has pillars is
not a stop: check that those files still match `CONTEXT_PILLARS.md`,
create a baseline `testing.md` if it is missing, and leave aligned files
alone.

## Authority — read, do not copy

Read `CONTEXT_PILLARS.md` before asking questions. Follow the lean
context model. Do not restate its ownership tables, examples, or
authorization rule here.

New directories take the lean model. Do not write `handoff_doc.md` or any
document listed under Prohibited replacements.

## 1. Gather — one question at a time

Ask as ordinary conversation. Do not use structured option prompts for
free-text. Skip any question the user already answered.

1. **Directory.** Repository-relative path. Use the argument if given.
   Resolve the **context root** per `CONTEXT_PILLARS.md` (Context root) and
   wait if you had to guess. If the argument is already under
   `agent_context/` or `codex_context/`, that context root is the home.

   Default schema for a new area — same tree as
   `mappings_2/codex_context/cross_agent_eval`, with `agent_context` as
   the context root:

   ```text
   <area>/agent_context/                                  # living files
   <area>/agent_context/cross_agent_eval/project_implementations/
   ```

   Living files go in the context root. Always create
   `<context-root>/cross_agent_eval/project_implementations/` if it is
   missing. Do not copy `extra_skills/` or sample notes from
   mappings_2. Do not create `completed_projects/` empty.
2. **Indexable scoping documents.** Apply `CONTEXT_PILLARS.md` (What
   counts as an indexable scoping document) to that area. List any you
   find. If none, do not create `open_work.md` or `strategy_index.md`.
   `/create-scoping-doc` creates those with the first area-local strategy,
   under `<context-root>/cross_agent_eval/project_implementations/`.
3. **Present truth.** If drafting a missing `current_state.md`, inspect
   implemented code or docs. If that is not enough, ask for purpose, then
   any missing workflows, boundaries, or limitations. Test commands belong
   in `testing.md`, not in `current_state.md`.

If `handoff_doc.md` is present, that is a defect. Stop and report. Do
not write a lean queue or catalog beside a handoff, and do not restore
the retired session model.

## 1b. Align existing files — do not overwrite

If any of `current_state.md`, `testing.md`, `open_work.md`, or
`strategy_index.md` already exists, **keep it**. Check alignment against
`CONTEXT_PILLARS.md` and report the result. Do not rewrite an aligned
file. Do not "fix" a misaligned file in this turn unless the user asks.

Checklist (stop and report on a defect other than missing `testing.md`):

- `handoff_doc.md` absent.
- `current_state.md` present (or will be drafted). Present-tense
  architecture: purpose, workflows, boundaries, contracts, surfaces,
  limitations. Not a queue, catalog, phase ledger, or test-selection
  doc. No append-only slice/grade/SHA diary.
- `testing.md` present, or missing (create a baseline; not a defect).
  If present: test-selection policy and lane commands for this area; not
  a test catalog, architecture copy, or suite log.
- Indexable strategies exist → both `open_work.md` and
  `strategy_index.md` exist. None exist → **neither** file. One without
  the other is misaligned.
- `open_work.md`, when present: `## Current` and `## Queued` only; no
  phase status, commit SHAs, or test results; paths are
  repository-relative and also listed **Active** in the sibling catalog.
- `strategy_index.md`, when present: **Active** and **Historical**
  only; an index, not a task source; repository-relative paths.

A missing `testing.md` is a gap this skill fills. A missing
`current_state.md` is drafted as today. A missing queue/catalog pair is
created only when this area already has indexable scoping documents.

## 2. Draft — wait

Draft **only files that will be created**. Show the context root, the
eval tree, alignment findings for files that already exist, and every
new file body. Do not write until the user approves.

Draft a missing `current_state.md` from present truth: purpose and
supported workflows, component boundaries, safety contracts, public
interfaces, and limitations (`CONTEXT_PILLARS.md`, `current_state.md`).
It describes implemented behavior, not a plan.

Draft a missing `testing.md` from the example in `CONTEXT_PILLARS.md`
(`testing.md`). Fill **Comprehensive** with this area's suite command
when one exists; otherwise leave `Command: none`. Do not copy
architecture into it.

If writing the catalog and queue, draft them from the examples in
`CONTEXT_PILLARS.md` (`strategy_index.md` — the catalog, `open_work.md` —
the scoping-document queue). Paths are repository-relative. Put live
strategies under **Active** and completed or superseded ones under
**Historical**. Leave **Current** and **Queued** empty unless the user
already named which documents are in line.

If every required file exists and aligns, report that, create nothing,
and skip to Confirm.

## 3. Write

Create `<context-root>/cross_agent_eval/project_implementations/` if
missing. Create only the approved **missing** living files in the context
root. Never overwrite a file that already exists.

## 4. Confirm

Read back the context root, every directory created, every path written,
every existing file checked, alignment findings, and which files were
skipped (already present and aligned, no indexable strategies, or
unmigrated handoff). Creating these files does not authorize
implementation (`CONTEXT_PILLARS.md`, Authorization rule).
