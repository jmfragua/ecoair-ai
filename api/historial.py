"""Historial de calidad del aire por intervalos. Modulo puro: sin base de datos ni red.

Define el SQL (lo ejecuta main.py) y convierte sus filas en la respuesta del endpoint
/devices/{id}/air-quality/history. Los intervalos se alinean a un origen fijo (2000-01-01 UTC)
para que no "bailen" entre consultas.
"""

from datetime import timedelta

from calidad_aire import COBERTURA_MIN_24H

HORAS_MIN, HORAS_MAX = 1, 168
BUCKET_MIN, BUCKET_MAX = 1, 60

SQL_HISTORIAL = (
    "SELECT date_bin(make_interval(mins => %(bucket_min)s), received_at, "
    "TIMESTAMPTZ '2000-01-01 00:00:00+00') AS t, "
    "avg(pm25) AS pm25, avg(pm10) AS pm10, avg(temperature) AS temperature, "
    "avg(humidity) AS humidity, count(*) AS muestras "
    "FROM measurements "
    "WHERE device_id = %(device_id)s AND received_at > %(desde)s AND received_at <= %(ahora)s "
    "GROUP BY t ORDER BY t"
)
# Marca de simulado de la medicion mas reciente (usa el indice device_id, received_at).
SQL_ES_SIMULADO = (
    "SELECT is_simulated FROM measurements WHERE device_id = %(device_id)s AND received_at <= now() "
    "ORDER BY received_at DESC LIMIT 1"
)


def _num(v, decimales=1):
    return None if v is None else round(float(v), decimales)


def construir_historial(device_id, filas, ahora, hours, bucket_min, es_simulado):
    """Respuesta del historial. `filas`: resultado de SQL_HISTORIAL (dicts).

    cobertura = intervalos con datos x bucket_min / (hours x 60). "parcial" usa el mismo
    supuesto del proyecto que la ventana de 24 h (COBERTURA_MIN_24H = 75 %).
    """
    puntos = [
        {
            "t": f["t"].isoformat(timespec="seconds"),
            "pm25": _num(f["pm25"]),
            "pm10": _num(f["pm10"]),
            "temperature": _num(f["temperature"]),
            "humidity": _num(f["humidity"]),
            "muestras": int(f["muestras"]),
        }
        for f in filas
    ]
    cobertura = len(puntos) * bucket_min / (hours * 60)
    return {
        "device_id": device_id,
        "desde_utc": (ahora - timedelta(hours=hours)).isoformat(timespec="seconds"),
        "hasta_utc": ahora.isoformat(timespec="seconds"),
        "hours": hours,
        "bucket_min": bucket_min,
        "is_simulated": bool(es_simulado),
        "horas_con_datos": round(len(puntos) * bucket_min / 60, 2),
        "cobertura": round(min(cobertura, 1.0), 3),
        "parcial": cobertura < COBERTURA_MIN_24H,
        "puntos": puntos,
    }
