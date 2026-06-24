"""Snowflake Cortex Agents adapter (Python).

Same contract as backend/agent/snowflakeClient.js (now removed): consume the
Cortex Agents :run SSE endpoint and normalize wire payloads into the canonical
event shape emitted by MockCortexClient. The rest of the backend (server.py
spans, SSE relay) and the frontend parser stay unchanged when AGENT_MODE flips
between 'mock' and 'snowflake'.

Field-name variants across Cortex API revisions are handled defensively:
  - tool_result:  result.searchResults[]            -> result.hits (count)
  - table:        table.rowType[] + table.data[][]  -> table.columns + table.rows
  - response:     usage.{prompt_tokens|input_tokens} / {completion_tokens|output_tokens}
                                                    -> tokens_in / tokens_out
Original Snowflake fields are preserved alongside the canonical ones so
consumers (gen_ai.context, dt-evals) keep full richness.
"""

import json
import os
import httpx
from httpx_sse import aconnect_sse
from .base import EVENTS


def normalize(ev, data):
    if not isinstance(data, dict):
        return {"event": ev, "data": data}

    if ev == EVENTS.TOOL_RESULT:
        result = data.get("result")
        if isinstance(result, dict):
            sr = result.get("searchResults")
            if isinstance(sr, list):
                result["hits"] = len(sr)
        return {"event": ev, "data": data}

    if ev == EVENTS.TABLE:
        t = data.get("table")
        if isinstance(t, dict):
            has_canonical = isinstance(t.get("columns"), list) and isinstance(t.get("rows"), list)
            if not has_canonical and isinstance(t.get("rowType"), list) and isinstance(t.get("data"), list):
                t["columns"] = [c.get("name") for c in t["rowType"] if isinstance(c.get("name"), str)]
                t["rows"] = t["data"]
        return {"event": ev, "data": data}

    if ev == EVENTS.DONE:
        u = data.get("usage")
        if isinstance(u, dict):
            data["tokens_in"] = u.get("prompt_tokens") or u.get("input_tokens") or 0
            data["tokens_out"] = u.get("completion_tokens") or u.get("output_tokens") or 0
        # Surface the actual model Snowflake used so server.py can override
        # gen_ai.response.model on the span. Cortex puts this under varying
        # keys depending on API revision; check both top-level and metadata.
        model = data.get("model") or (data.get("metadata") or {}).get("model")
        if model:
            data["model"] = model
        return {"event": ev, "data": data}

    return {"event": ev, "data": data}


class SnowflakeCortexClient:
    async def run(self, messages):
        host = os.environ.get("CORTEX_HOST")
        db = os.environ.get("CORTEX_DATABASE", "SNOWFLAKE_INTELLIGENCE")
        schema = os.environ.get("CORTEX_SCHEMA", "AGENTS")
        agent = os.environ.get("CORTEX_AGENT")
        pat = os.environ.get("CORTEX_PAT")
        if not (host and agent and pat):
            yield {"event": EVENTS.ERROR, "data": {"message": "Cortex env not set"}}
            return

        url = f"https://{host}/api/v2/databases/{db}/schemas/{schema}/agents/{agent}:run"
        headers = {
            "Authorization": f"Bearer {pat}",
            "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        # If CORTEX_MODEL is set, pass it; otherwise let the agent's own
        # configuration in Snowflake decide which model to use.
        body = {"messages": messages}
        cortex_model = os.environ.get("CORTEX_MODEL")
        if cortex_model:
            body["model"] = cortex_model

        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with aconnect_sse(client, "POST", url, json=body, headers=headers) as event_source:
                    async for sse in event_source.aiter_sse():
                        try:
                            data = json.loads(sse.data)
                        except Exception:
                            yield {"event": EVENTS.ERROR, "data": {"message": "bad SSE JSON"}}
                            continue
                        yield normalize(sse.event or "message", data)
            except httpx.HTTPStatusError as e:
                yield {"event": EVENTS.ERROR, "data": {"message": f"HTTP {e.response.status_code}"}}
            except Exception as e:
                yield {"event": EVENTS.ERROR, "data": {"message": str(e)}}
