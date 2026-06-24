"""FieldOps Copilot backend (Python / FastAPI).

Replaces the prior Node/Express backend. Behavior over the wire is identical:
POST /api/agent/run streams the canonical Cortex Agents SSE event contract
to the SPA. The mock and Snowflake clients yield identical event dicts.

Telemetry:
- OneAgent on the host ships infra/process/RUM/logs to its configured tenant.
- Traceloop (OpenLLMetry) ships gen_ai.* spans + events to the Dynatrace
  tenant configured via DT_OTLP_ENDPOINT + DT_API_TOKEN. The AI Observability
  app on that tenant lights up automatically (service detection, prompts/
  completions panel, token charts).
"""

# Import otel_init FIRST so the tracer provider is set before FastAPI / any
# other code creates spans.
import otel_init  # noqa: F401

import json
import logging
import os
import uuid
from typing import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from agent.base import EVENTS
from agent.mock_client import MockCortexClient
from agent.snowflake_client import SnowflakeCortexClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("fieldops")

tracer = trace.get_tracer("fieldops-copilot")
app = FastAPI()


def _log(level: str, msg: str, **extra):
    """Structured log. Goes through the root logger so the OTel LoggingHandler
    (configured in otel_init) ships it to Dynatrace via OTLP, where it lands
    on the same dt.entity.service as the spans. Active trace/span context is
    attached automatically by the OTel logging integration."""
    fn = getattr(log, level, log.info)
    fn(msg, extra={"attributes": extra} if extra else None)


def _client():
    return SnowflakeCortexClient() if os.environ.get("AGENT_MODE") == "snowflake" else MockCortexClient()


class RunReq(BaseModel):
    prompt: str
    role: str = "technician"


@app.post("/api/agent/run")
async def run(req: RunReq):
    request_id = str(uuid.uuid4())

    async def event_stream() -> AsyncIterator[dict]:
        # Root agent.run span — emits the canonical OTel GenAI semconv that
        # Dynatrace AI Observability + Smartscape GenAI detection rely on.
        with tracer.start_as_current_span("agent.run", kind=SpanKind.SERVER) as root:
            root.set_attribute("gen_ai.system", "snowflake.cortex")
            root.set_attribute("gen_ai.operation.name", "chat")
            root.set_attribute("gen_ai.request.model", "claude-4-sonnet")
            root.set_attribute("gen_ai.response.model", "claude-4-sonnet")
            root.set_attribute("user.role", req.role)
            root.set_attribute("snowflake.request_id", request_id)
            root.set_attribute("gen_ai.prompt", req.prompt)
            # OTel GenAI span event drives the AI Obs "Prompt trace" input column.
            root.add_event("gen_ai.user.message", {"content": req.prompt, "role": "user"})
            _log("info", "agent request", request_id=request_id, role=req.role)

            open_tools = {}
            completion = ""
            try:
                async for ev in _client().run([{"role": "user", "content": req.prompt}]):
                    if ev["event"] == EVENTS.TOOL_USE:
                        name = ev["data"].get("name")
                        ts = tracer.start_span(f"tool.{name}", kind=SpanKind.SERVER)
                        ts.set_attribute("gen_ai.system", "snowflake.cortex")
                        ts.set_attribute("gen_ai.operation.name", "execute_tool")
                        ts.set_attribute("gen_ai.tool.name", name)
                        q = (ev["data"].get("input") or {}).get("query")
                        if q:
                            ts.set_attribute("gen_ai.tool.query", q)
                        open_tools[name] = ts
                        _log("info", "tool invoked", request_id=request_id, tool=name)

                    elif ev["event"] == EVENTS.TOOL_RESULT:
                        name = ev["data"].get("name")
                        ts = open_tools.pop(name, None)
                        if ts is not None:
                            result = ev["data"].get("result") or {}
                            if isinstance(result, dict) and result.get("sql"):
                                ts.set_attribute("db.statement", result["sql"])
                            ts.set_attribute("gen_ai.context", json.dumps(result))
                            ts.end()
                        _log("info", "tool result", request_id=request_id, tool=name)

                    elif ev["event"] == EVENTS.TEXT_DELTA:
                        completion += ev["data"].get("text", "")

                    elif ev["event"] == EVENTS.DONE:
                        root.set_attribute("gen_ai.usage.input_tokens", ev["data"].get("tokens_in") or 0)
                        root.set_attribute("gen_ai.usage.output_tokens", ev["data"].get("tokens_out") or 0)
                        root.set_attribute("gen_ai.completion", completion)
                        root.set_attribute("gen_ai.response_id", request_id)
                        # OTel GenAI span event drives the AI Obs "Prompt trace" output column.
                        root.add_event(
                            "gen_ai.choice",
                            {
                                "finish_reason": "stop",
                                "index": 0,
                                "message": json.dumps({"role": "assistant", "content": completion}),
                            },
                        )

                    elif ev["event"] == EVENTS.ERROR:
                        root.set_attribute("error", True)
                        _log("error", "agent error", request_id=request_id, **ev["data"])

                    yield {"event": ev["event"], "data": json.dumps(ev["data"])}

            except Exception as e:
                root.set_attribute("error", True)
                _log("error", "stream failed", request_id=request_id, err=str(e))
            finally:
                for ts in open_tools.values():
                    ts.end()

    return EventSourceResponse(event_stream())
