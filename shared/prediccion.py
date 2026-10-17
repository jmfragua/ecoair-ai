"""Prediccion EXPERIMENTAL de PM2.5 a 1 y 3 horas. Python puro: sin numpy ni scikit-learn.

El modelo es un JSON pequeno (modelos/prediccion_pm25_v1.json) generado por
ai/entrenar_prediccion.py. Aqui solo se aplica la formula, igual en la API y en el notifier.

Modelo: regresion lineal con regularizacion (Ridge) sobre el RESIDUO de la persistencia:
    prediccion = ultimo_valor + (intercepto + coef . variables_estandarizadas)
Linea base: persistencia (dentro de h horas habra lo mismo que ahora).

Reglas de honestidad (decididas por el equipo):
- Un horizonte solo se muestra si el modelo SUPERO a la persistencia en la evaluacion
  (supera_persistencia = true en el JSON). Si no: "Prediccion no disponible: el modelo no supero
  la linea base". La persistencia NUNCA se presenta como prediccion.
- Siempre se dice con que fuentes se entreno ("Modelo entrenado con ... Experimental.") y, si
  incluye datos simulados, se avisa.

Variables (todas de PM2.5 promediado por minuto; hora de Colombia, UTC-5 fijo):
    ult            media de los ultimos 5 min (tambien es la persistencia)
    media_15/60/180
    pendiente_15_60  media_15 - media_60
    hora_sin/hora_cos
"""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

VARIABLES = ["ult", "media_15", "media_60", "media_180", "pendiente_15_60", "hora_sin", "hora_cos"]
HORIZONTES = (60, 180)          # minutos
COLOMBIA = timezone(timedelta(hours=-5))
# Datos minimos (minutos con datos) en cada ventana para poder calcular las variables
MINIMOS = {5: 3, 15: 10, 60: 40, 180: 120}
VENTANA_OBJETIVO = 5           # el objetivo es la media de 5 min alrededor del horizonte
NO_DISPONIBLE = "Predicción no disponible: el modelo no superó la línea base."
NOMBRE_FUENTE = {"reales": "datos propios reales", "externos": "datos externos", "simulados": "datos simulados"}

# PM2.5 por minuto de las ultimas 3 h (+5 min de margen). Parametros: device_id, ahora.
SQL_SERIE_PM25 = (
    "SELECT date_trunc('minute', received_at) AS t, avg(pm25) AS pm25 FROM measurements "
    "WHERE device_id = %(device_id)s AND received_at > %(ahora)s - interval '185 minutes' "
    "AND received_at <= %(ahora)s GROUP BY 1 ORDER BY 1"
)

_cache = {}


# ---------- Serie por minuto ----------

def minuto(dt):
    """datetime con zona -> entero (minutos desde 1970, UTC)."""
    return int(dt.timestamp() // 60)


def serie_por_minuto(filas):
    """[(datetime, pm25)] -> {minuto: media}. Ignora valores None."""
    suma, n = {}, {}
    for t, v in filas:
        if v is None:
            continue
        m = minuto(t)
        suma[m] = suma.get(m, 0.0) + float(v)
        n[m] = n.get(m, 0) + 1
    return {m: suma[m] / n[m] for m in suma}


def _media(serie, fin, n):
    vals = [serie[m] for m in range(fin - n + 1, fin + 1) if m in serie]
    return (sum(vals) / len(vals)) if len(vals) >= MINIMOS[n] else None


def variables_en(serie, m):
    """Variables en el minuto m, o None si faltan datos recientes."""
    medias = {n: _media(serie, m, n) for n in (5, 15, 60, 180)}
    if any(v is None for v in medias.values()):
        return None
    hora = datetime.fromtimestamp(m * 60, tz=COLOMBIA)
    h = hora.hour + hora.minute / 60
    return {
        "ult": medias[5], "media_15": medias[15], "media_60": medias[60], "media_180": medias[180],
        "pendiente_15_60": medias[15] - medias[60],
        "hora_sin": math.sin(2 * math.pi * h / 24), "hora_cos": math.cos(2 * math.pi * h / 24),
    }


def objetivo_en(serie, m, horizonte):
    """Media de PM2.5 en los 5 min que terminan en m + horizonte (para entrenar/evaluar)."""
    fin = m + horizonte
    vals = [serie[k] for k in range(fin - VENTANA_OBJETIVO + 1, fin + 1) if k in serie]
    return (sum(vals) / len(vals)) if len(vals) >= 3 else None


# ---------- Modelo ----------

def ruta_modelo_por_defecto():
    """modelos/ junto a shared/ (repo en desarrollo, /app en Docker)."""
    return Path(__file__).resolve().parent.parent / "modelos" / "prediccion_pm25_v1.json"


def cargar_modelo(ruta=None):
    """Lee el JSON del modelo (con cache). None si no existe."""
    ruta = Path(ruta or ruta_modelo_por_defecto())
    clave = str(ruta)
    if clave not in _cache:
        _cache[clave] = json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else None
    return _cache[clave]


def predecir_horizonte(modelo, x, horizonte):
    """Prediccion (ug/m3) para un horizonte con las variables x. Nunca negativa."""
    h = modelo["horizontes"][str(horizonte)]
    z = [(x[v] - mu) / (s or 1.0) for v, mu, s in zip(modelo["variables"], modelo["media"], modelo["escala"])]
    delta = h["intercepto"] + sum(c * zi for c, zi in zip(h["coef"], z))
    return max(0.0, x["ult"] + delta)


def texto_fuentes(modelo):
    tipos = [f["tipo"] for f in modelo.get("fuentes", [])]
    nombres = [NOMBRE_FUENTE.get(t, t) for t in dict.fromkeys(tipos)]
    return "Modelo entrenado con " + (" y ".join(nombres) if nombres else "fuentes no registradas") + ". Experimental."


def aviso_simulados(modelo):
    if any(f["tipo"] == "simulados" for f in modelo.get("fuentes", [])):
        return ("Atención: el modelo se entrenó con datos simulados; su error medido no representa "
                "la precisión con el sensor real.")
    return None


def tendencia_3h(serie, ahora_min):
    """Texto con la tendencia de las ultimas 3 h (para el correo cuando no hay prediccion)."""
    inicio = _media(serie, ahora_min - 165, 15)   # 15 min alrededor de hace 3 h
    fin = _media(serie, ahora_min, 15)
    if inicio is None or fin is None:
        return "Tendencia de las últimas 3 horas: sin datos suficientes."
    dif = fin - inicio
    sentido = "subió" if dif > 2 else "bajó" if dif < -2 else "se mantuvo estable"
    return (f"Tendencia de las últimas 3 horas: el PM2.5 {sentido} "
            f"(de {inicio:.1f} a {fin:.1f} µg/m³). Es lo observado, no una predicción.")


def prediccion(modelo, serie, ahora):
    """Resultado para el dashboard y el correo.

    estado: 'ok' (al menos un horizonte disponible), 'no_supera_linea_base', 'sin_datos' o
    'sin_modelo'. Los horizontes que no superaron a la persistencia no traen valor.
    """
    if modelo is None:
        return {"estado": "sin_modelo", "texto": "Predicción no disponible: no hay modelo cargado.",
                "fuentes": None, "aviso": None, "horizontes": []}
    m = minuto(ahora)
    base = {"modelo": modelo.get("nombre"), "fuentes": texto_fuentes(modelo), "aviso": aviso_simulados(modelo),
            "experimental": True}
    disponibles = [h for h in HORIZONTES if modelo["horizontes"][str(h)].get("supera_persistencia")]
    if not disponibles:
        return {**base, "estado": "no_supera_linea_base", "texto": NO_DISPONIBLE, "horizontes": [],
                "tendencia": tendencia_3h(serie, m)}
    x = variables_en(serie, m)
    if x is None:
        return {**base, "estado": "sin_datos", "horizontes": [],
                "texto": "Predicción no disponible: faltan datos de las últimas 3 horas.",
                "tendencia": tendencia_3h(serie, m)}
    horizontes, partes = [], []
    for h in HORIZONTES:
        info = modelo["horizontes"][str(h)]
        if h in disponibles:
            v = round(predecir_horizonte(modelo, x, h), 1)
            horizontes.append({"minutos": h, "pm25": v, "disponible": True,
                               "mae_modelo": info["metricas"]["mae_modelo"],
                               "mae_persistencia": info["metricas"]["mae_persistencia"]})
            partes.append(f"PM2.5 estimado en {h // 60} h: {v:.1f} µg/m³")
        else:
            horizontes.append({"minutos": h, "pm25": None, "disponible": False})
            partes.append(f"a {h // 60} h: no disponible (el modelo no superó la línea base)")
    return {**base, "estado": "ok", "horizontes": horizontes, "texto": "; ".join(partes) + "."}
