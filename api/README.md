# HTTP adapter thoughts (`mappings_2/api`)

Vendor-agnostic ideas behind the mill's FastAPI package. FastAPI is the
HTTP framework. The product, CRM vendors, and pandas stay out of this
folder.

This is **not** a copy of `mappings_2/api/*.py`. It is the contract the
Python package was written to satisfy. A friend can implement the same
shape in FastAPI without EasyImports, Salesforce, or HubSpot.

Source in the mill: `mappings_2/api/` (adapter),
`mappings_2/codex_context/reference/pipeline_architecture/PIPELINE_ARCHITECTURE.md`
(layers), `mappings_2/api/FRONTEND_CHECKPOINT_INTEGRATION.md` (client).

| File | What it captures |
|---|---|
| `architecture.md` | Two processes, control vs data flow, thin adapter, domain-blind engine |
| `http_contract.md` | FastAPI surface: OpenAPI, idempotency, revisions, presenters, errors |
| `effects_and_safety.md` | Fake-only default, opaque targets, maxima vs grants, JIT authorization |
| `frontend_client.md` | UI as a client of durable resources, not a second engine |

The mill's instance of these ideas: Django under `web/` talks HTTP to a
FastAPI process that is the sole credential holder. Default catalog is a
synthetic target with no live capabilities.
