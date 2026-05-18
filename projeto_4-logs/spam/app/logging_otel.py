"""Setup manual de logs OpenTelemetry para o serviço spam.

Esse é o terceiro e último módulo de telemetria do spam, completando
a trinca da observabilidade:

    telemetria.py  -> métricas (aula 2)
    tracing.py     -> spans/traces (aula 3)
    logging_otel.py -> logs (aula 4)  ← você está aqui

Como logs OTel funciona DIFERENTE de métricas e traces
======================================================

Para métricas e traces a gente importou ``opentelemetry.metrics`` e
``opentelemetry.trace`` e usou suas APIs diretamente.

Para logs, o OTel não cria uma API "concorrente" do ``logging`` padrão
do Python. Em vez disso, ele oferece uma **bridge** (ponte):

    logging.getLogger()  ─ .info("...") ─▶  Handler do OTel  ─▶ OTLP

Isso significa que:
    * Você continua usando ``logging.getLogger(__name__).info(...)``
      como sempre — sua aplicação NÃO sabe que o OTel existe.
    * O setup configura um ``LoggingHandler`` do OTel e o anexa ao
      logger raiz. A partir daí, todo ``logger.info()`` vira um
      ``LogRecord`` OTel e é exportado via OTLP.
    * Quando o log é emitido dentro de um span ativo (o que acontece
      automaticamente dentro de um endpoint instrumentado), o handler
      injeta ``trace_id`` e ``span_id`` no record. Resultado: no Loki
      cada linha de log carrega o trace_id da requisição que a gerou.

Nota sobre os imports com underscore
=====================================

Embora a especificação do OTel marque "Logs API" e "Logs SDK" como
**Stable**, o pacote Python ainda mantém os imports prefixados com
``_`` (``opentelemetry._logs``, ``opentelemetry.sdk._logs``) por
compatibilidade histórica. Quando o pacote remover os underscores,
basta substituir nos imports — o resto da API permanece igual.

Veja: https://opentelemetry.io/docs/specs/otel/logs/api/
"""
from __future__ import annotations

import logging
import os
from typing import Final

# Imports com underscore — sinal de logs ainda mantém esse prefixo
# no pacote Python (mesmo com a especificação sendo Stable).
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
)

SERVICE_NAME_VALUE: Final[str] = os.getenv("SERVICE_NAME", "spam")
SERVICE_VERSION_VALUE: Final[str] = "0.4.0"

OTLP_ENDPOINT: Final[str] = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://lgtm:4317"
)

# Nível padrão dos logs emitidos pela aplicação.
LOG_LEVEL: Final[str] = os.getenv("SPAM_LOG_LEVEL", "INFO").upper()


def setup_logging() -> logging.Logger:
    """Configura o pipeline OTel de logs e devolve o logger raiz.

    Após chamar essa função, qualquer ``logging.getLogger(__name__)``
    no processo emite logs que:
        1. Aparecem no terminal (handler padrão do Python).
        2. São enviados via OTLP para o LGTM/Loki.
        3. Carregam ``trace_id`` e ``span_id`` se emitidos dentro de
           um span ativo (correlação automática com tracing).
    """
    # 1. RESOURCE: mesmos atributos das aulas 2 e 3.
    # Permite correlação no Grafana entre métricas, traces e logs
    # do mesmo service.name.
    resource = Resource.create(
        attributes={
            SERVICE_NAME: SERVICE_NAME_VALUE,
            SERVICE_VERSION: SERVICE_VERSION_VALUE,
        }
    )

    # 2. PROVIDER de logs (análogo a TracerProvider e MeterProvider).
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)

    # 3. PROCESSOR + EXPORTER: Batch é o padrão para produção.
    # Igual ao BatchSpanProcessor da aula 3: acumula records e
    # envia em lote periodicamente.
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True)
        )
    )

    # 4. HANDLER: a "ponte" entre o logging do Python e o pipeline OTel.
    # Esse handler é instalado no logger raiz, então qualquer logger
    # filho (qualquer `logging.getLogger(...)`) herda automaticamente.
    handler = LoggingHandler(
        level=getattr(logging, LOG_LEVEL),
        logger_provider=logger_provider,
    )

    # 5. Configura o logger raiz com:
    #    a) o handler do OTel (envia para o Loki)
    #    b) um StreamHandler clássico (continua imprimindo no terminal)
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    # Limpa handlers que outras libs (uvicorn) possam ter instalado
    # para evitar duplicação de saída no terminal.
    root.addHandler(handler)

    # Garante que o nosso formato apareça também no stdout, com info
    # útil para debug local (timestamp + nome do logger + mensagem).
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, LoggingHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            )
        )
        root.addHandler(stream)

    # Loggers do uvicorn por padrão não propagam para o root;
    # forçamos propagação para que os logs de acesso também sejam
    # capturados pelo handler do OTel.
    for nome in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        log = logging.getLogger(nome)
        log.propagate = True
        log.setLevel(getattr(logging, LOG_LEVEL))

    return logging.getLogger(SERVICE_NAME_VALUE)
