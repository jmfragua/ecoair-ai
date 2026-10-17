"""Tests de ai/entrenar_prediccion_v3.py con series INVENTADAS (no son datos reales)."""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import entrenar_prediccion_v2 as e2  # noqa: E402
import entrenar_prediccion_v3 as e3  # noqa: E402
from test_entrenar_prediccion_v2 import H, _csv, _serie_sintetica  # noqa: E402


def test_mismas_muestras_que_v2():
    serie = _serie_sintetica()
    for hh in (1, 3, 6):
        for c in ("entrenamiento", "validacion", "prueba"):
            a, b = e3.muestras(serie, c, hh), e2.muestras(serie, c, hh)
            assert list(a[2]) == list(b[1]) and list(a[3]) == list(b[2])


def test_objetivo_y_prediccion_relativos():
    t = H("2019-03-10T12:00:00+00:00")
    serie = {t - 2: 4.0, t - 1: 6.0, t: 8.0, t + 1: 3.0}
    X, ylog, y, P, _, _ = e3.muestras(serie, "entrenamiento", 1)
    assert math.isclose(ylog[0], math.log((3 + 2) / (8 + 2))) and (y[0], P[0]) == (3.0, 8.0)
    import numpy as np
    assert math.isclose(e3.predecir(np.zeros((1, 6)), np.array([8.0]), math.log(0.5), np.zeros(6))[0], 3.0)


def _sesgo(valor_por_nivel):
    return {str(h): {str(hc): {str(n): valor_por_nivel(n) for n in e3.NIVELES} for hc in e3.HORAS_COLOMBIA}
            for h in (60, 180, 360)}


def test_criterio_de_sesgo_e_invariancia():
    ok = _sesgo(lambda n: 0.1 * (n + 2))          # proporcional a (nivel + c): invariante
    assert e3.invariancia_escala(ok, 60)
    assert e3.cumple_sesgo(ok, 60)                # 3 -> 0.5, 6 -> 0.8 <= 1.0
    malo = _sesgo(lambda n: 0.25 * (n + 2))       # 6 -> 2.0 > 1.5
    assert not e3.cumple_sesgo(malo, 360)
    assert not e3.invariancia_escala(_sesgo(lambda n: 1.0), 60)   # desvio fijo: no es proporcional
    # 12 y 25 no entran en el criterio
    assert e3.cumple_sesgo(_sesgo(lambda n: 0.0 if n < 10 else 50.0), 180)


def test_de_punta_a_punta_no_toca_v1_ni_v2(tmp_path):
    serie = _serie_sintetica()
    entrada = tmp_path / "ext.csv"
    entrada.write_text(_csv(sorted(serie.items()), licencia="Licencia X").getvalue(), encoding="utf-8")
    antes = [p.read_bytes() for p in e3.PROTEGIDOS]
    salida = tmp_path / "v3.json"
    assert e3.main(["--entrada", str(entrada), "--salida", str(salida)]) == 0
    assert [p.read_bytes() for p in e3.PROTEGIDOS] == antes
    m = json.loads(salida.read_text(encoding="utf-8"))
    assert (m["nombre"], m["transformacion"], m["c"], m["resolucion"]) == ("prediccion_pm25_v3", "relativa", 2.0, "hora")
    assert m["aviso_publico"] == ("Entrenado con datos externos históricos de Medellín (2018-2020); "
                                  "no representa la precisión con el sensor propio.")
    assert "no es una prueba virgen" in m["prueba_no_virgen"]
    for h, d in m["horizontes"].items():
        assert d["alpha"] in e3.ALPHAS and "mae_v2" in d["metricas"]
        assert d["invariancia_escala"] is True
        visible = int(h) in m["horizontes_visibles"]
        assert visible == (d["metricas"]["mejora_mae_pct"] >= 5.0 and d["cumple_sesgo"]) or \
            abs(d["metricas"]["mejora_mae_pct"] - 5.0) <= 0.05


def test_no_sobrescribe_v1_ni_v2():
    for p in e3.PROTEGIDOS:
        assert e3.main(["--salida", str(p)]) == 2
