"""Puntuacion de anomalias con un modelo ya entrenado. Sin base de datos.

El modelo es el dict que guarda entrenar_anomalias.py: scaler, model (IsolationForest),
features, contexto (medianas por hora), median y escala (MAD x 1.4826 de cada feature).
score = -score_samples: cuanto mas alto, mas anomalo.

Explicacion: distancia de cada feature a su mediana en unidades de escala, con un piso
(PISO_ESCALA) para que una feature casi constante no de cifras absurdas. Si pasa de
TOPE_VECES se escribe "muy por encima/debajo de lo habitual". El piso solo afecta a los
mensajes, no al modelo.
"""

import pandas as pd

from features import VARIABLES, agregar_contexto, construir_features, hora_local

NOMBRES = {"temperature": "Temperatura", "humidity": "Humedad", "pm25": "PM2.5", "pm10": "PM10"}
UNIDADES = {"temperature": "°C", "humidity": "puntos", "pm25": "µg/m³", "pm10": "µg/m³"}
PISO_ESCALA = {
    "temperature": 0.5, "humidity": 2.0, "pm25": 1.0, "pm10": 1.0,
    "pm25_cambio": 1.0, "pm25_media_15": 1.0, "pm25_std_15": 0.1,
    "temperature_std_10": 0.1, "humidity_std_10": 0.1, "pm25_std_10": 0.1, "pm10_std_10": 0.1,
    "ratio_pm10_pm25": 0.1, "hora_sin": 0.1, "hora_cos": 0.1,
    "pm25_vs_hora": 1.0, "humidity_vs_hora": 2.0, "temperature_vs_hora": 0.5,
}
DETALLE = {
    "pm25_media_15": "por su media de 15 min",
}
VENTANA_STD = {"pm25_std_15": 15}  # el resto de *_std son de 10 min
TOPE_VECES = 50
UMBRAL_OTRAS = 3
EPISODIO_GAP_MIN = 10


def _variable(feature):
    for v in VARIABLES:
        if feature == v or feature.startswith(v + "_"):
            return v
    return None


def _intensidad(veces, signo):
    if veces > TOPE_VECES:
        return "muy por encima de lo habitual" if signo > 0 else "muy por debajo de lo habitual"
    return f"unas {veces:.0f} veces su variación habitual"


def desviaciones(row, modelo):
    """Distancia con signo de cada feature a su mediana, en unidades de escala (con piso)."""
    features = modelo["features"]
    x = row[features].astype(float)
    return (x - modelo["median"][features]) / _escala_con_piso(modelo)


def _escala_con_piso(modelo):
    features = modelo["features"]
    piso = pd.Series({f: PISO_ESCALA.get(f, 0.0) for f in features})
    return modelo["escala"][features].combine(piso, max)


def explicar(row, modelo):
    """Mensaje en espanol con la feature mas alejada de su mediana."""
    z = desviaciones(row, modelo)
    orden = z.abs().sort_values(ascending=False)
    top = orden.index[0]
    valor, signo, veces = row[top], z[top], abs(z[top])

    if top in ("hora_sin", "hora_cos"):
        msg = "Lectura inusual para la hora del día."
    elif top == "ratio_pm10_pm25":
        msg = (f"Relación PM10/PM2.5 de {valor:.2f}, {_intensidad(veces, signo)}. "
               "Posible fallo de un canal del sensor de partículas.")
    elif top == "pm25_cambio":
        actual = row["pm25"]
        verbo = "subió" if valor > 0 else "bajó"
        causa = "Posible pico de contaminación." if valor > 0 else "Posible fin de un pico o lectura anómala."
        msg = (f"PM2.5 {verbo} {abs(valor):.0f} µg/m³ en 1 min (de {actual - valor:.0f} a {actual:.0f}), "
               f"un salto {_intensidad(veces, 1)}. {causa}")
    elif top.endswith("_vs_hora"):
        var = _variable(top)
        hora = f" a las {hora_local([row['timestamp']])[0]} h" if "timestamp" in row.index else ""
        lado = "por encima" if valor > 0 else "por debajo"
        unidad = UNIDADES[var]
        actual = row[var]
        valor_txt = f"{actual:.0f} %" if var == "humidity" else f"{actual:.1f} {unidad}"
        causa = {
            "pm25": "Posible contaminación fuera de horario.",
            "humidity": "Posible condición atípica o fallo del sensor de humedad.",
            "temperature": "Posible condición atípica o fallo del sensor de temperatura.",
        }[var]
        msg = (f"{NOMBRES[var]} de {valor_txt}{hora}, {abs(valor):.1f} {unidad} {lado} "
               f"de lo habitual para esa hora ({_intensidad(veces, signo)}). {causa}")
    elif top.endswith("_std_10") or top in VENTANA_STD:
        # Lo anomalo es la variabilidad, no el valor actual: no se menciona el valor.
        var = _variable(top)
        ventana = VENTANA_STD.get(top, 10)
        if signo < 0:
            msg = (f"{NOMBRES[var]} casi sin variación en {ventana} min (desv. {valor:.2f}). "
                   "Posible sensor congelado.")
        else:
            msg = (f"{NOMBRES[var]} con variación inusual en los últimos {ventana} min "
                   f"(desv. {valor:.1f} {UNIDADES[var]}, {_intensidad(veces, signo)}). "
                   "Posible cambio brusco o lectura inestable.")
    else:
        var = _variable(top)
        actual = row[var]
        if var in ("pm25", "pm10"):
            texto = f"{NOMBRES[var]} de {actual:.0f} µg/m³"
            causa = "Posible pico de contaminación." if signo > 0 else "Valor inusualmente bajo; revisar el sensor."
        elif var == "temperature":
            texto = f"Temperatura de {actual:.1f} °C"
            causa = "Posible fallo del sensor de temperatura."
        else:
            texto = f"Humedad de {actual:.0f} %"
            causa = "Posible fallo del sensor de humedad o condición inusual."
        detalle = f" ({DETALLE[top]})" if top in DETALLE else ""
        msg = f"{texto}, {_intensidad(veces, signo)}{detalle}. {causa}"

    otras = [f for f in orden.index[1:3] if orden[f] >= UMBRAL_OTRAS]
    if otras:
        msg += f" También alejadas: {', '.join(otras)}."
    return msg


def puntuar_features(feats, modelo):
    """Anade contexto por hora, score, es_anomalia y mensaje a las features de construir_features."""
    out = agregar_contexto(feats, modelo["contexto"])
    X = modelo["scaler"].transform(out[modelo["features"]])
    out["score"] = -modelo["model"].score_samples(X)
    out["es_anomalia"] = modelo["model"].predict(X) == -1
    out["mensaje"] = ""
    mask = out["es_anomalia"]
    if mask.any():
        out.loc[mask, "mensaje"] = [explicar(row, modelo) for _, row in out[mask].iterrows()]
    return out


def agrupar_episodios(scored, modelo, marcadas=None, gap_min=EPISODIO_GAP_MIN):
    """Fusiona filas marcadas separadas por menos de `gap_min` minutos en episodios de alarma.

    scored: salida de puntuar_features. marcadas: mascara booleana opcional (por defecto,
    es_anomalia). Devuelve inicio, fin (exclusivo: ultima fila + 1 min), score_max, filas y
    mensaje. El mensaje se explica con la fila del episodio cuya feature esta mas lejos de su
    mediana (en unidades de escala con piso).
    """
    marcadas = scored["es_anomalia"] if marcadas is None else pd.Series(marcadas, index=scored.index)
    m = scored[marcadas.to_numpy()].sort_values("timestamp")
    columnas = ["inicio", "fin", "score_max", "filas", "mensaje"]
    if m.empty:
        return pd.DataFrame(columns=columnas)
    features = modelo["features"]
    z = ((m[features] - modelo["median"][features]) / _escala_con_piso(modelo)).abs().max(axis=1)
    grupo = (m["timestamp"].diff() >= pd.Timedelta(minutes=gap_min)).cumsum()
    filas = []
    for _, g in m.groupby(grupo):
        fila_max = g.loc[z[g.index].idxmax()]
        filas.append({
            "inicio": g["timestamp"].iloc[0],
            "fin": g["timestamp"].iloc[-1] + pd.Timedelta(minutes=1),
            "score_max": float(g["score"].max()),
            "filas": int(len(g)),
            "mensaje": explicar(fila_max, modelo),
        })
    return pd.DataFrame(filas, columns=columnas)


def puntuar(df, modelo):
    """Mediciones crudas (timestamp, temperature, humidity, pm25, pm10) -> score, es_anomalia, mensaje."""
    feats, _ = construir_features(df)
    return puntuar_features(feats, modelo)
