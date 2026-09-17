---
name: closeout
description: Land a completed scoping document's worktree branch onto main, fast-forward when possible and otherwise use Path B in accept-and-land.md (rebase, prove, remap, fast-forward), then retire the branch and worktree. Never use git merge --no-ff. Use when every phase of a scoping document is accepted and the work should reach main, when asked to close out, land, finish, or bring a document branch current, or when the user runs /closeout.
---

# Document closeout

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/closeout.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Enter: `cd`, or start the session with that working directory. An explicit working directory is the correct entry, not a pin.

Init read-set: `closeout.md` + `accept-and-land.md`.

This stub exists only because Codex discovers skills at
`.agents/skills/closeout/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/closeout.md`.
