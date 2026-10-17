import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.recomendaciones import ETIQUETA, ORDEN, RECOMENDACIONES, TITULOS, recomendacion  # noqa: E402

TEXTOS = Path(__file__).resolve().parents[2] / "docs" / "normas" / "texto"
CAMPOS = ("efectos", "resumen", "poblacion_general", "grupos_sensibles", "acciones",
          "cuando_consultar", "si_sube", "fuentes")


@pytest.mark.parametrize("codigo", ORDEN)
def test_cada_nivel_tiene_todos_los_campos_con_texto(codigo):
    r = RECOMENDACIONES[codigo]
    assert set(CAMPOS) <= set(r)
    for campo in ("efectos", "resumen", "poblacion_general"):
        assert isinstance(r[campo], str) and len(r[campo].strip()) > 10, campo
    assert r["grupos_sensibles"]["quienes"].strip() and r["grupos_sensibles"]["recomendacion"].strip()
    assert 1 <= len(r["acciones"]) <= 5 and all(a.strip() for a in r["acciones"])
    assert r["cuando_consultar"] is None or r["cuando_consultar"].strip()
    # cada bloque con texto tiene su fuente
    for bloque in ("efectos", "poblacion_general", "grupos_sensibles", "acciones"):
        assert bloque in r["fuentes"], bloque
    if r["cuando_consultar"]:
        assert "cuando_consultar" in r["fuentes"]


def test_si_sube_en_todos_menos_el_nivel_mas_alto():
    assert all(RECOMENDACIONES[c]["si_sube"] for c in ORDEN[:-1])
    assert RECOMENDACIONES["peligrosa"]["si_sube"] is None
    for actual, siguiente in zip(ORDEN, ORDEN[1:]):           # si_sube cita los efectos del siguiente nivel
        assert RECOMENDACIONES[siguiente]["efectos"] in RECOMENDACIONES[actual]["si_sube"]


def test_cuando_consultar_solo_donde_la_fuente_lo_respalda():
    # La EPA solo da la indicacion de sintomas cardiacos desde "Unhealthy for Sensitive Groups"
    assert RECOMENDACIONES["buena"]["cuando_consultar"] is None
    assert RECOMENDACIONES["aceptable"]["cuando_consultar"] is None
    assert all(RECOMENDACIONES[c]["cuando_consultar"] for c in ORDEN[2:])


def test_salida_para_json_incluye_campos_nuevos_y_anteriores():
    r = recomendacion("danina")
    assert r["etiqueta"] == ETIQUETA == "Texto fijo de la norma, no IA" and r["tipo"] == "texto_fijo_no_ia"
    assert r["recomendacion"] == r["resumen"] and r["fuente"] and r["titulos"] == TITULOS
    r["acciones"].append("x")                                  # es una copia: no altera la constante
    assert "x" not in RECOMENDACIONES["danina"]["acciones"]


# ---------- Las paginas citadas existen en los textos de las normas del repo ----------

def _paginas(nombre):
    texto = (TEXTOS / nombre).read_text(encoding="utf-8")
    partes = re.split(r"=== PAGINA (\d+) ===", texto)
    return {int(partes[i]): partes[i + 1] for i in range(1, len(partes) - 1, 2)}


@pytest.mark.skipif(not TEXTOS.is_dir(), reason="sin docs/normas/texto")
def test_las_frases_originales_estan_en_la_pagina_citada():
    epa = {k: " ".join(v.split()) for k, v in _paginas("epa_aqi_tad.txt").items()}
    res = {k: " ".join(v.split()).lower() for k, v in _paginas("res_2254_2017.txt").items()}
    # (pagina PDF del .txt, fragmento original en ingles que respalda la traduccion). La Tabla 5 esta
    # en columnas en el .txt, asi que se buscan fragmentos contiguos de la columna de PM.
    epa_respaldo = [
        (13, "It’s a great day to be"),                    # buena
        (13, "Unusually sensitive"),                       # aceptable
        (13, "inside if you have"),
        (14, "outdoors but take"),                         # danina_sensibles
        (14, "Follow your asthma"),
        (14, "health care provider"),
        (15, "rescheduling or"),                           # danina
        (15, "moving all activities"),
        (16, "Avoid all physical"),                        # muy_danina
        (17, "Stay indoors and"),                          # peligrosa
        (17, "keep activity levels"),
        (17, "may be dangerous in extremely hot weather"), # nota al pie (*)
        (12, "People with heart or lung disease; older adults; and children, including"),  # Tabla 4
        (6, "AQI values at or below 100 are generally thought of as satisfactory"),         # seccion I
    ]
    for pagina, frase in epa_respaldo:
        assert " ".join(frase.split()) in epa[pagina], (pagina, frase)
    # Res. 2254 Tabla 5 (texto escaneado: se buscan fragmentos sin acentos ni erratas)
    for pagina, fragmento in [(8, "riesgo bajo"), (8, "grupos poblacionales"), (8, "enfermedad"),
                              (9, "estado de alerta"), (9, "efectos adversos graves")]:
        assert fragmento in res[pagina], (pagina, fragmento)
