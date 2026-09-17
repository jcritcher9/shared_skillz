---
name: create-agent-skill
description: Interactively create a runtime-neutral skill: write the body in agent_protocol/<name>.md (no frontmatter), then add thin SKILL.md adapters in any existing .claude/skills, .grok/skills, and .agents/skills directories that point back to that file. Use when making an agent-agnostic skill, a cross-runtime skill, a protocol skill, or when the user runs /create-agent-skill.
---

# Create an agent-agnostic skill

The normative instructions for this role live in the runtime-neutral
protocol directory, not in this file:

**`../../../agent_protocol/create-agent-skill.md`**

Read that file now and follow it exactly. Enter worktrees per
`../../../agent_protocol/README.md` §9.

This stub exists only because Claude Code discovers skills at
`.claude/skills/<name>/SKILL.md`. Any change to this role belongs in
`agent_protocol/create-agent-skill.md`.

See `../../../agent_protocol/README.md` for the protocol and the other roles.
