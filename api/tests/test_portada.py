"""Portada en "/", panel en "/panel" y compatibilidad de los enlaces existentes (QR y correos)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app, follow_redirects=False)
RAIZ = Path(__file__).resolve().parents[2]


def test_raiz_sirve_la_portada_sin_cache():
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    for texto in ("Proyecto académico · TdeA 2026", "EcoAir <span>AI</span>",
                  "Estación inteligente que mide el aire que respiras y te avisa por correo cuando empeora.",
                  'href="/panel"', 'href="/panel#suscripcion"', "ODS 3", "ODS 11", "ODS 13",
                  "no sustituye mediciones oficiales", "datos simulados y se indican como tal"):
        assert texto in r.text, texto
    assert "empresa" not in r.text.lower()
    assert "Cargando" not in r.text


def test_raiz_con_device_redirige_al_panel_conservando_parametros():
    r = client.get("/?device=x")
    assert r.status_code == 302 and r.headers["location"] == "/panel?device=x"
    r = client.get("/?device=ecoair-sim-01&utm=qr")
    assert r.status_code == 302 and r.headers["location"] == "/panel?device=ecoair-sim-01&utm=qr"


def test_raiz_con_otros_parametros_sin_device_muestra_la_portada():
    assert client.get("/?utm=qr").status_code == 200


def test_panel_sirve_el_dashboard_sin_cache():
    for ruta in ("/panel", "/panel?device=ecoair-sim-01"):
        r = client.get(ruta)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", ruta
        assert 'id="suscripcion"' in r.text and "Replay histórico" in r.text
        assert 'src="/js/app.js"' in r.text and 'href="/css/app.css"' in r.text   # rutas absolutas


def test_recursos_de_ambas_paginas_existen():
    for ruta in ("/css/app.css", "/css/portada.css", "/js/app.js", "/js/portada.js",
                 "/js/portada-vista.js", "/img/favicon.svg", "/fonts/inter-latin-wght-normal.woff2"):
        assert client.get(ruta).status_code == 200, ruta


def test_enlaces_existentes_siguen_llegando_a_un_destino_valido():
    """QR (http://.../?device=ecoair-node-01), correos antiguos y nuevos, confirmacion y baja."""
    # QR y correos enviados antes de este cambio: /?device=... -> /panel?device=...
    r = client.get("/?device=ecoair-node-01")
    assert r.status_code == 302 and client.get(r.headers["location"]).status_code == 200
    # Enlaces que genera hoy el notifier
    sys.path.insert(0, str(RAIZ / "notifier"))
    from correos import enlaces
    links = enlaces("", "ecoair-node-01", token_confirmacion="T", token_baja="B")
    assert links["dashboard"] == "/panel?device=ecoair-node-01"
    assert client.get(links["dashboard"]).status_code == 200
    assert links["confirmar"].startswith("/subscriptions/confirm?token=")
    assert links["baja"].startswith("/subscriptions/unsubscribe?token=")
    # La pagina de baja (GET) solo muestra el boton y vuelve al panel
    r = client.get(links["baja"])
    assert r.status_code == 200 and 'method="post"' in r.text and 'href="/panel"' in r.text


def test_portada_js_maneja_fallo_de_red_con_node():
    import shutil
    import subprocess

    import pytest
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    r = subprocess.run(["node", "--test", str(Path(__file__).parent / "js" / "test_portada_vista.mjs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
