---
name: dynatrace-oneagent-otel
description: How OneAgent and OpenLLMetry/OTLP coexist in this demo, and the gen_ai.* contract that makes the AI Observability app light up. Load for any tracing, span, or "no data in Dynatrace" issue.
---
This repo uses a deliberate **split-stack** approach:
- **OneAgent** on the VM ships host/process/Nginx/RUM/logs to the Sprint tenant.
- **OpenLLMetry (Traceloop) + OTLP HTTP** ships `gen_ai.*` spans from the Python
  backend to the Live tenant where the AI Observability app reads them.

Why split: OneAgent does NOT have an auto-instrumentation module for LLM SDKs
on Node or Python — those `gen_ai.*` spans only appear if the app emits them.
The official path the AI Observability app endorses is OpenLLMetry → OTLP.
Routing OTLP to a different tenant than OneAgent sidesteps the duplicate
service-entity / propagation-format conflict that arises when OneAgent's
codemodule and an OTLP SDK both report the same process to the same tenant.

If a customer wants a single-tenant deployment, the safe options are:
1. Exclude the AI app process group from OneAgent codemodules (Settings →
   Process group monitoring → Disable for that PG).
2. Keep OneAgent host-only on that node and run OpenLLMetry/OTLP in the app.

### What the AI Observability app needs on the spans

| Attribute | Why |
|---|---|
| `gen_ai.system` (e.g. `snowflake.cortex`) | Provider/system label, drives Smartscape GenAI provider entity |
| `gen_ai.operation.name` (`chat`/`embeddings`/…) | Required for service detection as an AI service |
| `gen_ai.request.model` | Model identification (drives the model dropdown) |
| `gen_ai.response.model` | Same; confirms which model actually responded |
| `gen_ai.usage.input_tokens` / `output_tokens` | Powers the token chart |
| `gen_ai.prompt` / `gen_ai.completion` / `gen_ai.context` / `gen_ai.response_id` | Required for dt-evals (faithfulness / relevance / hallucination evaluators) |
| Span events `gen_ai.user.message` (content + role) and `gen_ai.choice` (finish_reason + index + message) | Drives the "Prompt trace" panel's Prompt input / Prompt output columns |

### Other notes
- OTLP HTTP endpoint pattern for Dynatrace: `https://<env>.live.dynatrace.com/api/v2/otlp/v1/traces`, header `Authorization: Api-Token <token>` (scope `openTelemetryTrace.ingest`).
- When ingested via OTLP, span attributes are stored without any allow-list (different from OneAgent's custom-attribute capture, which DOES require an allow-list in Settings).
- Span kind doesn't matter for AI Obs ingestion via OTLP — INTERNAL works fine.
- Verify spans landed: `fetch spans | filter span.name == "agent.run"` on the OTLP tenant; the `events` array (queryable as `span.events`) holds the GenAI semconv events.
