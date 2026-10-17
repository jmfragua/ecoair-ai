import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# main.py lee la clave al importar; en los tests no se abre ninguna conexion real.
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

from historial import construir_historial  # noqa: E402
from main import app  # noqa: E402

# Sin "with": no se ejecuta el lifespan, asi que no se abre el pool. Las peticiones que
# fallan la validacion (422) nunca llegan al handler ni a la base.
client = TestClient(app)
URL = "/devices/ecoair-node-01/air-quality/history"
AHORA = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("query", [
    "hours=0", "hours=169", "hours=-5", "hours=abc",
    "bucket_min=0", "bucket_min=61", "bucket_min=2.5",
])
def test_parametros_invalidos_dan_422(query):
    r = client.get(f"{URL}?{query}")
    assert r.status_code == 422


def test_dashboard_se_sirve_en_la_raiz():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "EcoAir AI" in r.text


def test_static_no_tapa_las_rutas_de_la_api():
    # /docs es de FastAPI: si StaticFiles se montara antes, devolveria 404
    assert client.get("/docs").status_code == 200


def _fila(minutos_atras, pm25):
    return {"t": AHORA - timedelta(minutes=minutos_atras), "pm25": Decimal(str(pm25)),
            "pm10": Decimal("20.04"), "temperature": Decimal("22.36"), "humidity": None, "muestras": 60}


def test_construir_historial_convierte_y_redondea():
    h = construir_historial("ecoair-node-01", [_fila(10, 12.345), _fila(5, 13)], AHORA, 24, 5, False)
    assert h["puntos"][0] == {"t": "2026-10-15T11:50:00+00:00", "pm25": 12.3, "pm10": 20.0,
                              "temperature": 22.4, "humidity": None, "muestras": 60}
    assert h["desde_utc"] == "2026-10-14T12:00:00+00:00"
    assert h["hasta_utc"] == "2026-10-15T12:00:00+00:00"
    assert h["is_simulated"] is False


def test_historial_parcial_y_completo():
    # 2 intervalos de 5 min = 10 min de 24 h: parcial
    parcial = construir_historial("x", [_fila(10, 1), _fila(5, 1)], AHORA, 24, 5, True)
    assert parcial["parcial"] is True and parcial["horas_con_datos"] == 0.17
    assert parcial["is_simulated"] is True
    # 1 h con 12 intervalos de 5 min: completo
    filas = [_fila(5 * i, 1) for i in range(12)]
    completo = construir_historial("x", filas, AHORA, 1, 5, False)
    assert completo["parcial"] is False and completo["cobertura"] == 1.0


def test_historial_vacio():
    h = construir_historial("x", [], AHORA, 24, 5, False)
    assert h["puntos"] == [] and h["parcial"] is True and h["horas_con_datos"] == 0


# ---------- Prediccion experimental (paso 3) ----------

def test_forecast_responde_sin_inventar_valores(monkeypatch):
    import main
    from datetime import datetime, timedelta, timezone

    ahora = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    async def device_ok(device_id):
        return None

    async def query_falsa(sql, params=()):
        assert params["device_id"] == "ecoair-sim-01"
        if sql is main.SQL_ULTIMA:
            return [{"received_at": ahora, "temperature": 22.0, "humidity": 60.0}]
        assert sql is main.SQL_SERIE_PM25
        return [{"t": ahora - timedelta(minutes=i), "pm25": 20.0} for i in range(200)]

    monkeypatch.setattr(main, "require_device", device_ok)
    monkeypatch.setattr(main, "query", query_falsa)
    main._cache_prediccion.clear()
    r = TestClient(main.app).get("/devices/ecoair-sim-01/forecast").json()
    assert r["experimental"] is True and r["device_id"] == "ecoair-sim-01"
    assert r["fuentes"] == "Modelo entrenado con datos simulados. Experimental." and "simulados" in r["aviso"]
    por_h = {h["minutos"]: h for h in r["horizontes"]}
    # en el modelo versionado, 1 h no supero a la persistencia: no hay valor; 3 h si
    assert por_h[60] == {"minutos": 60, "pm25": None, "disponible": False}
    assert por_h[180]["disponible"] and por_h[180]["pm25"] is not None



def test_forecast_sin_datos_informa_la_antiguedad_real(monkeypatch):
    """Nodo apagado hace 47 min: sin_datos, horizontes vacios y minutos reales desde la ultima medicion."""
    import main
    from datetime import datetime, timedelta, timezone

    ahora = datetime.now(timezone.utc)

    async def device_ok(device_id):
        return None

    async def query_falsa(sql, params=()):
        if sql is main.SQL_ULTIMA:
            return [{"received_at": ahora - timedelta(minutes=47, seconds=10), "temperature": 22.0, "humidity": 60.0}]
        return []   # sin datos en las ultimas 3 h

    monkeypatch.setattr(main, "require_device", device_ok)
    monkeypatch.setattr(main, "query", query_falsa)
    main._cache_prediccion.clear()
    r = TestClient(main.app).get("/devices/ecoair-node-01/forecast").json()
    assert r["estado"] == "sin_datos" and r["horizontes"] == []
    assert r["minutos_desde_ultima_medicion"] == 47 and r["ultima_medicion_utc"]
    assert "sin datos suficientes" in r["tendencia"]


def test_forecast_de_dispositivo_sin_mediciones_no_inventa_antiguedad(monkeypatch):
    import main

    async def device_ok(device_id):
        return None

    async def query_falsa(sql, params=()):
        return []

    monkeypatch.setattr(main, "require_device", device_ok)
    monkeypatch.setattr(main, "query", query_falsa)
    main._cache_prediccion.clear()
    r = TestClient(main.app).get("/devices/nuevo/forecast").json()
    assert r["minutos_desde_ultima_medicion"] is None and r["ultima_medicion_utc"] is None


def test_dashboard_se_sirve_sin_cache():
    for ruta in ("/", "/js/app.js", "/js/prediccion-vista.js"):
        r = client.get(ruta)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", ruta


def test_vistas_del_dashboard_en_js_con_node(tmp_path):
    """Ejecuta los tests de JS (node --test) de las tarjetas de prediccion y de recomendaciones.

    Las recomendaciones se prueban con el JSON REAL de shared/recomendaciones.py (6 niveles), el
    mismo texto que usan los correos: una sola fuente de verdad.
    """
    import json
    import shutil
    import subprocess
    from pathlib import Path

    from recomendaciones import ORDEN, recomendacion

    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    fixture = tmp_path / "recomendaciones.json"
    fixture.write_text(json.dumps({c: recomendacion(c) for c in ORDEN}, ensure_ascii=False))
    carpeta = Path(__file__).parent / "js"
    archivos = sorted(str(f) for f in carpeta.glob("test_*.mjs"))
    assert len(archivos) >= 2
    r = subprocess.run(["node", "--test", *archivos], capture_output=True, text=True,
                       env={**os.environ, "RECOMENDACIONES_JSON": str(fixture)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "# skipped 0" in r.stdout or "ℹ skipped 0" in r.stdout, r.stdout
