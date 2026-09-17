---
name: worktree
description: Give every implementation agent its own isolated git worktree and branch instead of sharing one checkout, at a tool-neutral path any agent runtime can use. Use before spawning any agent to implement, remediate, refactor, or fix; when several agents will work at once; when the user asks to start a phase, start work in isolation, or work in a worktree; and at the start of your own turn if you are about to edit files while other agents are active.
---

# Worktree isolation

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/worktree.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Enter: `cd`, or start the session with that working directory. An explicit working directory is the correct entry, not a pin.

Init read-set: `worktree.md`.

This stub exists only because Codex discovers skills at
`.agents/skills/worktree/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/worktree.md`.
