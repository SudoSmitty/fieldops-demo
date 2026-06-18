import { EVENTS } from './base.js';
const sleep = ms => new Promise(r => setTimeout(r, ms));

const SCENARIOS = {
  data: {
    thinking: "User wants work-order data. I'll query with Cortex Analyst, then summarize by site.",
    tool: 'cortex_analyst',
    sql: "SELECT site, COUNT(*) AS overdue FROM work_orders WHERE status='OPEN' AND due_date < CURRENT_DATE GROUP BY site",
    table: { columns:['site','overdue'], rows:[['Pump Station 4',7],['Compressor Yard',3],['North Intake',2]] },
    answer: "There are 12 overdue work orders. Pump Station 4 has the most (7), followed by Compressor Yard (3) and North Intake (2).",
    tokens: [180, 90]
  },
  knowledge: {
    thinking: "Knowledge question. I'll use Cortex Search over the asset manuals.",
    tool: 'cortex_search', sql: null, table: null,
    answer: "Per the asset manual, the quarterly procedure for centrifugal pumps is: isolate, lock out, inspect seals, check vibration, log readings.",
    tokens: [210, 140]
  }
};

export class MockCortexClient {
  async *run(messages) {
    const prompt = messages[messages.length - 1].content;
    const sc = /\bhow\b|procedure|manual|maintenance|inspect/i.test(prompt)
      ? SCENARIOS.knowledge : SCENARIOS.data;
    yield { event: EVENTS.STATUS, data: { message: 'Planning...' } };
    await sleep(300);
    yield { event: EVENTS.THINKING, data: { text: sc.thinking } };
    yield { event: EVENTS.TOOL_USE, data: { name: sc.tool, input: { query: prompt } } };
    await sleep(400 + Math.random() * 800);
    yield { event: EVENTS.TOOL_RESULT,
            data: { name: sc.tool, result: sc.sql ? { sql: sc.sql } : { hits: 4 } } };
    if (sc.table) yield { event: EVENTS.TABLE, data: { table: sc.table } };
    for (const part of sc.answer.split('. '))
      { yield { event: EVENTS.TEXT_DELTA, data: { text: part + '. ' } }; await sleep(100); }
    yield { event: EVENTS.DONE, data: { tokens_in: sc.tokens[0], tokens_out: sc.tokens[1], tool: sc.tool } };
  }
}
