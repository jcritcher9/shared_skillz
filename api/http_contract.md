# FastAPI HTTP contract

Use FastAPI as the public adapter. Pin FastAPI, Pydantic, Uvicorn, HTTPX,
and `python-multipart`. Generate OpenAPI from the app; treat the
document as the client contract. Canonicalize the generated JSON and
compare its digest to a checked-in snapshot so silent schema drift fails
CI.

## Resources, not verbs that rebuild work

Typical v1 surface:

- `GET /health` — `{status, api_version}`
- `GET /v1/catalog/products` and `GET /v1/catalog/targets` — authoritative
  for product keys, tracks, allowed modes, configured target IDs
- `POST` / `GET /v1/uploads` — content-addressed blobs
- `POST /v1/workflows` and `GET /v1/workflows/{run_id}`
- revisioned commands: decisions, effect-authorizations, resumptions,
  review handoffs, continuations
- artifact metadata plus content only at the returned `download_url`

Do not expose original upload bytes. Filename is display-only; never a
path and never a parser selector. Freeze media type, byte count, digest,
parser-contract version, encoding or sheet, row count, and columns at
registration.

## Presenters

Routes call the application, then map frozen projections to JSON.
FastAPI must not reconstruct domain objects (`ApprovalBundle`, decision
graphs, delivery serializers) from artifact tables. Discriminated unions
in Pydantic (`product_key`, `decision_type`) pick exact request/response
models. Do not leave public objects as unconstrained `dict`.

## Idempotency

Every `POST`, including multipart upload, requires a globally unique
`Idempotency-Key`. One journal owns that namespace across routes.

- Same key, same route, same payload, same resource → frozen receipt
  (or complete a pending claim)
- Same key, different route/payload/resource → `409 idempotency_conflict`
- Keep the key for transport retries
- Mint a new key for every logically new mutation

Preclaim the key **before** application work. Freeze status and body
after success. API-journal and checkpoint stores are atomic **within
themselves**. No database transaction stays open during application or
external I/O.

A pending exclusive lease (heartbeat + TTL) prevents two callers from
executing the same unstaged key. A 409 busy is not a conflict of intent.

## Revisions

Workflow mutations carry `expected_revision`. Stale revision →
`409 revision_conflict`. The client reloads; it does not silently bump
the revision and replay. Exact retry of a completed command returns the
frozen receipt even if the provider registry later disappeared.

## Errors

Unexpected failures use a safe envelope: public `code`, human message,
opaque `error_id`. Raw exception text stays server-side. Persisted
workflow failures use the same shape. Unknown future workflow statuses
need a safe client fallback, not a crash and not an automatic submit.

## Checkpoint contracts

Public workflow identity and checkpoint-contract version are explicit.
Unsupported contracts fail closed (`409 checkpoint_contract_unsupported`)
before state decoding. Do not interpret an old checkpoint under an
amended protocol.

## CORS and factory

Localhost browser origins allowed by default. Extra origins from config.
Expose a FastAPI factory (`create_default_app`) so Uvicorn can load
`--factory` without importing a global app that touches disk at
import time.
