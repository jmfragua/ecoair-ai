import math
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


# ---------- Textos y horizontes visibles desde el JSON del modelo activo ----------

RAIZ = os.path.join(os.path.dirname(__file__), "..", "..")
FRASE_V2 = "datos externos históricos de Medellín (2018-2020)"


def _modelo_real(nombre):
    return pr.cargar_modelo(os.path.join(RAIZ, "modelos", f"prediccion_pm25_{nombre}.json"))


def test_textos_salen_del_json_y_no_de_frases_fijas():
    m = _modelo_horario()
    m["texto_fuentes"] = "Texto de fuentes del JSON."
    r = pr.prediccion(m, _serie(lambda i: 20.0), AHORA)
    assert (r["fuentes"], r["aviso"]) == ("Texto de fuentes del JSON.", "Entrenado con datos externos históricos.")
    # sin texto_fuentes ni aviso_publico: se arma con los tipos de "fuentes" del mismo JSON
    assert pr.texto_fuentes(_modelo(fuentes=("reales",))) == "Modelo entrenado con datos propios reales. Experimental."
    assert pr.aviso_modelo(_modelo(fuentes=("reales",))) is None


def test_horizontes_visibles_ocultan_sin_tocar_codigo():
    m = _modelo_horario(supera=(True, True, True))
    m["horizontes_visibles"] = [180, 360]
    r = pr.prediccion(m, _serie(lambda i: 20.0), AHORA)
    assert [h["minutos"] for h in r["horizontes"]] == [180, 360]
    assert "en 1 h" not in r["texto"] and "a 1 h" not in r["texto"]
    # si solo queda visible un horizonte que no supera la linea base: no disponible
    m["horizontes_visibles"] = [180]
    m["horizontes"]["180"]["supera_persistencia"] = False
    assert pr.prediccion(m, _serie(lambda i: 20.0), AHORA)["estado"] == "no_supera_linea_base"
    assert pr.horizontes_visibles(_modelo()) == (60, 180)


def test_v2_real_dice_datos_externos_historicos_y_muestra_3_y_6_h():
    v2 = _modelo_real("v2")
    assert v2["horizontes_visibles"] == [180, 360]
    assert v2["nota_horizontes_visibles"] == "1 h: mejora marginal (2,3 %), no se muestra"
    r = pr.prediccion(v2, _serie(lambda i: 20.0), AHORA)
    assert r["estado"] == "ok" and [h["minutos"] for h in r["horizontes"]] == [180, 360]
    assert FRASE_V2 in r["aviso"] and "no representa la precisión con el sensor propio" in r["aviso"]
    assert "datos externos" in r["fuentes"] and "simulados" not in r["fuentes"] + r["aviso"]


def test_v1_real_sigue_igual():
    v1 = _modelo_real("v1")
    r = pr.prediccion(v1, _serie(lambda i: 20.0), AHORA)
    assert r["fuentes"] == "Modelo entrenado con datos simulados. Experimental."
    assert r["aviso"].startswith("Atención: el modelo se entrenó con datos simulados")
    assert [h["minutos"] for h in r["horizontes"]] == [60, 180]


# ---------- Modelos relativos (v3): "transformacion": "relativa" ----------

def _modelo_relativo(intercepto=0.1, coef=None, c=2.0):
    h = lambda: {"intercepto": intercepto, "coef": coef or [0.0] * 6, "alpha": 1.0,  # noqa: E731
                 "metricas": {"mae_modelo": 1.0, "mae_persistencia": 2.0}, "supera_persistencia": True}
    return {"nombre": "v3", "resolucion": "hora", "transformacion": "relativa", "c": c, "persistencia": "pm25_h1",
            "variables": pr.VARIABLES_RELATIVAS, "media": [0.0] * 6, "escala": [1.0] * 6,
            "horizontes": {"60": h(), "180": h(), "360": h()}, "fuentes": [{"tipo": "externos"}]}


def test_variables_relativas():
    x = pr.variables_relativas_desde(8.0, 18.0, 3.0, pr.minuto(AHORA), 2.0)
    assert x["pm25_h1"] == 8.0
    assert math.isclose(x["lr_h2_h1"], math.log(20 / 10)) and math.isclose(x["lr_h3_h1"], math.log(5 / 10))
    assert math.isclose(x["lr_m3_h1"], math.log((29 / 3 + 2) / 10))
    assert math.isclose(x["pend_rel"], 5 / (29 / 3 + 2))


def test_prediccion_relativa_es_multiplicativa_y_no_negativa():
    m = _modelo_relativo(intercepto=0.1)
    for nivel in (3.0, 25.0):
        s = _serie(lambda i, n=nivel: n)
        x = pr.variables_para(m, s, pr.minuto(AHORA))
        assert x["lr_h2_h1"] == 0.0 and x["pend_rel"] == 0.0
        assert math.isclose(pr.predecir_horizonte(m, x, 60), (nivel + 2) * math.exp(0.1) - 2)
    x0 = pr.variables_para(_modelo_relativo(intercepto=-5.0), _serie(lambda i: 0.0), pr.minuto(AHORA))
    assert pr.predecir_horizonte(_modelo_relativo(intercepto=-5.0), x0, 60) == 0.0
    r = pr.prediccion(m, _serie(lambda i: 3.0), AHORA)
    assert r["estado"] == "ok" and [h["minutos"] for h in r["horizontes"]] == [60, 180, 360]


# ---------- Ruta del modelo inexistente o JSON invalido: sin_modelo + WARNING ----------

def test_modelo_inexistente_deja_warning_y_queda_sin_modelo(tmp_path, caplog):
    ruta = tmp_path / "no_existe.json"
    with caplog.at_level("WARNING", logger="prediccion"):
        assert pr.cargar_modelo(str(ruta)) is None
    assert str(ruta) in caplog.text and "no encontrado" in caplog.text
    assert pr.prediccion(pr.cargar_modelo(str(ruta)), _serie(), AHORA)["estado"] == "sin_modelo"


@pytest.mark.parametrize("contenido", ["{no es json", "[1, 2]", '{"nombre": "x"}', b"\xff\xfe"])
def test_json_invalido_deja_warning_y_queda_sin_modelo(tmp_path, caplog, contenido):
    ruta = tmp_path / "malo.json"
    ruta.write_bytes(contenido if isinstance(contenido, bytes) else contenido.encode())
    with caplog.at_level("WARNING", logger="prediccion"):
        assert pr.cargar_modelo(str(ruta)) is None
    assert str(ruta) in caplog.text and "invalido" in caplog.text


def test_modelo_valido_no_deja_warning(caplog):
    with caplog.at_level("WARNING", logger="prediccion"):
        assert pr.cargar_modelo(os.path.join(RAIZ, "modelos", "prediccion_pm25_v3.json"))["nombre"] == "prediccion_pm25_v3"
    assert caplog.text == ""


# ---------- Variables v4 (6 horas previas + media de 24 h, relativas al nivel) ----------

def test_variables_v4_desde_horas():
    horas = [10.0, 8.0, 6.0, 6.0, 4.0, 2.0] + [5.0] * 18          # h1..h24
    m = pr.minuto(AHORA)
    x = pr.variables_v4_desde(horas, m, 2.0)
    assert x["pm25_h1"] == 10.0 and x["media_6h"] == 6.0 and x["horas_24h"] == 24
    assert math.isclose(x["lr_h2"], math.log(10 / 12)) and math.isclose(x["lr_h6"], math.log(4 / 12))
    assert math.isclose(x["lr_m6"], math.log(8 / 12)) and math.isclose(x["pend_rel6"], 4 / 8)
    assert math.isclose(x["lr_m24"], math.log((sum(horas) / 24 + 2) / 12))
    assert set(pr.VARIABLES_V4) <= set(x)
    # 2026-11-05 15:00 UTC = jueves 10:00 en Colombia
    assert math.isclose(x["dia_sin"], math.sin(2 * math.pi * 3 / 7))


def test_variables_v4_exige_6_horas_completas_y_18_de_24():
    base = [5.0] * 24
    assert pr.variables_v4_desde(base[:5] + [None] + base[6:], 0, 2.0) is None
    assert pr.variables_v4_desde(base[:6] + [None] * 7 + base[13:], 0, 2.0) is None   # 17 validas
    assert pr.variables_v4_desde(base[:6] + [None] * 6 + base[12:], 0, 2.0)["horas_24h"] == 18


def test_v4_en_vivo_desde_la_serie_por_minuto_y_constante_da_relativas_cero():
    modelo = {"resolucion": "hora", "transformacion": "relativa", "variables_familia": "v4", "c": 2.0,
              "persistencia": "pm25_h1", "variables": pr.VARIABLES_V4, "media": [0.0] * 12, "escala": [1.0] * 12,
              "horizontes": {"180": {"intercepto": 0.0, "coef": [0.0] * 12, "supera_persistencia": True}}}
    s = pr.serie_por_minuto([(AHORA - timedelta(minutes=i), 7.0) for i in range(24 * 60)])
    x = pr.variables_para(modelo, s, pr.minuto(AHORA))
    assert all(x[v] == 0.0 for v in ("lr_h2", "lr_h6", "lr_m6", "lr_m24", "pend_rel6"))
    assert math.isclose(pr.predecir_horizonte(modelo, x, 180), 7.0)
    assert pr.variables_para(modelo, _serie(minutos=200), pr.minuto(AHORA)) is None   # solo 3 h de datos
    assert "1445 minutes" in pr.SQL_SERIE_PM25_24H and "185 minutes" in pr.SQL_SERIE_PM25
