"""OTel bootstrap. Imported first by server.py.

Sets up Traceloop with an OTLP HTTP exporter pointing at Dynatrace. This
process is also under OneAgent's Python sensor (we intentionally do NOT
exclude it per repo policy), and the two streams land in DIFFERENT tenants:

- OneAgent  -> Sprint tenant (infra, host, process, RUM, logs)
- Traceloop -> Live tenant   (LLM / gen_ai spans for the AI Observability app)

That separation avoids the classic OneAgent+OTel duplicate-service-entity
symptom in a single tenant. In a real customer deployment with one tenant,
either exclude the AI app PG from OneAgent codemodules, or accept that the
service may surface twice in service detection.
"""

import os
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from traceloop.sdk import Traceloop

DT_OTLP_ENDPOINT = os.environ.get("DT_OTLP_ENDPOINT")  # https://<env>.live.dynatrace.com/api/v2/otlp
DT_API_TOKEN = os.environ.get("DT_API_TOKEN")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "fieldops-backend")

if DT_OTLP_ENDPOINT and DT_API_TOKEN:
    exporter = OTLPSpanExporter(
        endpoint=f"{DT_OTLP_ENDPOINT.rstrip('/')}/v1/traces",
        headers={"Authorization": f"Api-Token {DT_API_TOKEN}"},
    )
    Traceloop.init(
        app_name=SERVICE_NAME,
        exporter=exporter,
        disable_batch=False,
    )
else:
    # No-op tracer if OTLP env not configured (local dev / mock-only).
    Traceloop.init(app_name=SERVICE_NAME, disable_batch=False)
