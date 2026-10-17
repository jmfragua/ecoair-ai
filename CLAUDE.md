# EcoAir AI - estación inteligente de monitoreo ambiental

Proyecto universitario (Ingeniería de Software, equipo de 4). El nodo IoT mide aire,
envía por MQTT, se guarda en PostgreSQL, una API lo expone, una IA detecta anomalías
y un dashboard las muestra. No sustituye equipos certificados de calidad del aire.

## Estado (2026-10-16)
Leyenda: [E2E] probado de punta a punta con el sistema real · [LOCAL] probado de punta a punta
con contenedores temporales (Postgres/alerter de prueba), aún no en el sistema real · [TESTS] solo
tests unitarios (pytest), sin prueba real registrada · [SIM] solo con datos simulados.

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
- [E2E] Dashboard público (api/static/panel.html; se probó cuando se servía en "/", hoy está en
  "/panel"): probado en el navegador del Mac y en un celular conectado a la misma Wi-Fi.
- [E2E] GET /devices/{id}/air-quality/history: lo consume el dashboard en esas mismas pruebas.
- [E2E] Suscripción por correo, probada hoy en modo DRY_RUN: suscribirse desde el panel,
  confirmar con el enlace del correo, alerta simulada de dispositivo caído enviada al
  suscriptor (escrita en el log del notifier) y baja con el botón. Comprobado: el GET de
  /subscriptions/unsubscribe NO cancela; el POST sí.
- [NO PROBADO] Envío real por Gmail (SMTP): todavía no se ha probado. NOTIFIER_DRY_RUN es true
  por defecto; hay que poner NOTIFIER_DRY_RUN=false de forma explícita para enviar.
- [LOCAL] Correo de bienvenida al confirmar (migración 007): ICA de 1 h, recomendación de texto
  fijo con fuente y página, "Predicción experimental", aviso de datos indicativos. Envío único
  (1.ª pasada 1 correo, 2.ª pasada 0). Respeta DRY_RUN y Brevo.
- [LOCAL] Estación simulada (tools/simular_estacion.py): episodio programado -> ica_1h abierta
  y marcada [SIMULACIÓN] -> correo con [SIMULACIÓN] en el asunto (DRY_RUN). Dashboard con banda
  SIMULACIÓN, selector de estación y chip en alertas simuladas.
- [SIM] Predicción experimental de PM2.5 (modelos/prediccion_pm25_v1.json): entrenada SOLO con
  datos simulados (semilla 42), evaluada con la semilla 43. Resultado: a 1 h NO supera a la
  persistencia (MAE 2,954 vs 2,740) -> no se muestra; a 3 h sí (MAE 4,221 vs 4,675, -9,7 %).
  alpha = 1000, extremo de la rejilla declarada (no se amplió tras ver resultados).
- [SIM] [LOCAL] Anomalías con Isolation Forest dentro del alerter (ANOMALIAS_ACTIVAS=false por
  defecto; solo dispositivos simulados). RAM medida con docker stats: alerter 54 MiB apagado,
  ~118 MiB activo (+64 MiB). Imagen del alerter 318 -> 531 MB (scikit-learn + scipy). Con un fallo
  inyectado (PM2.5 = 700) abrió isolation_forest (info, variable principal pm25_std_10); durante un
  episodio suave de 110 µg/m³ NO marcó anomalía (el entrenamiento ya tenía episodios así).
- [TESTS] 84 tests de la API (incluyen los de node de api/tests/js/), 38 del alerter, 48 del
  notifier, 13 del ingestor, 53 de ai/ y 94 de db/ + shared/ + tools/.
- PENDIENTE:
  - Correo de alerta: quitar la hora duplicada ("Inicio" y "Se abrió a las" dicen lo mismo).
  - Gráfica del panel: la franja gris de "sin datos" (hueco en la serie).
  - BASE_URL apunta a la IP local http://192.168.1.200:8000: los enlaces de los correos solo
    funcionan en la misma Wi-Fi. Cambiarla a la URL pública antes de la feria.
  - Probar el envío real por Gmail.
  - Aplicar las migraciones 006 (datos externos) y 007 (bienvenida) en las bases que se usen.
  - Reentrenar predicción y anomalías con datos REALES (hoy hay ~6 h): 2 a 4 semanas continuas.
  - Registro por QR, asistente RAG, pantalla OLED.
- Opcional: MQ-135, LED RGB y buzzer, ciclos de reposo del SDS011.

## Hardware (no inventar mediciones)
- ESP32 DevKit 38 pines. DHT22 en GPIO4. SDS011 por UART2: TXD sensor -> GPIO16, RXD sensor -> GPIO17, 5V y GND.
- El SDS011 mide PM2.5 y PM10 (NO PM1.0). El MQ-135 no es un medidor preciso de CO2.

## Contrato MQTT (borrador v0.1, por validar con el equipo)
- Tópico: ecoair/{deviceId}/telemetry, QoS 0, un mensaje cada 5 s en pruebas.
- JSON: {"deviceId","temperature","humidity","pm25","pm10"}. Sin timestamp: lo asigna la base (UTC).
- Rangos válidos: temperature -40..80, humidity 0..100, pm25 y pm10 0..1000.

## Componentes en ~/ecoair
- docker-compose.yml, 6 servicios con restart: unless-stopped:
  - mosquitto (1883) y postgres 16 (puerto 5433 en el Mac, porque el 5432 lo ocupa otro Postgres local).
  - ingestor (ingestor/): MQTT -> valida -> PostgreSQL. Reintenta el broker y reconecta a la base.
  - api (api/, puerto 8000): FastAPI de solo lectura. El Dockerfile copia todos los api/*.py
    y api/static/.
  - alerter (alerter/): revisa cada 60 s y abre/cierra alertas. Build con contexto en la raíz
    para reutilizar api/{ica,calidad_aire,recomendaciones}.py y ai/{reglas_sensor,features}.py
    sin duplicarlos; el .dockerignore de la raíz excluye .env, datos, docs y PDF.
  - notifier (notifier/): correos de confirmación (cada 10 s) y de alertas (cada 30 s) con una
    cola en la base (notifications_sent: un correo por episodio, reintentos 1, 2, 4, 8 min,
    máximo 5). Único servicio con SMTP_* y SUBSCRIPTION_SECRET. Build con contexto en la raíz
    para reutilizar api/subscriptions.py. NOTIFIER_DRY_RUN=true por defecto (escribe el correo
    en el log y no lo envía). Sin SMTP_* arranca en DRY_RUN; BASE_URL y SUBSCRIPTION_SECRET
    son obligatorias. Los logs enmascaran el correo (a***@gmail.com) y nunca llevan tokens,
    salvo en DRY_RUN.
  - Al cambiar código de api, alerter o notifier hay que reconstruir: docker compose up -d --build <servicio>.
- Base de datos:
  - db/schema.sql: tablas devices, measurements, alerts.
  - Migraciones APLICADAS: 002_simulated.sql (measurements.is_simulated, dispositivo
    ecoair-sim-01) y 004_alerts.sql (kind amplía a threshold/anomaly/sensor/device_down;
    rule_code, severity, started_at, ended_at, last_seen_at, details, is_simulated; una sola
    alerta abierta por device_id + rule_code). La 004 se verificó: la tabla alerts muestra
    sus 14 columnas. Y 005_subscriptions.sql (aplicada el 15 oct 2026): tablas subscribers
    (email, consentimiento, tokens solo como hash SHA-256, sin IP) y notifications_sent.
  - 007_welcome_email.sql: NO APLICADA todavía. subscribers.welcome_sent_at (+ intentos) para
    el correo de bienvenida único.
  - 006_external_measurements.sql: NO APLICADA todavía. Tabla external_measurements (datos
    externos de terceros), separada de measurements. Se aplica con db/migrate.py.
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
  - Suscripción por correo (api/subscriptions.py, lógica pura):
    - GET /subscriptions/consent: texto de autorización (Ley 1581); el contacto sale de
      CONTACT_EMAIL; si falta, muestra "contacto no configurado".
    - POST /subscriptions (email, device_id, accepted_terms, min_severity aviso|critica): siempre
      202 con el mismo mensaje (no revela si el correo existe). 422 sin consentimiento o correo
      inválido; 429 con más de 20 peticiones en 10 min por IP; 3 confirmaciones por correo en 24 h.
      Con TRUST_PROXY_HEADERS=true (solo detrás de Cloudflare/Railway) la IP sale de
      CF-Connecting-IP o X-Forwarded-For.
    - GET /subscriptions/confirm?token= (enlace válido 24 h).
    - GET /subscriptions/unsubscribe?token= solo muestra un botón; el POST da de baja (también
      sirve para la baja con un clic de List-Unsubscribe). Token de baja = HMAC con SUBSCRIPTION_SECRET.
  - Todas devuelven 404 si el dispositivo no existe.
- Portada (api/static/index.html, servida en "/"): presentación del proyecto, tarjeta de estado
  en vivo de ecoair-node-01 (en línea/fuera de línea + ICA; "Sin datos recientes" si falla),
  ODS 3/11/13 y botones a /panel y /panel#suscripcion. Lógica pura en js/portada-vista.js.
  "/?device=..." redirige con 302 a "/panel?..." conservando todos los parámetros (QR y
  enlaces antiguos). [TESTS] + render revisado con datos mock; sin prueba real registrada.
- Dashboard (api/static/panel.html, servido en "/panel"; el resto de api/static/ con
  StaticFiles montado al final, todo con Cache-Control: no-cache):
  - HTML, CSS y JavaScript sin framework. Dispositivo por defecto ecoair-node-01; se cambia
    con ?device=... Se actualiza cada 10 s (la gráfica cada 60 s); si la API falla, muestra un
    aviso y conserva los últimos valores.
  - Sin recursos externos (el Wi-Fi de la feria puede fallar): sin CDN; fuente Inter variable
    local (api/static/fonts/, licencia SIL OFL 1.1 en OFL-Inter.txt). Comprobar con
    grep "http" en api/static/ (solo deben aparecer espacios de nombres SVG).
  - Tarjeta del ICA: cuadrícula 2x2 con el último registro (/measurements/latest: PM2.5,
    PM10, temperatura, humedad; sin PM1.0), "Última medición: hace X min · hora de Colombia"
    y aviso "el nodo puede estar apagado" con más de 10 min. Valor que falta = "—". Lógica pura
    en js/ahora-vista.js (la portada la usa para su miniatura PM2.5/PM10/temperatura/humedad,
    2x2 en móvil y 4 columnas desde 720 px; junto al ICA, "(0–500; 0–50 = Buena)").
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

## Modelos (modelos/, versionado en Git; ai/models/ sigue ignorado)
- prediccion_pm25_v1.json (2,5 KB): Ridge sobre el residuo de la persistencia, horizontes 1 y 3 h.
  Inferencia en Python puro (shared/prediccion.py) en la API (GET /devices/{id}/forecast, caché 60 s)
  y en el notifier (correo de bienvenida). Se entrena con ai/entrenar_prediccion.py (fuentes
  simulados/reales/externos; los metadatos guardan cuáles).
  - Regla: un horizonte solo se muestra si superó a la persistencia en la evaluación. Si no:
    "Predicción no disponible: el modelo no superó la línea base" y, en el correo, la tendencia
    de las últimas 3 h marcada como observación. La persistencia NUNCA se muestra como predicción.
  - Siempre "Modelo entrenado con <fuentes>. Experimental." y aviso si incluye simulados.
- prediccion_pm25_v2.json (7 KB): NO ACTIVO (la API y el notifier siguen con v1; se activa con
  MODELO_PREDICCION_PATH, decisión pendiente del equipo). Entrenado SOLO con PM2.5 horario externo
  (OpenAQ, MED-UNNV El Volador, CSV local con su SHA-256; ninguna base) con
  ai/entrenar_prediccion_v2.py. Modelo de resolución horaria: shared/prediccion.py lo reconoce por
  "resolucion": "hora" (variables pm25_h1/h2/h3, media_3h, pendiente_h1_h3, hora sin/cos,
  calculadas de la serie por minuto de 3 h; cada hora con >= 40 min de datos); v1 no cambia.
  - Sin temperatura ni humedad (la estación externa solo trae PM2.5). Diseño declarado antes de
    ver resultados: entrenamiento 2018-11..2019-09, validación 2019-10..2019-11 (solo alpha),
    prueba 2019-12..2020-02 evaluada una vez; alphas 0.01..10000.
  - Prueba (MAE modelo / persistencia / media horaria): 1 h 3,985 / 4,078 / 6,960 (-2,3 %);
    3 h 5,884 / 6,619 / 6,991 (-11,1 %); 6 h 6,668 / 8,457 / 7,049 (-21,2 %). Los tres superan a
    la persistencia. alpha: 1 h = 10000 y 6 h = 0.01 son extremos de la rejilla (no se amplió).
  - Aviso público: "Entrenado con datos externos históricos de Medellín (2018-2020); no representa
    la precisión con el sensor propio." Supuesto: el valor de OpenAQ en T es la media de la hora
    que termina en T. Con el sensor propio solo hay ~6 h reales (358 min): sin evaluación real.
  - Presentación desde el JSON (sin tocar código): "texto_fuentes", "aviso_publico" y
    "horizontes_visibles" (v2: [180, 360]; "1 h: mejora marginal (2,3 %), no se muestra"). Panel y
    correo de bienvenida toman fuente y aviso del JSON del modelo activo. v1 no trae esos campos:
    sus textos se arman igual que antes con sus "fuentes".
  - Sesgo medido con series constantes (pred - valor): v2 se desvía más de 1 µg/m³ en niveles de
    3 a 12 (p. ej. 3 µg/m³: +1,4 a +2,0 a 1 h; +3,7 a +7,0 a 3 h; +5,2 a +10,6 a 6 h, según la hora del día).
    Causa: las variables de nivel absoluto (pm25_h1/h2/h3, media_3h) con coeficientes negativos
    regresan hacia la media del entrenamiento (~20 µg/m³ en MED-UNNV), y a 6 h también la hora del
    día. v1 tiene el mismo efecto hacia su media simulada (~12). Pendiente decidir la solución
    ANTES de activar v2 (los datos propios rondan 3-4 µg/m³).
- prediccion_pm25_v3.json: NO ACTIVO. Variables RELATIVAS al nivel ("transformacion": "relativa",
  c = 2,0 fija): log((h2+c)/(h1+c)), log((h3+c)/(h1+c)), log((media_3h+c)/(h1+c)),
  pend_rel = (h1-h3)/(media_3h+c), hora sin/cos; objetivo log((y+c)/(h1+c)); predicción
  (h1+c)·exp(f) - c. Mismo CSV, muestras y división que v2 (ai/entrenar_prediccion_v3.py).
  - La prueba NO es virgen: ya se había mirado una vez con v2 (anotado también en el JSON).
  - Prueba, MAE v3 / v2 / persistencia / media horaria: 1 h 4,041 / 3,985 / 4,078 / 6,960 (-0,9 %);
    3 h 6,380 / 5,884 / 6,619 / 6,991 (-3,6 %); 6 h 7,384 / 6,668 / 8,457 / 7,049 (-12,7 %).
    v3 es peor que v2 en los tres horizontes y a 6 h peor que la media por hora del día.
    alpha 3 h = 10000 (extremo de la rejilla; no se amplió).
  - Sesgo con series constantes (criterio declarado antes: niveles 3 y 6, <= 1,0 a 1 y 3 h,
    <= 1,5 a 6 h): CUMPLE en los tres (máx |pred - valor| 0,28 / 0,27 / 1,30) e invariancia de
    escala exacta. horizontes_visibles = [360] (único con mejora >= 5 % y sesgo cumplido).
- anomalias_sim_if_v2_A.joblib (1,97 MB) + .json de procedencia (SHA-256, evaluación, límites):
  Isolation Forest (scikit-learn 1.9.1; la imagen usa la misma versión). Exportado con
  ai/exportar_modelo_anomalias.py, sin reentrenar.
  - Límites (honestos): entrenado SOLO con simulados -> solo se aplica a dispositivos simulados;
    para el nodo real hay que reentrenar con datos reales. En la evaluación NO mejoró a las reglas
    en la anomalía contextual de PM (0 de 4 con contamination 0,005; con umbrales bajos "detectaba"
    por los bordes bruscos del simulador). Falsas alarmas medidas: 1 a 6 por día según umbral.
  - Alertas kind='anomaly', rule_code='isolation_forest', severidad info, con la variable que más
    contribuyó y la procedencia. Chip "modelo, experimental" en el panel. Correo apagado
    (ANOMALIAS_CORREO=false); si se activa, máx. 1 por dispositivo y suscriptor cada 6 h.

## Despliegue en Railway
- Un proyecto con 6 servicios: Postgres (gestionado por Railway), mosquitto, ingestor, api,
  alerter y notifier. Los cinco últimos se construyen desde este repo con su propio Dockerfile
  (mosquitto/, ingestor/, api/, alerter/, notifier/); ingestor, api, alerter y notifier usan
  contexto en la raíz para copiar shared/, modelos/ y módulos de api/ y ai/.
- Configuración solo por variables de entorno (ver .env.example; los valores reales viven en
  Railway, nunca en el repo):
  - Base de datos: DATABASE_URL (referencia al Postgres) en ingestor, api, alerter y notifier.
  - mosquitto: MQTT_DEVICE_USER/PASSWORD y MQTT_INGESTOR_USER/PASSWORD (sin MQTT_ALLOW_ANONYMOUS).
  - ingestor: MQTT_HOST (red privada), MQTT_PORT, MQTT_USER/PASSWORD del usuario de ingestor.
  - api: CONTACT_EMAIL, TRUST_PROXY_HEADERS=true; PORT lo asigna Railway.
  - alerter: ALERTER_INCLUIR_SIMULADOS y ANOMALIAS_ACTIVAS (demo).
  - notifier: BASE_URL (URL pública), SUBSCRIPTION_SECRET, EMAIL_PROVIDER=brevo, BREVO_API_KEY,
    EMAIL_FROM (verificado en Brevo), NOTIFIER_DRY_RUN, NOTIFIER_INCLUIR_SIMULADOS, ANOMALIAS_CORREO.
- Correo por la API HTTP de Brevo (EMAIL_PROVIDER=brevo) porque Railway bloquea la salida SMTP.
  SMTP sigue disponible como opción para uso local.
- Predicción: las imágenes de api y notifier copian v1 (por defecto) y v3 (no v2). Para activar
  v3: MODELO_PREDICCION_PATH=/app/modelos/prediccion_pm25_v3.json en api y notifier. Si la ruta
  no existe o el JSON no es válido, el servicio sigue, la predicción queda "sin_modelo" (NO cae a
  v1) y queda un WARNING con la ruta en el log; se guarda en caché: corregir y reiniciar.
- Migraciones: a mano con db/migrate.py contra la URL pública del Postgres (no en el despliegue).
- MQTT: el nodo publica a través del TCP Proxy de Railway (host y puerto asignados por Railway).
  Límite aceptado: MQTT SIN TLS (usuario/clave y datos viajan sin cifrar); se mitiga con un
  usuario de dispositivo que solo puede publicar en ecoair/+/telemetry y una clave rotable.
- Antes de activar el envío real: probar en DRY_RUN y revisar los logs del notifier.

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
- Estación de demo: tools/simular_estacion.py carga ecoair-sim-01 (is_simulated=true) con un
  episodio programado; las filas futuras aparecen a su hora (toda lectura filtra received_at <=
  ahora). Nunca escribe en un dispositivo con datos reales. Para que el alerter y el notifier
  traten la simulación: ALERTER_INCLUIR_SIMULADOS=true y NOTIFIER_INCLUIR_SIMULADOS=true (por
  defecto false). Todo lo simulado lleva [SIMULACIÓN] en alertas y correos y la banda SIMULACIÓN
  en el panel.
- Los datos simulados usan el dispositivo ecoair-sim-01 y measurements.is_simulated = true
  (migración db/migrations/002_simulated.sql; generador ai/simular_datos.py).
- Los scripts de entrenamiento y los reportes de datos reales deben filtrar is_simulated = false.
- Todo modelo entrenado con datos simulados lleva el prefijo sim_ y se describe como prueba de
  software, no como evidencia de precisión.

## Datos externos (estaciones de terceros)
- Van SOLO en la tabla external_measurements (migración 006), separada de measurements: nunca se
  insertan en measurements ni se mezclan con los datos propios (ecoair-node-*) ni con los
  simulados (ecoair-sim-01). Cada fila guarda fuente, estación, ciudad, hora (UTC), licencia y
  fecha de carga. PM2.5 es obligatorio; PM10, temperatura y humedad son opcionales (NULL), porque
  muchas estaciones solo miden PM2.5.
- Carga: db/load_external.py (columnas por argumentos, --col-pm10 opcional, rangos físicos, sin
  duplicados por fuente + estación + hora, por lotes). Probar siempre primero con --dry-run.
- Inventario: db/resumen_datos.py (SOLO LECTURA, sesión read-only): por dispositivo, mediciones,
  primera/última fecha, horas con datos y si son reales o simuladas; y external_measurements por
  fuente. Usa DATABASE_URL o DB_*; no imprime credenciales.
- Al EVALUAR un modelo, los datos externos nunca se mezclan con los propios: se reportan por
  separado y cada métrica dice de qué fuente salió.
- Todo entrenamiento debe registrar de qué fuente salieron sus datos (propios, externos con su
  fuente y licencia, o simulados) en los metadatos del modelo, igual que ya hace el JSON de los
  modelos sim_*.
- Respetar la licencia de cada fuente y citarla donde se usen sus datos.
- OpenAQ, estación MED-UNNV (Universidad Nacional, sede El Volador, Medellín; location_id 10641):
  - Archivo crudo en datos_externos/openaq_10641/ (CSV.gz diarios, ignorado por git; trae pm25,
    o3 y no2). Conversión con tools/convertir_openaq.py (solo biblioteca estándar, no toca la
    base) -> datos_externos/openaq_10641_pm25_utc.csv con source "OpenAQ archive", station
    "MED-UNNV El Volador (OpenAQ 10641)", city "Medellín".
  - Periodo útil: 2018-11-05 02:00 a 2020-02-25 13:00 UTC, solo PM2.5 por hora: 10.103 filas
    finales de 20.987 de pm25 (10.884 con -9999 = sin dato; desde 2020-03 todo es -9999).
    0 fuera de rango y 0 duplicados. load_external.procesar_csv acepta las 10.103 (validación sin base).
  - Zona horaria: TODAS las horas vienen con desfase -01:00 (Colombia es -05:00). Supuesto: se
    respeta el desfase tal cual y se pasa a UTC. Evidencia indirecta a favor: el pico medio de
    PM2.5 cae a las 12:00 de la hora etiquetada = 13:00 UTC = 08:00 en Colombia (hora pico de la
    mañana). No está confirmado con la documentación de OpenAQ.
  - Licencia: PENDIENTE DE VERIFICAR (valor por defecto de --licencia). Verificarla y citarla
    antes de cargar o usar estos datos. NO cargados en ninguna base todavía.

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