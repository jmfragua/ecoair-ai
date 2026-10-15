# EcoAir AI - estación inteligente de monitoreo ambiental

Proyecto universitario (Ingeniería de Software, equipo de 4). El nodo IoT mide aire,
envía por MQTT, se guarda en PostgreSQL, una API lo expone, una IA detecta anomalías
y un dashboard las muestra. No sustituye equipos certificados de calidad del aire.

## Estado (2026-10-15)
Leyenda: [E2E] probado de punta a punta con el sistema real · [TESTS] solo tests unitarios
(pytest), sin prueba real registrada · [SIM] solo con datos simulados.

- [E2E] ESP32 + DHT22 + SDS011 -> Wi-Fi 2,4 GHz -> MQTT -> ingestor -> PostgreSQL -> API.
- [E2E] Docker compose con 5 servicios en marcha: mosquitto, postgres, ingestor, api, alerter.
- [E2E] Alerta dispositivo_caido: en una prueba real abrió a los 6 min sin datos (más de 5 min
  + 2 revisiones) y se cerró al reconectar el nodo.
- [TESTS] Resto de reglas de alertas (ica_1h, tabla4_pm25/pm10, sensor_congelado,
  salto_temperatura, fuera_de_rango_fisico) e histéresis abrir/cerrar.
- [E2E] GET /devices/{id}/air-quality/current: probado con curl contra la API real en Docker
  (ICA real de ecoair-node-01; 404 y estado sin_datos para el dispositivo simulado).
- [E2E] GET /devices/{id}/alerts: probado con curl contra la API real en Docker (lista vacía;
  alerta dispositivo_caido vista abierta y luego cerrada).
- [TESTS] Casos límite del cálculo del ICA (api/ica.py) y de api/calidad_aire.py (ventana
  parcial, bordes de 5 min, Tabla 4 con media de 24 h): solo tests, sin prueba real registrada.
- [E2E] Dashboard público (api/static/, servido en "/"): probado en el navegador del Mac y en
  un celular conectado a la misma Wi-Fi.
- [E2E] GET /devices/{id}/air-quality/history: lo consume el dashboard en esas mismas pruebas.
- [TESTS] 36 tests de la API (pytest en api/), incluida la validación de parámetros del
  historial (422) y que "/" sirve la página sin tapar las rutas de la API.
- [SIM] Isolation Forest (ai/): evaluado solo con datos simulados; NO está en ningún servicio.
  En la última evaluación no aportó frente a las reglas en la anomalía contextual de PM.
- PENDIENTE: notificaciones, registro por QR, asistente RAG, Isolation Forest con datos reales,
  pantalla OLED.
- Opcional: MQ-135, LED RGB y buzzer, ciclos de reposo del SDS011.

## Hardware (no inventar mediciones)
- ESP32 DevKit 38 pines. DHT22 en GPIO4. SDS011 por UART2: TXD sensor -> GPIO16, RXD sensor -> GPIO17, 5V y GND.
- El SDS011 mide PM2.5 y PM10 (NO PM1.0). El MQ-135 no es un medidor preciso de CO2.

## Contrato MQTT (borrador v0.1, por validar con el equipo)
- Tópico: ecoair/{deviceId}/telemetry, QoS 0, un mensaje cada 5 s en pruebas.
- JSON: {"deviceId","temperature","humidity","pm25","pm10"}. Sin timestamp: lo asigna la base (UTC).
- Rangos válidos: temperature -40..80, humidity 0..100, pm25 y pm10 0..1000.

## Componentes en ~/ecoair
- docker-compose.yml, 5 servicios con restart: unless-stopped:
  - mosquitto (1883) y postgres 16 (puerto 5433 en el Mac, porque el 5432 lo ocupa otro Postgres local).
  - ingestor (ingestor/): MQTT -> valida -> PostgreSQL. Reintenta el broker y reconecta a la base.
  - api (api/, puerto 8000): FastAPI de solo lectura. El Dockerfile copia todos los api/*.py
    y api/static/.
  - alerter (alerter/): revisa cada 60 s y abre/cierra alertas. Build con contexto en la raíz
    para reutilizar api/{ica,calidad_aire,recomendaciones}.py y ai/{reglas_sensor,features}.py
    sin duplicarlos; el .dockerignore de la raíz excluye .env, datos, docs y PDF.
  - Al cambiar código de api o alerter hay que reconstruir: docker compose up -d --build <servicio>.
- Base de datos:
  - db/schema.sql: tablas devices, measurements, alerts.
  - Migraciones APLICADAS: 002_simulated.sql (measurements.is_simulated, dispositivo
    ecoair-sim-01) y 004_alerts.sql (kind amplía a threshold/anomaly/sensor/device_down;
    rule_code, severity, started_at, ended_at, last_seen_at, details, is_simulated; una sola
    alerta abierta por device_id + rule_code). La 004 se verificó: la tabla alerts muestra
    sus 14 columnas.
  - 003_air_levels_borrador.sql: SOLO BORRADOR, no aplicada (las tablas del ICA viven en api/ica.py).
- Endpoints de la API:
  - GET /health (503 si la base no responde en 3 s).
  - GET /devices · GET /devices/{id}/measurements (?since, ?limit) · GET /devices/{id}/measurements/latest.
  - GET /devices/{id}/air-quality/current: ICA de la media de 1 h, Tabla 4 y límite diario con
    la media de 24 h, recomendación de texto fijo, advertencias. 503 si la consulta pasa de 5 s.
  - GET /devices/{id}/alerts (?estado=abiertas|todas, ?limit): alertas del alerter con los campos nuevos.
  - GET /devices/{id}/air-quality/history (?hours=1..168, por defecto 24; ?bucket_min=1..60,
    por defecto 5): promedios por intervalo (date_bin) de PM2.5, PM10, temperatura y humedad,
    con horas cubiertas, "parcial" (cobertura < 75 %) e is_simulated. 422 si los parámetros
    no son válidos, 503 si la consulta pasa de 5 s.
  - GET /alerts (?device_id, ?limit): formato antiguo, se mantiene por compatibilidad.
  - Todas devuelven 404 si el dispositivo no existe.
- Dashboard (api/static/, servido por la API en "/" con StaticFiles montado al final):
  - HTML, CSS y JavaScript sin framework. Dispositivo por defecto ecoair-node-01; se cambia
    con ?device=... Se actualiza cada 10 s (la gráfica cada 60 s); si la API falla, muestra un
    aviso y conserva los últimos valores.
  - Sin recursos externos (el Wi-Fi de la feria puede fallar): sin CDN; fuente Inter variable
    local (api/static/fonts/, licencia SIL OFL 1.1 en OFL-Inter.txt). Comprobar con
    grep "http" en api/static/ (solo deben aparecer espacios de nombres SVG).
  - Muestra: ICA con medidor (base media de 1 h, indicativo), PM2.5/PM10 frente al límite
    diario, temperatura/humedad, gráfica de 24 h (escala ajustada a los datos o "Ver contra
    límites"), recomendación de texto fijo con su fuente, alertas en hora de Colombia, la
    insignia DATOS SIMULADOS y la advertencia de estación de bajo costo.
- Alertas (alerter/, solo reglas deterministas, no IA):
  - Umbral/norma (kind=threshold): ica_1h (danina_sensibles = aviso; danina o peor = crítica;
    marcada como indicativa) y tabla4_pm25/pm10 (prevención = aviso; alerta y emergencia =
    crítica; solo si la ventana de 24 h no es parcial).
  - Sensor (kind=sensor, aviso): sensor_congelado (ventana 45 min), salto_temperatura y
    fuera_de_rango_fisico (ventana 15 min).
  - Dispositivo caído (kind=device_down, crítica): última medición con más de 5 min.
  - Histéresis: abre tras 2 revisiones seguidas, cierra tras 3 sin cumplirse. Contadores en
    memoria; al arrancar recupera de la base las alertas abiertas.
  - Por defecto solo dispositivos con is_simulated = false. Constantes en alerter/config.py,
    configurables con variables de entorno ALERTER_*.
- La clave de la base está en .env como POSTGRES_PASSWORD. Nunca la escribas en el código ni en este archivo.
- Para ejecutar los scripts fuera de Docker: cargar .env y usar DB_PORT=5433.

## Supuestos del proyecto (no son de la Res. 2254/2017)
- ICA: la concentración se trunca a entero antes de buscar la fila de la Tabla 6, porque la
  tabla tiene huecos (PM2.5 12 y 13). El ICA se redondea con la mitad hacia arriba. El mismo
  truncado se usa para la Tabla 4 (nivel_alerta).
- Ventanas: el ICA se muestra con la media de 1 h (indicativo); la Tabla 4 y el límite diario
  (PM2.5 37, PM10 75) usan la media de 24 h, como pide la norma.
- La ventana de 24 h es "parcial" si los minutos con datos cubren menos del 75 % (18 h); entonces
  no se presenta como media de 24 h y no se abren alertas de la Tabla 4.
- Sin datos: si la última medición tiene más de 5 min, no se calcula ICA (estado sin_datos).
- Recomendaciones: texto fijo (api/recomendaciones.py) de la Tabla 5 de la Res. 2254 y de las
  cautionary statements de la EPA, asociadas por rango del índice; la de la EPA es orientativa.
- Dashboard, temperatura y humedad: la franja de confort (18–26 °C y 40–60 %) es una
  referencia orientativa elegida por el proyecto, NO un límite normativo; la página lo dice.
- Dashboard, colores del ICA: tonos ajustados para fondo oscuro; siempre van con el nombre de
  la categoría (no se depende solo del color).

## Datos simulados
- Los datos simulados usan el dispositivo ecoair-sim-01 y measurements.is_simulated = true
  (migración db/migrations/002_simulated.sql; generador ai/simular_datos.py).
- Los scripts de entrenamiento y los reportes de datos reales deben filtrar is_simulated = false.
- Todo modelo entrenado con datos simulados lleva el prefijo sim_ y se describe como prueba de
  software, no como evidencia de precisión.

## Normas de calidad del aire (docs/normas/)
- No leer los PDF de docs/normas/ (son pesados).
- Para cifras de normas: usar docs/normas/normas_aire.md.
- Para citar texto o construir el RAG: usar los .txt de docs/normas/texto/, que tienen marcas
  "=== PAGINA N ===" para citar la página.
- No abrir docs/normas/texto/who_aqg_2021.txt completo: buscar con grep lo que se necesite.

## Reglas
- Responder en español, simple y por pasos.
- Distinguir regla por umbral (no es IA) de detección de anomalías con Isolation Forest.
- Si hay datos simulados, marcarlos como simulados. No inventar métricas ni resultados.
- No afirmar que algo se probó si solo se propuso. Declarar las suposiciones.
- No subir .env ni secrets.h a GitHub.
- Prioridad: MVP funcional antes que extras.