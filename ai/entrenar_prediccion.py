"""Entrena y evalua la prediccion EXPERIMENTAL de PM2.5 a 1 y 3 h. Escribe el JSON del modelo.

Fuentes (etiquetadas; los metadatos guardan cuales se usaron):
  simulados  por defecto desde CSV: entrenamiento ai/data/sim_measurements.csv (semilla 42) y
             evaluacion ai/data/sim_test_measurements.csv (semilla 43): conjuntos separados.
  reales     measurements con is_simulated = false (base de datos; DATABASE_URL o DB_*)
  externos   external_measurements (base de datos)
  Para fuentes de la base, cada serie se parte por tiempo: 80 % entrenamiento, 20 % evaluacion,
  con un hueco de 3 h entre ambos para que ninguna ventana mezcle los dos tramos.

Modelo: Ridge sobre el residuo de la persistencia (ver shared/prediccion.py). La regularizacion
(alpha) se elige con una validacion tomada del ULTIMO 20 % del entrenamiento, nunca con la
evaluacion. Se reporta SIEMPRE el error de la persistencia en la misma evaluacion; si el modelo
no la supera en un horizonte, ese horizonte queda marcado supera_persistencia = false y no se
muestra en el panel ni en el correo.

  python ai/entrenar_prediccion.py                       # simulados (CSV), salida en modelos/
  python ai/entrenar_prediccion.py --fuentes reales,externos --desde-db
"""

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shared.prediccion import HORIZONTES, VARIABLES, objetivo_en, serie_por_minuto, variables_en  # noqa: E402

PASO_MIN = 5                     # una muestra cada 5 min (menos autocorrelacion, mas rapido)
ALPHAS = [0.1, 1.0, 10.0, 100.0, 1000.0]
HUECO_MIN = 180
NOMBRE = "prediccion_pm25_v1"
SALIDA = RAIZ / "modelos" / f"{NOMBRE}.json"


# ---------- Datos ----------

def leer_csv_sim(ruta):
    """{device_id: serie} desde un CSV del generador (solo filas is_simulated)."""
    filas = {}
    with open(ruta, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if str(r.get("is_simulated", "True")).lower() not in ("true", "1", "t"):
                raise ValueError(f"{ruta} contiene filas no simuladas")
            t = datetime.fromisoformat(r["received_at"])
            filas.setdefault(r["device_id"], []).append((t, float(r["pm25"]) if r["pm25"] else None))
    return {d: serie_por_minuto(v) for d, v in filas.items()}


def leer_db(fuente, conn):
    """{nombre_serie: serie} de la base para 'reales', 'simulados' o 'externos'."""
    sql = {
        "reales": "SELECT device_id, received_at, pm25 FROM measurements WHERE NOT is_simulated ORDER BY 2",
        "simulados": "SELECT device_id, received_at, pm25 FROM measurements WHERE is_simulated ORDER BY 2",
        "externos": "SELECT source || ' / ' || station, measured_at, pm25 FROM external_measurements ORDER BY 2",
    }[fuente]
    filas = {}
    for nombre, t, v in conn.execute(sql).fetchall():
        filas.setdefault(nombre, []).append((t, v))
    return {n: serie_por_minuto(v) for n, v in filas.items()}


def partir_por_tiempo(serie, fraccion=0.8, hueco=HUECO_MIN):
    """(minutos de inicio de entrenamiento, fin entrenamiento, inicio evaluacion)."""
    ms = sorted(serie)
    corte = ms[0] + int((ms[-1] - ms[0]) * fraccion)
    return corte, corte + hueco


def muestras(serie, desde=None, hasta=None):
    """Matriz X, objetivos Y (dict por horizonte), persistencia P. Solo m con m+h <= hasta."""
    if not serie:
        return np.empty((0, len(VARIABLES))), {h: np.empty(0) for h in HORIZONTES}, np.empty(0)
    ms = sorted(serie)
    ini = ms[0] if desde is None else max(desde, ms[0])
    fin = ms[-1] if hasta is None else min(hasta, ms[-1])
    X, Y, P = [], {h: [] for h in HORIZONTES}, []
    for m in range(ini - ini % PASO_MIN, fin + 1, PASO_MIN):
        if m + max(HORIZONTES) > fin:
            break
        x = variables_en(serie, m)
        if x is None:
            continue
        objs = {h: objetivo_en(serie, m, h) for h in HORIZONTES}
        if any(v is None for v in objs.values()):
            continue
        X.append([x[v] for v in VARIABLES])
        P.append(x["ult"])
        for h in HORIZONTES:
            Y[h].append(objs[h])
    return np.array(X), {h: np.array(v) for h, v in Y.items()}, np.array(P)


def juntar(partes):
    X = np.vstack([p[0] for p in partes if len(p[0])]) if any(len(p[0]) for p in partes) else np.empty((0, len(VARIABLES)))
    Y = {h: np.concatenate([p[1][h] for p in partes]) for h in HORIZONTES}
    P = np.concatenate([p[2] for p in partes])
    return X, Y, P


# ---------- Modelo ----------

def ajustar_ridge(Z, y, alpha):
    """Ridge con intercepto sin penalizar. Z ya estandarizada."""
    n, k = Z.shape
    A = np.hstack([np.ones((n, 1)), Z])
    reg = alpha * np.eye(k + 1)
    reg[0, 0] = 0.0
    w = np.linalg.solve(A.T @ A + reg, A.T @ y)
    return float(w[0]), w[1:]


def errores(y, pred):
    e = pred - y
    return float(np.mean(np.abs(e))), float(math.sqrt(np.mean(e ** 2)))


def entrenar(Xtr, Ytr, Ptr, Xev, Yev, Pev):
    """Devuelve (media, escala, horizontes) con metricas en la evaluacion."""
    media, escala = Xtr.mean(axis=0), Xtr.std(axis=0)
    escala[escala == 0] = 1.0
    Ztr, Zev = (Xtr - media) / escala, (Xev - media) / escala
    corte = int(len(Ztr) * 0.8)   # validacion = ultimo 20 % del entrenamiento (orden temporal)
    horizontes = {}
    for h in HORIZONTES:
        rtr = Ytr[h] - Ptr
        mejor = min(ALPHAS, key=lambda a: errores(
            rtr[corte:], (lambda b, c: b + Ztr[corte:] @ c)(*ajustar_ridge(Ztr[:corte], rtr[:corte], a)))[0])
        b, c = ajustar_ridge(Ztr, rtr, mejor)
        pred = np.maximum(0.0, Pev + b + Zev @ c)
        mae_m, rmse_m = errores(Yev[h], pred)
        mae_p, rmse_p = errores(Yev[h], Pev)
        horizontes[str(h)] = {
            "intercepto": b, "coef": [float(v) for v in c], "alpha": mejor,
            "metricas": {"n_evaluacion": int(len(Yev[h])), "mae_modelo": round(mae_m, 3),
                         "rmse_modelo": round(rmse_m, 3), "mae_persistencia": round(mae_p, 3),
                         "rmse_persistencia": round(rmse_p, 3),
                         "mejora_mae_pct": round(100 * (mae_p - mae_m) / mae_p, 1) if mae_p else None},
            "supera_persistencia": bool(mae_m < mae_p),
        }
    return media, escala, horizontes


# ---------- CLI ----------

def main(argv=None):
    p = argparse.ArgumentParser(description="Prediccion experimental de PM2.5 (1 y 3 h)")
    p.add_argument("--fuentes", default="simulados", help="lista: simulados,reales,externos")
    p.add_argument("--desde-db", action="store_true", help="leer las fuentes de la base (DATABASE_URL o DB_*)")
    p.add_argument("--csv-sim-entrenamiento", default=str(RAIZ / "ai/data/sim_measurements.csv"))
    p.add_argument("--csv-sim-evaluacion", default=str(RAIZ / "ai/data/sim_test_measurements.csv"))
    p.add_argument("--salida", default=str(SALIDA))
    args = p.parse_args(argv)
    fuentes = [f.strip() for f in args.fuentes.split(",") if f.strip()]
    if not set(fuentes) <= {"simulados", "reales", "externos"}:
        print("ERROR: fuentes validas: simulados, reales, externos")
        return 2

    tr, ev, meta_fuentes = [], [], []
    conn = None
    if args.desde_db or set(fuentes) - {"simulados"}:
        import psycopg
        from shared.settings import descripcion_db, dsn_desde_entorno
        conn = psycopg.connect(dsn_desde_entorno(connect_timeout=10))
        conn.read_only = True
        print(f"Base de datos: {descripcion_db()} (solo lectura)")
    for fuente in fuentes:
        if fuente == "simulados" and not args.desde_db:
            s_tr, s_ev = leer_csv_sim(args.csv_sim_entrenamiento), leer_csv_sim(args.csv_sim_evaluacion)
            a, b = [muestras(s) for s in s_tr.values()], [muestras(s) for s in s_ev.values()]
            detalle = (f"entrenamiento {Path(args.csv_sim_entrenamiento).name} (semilla 42), "
                       f"evaluacion {Path(args.csv_sim_evaluacion).name} (semilla 43)")
            minutos = sum(len(s) for s in s_tr.values()) + sum(len(s) for s in s_ev.values())
        else:
            series = leer_db(fuente, conn)
            a, b = [], []
            for s in series.values():
                if len(s) < 600:
                    continue   # menos de 10 h de datos: no alcanza para ventanas de 3 h + horizonte
                fin_tr, ini_ev = partir_por_tiempo(s)
                a.append(muestras(s, hasta=fin_tr))
                b.append(muestras(s, desde=ini_ev))
            detalle = f"base de datos, {len(series)} series, partidas por tiempo 80/20 con hueco de 3 h"
            minutos = sum(len(s) for s in series.values())
        n_tr, n_ev = sum(len(x[2]) for x in a), sum(len(x[2]) for x in b)
        print(f"Fuente {fuente}: {minutos} minutos con datos -> {n_tr} muestras de entrenamiento, {n_ev} de evaluacion")
        meta_fuentes.append({"tipo": fuente, "detalle": detalle, "minutos_con_datos": minutos,
                             "muestras_entrenamiento": n_tr, "muestras_evaluacion": n_ev})
        tr += a
        ev += b
    if conn is not None:
        conn.rollback()
        conn.close()

    Xtr, Ytr, Ptr = juntar(tr)
    Xev, Yev, Pev = juntar(ev)
    if len(Xtr) < 200 or len(Xev) < 50:
        print(f"ERROR: datos insuficientes ({len(Xtr)} muestras de entrenamiento, {len(Xev)} de evaluacion). "
              "No se escribe ningun modelo.")
        return 2

    media, escala, horizontes = entrenar(Xtr, Ytr, Ptr, Xev, Yev, Pev)
    simulados = any(f["tipo"] == "simulados" for f in meta_fuentes)
    modelo = {
        "nombre": NOMBRE,
        "tipo": "ridge_sobre_residuo_de_persistencia",
        "experimental": True,
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "advertencia": ("Prediccion experimental. Entrenado con datos simulados: el error medido no representa "
                        "la precision con el sensor real.") if simulados else "Prediccion experimental.",
        "variables": VARIABLES,
        "media": [float(v) for v in media],
        "escala": [float(v) for v in escala],
        "horizontes": horizontes,
        "linea_base": "persistencia: media de los ultimos 5 min",
        "objetivo": "media de PM2.5 de 5 min en el horizonte",
        "fuentes": meta_fuentes,
        "evaluacion": {"nota": ("Conjunto separado del entrenamiento; alpha elegido con validacion dentro del "
                                "entrenamiento. Un horizonte solo se muestra si supera a la persistencia en MAE.")},
    }
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'DATOS SIMULADOS: prueba de software, no evidencia de precision real' if simulados else ''}")
    print(f"Muestras: {len(Xtr)} entrenamiento, {len(Xev)} evaluacion (una cada {PASO_MIN} min)")
    print(f"{'horizonte':<10}{'MAE modelo':>12}{'MAE persist.':>14}{'RMSE modelo':>13}{'RMSE persist.':>15}{'mejora MAE':>12}  supera")
    for h in HORIZONTES:
        m = horizontes[str(h)]["metricas"]
        print(f"{str(h // 60) + ' h':<10}{m['mae_modelo']:>12.3f}{m['mae_persistencia']:>14.3f}"
              f"{m['rmse_modelo']:>13.3f}{m['rmse_persistencia']:>15.3f}{str(m['mejora_mae_pct']) + ' %':>12}  "
              f"{'si' if horizontes[str(h)]['supera_persistencia'] else 'NO'} (alpha {horizontes[str(h)]['alpha']})")
    print(f"\nModelo escrito en {Path(args.salida).relative_to(RAIZ) if Path(args.salida).is_relative_to(RAIZ) else args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
