import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reglas_sensor import evaluar_reglas  # noqa: E402

START = pd.Timestamp("2026-10-01T12:00:00Z")


def _df(n=120, minutes=None):
    """Datos por minuto que cambian en cada fila (ninguna regla deberia saltar)."""
    minutes = list(range(n)) if minutes is None else list(minutes)
    k = np.arange(len(minutes))
    return pd.DataFrame({
        "timestamp": [START + pd.Timedelta(minutes=m) for m in minutes],
        "temperature": 22 + 0.1 * np.sin(k),
        "humidity": 70 + 0.5 * np.cos(k),
        "pm25": 10 + 0.3 * np.sin(k / 3) + 0.01 * k,
        "pm10": 18 + 0.4 * np.cos(k / 3) + 0.01 * k,
    })


def _congelar(df, desde, n):
    df = df.copy()
    cols = ["temperature", "humidity", "pm25", "pm10"]
    df.loc[desde:desde + n - 1, cols] = df.loc[desde, cols].to_numpy()
    return df


def _tipos(det):
    return list(det.tipo)


def test_datos_normales_sin_detecciones():
    assert evaluar_reglas(_df()).empty


def test_sensor_congelado_30_min():
    det = evaluar_reglas(_congelar(_df(), 40, 30))
    assert _tipos(det) == ["sensor_congelado"]
    r = det.iloc[0]
    assert r.inicio == START + pd.Timedelta(minutes=40)
    assert r.fin == START + pd.Timedelta(minutes=70)
    assert "30 min" in r.mensaje and "congelado" in r.mensaje


def test_sensor_congelado_29_min_no_salta():
    assert evaluar_reglas(_congelar(_df(), 40, 29)).empty


def test_salto_temperatura():
    df = _df()
    df.loc[50, "temperature"] = 60.0  # sube y luego baja: una sola deteccion fusionada
    det = evaluar_reglas(df)
    assert _tipos(det) == ["salto_temperatura"]
    r = det.iloc[0]
    assert r.inicio == START + pd.Timedelta(minutes=49)
    assert r.fin == START + pd.Timedelta(minutes=52)
    assert "temperatura" in r.mensaje and "60.0" in r.mensaje


def test_salto_de_10_grados_no_salta():
    df = _df()
    df["temperature"] = np.where(np.arange(len(df)) % 2 == 0, 22.3, 22.4)
    df.loc[50:, "temperature"] = 32.4 - 0.1 * (np.arange(len(df) - 50) % 2)
    df.loc[49, "temperature"] = 22.4  # 22.4 -> 32.4: salto de 10.0 °C, no "mas de 10"
    assert "salto_temperatura" not in _tipos(evaluar_reglas(df))
    df.loc[50, "temperature"] = 32.5  # 10.1 °C: si salta
    assert "salto_temperatura" in _tipos(evaluar_reglas(df))


def test_fuera_de_rango_fisico():
    df = _df()
    df.loc[30, "humidity"] = 101.0
    det = evaluar_reglas(df)
    assert _tipos(det) == ["fuera_de_rango_fisico"]
    assert "Humedad 101.0" in det.iloc[0].mensaje


def test_limite_de_rango_no_salta():
    df = _df()
    df.loc[30, "humidity"] = 100.0
    assert evaluar_reglas(df).empty


def test_dispositivo_caido():
    det = evaluar_reglas(_df(minutes=list(range(0, 10)) + list(range(16, 40))))  # faltan 6 min
    assert _tipos(det) == ["dispositivo_caido"]
    r = det.iloc[0]
    assert r.inicio == START + pd.Timedelta(minutes=10)
    assert r.fin == START + pd.Timedelta(minutes=16)
    assert "6 min" in r.mensaje


def test_hueco_de_5_min_no_salta():
    assert evaluar_reglas(_df(minutes=list(range(0, 10)) + list(range(15, 40)))).empty  # faltan 5


def test_dispositivo_caido_al_final():
    df = _df(30)
    det = evaluar_reglas(df, hasta=START + pd.Timedelta(minutes=60))
    assert _tipos(det) == ["dispositivo_caido"]
    assert det.iloc[0].inicio == START + pd.Timedelta(minutes=30)
