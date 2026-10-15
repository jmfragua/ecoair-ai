"""Calidad del aire actual de un dispositivo. Modulo puro: sin base de datos ni red.

construir_respuesta recibe las medias ya calculadas por SQL, la ultima medicion y la hora
actual, y arma la respuesta del endpoint /devices/{id}/air-quality/current.

Supuestos del proyecto (no son de la Res. 2254/2017):
- COBERTURA_MIN_24H: la ventana de 24 h se marca "parcial" si los minutos con datos cubren
  menos del 75 % (18 h). En ese caso la media no se presenta como media de 24 h.
- El ICA se calcula con la media de 1 h para mostrar el estado actual; es indicativo, porque
  la Resolucion define el ICA con promedios de 24 h (ver docs/normas/normas_aire.md, sec. 5).
- Si la ultima medicion tiene mas de MAX_ANTIGUEDAD (5 min), el estado es "sin_datos" y no se
  calcula ICA. Exactamente 5 min todavia cuenta como datos actuales.
"""

from datetime import timedelta

from ica import calcular_ica, ica_general, nivel_alerta
from recomendaciones import recomendacion

FUENTE = "Res. 2254/2017"
ADVERTENCIA_ESTACION = "Estación de bajo costo, no sustituye mediciones oficiales"
MAX_ANTIGUEDAD = timedelta(minutes=5)
COBERTURA_MIN_24H = 0.75
LIMITE_DIARIO = {"pm25": 37, "pm10": 75}  # Res. 2254, Art. 2, Tabla 1 (24 h, µg/m³)
SEVERIDAD = [None, "prevencion", "alerta", "emergencia"]


def _ventana(v, horas):
    minutos = v.get("minutos_con_datos") or 0
    return {
        "pm25_media": None if v.get("pm25_media") is None else round(float(v["pm25_media"]), 1),
        "pm10_media": None if v.get("pm10_media") is None else round(float(v["pm10_media"]), 1),
        "muestras": int(v.get("muestras") or 0),
        "horas_con_datos": round(minutos / 60, 2),
        "cobertura": round(minutos / (horas * 60), 3),
    }


def construir_respuesta(device_id, ventana_1h, ventana_24h, ultima, ahora):
    """Arma la respuesta de calidad del aire actual.

    ventana_1h / ventana_24h: dict con pm25_media, pm10_media, muestras y minutos_con_datos.
    ultima: dict con received_at, temperature y humidity (o None si nunca hubo datos).
    ahora: datetime con zona horaria (UTC).
    """
    base = {
        "device_id": device_id,
        "calculado_utc": ahora.isoformat(timespec="seconds"),
        "fuente": FUENTE,
        "advertencia": ADVERTENCIA_ESTACION,
        "advertencias": [],
    }

    if ultima is None:
        return {**base, "estado": "sin_datos", "ultima_medicion": None,
                "advertencias": ["El dispositivo no tiene mediciones."]}

    antiguedad = ahora - ultima["received_at"]
    ultima_medicion = {
        "received_at": ultima["received_at"].isoformat(timespec="seconds"),
        "antiguedad_s": int(antiguedad.total_seconds()),
        "temperature": None if ultima.get("temperature") is None else float(ultima["temperature"]),
        "humidity": None if ultima.get("humidity") is None else float(ultima["humidity"]),
    }
    if antiguedad > MAX_ANTIGUEDAD or not ventana_1h.get("muestras"):
        minutos = int(antiguedad.total_seconds() // 60)
        return {**base, "estado": "sin_datos", "ultima_medicion": ultima_medicion,
                "advertencias": [f"La última medición tiene {minutos} min (más de 5 min): no se calcula el ICA."]}

    v1 = _ventana(ventana_1h, 1)
    v24 = _ventana(ventana_24h, 24)
    v24["parcial"] = v24["cobertura"] < COBERTURA_MIN_24H
    advertencias = [
        "El ICA se calcula con la media de la última hora y es indicativo: la Res. 2254 define "
        "el ICA con promedios de 24 h.",
    ]
    if v24["parcial"]:
        advertencias.append(
            f"La ventana de 24 h solo cubre {v24['horas_con_datos']} h de datos (menos del 75 %): "
            "su media, el nivel de alerta y la comparación con el límite diario son parciales y "
            "no equivalen a una media de 24 h."
        )

    ica_pm25 = calcular_ica("pm25", float(ventana_1h["pm25_media"]))
    ica_pm10 = calcular_ica("pm10", float(ventana_1h["pm10_media"]))
    general = ica_general(float(ventana_1h["pm25_media"]), float(ventana_1h["pm10_media"]))

    niveles = {p: nivel_alerta(p, float(ventana_24h[f"{p}_media"])) for p in ("pm25", "pm10")}
    peor = max(niveles.values(), key=SEVERIDAD.index)
    supera = {p: float(ventana_24h[f"{p}_media"]) > LIMITE_DIARIO[p] for p in ("pm25", "pm10")}

    return {
        **base,
        "estado": "ok",
        "advertencias": advertencias,
        "ultima_medicion": ultima_medicion,
        "ventana_1h": v1,
        "ventana_24h": v24,
        "ica": {
            "base": "media de 1 h (indicativo)",
            "pm25": ica_pm25,
            "pm10": ica_pm10,
            "general": {
                "ica": general["ica"],
                "category_code": general["category_code"],
                "category_name": general["category_name"],
                "color": general["color"],
                "definido_por": general["pollutant"],
            },
        },
        "recomendacion": recomendacion(general["category_code"]),
        "norma_24h": {
            "base": "media de 24 h" + (" (PARCIAL)" if v24["parcial"] else ""),
            "parcial": v24["parcial"],
            "nivel_alerta": peor,
            "nivel_alerta_por_contaminante": niveles,
            "limite_diario": LIMITE_DIARIO,
            "supera_limite_diario": supera,
        },
    }
