"""Serviço spam — agora com métricas customizadas via OpenTelemetry manual.

Diferenças em relação à aula 1:
    - O setup do OTel é feito explicitamente (ver `telemetria.py`).
    - Cada endpoint relevante usa instrumentos customizados.
    - Um middleware ASGI cuida do gauge "requisições em voo".
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .telemetria import Instrumentos, setup_telemetria

SERVICE_NAME: Final[str] = os.getenv("SERVICE_NAME", "spam")
EGGS_URL: Final[str] = os.getenv("EGGS_URL", "http://eggs:8000")
VERSION: Final[str] = "0.2.0"

# Variável de módulo que vai guardar os instrumentos após o startup.
# Inicia como None para deixar claro que `lifespan` precisa rodar antes.
_instrumentos: Instrumentos | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Hook de startup/shutdown do FastAPI.

    Configura o OpenTelemetry **antes** das primeiras requisições
    chegarem. O `yield` separa "antes de servir" de "depois de parar".
    """
    global _instrumentos
    _instrumentos = setup_telemetria()
    yield
    # Shutdown explícito do MeterProvider não é necessário aqui:
    # o SDK lida bem com o desligamento via `atexit` do Python.


app = FastAPI(
    title="spam",
    description="Serviço voltado para o cliente, instrumentado manualmente.",
    version=VERSION,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware: incrementa/decrementa o UpDownCounter de requisições em voo.
# Em produção isso normalmente é cuidado pela instrumentação automática
# FastAPI/ASGI, mas aqui é didático demonstrar que dá pra fazer no braço.
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
    """Endpoint instrumentado: counter + histograma da chamada externa.

    O counter é incrementado por requisição, com atributos que
    permitirão segmentar no Prometheus (status, rota_eggs).
    O histograma mede só o trecho da chamada HTTP ao eggs — esse é
    o ponto onde a latência mais varia.
    """
    assert _instrumentos is not None  # garantido pelo lifespan

    inicio = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resposta = await client.get(f"{EGGS_URL}/dados/aleatorio")
            resposta.raise_for_status()
    except httpx.HTTPError as erro:
        _instrumentos.requisicoes_combo.add(
            1, attributes={"status": "erro", "rota_eggs": "/dados/aleatorio"}
        )
        raise HTTPException(
            status_code=502,
            detail=f"eggs respondeu mal: {erro}",
        ) from erro
    finally:
        # Sempre registra a duração — sucesso ou falha — em milissegundos.
        duracao_ms = (time.perf_counter() - inicio) * 1000
        _instrumentos.duracao_chamada_eggs.record(
            duracao_ms,
            attributes={"rota_eggs": "/dados/aleatorio"},
        )

    _instrumentos.requisicoes_combo.add(
        1, attributes={"status": "ok", "rota_eggs": "/dados/aleatorio"}
    )

    dados = resposta.json()
    return Combo(
        saudacao=f"Olá, {nome}!",
        dado_do_eggs=dados["valor"],
        origem_eggs=dados["origem"],
    )


@app.get("/tarefa/{n}")
async def tarefa(n: int) -> dict[str, object]:
    """N chamadas sequenciais ao /processamento — cada uma vira uma
    observação no histograma de duração."""
    assert _instrumentos is not None

    if n < 1 or n > 10:
        raise HTTPException(status_code=400, detail="n deve estar entre 1 e 10")

    resultados: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for _ in range(n):
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
