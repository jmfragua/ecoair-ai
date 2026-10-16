# Contexto de build: la RAIZ del proyecto (ver docker-compose.yml), para copiar shared/.
# Al cambiar el codigo hay que reconstruir: docker compose up -d --build ingestor
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY ingestor/requirements.txt .
RUN pip install -r requirements.txt

# Configuracion comun (DATABASE_URL o DB_*, MQTT_*)
COPY shared/ ./shared/
COPY ingestor/ingestor.py .

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

CMD ["python", "ingestor.py"]
