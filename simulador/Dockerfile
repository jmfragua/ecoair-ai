# Estacion SIMULADA continua (tools/simular_continuo.py --servicio) para la demostracion.
# Contexto de build: la RAIZ del proyecto, como alerter y notifier (reutiliza tools/ y api/ica.py sin
# duplicarlos). No hace nada salvo con SIM_ACTIVO=true; usa DATABASE_URL del servicio y nunca la imprime.
# Todo lo que escribe es is_simulated=true, solo en ecoair-sim-NN y nunca con hora futura.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY simulador/requirements.txt .
RUN pip install -r requirements.txt

COPY shared/ ./shared/
COPY api/ica.py ./api/
COPY tools/simular_estacion.py tools/simular_continuo.py ./tools/

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

CMD ["python", "tools/simular_continuo.py", "--servicio"]
