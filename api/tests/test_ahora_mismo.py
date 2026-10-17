"""Valores de la ultima medicion: cuadricula 2x2 en el panel y miniatura en la portada."""
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

client = TestClient(main.app, follow_redirects=False)
STATIC = Path(__file__).resolve().parents[1] / "static"


def _texto_visible(html):
    return re.sub(r"<[^>]+>", " ", html)


def test_panel_tiene_la_cuadricula_2x2_en_la_tarjeta_del_ica():
    html = client.get("/panel").text
    tarjeta = html[html.index('class="card card--ica"'):html.index("<!-- Recomendacion -->")]
    for id_, etiqueta, unidad in (("now-pm25", "PM2.5", "µg/m³"), ("now-pm10", "PM10", "µg/m³"),
                                  ("now-temp", "Temperatura", "°C"), ("now-hum", "Humedad", "%")):
        assert f'<dt>{etiqueta}</dt><dd><span id="{id_}">—</span> <span class="unit">{unidad}</span>' in tarjeta
    assert 'id="now-ultima"' in tarjeta and 'id="now-aviso"' in tarjeta
    assert "ICA con la media de la última hora (indicativo) · Res. 2254/2017" in tarjeta
    assert 'id="sim-badge"' in html  # la marca SIMULACION se mantiene


def test_portada_tiene_la_miniatura_con_pm10_y_sin_pm1():
    html = client.get("/").text
    tarjeta = html[html.index('class="card live"'):html.index("</section>")]
    for id_, etiqueta, unidad in (("mini-pm25", "PM2.5", "µg/m³"), ("mini-pm10", "PM10", "µg/m³"),
                                  ("mini-temp", "Temperatura", "°C"), ("mini-hum", "Humedad", "%")):
        assert f'<dt>{etiqueta}</dt><dd><span id="{id_}">—</span> <span class="unit">{unidad}</span>' in tarjeta
    assert 'id="mini-aviso"' in tarjeta and 'id="sim"' in tarjeta
    assert not re.search(r"PM\s?1(\.0)?\b", _texto_visible(tarjeta))
    assert "(0–500; 0–50 = Buena)" in tarjeta
    codigo = re.sub(r"//[^\n]*", "", (STATIC / "js" / "portada.js").read_text())
    assert '$("mini-pm10").textContent = v.pm10' in codigo


def test_ninguna_pagina_muestra_pm1_ni_valores_basura():
    """El SDS011 no mide PM1.0; el HTML inicial nunca trae undefined/NaN."""
    for ruta in ("/", "/panel"):
        visible = _texto_visible(client.get(ruta).text)
        assert not re.search(r"PM\s?1(\.0)?\b", visible), ruta
        assert not re.search(r"undefined|NaN", visible), ruta
    for js in ("ahora-vista.js", "app.js", "portada.js"):
        codigo = re.sub(r"//[^\n]*", "", (STATIC / "js" / js).read_text())  # sin comentarios
        assert not re.search(r"\bpm1\b|PM1\.0|pm1_0", codigo), js


def test_ahora_vista_con_node():
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    r = subprocess.run(["node", "--test", str(Path(__file__).parent / "js" / "test_ahora_vista.mjs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
