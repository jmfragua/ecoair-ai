"""Tests de ai/entrenar_prediccion_v2.py con una serie horaria INVENTADA (no son datos reales)."""
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import entrenar_prediccion_v2 as e2  # noqa: E402
from shared import prediccion as pr  # noqa: E402

H = lambda s: int(datetime.fromisoformat(s).timestamp() // 3600)  # noqa: E731


def _csv(horas_valores, licencia="L"):
    lineas = ["source,station,city,measured_at,pm25,license"]
    for h, v in horas_valores:
        t = datetime.fromtimestamp(h * 3600, tz=timezone.utc).isoformat()
        lineas.append(f"OpenAQ archive,EST,Medellín,{t},{v},{licencia}")
    return io.StringIO("\n".join(lineas) + "\n")


def _serie_sintetica(huecos_cada=97):
    """2018-11-01 .. 2020-02-29 UTC: ciclo diario + ruido AR(1), con huecos regulares."""
    rng = np.random.default_rng(7)
    ini, fin = H("2018-11-01T00:00:00+00:00"), H("2020-02-29T23:00:00+00:00")
    serie, ruido = {}, 0.0
    for h in range(ini, fin + 1):
        ruido = 0.8 * ruido + rng.normal(0, 2)
        if h % huecos_cada == 0:
            continue
        hora = e2.hora_colombia(h)
        serie[h] = max(0.0, 20 + 8 * math.sin(2 * math.pi * (hora - 2) / 24) + ruido)
    return serie


def test_leer_csv_utc_en_punto_y_sin_duplicados():
    h = H("2019-01-01T05:00:00+00:00")
    texto = _csv([(h, 10), (h, 11), (h + 1, 12)])
    texto = io.StringIO(texto.getvalue() + "OpenAQ archive,EST,Medellín,2019-01-01T07:30:00+00:00,9,L\n")
    serie, info = e2.leer_csv(texto)
    assert serie == {h: 10.0, h + 1: 12.0}
    assert (info["filas"], info["duplicadas"], info["no_en_punto"]) == (4, 1, 1)
    assert dict(info["licencias"]) == {"L": 2}


def test_muestras_solo_con_ventana_y_objetivo_completos():
    t = H("2019-03-10T12:00:00+00:00")
    serie = {t - 2: 1.0, t - 1: 2.0, t: 3.0, t + 1: 4.0, t + 3: 9.0}   # falta t+2
    X, y, P, _ = e2.muestras(serie, "entrenamiento", 1)
    # t (objetivo t+1) y t+1 (pero t+1-2 = t-1, t, t+1 -> objetivo t+2 falta)
    assert list(y) == [4.0] and list(P) == [3.0]
    assert list(X[0][:3]) == [3.0, 2.0, 1.0]
    X3, y3, _, _ = e2.muestras(serie, "entrenamiento", 3)
    assert list(y3) == [9.0]


def test_ninguna_muestra_cruza_conjuntos():
    # ultima hora de septiembre 2019 -> objetivo en octubre: no entra en ningun conjunto
    t = H("2019-09-30T23:00:00+00:00")
    serie = {t + k: float(k) for k in range(-2, 3)}
    assert len(e2.muestras(serie, "entrenamiento", 1)[1]) == 0
    assert len(e2.muestras(serie, "validacion", 1)[1]) == 0
    assert e2.conjunto_de(t) == "entrenamiento" and e2.conjunto_de(t + 1) == "validacion"
    assert e2.conjunto_de(H("2020-03-01T00:00:00+00:00")) is None
    assert e2.conjunto_de(H("2019-12-01T00:00:00+00:00")) == "prueba"


def test_media_por_hora_solo_con_entrenamiento():
    t_tr, t_te = H("2019-01-01T15:00:00+00:00"), H("2020-01-01T15:00:00+00:00")   # 10:00 en Colombia
    tabla = e2.media_por_hora({t_tr: 10.0, t_tr + 24: 20.0, t_te: 1000.0})
    assert tabla == {10: 15.0}


def test_variables_de_entrenamiento_iguales_a_las_de_inferencia():
    """Con medias horarias constantes, la serie por minuto en vivo da las mismas variables."""
    t = H("2019-05-01T20:00:00+00:00")
    serie_h = {t - 2: 10.0, t - 1: 20.0, t: 30.0}
    ahora = datetime.fromtimestamp(t * 3600, tz=timezone.utc)
    filas = [(ahora - timedelta(minutes=i), 30.0 if i < 60 else 20.0 if i < 120 else 10.0) for i in range(180)]
    en_vivo = pr.variables_horarias_en(pr.serie_por_minuto(filas), pr.minuto(ahora))
    assert en_vivo == e2.entrada_en(serie_h, t)


def test_de_punta_a_punta_escribe_v2_cargable_y_no_toca_v1(tmp_path):
    serie = _serie_sintetica()
    entrada = tmp_path / "ext.csv"
    entrada.write_text(_csv(sorted(serie.items()), licencia="Licencia X").getvalue(), encoding="utf-8")
    v1_antes = e2.V1.read_bytes()
    salida = tmp_path / "v2.json"
    assert e2.main(["--entrada", str(entrada), "--salida", str(salida)]) == 0
    assert e2.V1.read_bytes() == v1_antes
    m = json.loads(salida.read_text(encoding="utf-8"))
    assert m["nombre"] == "prediccion_pm25_v2" and m["resolucion"] == "hora"
    assert set(m["horizontes"]) == {"60", "180", "360"}
    assert m["horizontes_visibles"] == [180, 360] and m["texto_fuentes"] == e2.TEXTO_FUENTES
    assert m["aviso_publico"] == e2.AVISO and "no representa la precisión con el sensor propio" in m["aviso_publico"]
    f = m["fuentes"][0]
    assert (f["tipo"], f["licencia"], len(f["sha256"])) == ("externos", "Licencia X", 64)
    assert m["conjuntos"]["prueba"]["meses_utc"] == ["2019-12", "2020-02"]
    for d in m["horizontes"].values():
        assert d["alpha"] in e2.ALPHAS and set(d["mae_validacion_por_alpha"]) == {str(a) for a in e2.ALPHAS}
        met = d["metricas"]
        assert d["supera_persistencia"] == (met["mae_modelo"] < met["mae_persistencia"]) or \
            met["mae_modelo"] == met["mae_persistencia"]
        assert {"mae_media_horaria", "rmse_media_horaria", "rmse_modelo", "rmse_persistencia"} <= set(met)
    # la inferencia en Python puro lo carga y lo aplica sin cambios en api/notifier
    ahora = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    s = pr.serie_por_minuto([(ahora - timedelta(minutes=i), 20.0) for i in range(185)])
    r = pr.prediccion(pr.cargar_modelo(salida), s, ahora)
    assert r["estado"] in ("ok", "no_supera_linea_base")
    assert r["aviso"] == e2.AVISO


def test_no_sobrescribe_v1():
    assert e2.main(["--salida", str(e2.V1)]) == 2
