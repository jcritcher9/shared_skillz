---
name: accept-and-land
description: Record critic Grade A / A− slices as done and land those commits onto local main without retiring the document worktree or branch. Use when the user runs /accept-and-land, or asks to land critic-ACCEPTED slices. A worker that receives Grade A proposes this skill and waits; it does not run it automatically.
argument-hint: scoping-doc-filename
---

# Accept and land

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/accept-and-land.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Do **not** pass `spawn_subagent`
`isolation: "worktree"`. Enter: `cd`, or `spawn_subagent` `cwd` set to the
worktree path. An explicit cwd is the correct entry, not a pin. A grader
launched with cwd already set to the implementer's worktree is already
entered; grade there.

Init read-set: `accept-and-land.md`.

This stub exists only because Grok discovers skills at
`.grok/skills/accept-and-land/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/accept-and-land.md`.
