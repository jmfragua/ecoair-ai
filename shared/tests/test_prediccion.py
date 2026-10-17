import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared import prediccion as pr  # noqa: E402

AHORA = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)   # 10:00 en Colombia


def _serie(valor=lambda i: 10.0, minutos=200, faltan=()):
    return pr.serie_por_minuto([(AHORA - timedelta(minutes=i), valor(i))
                                for i in range(minutos) if i not in faltan])


def _modelo(supera=(True, True), intercepto=2.0, fuentes=("simulados",)):
    h = lambda s: {"intercepto": intercepto, "coef": [0.0] * len(pr.VARIABLES), "alpha": 1.0,  # noqa: E731
                   "metricas": {"mae_modelo": 1.0, "mae_persistencia": 2.0}, "supera_persistencia": s}
    return {"nombre": "prueba", "variables": pr.VARIABLES, "media": [0.0] * 7, "escala": [1.0] * 7,
            "horizontes": {"60": h(supera[0]), "180": h(supera[1])},
            "fuentes": [{"tipo": f} for f in fuentes]}


def test_variables_y_objetivo():
    s = _serie()
    x = pr.variables_en(s, pr.minuto(AHORA))
    assert x["ult"] == x["media_180"] == 10.0 and x["pendiente_15_60"] == 0.0
    assert x["hora_sin"] == pytest.approx(0.5) and x["hora_cos"] == pytest.approx(-0.866, abs=1e-3)  # 10:00
    assert pr.objetivo_en(s, pr.minuto(AHORA) - 60, 60) == 10.0


def test_sin_datos_suficientes_no_hay_variables():
    assert pr.variables_en(_serie(minutos=100), pr.minuto(AHORA)) is None          # menos de 3 h
    assert pr.variables_en(_serie(faltan=range(0, 5)), pr.minuto(AHORA)) is None   # sin los ultimos 5 min


def test_formula_sobre_persistencia_y_nunca_negativa():
    x = pr.variables_en(_serie(), pr.minuto(AHORA))
    assert pr.predecir_horizonte(_modelo(intercepto=2.0), x, 60) == 12.0
    assert pr.predecir_horizonte(_modelo(intercepto=-50.0), x, 60) == 0.0


def test_ningun_horizonte_supera_la_linea_base():
    r = pr.prediccion(_modelo(supera=(False, False)), _serie(), AHORA)
    assert r["estado"] == "no_supera_linea_base" and r["texto"] == pr.NO_DISPONIBLE
    assert r["horizontes"] == [] and "Tendencia de las últimas 3 horas" in r["tendencia"]
    assert "no una predicción" in r["tendencia"]


def test_solo_se_muestra_el_horizonte_que_supera():
    r = pr.prediccion(_modelo(supera=(False, True)), _serie(), AHORA)
    assert r["estado"] == "ok"
    h1, h3 = r["horizontes"]
    assert h1 == {"minutos": 60, "pm25": None, "disponible": False}     # nunca la persistencia como prediccion
    assert h3["disponible"] and h3["pm25"] == 12.0
    assert "a 1 h: no disponible" in r["texto"] and "en 3 h: 12.0" in r["texto"]


def test_fuentes_y_aviso_de_simulados():
    r = pr.prediccion(_modelo(fuentes=("simulados",)), _serie(), AHORA)
    assert r["fuentes"] == "Modelo entrenado con datos simulados. Experimental."
    assert "simulados" in r["aviso"] and r["experimental"] is True
    r = pr.prediccion(_modelo(fuentes=("reales", "externos")), _serie(), AHORA)
    assert r["fuentes"] == "Modelo entrenado con datos propios reales y datos externos. Experimental."
    assert r["aviso"] is None


def test_sin_datos_y_sin_modelo():
    assert pr.prediccion(_modelo(), _serie(minutos=30), AHORA)["estado"] == "sin_datos"
    assert pr.prediccion(None, _serie(), AHORA)["estado"] == "sin_modelo"


def test_tendencia_3h():
    sube = _serie(valor=lambda i: 40.0 - i * 0.15)    # hace 3 h ~13, ahora ~40
    assert "subió" in pr.tendencia_3h(sube, pr.minuto(AHORA))
    assert "estable" in pr.tendencia_3h(_serie(), pr.minuto(AHORA))


def test_modelo_versionado_es_coherente():
    m = pr.cargar_modelo()
    assert m is not None and m["variables"] == pr.VARIABLES and m["experimental"] is True
    assert {f["tipo"] for f in m["fuentes"]} == {"simulados"} and "simulados" in m["advertencia"]
    for h in ("60", "180"):
        met = m["horizontes"][h]["metricas"]
        assert m["horizontes"][h]["supera_persistencia"] == (met["mae_modelo"] < met["mae_persistencia"])


# ---------- Modelos horarios (v2): misma interfaz, v1 sin cambios ----------

def _modelo_horario(supera=(True, False, True), intercepto=1.0):
    h = lambda s: {"intercepto": intercepto, "coef": [0.0] * 7, "alpha": 1.0,  # noqa: E731
                   "metricas": {"mae_modelo": 1.0, "mae_persistencia": 2.0}, "supera_persistencia": s}
    return {"nombre": "v2", "resolucion": "hora", "persistencia": "pm25_h1", "variables": pr.VARIABLES_HORARIAS,
            "media": [0.0] * 7, "escala": [1.0] * 7,
            "horizontes": {"60": h(supera[0]), "180": h(supera[1]), "360": h(supera[2])},
            "fuentes": [{"tipo": "externos"}], "aviso_publico": "Entrenado con datos externos históricos."}


def test_variables_horarias_desde_la_serie_por_minuto():
    # ultima hora 30, la anterior 20, la de antes 10 (i = minutos antes de AHORA)
    s = _serie(lambda i: 30.0 if i < 60 else 20.0 if i < 120 else 10.0)
    x = pr.variables_horarias_en(s, pr.minuto(AHORA))
    assert (x["pm25_h1"], x["pm25_h2"], x["pm25_h3"], x["media_3h"], x["pendiente_h1_h3"]) == (30.0, 20.0, 10.0, 20.0, 20.0)
    assert x == pr.variables_horarias_desde(30.0, 20.0, 10.0, pr.minuto(AHORA))
    # una hora con menos de 40 min con datos -> sin variables
    assert pr.variables_horarias_en(_serie(faltan=range(130, 160)), pr.minuto(AHORA)) is None


def test_prediccion_con_modelo_horario_usa_sus_horizontes_y_su_persistencia():
    s = _serie(lambda i: 30.0 if i < 60 else 20.0 if i < 120 else 10.0)
    r = pr.prediccion(_modelo_horario(), s, AHORA)
    assert r["estado"] == "ok" and r["fuentes"] == "Modelo entrenado con datos externos. Experimental."
    assert r["aviso"] == "Entrenado con datos externos históricos."
    assert [(h["minutos"], h["pm25"], h["disponible"]) for h in r["horizontes"]] == [(60, 31.0, True), (180, None, False), (360, 31.0, True)]
    assert "en 6 h: 31.0" in r["texto"] and "a 3 h: no disponible" in r["texto"]
    assert pr.prediccion(_modelo_horario(), _serie(minutos=100), AHORA)["estado"] == "sin_datos"
    assert pr.prediccion(_modelo_horario(supera=(False,) * 3), s, AHORA)["estado"] == "no_supera_linea_base"


def test_modelo_sin_resolucion_sigue_siendo_v1():
    m = _modelo()
    assert pr.horizontes_de(m) == (60, 180) and not pr.es_horario(m)
    assert pr.aviso_simulados(_modelo(fuentes=("reales",))) is None
