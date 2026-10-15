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
