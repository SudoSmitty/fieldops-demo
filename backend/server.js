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
// Emit logs with the active trace/span context so OneAgent links them to the
// AI Observability Logs tab (and to the span in the trace UI).
const log = (lvl, msg, extra={}) => {
  const sc = trace.getActiveSpan()?.spanContext();
  console.log(JSON.stringify({
    level:lvl, msg, ts:new Date().toISOString(),
    'trace.id': sc?.traceId, 'span.id': sc?.spanId,
    ...extra
  }));
};
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
  // OTel GenAI conventions — required for Dynatrace AI Observability app to surface
  root.setAttribute('gen_ai.system','snowflake.cortex');
  root.setAttribute('gen_ai.operation.name','chat');
  root.setAttribute('gen_ai.request.model','claude-4-sonnet');
  root.setAttribute('gen_ai.response.model','claude-4-sonnet');
  root.setAttribute('user.role', role);
  root.setAttribute('snowflake.request_id', requestId);   // join key for later
  root.setAttribute('gen_ai.prompt', prompt);
  // OTel GenAI semantic-convention span events. Dynatrace AI Observability's
  // "Prompt trace" panel reads input/output from these events, not from the
  // gen_ai.prompt/completion attributes (those feed DQL + the token chart only).
  root.addEvent('gen_ai.user.message', { content: prompt, role: 'user' });
  const ctx = trace.setSpan(context.active(), root);
  const openTools = new Map();
  let completion = '';
  // Run the entire request inside the root span's context so every log() call
  // — including tool-invoked/tool-result lines — picks up trace.id/span.id.
  await context.with(ctx, async () => {
  log('info','agent request', { requestId, role });
  try {
    for await (const ev of client().run([{ role:'user', content: prompt }])) {
      if (ev.event === EVENTS.TOOL_USE) {
        const span = tracer.startSpan(`tool.${ev.data.name}`, { kind: SpanKind.SERVER }, ctx);
        span.setAttribute('gen_ai.system','snowflake.cortex');
        span.setAttribute('gen_ai.operation.name','execute_tool');
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
        // OTel GenAI assistant response — the AI Obs Prompt trace panel renders
        // this as "Prompt output".
        root.addEvent('gen_ai.choice', {
          'finish_reason': 'stop',
          'index': 0,
          'message': JSON.stringify({ role: 'assistant', content: completion })
        });
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
});

app.listen(8000, () => log('info','backend listening', { port: 8000 }));
