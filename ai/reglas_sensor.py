"""Reglas deterministicas de salud del sensor. Funcion pura, sin base de datos.

Trabajan sobre el DataFrame por minuto de features.promediar_por_minuto
(timestamp UTC + temperature, humidity, pm25, pm10). No son IA: son reglas fijas.

- sensor_congelado: las 4 variables sin ningun cambio durante CONGELADO_MIN minutos o mas.
- salto_temperatura: cambio de mas de SALTO_TEMP_C °C entre minutos consecutivos.
- fuera_de_rango_fisico: algun valor fuera del rango del contrato MQTT. Como se evalua sobre
  promedios de 1 min, una sola lectura de 5 s fuera de rango puede quedar diluida; el ingestor
  ya rechaza esos mensajes, asi que esta regla es una red de seguridad.
- dispositivo_caido: mas de CAIDO_MIN minutos sin datos entre dos minutos con datos
  (o hasta `hasta`, si se pasa).

Cada deteccion: tipo, inicio, fin (exclusivo) y mensaje en espanol.
"""

import pandas as pd

from features import VARIABLES

RANGOS = {"temperature": (-40, 80), "humidity": (0, 100), "pm25": (0, 1000), "pm10": (0, 1000)}
NOMBRES = {"temperature": "Temperatura", "humidity": "Humedad", "pm25": "PM2.5", "pm10": "PM10"}
CONGELADO_MIN = 30
SALTO_TEMP_C = 10
CAIDO_MIN = 5
UN_MIN = pd.Timedelta(minutes=1)
COLUMNAS = ["tipo", "inicio", "fin", "mensaje"]


def _sensor_congelado(df):
    ts = df["timestamp"]
    igual = (df[VARIABLES].diff() == 0).all(axis=1) & (ts.diff() == UN_MIN)
    grupo = (~igual).cumsum()
    out = []
    for _, g in df.groupby(grupo):
        inicio, fin = g["timestamp"].iloc[0], g["timestamp"].iloc[-1] + UN_MIN
        minutos = int((fin - inicio) / UN_MIN)
        if minutos >= CONGELADO_MIN:
            v = g.iloc[0]
            out.append({
                "tipo": "sensor_congelado", "inicio": inicio, "fin": fin,
                "mensaje": (f"Las 4 variables sin ningún cambio durante {minutos} min "
                            f"(T {v.temperature:.1f} °C, HR {v.humidity:.1f} %, PM2.5 {v.pm25:.1f}, "
                            f"PM10 {v.pm10:.1f}). Posible sensor congelado."),
            })
    return out


def _salto_temperatura(df):
    ts = df["timestamp"]
    # Redondeo para que un salto de exactamente 10.0 (p. ej. 22.4 -> 32.4) no cuente por
    # error de coma flotante.
    delta = df["temperature"].diff().round(6)
    mask = (delta.abs() > SALTO_TEMP_C) & (ts.diff() == UN_MIN)
    out = []
    for i in df.index[mask]:
        antes, ahora = df.at[i - 1, "temperature"], df.at[i, "temperature"]
        out.append({
            "tipo": "salto_temperatura", "inicio": ts[i - 1], "fin": ts[i] + UN_MIN,
            "mensaje": (f"La temperatura pasó de {antes:.1f} a {ahora:.1f} °C en 1 min "
                        f"({ahora - antes:+.1f} °C). Posible fallo del sensor de temperatura."),
        })
    return out


def _fuera_de_rango(df):
    out = []
    for i, row in df.iterrows():
        malas = [f"{NOMBRES[v]} {row[v]:.1f}" for v, (lo, hi) in RANGOS.items() if not lo <= row[v] <= hi]
        if malas:
            out.append({
                "tipo": "fuera_de_rango_fisico", "inicio": row.timestamp, "fin": row.timestamp + UN_MIN,
                "mensaje": f"Valor fuera del rango físico del contrato: {', '.join(malas)}. Posible fallo del sensor.",
            })
    return out


def _dispositivo_caido(df, hasta=None):
    ts = df["timestamp"]
    out = []

    def caido(inicio, fin):
        minutos = int((fin - inicio) / UN_MIN)
        return {
            "tipo": "dispositivo_caido", "inicio": inicio, "fin": fin,
            "mensaje": f"Sin datos durante {minutos} min. Posible dispositivo caído o sin conexión.",
        }

    sin_datos = ts.diff() - UN_MIN
    for i in df.index[sin_datos > pd.Timedelta(minutes=CAIDO_MIN)]:
        out.append(caido(ts[i - 1] + UN_MIN, ts[i]))
    if hasta is not None and len(ts):
        hasta = pd.Timestamp(hasta)
        if hasta - (ts.iloc[-1] + UN_MIN) > pd.Timedelta(minutes=CAIDO_MIN):
            out.append(caido(ts.iloc[-1] + UN_MIN, hasta))
    return out


def _fusionar(detecciones):
    """Une detecciones del mismo tipo que se solapan o se tocan (p. ej. salto de subida y bajada)."""
    if detecciones.empty:
        return detecciones
    filas = []
    for _, g in detecciones.sort_values(["tipo", "inicio"]).groupby("tipo", sort=False):
        actual = None
        for r in g.to_dict("records"):
            if actual is not None and r["inicio"] <= actual["fin"]:
                actual["fin"] = max(actual["fin"], r["fin"])
            else:
                if actual is not None:
                    filas.append(actual)
                actual = r
        filas.append(actual)
    return pd.DataFrame(filas, columns=COLUMNAS)


def evaluar_reglas(df_min, hasta=None):
    """Aplica las 4 reglas al DataFrame por minuto. Devuelve un DataFrame (tipo, inicio, fin, mensaje)."""
    df = df_min.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    detecciones = (
        _sensor_congelado(df) + _salto_temperatura(df) + _fuera_de_rango(df) + _dispositivo_caido(df, hasta)
    )
    out = _fusionar(pd.DataFrame(detecciones, columns=COLUMNAS))
    return out.sort_values("inicio").reset_index(drop=True)
