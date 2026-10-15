"""Features para la deteccion de anomalias. Funcion pura, sin base de datos.

Supuestos:
- Primero se promedia por minuto (resample de 1 min), para que los datos simulados
  (1 por minuto) y los reales del ESP32 (1 cada 5 s) tengan la misma cadencia.
- Todas las ventanas y el umbral de hueco estan en minutos, no en filas.
- Si entre dos minutos con datos pasan mas de GAP_MINUTES, empieza un segmento nuevo:
  las ventanas moviles no cruzan el hueco. Un hueco mas corto no corta el segmento, y
  en ese caso pm25_cambio compara con el ultimo minuto con datos.
- Una fila sin ventana completa (menos de WINDOW_PM25_MIN minutos desde el inicio de su
  segmento) se descarta y se cuenta.
- Contexto por hora: las medianas por hora salen del entrenamiento (calcular_contexto_horario)
  y se aplican igual al puntuar (agregar_contexto).
"""

import numpy as np
import pandas as pd

VARIABLES = ["temperature", "humidity", "pm25", "pm10"]
GAP_MINUTES = 3
WINDOW_PM25_MIN = 15
WINDOW_STD_MIN = 10
COLOMBIA_OFFSET = pd.Timedelta(hours=5)

FEATURES = [
    "temperature",
    "humidity",
    "pm25",
    "pm10",
    "pm25_cambio",
    "pm25_media_15",
    "pm25_std_15",
    "temperature_std_10",
    "humidity_std_10",
    "pm25_std_10",
    "pm10_std_10",
    "ratio_pm10_pm25",
    "hora_sin",
    "hora_cos",
]

# Contexto por hora: desviacion respecto a la mediana de esa hora (hora Colombia) en el
# entrenamiento. Las medianas se guardan en el modelo y se reutilizan al puntuar.
CONTEXT_VARS = ["pm25", "humidity", "temperature"]
CONTEXT_FEATURES = [f"{v}_vs_hora" for v in CONTEXT_VARS]
MODEL_FEATURES = FEATURES + CONTEXT_FEATURES


def promediar_por_minuto(df):
    """Promedio por minuto (resample de 1 min) de las 4 variables; los minutos sin datos no aparecen.

    Devuelve un DataFrame con timestamp (UTC, inicio del minuto) y VARIABLES.
    """
    data = df[["timestamp"] + VARIABLES].copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    return (
        data.set_index("timestamp")[VARIABLES]
        .astype(float)
        .sort_index()
        .resample("1min")
        .mean()
        .dropna(how="any")
        .rename_axis("timestamp")
        .reset_index()
    )


def hora_local(timestamps):
    """Hora entera del dia en hora Colombia (UTC-5)."""
    return (pd.DatetimeIndex(timestamps) - COLOMBIA_OFFSET).hour.to_numpy()


def calcular_contexto_horario(feats):
    """Mediana por hora del dia (hora Colombia) de CONTEXT_VARS, con los datos de entrenamiento.

    Devuelve {variable: [24 medianas]}; una hora sin datos usa la mediana global.
    """
    horas = hora_local(feats["timestamp"])
    contexto = {}
    for v in CONTEXT_VARS:
        por_hora = feats[v].groupby(horas).median()
        global_ = float(feats[v].median())
        contexto[v] = [float(por_hora.get(h, global_)) for h in range(24)]
    return contexto


def agregar_contexto(feats, contexto):
    """Anade <var>_vs_hora = valor - mediana de esa hora en el entrenamiento."""
    out = feats.copy()
    horas = hora_local(out["timestamp"])
    for v in CONTEXT_VARS:
        out[f"{v}_vs_hora"] = out[v].to_numpy() - np.asarray(contexto[v])[horas]
    return out


def construir_features(df, gap_minutes=GAP_MINUTES):
    """Construye las features a partir de timestamp (UTC), temperature, humidity, pm25 y pm10.

    Devuelve (features, info). features tiene la columna timestamp (un minuto por fila)
    mas FEATURES. info cuenta filas de entrada, minutos, segmentos y filas descartadas.
    """
    per_min = promediar_por_minuto(df).set_index("timestamp")

    ts = per_min.index.to_series()
    segment = (ts.diff() > pd.Timedelta(minutes=gap_minutes)).cumsum().to_numpy()
    seg_start = ts.groupby(segment).transform("min")

    parts = []
    for _, g in per_min.groupby(segment):
        f = g.copy()
        f["pm25_cambio"] = g["pm25"].diff()
        r15 = g["pm25"].rolling(f"{WINDOW_PM25_MIN}min")
        f["pm25_media_15"] = r15.mean()
        f["pm25_std_15"] = r15.std()
        for v in VARIABLES:
            f[f"{v}_std_10"] = g[v].rolling(f"{WINDOW_STD_MIN}min").std()
        parts.append(f)
    feats = pd.concat(parts) if parts else per_min.reindex(columns=FEATURES)

    feats["ratio_pm10_pm25"] = feats["pm10"] / feats["pm25"].clip(lower=0.1)
    local = feats.index - COLOMBIA_OFFSET
    hour = local.hour + local.minute / 60
    feats["hora_sin"] = np.sin(2 * np.pi * hour / 24)
    feats["hora_cos"] = np.cos(2 * np.pi * hour / 24)

    completa = (ts - seg_start) >= pd.Timedelta(minutes=WINDOW_PM25_MIN - 1)
    completa &= feats[FEATURES].notna().all(axis=1)
    out = feats.loc[completa, FEATURES].rename_axis("timestamp").reset_index()

    info = {
        "filas_entrada": len(df),
        "minutos": len(per_min),
        "segmentos": int(segment.max() + 1) if len(per_min) else 0,
        "descartadas": int((~completa).sum()),
        "filas_features": len(out),
    }
    return out, info
