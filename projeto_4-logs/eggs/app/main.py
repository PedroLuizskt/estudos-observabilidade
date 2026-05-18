"""Serviço eggs.

É o serviço "interno" do nosso cenário: não é chamado diretamente pelo
cliente final, só pelo `spam`. Expõe endpoints simples que o spam usa
como fontes de dados.

NOTA DA AULA 4: o código aqui continua praticamente idêntico ao da
aula 1. Toda a observabilidade (métricas na aula 2, traces na aula 3,
LOGS NA AULA 4) é injetada **de fora**, via `opentelemetry-instrument`.

A aula 4 é o exemplo mais radical disso: ganhamos logs estruturados
no Loki com `trace_id` injetado em cada linha, e tudo que mudou foi:
    1. instalar `opentelemetry-instrumentation-logging` no Dockerfile
    2. trocar `OTEL_LOGS_EXPORTER=none` para `OTEL_LOGS_EXPORTER=otlp`
       no docker-compose
    3. setar `OTEL_PYTHON_LOG_CORRELATION=true`

Zero linhas de aplicação. É a vitória do design "ponte" do OTel para
logs: a sua aplicação continua usando `logging` padrão, e o OTel se
infiltra por baixo.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Final

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE_NAME: Final[str] = os.getenv("SERVICE_NAME", "eggs")
VERSION: Final[str] = "0.4.0"

# Logger padrão do Python. SEM nenhum setup OTel manual nesse arquivo!
# A captura desses logs e o envio ao Loki são responsabilidade da
# auto-instrumentação ativada pelo `opentelemetry-instrument` no CMD
# do Dockerfile + da env var OTEL_LOGS_EXPORTER=otlp.
logger = logging.getLogger(SERVICE_NAME)

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
    valor = random.randint(1, 1_000)
    logger.info("dado aleatorio gerado", extra={"valor": valor})
    return DadoAleatorio(valor=valor, origem=SERVICE_NAME)


@app.get("/processamento")
async def processamento() -> dict[str, str]:
    """Simula uma operação com latência variável (50–250ms)."""
    espera = random.uniform(0.05, 0.25)
    logger.info("processamento iniciado", extra={"espera_s": round(espera, 3)})
    time.sleep(espera)
    logger.info("processamento concluido")
    return {"status": "processado", "service": SERVICE_NAME}


@app.get("/fibonacci/{n}", response_model=ResultadoFibonacci)
async def fibonacci(n: int) -> ResultadoFibonacci:
    """Calcula fibonacci(n) de forma recursiva.

    Propositalmente ineficiente — o tempo cresce exponencialmente com `n`.
    Limite em 35 para não travar a demo.
    """
    if n < 0:
        logger.warning("fibonacci negativo rejeitado", extra={"n": n})
        raise HTTPException(status_code=400, detail="n precisa ser >= 0")
    if n > 35:
        logger.warning("fibonacci grande rejeitado", extra={"n": n})
        raise HTTPException(
            status_code=400,
            detail="n muito alto — limite 35 para essa demo",
        )

    inicio = time.perf_counter()
    resultado = _fib(n)
    duracao_ms = (time.perf_counter() - inicio) * 1000

    # Alerta proativo: fibonacci está ficando lento.
    if duracao_ms > 500:
        logger.warning(
            "fibonacci demorado",
            extra={"n": n, "duracao_ms": round(duracao_ms, 2)},
        )

    return ResultadoFibonacci(n=n, resultado=resultado, duracao_ms=duracao_ms)


def _fib(n: int) -> int:
    if n < 2:
        return n
    return _fib(n - 1) + _fib(n - 2)
