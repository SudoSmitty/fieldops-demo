# FieldOps — Complete-demo Plan (post-MVP)

This plan closes the gaps between the current FieldOps demo and the "cleanest complete" customer-shaped story for **Snowflake Cortex Agents on Dynatrace**.

Use this alongside the original [Cortex_Agent_Azure_OneAgent_Demo_Plan.md](Cortex_Agent_Azure_OneAgent_Demo_Plan.md). That document describes the MVP architecture; this one is the addendum to take it to a referenceable customer demo.

## Current state — what already works

Verified end-to-end on the live deployment:

- Browser SPA → nginx → FastAPI → mock Cortex client, full SSE event contract.
- OneAgent on host: nginx + RUM + host metrics + logs.
- Python OTel via Traceloop → OTLP HTTP → Dynatrace tenant.
- Single trace per user request: nginx → `POST /api/agent/run` → `agent.run (INTERNAL)` → `tool.cortex_* (INTERNAL)`.
- Single AI service entity (`fieldops-backend`); OneAgent PG-level monitoring disabled for the uvicorn PG to prevent duplicate publishing.
- Logs (uvicorn + FastAPI + app) ship via OTLP and bind to the same `dt.entity.service` as the spans.
- AI Obs `Prompt trace` panel populated via `gen_ai.input.messages` / `gen_ai.output.messages` with `parts[].type='text'`.
- All required OTel GenAI semconv v1.36+ attributes present: `gen_ai.system`, `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.is_streaming`, `gen_ai.usage.{input,output,total}_tokens`, `gen_ai.response.{id,finish_reasons}`, plus legacy `gen_ai.prompt`/`gen_ai.completion`/`gen_ai.context` for dt-evals.

## Gaps to close

| # | Gap | Why it matters | Effort |
|---|---|---|---|
| 1 | No CLIENT span for the outbound HTTPS call to Cortex | Trace shows agent.run timing only; no `http.url`, `http.status_code`, request/response size, or DNS/connect timing on the actual Cortex call | XS (~5 min) |
| 2 | `AGENT_MODE=mock` only | Real demo needs real prompts, real tokens, real model latency, real failure modes against a Snowflake account | M (~half day, depends on Cortex account access) |
| 3 | Inside-Cortex visibility is zero | Customer can't see what tools Cortex's orchestrator actually picked, the SQL Cortex Analyst ran on the warehouse, vector retrieval timing for Search, or per-call credit cost | L (DSOA deployment + plugin config; needs ACCOUNTADMIN) |
| 4 | No cross-system trace stitch | App-side trace stops at the outbound HTTPS call; Snowflake-side spans (when DSOA is in place) don't yet share a join key with our trace | XS once #3 is done (one line in the Cortex `:run` call body) |

The four are independent and stackable: ship #1 immediately; defer #2 until tenant access exists; do #3+#4 together as a single post-sale rollout.

---

## Phase 1 — Add outbound HTTPS CLIENT span (XS, no dependencies)

### Goal
Capture the Cortex HTTPS call as a proper OTel CLIENT span, so the trace shows the actual outbound latency, URL, status code, and bytes — separable from the agent orchestration overhead.

### Changes
- `backend/requirements.txt`: add `opentelemetry-instrumentation-httpx==0.48b0`
- `backend/otel_init.py`: after `Traceloop.init(...)`, add:
  ```python
  from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
  HTTPXClientInstrumentor().instrument()
  ```
- No `server.py` change — the Snowflake client uses `httpx.AsyncClient`, which gets instrumented globally.

### Acceptance
- New CLIENT span per request, name like `POST` with `http.url = https://<account>.snowflakecomputing.com/api/v2/databases/.../agents/...:run`.
- Span is child of `tool.cortex_*` (since the HTTPS call happens inside the tool span).
- DQL: `fetch spans, from: now()-5m | filter span.kind == "client" and contains(http.url, "snowflakecomputing.com") | summarize count()` returns ≥1 per recent request.

---

## Phase 2 — Switch from mock to live Snowflake Cortex (M)

### Goal
Demo against a real Cortex Agent with real prompts, real model latency, and real token counts.

### Prerequisites
- Snowflake account with Cortex Agents enabled.
- A **semantic model** for Cortex Analyst (defining the work-orders / assets tables).
- A **Cortex Search service** indexed over the asset-manual docs (for the knowledge scenario).
- A **Programmatic Access Token (PAT)** with privileges to call the agent.
- An **agent** created in the account (`SNOWFLAKE_INTELLIGENCE.AGENTS.FIELDOPS_AGENT` or similar) with the analyst + search tools wired in.

### Operator steps
1. In Snowflake: create the semantic model and Search service, then the agent (out of scope of this repo — follow Snowflake Cortex Agents quickstart).
2. On the demo host, populate the systemd EnvironmentFile with:
   ```
   AGENT_MODE=snowflake
   CORTEX_HOST=<account>.snowflakecomputing.com
   CORTEX_DATABASE=SNOWFLAKE_INTELLIGENCE
   CORTEX_SCHEMA=AGENTS
   CORTEX_AGENT=FIELDOPS_AGENT
   CORTEX_PAT=<PAT>
   ```
3. `systemctl restart fieldops-backend`.

### Code adjustments needed
- `backend/agent/snowflake_client.py` is already wired and shape-compatible with the mock per the `cortex-agent-protocol` skill.
- Re-verify the `normalize()` adapters against the live wire payload (Snowflake has revved field names between API versions: `tool_result.result.searchResults[]` vs `hits`, `table.rowType+data` vs `columns+rows`, `usage.{prompt,completion}_tokens` vs `{input,output}_tokens`). Update any mismatches without touching `server.py`.
- Pass through Cortex's own `response_id` / `request_id` if present in the final `response` event, in addition to our locally generated UUID.

### Acceptance
- 5 successful agent runs with real, varied prompts.
- `gen_ai.usage.input_tokens` / `output_tokens` reflect actual model usage (non-mock numbers).
- `tool.cortex_analyst` spans show the actual generated SQL in `db.statement`.
- `tool.cortex_search` spans show `hits` matching the indexed corpus.
- A deliberate failure scenario (invalid SQL, missing semantic model column, no search hits) surfaces as either an `error` SSE event with `error=true` on `agent.run`, or as a 4xx/5xx on the CLIENT span from Phase 1.

---

## Phase 3 — Deploy DSOA in the Snowflake account (L)

### Goal
Bring Snowflake-side telemetry (Cortex internal events, warehouse query history, credit consumption, active queries) into the same Dynatrace tenant.

### Tool
[`dynatrace-oss/dynatrace-snowflake-observability-agent` (DSOA)](https://github.com/dynatrace-oss/dynatrace-snowflake-observability-agent) — runs as a Snowflake task inside the customer account, polls Snowflake views, ships to Dynatrace.

### Prerequisites
- `ACCOUNTADMIN` in the Snowflake account.
- A dedicated DSOA warehouse (small / X-Small, auto-suspend 60s).
- A Dynatrace API token with `metrics.ingest`, `events.ingest`, `logs.ingest`, `openTelemetryTrace.ingest` scopes.

### Plugins to enable (minimum for the Cortex story)
| Plugin | Adds |
|---|---|
| `event_log` | Snowflake Trail → OTel spans with Snowtrail trace context. Cortex's internal LLM-call / planner spans land here. |
| `query_history` (with `query_cost_attribution: true`) | Every Cortex Analyst-issued SQL as a span on the warehouse service, with attributed compute credits. |
| `metering` | Credit consumption broken down by service type (`AI_SERVICES` separates Cortex compute from raw warehouse). |
| `active_queries` | 5-min-fresh feed of in-flight queries. Strong live-demo moment. |

Skip the rest of DSOA's plugin set for the demo — they're useful for full Snowflake monitoring but add cost and noise here.

### Acceptance
- `fetch events, from: now()-1h | filter event.kind == "GENAI_EVENT"` returns Cortex events from Snowtrail.
- `fetch spans, from: now()-1h | filter dt.entity.service matches "SERVICE-.*WAREHOUSE.*"` shows warehouse query spans for recent Cortex-issued SQL.
- A dashboard tile with `fetch metrics ... where metric.key == "snowflake.credits.ai_services"` shows AI-services credits trending up as we send prompts.

### Cost note
DSOA's polling tasks burn warehouse credits on their schedules. Sized correctly (X-Small auto-suspend) the overhead is low (~$5–10/day for a dev account), but it's not free. Document this explicitly for any customer rollout.

---

## Phase 4 — Cross-system trace stitch (XS, depends on Phase 3)

### Goal
Make the FieldOps app trace and the DSOA-pulled Snowflake warehouse spans joinable on a shared request ID, so a single user prompt can be followed from `agent.run` all the way to the warehouse query that satisfied it.

### Current state
- `snowflake.request_id` (a per-request UUID we generate) is already stamped on every `agent.run` span. ✅
- It is NOT yet passed to Cortex on the `:run` call. ❌

### Change
In `backend/agent/snowflake_client.py`, add the request ID to the POST body:

```python
body = {
    "model": "claude-4-sonnet",
    "messages": messages,
    "request_id": request_id,   # pass through from server.py
}
```

This requires `server.py` to pass the request UUID into `_client().run(...)`, so update the signature:
```python
async def run(self, messages, request_id=None):
```

The same UUID will then appear in Snowflake's `query_history.query_tag` (Cortex propagates it automatically), which DSOA captures as a span attribute.

### Acceptance
- DQL example for the stitch:
  ```dql
  fetch spans, from: now()-15m
  | filter span.name == "agent.run"
  | join (
      fetch spans, from: now()-15m
      | filter contains(dt.entity.service, "WAREHOUSE")
        and isNotNull(snowflake.request_id)
    ) on snowflake.request_id
  | fields snowflake.request_id, gen_ai.prompt, app_duration=duration, sql=db.statement, warehouse_duration=duration_1, credits_used
  ```
- Notebook: "Cortex end-to-end" — same prompt visible left (app side) and right (warehouse side), parented by `snowflake.request_id`.

---

## Phase 5 — Production polish (optional for demo, mandatory for any customer pilot)

These are listed for completeness so the gap to a real production deployment is explicit. None are needed for the demo itself.

- **Auth**: SSO (OIDC / SAML) between browser and FastAPI; JWT propagation to the backend; PAT rotation via Snowflake secret object.
- **Secrets**: move PAT + Dynatrace tokens from systemd EnvironmentFile to Azure Key Vault / GCP Secret Manager / AWS Secrets Manager, fetched at process startup.
- **Scaling**: container the app, deploy to AKS / EKS / GKE / Snowpark Container Services with HPA.
- **Resilience**: retry + backoff + circuit breaker around the Cortex call; idempotency on `snowflake.request_id`.
- **Frontend**: replace static SPA with Streamlit-in-Snowflake (closes the auth + secrets gap automatically) or Next.js with proper session management.

---

## Sequencing recommendation

1. **Ship Phase 1 today** — low risk, high signal, makes the demo trace look noticeably more "real" because the actual Cortex HTTPS hop is visible.
2. **Set up Phase 2 in the operator's Cortex sandbox** when convenient. The mock will keep working as a fallback (`AGENT_MODE=mock`).
3. **Schedule Phase 3 + 4 together as a post-sale "Snowflake side" engagement** with the customer. Don't try to fit it into a demo window.
4. **Phase 5 lives in the customer's productionization backlog**, not the demo backlog.

---

## Acceptance for "complete demo" status

The demo can be called complete-shape (not just MVP) when all of the following hold:

- [ ] Outbound Cortex call appears as its own CLIENT span (Phase 1)
- [ ] At least one full demo run uses `AGENT_MODE=snowflake` against a live Cortex agent (Phase 2)
- [ ] DSOA-ingested warehouse query spans visible in the same tenant (Phase 3)
- [ ] One DQL/notebook view stitches app-side and Snowflake-side spans on `snowflake.request_id` (Phase 4)
- [ ] The existing FieldOps dashboard and the AI Obs Prompt panel both light up for the same fresh request without manual UI gymnastics

When the four checkboxes are ticked, the demo represents what a customer would actually deploy and observe in production.
