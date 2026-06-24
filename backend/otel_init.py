"""OTel bootstrap. Imported first by server.py.

Sets up Traceloop with an OTLP HTTP exporter pointing at Dynatrace for both
TRACES and LOGS, and attaches an OTel LoggingHandler to Python's root logger
so everything written by FastAPI, uvicorn (access + error), our app, and any
library that uses `logging` ships to the same service entity as the spans.

Tenant model: single-tenant by default (DT_OTLP_ENDPOINT and OneAgent point
at the same Dynatrace env). Per repo policy the systemd unit also sets
DT_INJECT=false on this process so OneAgent does NOT inject codemodules,
leaving Traceloop/OTLP as the sole reporter for this AI service entity.
"""

import logging
import sys

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.resources import Resource

from traceloop.sdk import Traceloop

import os

DT_OTLP_ENDPOINT = os.environ.get("DT_OTLP_ENDPOINT")  # https://<env>.dynatrace.com/api/v2/otlp
DT_API_TOKEN = os.environ.get("DT_API_TOKEN")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "fieldops-backend")

if DT_OTLP_ENDPOINT and DT_API_TOKEN:
    base = DT_OTLP_ENDPOINT.rstrip("/")
    headers = {"Authorization": f"Api-Token {DT_API_TOKEN}"}

    # --- traces ---
    span_exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces", headers=headers)
    Traceloop.init(app_name=SERVICE_NAME, exporter=span_exporter, disable_batch=False)

    # --- logs ---
    # Use the same service.name so logs bind to the same dt.entity.service the
    # spans are published under -> the AI Obs Logs tab populates.
    log_resource = Resource.create({"service.name": SERVICE_NAME})
    logger_provider = LoggerProvider(resource=log_resource)
    set_logger_provider(logger_provider)
    log_exporter = OTLPLogExporter(endpoint=f"{base}/v1/logs", headers=headers)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    # DEBUG: also dump each OTel log record to stderr (SimpleLogRecordProcessor
    # exports immediately, no batching). Lets us verify the OTel logs pipeline
    # is processing records even if the OTLP exporter is silently failing.
    if os.environ.get("OTEL_LOG_DEBUG", "").lower() in ("1", "true", "yes"):
        logger_provider.add_log_record_processor(
            SimpleLogRecordProcessor(ConsoleLogExporter())
        )

    # Attach an OTel handler to the ROOT logger so FastAPI, uvicorn (access +
    # error), and our `fieldops` logger all flow to OTLP.
    otel_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    root = logging.getLogger()
    root.addHandler(otel_handler)
    # Also keep a stream handler so logs remain visible in journald (defense
    # in depth against silent OTLP failures, and for ssh-based debugging).
    if not any(isinstance(h, logging.StreamHandler) and h is not otel_handler for h in root.handlers):
        sh = logging.StreamHandler(stream=sys.stdout)
        sh.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        root.addHandler(sh)
    root.setLevel(logging.INFO)
else:
    # No-op tracer if OTLP env not configured (local dev / mock-only).
    Traceloop.init(app_name=SERVICE_NAME, disable_batch=False)
