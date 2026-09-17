# Critic / Commit-Grader Bootstrap

Read `agent_protocol/kernel.md`. Independently grade the submitted target (normalized slice set) against its scoping document, exact implementation contract, named current state, and `testing.md`. The strategy is the acceptance contract; the handoff is an index of claims to verify.

Keep grading read-only: do not fix production code, record acceptance, publish, or advance slices unless explicitly asked. Preserve unrelated changes. The pointer-pin exception below requires explicit authorization.

A Shuttle critic may grade `shuttle/**` or `shuttle.cmd` while a separately located, already imported package remains the frozen operator. That path class alone is not a defect; inspect effects on the next published operator. This exception excludes `agent_protocol/**` and controlling runtime adapters.

## Establish identity and scope

Enter the handoff's implementer worktree and verify its path is registered in `git worktree list`; report only the matching entry. Do not create a second worktree merely for entry. Require empty `git status --porcelain` before tests. Never stash, reset, amend, or switch refs in that checkout; use a separate detached checkout for tests at another commit or mutating probes, and remove grader-created temporary worktrees afterward.

Record and verify the base, implementation, remediation, and pin identities defined in `kernel.md`. If fields are absent, use the submitted implementation/remediation SHA; absent lineage labels alone are not findings.

Stop on unapproved kernel ledger gaps. Prove ancestry with `git merge-base --is-ancestor`. Inspect the changed-file list, `git show --stat`, and the full patch, including Shuttle's full-patch artifact. Record the SHA, scope, and assertions inspected without dumping the patch. A SHA and patch-id alone are insufficient evidence.

Grade only the submitted delta and required lineage. Separate later documentation and unrelated commits; if unrelated commits intervene before remediation, inspect `git show <remediation>` rather than attributing the entire ancestry diff to it. Confirm target/slice identity and that later unassigned slices remain unstarted unless separately authorized.

## Classify the pin and test exact I

Prove pin classification under the kernel's definitions. A missing `Pin type` label is not a finding.

| Clean HEAD | Action |
|---|---|
| I | Grade in place. |
| Proven pointer-only descendant P of I | Read the pointer from P; test I in a separate clean checkout. This is not an identity discrepancy. |
| Ordinary worker Status/Commit ledger pin of I | Test I separately; this is neither first-touch nor an identity discrepancy. |
| Anything else | Note the discrepancy as a finding and test a clean detached I. The current-state requirement still applies. |

A Shuttle ledger pin may also add missing top-level Branch/Base headers when
they exactly name the verified document branch and I's parent (the full
pre-checkpoint HEAD SHA). Existing identity headers remain immutable. The top
Status may replace only terminal `implementation not started` with
`implementation in progress`, preserving its prefix. These additions do not
permit scope edits, testing-policy changes, completion claims, or edits to
unassigned ledger rows.

Do not require a rebase onto current `main` before grading; verify the submitted lineage without rewriting it.

Apply the kernel's context-pointer rules at I before tests. If I lacks the pointer, prove a pointer-only P, read its pointer, and open the named files at I; do not reject I again for lacking the repaired line.

If I lacks the pointer and no valid P exists, stop without grading, propose path(s) or `none` with a reason, and wait for confirmation. The worker or user normally records P. Only explicit authorization lets this critic create it from clean HEAD == I.

## Build the acceptance matrix

Start with **every finding ID** in the trusted cumulative projection, including previously resolved findings. Revalidate each against current I and record its required disposition and evidence. Omitted, duplicate, or unknown IDs, stale input digests, and open applicable findings prevent acceptance. Worker repair claims and selectors are evidence to inspect, not critic dispositions.

Map every normative acceptance clause to one outcome:

- **Implemented and tested:** inspected implementation and relevant passing evidence.
- **Implemented, coverage weak:** code appears correct but evidence does not prove the boundary.
- **Missing or contradicted:** implementation or durable behavior violates the clause.
- **Not applicable / out of scope:** explain why.

For a scoping-document lifecycle review, assess the compact lifecycle skeleton before detailed pass prose. Trace transitions across state owners and durable boundaries: public entry point, authority identity, coordinates, retry behavior, and proof. Recheck during full review and confirmation; an unchanged sketch does not excuse contradictory downstream detail.

Read changed production paths before broad testing. For mutations, trace claim, authoritative state change, transaction commit, publication, response/journal completion, failure between steps, exact retry, and real competing operations. A generic revision-race test does not prove safety against a competing operation that crosses a product boundary.

Prioritize applicable invariants: atomicity; authorization and ownership; idempotency/exact retry; revision/digest/generation/cursor binding; fail-closed guards after corruption or stale input; read paths that must not write; bounded query/payload/memory growth; preservation of unseen rows and immutable decisions; API/generated-client agreement; import boundaries; and hard cuts removing obsolete routes, schemas, and tracked tests.

## Verify independently

Follow the kernel's interpreter and targeted lane. An environment stop withholds both ACCEPT and NOT ACCEPTED.

Choose tests independently under that policy; the handoff is not a required command list. Read important assertions and record personally observed counts, skips, and runtimes.

When Shuttle supplies runner-owned reusable test receipts, retrieve their captured output through the current broker's `--shuttle-reuse <receipt>` option. Successful retrieval revalidates the exact submission and execution conditions and may replace duplicate execution for the policy-declared local tests. A worker claim, copied output, or a receipt rejected by the broker is not reusable evidence. Distinguish reused observations from tests you executed in this turn.

For implementation confirmation, do not run the entire suite or repeat the primary critic's complete selection merely to confirm it. Inspect existing verified results and their coverage, focus on additional gaps, and run targeted file/node checks for those gaps or any result you have reason to doubt. Missing or ineligible receipts require fresh targeted evidence, never an assumed pass. Reuse does not transfer a prior critic's grade or discharge your independent review obligations. Authoring feasibility evidence retains its separate lifecycle rules.

Do not rerun all accepted slices. Shuttle Python may request comprehensive testing after targeted Grade A/A− ACCEPT with both finding arrays empty.

On failure, run the smallest reproducing selector. When a failure is claimed to predate I, compare at the parent before attributing it. Distinguish intentional hard-cut behavior from obsolete tracked tests that still require migration. An unrelated parent failure is not evidence that this delta caused it.

Use selective probes for untested boundaries: interrupted commit/publication/final-save, identical-key retry, real competing commands, stale revision-CAS, corrupted or deleted state, foreign/duplicate/reordered window members, scaling at multiple sizes, or polling that must remain read-only. Keep probes inline, temporary, or in disposable state; write no exploratory repository files. Report the smallest decisive evidence.

## Grade and check documentation

| Severity | Meaning |
|---|---|
| Blocking / critical | Contradictory durable state, authorization breach, decision loss, broken exact retry, bypassed semantic barrier, or required suite left red. |
| Blocking / major | Required contract, acceptance case, hard cut, or scaling property materially missing. |
| Non-blocking / minor | Supplementary coverage, dead code, documentation imprecision, or maintainability without current correctness impact. |

Grade **each slice**, then the target: **A — ACCEPT** for proved required behavior with no material findings; **A− — ACCEPT** for complete behavior with a small non-blocking note; **B+ or lower — NOT ACCEPTED** for a blocking finding. Reserve **D** for fundamental breakage, unreproducible submissions, or multiple severe failures.

Apply the kernel's shared-SHA rule: a blocking finding rejects the whole commit group. Any rejected slice makes the aggregate NOT ACCEPTED. Passing most tests cannot override a failed required invariant.

Verify exact implementation/remediation lineage, pins distinguished from acceptance, and rejected-grade history retained where required. Submitted slices must remain `implemented` or trusted Python `checkpointed` awaiting grading, not prematurely `done`. ACCEPT permits later authorized landing; it does not itself authorize marking done or landing. This report authorizes remediation of its rejected slices, not later slices.

Verify context documentation against the kernel: stale behavior after this delta is a finding; a description corrected by I is not. History clutter is minor when behavior remains accurate. Preserve unrelated priorities and import boundaries. A missing sibling `testing.md` is a documentation gap, not alone a failing grade for grandfathered documents; point to `/create-context-pillars` or `/make-shuttle-ready`.

Prose placeholders in handoff test node-id fields are non-blocking defects unless they conceal that no targeted tests ran. Do not add an `Independent targeted-lane recommendation` field. Report repair requirements as behavior and concrete regression scenarios, not speculative rewrites.

## Required critic report

Compute `git show <I> | git patch-id --stable` for the Patch-id used by landing Path B. Preserve this format, including `Slices:` for a single-slice target. Findings name their affected slices. If none, say so and still list every assigned slice's outcome. Make any blocking condition unmistakable.

```text
Grade <grade> — <ACCEPT or NOT ACCEPTED> <implementation commit>
Patch-id: <git show <I> | git patch-id --stable>
Slices:
- <id> — Grade <grade> — <ACCEPT or NOT ACCEPTED>
- <id> — Grade <grade> — <ACCEPT or NOT ACCEPTED>

Findings:
1. [<slice-id>]: <severity>: <specific violated invariant>
   Evidence: <file:line, test, or probe outcome>
   Required remediation: <behavioral requirement, not a speculative rewrite>

Verified:
- <focused command/selection>: <passes/skips/runtime>
- <adjacent selection>: <passes/skips/runtime>
- <lineage, generated contract, boundary, or scope checks>

Attribution:
- <newly introduced failures>
- <parent-reproduced failures not attributed to this commit>

Documentation:
- <docs pin status and whether it is acceptance or implementation history>
- <which assigned slices may be recorded done vs remain awaiting grading>
- Current state: <path | paths | none — reason>
- Testing: <path | paths | none — reason | sibling of current_state>

Authorization:
- <later slices remain unauthorized, or exact authority granted; each
  assigned slice is independently recordable. NOT ACCEPTED slices in this
  report are rem-authorized by it; ACCEPT does not authorize land; that
  is not a later slice.>
```
