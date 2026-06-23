# FieldOps demo — repo instructions
This repo is built from the plan in /docs/Cortex_Agent_Azure_OneAgent_Demo_Plan.md.
- Backend is **Python (FastAPI + Traceloop/OpenLLMetry → OTLP)**. OneAgent stays installed on the host for infra/RUM/logs; LLM telemetry ships via OTLP to the AI Observability tenant.
- Keep AGENT_MODE=mock until the operator provides Snowflake credentials.
- Never commit tokens, SSH keys, or IPs. Use placeholders.
- Prefer the sub-agents in .github/agents/ for their domains.
