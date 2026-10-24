# Contexto de build: la RAIZ del proyecto (ver docker-compose.yml), para copiar shared/.
# El .dockerignore de la raiz excluye .env, .venv, tests, datos y docs.
# Al cambiar cualquier .py o el dashboard hay que reconstruir: docker compose up -d --build api
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY api/requirements.txt .
RUN pip install -r requirements.txt

# Configuracion comun (DATABASE_URL o DB_*)
COPY shared/ ./shared/
# Todos los modulos de la API (main, ica, calidad_aire, recomendaciones...). Los tests no se copian.
COPY api/*.py ./
# Modelos de prediccion experimental (JSON versionados en modelos/). v1 es el valor por defecto;
# v3 se activa con MODELO_PREDICCION_PATH=/app/modelos/prediccion_pm25_v3.json. v2 no se copia.
COPY modelos/prediccion_pm25_v1.json ./modelos/
COPY modelos/prediccion_pm25_v3.json ./modelos/
# v4: lo emite el alerter; la API lo lee para saber que horizontes son visibles (/predictions/latest)
COPY modelos/prediccion_pm25_v4.json ./modelos/
# Dashboard publico (HTML, CSS, JS y fuente local; sin recursos de internet)
COPY api/static/ ./static/

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

EXPOSE 8000
# PORT lo asigna la plataforma (p. ej. Railway); en local se usa 8000.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
