"""Evaluacion con conjunto de prueba separado, sobre datos SIMULADOS.

DATOS SIMULADOS con eventos disenados por nosotros: no demuestran precision real.

- Entrenamiento: semilla 42 (ai/data/sim_measurements.csv). Las etiquetas de entrenamiento
  no se usan. El contexto por hora sale solo del entrenamiento.
- Prueba: semilla 43, mismo --end (ai/data/sim_test_measurements.csv y sim_test_events.csv).
  Toda metrica se calcula SOLO sobre la prueba.
- Dos variantes de Isolation Forest, declaradas antes de ver resultados (VARIANTES).
- Filas "normales": a mas de CERCA_MIN minutos de cualquier evento (huecos incluidos).
  Una fila marcada a mas de CERCA_MIN minutos de cualquier evento es falsa alarma.
- AUC por tipo: ROC-AUC del score separando las filas de ese tipo de las filas normales.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

from entrenar_anomalias import (
    DEFAULT_CONTAMINATION,
    N_ESTIMATORS,
    RULE_PM25,
    SEED,
    cargar_eventos,
    cargar_mediciones,
    entrenar,
    intervalos,
)
from features import MODEL_FEATURES, construir_features, promediar_por_minuto
from reglas_sensor import CAIDO_MIN, CONGELADO_MIN, SALTO_TEMP_C, evaluar_reglas
from scoring import puntuar_features

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
MODELS_DIR = BASE / "models"
UN_MIN = pd.Timedelta(minutes=1)
CERCA_MIN = 15

ADVERTENCIA = ("DATOS SIMULADOS con eventos diseñados por nosotros: prueba de software, "
               "no demuestran precisión real")
EVENT_TYPES = [
    "pico_corto", "episodio_largo", "anomalia_contextual",
    "sensor_congelado", "temperatura_imposible", "hueco_de_datos",
]
ROW_EVENTS = [t for t in EVENT_TYPES if t != "hueco_de_datos"]

NIVEL_PM = ["pm25", "pm10", "pm25_media_15"]
VARIANTES = {
    "A": list(MODEL_FEATURES),
    "B": [f for f in MODEL_FEATURES if f not in NIVEL_PM],
}
DEFINICION_VARIANTES = {
    "A": "todas las features de la v2 (17).",
    "B": ("A menos exactamente tres features de nivel absoluto de PM: pm25, pm10 y pm25_media_15 "
          "(14 features). Se mantienen temperature, humidity, pm25_cambio, pm25_vs_hora, "
          "humidity_vs_hora, temperature_vs_hora, pm25_std_15, las 4 *_std_10, ratio_pm10_pm25, "
          "hora_sin y hora_cos."),
}
CAPAS = {
    "a": f"Regla PM2.5 > {RULE_PM25}",
    "b": "Reglas de sensor",
    "A": "Isolation Forest A",
    "B": "Isolation Forest B",
}


def cerca_de_eventos(ts, events, minutos=CERCA_MIN):
    """True si el minuto ts esta a <= `minutos` de algun evento (end_utc exclusivo)."""
    ts = pd.Series(pd.to_datetime(ts, utc=True)).reset_index(drop=True)
    margen = pd.Timedelta(minutes=minutos)
    cerca = np.zeros(len(ts), dtype=bool)
    for e in events.itertuples():
        cerca |= ((ts >= e.start_utc - margen) & (ts < e.end_utc + margen)).to_numpy()
    return cerca


def tipo_por_fila(ts, events):
    ts = pd.Series(pd.to_datetime(ts, utc=True)).reset_index(drop=True)
    tipos = np.full(len(ts), "", dtype=object)
    for e in events.itertuples():
        tipos[((ts >= e.start_utc) & (ts < e.end_utc)).to_numpy()] = e.event_type
    return tipos


def auc_por_tipo(score, tipos, cerca):
    """ROC-AUC del score: filas de cada tipo vs. filas normales (a mas de CERCA_MIN de todo evento)."""
    score = np.asarray(score)
    normal = (np.asarray(tipos) == "") & ~np.asarray(cerca)
    out = {}
    for t in ROW_EVENTS + ["todos"]:
        pos = (np.asarray(tipos) != "") if t == "todos" else (np.asarray(tipos) == t)
        if pos.sum() == 0 or normal.sum() == 0:
            out[t] = None
            continue
        y = np.r_[np.ones(pos.sum()), np.zeros(normal.sum())]
        out[t] = float(roc_auc_score(y, np.r_[score[pos], score[normal]]))
    return out


def minutos_de(detecciones):
    """Expande intervalos [inicio, fin) a minutos."""
    if detecciones.empty:
        return pd.Series([], dtype="datetime64[ns, UTC]")
    partes = [pd.Series(pd.date_range(r.inicio, r.fin, freq="min", inclusive="left"))
              for r in detecciones.itertuples()]
    return pd.concat(partes).drop_duplicates().reset_index(drop=True)


def falsas_alarmas_por_fila(minutos_marcados, events, dias):
    lejos = ~cerca_de_eventos(minutos_marcados, events)
    return {
        "minutos_marcados": int(len(minutos_marcados)),
        "falsas_alarmas_filas": int(lejos.sum()),
        "falsas_alarmas_filas_por_dia": float(lejos.sum() / dias),
    }


def deteccion_por_evento(detecciones, events):
    filas = []
    for e in events.itertuples():
        fila = {"event_type": e.event_type, "start_utc": e.start_utc,
                "minutos": int((e.end_utc - e.start_utc) / UN_MIN)}
        for capa in CAPAS:
            d = detecciones[detecciones.capa == capa]
            fila[capa] = bool(((d.inicio < e.end_utc) & (d.fin > e.start_utc)).any())
        filas.append(fila)
    return pd.DataFrame(filas)


def _fmt_auc(v):
    return "n/a" if v is None else f"{v:.3f}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluacion con conjunto de prueba (DATOS SIMULADOS)")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    args = parser.parse_args(argv)
    data_dir, models_dir = Path(args.data_dir), Path(args.models_dir)

    train_path = data_dir / "sim_measurements.csv"
    test_path = data_dir / "sim_test_measurements.csv"
    test_events_path = data_dir / "sim_test_events.csv"
    train = cargar_mediciones(train_path)
    test = cargar_mediciones(test_path)
    events = cargar_eventos(test_events_path).sort_values("start_utc").reset_index(drop=True)
    for nombre, d in (("entrenamiento", train), ("prueba", test)):
        if not d["is_simulated"].all() or set(d["device_id"]) != {"ecoair-sim-01"}:
            raise SystemExit(f"El CSV de {nombre} contiene filas no simuladas.")

    # Entrenamiento (semilla 42), sin etiquetas
    feats_train, info_train = construir_features(train)
    modelos = {v: entrenar(feats_train, contamination=DEFAULT_CONTAMINATION, seed=SEED, features=f)
               for v, f in VARIANTES.items()}

    # Prueba (semilla 43)
    test_min = promediar_por_minuto(test)
    dias = (test_min["timestamp"].max() + UN_MIN - test_min["timestamp"].min()) / pd.Timedelta(days=1)
    feats_test, info_test = construir_features(test)
    tipos = tipo_por_fila(feats_test["timestamp"], events)
    cerca = cerca_de_eventos(feats_test["timestamp"], events)

    det = [intervalos(test_min.loc[test_min.pm25 > RULE_PM25, "timestamp"], "a", f"pm25_mayor_{RULE_PM25}")]
    det[0]["mensaje"] = f"PM2.5 por encima de {RULE_PM25} µg/m³ (promedio de 1 min)."
    det.append(evaluar_reglas(test_min).assign(capa="b"))
    scored, auc, prauc = {}, {}, {}
    for v, modelo in modelos.items():
        s = puntuar_features(feats_test, modelo)
        scored[v] = s
        auc[v] = auc_por_tipo(s["score"], tipos, cerca)
        normal = (tipos == "") & ~cerca
        pos = tipos != ""
        prauc[v] = float(average_precision_score(
            np.r_[np.ones(pos.sum()), np.zeros(normal.sum())],
            np.r_[s["score"].to_numpy()[pos], s["score"].to_numpy()[normal]],
        ))
        marc = s[s["es_anomalia"]]
        det.append(intervalos(marc["timestamp"], v, f"isolation_forest_{v}", marc["mensaje"]))
    detecciones = pd.concat(det, ignore_index=True)
    por_evento = deteccion_por_evento(detecciones, events)

    falsas = {}
    for capa in CAPAS:
        if capa in modelos:
            minutos = scored[capa].loc[scored[capa]["es_anomalia"], "timestamp"].reset_index(drop=True)
        else:
            minutos = minutos_de(detecciones[detecciones.capa == capa])
        falsas[capa] = falsas_alarmas_por_fila(minutos, events, dias)

    normal = (tipos == "") & ~cerca
    prevalencia = float((tipos != "").sum() / ((tipos != "").sum() + normal.sum()))

    # ---- Guardar ----
    models_dir.mkdir(parents=True, exist_ok=True)
    rutas = []
    for v, modelo in modelos.items():
        name = f"sim_isolation_forest_v2_{v}"
        joblib.dump(modelo, models_dir / f"{name}.joblib")
        sub = por_evento
        meta = {
            "modelo": name,
            "advertencia": ADVERTENCIA,
            "variante": v,
            "definicion_variante": DEFINICION_VARIANTES[v],
            "features": modelo["features"],
            "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entrenamiento": {"datos": str(train_path.relative_to(BASE.parent)), "semilla_datos": 42,
                              "etiquetas_usadas": False, **info_train},
            "prueba": {"datos": str(test_path.relative_to(BASE.parent)),
                       "eventos": str(test_events_path.relative_to(BASE.parent)),
                       "semilla_datos": 43, **info_test},
            "parametros": {"algoritmo": "IsolationForest", "n_estimators": N_ESTIMATORS,
                           "contamination": DEFAULT_CONTAMINATION, "random_state": SEED,
                           "escalado": "RobustScaler"},
            "librerias": {"scikit-learn": sklearn.__version__},
            "evaluacion_prueba": {
                "nota": ("Métricas solo sobre la semilla 43. Filas normales: a más de "
                         f"{CERCA_MIN} min de cualquier evento."),
                "auc_por_tipo": auc[v],
                "pr_auc_todos": prauc[v],
                "prevalencia": prevalencia,
                "deteccion_por_tipo": {t: f"{int(sub[sub.event_type == t][v].sum())}/{int((sub.event_type == t).sum())}"
                                       for t in EVENT_TYPES},
                **falsas[v],
            },
        }
        (models_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
        rutas += [models_dir / f"{name}.joblib", models_dir / f"{name}.json"]
    capas_path = data_dir / "sim_test_detecciones_por_capa.csv"
    detecciones.assign(capa=detecciones.capa.map(CAPAS), is_simulated=True).to_csv(capas_path, index=False)
    rutas.append(capas_path)

    # ---- Reporte ----
    w = 92
    print("=" * w)
    print(ADVERTENCIA)
    print("=" * w)
    print("Definición de variantes (declaradas antes de ver resultados):")
    for v, d in DEFINICION_VARIANTES.items():
        print(f"  Variante {v}: {d}")
        print(f"    features: {', '.join(VARIANTES[v])}")
    print(f"Isolation Forest: n_estimators={N_ESTIMATORS}, contamination={DEFAULT_CONTAMINATION}, "
          f"random_state={SEED}, RobustScaler. Entrenado SIN etiquetas ni salidas de reglas.")
    print(f"Capa (a): PM2.5 > {RULE_PM25} µg/m³ sobre promedios de 1 min (no 24 h). "
          f"Capa (b): congelado >= {CONGELADO_MIN} min, salto > {SALTO_TEMP_C} °C/min, "
          f"fuera de rango, sin datos > {CAIDO_MIN} min.")
    print(f"\nEntrenamiento: semilla 42, {train_path.name}, {info_train['filas_features']} filas con features "
          "(etiquetas no usadas; contexto por hora calculado aquí).")
    print(f"Prueba:        semilla 43, {test_path.name}, {test['timestamp'].min()} -> {test['timestamp'].max()} "
          f"({dias:.1f} días)")
    print(f"  filas de entrada {info_test['filas_entrada']}, minutos {info_test['minutos']}, segmentos "
          f"{info_test['segmentos']}, descartadas {info_test['descartadas']}, con features {info_test['filas_features']}")
    print(f"  filas en eventos {int((tipos != '').sum())}, filas normales (a más de {CERCA_MIN} min de "
          f"todo evento) {int(normal.sum())}, prevalencia {prevalencia:.4f}")
    print("  eventos: " + ", ".join(f"{t} {int((events.event_type == t).sum())}" for t in EVENT_TYPES))

    print("\n--- ROC-AUC por tipo de evento (sin umbral; filas del tipo vs. filas normales) ---")
    print(f"  {'tipo':<23}{'filas':>7}{'IF A':>9}{'IF B':>9}")
    for t in ROW_EVENTS + ["todos"]:
        n = int((tipos != "").sum()) if t == "todos" else int((tipos == t).sum())
        print(f"  {t:<23}{n:>7}{_fmt_auc(auc['A'][t]):>9}{_fmt_auc(auc['B'][t]):>9}")
    print(f"  {'hueco_de_datos':<23}{0:>7}{'n/a':>9}{'n/a':>9}   (sin filas)")
    print(f"  PR-AUC (todos los eventos vs. normales): A {prauc['A']:.3f}   B {prauc['B']:.3f}   "
          f"(azar = prevalencia {prevalencia:.4f})")
    print("  Las capas (a) y (b) son binarias: no tienen AUC.")

    print(f"\n--- Detección por evento (umbral: contamination {DEFAULT_CONTAMINATION}) — detectados / total ---")
    print(f"  {'tipo':<23}{'(a) PM>37':>11}{'(b) sensor':>12}{'IF A':>8}{'IF B':>8}{'alguna':>9}")
    for t in EVENT_TYPES + ["TOTAL"]:
        sub = por_evento if t == "TOTAL" else por_evento[por_evento.event_type == t]
        n = len(sub)
        alguna = int((sub.a | sub.b | sub.A | sub.B).sum())
        cols = [f"{int(sub[c].sum())}/{n}" for c in ("a", "b", "A", "B")]
        print(f"  {t:<23}{cols[0]:>11}{cols[1]:>12}{cols[2]:>8}{cols[3]:>8}{f'{alguna}/{n}':>9}")

    print(f"\n--- Falsas alarmas por fila (minuto marcado a más de {CERCA_MIN} min de cualquier evento) ---")
    print(f"  {'capa':<24}{'min. marcados':>14}{'falsas':>8}{'por día':>9}")
    for capa, nombre in CAPAS.items():
        f = falsas[capa]
        print(f"  {nombre:<24}{f['minutos_marcados']:>14}{f['falsas_alarmas_filas']:>8}"
              f"{f['falsas_alarmas_filas_por_dia']:>9.2f}")

    print("\n--- Evento por evento ---")
    print(f"  {'tipo':<23}{'inicio UTC':<27}{'min':>5}{'(a)':>6}{'(b)':>6}{'A':>5}{'B':>5}")
    si = {True: "sí", False: "no"}
    for r in por_evento.itertuples():
        print(f"  {r.event_type:<23}{str(r.start_utc):<27}{r.minutos:>5}{si[r.a]:>6}{si[r.b]:>6}"
              f"{si[r.A]:>5}{si[r.B]:>5}")

    print("\n--- Ejemplos de mensajes del Isolation Forest (primer intervalo por tipo de evento) ---")
    for v in modelos:
        d = detecciones[detecciones.capa == v]
        vistos = set()
        for r in d.itertuples():
            ev = events[(events.start_utc < r.fin) & (events.end_utc > r.inicio)]
            etiqueta = ev.event_type.iloc[0] if len(ev) else "sin evento"
            if etiqueta in vistos:
                continue
            vistos.add(etiqueta)
            print(f"  (IF {v}) [{etiqueta}] {r.inicio}: {r.mensaje}")

    print("\nArchivos guardados:")
    for p in rutas:
        print(f"  {p.relative_to(BASE.parent)}")
    print("=" * w)
    print(ADVERTENCIA)
    print("=" * w)


if __name__ == "__main__":
    main()
