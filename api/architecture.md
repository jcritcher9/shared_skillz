# Architecture

HTTP is a transport over an application service. FastAPI never rebuilds
workflow state, effect plans, review bundles, or artifact bytes.

## Two processes

| Process | Role |
|---|---|
| FastAPI | Durable jobs, catalogs, mutation journal, the only process that holds credentials or calls vendors |
| Operator UI | Renders projections and submits revisioned commands. No tokens, no vendor clients |

The UI does not import the application package. If the browser can reach
the vendor, the split is already broken.

## Control flow vs data flow

Do not collapse these into one package chain.

Control flow (who selects and executes):

```text
caller
  -> public adapter (HTTP or one-call)
  -> typed product request
  -> application service (composition root)
  -> product box / ordered recipe
  -> domain-blind workflow engine
  -> product phases
  -> one terminal result
```

Data flow (how business data changes):

```text
acquired bytes / caller data
  -> integrations materialize it
  -> canon (shared internal language)
  -> workflow operations (pure transforms)
  -> product state, artifacts, terminal result
  -> integrations deliver outputs
```

The recipe exists before the run. "Load → canon → transform" is runtime
data flow, not the order you build the architecture.

## What each layer owns

| Layer | Owns | Must not |
|---|---|---|
| FastAPI adapter | HTTP, OpenAPI models, idempotency journal, JSON presenters | Product phase order, matching, vendor transports |
| Public one-call adapters | Friendly args → typed request + process-local live deps | A second pipeline |
| Application service | Wire box, runtime, stores, execution deps; invoke the job once | Decide the next phase |
| Product boxes | Recipe, phase order, product state, terminal result | Vendor implementation |
| Domain-blind engine | Ordering, checkpoints, decisions, pause/resume, effect attempt protocol | Product or vendor words in its types |
| Workflow operations | Deterministic transforms | Phase order, checkpoints, capabilities |
| Integrations | Auth, transport, vendor schemas, verification | Product recipe |

A phase is an orchestration-visible business boundary (checkpoint,
decision, effect, resume). An operation is a data transformation. A box
is the complete workflow definition. There is no master pipeline above
boxes.

## Live capabilities stay process-local

Durable state stores declarations and safe bindings. It never stores live
clients, credentials, sessions, transports, or callables.
`ExecutionDependencies` attach only to the in-process phase context.

Public catalog targets and private effect-target providers are different
registries. A synthetic preview target may be the only public catalog
entry. Live vendors become connectable after explicit registration, not
because a request string looked like an environment name.

## Thin adapters

HTTP and one-call wrappers: bind inputs, invoke the application service
once, present the result or a stable error. They do not sequence phases,
interpret the payload graph, score matches, or assemble the terminal
result.
