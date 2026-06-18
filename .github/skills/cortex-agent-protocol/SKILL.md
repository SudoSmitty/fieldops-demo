---
name: cortex-agent-protocol
description: The Snowflake Cortex Agents SSE event contract and the swappable-client rule. Load when building or changing the backend agent clients or the frontend stream parser.
---
Event names (exact): response.status, response.thinking, response.tool_use,
response.tool_result, response.table, response.text.delta, response, error.
Swap rule: MockCortexClient is canonical. SnowflakeCortexClient is an adapter
that translates Snowflake's wire shape into the canonical fields before yielding,
so server.js and the frontend parser never change when AGENT_MODE flips:
  - tool_result: `result.searchResults[]` -> add `result.hits` (count)
  - table:       `table.rowType[]` + `table.data[][]` -> add `table.columns` + `table.rows`
  - response:    `usage.prompt_tokens` / `completion_tokens` (or `input_tokens` /
                 `output_tokens`) -> add `tokens_in` / `tokens_out`
Original Snowflake fields are preserved alongside the canonical ones for richness.
The frontend parses SSE as `event:` / `data:` blocks separated by a blank line.
