import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calidad_aire import construir_respuesta  # noqa: E402

AHORA = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _v(pm25, pm10, minutos, muestras=None):
    return {"pm25_media": pm25, "pm10_media": pm10,
            "muestras": muestras if muestras is not None else minutos * 12,
            "minutos_con_datos": minutos}


def _ultima(hace=timedelta(seconds=10)):
    return {"received_at": AHORA - hace, "temperature": 24.3, "humidity": 66.0}


def _resp(v1=None, v24=None, ultima="default"):
    return construir_respuesta(
        "ecoair-node-01",
        v1 or _v(25, 40, 60),
        v24 or _v(20, 35, 24 * 60),
        _ultima() if ultima == "default" else ultima,
        AHORA,
    )


def test_caso_normal():
    r = _resp()
    assert r["estado"] == "ok"
    assert r["fuente"] == "Res. 2254/2017"
    assert r["advertencia"] == "Estación de bajo costo, no sustituye mediciones oficiales"
    assert r["calculado_utc"] == "2026-10-15T12:00:00+00:00"
    assert r["ica"]["pm25"]["ica"] == 76  # pm25 25 -> 76 (aceptable)
    assert r["ica"]["general"]["definido_por"] == "pm25"
    assert r["ventana_24h"]["parcial"] is False
    assert r["ventana_24h"]["horas_con_datos"] == 24.0
    assert r["norma_24h"]["nivel_alerta"] is None
    assert r["norma_24h"]["supera_limite_diario"] == {"pm25": False, "pm10": False}
    assert r["ultima_medicion"]["temperature"] == 24.3
    assert r["ultima_medicion"]["humidity"] == 66.0
    assert r["recomendacion"]["tipo"] == "texto_fijo_no_ia"
    assert r["recomendacion"]["fuente"]


def test_ventana_24h_parcial():
    # 17 h de datos: menos del 75 % (18 h)
    r = _resp(v24=_v(20, 35, 17 * 60))
    assert r["ventana_24h"]["parcial"] is True
    assert r["norma_24h"]["parcial"] is True
    assert "PARCIAL" in r["norma_24h"]["base"]
    assert any("17.0 h" in a and "no equivalen a una media de 24 h" in a for a in r["advertencias"])


def test_ventana_24h_borde_75_por_ciento():
    assert _resp(v24=_v(20, 35, 18 * 60))["ventana_24h"]["parcial"] is False


def test_sin_datos_mas_de_5_min():
    r = _resp(ultima=_ultima(hace=timedelta(minutes=5, seconds=1)))
    assert r["estado"] == "sin_datos"
    assert "ica" not in r
    assert r["ultima_medicion"]["received_at"] == "2026-10-15T11:54:59+00:00"
    assert r["fuente"] == "Res. 2254/2017"
    assert r["advertencia"] == "Estación de bajo costo, no sustituye mediciones oficiales"


def test_borde_5_min_exactos_cuenta_como_actual():
    assert _resp(ultima=_ultima(hace=timedelta(minutes=5)))["estado"] == "ok"


def test_sin_ninguna_medicion():
    r = _resp(ultima=None)
    assert r["estado"] == "sin_datos"
    assert r["ultima_medicion"] is None


def test_ica_general_definido_por_pm10():
    # pm25 10 -> ICA 42; pm10 100 -> ICA 73
    r = _resp(v1=_v(10, 100, 60))
    assert r["ica"]["general"]["ica"] == 73
    assert r["ica"]["general"]["definido_por"] == "pm10"
    assert r["ica"]["general"]["category_code"] == "aceptable"


def test_nivel_alerta_usa_media_24h():
    # Media de 1 h baja, pero la de 24 h de PM2.5 (60) esta en "alerta" y supera el limite de 37
    r = _resp(v1=_v(8, 15, 60), v24=_v(60, 80, 24 * 60))
    assert r["ica"]["general"]["category_code"] == "buena"
    assert r["norma_24h"]["nivel_alerta"] == "alerta"
    assert r["norma_24h"]["nivel_alerta_por_contaminante"] == {"pm25": "alerta", "pm10": None}
    assert r["norma_24h"]["supera_limite_diario"] == {"pm25": True, "pm10": True}
