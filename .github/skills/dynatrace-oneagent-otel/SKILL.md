---
name: dynatrace-oneagent-otel
description: How OneAgent captures custom OpenTelemetry spans and the config that makes it work. Load for any tracing, span, or "no data in Dynatrace" issue.
---
1. OneAgent auto-captures custom OTel spans for Java, Go, Node.js, PHP, .NET — NOT Python.
2. OneAgent ingests only Server/Consumer span kinds by default → create tool spans as SpanKind.SERVER.
3. Custom span attributes are NOT stored until allow-listed in Settings (attribute capturing):
   gen_ai.system, gen_ai.operation.name, gen_ai.request.model, gen_ai.response.model,
   gen_ai.tool.name, gen_ai.tool.query, db.statement, user.role, snowflake.request_id,
   gen_ai.usage.input_tokens, gen_ai.usage.output_tokens,
   gen_ai.prompt, gen_ai.completion, gen_ai.context, gen_ai.response_id.
   Notes:
   - gen_ai.operation.name + request.model + response.model are required for the Dynatrace
     AI Observability app and Trace UI to recognize these as AI calls and render the
     prompt/completion inline. Without them, the data is in Grail (queryable via DQL) but
     does not surface in the AI app's built-in views.
   - gen_ai.completion / gen_ai.context / gen_ai.response_id are required for dt-evals
     (https://github.com/dynatrace-oss/dt-evals) faithfulness / relevance / hallucination evaluators.
4. Enable the OneAgent OpenTelemetry (Node.js) sensor. No OTLP exporter — OneAgent ships the data.
Verify with: fetch spans | filter span.name == "agent.run".
