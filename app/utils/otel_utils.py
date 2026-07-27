import os
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def init_otel(service_name):
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        resource = Resource(attributes={
            "service.name": service_name,
            "batch_id": os.environ.get("OTEL_BATCH_ID", ""),
        })
        provider = TracerProvider(resource=resource)
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318").rstrip("/")
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
