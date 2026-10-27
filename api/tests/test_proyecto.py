"""Franja del proyecto (api/proyecto.py, /proyecto, QR local) y modo kiosco. Sin base de datos."""
import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import proyecto  # noqa: E402

client = TestClient(main.app)
STATIC = Path(main.STATIC_DIR)
VENDOR_QR = STATIC / "vendor" / "qrcode-generator-2.0.4"


@pytest.mark.parametrize("base,esperado", [
    ("https://ecoair.up.railway.app", "https://ecoair.up.railway.app/panel"),
    ("https://ecoair.up.railway.app/", "https://ecoair.up.railway.app/panel"),
    (" http://192.168.1.200:8000 ", "http://192.168.1.200:8000/panel"),
    (None, None), ("", None), ("ecoair.app", None), ("ftp://x.org", None), ("javascript:alert(1)", None),
    ("https://", None), ("https://x.org/?a=1", None),
])
def test_url_panel(base, esperado):
    assert proyecto.url_panel(base) == esperado


def test_ruta_sin_base(monkeypatch):
    async def sin_base(*a, **k):
        raise AssertionError("/proyecto no debe consultar la base")
    monkeypatch.setattr(main, "query", sin_base)
    monkeypatch.setenv("BASE_URL", "https://ecoair.example.org")
    assert client.get("/proyecto").json() == {"url_panel": "https://ecoair.example.org/panel", "configurada": True}
    monkeypatch.delenv("BASE_URL")
    assert client.get("/proyecto").json() == {"url_panel": None, "configurada": False}


def test_qr_local_fijo_y_sin_alterar():
    for linea in (VENDOR_QR / "SHA256SUMS").read_text().splitlines():
        h, nombre = linea.split()
        assert hashlib.sha256((VENDOR_QR / nombre).read_bytes()).hexdigest() == h, nombre
    lic = (VENDOR_QR / "LICENSE.txt").read_text()
    assert "MIT License" in lic and "Kazuhiko Arase" in lic
    js = (STATIC / "js" / "proyecto-vista.js").read_text()
    assert 'from "../vendor/qrcode-generator-2.0.4/qrcode.mjs"' in js
    assert "://" not in js                                                   # ninguna URL de servicio externo


def test_franja_del_proyecto_en_el_panel():
    panel = (STATIC / "panel.html").read_text()
    franja = panel[panel.index('<section class="proyecto"'):panel.index("</section>", panel.index('<section class="proyecto"'))]
    for texto in ("EcoAir AI", "ODS 3</span> Salud y bienestar", "ODS 11</span> Ciudades y comunidades sostenibles",
                  "ODS 13</span> Acción por el clima", 'id="proyecto-qr"'):
        assert texto in franja
    assert "<img" not in franja and "logo" not in franja.lower()             # sin logos oficiales de la ONU


def test_kiosco_oculta_formularios_mapas_y_pie():
    css = (STATIC / "css" / "app.css").read_text()
    oculta = css[css.index("body.modo-kiosco .brand__device"):css.index("{ display: none; }", css.index("body.modo-kiosco .brand__device"))]
    for sel in (".brand__device", ".foot", ".proyecto", ".grid > .card:not(.card--ica)"):
        assert f"body.modo-kiosco {sel}" in oculta
    app = (STATIC / "js" / "app.js").read_text()
    inicio = app[app.index("function start()"):]
    assert "if (!MODO_KIOSCO) iniciarUbicacion();" in inicio and "if (!MODO_KIOSCO) iniciarProyecto();" in inicio
    kiosco = inicio[inicio.index("if (MODO_KIOSCO) {"):inicio.index("return;", inicio.index("if (MODO_KIOSCO) {"))]
    for no in ("initSubscriptionForm", "loadConsent", "iniciarReplay"):
        assert no not in kiosco


def test_valores_atenuados_sin_conexion():
    """Bajo cada valor de la cuadricula del ICA (panel y kiosco) y de temperatura/humedad hay un hueco
    para "última lectura · hora"; con el nodo sin enviar (> 10 min) se atenuan con --muted."""
    panel = (STATIC / "panel.html").read_text()
    for k in ("pm25", "pm10", "temp", "hum"):
        assert f'<span id="now-{k}">—</span>' in panel
    assert panel.count("data-lectura") == 6                                # 4 de la cuadricula + temperatura + humedad
    css = (STATIC / "css" / "app.css").read_text()
    assert ".now.is-antigua dd, .card--pm.is-antigua .metric__value, .card--env.is-antigua .env__value { color: var(--muted); }" in css
    assert ".lectura:empty { display: none; }" in css                       # en linea no se ve nada
    app = (STATIC / "js" / "app.js").read_text()
    assert 'n.classList.toggle("is-antigua", v.antigua)' in app and "n.textContent = v.lectura" in app
