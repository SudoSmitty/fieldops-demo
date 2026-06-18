import { EVENTS } from './base.js';

// Snowflake Cortex Agents adapter.
//
// This is the REAL Snowflake client. It calls the Cortex Agents :run endpoint
// over SSE and normalizes Snowflake's wire payloads into the canonical event
// shape defined by MockCortexClient (see backend/agent/mockClient.js). The
// rest of the backend (server.js spans, SSE relay) and the frontend parser
// stay unchanged when AGENT_MODE flips between 'mock' and 'snowflake'.
//
// Field-name variants across Cortex Agents API revisions are handled
// defensively (every Snowflake-side field uses optional chaining and falls
// back without throwing). When first wiring against a live tenant, re-verify
// the names below — Snowflake has revved these between API versions:
//   - tool_result:  result.searchResults[]      -> result.hits (count)
//   - table:        table.rowType[] + table.data[][] -> table.columns + table.rows
//   - response:     usage.prompt_tokens / completion_tokens
//                     (or input_tokens / output_tokens) -> tokens_in / tokens_out
// Original Snowflake fields are preserved alongside the canonical ones so
// consumers (e.g. gen_ai.context) keep full richness.

export function normalize(ev, data) {
  if (!data || typeof data !== 'object') return { event: ev, data };

  if (ev === EVENTS.TOOL_RESULT) {
    const result = data.result;
    if (result && typeof result === 'object') {
      const sr = result.searchResults;
      if (Array.isArray(sr)) {
        result.hits = sr.length;
      }
    }
    return { event: ev, data };
  }

  if (ev === EVENTS.TABLE) {
    const t = data.table;
    if (t && typeof t === 'object') {
      const hasCanonical = Array.isArray(t.columns) && Array.isArray(t.rows);
      if (!hasCanonical && Array.isArray(t.rowType) && Array.isArray(t.data)) {
        t.columns = t.rowType.map(c => c?.name).filter(n => typeof n === 'string');
        t.rows = t.data;
      }
    }
    return { event: ev, data };
  }

  if (ev === EVENTS.DONE) {
    const u = data.usage;
    if (u && typeof u === 'object') {
      data.tokens_in = u.prompt_tokens ?? u.input_tokens ?? 0;
      data.tokens_out = u.completion_tokens ?? u.output_tokens ?? 0;
    }
    return { event: ev, data };
  }

  // status, thinking, text.delta, tool_use, error, and anything else: pass through.
  return { event: ev, data };
}

export class SnowflakeCortexClient {
  async *run(messages) {
    const { CORTEX_HOST, CORTEX_DATABASE='SNOWFLAKE_INTELLIGENCE',
            CORTEX_SCHEMA='AGENTS', CORTEX_AGENT, CORTEX_PAT } = process.env;
    const resp = await fetch(
      `https://${CORTEX_HOST}/api/v2/databases/${CORTEX_DATABASE}/schemas/${CORTEX_SCHEMA}/agents/${CORTEX_AGENT}:run`,
      { method:'POST', headers:{
        'Authorization':`Bearer ${CORTEX_PAT}`,
        'X-Snowflake-Authorization-Token-Type':'PROGRAMMATIC_ACCESS_TOKEN',
        'Content-Type':'application/json',
        'Accept':'text/event-stream' },
        body: JSON.stringify({ model:'claude-4-sonnet', messages }) });
    if (!resp.ok) { yield { event: EVENTS.ERROR, data:{ message:`HTTP ${resp.status}` } }; return; }
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf='';
    for (;;) { const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream:true }); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0,i); buf = buf.slice(i+2);
        let ev='message', data=''; block.split('\n').forEach(l=>{
          if (l.startsWith('event:')) ev=l.slice(6).trim();
          else if (l.startsWith('data:')) data+=l.slice(5).trim(); });
        if (data) {
          let parsed;
          try { parsed = JSON.parse(data); }
          catch { yield { event: EVENTS.ERROR, data:{ message:'bad SSE JSON' } }; continue; }
          yield normalize(ev, parsed);
        }
      } }
  }
}
