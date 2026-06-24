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
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from agent.base import EVENTS
from agent.mock_client import MockCortexClient
from agent.snowflake_client import SnowflakeCortexClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("fieldops")

tracer = trace.get_tracer("fieldops-copilot")
app = FastAPI()
# Default model identifier surfaced on spans. Overridable via env so the
# operator can match whatever model their Snowflake Cortex agent is configured
# to use. Real Snowflake mode also overrides gen_ai.response.model with the
# value the Cortex response actually returns (see EVENTS.DONE branch below).
CORTEX_MODEL_DEFAULT = os.environ.get("CORTEX_MODEL", "claude-3-5-sonnet")
# Auto-create the HTTP server span for every request, read inbound
# W3C traceparent / Dynatrace headers as parent context. This lets the
# OneAgent-captured nginx span stitch to our OTel spans, and makes
# agent.run / tool.* spans children of the HTTP request, not roots.
#
# Note: the FastAPI/ASGI instrumentation also emits `<endpoint> http receive`
# and `http send` INTERNAL sub-spans for each ASGI message. They're harmless
# noise in the trace tree; no documented flag to suppress them on this version.
# If they become a problem, drop them via a custom SpanProcessor filter.
FastAPIInstrumentor().instrument_app(app)


def _log(level: str, msg: str, **extra):
    """Structured log. Goes through the root logger so the OTel LoggingHandler
    (configured in otel_init) ships it to Dynatrace via OTLP, where it lands
    on the same dt.entity.service as the spans. Active trace/span context is
    attached automatically by the OTel logging integration.

    Extras are flattened into a JSON suffix on the message so they're queryable
    in the log content without violating OTel attribute primitive-type rules
    (OTel attributes can't be dicts)."""
    fn = getattr(log, level, log.info)
    if extra:
        fn(f"{msg} {json.dumps(extra)}")
    else:
        fn(msg)


def _client():
    return SnowflakeCortexClient() if os.environ.get("AGENT_MODE") == "snowflake" else MockCortexClient()


class RunReq(BaseModel):
    prompt: str
    role: str = "technician"


@app.post("/api/agent/run")
async def run(req: RunReq):
    request_id = str(uuid.uuid4())

    async def event_stream() -> AsyncIterator[dict]:
        # Root agent.run span — emits the canonical OTel GenAI semconv (v1.36+)
        # that Dynatrace AI Observability + Smartscape GenAI detection rely on.
        # INTERNAL (not SERVER): agent.run is a logical operation INSIDE the
        # HTTP request, not a separate request entry. The FastAPI auto-instrument
        # already publishes the SERVER span for POST /api/agent/run.
        with tracer.start_as_current_span("agent.run", kind=SpanKind.INTERNAL) as root:
            root.set_attribute("gen_ai.system", "snowflake.cortex")
            root.set_attribute("gen_ai.provider.name", "snowflake.cortex")
            root.set_attribute("gen_ai.operation.name", "chat")
            root.set_attribute("gen_ai.agent.name", "fieldops-supervisor")
            root.set_attribute("gen_ai.request.model", CORTEX_MODEL_DEFAULT)
            root.set_attribute("gen_ai.response.model", CORTEX_MODEL_DEFAULT)
            root.set_attribute("gen_ai.is_streaming", True)
            root.set_attribute("user.role", req.role)
            root.set_attribute("snowflake.request_id", request_id)
            # AI Obs "Prompt trace" panel reads the user prompt from this attribute
            # in the current OTel GenAI semconv (v1.36+). The older span event format
            # (`gen_ai.user.message`) is NOT what the panel renders. The `type` on
            # each part is REQUIRED for the panel to label the part properly
            # (without it, the part renders as "Unknown").
            root.set_attribute(
                "gen_ai.input.messages",
                json.dumps([{
                    "role": "user",
                    "parts": [{"type": "text", "content": req.prompt}],
                }]),
            )
            # Legacy attribute kept for dt-evals / existing DQL dashboards.
            root.set_attribute("gen_ai.prompt", req.prompt)
            _log("info", "agent request", request_id=request_id, role=req.role)

            open_tools = {}
            completion = ""
            try:
                async for ev in _client().run([{"role": "user", "content": req.prompt}]):
                    if ev["event"] == EVENTS.TOOL_USE:
                        name = ev["data"].get("name")
                        # INTERNAL (not SERVER): tool calls are child operations
                        # of agent.run, not new entrypoints. SERVER would make
                        # Dynatrace show them as separate top-level requests.
                        ts = tracer.start_span(f"tool.{name}", kind=SpanKind.INTERNAL)
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
                        tokens_in = ev["data"].get("tokens_in") or 0
                        tokens_out = ev["data"].get("tokens_out") or 0
                        # If the real Snowflake response carried the model it
                        # actually used, override response.model with that. Mock
                        # mode also yields `model` on DONE for parity.
                        actual_model = ev["data"].get("model")
                        if actual_model:
                            root.set_attribute("gen_ai.response.model", actual_model)
                        root.set_attribute("gen_ai.usage.input_tokens", tokens_in)
                        root.set_attribute("gen_ai.usage.output_tokens", tokens_out)
                        root.set_attribute("gen_ai.usage.total_tokens", tokens_in + tokens_out)
                        root.set_attribute("gen_ai.response.id", request_id)
                        root.set_attribute("gen_ai.response.finish_reasons", ["stop"])
                        # AI Obs reads the assistant response from this attribute.
                        root.set_attribute(
                            "gen_ai.output.messages",
                            json.dumps([{
                                "role": "assistant",
                                "parts": [{"type": "text", "content": completion}],
                                "finish_reason": "stop",
                            }]),
                        )
                        # Legacy attributes kept for dt-evals / existing DQL dashboards.
                        root.set_attribute("gen_ai.completion", completion)
                        root.set_attribute("gen_ai.response_id", request_id)

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
