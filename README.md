# shared_skillz

A portable slice of the mill: runtime-neutral `agent_protocol` bodies plus
thin Claude / Grok / Codex adapters. Drop this tree into a git repo (or
clone it as the repo) so agents share one worker, critic, worktree, and
scoping-document contract.

This is **not** Shuttle, Salesforce skills, or area binders. It does include
the Django operator UI under `web/` and FastAPI adapter thoughts under `api/`.
Pins for those two folders are in `DEPENDENCIES.md`.

## What is in the package

| Skill / file | Why it is here |
|---|---|
| `agent_protocol/` | Normative role files. Adapters only point here. |
| `/create-scoping-doc` | Write a phased strategy with commit + pass tables. |
| `/create-context-pillars` | Scaffold `current_state.md`, `testing.md`, and the queue/catalog when needed. |
| `implementation_notes_strategy.md` | Designer rules the scoping-doc skill follows. Do not restate them in the skill. |
| `CONTEXT_PILLARS.md` | Lean living-doc model the pillars skill follows. |
| `/worker` | Implement one normalized target in a document worktree; emit the handoff. |
| `/critic` | Independent grade; emit the critic report. |
| `/worktree` | Create or attach the 1:1 document worktree/branch. |
| `/worker-critic-loop` | Lightweight orchestrator: spawn worker, then critic, until Grade A / A−. |
| `/accept-and-land` | After Grade A / A−, record `done` and land on local `main` without retiring the worktree. |
| `/closeout` | When every slice is `done`, land the document and retire the worktree. |
| `/create-agent-skill` | Add another runtime-neutral skill the same way. |
| `agent_protocol/kernel.md` | Shared status vocabulary, `$py`, I/P identity. Worker and critic both read it. |
| `agent_protocol/worker-area.md` | Area-only `/worker` dispatch (queue propose-and-wait). |
| `web/` | Django operator UI (`web/manage.py`). Tracked source only: no `db.sqlite3`, no `media/` session CSVs. Living docs are in `web/codex_context/`. The UI talks HTTP to a FastAPI process. |
| `api/` | Vendor-agnostic thoughts behind `mappings_2/api`. FastAPI is the HTTP framework. Not a copy of the Python package. |
| `DEPENDENCIES.md` | Pins and roles for `web/` and `api/` (`requirements-api.txt` for the FastAPI stack). |
| `scripts/` | Local start/stop: one Python for the stack, two windows, `/health` wait, orphan sweep. Path C / HubSpot launchers are not included. |

`accept-and-land` and `closeout` are included because `/worker` and
`/worker-critic-loop` stop at “propose landing”; they do not land unless
those skills exist.

Shuttle mentions in the protocol files are mill history. Friends without
Shuttle ignore those clauses.

## How friends install it

Clone into their project **or** copy these paths so the adapter pointers
stay valid (`../../../agent_protocol/<name>.md` from each skill directory):

```text
agent_protocol/
implementation_notes_strategy.md
CONTEXT_PILLARS.md
web/
api/
scripts/
DEPENDENCIES.md
requirements-api.txt
.claude/skills/<name>/SKILL.md
.grok/skills/<name>/SKILL.md
.agents/skills/<name>/SKILL.md
.agents/skills/<name>/agents/openai.yaml
```

Then open the repo in Claude Code, Grok, or Codex. Skills are discovered
from those directories. Do not copy a role body into a runtime folder —
two copies diverge.

Local UI (from repo root):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1
```

Typical mill-skill flow:

1. `/create-context-pillars` on a new area
2. `/create-scoping-doc` for the initiative
3. `/worktree` (or let `/worker` create-or-attach)
4. `/worker-critic-loop` (or `/worker` then `/critic` by hand)
5. `/accept-and-land` after Grade A / A−
6. `/closeout` when the document is finished

## Layout rule

Protocol files have **no YAML frontmatter** and **no** `.claude` / `.grok`
/ `.agents` paths. Runtime stubs are frontmatter plus a pointer. Edit
`agent_protocol/<name>.md` when the role changes.

Init read-sets (doers start here; do not send them to this README):

| Role | Init read-set |
|---|---|
| Worker | `worker.md` + `worktree.md` + `kernel.md` |
| Critic | `critic.md` + `kernel.md` |
| Area worker | `worker-area.md` + `worktree.md` + `kernel.md` |
| Accept-and-land | `accept-and-land.md` |
| Closeout | `closeout.md` + `accept-and-land.md` |
| Worktree | `worktree.md` |

Source of that table: `agent_protocol/read_set.py`.

## Collaborators

The GitHub repo is currently **public**:
https://github.com/jcritcher9/shared_skillz

To add friends as write collaborators: GitHub → Settings → Collaborators
(the `/settings/access` page). Invites are sent by GitHub username or email.
