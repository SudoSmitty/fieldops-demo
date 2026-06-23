"""Canonical Cortex Agents SSE event names.

Mirrors backend/agent/base.js (now removed) one-for-one so the frontend
parser and the OTel span code stay framework-agnostic. See
.github/skills/cortex-agent-protocol/SKILL.md.
"""

from types import SimpleNamespace

EVENTS = SimpleNamespace(
    STATUS="response.status",
    THINKING="response.thinking",
    TOOL_USE="response.tool_use",
    TOOL_RESULT="response.tool_result",
    TABLE="response.table",
    TEXT_DELTA="response.text.delta",
    DONE="response",
    ERROR="error",
)
