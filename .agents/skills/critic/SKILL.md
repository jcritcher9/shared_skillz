---
name: critic
description: Act as an independent commit grader for an assigned implementation target (a normalized slice set) and return an accept / not-accepted grade with findings-first evidence. Use when grading or reviewing a submitted implementation or remediation commit, when checking whether a target contract is actually met and safe to accept, or when an implementer handoff needs its claimed tests independently verified. Covers lineage and scope proof, acceptance matrices, targeted test selection from testing.md, adversarial probes, severity rules, and the required critic report format.
---

# Commit grader

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/critic.md`**

Read that file now and follow it exactly.

Create: plain `git worktree add`. Enter: `cd`, or start the session with that working directory. An explicit working directory is the correct entry, not a pin.

Init read-set: `critic.md` + `kernel.md`.

This stub exists only because Codex discovers skills at
`.agents/skills/critic/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/critic.md`.
