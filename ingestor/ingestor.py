import json
import logging
import os
import time

import paho.mqtt.client as mqtt
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingestor")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_RETRY_SECONDS = 5
TOPIC = "ecoair/+/telemetry"
DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5433')} "
    f"dbname=ecoair user=ecoair password={os.environ['POSTGRES_PASSWORD']}"
)

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
        log.info("Conectado a PostgreSQL")
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


def on_connect(client, userdata, flags, reason_code, properties):
    log.info("Conectado al broker MQTT (%s)", reason_code)
    client.subscribe(TOPIC, qos=0)


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
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
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
