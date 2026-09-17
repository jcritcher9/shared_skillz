# Repository context strategy



The **lean context model is the repository standard for every work area.**

There is no session handoff document and no living Markdown diary. Their

absence is the expected state, not a degraded mode. Do not create, restore,

or emulate a handoff.



Living documents:



- `current_state.md` — present-tense implemented truth

- `testing.md` — test-selection policy and lane commands

- `open_work.md` — scoping-document **queue** (Current + Queued), **only

  where indexable scoping documents exist**

- `strategy_index.md` — **catalog** of those documents, **only where they

  exist**



Every area keeps `current_state.md` and `testing.md`. Queue and catalog

depend on whether it has indexable scoping documents (defined below):



| Area | Scoping documents | Living files |

|---|---:|---|

| `mappings_2/` | 63 | `current_state.md` + `testing.md` + `open_work.md` queue + `strategy_index.md` |

| `metadata_deployer_project/` | 39 | `current_state.md` + `testing.md` + `open_work.md` queue + `strategy_index.md` |

| `archon/` | 5 | `current_state.md` + `testing.md` + `open_work.md` queue + `strategy_index.md` |

| `web/` | 0 | `current_state.md` + `testing.md` — no queue, no index |

| `AI_Engineer/` | 1 | `current_state.md` + `testing.md` + `open_work.md` queue + `strategy_index.md` |

| `deltarune_factory/` | 1 | `current_state.md` + `testing.md` + `open_work.md` queue + `strategy_index.md` |

| `revops_dashboards/` | 1 | `current_state.md` + `testing.md` + `open_work.md` queue + `strategy_index.md` |



An area with no **indexable** scoping documents has nothing to queue or index

and keeps **neither** `open_work.md` nor `strategy_index.md`. An empty queue

or empty index is not a deliverable for that case.



The lean model is in effect for every area. A tracked `handoff_doc.md` is a

defect, not a second living model.



## Context root



Living files sit in one **context root** per area. `agent_context/` is the

default for new areas (`agent_protocol/create-context-pillars.md`).

`codex_context/` remains valid for areas that already use it.

`agent_context` is a supplemental replacement for `codex_context`; both

names are the same role.



Resolve: use `agent_context/` if that directory exists, else

`codex_context/` if that exists, else `agent_context/` for a new area. Do

not write living files into both. Classify still inspects **either** name

for `handoff_doc.md` (defect if present) and `open_work.md` (queue if present).



A tracked `handoff_doc.md` under either context-root name is a defect. Do

not read it as authority, do not update it, and do not restore the retired

three-pillar session. `open_work.md` is a **queue of scoping documents**

(Current + Queued) where it exists. It is not a diary or phase ledger.



`project_implementations/completed_projects/context_handoff_open_work_retirement_strategy.md` is

the normative authority for the lean model, for the repository-wide retirement

of `handoff_doc.md`, and for the queue and index contracts. It controls the

retirement boundary whenever another routing document disagrees.



---



# Lean context model



The repository standard, for every area including every nested context root

(`agent_context/` or `codex_context/`).



There is no session handoff document and no living Markdown diary. Their

absence is the expected state, not a degraded mode. Do not create, restore,

or emulate a handoff.



Every area keeps `current_state.md` and `testing.md`. What else it keeps

depends on whether it has indexable scoping documents:



- **it has some** — it keeps `open_work.md` as the scoping-document **queue**

  and `strategy_index.md` as the **catalog**. See the two-file contract below.

- **it has none** — it keeps **neither** queue nor catalog. Their absence is

  correct and complete, not a missing deliverable. `web/` is this case.



## Where each kind of truth lives



| Need | Authoritative source |

|---|---|

| What exists now | The `current_state.md` named on the governing strategy's `**Current state:**` line (usually the area file) and its executable contracts |

| Test-selection policy | The `testing.md` sibling of that `current_state.md` (or the path on `**Testing:**`), and this file's `testing.md` section |

| Which scoping documents exist, and which still govern | The area's `strategy_index.md`, where the area has one |

| Which scoping document is current or next in line | The area's `open_work.md` queue, where the area has one |

| What an initiative is intended to deliver | Its scoped implementation strategy |

| Phase status and implementation evidence | The strategy's phase/commit table |

| What is authorized now | The user's current instruction, a confirmation of a worker / area-worker proposal, a structured task-queue entry, or a critic NOT ACCEPTED report in this turn (rem of those slices only) |

| Queued machine work | A structured task queue where the product already supports one |

| Worktree and branch state | Git status, diff, and log read at session start |

| Verification evidence | Test output, CI, commit history, and durable acceptance artifacts |



### `current_state.md`



**Owns:** what the system is today.



Use it for durable architecture and implemented behavior:



- purpose and supported workflows

- component responsibilities and boundaries

- current safety contracts

- supported types, routes, and public interfaces

- important limitations that affect how the system behaves



Do not use it as a chronological progress log, session diary, prioritized task

list, or backlog substitute. It describes implemented truth, not aspirations.

Test-selection policy and lane commands live in `testing.md`, not here.

Do not append a scoping document's slice or phase ids, grades, rem stacks, or

commit SHAs; those belong in that document's phase/commit table. When behavior

changes, rewrite the relevant description so the file continues to describe the

present rather than accumulating an implementation history.



Every scoping document must name that file (or `none`) in a `**Current

state:**` header line. Worker and critic stop if the line is missing both a

path and `none`. See `implementation_notes_strategy.md` (Current state

pointer) and `agent_protocol/worker.md`.



### `testing.md`



**Owns:** test-selection policy and lane commands.



Always created with the area, as a sibling of `current_state.md`. It is

not a catalog of test files or test cases. Concrete targeted selectors

are chosen from the assigned diff, prior findings, and shared invariants

at execution time. It is not architecture, not a queue, and not a grade.



Use it for:



- targeted vs comprehensive policy for this area

- the commands or command shapes that implement those lanes

- test-writing grain (smallest tests that prove the assigned change)



Do not use it as a test inventory, suite log, phase ledger, or copy of

`current_state.md`.



**Global default (every area inherits this):**



- **Write** the smallest tests that prove the assigned change.

- **Interpreter:** Never PATH `python` or `py` as the first test

  invocation. Interpreter and targeted-lane default live in

  `agent_protocol/kernel.md`. Missing `$py` is an environment stop, not a

  red suite: report the missing interpreter. Do not record the resolved

  absolute path in versioned docs or the handoff. Do not activate the

  venv. Do not create a `.venv` per worktree. Example command shapes use

  `<clone>/.venv/Scripts/python.exe -m pytest …` (Windows) or

  `<clone>/.venv/bin/python -m pytest …` (Unix), not `python -m pytest`.

- **Targeted** (implementer inner loop and first critic): follow the

  three-step lane and prior-finding deselect in `agent_protocol/kernel.md`.

  The implementer may run that selection. Red results are remediation, not

  a grade. Do not write grading analysis in the implementer handoff.

- **Comprehensive** is not the default during implementer/critic

  ping-pong. It is reserved for a later Shuttle critic lane after a

  landable targeted Grade A/A- ACCEPT with both finding arrays empty,

  for CI, or when the governing strategy names that envelope.

- Parent-commit comparison uses only the failing selector, never the

  area full suite.



Each area's file names **this** suite's commands. New scoping documents

also carry a `**Testing:**` header (path, several paths, or `none`) next

to `**Current state:**`. See `implementation_notes_strategy.md` (Testing

pointer). Existing documents without that header are grandfathered: read

the `testing.md` sibling of each named `current_state.md` (`none` → skip).



Example:



````markdown

# Testing



This file is the area testing pillar. It is not a test inventory. It is

not architecture and not a scoping-document queue. See

`CONTEXT_PILLARS.md` (`testing.md`).



## Default selection



Write the smallest tests that prove the assigned change.

Run the smallest command that can disprove it.



Targeted (implementer and first critic):

1. new tests for this diff

2. exact tests for prior grader findings

3. existing tests for invariants this diff actually shares



The implementer may run that targeted selection. Red results are

remediation, not a grade.



Do not run this area's comprehensive suite during implementer/critic

ping-pong. Parent comparison uses only the failing selector.



Typical targeted commands:



```text

<clone>/.venv/Scripts/python.exe -m pytest <area-tests>/test_<module>.py -q

<clone>/.venv/bin/python -m pytest <area-tests>/test_<module>.py -q

```



## Comprehensive



Reserved for a later Shuttle critic lane after a landable targeted

Grade A/A- ACCEPT with empty finding arrays, for CI, or when the

governing strategy names this envelope.



Command: none — name this area's suite here when one exists, invoked

via the clone `.venv` interpreter above.

````



### Scoped implementation strategies



**Own:** intended deliverables, phase order, authorization boundaries, and

acceptance criteria for one initiative.



Keep the status table concise. Detailed evidence belongs in tests, commits, and

purpose-built acceptance artifacts, not in a strategy turned into a session log.



### The two-file contract



An area can hold dozens of scoping documents. That is two jobs: **find every

document**, and **know which ones are in line to be worked**. Those jobs are

not the same file.



Both files exist only where the area has indexable scoping documents. `web/`

has none and keeps neither. Do not create either file speculatively; create

them with the first strategy.



### `strategy_index.md` — the catalog



**Owns:** where every area-local scoping document is. Nothing else.



It is an **index, not an authority**.



**This file exists only where there is something to index.**



It contains, and contains only:



- a header stating that the file is an index, is not a task source, and that

  listing does not authorize implementation;

- an **Active** section: repository-relative paths to scoping documents that

  still govern, each with a one-line purpose;

- a **Historical** section: repository-relative paths to completed or

  superseded scoping documents, each with a one-line purpose.



Paths are relative to the **repository root**, not to the index's own

directory. Every path must resolve from the repository root exactly as written;

a path that only resolves relative to the context root is a defect.



The two sections must be clearly separated and separately headed.



It must **not** contain: a current pointer; ordered upcoming work; phase

status, commit SHAs, or test results; acceptance criteria; deferred-work

narrative; or any prose describing what should be done.



Active versus historical says which document governs. It is not a queue

position. Being listed under **Active** means the strategy is live, not that

it is next and not that its unimplemented phases are authorized.



Example:



```markdown

# Strategy index



This file is an index, not a task source. Listing does not authorize

implementation.



## Active



| Strategy | Purpose |

|---|---|

| `mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_agnostic_imports/crm_agnostic_imports.md` | CRM-neutral import foundation |



## Historical



| Strategy | Purpose |

|---|---|

| `mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_review_clarity.md` | Duplicate review clarity; closed |

```



Maintaining the index at session end is an index-only edit: add a path, or move

one between Active and Historical.



### `open_work.md` — the scoping-document queue



**Owns:** which live scoping documents are in line to be worked, and which one

is current.



It contains, and contains only:



- a header stating that the file is the area's scoping-document queue, is not

  a diary or phase ledger, and that listing does not authorize implementation;

- a **Current** section: at most one repository-relative path plus a one-line

  purpose. Empty Current means nothing is in flight;

- a **Queued** section: an ordered list of upcoming scoping-document paths,

  each with a one-line purpose.



Every queue path must also appear under **Active** in the sibling

`strategy_index.md`.



It must **not** contain phase status, commit SHAs, test results, session

narrative, or a next *phase*. Phase status stays in the scoping document.



Listing a document in Current or Queued does not start work. It is the

candidate source for an area worker.



Example:



```markdown

# Open work



This file is the scoping-document queue for this area. Listing does not

authorize implementation.



## Current



| Strategy | Purpose |

|---|---|

| `metadata_deployer_project/codex_context/cross_agent_eval/project_implementations/in_progress/report_dashboard_flow_create_update_strategy/report_dashboard_flow_create_update_strategy.md` | Report/Dashboard/Flow create and update |



## Queued



| Strategy | Purpose |

|---|---|

| `metadata_deployer_project/codex_context/cross_agent_eval/project_implementations/in_progress/flexipage_structure_and_static_resources_strategy/flexipage_structure_and_static_resources_strategy.md` | FlexiPage structure and static resources |

```



Maintaining the queue at session end is a membership or order edit: set or

clear Current, add/remove/reorder Queued. Never write a diary, SHA, or test

result there.



### Area worker



An area-bound worker (`/mdp-worker`, `/archon-worker`, `/mappings-worker`,

or `/worker` given only an area):



1. Fetch origin. Classify from `origin/main` **before** parsing a queue:

   handoff present → **UNMIGRATED** (defect: stop; require a named document;

   do not parse `open_work.md` as Current/Queued and do not restore a

   handoff session); handoff gone and

   `open_work.md` present → **QUEUE**; handoff gone and `open_work.md`

   absent → **NO_QUEUE** (valid; report no queued document; do not create

   a file). Only **QUEUE** continues.

2. Enter the area **queue worktree**

   (`<parent>/worktrees/<repo>/<area-slug>-queue` on

   `agent/<area-slug>-queue`). Read `open_work.md` **there**. That is the

   only authoritative queue. Do not read `main`'s working tree and do not

   read a document worktree.

3. Take Current if present, else the first Queued row. If the queue is

   empty, stop and report.

4. Enter the **document** worktree for that filename and propose-and-wait

   the next schedulable target (`agent_protocol/worker.md`).

5. After confirmation, if the file came from Queued, claim it on the queue

   worktree (Current = that path, drop it from Queued), push the queue

   branch, and fast-forward the claim onto `origin/main` before implementing.

   If Current already named that document, do not edit the queue.



A second area-worker must see that claim. If Current is already set, it

proposes the next schedulable target of **that** document (and stops on an awaiting-grading

gap). It must not start a second document while Current is occupied.



Once `handoff_doc.md` is gone from `origin/main`, document branches must

not modify `open_work.md` or `strategy_index.md`. Queue and index membership

edits belong on the area **queue worktree**. This is a thin adapter, not a

second worker protocol. Archon product runtime must not scrape the queue

for a task.



### What counts as an indexable scoping document



A scoping document is **indexable by an area** when both hold:



1. **Area-local** — it lives inside that area's own tree (for example

   `mappings_2/codex_context/cross_agent_eval/project_implementations/*.md`),

   not in the repository-root `project_implementations/`.

2. **Area-owned and durable** — it governs that area's own work and persists

   after its phases land, as a live or historical record of that initiative.



A **repository-wide** strategy is not indexable by any area, even while it

governs that area. `project_implementations/completed_projects/context_handoff_open_work_retirement_strategy.md`

governs all four areas through R1-R6, but it is found from the repository root,

not by asking a single area which of *its* strategies applies. Counting it would

force every area to keep an index forever merely because a cross-cutting

initiative exists, which defeats the rule.



By this definition `web/` has **zero** indexable scoping documents and keeps

neither `open_work.md` nor `strategy_index.md`, while `mappings_2` has 63,

`metadata_deployer_project` 39, `archon` 5, `AI_Engineer` 1,

`deltarune_factory` 1, and `revops_dashboards` 1.



A tracked `handoff_doc.md` is a defect. Do not read it as authority.



## Authorization rule



An agent may implement only:



- a bounded task the user stated in the current instruction; or

- a strategy **slice**, **phase**, or **pass** the user named in the current

  instruction, normalized before dispatch (`agent_protocol/worker.md`):

  slice → that slice only; phase → remaining unfinished slices of that phase

  except `do not combine`; pass → exactly that pass-table row. A phase that

  expands to zero slices (Phase 5 when every remaining slice is

  `do not combine`) is a reject, not a combined pass; or

- a confirmation of a worker / area-worker proposal; or

- an entry supplied by a structured task queue the product already supports; or

- a critic report in this turn that grades assigned slices NOT ACCEPTED /

  Grade B or below — that report authorizes remediation of those slices

  only (`agent_protocol/worker.md`); it does not authorize a later slice.



Unimplemented strategy phases describe possible future work. They are **not

self-authorizing**. An agent must not infer a next *phase* from prose merely

because the prose appears first, is marked open or "not started", or follows the

last completed phase. Adopting or writing a strategy never authorizes an

implementation pass. Adopting a pass table approves its grain; it does not

start work. The user must still name or confirm the pass (or a slice or

phase).



An area worker may propose from a **migrated** `open_work.md` queue only after

the `origin/main` classify step (UNMIGRATED / QUEUE / NO_QUEUE). It must not

parse a diary or phase ledger as Current/Queued. On QUEUE it proposes Current

(else first Queued) and then the next schedulable target, and stops until the

user confirms. Auto-starting from the queue is forbidden. Scanning diary

prose for a plausible next item is still forbidden.



## Prohibited replacements



Do not introduce, under any name, a document that serves the retired

living-diary role. This includes `handoff.md`, `next_work.md`, `backlog.md`,

`session_state.md`, and parallel progress or gap trackers — and it includes

letting `open_work.md` drift back into a diary or phase ledger. The lean

`open_work.md` queue is not that role. Do not retain runtime symbols, payload

fields, artifact names, source labels, help text, or configuration keys named

for a handoff document, nor any named for auto-starting a phase from

`open_work.md`. Do not add a compatibility alias, redirect, symlink, empty

placeholder, dual-read, or fallback reader for a handoff document. Archon

must not scrape the queue or the index for a task.



## Session lifecycle



At the start of work, read only:



1. the `current_state.md` named on the governing strategy's

   `**Current state:**` line (skip if that line is `none`; if the line is

   missing both a path and `none`, stop and ask — do not guess);

2. the `testing.md` named on `**Testing:**`, or the sibling of each named

   `current_state.md` when that header is absent (`none` → skip);

3. the governing strategy for the authorized or proposed task. If classify

   is **QUEUE**, consult `strategy_index.md` and `open_work.md` from the

   area **queue worktree** (`agent/<area-slug>-queue`), not from a document

   branch and not from a dirty `main` checkout. Do not create or enter a

   queue worktree on UNMIGRATED or NO_QUEUE. An area with no indexable

   scoping documents has neither queue nor catalog, and `current_state.md`,

   `testing.md`, and the user's instruction are the whole starting set;

4. repository instructions;

5. live Git state (`git status`, `git diff`, `git log`).



At the end of work, update durable architecture in `current_state.md` when

behavior changed, update `testing.md` when this area's test commands or

selection changed, and update the governing strategy's status/commit record when

a slice landed — those edits belong on the **document** branch. Queue and

index membership edits belong on the **queue** worktree, then fast-forward

onto `origin/main`: claim Current when a Queued document is confirmed; clear

Current on document closeout when every slice is `done` or `SUPERSEDED` (do

not auto-promote Queued into Current). After a land, fast-forward the queue

branch onto the new `origin/main` **before** committing that clear. Do not

commit queue or index edits on `agent/<doc-slug>` after that area's

`handoff_doc.md` is gone from `origin/main`. Where the area has

neither file, there is nothing to update and none should be created.

Ephemeral continuity belongs in the conversation and in Git, not in a diary.



## Historical-reference exception



Historical records may say that the retired documents once existed. Completed

strategy records, `archived/` directories, `_old_structure/`, and migration or

supersession descriptions are permitted to contain the retired names as

non-normative history, and must not be rewritten merely to erase terminology.



The exception covers **history only**. It does not cover any file that an

active tool or instruction reads as authority.



## Active-reference search gate



Before declaring a lean area free of the retired roles, run a

tracked-reference search that is independent of test collection:



```powershell

# Handoffs must be gone entirely, repository-wide.

git ls-files |

  Where-Object { [IO.Path]::GetFileName($_) -eq 'handoff_doc.md' }



# Open-work files must be exactly the planned queues, and no others.

# web/ must not appear. AI_Engineer/ is lean-from-birth and is a planned

# queue/catalog pair, not an R1–R6 leftover.

git ls-files |

  Where-Object { [IO.Path]::GetFileName($_) -eq 'open_work.md' }



# Strategy indexes must be exactly the planned catalogs, same areas as the queues.

git ls-files |

  Where-Object { [IO.Path]::GetFileName($_) -eq 'strategy_index.md' }



# Word-anchored so unrelated identifiers such as open_workbook() stay out.

git grep -n -i -E "\bhandoff_doc\b|\bopen_work\b|\bstrategy_index\b" -- .

```



The first command must print nothing. The second must print exactly the planned

queue paths, each checked against the queue contract. The third must print

exactly the planned index paths, each checked against the index contract. A

surviving diary and a surviving queue look identical to `git ls-files`.



The `git grep` is a **review gate, not a zero-match rule**. Every remaining

match must receive an explicit disposition as one of:



1. active in-scope surface reading a handoff, or reading `open_work.md` as a

   diary, phase ledger, or auto-start source — a failure that must be removed;

2. active in-scope surface treating `open_work.md` as the scoping-document

   queue, or `strategy_index.md` as the catalog — permitted;

3. non-normative history — recorded and left alone;

4. unrelated substring match (for example `open_workbook`) — recorded.



Treat every tracked instruction, command, prompt, configuration, runtime path,

schema, fixture, test, template, bootstrap, and agent-routing document as

**active until inspection proves otherwise**. This class includes hidden roots

such as `.archon/`, non-collected modules under `tests/`, repo-root instruction

files, and references that live outside a scoped root but point into one. A path

is never safe merely because it is hidden, sits outside a project root, or is

not collected by a test runner.



---



# Historical documents



Design proposals, field diaries, completed progress logs, and superseded task

lists may remain useful background. Move them under an `archived/` directory or

mark them clearly as historical.



Archived documents must not override the living lean documents. A local README

or thin strategy file should route optional historical reading explicitly.

