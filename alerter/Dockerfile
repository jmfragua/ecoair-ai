# Contexto de build: la RAIZ del proyecto (ver docker-compose.yml), para reutilizar modulos
# de api/ y ai/ sin duplicarlos. El .dockerignore de la raiz excluye .env, .venv, datos y docs.
# Al cambiar cualquiera de los .py copiados hay que reconstruir: docker compose up -d --build alerter
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY alerter/requirements.txt .
RUN pip install -r requirements.txt

# Configuracion comun (DATABASE_URL o DB_*)
COPY shared/ ./shared/
# Modulos reutilizados (puros, sin acceso a red)
COPY api/ica.py api/calidad_aire.py api/recomendaciones.py ./
COPY ai/reglas_sensor.py ai/features.py ai/scoring.py ./
# Modelo experimental de anomalias + su procedencia (solo se carga con ANOMALIAS_ACTIVAS=true)
COPY modelos/anomalias_sim_if_v2_A.joblib modelos/anomalias_sim_if_v2_A.json ./modelos/
COPY alerter/*.py ./

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

CMD ["python", "alerter.py"]
