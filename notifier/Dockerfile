# Contexto de build: la RAIZ del proyecto (ver docker-compose.yml), para reutilizar
# api/subscriptions.py sin duplicarlo. El .dockerignore de la raiz excluye .env, .venv y docs.
# Al cambiar cualquiera de los .py copiados hay que reconstruir: docker compose up -d --build notifier
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY notifier/requirements.txt .
RUN pip install -r requirements.txt

COPY api/subscriptions.py ./
COPY notifier/*.py ./

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

CMD ["python", "notifier.py"]
