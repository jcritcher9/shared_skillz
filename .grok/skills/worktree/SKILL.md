---
name: worktree
description: Give every implementation agent its own isolated git worktree and branch instead of sharing one checkout, at a tool-neutral path any agent runtime can use. Use before spawning any agent to implement, remediate, refactor, or fix; when several agents will work at once; when the user asks to start a phase, start work in isolation, or work in a worktree; and at the start of your own turn if you are about to edit files while other agents are active. Use when the user runs /worktree.
argument-hint: scoping-doc-filename
---

# Worktree isolation

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/worktree.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Do **not** pass `spawn_subagent`
`isolation: "worktree"`. Enter: `cd`, or `spawn_subagent` `cwd` set to the
worktree path. An explicit cwd is the correct entry, not a pin. A grader
launched with cwd already set to the implementer's worktree is already
entered; grade there.

Init read-set: `worktree.md`.

This stub exists only because Grok discovers skills at
`.grok/skills/worktree/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/worktree.md`.
