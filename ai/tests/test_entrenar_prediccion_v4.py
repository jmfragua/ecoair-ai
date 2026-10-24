"""Tests de ai/entrenar_prediccion_v4.py con series INVENTADAS (sin red ni base)."""
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import entrenar_prediccion_v4 as e4  # noqa: E402
from shared import prediccion as pr  # noqa: E402

H = lambda s: int(datetime.fromisoformat(s).timestamp() // 3600)  # noqa: E731


def _series_sinteticas(estaciones=("A (IDEAM 1)", "B (IDEAM 2)"), dias_por_mes=6):
    """2020-01..2025-12, primeros dias de cada mes: ciclo diario + ruido AR(1), con algun hueco."""
    rng = np.random.default_rng(11)
    out = {}
    for k, st in enumerate(estaciones):
        s, ruido = {}, 0.0
        for anio in range(2020, 2026):
            for mes in range(1, 13):
                ini = H(f"{anio}-{mes:02d}-01T00:00:00+00:00")
                for h in range(ini, ini + 24 * dias_por_mes):
                    ruido = 0.85 * ruido + rng.normal(0, 2)
                    if h % 101 == 0:
                        continue
                    hc = e4.hora_colombia(h)
                    s[h] = max(0.5, 18 + 4 * k + 7 * math.sin(2 * math.pi * (hc - 2) / 24) + ruido)
        out[st] = s
    return out


INFO = {"fuente": "IDEAM datos.gov.co", "licencia": "CC BY-SA 4.0", "api": "x", "filtro_soql": "x",
        "conteos_importador": {"leidas": 1}, "conteos_limpieza": {"ceros": 0, "rachas": 0}, "sha256_datos": "0" * 64}


def test_conjuntos_por_fecha():
    assert e4.conjunto_de(H("2023-06-30T23:00:00+00:00")) == "entrenamiento"
    assert e4.conjunto_de(H("2023-07-01T00:00:00+00:00")) == "validacion"
    assert e4.conjunto_de(H("2024-01-01T00:00:00+00:00")) == "prueba_a"
    assert e4.conjunto_de(H("2025-01-01T00:00:00+00:00")) == "prueba_b"
    assert e4.conjunto_de(H("2026-01-01T00:00:00+00:00")) is None


def test_estacion_31366_excluida_y_documentada():
    assert "31366" not in e4.ESTACIONES and set(e4.ESTACIONES) == {"29509", "29493", "31854"}
    assert "incoherentes" in e4.EXCLUIDAS["31366"]
    assert e4.HASTA.isoformat() == "2025-12-31"


def test_muestras_exigen_ventana_completa_y_no_cruzan_conjuntos():
    t = H("2022-05-10T12:00:00+00:00")
    serie = {t - k: 10.0 + k for k in range(24)}
    serie[t + 3] = 9.0
    X, ylog, y, P, _, E, _ = e4.muestras({"A": serie}, "entrenamiento", 3)
    i = [k for k in range(len(y)) if y[k] == 9.0]          # la muestra emitida en t (objetivo t+3)
    assert len(i) == 1 and P[i[0]] == 10.0 and E[i[0]] == "A"
    assert math.isclose(ylog[i[0]], math.log((9 + 2) / (10 + 2)))
    del serie[t - 3]                           # falta una de las 6 horas previas de t
    assert 9.0 not in list(e4.muestras({"A": serie}, "entrenamiento", 3)[2])
    # ventana de 24 h que empieza en junio y objetivo en julio de 2023: no entra en ningun conjunto
    t2 = H("2023-07-01T02:00:00+00:00")
    s2 = {t2 - k: 5.0 for k in range(24)}
    s2[t2 + 3] = 6.0
    assert all(6.0 not in list(e4.muestras({"A": s2}, c, 3)[2]) for c in e4.CONJUNTOS)


def test_media_por_hora_por_estacion_solo_entrenamiento():
    t_tr, t_te = H("2021-01-01T15:00:00+00:00"), H("2024-03-01T15:00:00+00:00")
    tabla = e4.media_por_hora({"A": {t_tr: 10.0, t_tr + 24: 20.0, t_te: 900.0}, "B": {t_tr: 4.0}})
    assert tabla == {"A": {10: 15.0}, "B": {10: 4.0}}


def test_variables_de_entrenamiento_iguales_a_las_de_inferencia():
    t = H("2022-05-10T20:00:00+00:00")
    serie = {t - k: 3.0 + k for k in range(24)}
    ahora = datetime.fromtimestamp(t * 3600, tz=timezone.utc)
    filas = [(ahora - timedelta(minutes=i), 3.0 + i // 60) for i in range(24 * 60)]
    modelo = {"variables_familia": "v4", "c": e4.C}
    assert pr.variables_para(modelo, pr.serie_por_minuto(filas), pr.minuto(ahora)) == e4.entrada_en(serie, t)


def test_de_punta_a_punta_escribe_v4_sin_tocar_v1_v2_v3(tmp_path, capsys):
    series = _series_sinteticas()
    antes = [p.read_bytes() for p in e4.PROTEGIDOS]
    salida = tmp_path / "v4.json"
    assert e4.main(["--salida", str(salida)], series=series, info=INFO) == 0
    assert [p.read_bytes() for p in e4.PROTEGIDOS] == antes
    m = json.loads(salida.read_text(encoding="utf-8"))
    assert (m["nombre"], m["variables_familia"], m["c"], set(m["horizontes"])) == \
        ("prediccion_pm25_v4", "v4", 2.0, {"180", "240", "300"})
    assert m["conjuntos"]["prueba_a"]["meses_utc"] == ["2024-01", "2024-12"]
    assert m["conjuntos"]["prueba_b"]["meses_utc"] == ["2025-01", "2025-12"]
    assert "31366" in m["fuentes"][0]["estaciones_excluidas"]
    assert any("festivos" in x for x in m["limitaciones"])
    for h, d in m["horizontes"].items():
        met = d["metricas"]
        assert d["alpha"] in e4.ALPHAS and set(met["por_estacion"]) == set(series)
        assert met["conjunto"] == "prueba_a" and d["metricas_prueba_b"]["conjunto"] == "prueba_b"
        assert d["metricas_prueba_b"]["n_prueba"] > 0 and set(d["metricas_prueba_b"]["por_estacion"]) == set(series)
        visible = int(h) in m["horizontes_visibles"]
        assert visible == (met["mejora_mae_pct"] >= 5.0 and d["cumple_sesgo"]) or abs(met["mejora_mae_pct"] - 5.0) <= 0.05
        assert set(d["sesgo_series_constantes"]) == {"miercoles", "domingo"}
    # la inferencia en Python puro lo carga y lo aplica
    ahora = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    s = pr.serie_por_minuto([(ahora - timedelta(minutes=i), 20.0) for i in range(24 * 60 + 5)])
    r = pr.prediccion(pr.cargar_modelo(salida), s, ahora)
    assert r["estado"] in ("ok", "no_supera_linea_base")
    assert "horizontes_visibles" in capsys.readouterr().out


def test_no_sobrescribe_modelos_anteriores():
    for p in e4.PROTEGIDOS:
        assert e4.main(["--salida", str(p)], series={}, info=INFO) == 2
