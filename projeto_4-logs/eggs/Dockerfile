FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Diferença crucial em relação ao spam: aqui usamos `opentelemetry-instrument`
# como wrapper do uvicorn. Ele:
#   1. lê as variáveis de ambiente OTEL_* (ver docker-compose.yml)
#   2. carrega automaticamente todas as instrumentations encontradas
#      (FastAPI, ASGI, system-metrics, etc.)
#   3. dispara o uvicorn com tudo já configurado.
#
# Resultado: zero linha de OTel dentro de `app/main.py`.
CMD ["opentelemetry-instrument", \
     "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
