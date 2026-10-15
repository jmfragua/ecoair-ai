import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entrenar_anomalias import entrenar  # noqa: E402
from evaluar_prueba import VARIANTES, auc_por_tipo, cerca_de_eventos, umbral_para_presupuesto  # noqa: E402
from features import construir_features  # noqa: E402
from scoring import puntuar_features  # noqa: E402
from simular_datos import generar, main  # noqa: E402

END = "2026-10-14T12:00:00Z"
T0 = pd.Timestamp("2026-10-01T10:00:00Z")


def _eventos(*filas):
    return pd.DataFrame(
        [{"event_type": t, "start_utc": s, "end_utc": e} for t, s, e in filas]
    )


def test_prefix_no_pisa_archivos(tmp_path):
    main(["--seed", "43", "--end", END, "--out-dir", str(tmp_path), "--prefix", "sim_test_"])
    assert (tmp_path / "sim_test_measurements.csv").is_file()
    assert (tmp_path / "sim_test_events.csv").is_file()
    assert not (tmp_path / "sim_measurements.csv").exists()
    assert not (tmp_path / "sim_events.csv").exists()


def test_variante_b_quita_exactamente_tres_features_de_nivel():
    assert set(VARIANTES["A"]) - set(VARIANTES["B"]) == {"pm25", "pm10", "pm25_media_15"}
    assert len(VARIANTES["A"]) == 17 and len(VARIANTES["B"]) == 14
    for f in ("temperature", "humidity", "pm25_cambio", "pm25_vs_hora", "ratio_pm10_pm25"):
        assert f in VARIANTES["B"]


def test_cerca_de_eventos_borde_15_min():
    ev = _eventos(("pico_corto", T0, T0 + pd.Timedelta(minutes=30)))  # ultimo minuto 10:29
    m = lambda x: T0 + pd.Timedelta(minutes=x)  # noqa: E731
    ts = [m(-16), m(-15), m(10), m(44), m(45)]
    assert list(cerca_de_eventos(ts, ev)) == [False, True, True, True, False]


def test_auc_por_tipo_caso_conocido():
    tipos = np.array(["pico_corto"] * 3 + ["sensor_congelado"] * 3 + [""] * 6)
    cerca = np.array([True] * 6 + [True, False, False, False, False, False])
    # pico_corto con score alto (AUC 1), congelado con score bajo (AUC 0);
    # la fila normal "cerca" de un evento tiene score muy alto pero se excluye.
    score = np.array([9, 9, 9, 0, 0, 0, 99, 1, 2, 3, 4, 5], dtype=float)
    auc = auc_por_tipo(score, tipos, cerca)
    assert auc["pico_corto"] == 1.0
    assert auc["sensor_congelado"] == 0.0
    assert auc["episodio_largo"] is None


def test_entrenar_con_variante_b():
    df, _ = generar(seed=1, end=END, days=5)
    feats, _ = construir_features(df.rename(columns={"received_at": "timestamp"}))
    modelo = entrenar(feats, features=VARIANTES["B"])
    assert modelo["features"] == VARIANTES["B"]
    s = puntuar_features(feats, modelo)
    assert s["score"].notna().all()
    assert s["es_anomalia"].any()


def _ts(*minutos):
    return [T0 + pd.Timedelta(minutes=m) for m in minutos]


def test_umbral_para_presupuesto_caso_conocido():
    # 1 dia, presupuesto 2: con score >= 9 hay 2 episodios; al anadir 8 serian 3.
    umbral, eps = umbral_para_presupuesto([10, 9, 8, 7], _ts(0, 100, 200, 205), 2, dias=1)
    assert umbral == 9
    assert eps == 2


def test_umbral_para_es_conservador_si_fusiona():
    # Presupuesto 1: score 9 crea un 2o episodio (gap 16 min) y se para ahi,
    # aunque con score 8 (minuto 8) los tres se fusionarian en uno solo.
    umbral, eps = umbral_para_presupuesto([10, 9, 8], _ts(0, 16, 8), 1, dias=1)
    assert umbral == 10
    assert eps == 1


def test_umbral_si_ni_la_primera_fila_cabe():
    umbral, eps = umbral_para_presupuesto([10, 9], _ts(0, 100), 0, dias=1)
    assert umbral == np.inf
    assert eps == 0
