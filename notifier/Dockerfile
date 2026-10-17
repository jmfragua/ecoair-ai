# Contexto de build: la RAIZ del proyecto (ver docker-compose.yml), para reutilizar
# api/subscriptions.py sin duplicarlo. El .dockerignore de la raiz excluye .env, .venv y docs.
# Al cambiar cualquiera de los .py copiados hay que reconstruir: docker compose up -d --build notifier
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY notifier/requirements.txt .
RUN pip install -r requirements.txt

# Configuracion comun (DATABASE_URL o DB_*)
COPY shared/ ./shared/
# Modulos reutilizados (puros): suscripciones y calidad del aire para el correo de bienvenida
COPY api/subscriptions.py api/calidad_aire.py api/ica.py api/recomendaciones.py ./
COPY notifier/*.py ./
# Modelos de prediccion experimental (JSON versionados en modelos/). v1 es el valor por defecto;
# v3 se activa con MODELO_PREDICCION_PATH=/app/modelos/prediccion_pm25_v3.json. v2 no se copia.
COPY modelos/prediccion_pm25_v1.json ./modelos/
COPY modelos/prediccion_pm25_v3.json ./modelos/

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

CMD ["python", "notifier.py"]
