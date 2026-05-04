"""Serviço eggs.

É o serviço "interno" do nosso cenário: não é chamado diretamente pelo
cliente final, só pelo `spam`. Expõe endpoints simples que o spam usa
como fontes de dados.

NOTA DA AULA 2: o código aqui é praticamente idêntico ao da aula 1.
A telemetria é injetada **de fora**, via `opentelemetry-instrument`
(ver Dockerfile). Esse é o ponto didático da instrumentação automática:
ganhar métricas HTTP padronizadas, métricas de sistema e métricas de
runtime sem mexer em uma linha do código de produção.
"""
from __future__ import annotations

import os
import random
import time
from typing import Final

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE_NAME: Final[str] = os.getenv("SERVICE_NAME", "eggs")
VERSION: Final[str] = "0.2.0"

app = FastAPI(
    title="eggs",
    description="Serviço interno chamado por spam.",
    version=VERSION,
)


class Saude(BaseModel):
    status: str
    service: str
    version: str


class DadoAleatorio(BaseModel):
    valor: int
    origem: str


class ResultadoFibonacci(BaseModel):
    n: int
    resultado: int
    duracao_ms: float


@app.get("/", response_model=Saude)
async def home() -> Saude:
    """Endpoint raiz — útil para verificar rapidamente se o serviço subiu."""
    return Saude(status="ok", service=SERVICE_NAME, version=VERSION)


@app.get("/health", response_model=Saude)
async def health() -> Saude:
    """Healthcheck dedicado — convenção usada por orquestradores."""
    return Saude(status="healthy", service=SERVICE_NAME, version=VERSION)


@app.get("/dados/aleatorio", response_model=DadoAleatorio)
async def dado_aleatorio() -> DadoAleatorio:
    """Retorna um inteiro aleatório. Fonte de dados "rápida" para o spam."""
    return DadoAleatorio(valor=random.randint(1, 1_000), origem=SERVICE_NAME)


@app.get("/processamento")
async def processamento() -> dict[str, str]:
    """Simula uma operação com latência variável (50–250ms).

    Existe para a gente ter algo com tempo de resposta instável nas
    próximas aulas, quando medirmos latência via histograma.
    """
    time.sleep(random.uniform(0.05, 0.25))
    return {"status": "processado", "service": SERVICE_NAME}


@app.get("/fibonacci/{n}", response_model=ResultadoFibonacci)
async def fibonacci(n: int) -> ResultadoFibonacci:
    """Calcula fibonacci(n) de forma recursiva.

    Propositalmente ineficiente — o tempo cresce exponencialmente com `n`.
    Ótimo exemplo de endpoint "problemático" para observar nos traces
    e métricas das aulas seguintes.

    Limite em 35 para não travar a demo.
    """
    if n < 0:
        raise HTTPException(status_code=400, detail="n precisa ser >= 0")
    if n > 35:
        raise HTTPException(
            status_code=400,
            detail="n muito alto — limite 35 para essa demo",
        )

    inicio = time.perf_counter()
    resultado = _fib(n)
    duracao_ms = (time.perf_counter() - inicio) * 1000

    return ResultadoFibonacci(n=n, resultado=resultado, duracao_ms=duracao_ms)


def _fib(n: int) -> int:
    if n < 2:
        return n
    return _fib(n - 1) + _fib(n - 2)
