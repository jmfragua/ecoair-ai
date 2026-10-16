"""Configuracion comun por variables de entorno: base de datos y broker MQTT.

Sin dependencias externas. La usan ingestor, api, alerter, notifier y db/migrate.py.

Base de datos:
- Si existe DATABASE_URL (p. ej. la que da Railway: postgresql://usuario:clave@host:puerto/base),
  se usa tal cual; libpq acepta URIs.
- Si no, se construye con DB_HOST, DB_PORT, DB_NAME, DB_USER y POSTGRES_PASSWORD, con los
  valores por defecto de siempre (localhost:5433, base y usuario "ecoair"), para que el
  docker compose local funcione igual que antes.

MQTT: MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD, MQTT_TLS y MQTT_CA_CERT.
Ninguna funcion devuelve ni registra claves en logs: solo las pasa a la conexion.
"""

import os

DB_DEFAULTS = {"DB_HOST": "localhost", "DB_PORT": "5433", "DB_NAME": "ecoair", "DB_USER": "ecoair"}
MQTT_DEFAULTS = {"MQTT_HOST": "localhost", "MQTT_PORT": "1883"}
_VERDADERO = ("1", "true", "si", "sí", "yes")


def _valor_libpq(v):
    """Comillas para un valor de conninfo (libpq): soporta espacios, comillas y barras."""
    v = str(v)
    return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"


def dsn_desde_entorno(env=None, connect_timeout=None):
    """Cadena de conexion a PostgreSQL desde variables de entorno.

    connect_timeout (segundos) se anade si se pide y la URL no lo trae ya.
    """
    env = os.environ if env is None else env
    url = (env.get("DATABASE_URL") or "").strip()
    if url:
        if connect_timeout and "connect_timeout=" not in url:
            url += ("&" if "?" in url else "?") + f"connect_timeout={int(connect_timeout)}"
        return url
    partes = {
        "host": env.get("DB_HOST") or DB_DEFAULTS["DB_HOST"],
        "port": env.get("DB_PORT") or DB_DEFAULTS["DB_PORT"],
        "dbname": env.get("DB_NAME") or DB_DEFAULTS["DB_NAME"],
        "user": env.get("DB_USER") or DB_DEFAULTS["DB_USER"],
        "password": env.get("POSTGRES_PASSWORD", ""),
    }
    if connect_timeout:
        partes["connect_timeout"] = int(connect_timeout)
    return " ".join(f"{k}={_valor_libpq(v)}" for k, v in partes.items())


def descripcion_db(env=None):
    """Destino de la base para el log, SIN usuario ni clave (p. ej. 'postgres:5432/ecoair')."""
    env = os.environ if env is None else env
    url = (env.get("DATABASE_URL") or "").strip()
    if url:
        resto = url.split("@", 1)[-1] if "@" in url else url.split("://", 1)[-1]
        return resto.split("?", 1)[0]
    return (f"{env.get('DB_HOST') or DB_DEFAULTS['DB_HOST']}:{env.get('DB_PORT') or DB_DEFAULTS['DB_PORT']}"
            f"/{env.get('DB_NAME') or DB_DEFAULTS['DB_NAME']}")


def mqtt_desde_entorno(env=None):
    """Datos de conexion al broker. user/password son None si no se configuraron.

    Lanza ValueError si hay usuario sin clave (o al reves) o si el puerto no es valido.
    """
    env = os.environ if env is None else env
    user = (env.get("MQTT_USER") or "").strip() or None
    password = env.get("MQTT_PASSWORD") or None
    if (user is None) != (password is None):
        raise ValueError("MQTT_USER y MQTT_PASSWORD deben configurarse juntos")
    try:
        port = int(env.get("MQTT_PORT") or MQTT_DEFAULTS["MQTT_PORT"])
    except ValueError:
        raise ValueError("MQTT_PORT debe ser un numero") from None
    if not 1 <= port <= 65535:
        raise ValueError("MQTT_PORT fuera de rango")
    return {
        "host": env.get("MQTT_HOST") or MQTT_DEFAULTS["MQTT_HOST"],
        "port": port,
        "user": user,
        "password": password,
        "tls": (env.get("MQTT_TLS") or "false").strip().lower() in _VERDADERO,
        "ca_cert": (env.get("MQTT_CA_CERT") or "").strip() or None,
    }
