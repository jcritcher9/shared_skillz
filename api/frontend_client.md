# Frontend as a FastAPI client

The UI uploads inputs, creates a catalog product run, renders
application projections, and submits revisioned commands. It must not
duplicate validation, matching, duplicate analysis, effect planning,
delivery serialization, or review reconstruction.

Generate TypeScript (or equivalent) types from the FastAPI OpenAPI
document. Do not maintain a second permissive interface.

## Route on `status`

After every create, command, or reload, replace cached workflow state
with `GET /v1/workflows/{run_id}`. Never increment `revision` locally.

```text
running / ready
  -> needs_decision
  -> awaiting_review
  -> awaiting_effect_authorization
  -> paused_unknown | paused_verification
  -> succeeded | failed
```

A successful command can immediately expose another boundary. Handle
unknown future statuses with a safe fallback.

| Status | Client action |
|---|---|
| `needs_decision` | Render `decision.body`; submit `decision_id`, `decision_type`, and current revision. Keep hidden identity fields unchanged. |
| `awaiting_review` | Follow opaque application operations in order. Do not derive review inputs from artifacts. |
| `awaiting_effect_authorization` | Render `effect_intent.supported_modes` only. Echo every intent field; add `selected_mode`. |
| `paused_unknown` | Explain uncertainty; resume/reconcile the same attempt |
| `paused_verification` | Offer resume |
| `succeeded` | Terminal evidence and artifact metadata/downloads |
| `failed` | Safe envelope + opaque `error_id`; new run if appropriate |

## Catalog is authoritative

Products, tracks, supported modes, and configured target IDs come from
catalog endpoints. Do not hardcode a universal mode list into controls.
`maximum_modes` on create are ceilings, not grants.

## Idempotency keys in the browser

Mint a unique key per logical POST. Retain it after the response so a
transport retry is exact. On `409 revision_conflict`, reload and let the
operator reconcile the draft — do not replay with a bumped revision. On
`409 idempotency_conflict`, do not mint a replacement key until the
caller knows which request owns the original.

For multipart uploads, let the browser set `Content-Type` (boundary).
Retry with the same key, bytes, display filename, and parser settings.

## Opaque review

1. Load the review handoff
2. Start the internal review run
3. Complete each internal review decision
4. Request a completed decision-set handoff
5. Bind it to a **new** continuation run

The source analysis run stays paused as lineage. Changed upload digest,
target, reference snapshot, entity, group revision, or analysis digest
fails binding; start a new analysis.

## Downloads

Enable download only when `download_url` is non-null. Validate against
ETag / SHA-256 when the environment allows it.
