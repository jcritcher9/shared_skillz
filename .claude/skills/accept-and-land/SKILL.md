---
name: accept-and-land
description: Record critic Grade A / A− slices as done and land those commits onto local main without retiring the document worktree or branch. Use when the user runs /accept-and-land, or asks to land critic-ACCEPTED slices. A worker that receives Grade A proposes this skill and waits; it does not run it automatically.
---

# Accept and land

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/accept-and-land.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Do **not** use `EnterWorktree` or
`isolation: "worktree"` to create — both force `.claude/worktrees/`.
`worktree.baseRef` is irrelevant; the block passes the base explicitly.
Enter: `EnterWorktree(path:)` to enter an **existing** path. Spawn agents
**unpinned**. An agent pinned at launch (`isolation`, or an explicit cwd)
can only enter `.claude/worktrees/`. A session that has already entered a
worktree is under the same restriction: `ExitWorktree` first, then enter
the next.

Init read-set: `accept-and-land.md`.

This stub exists only because Claude Code discovers skills at
`.claude/skills/accept-and-land/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/accept-and-land.md`.
