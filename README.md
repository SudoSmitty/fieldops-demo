# FieldOps Copilot — Azure + Full-Stack OneAgent + OpenLLMetry Demo

End-to-end observability demo for a field-service AI agent on **Azure**, with a swappable Snowflake Cortex Agents client (mock today, real later). Telemetry is split across two complementary stacks:

- **Dynatrace OneAgent** — host, process, Nginx, RUM, logs, infra metrics (full-stack on the VM).
- **OpenLLMetry (Traceloop) → OTLP** — GenAI spans (`gen_ai.*`) and span events that light up the Dynatrace **AI Observability** app (prompts, completions, tokens, tool breakdown).

The full plan and rationale live in [docs/Cortex_Agent_Azure_OneAgent_Demo_Plan.md](docs/Cortex_Agent_Azure_OneAgent_Demo_Plan.md). Build state is tracked in `.github/agents/` (the sub-agents that built this) and `.github/skills/` (the contracts they obey).

## Repo layout

```
infra/      # Terraform — Azure VM, NSG, cloud-init
backend/    # Python FastAPI + sse-starlette + Traceloop OTLP; mock and Snowflake clients
frontend/   # Static SPA with manual RUM tag (auto-injection fallback)
scripts/    # up.sh / down.sh / deploy.sh — one-command bring-up/teardown
dashboards/ # FieldOps observability dashboard (dtctl-managed)
docs/       # The plan
.github/    # Sub-agents, skills, repo instructions
```

## Quick local check (mock mode, no Azure)

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
AGENT_MODE=mock uvicorn server:app --host 127.0.0.1 --port 8000
# in another shell:
curl -N -X POST http://127.0.0.1:8000/api/agent/run \
  -H 'content-type: application/json' \
  -d '{"prompt":"Show overdue work orders","role":"technician"}'
```

Open [frontend/index.html](frontend/index.html) directly in a browser — the built-in simulator runs offline if the backend isn't reachable.

## How Dynatrace knows this is a GenAI service

Dynatrace does **not** sniff outbound traffic and recognize `*.snowflakecomputing.com` as "Snowflake Cortex". It does not pattern-match URLs, ports, or payloads. The classification is **100% driven by OpenTelemetry GenAI semantic-convention attributes that the backend code puts on the spans**, shipped via OTLP by [Traceloop / OpenLLMetry](https://github.com/traceloop/openllmetry).

The browser doesn't make the Cortex call — it POSTs to `/api/agent/run`, and the Python backend makes the outbound HTTPS call. So Dynatrace's GenAI detection happens on the **backend service**, not in the browser.

```mermaid
flowchart LR
  A[Browser POST<br>/api/agent/run] -->|HTTPS| B[Python FastAPI backend]
  B -->|HTTPS| C[Cortex API]
  B -.emits.-> S["OTel span<br>name: agent.run<br>attrs: gen_ai.system=snowflake.cortex<br>gen_ai.operation.name=chat<br>gen_ai.request.model=claude-4-sonnet<br>gen_ai.response.model=claude-4-sonnet<br>events: gen_ai.user.message / gen_ai.choice"]
  S -.OTLP HTTP.-> T[Traceloop exporter]
  T -.HTTPS.-> G[Dynatrace OTLP ingest]
  G -->|detects gen_ai.* attrs| SS[Smartscape GenAI entities<br>dt.smartscape.gen_ai.model<br>dt.smartscape.gen_ai.provider<br>dt.smartscape.gen_ai.service]
  SS --> AIO[AI Observability app<br>Prompt trace panel populates]
```

The four trigger attributes on any span emitted by the service:

| Attribute | Our value | What it tells Dynatrace |
|---|---|---|
| `gen_ai.system` | `snowflake.cortex` | The provider/system |
| `gen_ai.operation.name` | `chat` | Chat-style LLM operation (vs `embeddings`, `completion`) |
| `gen_ai.request.model` | `claude-4-sonnet` | Which model was requested |
| `gen_ai.response.model` | `claude-4-sonnet` | Which model actually responded |

When the ingest pipeline sees these on a span, it derives the three Smartscape GenAI entities (model, provider, service) and links them to the parent `dt.entity.service`. The AI Observability app queries by those entities — that's why `fieldops-backend` shows up in its AI Services list with `claude-4-sonnet` as a model version.

The "Prompt trace" panel additionally reads OTel **span events** (`gen_ai.user.message`, `gen_ai.choice`) for the input/output columns. Attribute-based `gen_ai.prompt` / `gen_ai.completion` feed DQL and the token chart, but the panel itself uses events per the OTel GenAI semantic conventions.

### Why we set the attrs manually

Two paths exist for getting `gen_ai.*` onto spans:

1. **SDK auto-instrumentation** — Traceloop's Python SDK auto-instruments the major LLM SDKs (OpenAI, Anthropic, LangChain, LlamaIndex, Bedrock, Vertex…). Drop in `Traceloop.init()` and `gen_ai.*` lands automatically.
2. **Manual instrumentation** — Snowflake Cortex Agents is a plain REST endpoint with no first-class OTel SDK, so we wrap the call in an `agent.run` span and set the attributes / span events ourselves in [backend/server.py](backend/server.py).

Either path produces identical telemetry. The semantic conventions are the contract — Cortex, OpenAI, Bedrock, Vertex, custom-hosted Llama: the AI Observability wiring is provider-agnostic.

## Why two telemetry streams (and two tenants)

The demo intentionally splits telemetry to keep both stacks clean:

| Stream | What it captures | Where it goes |
|---|---|---|
| **OneAgent (full-stack on the VM)** | Host CPU/memory/disk/net, process discovery, Nginx requests, RUM beacons from the SPA, system + app logs | Sprint tenant (`yuf3378h.sprint.dynatracelabs.com`) |
| **OpenLLMetry / Traceloop → OTLP** | `agent.run` root span + `tool.cortex_*` child spans with `gen_ai.*` attrs and `gen_ai.user.message` / `gen_ai.choice` events | Live tenant (`yuf3378h.live.dynatrace.com`) via OTLP HTTP |

Because the two streams target different tenants, the classic **OneAgent + OTel SDK duplicate-service-entity conflict** does not surface (neither tenant sees two views of the same process). In a real customer single-tenant deployment, options are:

- Exclude the AI app process group from OneAgent codemodules (recommended), or
- Accept that the service may appear twice in service detection until Dynatrace's planned reconciliation lands.

See [.github/skills/dynatrace-oneagent-otel/SKILL.md](.github/skills/dynatrace-oneagent-otel/SKILL.md) for the full coexistence playbook.

## Deploy to Azure (one command)

Prereqs: `terraform`, `az` (logged in via `az login`), `ssh`, `curl`.

Two Dynatrace tokens are needed (any not in env is prompted for at runtime — never written to disk):

| Env var | Tenant | Scope |
|---|---|---|
| `TF_VAR_dt_paas_token` | Sprint (OneAgent install) | `InstallerDownload` |
| `DT_API_TOKEN` | Live (OTLP ingest for AI Obs) | `openTelemetryTrace.ingest` |
| `DT_OTLP_ENDPOINT` | Live | e.g. `https://yuf3378h.live.dynatrace.com/api/v2/otlp` |

```bash
export TF_VAR_dt_paas_token='dt0c01....'
export DT_API_TOKEN='dt0c01....'
export DT_OTLP_ENDPOINT='https://yuf3378h.live.dynatrace.com/api/v2/otlp'
./scripts/up.sh
```

What [scripts/up.sh](scripts/up.sh) does:
1. Verifies prereqs, Azure login, and that both tokens are present.
2. Generates `~/.ssh/fieldops_rsa` if missing (azurerm rejects ed25519).
3. Auto-syncs your current public IP into `infra/terraform.tfvars`.
4. `terraform apply` — creates RG, VNet, NSG, public IP, VM.
5. SSHes in and runs [scripts/deploy.sh](scripts/deploy.sh) — installs OneAgent, Python 3.11, Nginx, clones the app, builds a venv, writes `/etc/fieldops/backend.env` with the OTLP creds, starts the systemd service. Idempotent.
6. Smoke test: confirms 200 on the frontend and an SSE stream from the backend.
7. Prints the URL, SSH command, and dashboard link.

Total time: ~5 minutes.

```bash
./scripts/down.sh   # tears down all Azure resources when you're done
```

`down.sh` removes the resource group (and everything in it). Dynatrace tenant resources (dashboard, web application) survive across up/down cycles — see comments in the script for manual cleanup if needed.

### Why cloud-init isn't the deploy path
The plan's Section 6 uses cloud-init for deploy. In practice on Sprint with the canonical Ubuntu image we saw cloud-init's `runcmd` partially execute (typically the first `apt` step ran and subsequent steps didn't), leaving the VM bare. `scripts/deploy.sh` runs the same logic over SSH and is idempotent, so it's the reliable path. cloud-init remains in `infra/cloud-init.yaml` as the documented intent but the SSH script is what guarantees the state.

## After the customer is sold — Snowflake-side observability follow-up

The app-side trace stops at our `tool.cortex_*` spans. To stitch in Snowflake's internal query spans, Cortex telemetry from Snowflake Trail, and per-query credit attribution, deploy:

- **[dynatrace-oss/dynatrace-snowflake-observability-agent (DSOA)](https://github.com/dynatrace-oss/dynatrace-snowflake-observability-agent)** — runs as Snowflake tasks inside the customer's account, pushes telemetry to Dynatrace.

Recommended plugins for the Cortex story (skip the rest):

| Plugin | What it adds |
|---|---|
| `event_log` | Snowflake Trail → OTel spans with Snowtrail `trace_id`/`span_id`. Cortex internals land here. |
| `query_history` (with `query_cost_attribution: true`) | Every Cortex-Analyst-issued SQL as a span with attributed compute credits. |
| `metering` | Credit consumption broken down by service type (`AI_SERVICES` separates Cortex from raw warehouse). |
| `active_queries` | 5-min fresh feed of running queries — strong demo moment. |

**Join key**: `snowflake.request_id` is already stamped on our `agent.run` span. The same UUID appears on Snowflake's query records (when passed via the Cortex Agents `request_id` parameter), making the cross-system stitch a one-line DQL `join`.

Setup requires `ACCOUNTADMIN` and burns warehouse credits on its plugin schedules — not for the demo itself, just for the post-sale rollout.

## Eval follow-up

The `gen_ai.prompt`, `gen_ai.completion`, and `gen_ai.context` attributes on `agent.run` and `tool.*` spans are populated for [dynatrace-oss/dt-evals](https://github.com/dynatrace-oss/dt-evals) compatibility. Run evaluators against live spans with:

```bash
uvx dt-evals doctor
uvx dt-evals run --since 1h --metric faithfulness
```

No OneAgent attribute allow-list is needed when shipping via OTLP — Dynatrace stores all OTLP span attributes by default. (The allow-list still applies if you ever route the same telemetry through OneAgent's custom OTel sensor.)
