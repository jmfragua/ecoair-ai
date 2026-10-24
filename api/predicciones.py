"""Logica pura de /devices/{id}/predictions/latest y /predictions/metrics (sin base de datos).

Las predicciones las escribe el alerter (alerter/prediccion_horaria.py) en la tabla predictions
(migracion 008) y las metricas diarias en prediction_metrics. Aqui solo se decide que se muestra.

Visibilidad de un horizonte (panel y respuesta por defecto):
1. Debe estar en "horizontes_visibles" del JSON del modelo (v4: [240, 300] -> 4 y 5 h; 3 h oculto).
2. Regla en vivo: si en los ultimos 30 dias hay >= N_MIN_VIVO (168, una semana de emisiones horarias)
   predicciones evaluadas de esa version y horizonte, y el MAE del modelo (ponderado por n) NO es menor
   que el de la persistencia, el horizonte se oculta ("dejo de superar a la persistencia en vivo").
Con ?incluir_ocultos=true se devuelven todos, con visible=false y el motivo.
"""

import math
from datetime import timedelta

N_MIN_VIVO = 168
HORAS_DESACTUALIZADA = 2
DIAS_REGLA_VIVO = 30

SQL_ULTIMAS = (
    "SELECT created_at, target_time, horizon_hours, predicted_pm25, baseline_pm25, model_version, last_reading_at "
    "FROM predictions WHERE device_id = %(device_id)s AND NOT is_simulated AND created_at = ("
    "  SELECT max(created_at) FROM predictions WHERE device_id = %(device_id)s AND NOT is_simulated) "
    "ORDER BY horizon_hours"
)
SQL_METRICAS = (
    "SELECT fecha, horizon_hours, model_version, n, n_sin_observacion, mae_model, rmse_model, mae_baseline, "
    "rmse_baseline FROM prediction_metrics WHERE device_id = %(device_id)s AND NOT is_simulated "
    "AND fecha > (now() AT TIME ZONE 'America/Bogota')::date - %(dias)s::int "
    "AND (%(horizon)s::int IS NULL OR horizon_hours = %(horizon)s::int) ORDER BY fecha DESC, horizon_hours"
)


def _f(v):
    return None if v is None else float(v)


def agregar(filas):
    """Agrega metricas diarias por (horizonte, version): MAE ponderado por n, RMSE = sqrt(sum rmse^2 n / sum n)."""
    grupos = {}
    for f in filas:
        g = grupos.setdefault((int(f["horizon_hours"]), f["model_version"]),
                              {"n": 0, "n_sin_observacion": 0, "sm": 0.0, "sb": 0.0, "qm": 0.0, "qb": 0.0, "dias": 0})
        n = int(f["n"])
        g["n"] += n
        g["n_sin_observacion"] += int(f["n_sin_observacion"])
        g["dias"] += 1
        g["sm"] += float(f["mae_model"]) * n
        g["sb"] += float(f["mae_baseline"]) * n
        g["qm"] += float(f["rmse_model"]) ** 2 * n
        g["qb"] += float(f["rmse_baseline"]) ** 2 * n
    out = []
    for (h, version), g in sorted(grupos.items()):
        n = g["n"]
        mae_m, mae_b = g["sm"] / n, g["sb"] / n
        out.append({"horizon_hours": h, "model_version": version, "dias": g["dias"], "n": n,
                    "n_sin_observacion": g["n_sin_observacion"], "mae_model": round(mae_m, 3),
                    "rmse_model": round(math.sqrt(g["qm"] / n), 3), "mae_baseline": round(mae_b, 3),
                    "rmse_baseline": round(math.sqrt(g["qb"] / n), 3),
                    "mejora_mae_pct": round(100 * (mae_b - mae_m) / mae_b, 1) if mae_b else None})
    return out


def ocultos_en_vivo(metricas_30d, version, n_min=N_MIN_VIVO):
    """{horizonte: motivo} para los horizontes que dejaron de superar a la persistencia en vivo."""
    out = {}
    for a in agregar([f for f in metricas_30d if f["model_version"] == version]):
        if a["n"] >= n_min and a["mae_model"] >= a["mae_baseline"]:
            out[a["horizon_hours"]] = (f"en vivo no supera a la persistencia: MAE {a['mae_model']} frente a "
                                       f"{a['mae_baseline']} con {a['n']} predicciones evaluadas ({DIAS_REGLA_VIVO} dias)")
    return out


def construir_latest(device_id, filas, modelo, metricas_30d, ahora, incluir_ocultos=False):
    base = {"device_id": device_id, "experimental": True,
            "aviso": (modelo or {}).get("aviso_publico"), "fuentes": (modelo or {}).get("texto_fuentes"),
            "regla_en_vivo": f"se oculta un horizonte si con >= {N_MIN_VIVO} predicciones evaluadas en "
                             f"{DIAS_REGLA_VIVO} dias no supera a la persistencia"}
    if not filas:
        return {**base, "estado": "sin_predicciones", "created_at": None, "model_version": None,
                "minutos_desde_emision": None, "predicciones": [],
                "mensaje": "Todavía no hay predicciones para esta estación."}
    created_at = filas[0]["created_at"]
    version = filas[0]["model_version"]
    visibles_modelo = set((modelo or {}).get("horizontes_visibles") or [])
    vivo = ocultos_en_vivo(metricas_30d, version)
    preds = []
    for f in filas:
        h = int(f["horizon_hours"])
        if modelo is None:
            motivo = "modelo no disponible en la API: no se puede confirmar su visibilidad"
        elif h * 60 not in visibles_modelo:
            motivo = "oculto por el modelo: no cumplió la regla de visibilidad en la evaluación"
        else:
            motivo = vivo.get(h)
        item = {"horizon_hours": h, "target_time": f["target_time"].isoformat(), "predicted_pm25": _f(f["predicted_pm25"]),
                "baseline_pm25": _f(f["baseline_pm25"]), "visible": motivo is None}
        if motivo:
            item["motivo_oculto"] = motivo
        if motivo is None or incluir_ocultos:
            preds.append(item)
    minutos = int((ahora - created_at).total_seconds() // 60)
    estado = "desactualizada" if ahora - created_at > timedelta(hours=HORAS_DESACTUALIZADA) else "ok"
    r = {**base, "estado": estado, "created_at": created_at.isoformat(), "model_version": version,
         "minutos_desde_emision": minutos, "predicciones": preds}
    if not preds:
        r["mensaje"] = "Hay predicciones, pero ningún horizonte está visible."
    return r


def construir_metricas(device_id, filas, dias, horizon, hoy):
    if not filas:
        return {"device_id": device_id, "estado": "sin_metricas", "dias": dias, "horizon": horizon,
                "desde": (hoy - timedelta(days=dias - 1)).isoformat(), "hasta": hoy.isoformat(),
                "por_horizonte": [], "diario": [],
                "mensaje": "Aún no hay predicciones evaluadas: cada una necesita que llegue su hora objetivo con datos."}
    diario = [{"fecha": f["fecha"].isoformat(), "horizon_hours": int(f["horizon_hours"]), "model_version": f["model_version"],
               "n": int(f["n"]), "n_sin_observacion": int(f["n_sin_observacion"]), "mae_model": _f(f["mae_model"]),
               "rmse_model": _f(f["rmse_model"]), "mae_baseline": _f(f["mae_baseline"]),
               "rmse_baseline": _f(f["rmse_baseline"])} for f in filas]
    return {"device_id": device_id, "estado": "ok", "dias": dias, "horizon": horizon,
            "desde": (hoy - timedelta(days=dias - 1)).isoformat(), "hasta": hoy.isoformat(),
            "por_horizonte": agregar(filas), "diario": diario,
            "nota": "Medido contra el sensor propio (media de la hora objetivo con >= 40 min con datos). Experimental."}
