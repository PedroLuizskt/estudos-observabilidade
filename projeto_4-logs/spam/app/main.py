"""Serviço spam — agora com a trinca completa de observabilidade.

Acumulando as aulas:
    Aula 2: métricas customizadas (counter, up-down counter, histograma)
    Aula 3: tracing manual com spans + propagação de contexto httpx
    Aula 4: logs estruturados via OTel (essa aula) ⭐

O detalhe importante da aula 4: TODO ``logger.info(...)`` emitido
dentro de um endpoint instrumentado é automaticamente correlacionado
com o trace ativo. No Loki, cada linha de log carrega o ``trace_id``,
permitindo navegar de um trace lento para os logs daquela requisição
específica.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final

import httpx
from fastapi import FastAPI, HTTPException, Request
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from pydantic import BaseModel

from .logging_otel import setup_logging
from .telemetria import Instrumentos, setup_telemetria
from .tracing import setup_tracing

SERVICE_NAME: Final[str] = os.getenv("SERVICE_NAME", "spam")
EGGS_URL: Final[str] = os.getenv("EGGS_URL", "http://eggs:8000")
VERSION: Final[str] = "0.4.0"

# Estado de módulo populado pelo lifespan, antes de qualquer request.
_instrumentos: Instrumentos | None = None
_tracer: Tracer | None = None
logger = logging.getLogger(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Inicializa os três pipelines de telemetria antes do primeiro request.

    Ordem importa: logs primeiro, para que a inicialização dos outros
    dois (métricas, traces) já fique registrada nos logs do Loki.
    """
    global _instrumentos, _tracer

    setup_logging()
    logger.info("startup: configurando observabilidade do spam")

    _instrumentos = setup_telemetria()
    logger.info("startup: métricas OK")

    _tracer = setup_tracing()
    logger.info("startup: tracing OK")

    logger.info("startup completo — pronto para receber requisições")
    yield
    logger.info("shutdown do spam")


app = FastAPI(
    title="spam",
    description="Serviço cliente, com métricas (aula 2), traces (aula 3) e logs (aula 4).",
    version=VERSION,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware: gauge "em voo" — mesmo da aula 2, agora com log.
# ---------------------------------------------------------------------------
@app.middleware("http")
async def medir_em_voo(request: Request, call_next):  # type: ignore[no-untyped-def]
    if _instrumentos is None:
        return await call_next(request)

    rota = request.url.path
    _instrumentos.requisicoes_em_voo.add(1, attributes={"rota": rota})
    try:
        return await call_next(request)
    finally:
        _instrumentos.requisicoes_em_voo.add(-1, attributes={"rota": rota})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class Saude(BaseModel):
    status: str
    service: str
    version: str


class Combo(BaseModel):
    saudacao: str
    dado_do_eggs: int
    origem_eggs: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_model=Saude)
async def home() -> Saude:
    return Saude(status="ok", service=SERVICE_NAME, version=VERSION)


@app.get("/health", response_model=Saude)
async def health() -> Saude:
    return Saude(status="healthy", service=SERVICE_NAME, version=VERSION)


@app.get("/saudacao/{nome}")
async def saudacao(nome: str) -> dict[str, str]:
    logger.info("saudacao requisitada", extra={"nome": nome})
    return {"mensagem": f"Olá, {nome}! Bem-vinda ao spam."}


@app.get("/combo/{nome}", response_model=Combo)
async def combo(nome: str) -> Combo:
    """Endpoint instrumentado com a trinca: métricas + spans + logs.

    Os logs emitidos aqui automaticamente herdam o ``trace_id`` do span
    ativo. No Loki, você pode buscar ``{service_name="spam"} |= "combo"``
    e ver as linhas correspondentes a cada requisição, com o trace_id
    para clicar e abrir a cascata correspondente no Tempo.
    """
    assert _instrumentos is not None
    assert _tracer is not None

    with _tracer.start_as_current_span(
        "spam.combo",
        kind=SpanKind.INTERNAL,
        attributes={"app.nome_param": nome, "app.endpoint": "/combo"},
    ) as span:
        # Esse log já vai sair com trace_id e span_id no record OTLP.
        logger.info("combo iniciado", extra={"nome": nome})
        inicio = time.perf_counter()

        try:
            with _tracer.start_as_current_span(
                "spam.combo.chamada_eggs",
                kind=SpanKind.INTERNAL,
                attributes={"http.target": "/dados/aleatorio"},
            ):
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resposta = await client.get(
                        f"{EGGS_URL}/dados/aleatorio"
                    )
                    resposta.raise_for_status()

        except httpx.HTTPError as erro:
            # Log do erro: aparece no Loki com nível ERROR e o trace_id
            # da requisição que falhou — facilita postmortem.
            logger.error(
                "falha na chamada ao eggs",
                extra={"erro": str(erro), "rota_eggs": "/dados/aleatorio"},
            )
            span.add_event(
                "falha_na_chamada_eggs",
                attributes={"erro": str(erro)},
            )
            span.set_status(Status(StatusCode.ERROR, description=str(erro)))

            _instrumentos.requisicoes_combo.add(
                1,
                attributes={
                    "status": "erro",
                    "rota_eggs": "/dados/aleatorio",
                },
            )
            raise HTTPException(
                status_code=502,
                detail=f"eggs respondeu mal: {erro}",
            ) from erro

        finally:
            duracao_ms = (time.perf_counter() - inicio) * 1000
            _instrumentos.duracao_chamada_eggs.record(
                duracao_ms,
                attributes={"rota_eggs": "/dados/aleatorio"},
            )

        _instrumentos.requisicoes_combo.add(
            1,
            attributes={"status": "ok", "rota_eggs": "/dados/aleatorio"},
        )

        dados = resposta.json()
        span.set_attribute("app.dado_do_eggs", dados["valor"])

        logger.info(
            "combo concluído",
            extra={
                "nome": nome,
                "dado_do_eggs": dados["valor"],
                "duracao_ms": round(duracao_ms, 2),
            },
        )

        return Combo(
            saudacao=f"Olá, {nome}!",
            dado_do_eggs=dados["valor"],
            origem_eggs=dados["origem"],
        )


@app.get("/tarefa/{n}")
async def tarefa(n: int) -> dict[str, object]:
    """N chamadas sequenciais — log por iteração para ver o progresso."""
    assert _instrumentos is not None
    assert _tracer is not None

    if n < 1 or n > 10:
        logger.warning("tarefa rejeitada — n fora do intervalo", extra={"n": n})
        raise HTTPException(status_code=400, detail="n deve estar entre 1 e 10")

    with _tracer.start_as_current_span(
        "spam.tarefa", attributes={"app.n_chamadas": n}
    ):
        logger.info("tarefa iniciada", extra={"n_chamadas": n})

        resultados: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for i in range(n):
                with _tracer.start_as_current_span(
                    "spam.tarefa.iteracao",
                    attributes={"app.iteracao": i},
                ):
                    inicio = time.perf_counter()
                    resposta = await client.get(f"{EGGS_URL}/processamento")
                    resposta.raise_for_status()
                    duracao_ms = (time.perf_counter() - inicio) * 1000
                    _instrumentos.duracao_chamada_eggs.record(
                        duracao_ms,
                        attributes={"rota_eggs": "/processamento"},
                    )
                    resultados.append(resposta.json())
                    logger.debug(
                        "iteracao concluida",
                        extra={"i": i, "duracao_ms": round(duracao_ms, 2)},
                    )

        logger.info("tarefa concluída", extra={"n_chamadas": n})
        return {"total_chamadas": n, "resultados": resultados}


@app.get("/eco")
async def eco(mensagem: str = "pong") -> dict[str, str]:
    return {"eco": mensagem}
