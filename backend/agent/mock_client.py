"""Mock Cortex Agents client. Same scenarios as backend/agent/mockClient.js
(now removed): two routes (`data` -> Cortex Analyst + SQL + table, `knowledge`
-> Cortex Search + answer) selected by a regex on the prompt.

Yields the canonical event dicts defined by EVENTS.
"""

import asyncio
import random
import re
from .base import EVENTS

SCENARIOS = {
    "data": {
        "thinking": (
            "User wants work-order data. I'll query with Cortex Analyst, "
            "then summarize by site."
        ),
        "tool": "cortex_analyst",
        "sql": (
            "SELECT site, COUNT(*) AS overdue FROM work_orders "
            "WHERE status='OPEN' AND due_date < CURRENT_DATE GROUP BY site"
        ),
        "table": {
            "columns": ["site", "overdue"],
            "rows": [
                ["Pump Station 4", 7],
                ["Compressor Yard", 3],
                ["North Intake", 2],
            ],
        },
        "answer": (
            "There are 12 overdue work orders. Pump Station 4 has the most (7), "
            "followed by Compressor Yard (3) and North Intake (2)."
        ),
        "tokens": (180, 90),
    },
    "knowledge": {
        "thinking": "Knowledge question. I'll use Cortex Search over the asset manuals.",
        "tool": "cortex_search",
        "sql": None,
        "table": None,
        "answer": (
            "Per the asset manual, the quarterly procedure for centrifugal pumps "
            "is: isolate, lock out, inspect seals, check vibration, log readings."
        ),
        "tokens": (210, 140),
    },
}

_KNOWLEDGE_RE = re.compile(r"\b(how|procedure|manual|maintenance|inspect)\b", re.IGNORECASE)


class MockCortexClient:
    async def run(self, messages):
        prompt = messages[-1]["content"]
        sc = SCENARIOS["knowledge"] if _KNOWLEDGE_RE.search(prompt) else SCENARIOS["data"]

        yield {"event": EVENTS.STATUS, "data": {"message": "Planning..."}}
        await asyncio.sleep(0.3)
        yield {"event": EVENTS.THINKING, "data": {"text": sc["thinking"]}}
        yield {
            "event": EVENTS.TOOL_USE,
            "data": {"name": sc["tool"], "input": {"query": prompt}},
        }
        await asyncio.sleep(0.4 + random.random() * 0.8)
        result = {"sql": sc["sql"]} if sc["sql"] else {"hits": 4}
        yield {
            "event": EVENTS.TOOL_RESULT,
            "data": {"name": sc["tool"], "result": result},
        }
        if sc["table"]:
            yield {"event": EVENTS.TABLE, "data": {"table": sc["table"]}}
        for part in sc["answer"].split(". "):
            yield {"event": EVENTS.TEXT_DELTA, "data": {"text": part + ". "}}
            await asyncio.sleep(0.1)
        yield {
            "event": EVENTS.DONE,
            "data": {
                "tokens_in": sc["tokens"][0],
                "tokens_out": sc["tokens"][1],
                "tool": sc["tool"],
            },
        }
