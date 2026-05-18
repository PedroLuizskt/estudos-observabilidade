"""Setup manual do OpenTelemetry para o serviço spam.

Esse arquivo concentra os 4 componentes que o Dunossauro destacou na live:

    Resource  -> identifica o nosso serviço para o backend
    Provider  -> fábrica de meters/instrumentos
    Reader    -> coleta as métricas em intervalos periódicos
    Exporter  -> envia o que o reader coletou para o destino

A função ``setup_telemetria`` é chamada uma única vez na inicialização
do FastAPI (ver ``main.py``), e devolve os instrumentos prontos para
serem usados nos endpoints.

Mantemos *dois* readers em paralelo, exatamente como nos slides:
    1. ``ConsoleMetricExporter`` — imprime no terminal, ótimo para debug.
    2. ``OTLPMetricExporter``    — envia via gRPC para o LGTM/Collector.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.metrics import Counter, Histogram, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
)

SERVICE_NAME_VALUE: Final[str] = os.getenv("SERVICE_NAME", "spam")
SERVICE_VERSION_VALUE: Final[str] = "0.2.0"

# Endpoint OTLP — o LGTM expõe gRPC na 4317.
# Em docker-compose o host é "lgtm"; rodando local na máquina é "localhost".
OTLP_ENDPOINT: Final[str] = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://lgtm:4317"
)

# Quanto tempo o Reader espera entre uma coleta e outra.
# 5s é razoável para demo; em produção 30-60s é o típico.
EXPORT_INTERVAL_MS: Final[int] = int(
    os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "5000")
)


@dataclass(frozen=True, slots=True)
class Instrumentos:
    """Pacote de instrumentos customizados que os endpoints vão usar."""

    requisicoes_combo: Counter
    requisicoes_em_voo: UpDownCounter
    duracao_chamada_eggs: Histogram


def setup_telemetria() -> Instrumentos:
    """Configura o OpenTelemetry e devolve os instrumentos customizados.

    Deve ser chamado **uma única vez**, antes de qualquer endpoint
    receber tráfego — caso contrário o ``MeterProvider`` global não
    estará configurado e os instrumentos viram no-op.
    """
    # 1. RESOURCE: descreve quem é o serviço.
    # Esses atributos viram labels no Prometheus, facilitando filtrar
    # métricas por serviço/versão na hora de fazer query.
    resource = Resource.create(
        attributes={
            SERVICE_NAME: SERVICE_NAME_VALUE,
            SERVICE_VERSION: SERVICE_VERSION_VALUE,
        }
    )

    # 2. READERS + EXPORTERS: dois pipelines de saída em paralelo.
    reader_console = PeriodicExportingMetricReader(
        ConsoleMetricExporter(),
        export_interval_millis=EXPORT_INTERVAL_MS,
    )
    reader_otlp = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True),
        export_interval_millis=EXPORT_INTERVAL_MS,
    )

    # 3. PROVIDER: amarra o resource aos readers.
    provider = MeterProvider(
        resource=resource,
        metric_readers=[reader_console, reader_otlp],
    )

    # 4. Registra como provider global — a partir daqui qualquer
    # `metrics.get_meter(...)` no processo usa essa configuração.
    metrics.set_meter_provider(provider)

    # 5. Cria o meter e os instrumentos customizados.
    meter = metrics.get_meter(
        name=f"{SERVICE_NAME_VALUE}.meter",
        version=SERVICE_VERSION_VALUE,
    )

    requisicoes_combo = meter.create_counter(
        name="spam.combo.requests",
        unit="1",
        description="Total de requisições recebidas no endpoint /combo.",
    )

    requisicoes_em_voo = meter.create_up_down_counter(
        name="spam.requests.in_flight",
        unit="1",
        description=(
            "Quantas requisições estão sendo processadas "
            "neste exato instante (incrementa na entrada, "
            "decrementa na saída)."
        ),
    )

    duracao_chamada_eggs = meter.create_histogram(
        name="spam.eggs_call.duration",
        unit="ms",
        description=(
            "Tempo de cada chamada HTTP que o spam faz para o "
            "serviço eggs. Histograma porque o que interessa é "
            "a distribuição (p50, p95, p99), não só a média."
        ),
    )

    return Instrumentos(
        requisicoes_combo=requisicoes_combo,
        requisicoes_em_voo=requisicoes_em_voo,
        duracao_chamada_eggs=duracao_chamada_eggs,
    )
