"""Serviço spam — agora com métricas + traces.

Acumula a instrumentação:
    * Aula 2: métricas customizadas (counter, up-down counter, histograma).
    * Aula 3: tracing manual com spans + propagação de contexto via httpx.

Os endpoints continuam os mesmos da aula 2 (compatibilidade); o que
muda é o que acontece *dentro* deles em termos de telemetria.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final

import httpx
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace as otel_trace
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from pydantic import BaseModel

from .telemetria import Instrumentos, setup_telemetria
from .tracing import setup_tracing

SERVICE_NAME: Final[str] = os.getenv("SERVICE_NAME", "spam")
EGGS_URL: Final[str] = os.getenv("EGGS_URL", "http://eggs:8000")
VERSION: Final[str] = "0.3.0"

# Estado de módulo populado pelo lifespan, antes de qualquer request.
_instrumentos: Instrumentos | None = None
_tracer: Tracer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Inicializa métricas e traces antes do primeiro request."""
    global _instrumentos, _tracer
    _instrumentos = setup_telemetria()
    _tracer = setup_tracing()
    yield


app = FastAPI(
    title="spam",
    description="Serviço cliente, com métricas (aula 2) e traces (aula 3).",
    version=VERSION,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware: gauge "em voo" — herdado da aula 2.
# Observação importante: a partir da aula 3, o FastAPI também é
# auto-instrumentado para tracing (ver `eggs/`), mas no spam mantemos
# a instrumentação manual didática como antes.
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
    return {"mensagem": f"Olá, {nome}! Bem-vinda ao spam."}


@app.get("/combo/{nome}", response_model=Combo)
async def combo(nome: str) -> Combo:
    """Endpoint instrumentado com métricas + spans manuais.

    Decomposição em spans:
        1. Span pai "spam.combo" — vida útil do endpoint todo.
        2. Span filho "spam.combo.chamada_eggs" — só o trecho HTTP.

    A chamada httpx para o eggs **já está auto-instrumentada** (ver
    tracing.py), então cria seu próprio span de cliente HTTP que
    aparece como filho de "spam.combo.chamada_eggs". Isso te dá
    três níveis de spans para essa rota, todos no mesmo trace_id.
    """
    assert _instrumentos is not None
    assert _tracer is not None

    # Span pai: registra contexto de negócio (qual usuário, etc.)
    with _tracer.start_as_current_span(
        "spam.combo",
        kind=SpanKind.INTERNAL,
        attributes={"app.nome_param": nome, "app.endpoint": "/combo"},
    ) as span:
        inicio = time.perf_counter()

        try:
            # Span filho: trecho da chamada externa. O httpx
            # auto-instrumentado vai aninhar um span CLIENT abaixo
            # desse, propagando o traceparent para o eggs.
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
            # Adicionar evento dentro do span torna o erro buscável
            # no Tempo. Mais útil que só marcar status=ERROR.
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

        # Atributo no span pai: o valor que o eggs retornou.
        # Útil para correlação: "qual trace deu valor X?"
        span.set_attribute("app.dado_do_eggs", dados["valor"])

        return Combo(
            saudacao=f"Olá, {nome}!",
            dado_do_eggs=dados["valor"],
            origem_eggs=dados["origem"],
        )


@app.get("/tarefa/{n}")
async def tarefa(n: int) -> dict[str, object]:
    """N chamadas sequenciais — cada uma vira um span próprio."""
    assert _instrumentos is not None
    assert _tracer is not None

    if n < 1 or n > 10:
        raise HTTPException(status_code=400, detail="n deve estar entre 1 e 10")

    with _tracer.start_as_current_span(
        "spam.tarefa",
        attributes={"app.n_chamadas": n},
    ):
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

        return {"total_chamadas": n, "resultados": resultados}


@app.get("/eco")
async def eco(mensagem: str = "pong") -> dict[str, str]:
    return {"eco": mensagem}
