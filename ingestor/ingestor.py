import json
import logging
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg

# shared/ esta en la raiz del repo en desarrollo; en Docker se copia junto al codigo (/app/shared).
_RAIZ = Path(__file__).resolve().parent.parent
if (_RAIZ / "shared").is_dir() and str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from shared.settings import descripcion_db, dsn_desde_entorno, mqtt_desde_entorno  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingestor")

# MQTT_HOST/PORT/USER/PASSWORD/TLS y DATABASE_URL o DB_* (ver shared/settings.py).
MQTT = mqtt_desde_entorno()
MQTT_HOST, MQTT_PORT = MQTT["host"], MQTT["port"]
MQTT_RETRY_SECONDS = 5
TOPIC = "ecoair/+/telemetry"
DB_DSN = dsn_desde_entorno()

# Codigos de CONNACK de credenciales (MQTT 3.1.1: 4 y 5; paho 2.x los convierte a 134 y 135).
CODIGOS_CREDENCIALES = {4, 5, 134, 135}

RANGES = {
    "temperature": (-40, 80),
    "humidity": (0, 100),
    "pm25": (0, 1000),
    "pm10": (0, 1000),
}

db = None


def get_db():
    global db
    if db is None or db.closed:
        db = psycopg.connect(DB_DSN, autocommit=True)
        log.info("Conectado a PostgreSQL (%s)", descripcion_db())
    return db


def reset_db():
    global db
    if db is not None:
        try:
            db.close()
        except psycopg.Error:
            pass
    db = None


def validate(topic_device, data):
    if not isinstance(data, dict):
        raise ValueError("el mensaje no es un objeto JSON")
    device_id = data.get("deviceId")
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("falta deviceId")
    if device_id != topic_device:
        raise ValueError(f"deviceId '{device_id}' no coincide con el topico '{topic_device}'")
    values = {}
    for field, (lo, hi) in RANGES.items():
        v = data.get(field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{field} falta o no es numerico")
        if not lo <= v <= hi:
            raise ValueError(f"{field}={v} fuera de rango [{lo}, {hi}]")
        values[field] = v
    return device_id, values


def resultado_conexion(codigo, es_fallo, usuario):
    """(ok, mensaje para el log) a partir del codigo de CONNACK. Nunca incluye la clave."""
    if not es_fallo:
        return True, f"Conectado al broker MQTT {MQTT_HOST}:{MQTT_PORT}"
    if codigo in CODIGOS_CREDENCIALES:
        quien = f"usuario '{usuario}'" if usuario else "sin usuario (anonimo)"
        return False, (f"El broker MQTT rechazo las credenciales ({quien}, codigo {codigo}). "
                       "Revisa MQTT_USER y MQTT_PASSWORD del ingestor y el ACL de Mosquitto.")
    return False, f"El broker MQTT rechazo la conexion (codigo {codigo})"


def on_connect(client, userdata, flags, reason_code, properties):
    ok, mensaje = resultado_conexion(getattr(reason_code, "value", reason_code),
                                     getattr(reason_code, "is_failure", False), MQTT["user"])
    if not ok:
        log.error(mensaje)  # paho reintenta solo, con espera creciente (reconnect_delay_set)
        return
    log.info(mensaje)
    client.subscribe(TOPIC, qos=0)


def configurar_cliente(client, cfg):
    """Credenciales y TLS opcionales; espera creciente entre reconexiones."""
    if cfg["user"]:
        client.username_pw_set(cfg["user"], cfg["password"])
    if cfg["tls"]:
        client.tls_set(ca_certs=cfg["ca_cert"])
    client.reconnect_delay_set(min_delay=MQTT_RETRY_SECONDS, max_delay=60)
    return client


def on_message(client, userdata, msg):
    try:
        topic_device = msg.topic.split("/")[1]
        data = json.loads(msg.payload.decode("utf-8"))
        device_id, v = validate(topic_device, data)
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("Mensaje descartado en %s: %s", msg.topic, e)
        return

    for attempt in (1, 2):
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO devices (device_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (device_id,),
                )
                cur.execute(
                    "INSERT INTO measurements (device_id, temperature, humidity, pm25, pm10) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (device_id, v["temperature"], v["humidity"], v["pm25"], v["pm10"]),
                )
            log.info("Guardado %s %s", device_id, v)
            return
        except psycopg.Error as e:
            log.error("Error de base de datos (intento %d/2): %s", attempt, e)
            reset_db()


def main():
    client = configurar_cliente(mqtt.Client(mqtt.CallbackAPIVersion.VERSION2), MQTT)
    client.on_connect = on_connect
    client.on_message = on_message
    log.info("Broker MQTT %s:%s, %s, TLS=%s", MQTT_HOST, MQTT_PORT,
             f"usuario '{MQTT['user']}'" if MQTT["user"] else "sin usuario", MQTT["tls"])
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT)
            break
        except OSError as e:
            log.error("No se pudo conectar al broker %s:%s (%s); reintento en %ss",
                      MQTT_HOST, MQTT_PORT, e, MQTT_RETRY_SECONDS)
            time.sleep(MQTT_RETRY_SECONDS)
    client.loop_forever()


if __name__ == "__main__":
    main()
