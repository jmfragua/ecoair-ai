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


def test_zona_alertas_suscripcion_y_ubicacion():
    # movil (una columna, orden del HTML): alertas, mapa, suscripcion
    i = [PANEL.index(f'class="card {c}"') for c in ("card--alerts", "card--ubicacion", "card--subs")]
    assert i == sorted(i)
    # >= 900 px: izquierda alertas + suscripcion, derecha el mapa en las dos filas (solo vista del nodo)
    assert "@media (min-width: 900px) and (max-width: 1199px)" in CSS
    assert "body:not(.modo-replay) .card--ubicacion { grid-column: 2; grid-row: span 2;" in CSS
    assert ".card--ubicacion { grid-column: 3 / 5; grid-row: 5 / 7;" in CSS
    assert ".card--subs { grid-column: 1 / 3; grid-row: 6;" in CSS
    # el replay conserva su reparto (alertas | suscripcion en la fila 6) y el kiosco oculta la suscripcion
    assert "body.modo-replay .card--subs { grid-column: 3 / 5; grid-row: 6; }" in CSS
    assert "body.modo-kiosco .grid > .card:not(.card--ica)" in CSS
    assert CSS.count("{") == CSS.count("}")                               # sin llaves sueltas


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


def test_simulacion_sin_banner_grande_pero_con_etiqueta_compacta():
    # el banner grande punteado se quito (panel normal y kiosco); queda la etiqueta SIMULACIÓN junto al estado
    assert 'id="sim-banner"' not in PANEL and '"sim-banner"' not in APP
    assert "datos inventados para demostración. No son mediciones" not in PANEL
    assert '<span id="sim-badge" class="sim-badge" hidden>SIMULACIÓN</span>' in PANEL
    # se activa por /devices, por la tendencia o por el nombre ecoair-sim-NN (aunque la API falle), desde el arranque
    cuerpo = _funcion("setSimulacion")
    assert "const sim = simDispositivo || simDatos || esDispositivoSimulado(DEVICE);" in cuerpo
    assert '$("sim-badge").hidden = !sim;' in cuerpo and 'SIMULACIÓN · ' in cuerpo   # etiqueta y titulo de la pestana
    assert "setSimulacion();" in _funcion("start").split("if (MODO_REPLAY)")[0]
    # el replay conserva su banner violeta (usa la clase .sim-banner para la forma)
    assert 'id="replay-banner" class="sim-banner replay-banner"' in PANEL
    rep_css = CSS[CSS.index(".replay-banner {"):CSS.index("}", CSS.index(".replay-banner {"))]
    assert "var(--replay)" in rep_css and "var(--warn)" not in rep_css
    # las preguntas rapidas siguen sabiendo si la estacion es simulada
    assert "simulado: simDispositivo || simDatos || esDispositivoSimulado(DEVICE)" in _funcion("datosPreguntas")


def test_tarjeta_prediccion_simulada_solo_en_ecoair_sim():
    sec = _seccion("card--pred card--pred-sim")
    assert " hidden" in sec and 'id="predsim"' in sec                      # oculta hasta que la vista la active
    assert "Predicción de PM2.5 · SIMULACIÓN" in PANEL
    assert 'id="predsim-msg" role="status" aria-live="polite"' in PANEL
    # sin la linea larga del aviso de la API (el titulo y la etiqueta "experimental" se quedan)
    assert 'id="predsim-aviso"' not in PANEL and "v.aviso" not in _funcion("pintarPrediccionSim")
    assert '<span class="chip chip--muted">experimental</span>' in PANEL
    assert "const VISTA = secciones({ ...MODOS, device: DEVICE });" in APP
    assert "if (VISTA.prediccionSim) iniciarPrediccionSim();" in APP
    # en replay y kiosco start() vuelve antes de llegar a ella
    vivo = _funcion("start")
    assert vivo.index("iniciarPrediccionSim") > vivo.index("if (MODO_KIOSCO) {") > vivo.index("if (MODO_REPLAY)")
    pinta = _funcion("pintarPrediccionSim")
    assert "textContent" in pinta and "innerHTML" not in pinta
    assert "/predictions/latest" in _funcion("refreshPrediccionSim")
    # ambar de la simulacion; a >= 1200 px encima de la tendencia sin mover nada en el nodo real
    assert ".card--pred-sim { border: 2px dashed var(--warn);" in CSS
    assert "body.con-pred-sim .card--pred-sim { grid-column: 1 / -1; grid-row: 4; }" in CSS
    assert ".card--chart { grid-column: 1 / -1; grid-row: 4; }" in CSS                   # nodo real igual que antes
