---
name: create-context-pillars
description: Scaffold the lean context documents from `CONTEXT_PILLARS.md` into a given directory: always `current_state.md` and `testing.md`, plus `open_work.md` (queue) and `strategy_index.md` (catalog) only when the area has indexable scoping documents. Idempotent — existing files are checked against the pillar rules and left in place; a missing `testing.md` is created. Never create `handoff_doc.md` or a diary. Use when initializing a work area's living docs, creating context pillars, or when the user runs `/create-context-pillars`.
argument-hint: directory
---

# Create context pillars

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/create-context-pillars.md`**

Read that file now and follow it exactly. Enter worktrees per
`../../../agent_protocol/README.md` §9.

This stub exists only because Grok discovers skills at
`.grok/skills/<name>/SKILL.md`. Any change to this role belongs in
`agent_protocol/create-context-pillars.md`.

See `../../../agent_protocol/README.md` for the protocol and the other roles.
