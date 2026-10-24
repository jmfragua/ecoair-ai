"""Replay HISTORICO de la prediccion de PM2.5 (modelo prediccion_pm25_v4) con datos externos.

No usa las mediciones del proyecto (measurements) ni la tabla predictions: lee SOLO
external_measurements (fuentes 'IDEAM datos.gov.co' e 'IDEAM SISAIRE'), aplica la limpieza de
shared/limpieza_pm25.py y calcula todo al vuelo. Nunca escribe en la base ni guarda cifras en cache.

- Estacion del replay = una estacion fisica que puede tener filas en las dos fuentes (con nombres
  distintos). Si la misma hora esta en ambas, manda 'IDEAM datos.gov.co' (en 2025 son identicas).
- Hora: `at` es una hora en punto de Colombia (la emision). La serie esta en UTC con el FIN de la
  hora (convencion de los importadores): h1 = media de la hora que termina en `at`.
- Modelo v4: 6 horas previas completas + media de 24 h (>= 18 horas). Solo horizontes visibles del
  JSON (4 y 5 h). Prediccion recortada a un minimo de 0.
"""

import math
from datetime import date, datetime, time, timedelta, timezone

from ica import calcular_ica
from recomendaciones import recomendacion
from shared import prediccion as pr
from shared.limpieza_pm25 import limpiar

COLOMBIA = timezone(timedelta(hours=-5))
FUENTE_IDEAM = "IDEAM datos.gov.co"
FUENTE_SISAIRE = "IDEAM SISAIRE"
PRIORIDAD = {FUENTE_IDEAM: 0, FUENTE_SISAIRE: 1}
AVISO_REPLAY = ("Replay histórico con datos de estaciones de referencia del IDEAM. "
                "No es un pronóstico en tiempo real ni un equipo certificado.")
AT_POR_DEFECTO = datetime(2026, 9, 30, 12, 0, tzinfo=COLOMBIA)
MAX_DIAS_METRICAS = 366
MIN_HORAS_METRICAS = 168          # menos que esto: se avisa que hay pocas horas validas
MAX_SALTO_HORAS = 7 * 24          # buscar=siguiente: hasta 7 dias hacia adelante
LIMITE_DIARIO_PM25 = 37           # Res. 2254/2017, ug/m3 (24 h)
NO_DISPONIBLE = "no disponible en el histórico"

# Estaciones fisicas -> sus nombres en cada fuente (tal como los guardan los importadores).
# 31366 (Fernando Botero): sus filas IDEAM de 2020-2023 son incoherentes (excluida de v4); el replay
# usa solo su serie del SISAIRE (2026), que es plausible.
ESTACIONES = {
    "29509": {"nombre": "Tráfico Centro (Medellín)",
              "series": ((FUENTE_IDEAM, "TRÁFICO CENTRO (IDEAM 29509)"),
                         (FUENTE_SISAIRE, "ESTACIÓN TRÁFICO CENTRO - MEDELLÍN"))},
    "31854": {"nombre": "Fiscalía Sector Caribe (Medellín)",
              "series": ((FUENTE_IDEAM, "FISCALÍA SECTOR CARIBE (IDEAM 31854)"),
                         (FUENTE_SISAIRE, "FISCALÍA GENERAL DE LA NACIÓN - SECTOR CARIBE, MEDELLÍN"))},
    "29493": {"nombre": "Casa de Justicia (Itagüí)",
              "series": ((FUENTE_IDEAM, "CASA JUSTICIA (IDEAM 29493)"),)},
    "31366": {"nombre": "Parque Biblioteca Fernando Botero (Medellín)",
              "series": ((FUENTE_SISAIRE, "PARQUE BIBLIOTECA FERNANDO BOTERO - MEDELLÍN, SAN CRISTOBAL"),)},
    "31946": {"nombre": "Parque Biblioteca Tomás Carrasquilla (Medellín)",
              "series": ((FUENTE_SISAIRE, "PARQUE BIBLIOTECA TOMÁS CARRASQUILLA, LA QUINTANA - MEDELLÍN"),)},
}


class ReplayError(ValueError):
    """Error de datos o de parametros (la API lo convierte en 422)."""


# ---------- SQL (solo lectura de external_measurements) ----------

def _filtro_estacion(codigo):
    """WHERE con los pares (source, station) de la tabla ESTACIONES (constantes, no entrada del usuario)."""
    pares = ESTACIONES[codigo]["series"]
    cond = " OR ".join("(source = %s AND station = %s)" for _ in pares)
    return f"({cond})", [v for par in pares for v in par]


def sql_serie(codigo):
    cond, params = _filtro_estacion(codigo)
    return (f"SELECT source, station, measured_at, pm25 FROM external_measurements WHERE {cond} "
            "AND measured_at > %s AND measured_at <= %s ORDER BY measured_at", params)


def sql_rango(codigo):
    cond, params = _filtro_estacion(codigo)
    return (f"SELECT source, min(measured_at) AS primera, max(measured_at) AS ultima, count(*) AS horas "
            f"FROM external_measurements WHERE {cond} GROUP BY source ORDER BY source", params)


# ---------- Serie ----------

def hora_idx(t):
    return int(t.timestamp() // 3600)


def de_idx(h):
    return datetime.fromtimestamp(h * 3600, tz=timezone.utc)


def serie_limpia(filas):
    """filas (source, station, measured_at, pm25) -> ({hora UTC: pm25}, fuentes usadas).

    Une las fuentes (prioridad IDEAM datos.gov.co) y vuelve a pasar la limpieza (ceros y rachas).
    """
    elegidas = {}
    for f in sorted(filas, key=lambda f: PRIORIDAD.get(f["source"], 9)):
        h = hora_idx(f["measured_at"])
        if h not in elegidas:
            elegidas[h] = f
    validas, _ = limpiar([{"station": "s", "measured_at": de_idx(h), "pm25": float(f["pm25"]), "source": f["source"]}
                          for h, f in sorted(elegidas.items())])
    serie = {hora_idx(f["measured_at"]): f["pm25"] for f in validas}
    fuentes = sorted({f["source"] for f in validas})
    return serie, fuentes


def parsear_at(texto):
    """'2026-09-30T12:00' (hora de Colombia) o con zona -> datetime en hora de Colombia. Hora en punto."""
    try:
        t = datetime.fromisoformat(str(texto).strip())
    except ValueError:
        raise ReplayError(f"'at' no es una fecha-hora ISO valida: {texto!r} (use p. ej. 2026-09-30T12:00)") from None
    t = t.replace(tzinfo=COLOMBIA) if t.tzinfo is None else t.astimezone(COLOMBIA)
    if t.minute or t.second or t.microsecond:
        raise ReplayError("'at' debe ser una hora en punto (minutos y segundos en 0)")
    return t


def ventana(serie, t_idx, c):
    """Variables v4 en la hora t_idx o ReplayError con el motivo (que horas faltan)."""
    horas = [serie.get(t_idx - k) for k in range(pr.V4_HORAS_MEDIA)]
    x = pr.variables_v4_desde(horas, t_idx * 60, c)
    if x is None:
        faltan6 = [de_idx(t_idx - k).astimezone(COLOMBIA).strftime("%Y-%m-%d %H:%M")
                   for k in range(pr.V4_HORAS_LAGS) if horas[k] is None]
        validas = sum(h is not None for h in horas)
        partes = []
        if faltan6:
            partes.append("faltan horas de las 6 previas (fin de hora, Colombia): " + ", ".join(faltan6))
        if validas < pr.V4_MIN_HORAS_MEDIA:
            partes.append(f"solo {validas} de 24 horas con datos para la media de 24 h (minimo {pr.V4_MIN_HORAS_MEDIA})")
        raise ReplayError("Ventana incompleta: " + "; ".join(partes))
    return x


def horizontes_visibles(modelo):
    return [h for h in pr.horizontes_de(modelo) if h in set(modelo.get("horizontes_visibles") or [])]


def _r(v, d=1):
    return None if v is None else round(v, d)


def calcular_replay(codigo, serie, fuentes, modelo, at):
    """Respuesta de /predictions/replay. Lanza ReplayError si la ventana esta incompleta."""
    t_idx = hora_idx(at)
    x = ventana(serie, t_idx, float(modelo["c"]))
    pers = x["pm25_h1"]
    preds = []
    for h in horizontes_visibles(modelo):
        obj = t_idx + h // 60
        pred = pr.predecir_horizonte(modelo, x, h)
        obs = serie.get(obj)
        preds.append({"horizon_hours": h // 60,
                      "target_local": de_idx(obj).astimezone(COLOMBIA).strftime("%Y-%m-%dT%H:%M"),
                      "target_utc": de_idx(obj).isoformat(), "predicted_pm25": _r(pred), "persistence_pm25": _r(pers),
                      "observed_pm25": _r(obs),
                      "error_modelo": None if obs is None else _r(pred - obs, 2),
                      "error_persistencia": None if obs is None else _r(pers - obs, 2)})
    ica = calcular_ica("pm25", pers)
    actual = {"pm25_1h": _r(pers), "media_24h": _r(x["media_24h"]), "horas_24h": x["horas_24h"],
              "limite_diario_pm25": LIMITE_DIARIO_PM25,
              "ica": {"ica": ica["ica"], "category_code": ica["category_code"], "category_name": ica["category_name"],
                      "color": ica["color"], "solo_pm25": True,
                      "base": "ICA con PM2.5 solamente · media de la hora que termina en 'at' (indicativo)"},
              "recomendacion": recomendacion(ica["category_code"]),
              "no_disponibles": {"pm10": NO_DISPONIBLE, "temperature": NO_DISPONIBLE, "humidity": NO_DISPONIBLE}}
    return {"station": codigo, "nombre": ESTACIONES[codigo]["nombre"], "at_local": at.strftime("%Y-%m-%dT%H:%M"),
            "at_utc": at.astimezone(timezone.utc).isoformat(), "modelo": modelo.get("nombre"),
            "fuentes": fuentes, "aviso": AVISO_REPLAY, "aviso_modelo": modelo.get("aviso_publico"),
            "ventana": {"h1_a_h6": [_r(serie.get(t_idx - k)) for k in range(pr.V4_HORAS_LAGS)],
                        "media_6h": _r(x["media_6h"]), "media_24h": _r(x["media_24h"]), "horas_24h": x["horas_24h"]},
            "predicciones": preds, "actual": actual,
            "nota": "Valores en ug/m3. Hora objetivo = fin de la hora (Colombia). Persistencia = media de la hora que termina en 'at'."}


def calcular_replay_siguiente(codigo, serie, fuentes, modelo, at, max_horas=MAX_SALTO_HORAS):
    """Como calcular_replay, pero si la ventana de 'at' esta incompleta salta a la siguiente hora valida
    (hasta max_horas). La respuesta lleva 'salto' con la hora pedida, cuantas horas se saltaron y el motivo."""
    try:
        return calcular_replay(codigo, serie, fuentes, modelo, at)
    except ReplayError as e:
        motivo = str(e)
    for k in range(1, max_horas + 1):
        t = at + timedelta(hours=k)
        try:
            r = calcular_replay(codigo, serie, fuentes, modelo, t)
        except ReplayError:
            continue
        r["salto"] = {"pedida_local": at.strftime("%Y-%m-%dT%H:%M"), "horas": k, "motivo": motivo}
        return r
    raise ReplayError(f"No hay horas con ventana completa en las {max_horas} horas siguientes a "
                      f"{at.strftime('%Y-%m-%d %H:%M')} ({motivo})")


def rango_periodo(desde, hasta):
    """Fechas locales inclusive -> (emision_inicial, emision_final) en hora de Colombia. ReplayError si no vale."""
    if hasta < desde:
        raise ReplayError("'hasta' es anterior a 'desde'")
    if (hasta - desde).days + 1 > MAX_DIAS_METRICAS:
        raise ReplayError(f"el periodo no puede pasar de {MAX_DIAS_METRICAS} dias")
    return datetime.combine(desde, time(0), tzinfo=COLOMBIA), datetime.combine(hasta, time(23), tzinfo=COLOMBIA)


def calcular_metricas(codigo, serie, fuentes, modelo, desde, hasta):
    """MAE y RMSE del modelo y de la persistencia, emision por emision en [desde, hasta] (hora de Colombia)."""
    ini, fin = rango_periodo(desde, hasta)
    c = float(modelo["c"])
    vis = horizontes_visibles(modelo)
    err = {h: ([], []) for h in vis}
    emisiones = sin_ventana = 0
    sin_obs = {h: 0 for h in vis}
    for t_idx in range(hora_idx(ini), hora_idx(fin) + 1):
        emisiones += 1
        horas = [serie.get(t_idx - k) for k in range(pr.V4_HORAS_MEDIA)]
        x = pr.variables_v4_desde(horas, t_idx * 60, c)
        if x is None:
            sin_ventana += 1
            continue
        for h in vis:
            obs = serie.get(t_idx + h // 60)
            if obs is None:
                sin_obs[h] += 1
                continue
            err[h][0].append(pr.predecir_horizonte(modelo, x, h) - obs)
            err[h][1].append(x["pm25_h1"] - obs)
    por_h = []
    for h in vis:
        em, ep = err[h]
        n = len(em)
        fila = {"horizon_hours": h // 60, "n": n, "sin_observado": sin_obs[h]}
        if n:
            mae_m, mae_p = sum(map(abs, em)) / n, sum(map(abs, ep)) / n
            fila.update({"mae_modelo": round(mae_m, 3), "rmse_modelo": round(math.sqrt(sum(e * e for e in em) / n), 3),
                         "mae_persistencia": round(mae_p, 3), "rmse_persistencia": round(math.sqrt(sum(e * e for e in ep) / n), 3),
                         "mejora_mae_pct": round(100 * (mae_p - mae_m) / mae_p, 1) if mae_p else None})
        por_h.append(fila)
    n_min = min((f["n"] for f in por_h), default=0)
    r = {"station": codigo, "nombre": ESTACIONES[codigo]["nombre"], "desde": desde.isoformat(), "hasta": hasta.isoformat(),
         "modelo": modelo.get("nombre"), "fuentes": fuentes, "emisiones": emisiones, "sin_ventana": sin_ventana,
         "por_horizonte": por_h, "aviso": AVISO_REPLAY,
         "nota": "Calculado al vuelo con los datos reales del periodo; no se guarda ni se cachea."}
    if n_min == 0:
        r["estado"] = "sin_datos"
        r["mensaje"] = "No hay horas válidas (ventana completa y valor observado) en el periodo."
    elif n_min < MIN_HORAS_METRICAS:
        r["estado"] = "pocas_horas"
        r["mensaje"] = (f"Pocas horas válidas: {n_min} (menos de {MIN_HORAS_METRICAS}, una semana); "
                        "las cifras son poco representativas.")
    else:
        r["estado"] = "ok"
    return r


def ultima_valida(serie, modelo, preferida=AT_POR_DEFECTO):
    """Hora (Colombia) por defecto del selector: 'preferida' si se puede calcular con observado a todos
    los horizontes visibles; si no, la ultima hora de la serie que si lo permita. None si ninguna."""
    c = float(modelo["c"])
    vis = horizontes_visibles(modelo)

    def ok(t_idx):
        horas = [serie.get(t_idx - k) for k in range(pr.V4_HORAS_MEDIA)]
        return (pr.variables_v4_desde(horas, t_idx * 60, c) is not None
                and all(serie.get(t_idx + h // 60) is not None for h in vis))
    p = hora_idx(preferida)
    if ok(p):
        return preferida
    for t_idx in sorted(serie, reverse=True):
        if ok(t_idx):
            return de_idx(t_idx).astimezone(COLOMBIA)
    return None
