---
name: critic
description: Act as an independent commit grader for an assigned implementation target (a normalized slice set) and return an accept / not-accepted grade with findings-first evidence. Use when grading or reviewing a submitted implementation or remediation commit, when checking whether a target contract is actually met and safe to accept, or when an implementer handoff needs its claimed tests independently verified. Covers lineage and scope proof, acceptance matrices, targeted test selection from testing.md, adversarial probes, severity rules, and the required critic report format.
argument-hint: handoff-or-commit
---

# Commit grader

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/critic.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Do **not** pass `spawn_subagent`
`isolation: "worktree"`. Enter: `cd`, or `spawn_subagent` `cwd` set to the
worktree path. An explicit cwd is the correct entry, not a pin. A grader
launched with cwd already set to the implementer's worktree is already
entered; grade there.

Init read-set: `critic.md` + `kernel.md`.

This stub exists only because Grok discovers skills at
`.grok/skills/critic/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/critic.md`.
