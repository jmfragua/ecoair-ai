"""Recomendaciones por categoria del ICA.

REGLAS DE TEXTO FIJO, NO IA: cada categoria tiene un texto escrito a mano. Ningun modelo
genera ni modifica estos textos.

Fuentes (no se anadio ninguna afirmacion medica propia):
- "efectos": Res. 2254/2017, Art. 19, Tabla 5 (texto del .txt de docs/normas/texto/, con las
  erratas del escaneo corregidas). Paginas segun las marcas "=== PAGINA N ===" del .txt.
- "recomendacion": traduccion del equipo de las cautionary statements de material particulado
  (columna "Particulate Matter, PM2.5 / PM10 (24-hr)") de la Tabla 5 del EPA AQI Technical
  Assistance Document (mayo 2026), paginas impresas 8 a 12 (= PDF/.txt 13 a 17).
- Supuesto del proyecto: las categorias de la EPA se asocian a las colombianas por rango del
  indice (0-50, 51-100, ...). Los puntos de corte de concentracion no son iguales (la EPA es mas
  estricta para PM2.5 en los niveles bajos), asi que la recomendacion EPA es solo orientativa.
- Grupos sensibles para material particulado (Res. 2254, Tabla 5): personas con enfermedad
  cardiaca o pulmonar, adultos mayores y ninos.
"""

FUENTE_RES = "Res. 2254/2017, Art. 19, Tabla 5"
FUENTE_EPA = "EPA AQI Technical Assistance Document (mayo 2026), Tabla 5, material particulado"
NOTA_CALOR_EPA = (
    "Nota EPA: si no tiene aire acondicionado, quedarse adentro con las ventanas cerradas puede "
    "ser peligroso con calor extremo; si tiene calor, vaya a un lugar con aire acondicionado."
)

ASMA = "Personas con asma: sigan su plan de acción para el asma y tengan a mano su medicamento de alivio rápido."
CORAZON = (
    "Personas con enfermedad cardiaca: síntomas como palpitaciones, falta de aire o cansancio "
    "inusual pueden indicar un problema serio; si los tiene, contacte a su proveedor de salud."
)

RECOMENDACIONES = {
    "buena": {
        "efectos": "La contaminación atmosférica supone un riesgo bajo para la salud.",
        "recomendacion": "Es un gran día para estar activo al aire libre.",
        "fuente": [
            {"documento": FUENTE_RES, "pagina": "8 (PDF)"},
            {"documento": FUENTE_EPA, "pagina": "8 impresa (PDF 13)"},
        ],
    },
    "aceptable": {
        "efectos": "Posibles síntomas respiratorios en grupos poblacionales sensibles.",
        "recomendacion": (
            "Personas inusualmente sensibles: consideren hacer las actividades al aire libre más "
            "cortas y menos intensas. Entren a un lugar cerrado si tienen síntomas como tos o "
            "falta de aire."
        ),
        "fuente": [
            {"documento": FUENTE_RES, "pagina": "8 (PDF)"},
            {"documento": FUENTE_EPA, "pagina": "8 impresa (PDF 13)"},
        ],
    },
    "danina_sensibles": {
        "efectos": (
            "Los grupos poblacionales sensibles pueden presentar efectos sobre la salud. Material "
            "particulado: las personas con enfermedad cardiaca o pulmonar, los adultos mayores y "
            "los niños se consideran sensibles y por lo tanto en mayor riesgo."
        ),
        "recomendacion": " ".join([
            "Grupos sensibles: hagan las actividades al aire libre más cortas y menos intensas.",
            "Pueden estar activos al aire libre, pero tomen más pausas.",
            "Estén atentos a síntomas como tos o falta de aire.",
            ASMA,
            CORAZON,
        ]),
        "fuente": [
            {"documento": FUENTE_RES, "pagina": "8 (PDF)"},
            {"documento": FUENTE_EPA, "pagina": "9 impresa (PDF 14)"},
        ],
    },
    "danina": {
        "efectos": (
            "Todos los individuos pueden comenzar a experimentar efectos sobre la salud. Los "
            "grupos sensibles pueden experimentar efectos más graves para la salud."
        ),
        "recomendacion": " ".join([
            "Grupos sensibles: consideren reprogramar o pasar todas las actividades a un lugar cerrado.",
            "Entren si tienen síntomas.",
            ASMA,
            CORAZON,
            "Todos los demás: hagan las actividades al aire libre más cortas y menos intensas.",
            "Entren si tienen síntomas.",
        ]),
        "fuente": [
            {"documento": FUENTE_RES, "pagina": "8 (PDF)"},
            {"documento": FUENTE_EPA, "pagina": "10 impresa (PDF 15)"},
        ],
    },
    "muy_danina": {
        "efectos": (
            "Estado de alerta que significa que todos pueden experimentar efectos más graves "
            "para la salud."
        ),
        "recomendacion": " ".join([
            "Grupos sensibles: eviten toda actividad física al aire libre.",
            "Reprogramen para cuando la calidad del aire sea mejor o pasen las actividades a un lugar cerrado.",
            ASMA,
            CORAZON,
            "Todos los demás: limiten la actividad física al aire libre.",
            "Entren a un lugar cerrado si tienen síntomas.",
            NOTA_CALOR_EPA,
        ]),
        "fuente": [
            {"documento": FUENTE_RES, "pagina": "9 (PDF)"},
            {"documento": FUENTE_EPA, "pagina": "11 impresa (PDF 16)"},
        ],
    },
    "peligrosa": {
        "efectos": (
            "Advertencia sanitaria. Toda la población puede presentar efectos adversos graves en "
            "la salud humana y están propensos a verse afectados por graves efectos sobre la salud."
        ),
        "recomendacion": " ".join([
            "Grupos sensibles: permanezcan en un lugar cerrado y mantengan un nivel de actividad ligero.",
            "Sigan recomendaciones para mantener bajos los niveles de partículas en interiores.",
            ASMA,
            CORAZON,
            "Todos: eviten toda actividad física al aire libre.",
            NOTA_CALOR_EPA,
        ]),
        "fuente": [
            {"documento": FUENTE_RES, "pagina": "9 (PDF)"},
            {"documento": FUENTE_EPA, "pagina": "12 impresa (PDF 17)"},
        ],
    },
}


def recomendacion(category_code):
    """Texto fijo de la categoria (copia, para que el llamador no modifique la constante)."""
    r = RECOMENDACIONES[category_code]
    return {**r, "fuente": [dict(f) for f in r["fuente"]], "tipo": "texto_fijo_no_ia"}
