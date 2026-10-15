import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from features import FEATURES, construir_features  # noqa: E402

START = pd.Timestamp("2026-10-01T12:00:00Z")


def _df(minutes, pm25):
    ts = [START + pd.Timedelta(minutes=m) for m in minutes]
    return pd.DataFrame({
        "timestamp": ts,
        "temperature": 22.0,
        "humidity": 70.0,
        "pm25": pm25,
        "pm10": np.array(pm25, dtype=float) * 1.5,
    })


def test_ventanas_no_cruzan_huecos():
    # 30 min con PM2.5 = 100, hueco de 10 min, 30 min con PM2.5 = 10
    before = _df(range(0, 30), [100.0] * 30)
    after = _df(range(40, 70), [10.0] * 30)
    feats, info = construir_features(pd.concat([before, after], ignore_index=True))

    assert info["segmentos"] == 2
    # cada segmento pierde sus primeros 14 minutos (ventana de 15 min incompleta)
    assert info["descartadas"] == 28
    assert len(feats) == 32

    despues = feats[feats.timestamp >= START + pd.Timedelta(minutes=40)]
    assert despues.timestamp.min() == START + pd.Timedelta(minutes=54)
    assert (despues.pm25_media_15 == 10).all()  # sin contaminar con los 100 previos
    assert (despues.pm25_cambio == 0).all()
    assert (despues.pm25_std_15 == 0).all()


def test_umbral_de_hueco_en_minutos():
    # 19 -> 22: 3 min entre filas, no corta. 19 -> 23: 4 min, si corta.
    _, info = construir_features(_df(list(range(0, 20)) + list(range(22, 40)), [10.0] * 38))
    assert info["segmentos"] == 1
    _, info = construir_features(_df(list(range(0, 20)) + list(range(23, 40)), [10.0] * 37))
    assert info["segmentos"] == 2


def test_resample_a_un_minuto():
    # ESP32: una lectura cada 5 s durante 30 min
    ts = pd.date_range(START, periods=30 * 12, freq="5s")
    pm25 = np.arange(len(ts), dtype=float)
    df = pd.DataFrame({"timestamp": ts, "temperature": 22.0, "humidity": 70.0,
                       "pm25": pm25, "pm10": pm25 * 1.5})
    feats, info = construir_features(df)
    assert info["filas_entrada"] == 360
    assert info["minutos"] == 30
    primera = feats.iloc[0]
    assert primera.timestamp == START + pd.Timedelta(minutes=14)
    assert primera.pm25 == pm25[14 * 12:15 * 12].mean()
    assert list(feats.columns) == ["timestamp"] + FEATURES
