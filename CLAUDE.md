# EcoAir AI - estación inteligente de monitoreo ambiental

Proyecto universitario (Ingeniería de Software, equipo de 4). El nodo IoT mide aire,
envía por MQTT, se guarda en PostgreSQL, una API lo expone, una IA detecta anomalías
y un dashboard las muestra. No sustituye equipos certificados de calidad del aire.

## Estado (2026-10-13)
- FUNCIONA: ESP32 + DHT22 + SDS011 -> Wi-Fi 2,4 GHz -> MQTT -> ingestor -> PostgreSQL -> API.
- PENDIENTE: servicio de IA, dashboard, alertas por umbral, docker compose completo, pantalla OLED.
- Opcional: MQ-135, LED RGB y buzzer, ciclos de reposo del SDS011.

## Hardware (no inventar mediciones)
- ESP32 DevKit 38 pines. DHT22 en GPIO4. SDS011 por UART2: TXD sensor -> GPIO16, RXD sensor -> GPIO17, 5V y GND.
- El SDS011 mide PM2.5 y PM10 (NO PM1.0). El MQ-135 no es un medidor preciso de CO2.

## Contrato MQTT (borrador v0.1, por validar con el equipo)
- Tópico: ecoair/{deviceId}/telemetry, QoS 0, un mensaje cada 5 s en pruebas.
- JSON: {"deviceId","temperature","humidity","pm25","pm10"}. Sin timestamp: lo asigna la base (UTC).
- Rangos válidos: temperature -40..80, humidity 0..100, pm25 y pm10 0..1000.

## Componentes en ~/ecoair
- docker-compose.yml: Mosquitto (1883) y PostgreSQL 16 (puerto 5433 en el Mac, porque el 5432 lo ocupa otro Postgres local).
- db/schema.sql: tablas devices, measurements, alerts (alerts.kind = 'threshold' o 'anomaly').
- ingestor/ingestor.py: MQTT -> valida -> PostgreSQL (Python, paho-mqtt, psycopg).
- api/main.py: FastAPI de solo lectura (/health, /devices, /devices/{id}/measurements, /devices/{id}/measurements/latest, /alerts).
- La clave de la base está en .env como POSTGRES_PASSWORD. Nunca la escribas en el código ni en este archivo.
- Para ejecutar los scripts: cargar .env y usar DB_PORT=5433.

## Reglas
- Responder en español, simple y por pasos.
- Distinguir regla por umbral (no es IA) de detección de anomalías con Isolation Forest.
- Si hay datos simulados, marcarlos como simulados. No inventar métricas ni resultados.
- No afirmar que algo se probó si solo se propuso. Declarar las suposiciones.
- No subir .env ni secrets.h a GitHub.
- Prioridad: MVP funcional antes que extras.