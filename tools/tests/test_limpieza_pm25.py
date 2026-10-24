"""Tests de tools/limpieza_pm25.py con series INVENTADAS."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import limpieza_pm25 as lp  # noqa: E402

T0 = datetime(2025, 3, 1, 5, 0, tzinfo=timezone.utc)


def serie(valores, st="EST", t0=T0, paso=1):
    return [{"station": st, "measured_at": t0 + timedelta(hours=i * paso), "pm25": v} for i, v in enumerate(valores)]


def test_cero_exacto_nulo_negativo_y_mayor_1000():
    validas, res = lp.limpiar(serie([0.0, 0, None, -1.0, 1000.1, 1000.0, 0.1, 15.0]))
    c = res["conteos"]
    assert (c["ceros"], c["nulos"], c["negativos"], c["mayores_1000"]) == (2, 1, 1, 1)
    assert [f["pm25"] for f in validas] == [1000.0, 0.1, 15.0]


def test_racha_de_6_horas_iguales_se_descarta_entera():
    validas, res = lp.limpiar(serie([10, 20, 20, 20, 20, 20, 20, 30]))
    assert [f["pm25"] for f in validas] == [10, 30]
    assert (res["conteos"]["rachas"], res["conteos"]["horas_en_rachas"]) == (1, 6)
    r = res["rachas"][0]
    assert (r["valor"], r["horas"], r["inicio"], r["fin"]) == (20, 6, T0 + timedelta(hours=1), T0 + timedelta(hours=6))


def test_racha_de_5_se_conserva_y_de_8_cuenta_una_vez():
    v5, r5 = lp.limpiar(serie([7] * 5))
    assert len(v5) == 5 and r5["conteos"]["rachas"] == 0
    v8, r8 = lp.limpiar(serie([7] * 8 + [9]))
    assert [f["pm25"] for f in v8] == [9] and (r8["conteos"]["rachas"], r8["conteos"]["horas_en_rachas"]) == (1, 8)


def test_un_hueco_o_un_valor_distinto_corta_la_racha():
    # 3 + hueco de 1 h + 3: no son seguidas
    filas = serie([12] * 3) + serie([12] * 3, t0=T0 + timedelta(hours=4))
    v, r = lp.limpiar(filas)
    assert len(v) == 6 and r["conteos"]["rachas"] == 0
    v2, r2 = lp.limpiar(serie([12, 12, 12, 13, 12, 12, 12]))
    assert len(v2) == 7 and r2["conteos"]["rachas"] == 0


def test_los_ceros_no_forman_racha_y_se_cuentan_como_ceros():
    v, r = lp.limpiar(serie([0] * 10 + [5]))
    assert [f["pm25"] for f in v] == [5]
    assert (r["conteos"]["ceros"], r["conteos"]["rachas"]) == (10, 0)


def test_estaciones_separadas_desordenadas_y_duplicados():
    a = serie([4] * 6, st="A")
    b = serie([4, 5, 4, 5, 4, 5], st="B")
    filas = list(reversed(a + b)) + [dict(b[0], pm25=99)]
    v, r = lp.limpiar(filas)
    assert r["conteos"]["duplicadas"] == 1 and r["conteos"]["rachas"] == 1
    assert {f["station"] for f in v} == {"B"} and len(v) == 6
    assert [f["measured_at"] for f in v] == sorted(f["measured_at"] for f in v)


def test_resumen_impreso(capsys):
    _, res = lp.limpiar(serie([0, 3, 3, 3, 3, 3, 3]))
    lp.imprimir_resumen(res)
    out = capsys.readouterr().out
    assert "cero exacto (falla del equipo): 1" in out
    assert "rachas de 6+ horas con el mismo valor: 1 (6 horas descartadas)" in out
