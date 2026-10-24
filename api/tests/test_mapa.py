"""Tests del mapa de estaciones (api/mapa.py, /mapa/estaciones, Leaflet local). Sin base de datos."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import mapa  # noqa: E402
import replay  # noqa: E402

client = TestClient(main.app)
STATIC = Path(main.STATIC_DIR)
VENDOR = STATIC / "vendor" / "leaflet-1.9.4"

# Copia literal de lo que devolvio datos.gov.co (g4t8-zkc3) el 2026-10-24: si alguien cambia una
# coordenada de la tabla sin fuente, este test falla.
IDEAM_G4T8 = {
    "29509": (6.252709, -75.569656),
    "31854": (6.268792, -75.573653),
    "29493": (6.185667, -75.597206),
    "31366": (6.277838, -75.636363),
    "31946": (6.285101, -75.583145),
}


def test_coordenadas_con_fuente_y_sin_inventar():
    assert set(mapa.COORDENADAS) == set(IDEAM_G4T8)
    for codigo, c in mapa.COORDENADAS.items():
        assert (c["lat"], c["lon"]) == IDEAM_G4T8[codigo]
        assert "g4t8-zkc3" in c["fuente"] and "IDEAM" in c["fuente"]
        assert 6.05 < c["lat"] < 6.45 and -75.75 < c["lon"] < -75.45      # Valle de Aburra


def test_estacion_sin_coordenada_no_se_dibuja(monkeypatch):
    monkeypatch.setattr(mapa, "COORDENADAS", {k: v for k, v in mapa.COORDENADAS.items() if k != "31854"})
    r = mapa.construir_mapa()
    assert "31854" not in [e["station"] for e in r["estaciones"]]
    assert r["faltantes"] == [{"station": "31854", "nombre": replay.ESTACIONES["31854"]["nombre"]}]


def test_todas_las_del_replay_y_tipo_referencia():
    r = mapa.construir_mapa()
    assert [e["station"] for e in r["estaciones"]] == list(replay.ESTACIONES)
    assert {e["tipo"] for e in r["estaciones"]} == {"referencia"}
    assert r["faltantes"] == []


@pytest.mark.parametrize("valor,esperado", [
    ("6.2512,-75.5634", {"lat": 6.25, "lon": -75.56}),
    (" 6.2489 , -75.5711 ", {"lat": 6.25, "lon": -75.57}),
    (None, None), ("", None), ("6.25", None), ("a,b", None), ("95,-75", None), ("6.2,-190", None),
    ("nan,nan", None), ("inf,-75", None), ("6.2,-75.5,1", None),
])
def test_ubicacion_ecoair_redondeada_o_ninguna(valor, esperado):
    assert mapa.ubicacion_ecoair(valor) == esperado


def test_ecoair_aproximada_y_sin_configurar():
    r = mapa.construir_mapa("6.25123,-75.56789")
    assert r["ecoair"]["aproximada"] is True and (r["ecoair"]["lat"], r["ecoair"]["lon"]) == (6.25, -75.57)
    assert r["mensaje_ecoair"] == ""
    sin = mapa.construir_mapa(None)
    assert sin["ecoair"] is None and "no está configurada" in sin["mensaje_ecoair"]


def test_ruta_sin_base(monkeypatch):
    async def sin_base(*a, **k):
        raise AssertionError("/mapa/estaciones no debe consultar la base")
    monkeypatch.setattr(main, "query", sin_base)
    monkeypatch.setenv("ECOAIR_UBICACION_APROX", "6.2444,-75.5812")
    r = client.get("/mapa/estaciones")
    assert r.status_code == 200
    j = r.json()
    assert len(j["estaciones"]) == 5 and j["ecoair"]["lat"] == 6.24
    monkeypatch.delenv("ECOAIR_UBICACION_APROX")
    assert client.get("/mapa/estaciones").json()["ecoair"] is None


def test_leaflet_local_fijo_y_sin_alterar():
    sumas = (VENDOR / "SHA256SUMS").read_text().split("\n")
    archivos = [l.split() for l in sumas if l.strip()]
    assert {n for _, n in archivos} >= {"leaflet.js", "leaflet.css", "LICENSE.txt"}
    for h, nombre in archivos:
        assert hashlib.sha256((VENDOR / nombre).read_bytes()).hexdigest() == h, nombre
    assert "Leaflet 1.9.4" in (VENDOR / "leaflet.js").read_text()[:300]
    assert "BSD 2-Clause" in (VENDOR / "LICENSE.txt").read_text()
    r = client.get("/vendor/leaflet-1.9.4/leaflet.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_unico_recurso_externo_son_las_teselas_osm():
    """Fuera de vendor/, toda URL http(s) es un espacio de nombres, un enlace de licencia/atribucion
    o la plantilla de teselas de OSM. Ningun CDN, ninguna fuente o script remoto."""
    permitidas = {"http://www.w3.org/2000/svg", "http://scripts.sil.org/OFL", "https://github.com/rsms/inter",
                  "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "https://www.openstreetmap.org/copyright"}
    vistas = set()
    for f in STATIC.rglob("*"):
        if f.is_file() and "vendor" not in f.parts and f.suffix in (".html", ".js", ".css", ".txt", ".svg"):
            vistas |= set(re.findall(r"https?://[^\"'\s)<>]*", f.read_text(errors="replace")))
    assert vistas <= permitidas, vistas - permitidas
    panel = (STATIC / "panel.html").read_text()
    assert not re.search(r"<(script|link)[^>]+https?://", panel)


def test_mapa_no_toca_mediciones_predicciones_ni_alertas():
    fuente = (Path(mapa.__file__)).read_text()
    for prohibido in ("measurements", "predictions", "alerts", "query(", "psycopg"):
        assert prohibido not in fuente


def test_js_mapa_vista():
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    r = subprocess.run(["node", "--test", str(Path(__file__).parent / "js" / "test_mapa_vista.mjs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
