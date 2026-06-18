# FieldOps Copilot — Azure + Full-Stack OneAgent Demo

End-to-end observability demo for a field-service AI agent on **Azure**, instrumented entirely by **Dynatrace OneAgent**, with a swappable Snowflake Cortex Agents client (mock today, real later).

The full plan and rationale live in [docs/Cortex_Agent_Azure_OneAgent_Demo_Plan.md](docs/Cortex_Agent_Azure_OneAgent_Demo_Plan.md). Build state is tracked in `.github/agents/` (the sub-agents that built this) and `.github/skills/` (the contracts they obey).

## Repo layout

```
infra/      # Terraform — Azure VM, NSG, cloud-init (OneAgent + Node + Nginx)
backend/    # Node/Express + SSE; OTel tool spans; mock and Snowflake clients
frontend/   # Static SPA (no telemetry SDK; OneAgent injects RUM)
docs/       # The plan
.github/    # Sub-agents, skills, repo instructions
```

## Quick local check (mock mode)

```bash
cd backend && npm install --omit=dev
AGENT_MODE=mock node server.js
# in another shell:
curl -N -X POST http://127.0.0.1:8000/api/agent/run \
  -H 'content-type: application/json' \
  -d '{"prompt":"Show overdue work orders","role":"technician"}'
```

Open [frontend/index.html](frontend/index.html) directly in a browser — the built-in simulator runs offline if the backend isn't reachable.

## Deploy

See plan section 10. **Do not `terraform apply` without supplying real values for `ssh_public_key`, `allowed_ip`, `dt_environment_url`, and `dt_paas_token`.**

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
npx @dynatrace-oss/dt-evals doctor
npx @dynatrace-oss/dt-evals run --since 1h --metric faithfulness
```

The OneAgent attribute allow-list (plan section 9.2) must include those three attributes, plus `gen_ai.response_id`, before evals will work.
