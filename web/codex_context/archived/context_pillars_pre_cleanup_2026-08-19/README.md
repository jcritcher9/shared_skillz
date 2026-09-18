# Historical context-pillar snapshot — web

**Historical and non-authoritative.**

This directory preserves the pre-cleanup web context pillars and thin router
captured on 2026-08-19 from baseline commit `a44572328`. The only living
context document under `web/codex_context/` is
[`current_state.md`](../../current_state.md). There is no living handoff,
queue, or catalog; those files were retired. The dated snapshots here are
the historical record and are not reopened.

The repository-wide ownership rules live in `CONTEXT_PILLARS.md`. Product
and cross-layer authority remains under `mappings_2/codex_context/`.
Cleanup scope and sequencing live in
`project_implementations/context_handoff_open_work_retirement_strategy.md`.
The associated heading-by-heading disposition evidence is non-normative
and lives in
`project_implementations/context_pillars_cleanup_section_inventory.md`.

## Snapshot contents

| File | Historical source | Role |
|---|---|---|
| [`current_state_2026-08-19.md`](current_state_2026-08-19.md) | `web/codex_context/current_state.md` | Pre-cleanup web state plus accumulated chronology |
| [`handoff_doc_2026-08-19.md`](handoff_doc_2026-08-19.md) | retired `web/codex_context/handoff_doc.md` | Pre-cleanup web operational and test history |
| [`open_work_2026-08-19.md`](open_work_2026-08-19.md) | retired `web/codex_context/open_work.md` | Pre-cleanup web backlog and completed-project history |
| [`thin_strategy_2026-08-19.md`](thin_strategy_2026-08-19.md) | `web/codex_context/thin_strategy.md` | Pre-cleanup three-line read-order router |

These snapshots are optional historical evidence. Do not read them during
normal session startup, use them to choose the next task, or treat them as
more current than the living `current_state.md`. Snapshot text is preserved
after the warning banner; trailing whitespace alone is normalized to
satisfy repository checks.
