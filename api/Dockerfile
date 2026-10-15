FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Todos los modulos de la API (main, ica, calidad_aire, recomendaciones...). Los tests no se copian.
# Al cambiar cualquier .py hay que reconstruir la imagen: docker compose up -d --build api
COPY *.py ./

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
