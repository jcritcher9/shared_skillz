# Create an agent-agnostic skill

Write one runtime-neutral body and a thin adapter in each runtime skill
directory that already exists. Never copy the body into an adapter. Skip
any mapped runtime whose skills directory is absent. Do not create a runtime
skills directory from scratch.

This skill may write a protocol-role body, a non-protocol body, or only
area-binder adapters. It does not invent worktree-protocol roles. Do not
add a row to the init read-set table below (or to `read_set.py`) unless
the user is defining such a role and asks.

## 1. Gather — one question at a time

Ask as ordinary conversation. Do not use structured option prompts for
free-text. Skip any question the user already answered.

1. **Name.** Lowercase letters, digits, and hyphens; 2–64 characters; must
   start and end with a letter or digit. Validate before continuing.
2. **Kind.** Protocol role, area binder, or other. Protocol roles are
   `worker`, `critic`, `worktree`, `accept-and-land`, `closeout`, and
   `worker-area`. Area binders only bind an area; they are optional sugar.
3. **Home** (other skills only). Default `agent_protocol/<name>.md`. Ask
   which home when the user is writing a Salesforce data-editing skill
   (`standard_practice.md`) or an account-files skill:

   | Kind | Protocol file |
   |---|---|
   | Protocol role | `agent_protocol/<name>.md` |
   | Area binder | none — adapters only; point at `agent_protocol/worker-area.md` |
   | Other (default) | `agent_protocol/<name>.md` |
   | Salesforce data-editing | `agent_protocol/salesforce_skills/<name>.md` |
   | Account-files | `sf_agent_protocol/<name>.md` |
4. **What it should do.** The workflow, a repeated prompt, or the task to
   automate. Area binder: also the area root (repository-relative path).
5. **Description.** Draft the adapter `description`: what the skill does,
   trigger phrases, and `Use when the user runs /<name>`. Show it and wait
   for approval or replacement wording.

If that protocol file (when the kind writes one) or any adapter path for
that name already exists, stop and ask. Do not overwrite.

## 2. Protocol file

Skip this section for area binders.

Create the file at the chosen home:

- Markdown body only. No YAML frontmatter.
- No runtime-specific paths (`.claude/`, `.grok/`, `.agents/`, `.codex/`).
- Operating instructions the agent will follow, not documentation.
- Point at existing protocol files instead of restating them.
- One home per fact. Prefer existing CLI tools over new helper scripts.
  If a helper is required, colocate it with the body so every runtime
  reaches the same file.
- Salesforce data-editing bodies follow
  `agent_protocol/salesforce_skills/standard_practice.md`.

Never copy the body into an adapter.

## 3. Adapters

For each runtime skills directory that exists, write
`<skills-dir>/<name>/SKILL.md`. Skip any directory that is absent. Do not create a runtime skills directory from scratch.

| Runtime | Skills directory |
|---|---|
| Claude Code | `.claude/skills` |
| Grok | `.grok/skills` |
| Codex | `.agents/skills` |

Choose a short `#` title from the user's description, not the hyphenated
name. `<Runtime>` and `<skills-dir>` come from that table.

Grok adapter only: add `argument-hint` in the frontmatter when the skill
takes a short argument.

Codex adapter only: also write `<skills-dir>/<name>/agents/openai.yaml`
from the Codex yaml template below.

`<Display>` is the hyphenated name with hyphens turned into spaces and
the first word capitalized. `short_description` is one line taken from
the approved description.

Use one stub wording on every runtime. Put “must not be edited in place”
on every runtime; do not keep a Claude-only variant.

Include the matching runtime create/enter row **only** when the new skill
is a protocol role or an area binder. Omit it for every other skill, even
when the body creates a worktree. Non-protocol skills must not tell the
agent to enter a worktree as an implementer unless that body says to
enter.

Protocol-role stubs carry the role's `Init read-set:` line from the table
below. Area-binder stubs carry the Area worker line. Non-protocol stubs
omit the read-set footer. Do not send a generated stub to README for an
init read-set or for create/enter.

A generated stub must not cite a file outside that role's read-set.

### Init read-set (copy of the role map)

| Role | Init read-set |
|---|---|
| Worker | `worker.md` + `worktree.md` + `kernel.md` |
| Critic | `critic.md` + `kernel.md` |
| Area worker | `worker-area.md` + `worktree.md` + `kernel.md` |
| Accept-and-land | `accept-and-land.md` |
| Closeout | `closeout.md` + `accept-and-land.md` |
| Worktree | `worktree.md` |

Machine source: `agent_protocol/read_set.py` `ROLES`. Do not invent a
read-set. A new protocol role the user asked to register gets a line they
supply; update `read_set.py` in the same change if they also asked to add
the row.

### Create/enter rows

Insert the row for the adapter's runtime. These rows live in the adapter,
not in README.

#### Claude Code

```text
Create: plain `git worktree add`. Do **not** use `EnterWorktree` or
`isolation: "worktree"` to create — both force `.claude/worktrees/`.
`worktree.baseRef` is irrelevant; the block passes the base explicitly.
Enter: `EnterWorktree(path:)` to enter an **existing** path. Spawn agents
**unpinned**. An agent pinned at launch (`isolation`, or an explicit cwd)
can only enter `.claude/worktrees/`. A session that has already entered a
worktree is under the same restriction: `ExitWorktree` first, then enter
the next.
```

#### Grok

```text
Create: plain `git worktree add`. Do **not** pass `spawn_subagent`
`isolation: "worktree"`. Enter: `cd`, or `spawn_subagent` `cwd` set to the
worktree path. An explicit cwd is the correct entry, not a pin. A grader
launched with cwd already set to the implementer's worktree is already
entered; grade there.
```

#### Codex

```text
Create: plain `git worktree add`. Enter: `cd`, or start the session with that working directory. An explicit working directory is the correct entry, not a pin.
```

### Protocol-role stub

```markdown
---
name: <name>
description: <approved description>
---

# <short title>

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/<name>.md`**

Read that file now and follow it exactly.

<create-enter-row>

Init read-set: <read-set-line>.

This stub exists only because <Runtime> discovers skills at
`<skills-dir>/<name>/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/<name>.md`.
```

### Worker stub

When `<name>` is `worker`, use this pointer and the two read-set lines
instead of the generic protocol-role pointer. The area-only routing
sentence sends `/worker <area>` to the same `worker-area.md` body the
binder points at.

```markdown
---
name: worker
description: <approved description>
---

# <short title>

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file. Area-only argument (an area name and
no scoping-document filename) → **`../../../agent_protocol/worker-area.md`**;
otherwise → **`../../../agent_protocol/worker.md`**. Read that one file now
and follow it exactly.

<create-enter-row>

Init read-set: `worker.md` + `worktree.md` + `kernel.md`.
Init read-set (area-only): `worker-area.md` + `worktree.md` + `kernel.md`.

This stub exists only because <Runtime> discovers skills at
`<skills-dir>/<name>/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/worker.md` or `agent_protocol/worker-area.md`.
```

### Area-binder stub

Do not write a new protocol file. Point at `worker-area.md`; do not copy
it. Binders are optional sugar; do not mint one per QUEUE area.

```markdown
---
name: <name>
description: <approved description>
---

# <short title>

Area root: `<area>`

This is an area-only assignment for that root. Read
**`../../../agent_protocol/worker-area.md`** now and follow it exactly.

<create-enter-row>

Init read-set: `worker-area.md` + `worktree.md` + `kernel.md`.

`/worker <area>` is the generic area path. The worker stub routes an
area-only argument to the same body. Binders are optional sugar.

This stub exists only because <Runtime> discovers skills at
`<skills-dir>/<name>/SKILL.md`. It only binds the area. It carries no
instructions of its own and must not be edited in place. Any change to
worker behavior belongs in `agent_protocol/worker-area.md`.
```

### Non-protocol stub

No create/enter row. No `Init read-set:` footer. Replace the pointer
target with the chosen home (`agent_protocol/<name>.md`,
`agent_protocol/salesforce_skills/<name>.md`, or
`sf_agent_protocol/<name>.md`).

```markdown
---
name: <name>
description: <approved description>
---

# <short title>

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../<home-path>`**

Read that file now and follow it exactly.

This stub exists only because <Runtime> discovers skills at
`<skills-dir>/<name>/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`<home-path>`.
```

### Salesforce-home extra

When the home is `agent_protocol/salesforce_skills/<name>.md`, append
after “Read that file now and follow it exactly.”:

```markdown
Its supporting Python mechanics live next to it at
`agent_protocol/salesforce_skills/<name>.py` when `standard_practice.md`
requires one.

See `../../../agent_protocol/salesforce_skills/standard_practice.md` for
the conventions these Salesforce data-editing skills follow.
```

### Codex yaml

```yaml
interface:
  display_name: "<Display>"
  short_description: "<one line>"
  default_prompt: "Use $<name>."
```

## 4. Confirm

Read back the protocol file (when written) and every adapter you wrote.
Tell the user the slash command `/<name>`, which adapters were written,
and which mapped runtimes were skipped because their skills directory was
missing.
