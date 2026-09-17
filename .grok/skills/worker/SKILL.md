---
name: worker
description: Bootstrap an implementer worker turn for an assigned target and produce a handoff report. If the user names a scoping document but no target, propose the next schedulable slice or pass and wait. If the user names an area but no document, classify the area, then use its queue when present and wait. After Grade A / A- ACCEPT, propose /accept-and-land and wait; do not land unless the user asked. After NOT ACCEPTED / Grade B or below in this turn, remediate those slices without a further yes. Writes targeted tests and may run that small selection; red results are remediation, not a grade. Use when starting an assigned implementation or remediation target, when writing the final report for one, or when the user runs /worker.
argument-hint: scoping-doc-filename [target]
---

# Implementer worker

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file. Area-only argument (an area name and
no scoping-document filename) → **`../../../agent_protocol/worker-area.md`**;
otherwise → **`../../../agent_protocol/worker.md`**. Read that one file now
and follow it exactly.

Create: plain `git worktree add`. Do **not** pass `spawn_subagent`
`isolation: "worktree"`. Enter: `cd`, or `spawn_subagent` `cwd` set to the
worktree path. An explicit cwd is the correct entry, not a pin. A grader
launched with cwd already set to the implementer's worktree is already
entered; grade there.

Init read-set: `worker.md` + `worktree.md` + `kernel.md`.
Init read-set (area-only): `worker-area.md` + `worktree.md` + `kernel.md`.

This stub exists only because Grok discovers skills at
`.grok/skills/worker/SKILL.md`. It carries no instructions of its own
and must not be edited in place. Any change to this role belongs in
`agent_protocol/worker.md` or `agent_protocol/worker-area.md`.
