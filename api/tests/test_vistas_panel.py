"""Vista del nodo (solo en vivo) frente a ?modo=replay, y fila de preguntas rapidas. Estructura del
panel (HTML, CSS y app.js); la logica de las respuestas se prueba con node (test_preguntas_vista.mjs)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from recomendaciones import RECOMENDACIONES  # noqa: E402  (puente a shared/recomendaciones.py)

STATIC = Path(__file__).resolve().parent.parent / "static"
PANEL = (STATIC / "panel.html").read_text()
APP = (STATIC / "js" / "app.js").read_text()
CSS = (STATIC / "css" / "app.css").read_text()
PREG = (STATIC / "js" / "preguntas-vista.js").read_text()


def _seccion(clase):
    i = PANEL.index(f'<section class="card {clase}"')
    return PANEL[i:PANEL.index(">", i) + 1]


def _funcion(nombre):
    i = APP.index(f"function {nombre}(")
    return APP[i:APP.index("\n}\n", i)]


# (a) sin ?modo=replay no aparece nada de replay
def test_a_vista_del_nodo_sin_replay():
    assert " hidden" in _seccion("card--replay")                          # tarjeta + mapa IDEAM ocultos
    assert ".card--replay[hidden]" in CSS
    assert 'from "./replay-vista.js"' not in APP                          # sin import estatico
    assert 'const RV = MODO_REPLAY ? await import("./replay-vista.js") : null;' in APP
    vivo = _funcion("start")
    vivo = vivo[vivo.index("if (MODO_REPLAY) { iniciarModoReplay(); return; }"):]
    for no in ("iniciarReplay", "iniciarMapa", "/predictions", "RV."):
        assert no not in vivo, no
    # todo /predictions/replay* vive en funciones del replay, que solo arranca iniciarModoReplay
    assert re.search(r"/predictions/replay", APP)
    assert APP.count("iniciarReplay();") == 1 and "iniciarReplay();" in _funcion("iniciarModoReplay")
    assert "nth-of-type" not in CSS                                       # PM ubicadas por clase, no por posicion


# (b) con ?modo=replay si aparece
def test_b_modo_replay_muestra_la_tarjeta():
    cuerpo = _funcion("iniciarModoReplay")
    assert 'document.querySelector(".card--replay").hidden = false;' in cuerpo
    assert '$("replay-banner").hidden = false;' in cuerpo
    assert 'id="mapa-bloque"' in PANEL[PANEL.index('class="card card--replay"'):]   # el mapa IDEAM sigue en la tarjeta


# (c) la fila de preguntas: vista del nodo si; replay y kiosco no
def test_c_preguntas_solo_en_la_vista_del_nodo():
    sec = _seccion("card--preguntas")
    assert " hidden" in sec and 'id="preguntas"' in sec                   # oculta hasta que la vista la active
    assert PANEL.index('class="card card--preguntas"') < PANEL.index('class="card card--rec"')   # debajo del ICA
    assert 'id="preguntas-resp" aria-live="polite"' in PANEL
    assert "if (VISTA.preguntas) iniciarPreguntas();" in APP
    assert "body.modo-kiosco .grid > .card:not(.card--ica)" in CSS        # el kiosco oculta todo salvo el ICA
    assert ".pregunta:focus-visible" in CSS
    cuerpo = _funcion("iniciarPreguntas")
    assert 'el("button", { type: "button"' in cuerpo and '"aria-pressed"' in cuerpo


def test_preguntas_sin_innerhtml_y_sin_textos_de_la_norma_copiados():
    for codigo in (APP, PREG):
        assert "innerHTML" not in codigo.replace("(nunca innerHTML)", "")
    assert "fetch(" not in PREG and "getJSON" not in PREG                 # sin llamadas nuevas
    # Los textos de la norma vienen de la API (shared/recomendaciones.py): no se copian en el JS.
    for r in RECOMENDACIONES.values():
        assert r["efectos"] not in PREG
        for a in r["acciones"]:
            assert a not in PREG


def test_js_preguntas_vista():
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    r = subprocess.run(["node", "--test", str(Path(__file__).parent / "js" / "test_preguntas_vista.mjs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_devices_con_un_dispositivo_sin_mediciones_no_da_500():
    """Un dispositivo sin mediciones y sin location daba is_simulated = NULL y /devices respondia 500
    (el selector de estacion quedaba vacio). Ahora el COALESCE termina en false."""
    os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")
    import main
    assert "d.location = 'Simulado', false) AS is_simulated" in main.SQL_DEVICES
