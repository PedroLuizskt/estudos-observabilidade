"""Serviço spam.

É o serviço voltado para o cliente externo. Recebe as requisições via
Nginx e, quando precisa de dados, chama o serviço `eggs` internamente
por HTTP.

Assim como o eggs, nessa aula 1 esse serviço é propositalmente "cego"
— zero instrumentação de telemetria. Estado inicial para incrementar
nas próximas aulas.
"""
from __future__ import annotations

import os
from typing import Final

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE_NAME: Final[str] = os.getenv("SERVICE_NAME", "spam")
EGGS_URL: Final[str] = os.getenv("EGGS_URL", "http://eggs:8000")
VERSION: Final[str] = "0.1.0"

app = FastAPI(
    title="spam",
    description="Serviço voltado para o cliente. Orquestra chamadas ao eggs.",
    version=VERSION,
)


class Saude(BaseModel):
    status: str
    service: str
    version: str


class Combo(BaseModel):
    saudacao: str
    dado_do_eggs: int
    origem_eggs: str


@app.get("/", response_model=Saude)
async def home() -> Saude:
    """Raiz do serviço — sanity check."""
    return Saude(status="ok", service=SERVICE_NAME, version=VERSION)


@app.get("/health", response_model=Saude)
async def health() -> Saude:
    """Healthcheck dedicado."""
    return Saude(status="healthy", service=SERVICE_NAME, version=VERSION)


@app.get("/saudacao/{nome}")
async def saudacao(nome: str) -> dict[str, str]:
    """Endpoint sem dependências externas — útil para isolar problemas."""
    return {"mensagem": f"Olá, {nome}! Bem-vinda ao spam."}


@app.get("/combo/{nome}", response_model=Combo)
async def combo(nome: str) -> Combo:
    """Combina uma saudação local com um dado vindo do eggs.

    Esse é o endpoint "estrela" da demo: atravessa dois serviços,
    e por isso vai brilhar muito nas aulas de tracing.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resposta = await client.get(f"{EGGS_URL}/dados/aleatorio")
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            raise HTTPException(
                status_code=502,
                detail=f"eggs respondeu mal: {erro}",
            ) from erro

    dados = resposta.json()
    return Combo(
        saudacao=f"Olá, {nome}!",
        dado_do_eggs=dados["valor"],
        origem_eggs=dados["origem"],
    )


@app.get("/tarefa/{n}")
async def tarefa(n: int) -> dict[str, object]:
    """Dispara N chamadas sequenciais ao /processamento do eggs.

    Cenário proposital de "endpoint pesado que depende de outro serviço".
    Excelente para estudar latência acumulada nos traces das próximas aulas.
    """
    if n < 1 or n > 10:
        raise HTTPException(status_code=400, detail="n deve estar entre 1 e 10")

    resultados: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for _ in range(n):
            resposta = await client.get(f"{EGGS_URL}/processamento")
            resposta.raise_for_status()
            resultados.append(resposta.json())

    return {"total_chamadas": n, "resultados": resultados}


@app.get("/eco")
async def eco(mensagem: str = "pong") -> dict[str, str]:
    """Endpoint trivial para testes de carga — sem I/O, resposta imediata."""
    return {"eco": mensagem}
