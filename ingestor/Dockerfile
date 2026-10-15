FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ingestor.py .

RUN useradd --create-home --uid 1000 ecoair
USER ecoair

CMD ["python", "ingestor.py"]
