"""Tests de /devices/{id}/predictions/latest y /predictions/metrics (sin base: query simulada)."""
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import predicciones as pz  # noqa: E402

client = TestClient(main.app)
AHORA = datetime(2026, 10, 20, 15, 30, tzinfo=timezone.utc)
EMISION = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
MODELO = {"horizontes_visibles": [240, 300], "aviso_publico": "Aviso v4", "texto_fuentes": "Fuentes v4"}


def fila_p(h, pred, base=Decimal("10.0"), version="prediccion_pm25_v4@abcd1234", created=EMISION):
    return {"created_at": created, "target_time": created + timedelta(hours=h), "horizon_hours": h,
            "predicted_pm25": Decimal(str(pred)), "baseline_pm25": base, "model_version": version, "last_reading_at": created}


def fila_m(h, n, mae_m, mae_b, fecha=date(2026, 10, 19), version="prediccion_pm25_v4@abcd1234"):
    return {"fecha": fecha, "horizon_hours": h, "model_version": version, "n": n, "n_sin_observacion": 1,
            "mae_model": Decimal(str(mae_m)), "rmse_model": Decimal(str(mae_m + 1)), "mae_baseline": Decimal(str(mae_b)),
            "rmse_baseline": Decimal(str(mae_b + 1))}


ULTIMAS = [fila_p(3, 11.2), fila_p(4, 12.0), fila_p(5, 12.5)]


# ---------- Logica pura ----------

def test_latest_solo_visibles_por_defecto_y_ocultos_a_pedido():
    r = pz.construir_latest("ecoair-node-01", ULTIMAS, MODELO, [], AHORA)
    assert r["estado"] == "ok" and r["minutos_desde_emision"] == 30 and r["model_version"].startswith("prediccion_pm25_v4@")
    assert [p["horizon_hours"] for p in r["predicciones"]] == [4, 5] and all(p["visible"] for p in r["predicciones"])
    assert r["aviso"] == "Aviso v4" and r["experimental"] is True
    todos = pz.construir_latest("ecoair-node-01", ULTIMAS, MODELO, [], AHORA, incluir_ocultos=True)
    p3 = todos["predicciones"][0]
    assert (p3["horizon_hours"], p3["visible"]) == (3, False) and "regla de visibilidad" in p3["motivo_oculto"]
    assert p3["predicted_pm25"] == 11.2 and p3["baseline_pm25"] == 10.0


def test_latest_sin_predicciones_y_desactualizada():
    r = pz.construir_latest("d", [], MODELO, [], AHORA)
    assert r["estado"] == "sin_predicciones" and r["predicciones"] == []
    vieja = [fila_p(4, 9, created=EMISION - timedelta(hours=3))]
    assert pz.construir_latest("d", vieja, MODELO, [], AHORA)["estado"] == "desactualizada"


def test_regla_en_vivo_oculta_tras_n_168_si_no_supera_a_la_persistencia():
    # 4 h: 168 evaluadas y MAE modelo >= persistencia -> oculto; 5 h: 167 evaluadas -> sigue visible
    metricas = [fila_m(4, 100, 6.0, 5.0), fila_m(4, 68, 5.0, 5.0, fecha=date(2026, 10, 18)), fila_m(5, 167, 9.0, 5.0)]
    ocultos = pz.ocultos_en_vivo(metricas, "prediccion_pm25_v4@abcd1234")
    assert set(ocultos) == {4} and "168 predicciones" in ocultos[4]
    r = pz.construir_latest("d", ULTIMAS, MODELO, metricas, AHORA)
    assert [p["horizon_hours"] for p in r["predicciones"]] == [5]
    # metricas de otra version del modelo no cuentan
    assert pz.ocultos_en_vivo(metricas, "otra@00000000") == {}


def test_agregar_pondera_por_n():
    a = pz.agregar([fila_m(3, 10, 2.0, 4.0), fila_m(3, 30, 4.0, 4.0, fecha=date(2026, 10, 18))])[0]
    assert (a["n"], a["dias"], a["mae_model"], a["mae_baseline"]) == (40, 2, 3.5, 4.0)
    assert a["rmse_model"] == round(((9 * 10 + 25 * 30) / 40) ** 0.5, 3) and a["mejora_mae_pct"] == 12.5


def test_metricas_vacias_dicen_por_que():
    r = pz.construir_metricas("d", [], 30, None, date(2026, 10, 20))
    assert r["estado"] == "sin_metricas" and r["desde"] == "2026-09-21" and "Aún no hay" in r["mensaje"]


# ---------- Rutas (query simulada) ----------

def query_falsa(respuestas):
    async def fake(sql, params=()):
        if "FROM devices" in sql:
            return [{"?column?": 1}] if respuestas.get("existe", True) else []
        if "UndefinedTable" in respuestas:
            raise psycopg.errors.UndefinedTable("relation predictions does not exist")
        if "FROM predictions" in sql:
            return respuestas.get("ultimas", [])
        if "FROM prediction_metrics" in sql:
            respuestas.setdefault("params_metricas", []).append(params)
            return respuestas.get("metricas", [])
        raise AssertionError(sql)
    return fake


def test_ruta_latest_200_404_503(monkeypatch):
    monkeypatch.setattr(main, "cargar_modelo", lambda ruta: MODELO)
    monkeypatch.setattr(main, "query", query_falsa({"ultimas": ULTIMAS}))
    r = client.get("/devices/ecoair-node-01/predictions/latest")
    assert r.status_code == 200 and [p["horizon_hours"] for p in r.json()["predicciones"]] == [4, 5]
    r = client.get("/devices/ecoair-node-01/predictions/latest?incluir_ocultos=true")
    assert [p["horizon_hours"] for p in r.json()["predicciones"]] == [3, 4, 5]
    monkeypatch.setattr(main, "query", query_falsa({"existe": False}))
    assert client.get("/devices/no-existe/predictions/latest").status_code == 404
    monkeypatch.setattr(main, "query", query_falsa({"UndefinedTable": True}))
    r = client.get("/devices/ecoair-node-01/predictions/latest")
    assert r.status_code == 503 and "008_predictions.sql" in r.json()["detail"]


@pytest.mark.parametrize("q", ["days=0", "days=366", "days=abc", "horizon=6", "horizon=1", "horizon=x"])
def test_ruta_metricas_422(q, monkeypatch):
    monkeypatch.setattr(main, "query", query_falsa({}))
    assert client.get(f"/devices/ecoair-node-01/predictions/metrics?{q}").status_code == 422


def test_ruta_metricas_200_filtra_horizonte_y_503(monkeypatch):
    resp = {"metricas": [fila_m(3, 20, 5.0, 6.0)]}
    monkeypatch.setattr(main, "query", query_falsa(resp))
    r = client.get("/devices/ecoair-node-01/predictions/metrics?days=7&horizon=3")
    assert r.status_code == 200 and r.json()["estado"] == "ok" and r.json()["por_horizonte"][0]["n"] == 20
    assert resp["params_metricas"][-1] == {"device_id": "ecoair-node-01", "dias": 7, "horizon": 3}
    monkeypatch.setattr(main, "query", query_falsa({}))
    assert client.get("/devices/ecoair-node-01/predictions/metrics").json()["estado"] == "sin_metricas"
    monkeypatch.setattr(main, "query", query_falsa({"UndefinedTable": True}))
    assert client.get("/devices/ecoair-node-01/predictions/metrics").status_code == 503


def test_forecast_se_mantiene():
    assert "/devices/{device_id}/forecast" in [r.path for r in main.app.routes]


# ---------- Tarjeta del panel ----------

def test_panel_sin_tarjeta_de_prediccion_en_vivo_y_con_replay():
    """La prediccion con mediciones en vivo se quito del panel; queda el replay historico."""
    html = client.get("/panel").text
    assert 'class="card card--pred"' not in html and 'id="pred-list"' not in html
    tarjeta = html[html.index('class="card card--replay"'):html.index("<!-- Suscripcion por correo -->")]
    assert ("Replay histórico con datos de estaciones de referencia del IDEAM. "
            "No es un pronóstico en tiempo real ni un equipo certificado.") in tarjeta
    for id_ in ("replay-estacion", "replay-at", "replay-filas", "replay-mae"):
        assert f'id="{id_}"' in tarjeta
    app_js = client.get("/js/app.js").text
    assert "/predictions/replay" in app_js and "/predictions/latest" not in app_js and "/forecast`" not in app_js


def test_replay_vista_con_node():
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    r = subprocess.run(["node", "--test", os.path.join(os.path.dirname(__file__), "js", "test_replay_vista.mjs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_predicciones_vista_con_node():
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    r = subprocess.run(["node", "--test", os.path.join(os.path.dirname(__file__), "js", "test_predicciones_vista.mjs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
