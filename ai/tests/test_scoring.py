import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entrenar_anomalias import entrenar  # noqa: E402
from features import MODEL_FEATURES, agregar_contexto, construir_features  # noqa: E402
from scoring import agrupar_episodios, explicar, puntuar  # noqa: E402
from simular_datos import generar  # noqa: E402

END = "2026-10-14T12:00:00Z"


@pytest.fixture(scope="module")
def base():
    df, events = generar(seed=1, end=END, days=5)
    df = df.rename(columns={"received_at": "timestamp"})
    feats, _ = construir_features(df)
    modelo = entrenar(feats)
    # Minuto normal: lejos (> 2 h) de cualquier evento y con features completas
    lejos = pd.Series(True, index=feats.index)
    for e in events.itertuples():
        margen = pd.Timedelta(hours=2)
        lejos &= ~feats.timestamp.between(e.start_utc - margen, e.end_utc + margen)
    ts_normal = feats.timestamp[lejos].iloc[len(feats[lejos]) // 2]
    return df, modelo, ts_normal


def _modificar(df, ts, **valores):
    out = df.copy()
    for col, v in valores.items():
        out.loc[out.timestamp == ts, col] = v
    return out


def test_pm25_absurdo_tiene_score_mayor(base):
    df, modelo, ts = base
    normal = puntuar(df, modelo).set_index("timestamp").loc[ts]
    absurdo = puntuar(_modificar(df, ts, pm25=600.0, pm10=900.0), modelo).set_index("timestamp").loc[ts]
    assert absurdo.score > normal.score
    assert absurdo.es_anomalia


def test_mensaje_menciona_pm25(base):
    df, modelo, ts = base
    row = puntuar(_modificar(df, ts, pm25=600.0, pm10=900.0), modelo).set_index("timestamp").loc[ts]
    assert "PM2.5" in row.mensaje


def test_mensaje_menciona_temperatura(base):
    df, modelo, ts = base
    feats, _ = construir_features(_modificar(df, ts, temperature=60.0))
    row = agregar_contexto(feats, modelo["contexto"]).set_index("timestamp").loc[ts]
    assert "Temperatura" in explicar(row, modelo)


def _modelo_falso(escala_cambio=1.0):
    # Solo pm25_cambio tiene escala pequena: las demas features no compiten por ser la principal
    features = list(MODEL_FEATURES)
    escala = pd.Series(1000.0, index=features)
    escala["pm25_cambio"] = escala_cambio
    return {"features": features, "median": pd.Series(0.0, index=features), "escala": escala}


def _fila(**valores):
    row = pd.Series(0.0, index=MODEL_FEATURES)
    for k, v in valores.items():
        row[k] = v
    return row


def test_mensaje_de_salto_con_tope():
    msg = explicar(_fila(pm25_cambio=-140.0, pm25=10.0), _modelo_falso())
    assert "PM2.5 bajó 140 µg/m³ en 1 min" in msg
    assert "muy por encima de lo habitual" in msg  # 140 veces > tope de 50


def test_mensaje_de_salto_bajo_el_tope():
    msg = explicar(_fila(pm25_cambio=30.0, pm25=45.0), _modelo_falso())
    assert "PM2.5 subió 30 µg/m³ en 1 min" in msg
    assert "unas 30 veces" in msg


def test_piso_de_escala():
    # MAD casi cero: sin piso daria 3000 veces; con piso de 1 µg/m³ da 3 veces
    msg = explicar(_fila(pm25_cambio=3.0, pm25=12.0), _modelo_falso(escala_cambio=0.001))
    assert "unas 3 veces" in msg


def test_mensaje_de_variabilidad_no_sugiere_valor_alto():
    msg = explicar(_fila(pm25_std_10=8.0, pm25=6.0), _modelo_falso_std("pm25_std_10"))
    assert "PM2.5 con variación inusual en los últimos 10 min" in msg
    assert "PM2.5 de 6" not in msg
    assert "pico de contaminación" not in msg


def test_mensaje_de_variabilidad_15_min():
    msg = explicar(_fila(pm25_std_15=8.0, pm25=6.0), _modelo_falso_std("pm25_std_15"))
    assert "PM2.5 con variación inusual en los últimos 15 min" in msg


def _modelo_falso_std(feature):
    features = list(MODEL_FEATURES)
    escala = pd.Series(1000.0, index=features)
    escala[feature] = 0.5
    return {"features": features, "median": pd.Series(0.0, index=features), "escala": escala}


def _scored(minutos, scores):
    t0 = pd.Timestamp("2026-10-01T10:00:00Z")
    row = {f: 0.0 for f in MODEL_FEATURES}
    return pd.DataFrame([
        {**row, "timestamp": t0 + pd.Timedelta(minutes=m), "score": s, "es_anomalia": True}
        for m, s in zip(minutos, scores)
    ])


def test_episodios_borde_9_y_10_min():
    modelo = _modelo_falso()
    # 0 y 9 min: se fusionan (< 10). 9 y 19 min: separados (= 10).
    eps = agrupar_episodios(_scored([0, 9, 19], [0.6, 0.9, 0.7]), modelo)
    assert len(eps) == 2
    assert eps.iloc[0].inicio == pd.Timestamp("2026-10-01T10:00:00Z")
    assert eps.iloc[0].fin == pd.Timestamp("2026-10-01T10:10:00Z")
    assert eps.iloc[0].score_max == 0.9
    assert eps.iloc[0].filas == 2
    assert eps.iloc[1].filas == 1
