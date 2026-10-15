"""Entrena y evalua la deteccion de anomalias por capas con el dataset SIMULADO de ecoair-sim-01.

DATOS SIMULADOS: prueba de software, no evidencia de precision real.

Tres capas, evaluadas por separado:
  (a) regla fija PM2.5 > 37 ug/m3 sobre cada promedio de 1 min (no es el promedio de 24 h
      de la Resolucion 2254; solo sirve para comparar regla vs. IA);
  (b) reglas de salud del sensor (reglas_sensor.py), deterministicas;
  (c) Isolation Forest con features de PM, humedad, temperatura y contexto por hora.
      Se entrena SIN etiquetas y sin las salidas de las reglas.

- La evaluacion se hace sobre los mismos datos de entrenamiento (no hay conjunto de prueba).
- Con contaminacion 0.005 se marca ~0.5 % de las filas; el techo de recall por fila es
  filas marcadas / filas en eventos (era ~0.27 antes de anadir anomalia_contextual).
  La metrica principal es el recall por evento junto con PR-AUC.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import matplotlib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import RobustScaler

from features import (
    CONTEXT_FEATURES,
    GAP_MINUTES,
    MODEL_FEATURES,
    WINDOW_PM25_MIN,
    WINDOW_STD_MIN,
    agregar_contexto,
    calcular_contexto_horario,
    construir_features,
    promediar_por_minuto,
)
from reglas_sensor import CAIDO_MIN, CONGELADO_MIN, SALTO_TEMP_C, evaluar_reglas
from scoring import puntuar_features

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
MODELS_DIR = BASE / "models"
MODEL_NAME = "sim_isolation_forest_v2"

SEED = 42
N_ESTIMATORS = 200
MAD_SCALE = 1.4826
RULE_PM25 = 37
DEFAULT_CONTAMINATION = 0.005
TECHO_V1 = 0.27
UN_MIN = pd.Timedelta(minutes=1)
TRAS_EVENTO = pd.Timedelta(minutes=WINDOW_PM25_MIN)

ADVERTENCIA = "DATOS SIMULADOS: prueba de software, no evidencia de precisión real"
EVENT_TYPES = [
    "pico_corto", "episodio_largo", "anomalia_contextual",
    "sensor_congelado", "temperatura_imposible", "hueco_de_datos",
]
ROW_EVENTS = [t for t in EVENT_TYPES if t != "hueco_de_datos"]  # los huecos no tienen filas
CAPAS = {
    "a": f"Regla PM2.5 > {RULE_PM25}",
    "b": "Reglas de sensor",
    "c": "Isolation Forest",
}
COLORS = {
    "pico_corto": "tab:orange",
    "episodio_largo": "tab:purple",
    "anomalia_contextual": "tab:cyan",
    "sensor_congelado": "tab:blue",
    "temperatura_imposible": "tab:green",
    "hueco_de_datos": "lightgrey",
}


def cargar_mediciones(path):
    df = pd.read_csv(path).rename(columns={"received_at": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def cargar_eventos(path):
    ev = pd.read_csv(path)
    ev["start_utc"] = pd.to_datetime(ev["start_utc"], utc=True)
    ev["end_utc"] = pd.to_datetime(ev["end_utc"], utc=True)
    return ev


def entrenar(feats, contamination=DEFAULT_CONTAMINATION, seed=SEED, features=MODEL_FEATURES):
    """Entrena RobustScaler + IsolationForest SIN etiquetas sobre `features` (por defecto, v2).

    feats: salida de construir_features. El contexto por hora se calcula aqui con estos
    mismos datos y se guarda en el modelo.
    """
    features = list(features)
    contexto = calcular_contexto_horario(feats)
    X_df = agregar_contexto(feats, contexto)[features]
    scaler = RobustScaler().fit(X_df)
    model = IsolationForest(
        n_estimators=N_ESTIMATORS, contamination=contamination, random_state=seed
    ).fit(scaler.transform(X_df))
    median = X_df.median()
    mad = (X_df - median).abs().median() * MAD_SCALE
    escala = mad.where(mad > 0, X_df.std()).where(lambda s: s > 0, 1.0)
    return {
        "scaler": scaler,
        "model": model,
        "features": features,
        "contexto": contexto,
        "median": median,
        "escala": escala,
        "contamination": contamination,
        "seed": seed,
        "advertencia": ADVERTENCIA,
    }


def intervalos(ts, capa, tipo, mensajes=None):
    """Agrupa minutos marcados consecutivos en intervalos [inicio, fin)."""
    ts = pd.Series(ts).sort_values().reset_index(drop=True)
    mensajes = pd.Series(mensajes).reset_index(drop=True) if mensajes is not None else None
    if ts.empty:
        return pd.DataFrame(columns=["capa", "tipo", "inicio", "fin", "mensaje"])
    grupo = (ts.diff() > UN_MIN).cumsum()
    filas = []
    for _, idx in ts.groupby(grupo).groups.items():
        filas.append({
            "capa": capa,
            "tipo": tipo,
            "inicio": ts[idx[0]],
            "fin": ts[idx[-1]] + UN_MIN,
            "mensaje": mensajes[idx[0]] if mensajes is not None else "",
        })
    return pd.DataFrame(filas)


def _solapa(det, inicio, fin):
    return (det["inicio"] < fin) & (det["fin"] > inicio)


def evaluar_capas(detecciones, events, dias):
    """Por evento: que capas lo detectan. Por capa: falsas alarmas (detecciones sin evento)."""
    filas = []
    for i, e in events.iterrows():
        fila = {"evento": i, "event_type": e.event_type, "start_utc": e.start_utc,
                "minutos": int((e.end_utc - e.start_utc) / UN_MIN)}
        for capa in CAPAS:
            d = detecciones[detecciones.capa == capa]
            fila[capa] = bool(_solapa(d, e.start_utc, e.end_utc).any())
        filas.append(fila)
    por_evento = pd.DataFrame(filas)

    falsas = {}
    for capa in CAPAS:
        d = detecciones[detecciones.capa == capa]
        con_evento = np.zeros(len(d), dtype=bool)
        tras = np.zeros(len(d), dtype=bool)
        for e in events.itertuples():
            con_evento |= _solapa(d, e.start_utc, e.end_utc).to_numpy()
            tras |= _solapa(d, e.end_utc, e.end_utc + TRAS_EVENTO).to_numpy()
        fa = d[~con_evento]
        falsas[capa] = {
            "detecciones": int(len(d)),
            "falsas_alarmas": int(len(fa)),
            "falsas_alarmas_por_dia": float(len(fa) / dias),
            "falsas_alarmas_tras_evento_15min": int((~con_evento & tras).sum()),
            "falsas_alarmas_por_tipo": fa.tipo.value_counts().to_dict(),
        }
    return por_evento, falsas


def metricas_fila(scored, events):
    """Metricas por fila del Isolation Forest (los huecos no tienen filas y no entran)."""
    ts = scored["timestamp"]
    scored["evento_real"] = ""
    for e in events.itertuples():
        if e.event_type in ROW_EVENTS:
            scored.loc[(ts >= e.start_utc) & (ts < e.end_utc), "evento_real"] = e.event_type
    y = (scored["evento_real"] != "").to_numpy()
    pred = scored["es_anomalia"].to_numpy()
    return {
        "filas_evaluadas": int(len(scored)),
        "filas_en_eventos": int(y.sum()),
        "prevalencia": float(y.mean()),
        "filas_marcadas": int(pred.sum()),
        "techo_recall_por_fila": float(min(1.0, pred.sum() / max(y.sum(), 1))),
        "roc_auc": float(roc_auc_score(y, scored["score"])),
        "pr_auc": float(average_precision_score(y, scored["score"])),
        "precision_fila": float(precision_score(y, pred, zero_division=0)),
        "recall_fila": float(recall_score(y, pred, zero_division=0)),
    }


def graficar(df_min, scored, events, path):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 8), sharex=True, height_ratios=[3, 1])
    ax1.plot(df_min["timestamp"], df_min["pm25"], lw=0.5, color="black", label="PM2.5 (1 min)")
    ax2.plot(df_min["timestamp"], df_min["humidity"], lw=0.5, color="tab:blue", label="Humedad (1 min)")
    vistos = set()
    for _, e in events.iterrows():
        label = e.event_type if e.event_type not in vistos else None
        vistos.add(e.event_type)
        for ax in (ax1, ax2):
            ax.axvspan(e.start_utc, e.end_utc, color=COLORS[e.event_type], alpha=0.35,
                       label=label if ax is ax1 else None,
                       hatch="//" if e.event_type == "hueco_de_datos" else None)
    marcadas = scored[scored["es_anomalia"]]
    ax1.scatter(marcadas["timestamp"], marcadas["pm25"], s=14, color="red", marker="x",
                label="Detección Isolation Forest", zorder=3)
    ax1.axhline(RULE_PM25, color="grey", ls="--", lw=1, label=f"Regla PM2.5 > {RULE_PM25}")
    ax1.set_title(f"SIMULADO — ecoair-sim-01 — {MODEL_NAME}\n{ADVERTENCIA}")
    ax1.set_ylabel("PM2.5 (µg/m³)")
    ax2.set_ylabel("Humedad (%)")
    ax2.set_xlabel("Fecha (UTC)")
    ax1.legend(loc="upper left", fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deteccion de anomalias por capas sobre datos SIMULADOS")
    parser.add_argument("--contamination", type=float, default=DEFAULT_CONTAMINATION)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    args = parser.parse_args(argv)

    data_dir, models_dir = Path(args.data_dir), Path(args.models_dir)
    meas_path = data_dir / "sim_measurements.csv"
    events_path = data_dir / "sim_events.csv"
    df = cargar_mediciones(meas_path)
    events = cargar_eventos(events_path).sort_values("start_utc").reset_index(drop=True)
    if not df["is_simulated"].all() or set(df["device_id"]) != {"ecoair-sim-01"}:
        raise SystemExit("El CSV contiene filas no simuladas; este script solo acepta datos simulados.")

    df_min = promediar_por_minuto(df)
    dias = (df_min["timestamp"].max() + UN_MIN - df_min["timestamp"].min()) / pd.Timedelta(days=1)

    # (c) Isolation Forest: entrenado sin etiquetas ni salidas de reglas
    feats, info = construir_features(df)
    modelo = entrenar(feats, contamination=args.contamination)
    scored = puntuar_features(feats, modelo)
    m = metricas_fila(scored, events)

    # (a) regla PM2.5 y (b) reglas de sensor, sobre todos los minutos
    det_a = intervalos(df_min.loc[df_min.pm25 > RULE_PM25, "timestamp"], "a", f"pm25_mayor_{RULE_PM25}")
    det_a["mensaje"] = f"PM2.5 por encima de {RULE_PM25} µg/m³ (promedio de 1 min)."
    reglas = evaluar_reglas(df_min)
    det_b = reglas.assign(capa="b")
    marc = scored[scored["es_anomalia"]]
    det_c = intervalos(marc["timestamp"], "c", "isolation_forest", marc["mensaje"])
    detecciones = pd.concat([det_a, det_b, det_c], ignore_index=True)

    por_evento, falsas = evaluar_capas(detecciones, events, dias)
    techo = m["techo_recall_por_fila"]
    nota = (
        "La evaluación se hizo sobre los mismos datos de entrenamiento (no hay conjunto de prueba "
        f"separado). Con contaminación {args.contamination} el techo de recall por fila es "
        f"aproximadamente {techo:.2f} en esta ejecución (era ≈{TECHO_V1} antes de añadir "
        "anomalia_contextual), así que la métrica principal es el recall por evento junto con PR-AUC."
    )
    capa_resumen = {}
    for capa in CAPAS:
        sub = por_evento[por_evento.event_type.isin(EVENT_TYPES)]
        capa_resumen[capa] = {
            "eventos_detectados": int(sub[capa].sum()),
            "eventos_total": int(len(sub)),
            "recall_por_evento": float(sub[capa].mean()),
            "por_tipo": {t: f"{int(sub[sub.event_type == t][capa].sum())}/{int((sub.event_type == t).sum())}"
                         for t in EVENT_TYPES},
            **falsas[capa],
        }
    recall_c_filas = por_evento[por_evento.event_type.isin(ROW_EVENTS)]["c"].mean()

    # ---- Guardar ----
    models_dir.mkdir(parents=True, exist_ok=True)
    joblib_path = models_dir / f"{MODEL_NAME}.joblib"
    json_path = models_dir / f"{MODEL_NAME}.json"
    csv_path = data_dir / "sim_anomalias_detectadas.csv"
    capas_path = data_dir / "sim_detecciones_por_capa.csv"
    png_path = data_dir / "sim_anomalias.png"

    joblib.dump(modelo, joblib_path)
    meta = {
        "modelo": MODEL_NAME,
        "advertencia": ADVERTENCIA,
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuente_datos": {
            "mediciones": str(meas_path.relative_to(BASE.parent)),
            "eventos": str(events_path.relative_to(BASE.parent)),
            "device_id": "ecoair-sim-01",
            "is_simulated": True,
            "desde_utc": str(df["timestamp"].min()),
            "hasta_utc": str(df["timestamp"].max()),
            **info,
        },
        "features": list(MODEL_FEATURES),
        "features_contexto": list(CONTEXT_FEATURES),
        "contexto_horario": modelo["contexto"],
        "parametros": {
            "algoritmo": "IsolationForest",
            "n_estimators": N_ESTIMATORS,
            "contamination": args.contamination,
            "random_state": SEED,
            "escalado": "RobustScaler",
            "resample": "1min",
            "gap_minutes": GAP_MINUTES,
            "ventana_pm25_min": WINDOW_PM25_MIN,
            "ventana_std_min": WINDOW_STD_MIN,
            "explicacion": "distancia a la mediana en unidades de MAD x 1.4826, con piso por feature",
            "entrenado_sin_etiquetas": True,
            "entrenado_sin_salidas_de_reglas": True,
        },
        "reglas_sensor": {
            "congelado_min": CONGELADO_MIN, "salto_temp_c": SALTO_TEMP_C, "caido_min": CAIDO_MIN,
        },
        "librerias": {"scikit-learn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "evaluacion": {
            "nota": nota,
            "techo_recall_por_fila": techo,
            "techo_recall_por_fila_v1": TECHO_V1,
            "metricas_principales": ["recall_por_evento", "pr_auc"],
            "metricas_fila_isolation_forest": m,
            "capas": {CAPAS[k]: v for k, v in capa_resumen.items()},
        },
    }
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))

    cols = ["timestamp", "temperature", "humidity", "pm25", "pm10", "score", "evento_real", "mensaje"]
    scored.loc[scored["es_anomalia"], cols].assign(is_simulated=True).to_csv(csv_path, index=False)
    detecciones.assign(capa=detecciones.capa.map(CAPAS), is_simulated=True).to_csv(capas_path, index=False)
    graficar(df_min, scored, events, png_path)

    # ---- Reporte ----
    print("=" * 86)
    print(ADVERTENCIA)
    print("=" * 86)
    print(f"Datos: {meas_path.name}  {df['timestamp'].min()} -> {df['timestamp'].max()}  ({dias:.1f} días)")
    print(f"  filas de entrada {info['filas_entrada']}, minutos tras resample {info['minutos']}, "
          f"segmentos {info['segmentos']}, descartadas sin ventana completa {info['descartadas']}, "
          f"filas con features {info['filas_features']}")
    print(f"\nCapa (a): regla PM2.5 > {RULE_PM25} µg/m³ sobre promedios de 1 min (no 24 h).")
    print(f"Capa (b): reglas de sensor: congelado >= {CONGELADO_MIN} min, salto de temperatura > "
          f"{SALTO_TEMP_C} °C/min, fuera de rango del contrato, sin datos > {CAIDO_MIN} min.")
    print(f"Capa (c): {MODEL_NAME} (IsolationForest, n_estimators={N_ESTIMATORS}, "
          f"contamination={args.contamination}, random_state={SEED}, RobustScaler), "
          f"{len(MODEL_FEATURES)} features ({', '.join(CONTEXT_FEATURES)} incluidas).")
    print("          Entrenado SIN etiquetas y sin las salidas de las reglas.")
    print(f"\nNOTA: {nota}")

    print("\n--- Detección por tipo de evento (detectados / total) ---")
    print(f"  {'tipo':<23}{'(a) PM>37':>11}{'(b) sensor':>12}{'(c) IForest':>13}{'alguna':>9}")
    for t in EVENT_TYPES:
        sub = por_evento[por_evento.event_type == t]
        alguna = int((sub.a | sub.b | sub.c).sum())
        n = len(sub)
        print(f"  {t:<23}{f'{int(sub.a.sum())}/{n}':>11}{f'{int(sub.b.sum())}/{n}':>12}"
              f"{f'{int(sub.c.sum())}/{n}':>13}{f'{alguna}/{n}':>9}")
    tot = len(por_evento)
    alguna_tot = int((por_evento.a | por_evento.b | por_evento.c).sum())
    print(f"  {'TOTAL':<23}{f'{int(por_evento.a.sum())}/{tot}':>11}{f'{int(por_evento.b.sum())}/{tot}':>12}"
          f"{f'{int(por_evento.c.sum())}/{tot}':>13}{f'{alguna_tot}/{tot}':>9}")

    print("\n--- Falsas alarmas por capa (detecciones que no se solapan con ningún evento) ---")
    print(f"  {'capa':<24}{'detecciones':>12}{'falsas':>8}{'por día':>9}{'tras evento':>13}  tipos")
    for capa, nombre in CAPAS.items():
        f = falsas[capa]
        tipos = ", ".join(f"{k}: {v}" for k, v in f["falsas_alarmas_por_tipo"].items()) or "-"
        print(f"  ({capa}) {nombre:<20}{f['detecciones']:>12}{f['falsas_alarmas']:>8}"
              f"{f['falsas_alarmas_por_dia']:>9.2f}{f['falsas_alarmas_tras_evento_15min']:>13}  {tipos}")
    print("  'tras evento' = falsas alarmas que empiezan en los 15 min siguientes al fin de un evento.")
    print("  Una detección = un intervalo de minutos consecutivos marcados.")

    print("\n--- Isolation Forest, métricas por fila (sobre los datos de entrenamiento) ---")
    print(f"  Prevalencia (filas en eventos): {m['prevalencia']:.4f}  <- PR-AUC de un azar")
    print(f"  ROC-AUC:   {m['roc_auc']:.3f}")
    print(f"  PR-AUC:    {m['pr_auc']:.3f}")
    print(f"  Precisión: {m['precision_fila']:.3f}")
    print(f"  Recall:    {m['recall_fila']:.3f}  (techo {m['filas_marcadas']} marcadas / "
          f"{m['filas_en_eventos']} en eventos = {techo:.3f})")
    print(f"  Recall por evento (principal, sin huecos): {recall_c_filas:.3f}")

    print("\n--- Evento por evento ---")
    print(f"  {'tipo':<23}{'inicio UTC':<27}{'min':>5}{'(a)':>6}{'(b)':>6}{'(c)':>6}")
    for r in por_evento.itertuples():
        si = lambda v: "sí" if v else "no"  # noqa: E731
        print(f"  {r.event_type:<23}{str(r.start_utc):<27}{r.minutos:>5}{si(r.a):>6}{si(r.b):>6}{si(r.c):>6}")

    print("\n--- Ejemplos de mensajes ---")
    for capa in CAPAS:
        d = detecciones[detecciones.capa == capa]
        vistos = set()
        for r in d.itertuples():
            ev = events[(events.start_utc < r.fin) & (events.end_utc > r.inicio)]
            etiqueta = ev.event_type.iloc[0] if len(ev) else "falsa alarma"
            if etiqueta in vistos:
                continue
            vistos.add(etiqueta)
            print(f"  ({capa}) [{etiqueta}] {r.inicio}: {r.mensaje}")

    print("\nArchivos guardados:")
    for p in (joblib_path, json_path, csv_path, capas_path, png_path):
        print(f"  {p.relative_to(BASE.parent)}")
    print("=" * 86)
    print(ADVERTENCIA)
    print("=" * 86)


if __name__ == "__main__":
    main()
