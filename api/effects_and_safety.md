# Effects and safety

External I/O is a first-class protocol, not a function call from a view.

## Fake-only default

Default FastAPI startup publishes only a synthetic target in
`GET /v1/catalog/targets`. That target has no live capabilities and
allows at most preview. A target string from a request is never an
environment name, hostname, filesystem path, or credential selector.
Adding a live target requires an explicit provider registration.

## Opaque target identity

Public IDs are stable and externally assigned. Org IDs, hub IDs, and
startup-random values stay private. Only the opaque ID may enter
checkpoints, receipts, catalogs, logs, or artifacts. Recreating a
private binding registry invalidates old checkpoints rather than
silently attaching them to another tenant.

State roots, credential files, logs, and evidence directories resolve
**outside** the repository, including through symlinks, and do not
overlap each other.

## Creation freezes maxima, not selected modes

`POST /v1/workflows` accepts a ceiling per track (`maximum_modes`).
That is not a grant and does not cross an external-effect boundary.
Allowed modes = product catalog ∩ current provider ceiling. Tracks not
migrated expose only `disabled`. Some tracks have no `dry_run` (example:
filesystem delivery).

Modes that mutate a live system require an acquired, target-bound
reference snapshot from **this** run. Supplied reference files plus
`dry_run`/`execute` is a closed combination.

## Just-in-time authorization

When a run hits an effect boundary it reports
`awaiting_effect_authorization` and an immutable, mode-neutral
`effect_intent`. No mode-bearing plan or attempt exists yet.

The client echoes the intent **exactly** and adds `selected_mode`. The
command atomically freezes the selected mode and the prepared attempt
**before any external I/O**. The track is then consumed.

- `disabled` is final for that track (skipped receipt). Upgrade requires
  a new run.
- Recovery reconciles or resumes the **same** attempt. It never creates
  an upgraded replacement.
- A persisted grant wins for recovery. Exact replay of a completed
  command returns the frozen receipt before provider/ceiling lookup.
- A ceiling rejection is also a durable receipt; retrying after config
  changes returns that rejection. A new logical attempt needs a new
  idempotency key.

For `paused_unknown` or `paused_verification`, post a resumption at the
current revision. Do not post another authorization.

## Attempt lifecycle

Durable effect attempts progress prepared → in_flight → verified.
Pause outcomes: `paused_unknown`, `paused_verification`. Attributable
partial failure may terminate as `completed_with_failures`. Mixed cycles
that still have eligible work park at a continuation status with
generation counters — continue/finish commands, not a second
`authorize_effect` and not a GET that writes.

GET never writes. Exception pages are digest-bound, cursor-opaque, and
fail closed on ledger/decode disagreement.

## Artifacts

Bytes and SHA-256 come from the frozen delivery plan. HTTP does not
re-serialize.

| Availability | Content |
|---|---|
| `preview` / `delivered` | Downloadable |
| `delivery_disabled` / `skipped_empty` | Metadata only |
| `delivery_unverified` | No content while paused unknown/verification |

Do not serve execute-mode bytes from an earlier cache during pause.
