"""Setup manual de tracing OpenTelemetry para o serviço spam.

Espelha o que fizemos em ``telemetria.py`` (métricas) na aula 2, mas
agora para o sinal *traces*. Os componentes principais são:

    Resource       -> identifica o serviço (compartilhado com métricas)
    TracerProvider -> fábrica de Tracers/spans
    SpanProcessor  -> decide *quando* e *como* exportar os spans
    SpanExporter   -> sabe falar OTLP (ou Console, etc.)

Diferença crucial em relação a métricas:

    * Métricas são **amostradas periodicamente** (Reader → Exporter).
    * Spans são **eventos discretos**, exportados via SpanProcessor:
      - ``SimpleSpanProcessor`` exporta um span imediatamente após o
        ``end()``. Bom para debug, ruim para performance.
      - ``BatchSpanProcessor`` acumula spans e envia em lote. Padrão
        para produção (e o que vamos usar).

Mantemos dois processors em paralelo, no mesmo espírito da aula 2:
    1. ``ConsoleSpanExporter`` (via Simple) — debug visual no terminal.
    2. ``OTLPSpanExporter``    (via Batch)  — envia para o LGTM/Tempo.
"""
from __future__ import annotations

import os
from typing import Final

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Tracer

SERVICE_NAME_VALUE: Final[str] = os.getenv("SERVICE_NAME", "spam")
SERVICE_VERSION_VALUE: Final[str] = "0.3.0"

OTLP_ENDPOINT: Final[str] = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://lgtm:4317"
)

# Quando True, exporta também para o console (verboso, bom em dev).
TRACE_TO_CONSOLE: Final[bool] = (
    os.getenv("SPAM_TRACE_TO_CONSOLE", "false").lower() == "true"
)


def setup_tracing() -> Tracer:
    """Configura o tracing OTel e devolve o tracer pronto para uso.

    Deve ser chamado **uma única vez** no startup. Idempotente o
    suficiente para sobreviver a reloads do uvicorn em dev: se um
    TracerProvider já estiver configurado, simplesmente retorna o
    tracer associado ao provider existente.
    """
    # Idempotência: o SDK fica ranzinza se você setar dois providers.
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return trace.get_tracer(SERVICE_NAME_VALUE, SERVICE_VERSION_VALUE)

    # 1. RESOURCE: mesmas chaves usadas para métricas.
    # No backend (Tempo/Prometheus) o atributo service.name é o que
    # liga trace e métrica do mesmo serviço.
    resource = Resource.create(
        attributes={
            SERVICE_NAME: SERVICE_NAME_VALUE,
            SERVICE_VERSION: SERVICE_VERSION_VALUE,
        }
    )

    # 2. PROVIDER global de traces.
    provider = TracerProvider(resource=resource)

    # 3. EXPORTER + PROCESSOR para produção (Batch é o padrão certo).
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
        )
    )

    # 4. (Opcional) Console para debug. Usa Simple processor de
    #    propósito: quero ver cada span imediatamente no log.
    if TRACE_TO_CONSOLE:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # 5. Registra como provider global. A partir daqui qualquer
    #    `trace.get_tracer(...)` no processo usa essa configuração.
    trace.set_tracer_provider(provider)

    # 6. AUTO-INSTRUMENTAÇÃO do httpx.
    # Esse é o ponto que faz a *propagação de contexto* funcionar:
    # toda chamada httpx do spam para o eggs ganha automaticamente
    # o header W3C `traceparent`, garantindo que o eggs continue
    # o MESMO trace iniciado no spam.
    HTTPXClientInstrumentor().instrument()

    return trace.get_tracer(SERVICE_NAME_VALUE, SERVICE_VERSION_VALUE)
