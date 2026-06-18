import './otel.js';
import express from 'express';
import { randomUUID } from 'crypto';
import { trace, SpanKind, context } from '@opentelemetry/api';
import { MockCortexClient } from './agent/mockClient.js';
import { SnowflakeCortexClient } from './agent/snowflakeClient.js';
import { EVENTS } from './agent/base.js';

const tracer = trace.getTracer('fieldops-copilot');
const app = express();
app.use(express.json());
const log = (lvl, msg, extra={}) =>
  console.log(JSON.stringify({ level:lvl, msg, ts:new Date().toISOString(), ...extra }));
const client = () =>
  process.env.AGENT_MODE === 'snowflake' ? new SnowflakeCortexClient() : new MockCortexClient();

app.post('/api/agent/run', async (req, res) => {
  const { prompt, role='technician' } = req.body;
  const requestId = randomUUID();
  res.setHeader('Content-Type','text/event-stream');
  res.setHeader('Cache-Control','no-cache');
  res.flushHeaders();

  // Root agent span (child of the Express server span OneAgent already created)
  const root = tracer.startSpan('agent.run', { kind: SpanKind.SERVER });
  root.setAttribute('gen_ai.system','snowflake.cortex');
  root.setAttribute('user.role', role);
  root.setAttribute('snowflake.request_id', requestId);   // join key for later
  root.setAttribute('gen_ai.prompt', prompt);
  log('info','agent request', { requestId, role });

  const ctx = trace.setSpan(context.active(), root);
  const openTools = new Map();
  let completion = '';
  try {
    for await (const ev of client().run([{ role:'user', content: prompt }])) {
      if (ev.event === EVENTS.TOOL_USE) {
        const span = tracer.startSpan(`tool.${ev.data.name}`, { kind: SpanKind.SERVER }, ctx);
        span.setAttribute('gen_ai.tool.name', ev.data.name);
        if (ev.data.input?.query) span.setAttribute('gen_ai.tool.query', ev.data.input.query);
        openTools.set(ev.data.name, span);
        log('info','tool invoked', { requestId, tool: ev.data.name });
      } else if (ev.event === EVENTS.TOOL_RESULT) {
        const span = openTools.get(ev.data.name);
        if (span) { if (ev.data.result?.sql) span.setAttribute('db.statement', ev.data.result.sql);
                    span.setAttribute('gen_ai.context', JSON.stringify(ev.data.result || {}));
                    span.end(); openTools.delete(ev.data.name); }
        log('info','tool result', { requestId, tool: ev.data.name });
      } else if (ev.event === EVENTS.TEXT_DELTA) {
        completion += ev.data.text;
      } else if (ev.event === EVENTS.DONE) {
        root.setAttribute('gen_ai.usage.input_tokens', ev.data.tokens_in ?? 0);
        root.setAttribute('gen_ai.usage.output_tokens', ev.data.tokens_out ?? 0);
        root.setAttribute('gen_ai.completion', completion);
        root.setAttribute('gen_ai.response_id', requestId);
      } else if (ev.event === EVENTS.ERROR) {
        root.setAttribute('error', true); log('error','agent error', { requestId, ...ev.data });
      }
      res.write(`event: ${ev.event}\ndata: ${JSON.stringify(ev.data)}\n\n`);
    }
  } catch (e) {
    root.setAttribute('error', true); log('error','stream failed', { requestId, err:String(e) });
  } finally {
    for (const s of openTools.values()) s.end();
    root.end(); res.end();
  }
});

app.listen(8000, () => log('info','backend listening', { port: 8000 }));
